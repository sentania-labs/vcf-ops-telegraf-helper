"""Work on several machines in one run, two at a time, each reporting on its own.

The runner takes one MachinePlan per machine and drives the ordinary configure workflow (or
the takeover workflow) for each. One machine's failure never stops the others. A stop request
is honoured only for machines that have not been changed yet. Every picked machine ends with
an outcome in plain words, and the run is turned into one history entry that counts against
what the person picked.
"""

from __future__ import annotations

import tempfile
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel, Field

from vcf_ops_telegraf_helper.adapters.base import VCFOpsIntegration
from vcf_ops_telegraf_helper.executors.base import EndpointExecutor
from vcf_ops_telegraf_helper.logger import get_logger
from vcf_ops_telegraf_helper.models.endpoint import EndpointTarget
from vcf_ops_telegraf_helper.models.vcf import VCFEnvironment
from vcf_ops_telegraf_helper.models.workflow import StageResult, StageStatus, TakeoverStage, WorkflowStage
from vcf_ops_telegraf_helper.storage.history import (
    KIND_REMOVE,
    KIND_TEST,
    KIND_TITLES,
    ActivityEntry,
    ActivityRow,
)
from vcf_ops_telegraf_helper.storage.journal import TakeoverJournal
from vcf_ops_telegraf_helper.utils import local_now_formatted
from vcf_ops_telegraf_helper.workflow.checks import _system_file, collector_label
from vcf_ops_telegraf_helper.workflow.collect import DEFAULT_WEB_URL, effective_keys, name_of, names_of
from vcf_ops_telegraf_helper.workflow.engine import ConfigureEndpointWorkflow
from vcf_ops_telegraf_helper.workflow.plain import (
    FIRST_DATA_WAIT,
    explain_exception,
    explain_failure,
    join_names,
    stage_log,
    takeover_step_for_stage,
)
from vcf_ops_telegraf_helper.workflow.plan import (
    KIND_CHANGE,
    KIND_SETUP,
    KIND_TAKEOVER,
    ConnectionChoices,
    MachinePlan,
    build_command,
)
from vcf_ops_telegraf_helper.workflow.takeover import (
    RESULT_INGESTION,
    RESULT_INSTALL,
    RESULT_REGISTRATION,
    RESULT_RETIREMENT,
    TakeoverOptions,
    TakeoverWorkflow,
)

logger = get_logger("batch")

PARALLEL = 2

AdapterFactory = Callable[[VCFEnvironment], VCFOpsIntegration]
ExecutorFactory = Callable[[EndpointTarget], EndpointExecutor]

STOPPED_TEXT = "Not set up: you stopped the run before anything was changed on it."


class MachineOutcome(BaseModel):
    """How one machine's part of a run ended."""

    machine_id: str
    ok: bool = True
    pending: bool = False
    skipped: bool = False
    note: bool = False
    msg: str = ""
    todo: str = ""
    log: str = ""
    report: str = ""
    collecting: Optional[List[str]] = None
    telegraf: str = ""
    checks: List[List[Any]] = Field(default_factory=list, description="Takeover: [label, state] for each result")
    backup_dir: Optional[str] = None


class _Reporter:
    """Turns engine stage events into the five plain steps of one machine."""

    def __init__(self, on_step: Callable[[int], None], kind: str, practice: bool = False):
        self.on_step = on_step
        self.kind = kind
        self.practice = practice
        self.messages: List[str] = []

    def on_stage_start(self, stage: Any) -> None:
        if isinstance(stage, TakeoverStage):
            if self.practice:
                # A practice run has three parts: read the settings, save them, validate the replacement
                order = {TakeoverStage.CAPTURE: 0, TakeoverStage.BACKUP: 1, TakeoverStage.PREFLIGHT: 2}
                if stage in order:
                    self.on_step(order[stage])
                return
            self.on_step(takeover_step_for_stage(stage))
            return
        if self.kind == KIND_TAKEOVER:
            return  # the inner install stages belong to the takeover's own steps
        index = {
            WorkflowStage.CONNECT: 0, WorkflowStage.DETECT: 1, WorkflowStage.PREPARE_VCF: 1,
            WorkflowStage.RENDER_INPUTS: 1, WorkflowStage.VALIDATE: 1, WorkflowStage.APPLY: 2,
            WorkflowStage.RESTART: 3, WorkflowStage.VERIFY: 4,
        }.get(stage)
        if index is not None:
            self.on_step(index)

    def on_stage_complete(self, result: StageResult) -> None:
        pass

    def on_message(self, message: str) -> None:
        self.messages.append(message)

    def on_phase(self, name: str) -> None:
        if self.kind == KIND_SETUP and name == "write":
            self.on_step(3)


def run_pool(jobs: List[Callable[[], None]], parallel: int = PARALLEL) -> None:
    """Run the jobs with at most `parallel` at the same moment; returns when all have ended."""
    jobs = list(jobs)
    lock = threading.Lock()

    def worker() -> None:
        while True:
            with lock:
                if not jobs:
                    return
                job = jobs.pop(0)
            try:
                job()
            except Exception:
                logger.exception("A batch job raised")

    threads = [threading.Thread(target=worker, daemon=True, name=f"batch-{i}") for i in range(max(1, min(parallel, len(jobs))))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


class BatchRunner:
    """Runs the plans. Callbacks are called from worker threads; the caller marshals them as it needs."""

    def __init__(
        self,
        plans: List[MachinePlan],
        conn: ConnectionChoices,
        adapter_factory: AdapterFactory,
        executor_factory: ExecutorFactory,
        on_step: Optional[Callable[[str, int], None]] = None,
        on_done: Optional[Callable[[str, MachineOutcome], None]] = None,
        on_changing: Optional[Callable[[str], None]] = None,
        confirm_text: str = "",
        accept_endpoint: Optional[Callable[[List[str], str], bool]] = None,
        package_fetcher: Optional[Callable[[str, bool, bool], tuple]] = None,
        journal_dir: Optional[Path] = None,
        takeover_waits: Optional[Dict[str, int]] = None,
    ):
        self.plans = plans
        self.conn = conn
        self.adapter_factory = adapter_factory
        self.executor_factory = executor_factory
        self.on_step = on_step or (lambda machine_id, step: None)
        self.on_done = on_done or (lambda machine_id, outcome: None)
        self.on_changing = on_changing or (lambda machine_id: None)
        self.confirm_text = confirm_text
        self.accept_endpoint = accept_endpoint
        self.package_fetcher = package_fetcher
        self.journal_dir = journal_dir
        self.takeover_waits = takeover_waits or {}
        self.outcomes: Dict[str, MachineOutcome] = {}
        self._stop = threading.Event()
        self._lock = threading.Lock()

    def stop(self) -> None:
        """Ask the run to stop. Machines already being changed finish; the rest are left as they were."""
        self._stop.set()

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    def run(self) -> Dict[str, MachineOutcome]:
        run_pool([lambda plan=plan: self._run_one(plan) for plan in self.plans])
        return self.outcomes

    # ------------------------------------------------------------------ one machine
    def _finish(self, plan: MachinePlan, outcome: MachineOutcome) -> None:
        with self._lock:
            self.outcomes[plan.machine_id] = outcome
        self.on_done(plan.machine_id, outcome)

    def _run_one(self, plan: MachinePlan) -> None:
        if self._stop.is_set():
            self._finish(plan, MachineOutcome(machine_id=plan.machine_id, skipped=True, msg=STOPPED_TEXT))
            return
        executor = None
        try:
            self.on_step(plan.machine_id, 0)
            env = self.conn.environment(plan.collector)
            adapter = self.adapter_factory(env)
            executor = self.executor_factory(plan.target())
            if plan.kind == KIND_TAKEOVER:
                outcome = self._take_over(plan, env, adapter, executor)
            else:
                outcome = self._configure(plan, env, adapter, executor)
        except Exception as exc:
            logger.exception("Run on %s could not start", plan.name)
            problem = explain_exception(str(exc))
            outcome = MachineOutcome(machine_id=plan.machine_id, ok=False, msg=problem.what, todo=problem.todo,
                                     log=f"== {plan.name} ==\n{exc}")
        finally:
            if executor is not None:
                try:
                    executor.close()
                except Exception:
                    pass
        self._finish(plan, outcome)

    def _configure(self, plan: MachinePlan, env, adapter, executor) -> MachineOutcome:
        reporter = _Reporter(lambda step: self.on_step(plan.machine_id, step), plan.kind)
        workflow = ConfigureEndpointWorkflow(
            environment=env, target=plan.target(), monitoring=plan.monitoring(), executor=executor,
            adapter=adapter, options=plan.options(), reporter=reporter,
        )
        workflow.should_stop = self._stop.is_set
        workflow.phase_callback = reporter.on_phase
        workflow.package_fetcher = self.package_fetcher
        original_apply = workflow.apply

        def apply_and_tell():
            if not plan.practice:
                self.on_changing(plan.machine_id)
            return original_apply()

        workflow.apply = apply_and_tell  # type: ignore[method-assign]
        summary = workflow.run()
        log = stage_log(plan.name, summary.stages, practice=plan.practice, extra=_verification_lines(summary.verifications))
        base = dict(machine_id=plan.machine_id, log=log, report=summary.to_markdown())
        if summary.stopped:
            return MachineOutcome(skipped=True, msg=STOPPED_TEXT, **base)
        if not summary.success:
            problem = explain_failure(summary.stages)
            return MachineOutcome(ok=False, msg=problem.what, todo=problem.todo, **base)
        if plan.practice:
            return MachineOutcome(
                msg="Would be changed without problems." if plan.kind == KIND_CHANGE else "Would be set up without problems.",
                **base,
            )
        collecting = effective_keys(plan.choices.keys, plan.os, plan.found, plan.single)
        version = (workflow.discovery.telegraf_version if workflow.discovery else None) or plan.version
        base.update(collecting=collecting, telegraf=_short_version(version) or plan.version)
        if plan.kind == KIND_CHANGE:
            return self._change_outcome(plan, workflow, executor, summary, base)
        pending = any(str(v).startswith("PENDING") for v in summary.verifications.values())
        extra = _unwatched_note(plan) + _status_page_note(plan)
        if pending:
            return MachineOutcome(
                pending=True, note=bool(extra),
                msg=f'Set up and running. Waiting for first data: {FIRST_DATA_WAIT} The Machines list says "waiting" until it arrives.' + extra,
                **base,
            )
        return MachineOutcome(note=bool(extra), msg="Set up. Data is arriving in VCF Operations." + extra, **base)

    def _change_outcome(self, plan, workflow, executor, summary, base) -> MachineOutcome:
        windows = plan.windows
        config_dir = workflow.discovery.config_dir if workflow.discovery else None
        try:
            deployed = executor.download(_system_file(config_dir, windows))
        except Exception as exc:
            deployed = None
            logger.warning("Could not read the settings back from %s: %s", plan.name, exc)
        if deployed is None or deployed.strip() != workflow.system_conf_content.strip():
            return MachineOutcome(
                ok=False,
                msg="The change was sent, but the settings read back from the machine are not what was asked for.",
                todo="Press Try again. If it happens again, open Technical details on this result and send them to "
                     "whoever supports this app.",
                **base,
            )
        restart = next((s for s in summary.stages if s.stage == WorkflowStage.RESTART), None)
        if restart is not None and restart.status == StageStatus.PASS:
            msg = "Change applied. The app read the settings back from the machine, and Telegraf restarted without errors."
        else:
            msg = ("The machine already had exactly these settings, so nothing needed to change. "
                   "The app read them back from the machine to confirm.")
        return MachineOutcome(msg=msg, **base)

    # ------------------------------------------------------------------ takeover
    def _takeover_workflow(self, plan, env, adapter, executor, reporter, journal=None, resume=True) -> TakeoverWorkflow:
        options = TakeoverOptions(
            confirmation_text=self.confirm_text or f"Practice run on {plan.name}",
            journal_dir=self.journal_dir,
            resume=resume,
            **self.takeover_waits,
        )
        workflow = TakeoverWorkflow(
            environment=env, target=plan.target().model_copy(update={"install_telegraf": True}), monitoring=None,
            executor=executor, adapter=adapter, takeover=options, options=plan.options().model_copy(update={"dry_run": False}),
            reporter=reporter, journal=journal,
        )
        workflow.accept_endpoint_callback = self.accept_endpoint
        return workflow

    def _take_over(self, plan: MachinePlan, env, adapter, executor) -> MachineOutcome:
        reporter = _Reporter(lambda step: self.on_step(plan.machine_id, step), KIND_TAKEOVER, practice=plan.practice)
        if plan.practice:
            with tempfile.TemporaryDirectory(prefix="takeover-practice-") as scratch:
                workflow = self._takeover_workflow(
                    plan, env, adapter, executor, reporter, journal=TakeoverJournal(Path(scratch)), resume=False
                )
                stages = workflow.run_practice()
            log = stage_log(plan.name, stages, practice=True)
            failed = any(s.status == StageStatus.FAIL for s in stages)
            if failed:
                problem = explain_failure(stages)
                return MachineOutcome(machine_id=plan.machine_id, ok=False, msg=problem.what, todo=problem.todo, log=log)
            return MachineOutcome(machine_id=plan.machine_id, msg="Would be taken over without problems.", log=log)

        workflow = self._takeover_workflow(plan, env, adapter, executor, reporter)
        original_retire = workflow.retire

        def retire_and_tell():
            self.on_changing(plan.machine_id)
            return original_retire()

        workflow.retire = retire_and_tell  # type: ignore[method-assign]
        summary = workflow.run()
        extra = [f"{name:<40}: {status}" for name, status in summary.results.items()]
        extra += _verification_lines(summary.verifications)
        log = stage_log(plan.name, summary.stages, extra=extra)
        if summary.install_summary is not None:
            log += "\n\n" + stage_log(f"{plan.name}: installing open-source Telegraf", summary.install_summary.stages)
        checks = takeover_checks(summary.results)
        base = dict(machine_id=plan.machine_id, log=log, report=summary.to_markdown(), checks=checks,
                    backup_dir=summary.backup_dir)
        if not summary.success:
            problem = explain_failure(summary.stages)
            return MachineOutcome(ok=False, msg=problem.what, todo=problem.todo, **base)
        imported = workflow.imported.monitoring if workflow.imported else workflow.monitoring
        if imported is not None:
            from vcf_ops_telegraf_helper.workflow.collect import keys_from_monitoring

            base["collecting"] = keys_from_monitoring(imported, plan.os)
        base["telegraf"] = plan.version
        changed = any(str(v).startswith("CHANGED") for v in summary.results.values())
        pending = any(str(v).startswith(("PENDING", "UNKNOWN")) for v in summary.results.values())
        retained = ""
        if summary.retained_items:
            count = len(summary.retained_items)
            retained = (f" {count} item{'s were' if count != 1 else ' was'} left on the machine from the old agent for you "
                        "to look at; they are listed under Technical details.")
            base["log"] = log + "\n\nLeft on the machine for review:\n" + "\n".join(f"  {i}" for i in summary.retained_items)
        if changed:
            return MachineOutcome(
                note=True,
                msg="Taken over, and open-source Telegraf is sending data. But VCF Operations made a new machine record "
                    "instead of keeping the old one, so the earlier history stays on the old record." + retained,
                **base,
            )
        if pending:
            return MachineOutcome(
                pending=True, note=bool(retained),
                msg="Taken over. Open-source Telegraf is installed and running, but VCF Operations has not shown its "
                    'first data yet. That can take up to 15 minutes; the Machines list says "waiting" until it arrives.' + retained,
                **base,
            )
        return MachineOutcome(note=bool(retained),
                              msg="Taken over. Open-source Telegraf is sending data to the same machine record." + retained, **base)


def takeover_checks(results: Dict[str, str]) -> List[List[Any]]:
    """The four things that had to be true, each with 'ok', 'wait' or 'bad'."""
    def state(value: Optional[str]) -> str:
        text = str(value or "")
        if text.startswith("PASS"):
            return "ok"
        if text.startswith(("PENDING", "UNKNOWN")):
            return "wait"
        return "bad"

    return [
        ["The old agent is gone from the machine", state(results.get(RESULT_RETIREMENT))],
        ["Open-source Telegraf is installed and running", state(results.get(RESULT_INSTALL))],
        ["VCF Operations kept the same machine record", state(results.get(RESULT_REGISTRATION))],
        ["Every measurement it sent before is arriving again", state(results.get(RESULT_INGESTION))],
    ]


def _verification_lines(verifications: Dict[str, str]) -> List[str]:
    return [f"{check:<26}: {status}" for check, status in verifications.items()]


def _short_version(value: Optional[str]) -> str:
    from vcf_ops_telegraf_helper.utils import parse_telegraf_version

    parsed = parse_telegraf_version(value)
    return ".".join(str(p) for p in parsed) if parsed else ""


def unwatched(plan: MachinePlan) -> List[str]:
    """Software found on the machine that this run does not watch."""
    return [k for k in plan.found if not plan.choices.has(k)]


def _unwatched_note(plan: MachinePlan) -> str:
    missing = names_of(unwatched(plan))
    if not missing:
        return ""
    return f" {join_names(missing)} was found here but is not watched; add it any time from the machine's page."


def _status_page_note(plan: MachinePlan) -> str:
    """A web server was switched on but its status page did not answer on this machine."""
    off = [k for k in DEFAULT_WEB_URL if plan.choices.has(k) and k in plan.found and k not in plan.web_urls and not plan.windows]
    if not off:
        return ""
    how = {
        "nginx": 'add a "stub_status" location to its settings',
        "apache": 'switch on its "mod_status" page',
    }
    return "".join(
        f" {name_of(k)} is switched on, but its built-in status page did not answer on this machine, so its figures "
        f"will be empty until someone who looks after the machine does this: {how[k]}."
        for k in off
    )


class LeftOut(BaseModel):
    """A machine the person picked that the run did not touch."""

    machine_id: str
    name: str
    os: str = "linux"
    why: str


def run_title(kind: str, practice: bool = False, resume: bool = False) -> str:
    title = "Finish a takeover" if resume else KIND_TITLES.get(kind, kind)
    return ("Practice run: " if practice else "") + title


def build_entry(
    kind: str,
    plans: List[MachinePlan],
    outcomes: Dict[str, MachineOutcome],
    conn: ConnectionChoices,
    left_out: Optional[List[LeftOut]] = None,
    practice: bool = False,
    resume: bool = False,
) -> ActivityEntry:
    """One history entry for a run: a line for every machine the person picked."""
    rows: List[ActivityRow] = []
    logs: List[str] = []
    reports: List[str] = []
    for plan in plans:
        outcome = outcomes.get(plan.machine_id) or MachineOutcome(
            machine_id=plan.machine_id, ok=False, msg="The run ended before this machine was reached.", todo="Press Try again."
        )
        rows.append(ActivityRow(
            machine_id=plan.machine_id, name=plan.name, os=plan.os, ok=outcome.ok, pending=outcome.pending,
            skipped=outcome.skipped, note=outcome.note, msg=outcome.msg, todo=outcome.todo,
            cmd="" if outcome.skipped else build_command(plan, conn),
            collecting=outcome.collecting, collector=plan.collector_label or collector_label(plan.collector),
            telegraf=outcome.telegraf,
        ))
        if outcome.log:
            logs.append(outcome.log)
        if outcome.report:
            reports.append(outcome.report)
    for item in left_out or []:
        rows.append(ActivityRow(machine_id=item.machine_id, name=item.name, os=item.os, left=True,
                                msg=f"Left out. {_sentence(item.why)} Nothing was done to it."))
    detail = ""
    if kind in (KIND_SETUP, KIND_CHANGE) and plans:
        keys = plans[0].choices.keys
        where = join_names(list(dict.fromkeys(p.collector_label or collector_label(p.collector) for p in plans)))
        detail = f"Collecting: {', '.join(names_of(keys))}. Data goes to {where}."
    first = plans[0] if plans else None
    choices: Dict[str, Any] = {}
    if first is not None:
        choices = {
            "collect": first.choices.without_secrets().model_dump(),
            "collectors": {p.machine_id: p.collector.model_dump() for p in plans},
            "install": {p.machine_id: p.install for p in plans},
            "version": first.version,
        }
    takeover_extra = [o for o in outcomes.values() if o.checks]
    if takeover_extra:
        choices["takeover_checks"] = takeover_extra[0].checks
        choices["backup_dir"] = takeover_extra[0].backup_dir
    footer = '\n\nThe command to do this again is on the result screen, under "Do this again from the command line".'
    return ActivityEntry(
        title=run_title(kind, practice, resume), kind=kind, practice=practice, rows=rows, detail=detail,
        log="\n\n".join(logs) + (footer if any(r.cmd for r in rows) else ""),
        report=_report(run_title(kind, practice, resume), rows, detail, reports),
        choices=choices,
    )


def _sentence(text: str) -> str:
    text = (text or "").strip()
    return text if text.endswith((".", "!", "?")) else text + "."


def _report(title: str, rows: List[ActivityRow], detail: str, reports: List[str]) -> str:
    """The saved report: the outcome in plain words first, then the technical summaries."""
    lines = [f"# Telegraf Helper: {title}", "", f"**When:** {local_now_formatted()}  ", f"**Machines:** {len(rows)}", ""]
    lines += ["| Machine | Result |", "| --- | --- |"]
    for row in rows:
        mark = "Left out" if row.left else "Stopped" if row.skipped else "Waiting for first data" if row.pending else (
            "Worked" if row.ok else "Did not work")
        text = row.msg + (f" What to do: {row.todo}" if row.todo and not row.ok else "")
        lines.append(f"| {row.name} | **{mark}.** {text.replace('|', '/')} |")
    if detail:
        lines += ["", detail]
    commands = [r for r in rows if r.cmd]
    if commands:
        lines += ["", "## Do this again from the command line", "",
                  "Passwords are never written out. Put your own in place of each <password> before you run it."]
        for row in commands:
            lines += ["", f"### {row.name}", "", "```", row.cmd, "```"]
    for report in reports:
        lines += ["", "---", "", report]
    return "\n".join(lines) + "\n"


def test_entry(rows: List[ActivityRow], entry_id: Optional[str] = None) -> ActivityEntry:
    """A connection test is kept in history like everything else. It changes nothing and has no command."""
    for row in rows:
        row.test = True
    entry = ActivityEntry(title=KIND_TITLES[KIND_TEST], kind=KIND_TEST, rows=rows)
    if entry_id:
        entry.id = entry_id
    entry.report = _report(entry.title, rows, "Nothing was changed on any machine.", [])
    return entry


def remove_entry(row: ActivityRow, log: str = "") -> ActivityEntry:
    entry = ActivityEntry(title=KIND_TITLES[KIND_REMOVE], kind=KIND_REMOVE, rows=[row], log=log)
    entry.report = _report(entry.title, [row], "", [])
    return entry


test_entry.__test__ = False  # not a pytest test, despite the name
