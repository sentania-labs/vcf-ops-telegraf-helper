"""Take over a VCF Operations product-managed Telegraf agent with open-source Telegraf (Windows).

Sequence proven in the lab on October 9, 2026 (issue #61): capture the Ops identity and the
managed configuration, back it up, retire the agent through the Ops agents API, verify the
endpoint is clean, install and enroll open-source Telegraf with the ported inputs through the
ordinary configure workflow, then confirm Ops kept the same OS object and flipped it to
Open Source. Four results are reported separately: retirement, installation, registration and
fresh ingestion.
"""

from __future__ import annotations

import time
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, List, Optional

from pydantic import BaseModel, Field

from vcf_ops_telegraf_helper.adapters.base import VCFOpsIntegration
from vcf_ops_telegraf_helper.executors.base import EndpointExecutor
from vcf_ops_telegraf_helper.logger import get_logger
from vcf_ops_telegraf_helper.models.endpoint import EndpointTarget, OSFamily
from vcf_ops_telegraf_helper.models.monitoring import MonitoringConfig
from vcf_ops_telegraf_helper.models.vcf import AgentObjectInfo, VCFEnvironment
from vcf_ops_telegraf_helper.models.workflow import RunSummary, StageResult, StageStatus, WorkflowOptions
from vcf_ops_telegraf_helper.security.redaction import redact_secrets
from vcf_ops_telegraf_helper.storage.journal import TakeoverJournal, TakeoverRecord
from vcf_ops_telegraf_helper.utils import local_now_formatted
from vcf_ops_telegraf_helper.workflow.engine import ConfigureEndpointWorkflow
from vcf_ops_telegraf_helper.workflow.managed_config import ImportedConfig, import_managed_config, merge_additions
from vcf_ops_telegraf_helper.workflow.progress import ProgressReporter, SilentProgressReporter
from vcf_ops_telegraf_helper.workflow.windows import (
    MANAGED_CERT_DIR,
    MANAGED_ROOT,
    ManagedInstallation,
    detect_managed_installation,
    detect_windows_telegraf,
)

logger = get_logger("workflow.takeover")


class TakeoverStage(str, Enum):
    CAPTURE = "1/6 Capturing the managed agent and its Ops identity"
    BACKUP = "2/6 Backing up the managed configuration"
    RETIRE = "3/6 Retiring the managed agent through VCF Operations"
    CLEAN = "4/6 Verifying the endpoint is clean"
    INSTALL = "5/6 Installing and enrolling open-source Telegraf"
    CONTINUITY = "6/6 Verifying object continuity in VCF Operations"


class TakeoverOptions(BaseModel):
    """Operator choices for a takeover run."""

    confirmation_text: str = Field(description="The operator's confirmation, recorded verbatim in the journal")
    uninstall_timeout_seconds: int = 300
    uninstall_poll_seconds: int = 10
    continuity_wait_seconds: int = 900
    continuity_poll_seconds: int = 60
    journal_dir: Optional[Path] = None
    resume: bool = True


class TakeoverSummary(BaseModel):
    """Outcome of a takeover: the four results, the stages, and where the record lives."""

    timestamp: str = Field(default_factory=local_now_formatted)
    target_hostname: str
    vm_name: Optional[str] = None
    vm_mor: Optional[str] = None
    vc_id: Optional[str] = None
    success: bool
    stages: List[StageResult] = Field(default_factory=list)
    results: Dict[str, str] = Field(default_factory=dict)
    verifications: Dict[str, str] = Field(default_factory=dict)
    journal_path: Optional[str] = None
    backup_dir: Optional[str] = None
    agent_object_before: Optional[AgentObjectInfo] = None
    agent_object_after: Optional[AgentObjectInfo] = None
    install_summary: Optional[RunSummary] = None
    import_summary: List[str] = Field(default_factory=list)

    def to_json(self, indent: int = 2) -> str:
        return self.model_dump_json(indent=indent)

    def to_markdown(self) -> str:
        lines = [
            "# VCF Operations Open Telegraf Helper: Agent Takeover Summary",
            "",
            f"**Timestamp (Local):** {self.timestamp}  ",
            f"**Target Host:** `{self.target_hostname}`  ",
            f"**VM:** `{self.vm_name or '?'}` (`{self.vm_mor}` in `{self.vc_id}`)  ",
            f"**Overall Status:** `{'SUCCESS' if self.success else 'FAILED'}`  ",
            "",
            "## Results",
            "",
            "| Result | Status |",
            "| --- | --- |",
        ]
        for name, status in self.results.items():
            lines.append(f"| {name} | `{status}` |")
        lines.extend(["", "## Stage Execution", "", "| Stage | Status | Message | Duration |", "| --- | --- | --- | --- |"])
        for s in self.stages:
            stage = s.stage.value if hasattr(s.stage, "value") else str(s.stage)
            lines.append(f"| {stage} | **{s.status.value}** | {s.message} | {s.duration_ms} ms |")
        for s in self.stages:
            if s.details:
                stage = s.stage.value if hasattr(s.stage, "value") else str(s.stage)
                lines.extend(["", f"### {stage}", "", s.details])
        if self.verifications:
            lines.extend(["", "## Verification Checklist", "", "| Check | Status |", "| --- | --- |"])
            for check, status in self.verifications.items():
                lines.append(f"| {check} | `{status}` |")
        if self.import_summary:
            lines.extend(["", "## Imported Monitoring Configuration", ""])
            lines.extend(self.import_summary)
        if self.journal_path:
            lines.extend(["", f"Journal: `{self.journal_path}`"])
        if self.backup_dir:
            lines.append(f"Backup: `{self.backup_dir}`")
        if self.install_summary:
            lines.extend(["", "---", "", self.install_summary.to_markdown()])
        lines.append("")
        return "\n".join(lines)


RESUMABLE_STATES = ("retired", "cleaned", "retire_failed")

RESULT_RETIREMENT = "Managed agent retirement"
RESULT_INSTALL = "Open-source Telegraf installation"
RESULT_REGISTRATION = "Registration on the same Ops object"
RESULT_INGESTION = "Fresh metric ingestion"


class TakeoverWorkflow:
    """Orchestrates the six takeover stages around the ordinary configure workflow."""

    def __init__(
        self,
        environment: VCFEnvironment,
        target: EndpointTarget,
        monitoring: Optional[MonitoringConfig],
        executor: EndpointExecutor,
        adapter: VCFOpsIntegration,
        takeover: TakeoverOptions,
        options: Optional[WorkflowOptions] = None,
        reporter: Optional[ProgressReporter] = None,
        journal: Optional[TakeoverJournal] = None,
        sleep: Callable[[float], None] = time.sleep,
        additions: Optional[MonitoringConfig] = None,
    ):
        self.env = environment
        self.target = target
        self.monitoring = monitoring
        self.additions = additions
        self.executor = executor
        self.adapter = adapter
        self.takeover = takeover
        self.options = options or WorkflowOptions()
        self.reporter = reporter or SilentProgressReporter()
        self.journal = journal or TakeoverJournal(takeover.journal_dir) if takeover.journal_dir else (journal or TakeoverJournal())
        self._sleep = sleep
        self.preview_callback: Optional[Callable[[], None]] = None

        self.record: Optional[TakeoverRecord] = None
        self.managed: Optional[ManagedInstallation] = None
        self.imported: Optional[ImportedConfig] = None
        self.agent_before: Optional[AgentObjectInfo] = None
        self.agent_after: Optional[AgentObjectInfo] = None
        self.install_summary: Optional[RunSummary] = None
        self.install_workflow: Optional[ConfigureEndpointWorkflow] = None
        self.stage_results: List[StageResult] = []
        self.results: Dict[str, str] = {}
        self.verifications: Dict[str, str] = {}
        self.cutover_started: Optional[float] = None
        self._resumed = False

        self._secrets: List[str] = [s for s in (self.env.password, self.env.token, self.target.password) if s]

    # ------------------------------------------------------------------ helpers
    def _sanitize(self, text: Optional[str]) -> Optional[str]:
        return None if text is None else redact_secrets(text, self._secrets)

    def _result(self, stage: TakeoverStage, status: StageStatus, message: str, start: float,
                details: Optional[str] = None) -> StageResult:
        res = StageResult(stage=stage, status=status, message=self._sanitize(message) or "",
                          details=self._sanitize(details), duration_ms=int((time.monotonic() - start) * 1000))
        self.reporter.on_stage_complete(res)
        if self.record is not None:
            self.record.stages.append({"stage": stage.value, "status": status.value, "message": res.message,
                                       "at": local_now_formatted()})
            self.journal.save(self.record)
        return res

    def _is_windows(self) -> bool:
        return self.target.os_family == OSFamily.WINDOWS or type(self.executor).__name__ == "WinRMExecutor"

    # ------------------------------------------------------------------ stages
    def capture(self) -> StageResult:
        start = time.monotonic()
        self.reporter.on_stage_start(TakeoverStage.CAPTURE)
        try:
            if not self._is_windows():
                raise RuntimeError("Takeover is only supported for Windows endpoints in this release.")
            if not self.target.vm_mor or not self.target.vc_id:
                raise RuntimeError("Takeover needs the VM selected from VCF Operations inventory (vCenter id and MOR).")
            if not self.target.username or not self.target.password:
                raise RuntimeError("Takeover needs the guest username and password; VCF Operations uses them to uninstall its agent.")
            if not self.executor.test_connection():
                raise RuntimeError(f"Failed to connect to {self.target.hostname}: "
                                   f"{getattr(self.executor, 'connection_error', None) or 'connection unavailable'}")

            win_det = detect_windows_telegraf(self.executor)
            self.managed = detect_managed_installation(self.executor, win_det, read_config=True)

            previous = self.journal.load(self.target.vc_id, self.target.vm_mor) if self.takeover.resume else None
            if not self.managed.present:
                if previous and previous.state in RESUMABLE_STATES and previous.agent_object_before:
                    return self._resume_from(previous, start)
                if previous and previous.state in RESUMABLE_STATES:
                    raise RuntimeError(
                        f"A journaled takeover for this VM is in state {previous.state} but its record is incomplete; "
                        "the managed agent is gone. Enroll the endpoint with the ordinary workflow."
                    )
                raise RuntimeError("No Ops-managed agent found on this endpoint; nothing to take over.")

            grains = self.managed.grain_values
            g_mor, g_vc = grains.get("vm_id"), grains.get("vc_id")
            if not g_mor or not g_vc:
                raise RuntimeError(
                    "The managed agent's grains do not name its vCenter id and VM id, so the endpoint cannot be matched "
                    "to the selected VM. Refusing an ambiguous takeover."
                )
            if g_mor != self.target.vm_mor or g_vc != self.target.vc_id:
                raise RuntimeError(
                    f"The managed agent on this endpoint is bound to {g_vc}/{g_mor}, not the selected VM "
                    f"{self.target.vc_id}/{self.target.vm_mor}. Refusing an ambiguous takeover."
                )

            vm_resource_id = self.adapter.resolve_vm_resource_id(self.target.vc_id, self.target.vm_mor)
            if not vm_resource_id:
                raise RuntimeError("The selected VM was not found in VCF Operations inventory; cannot address the uninstall.")
            self.agent_before = self.adapter.get_agent_object(self.target.vc_id, self.target.vm_mor, include_stat_keys=True)
            if self.agent_before is None:
                raise RuntimeError("VCF Operations has no agent OS object for this VM; takeover needs a registered managed agent.")
            if not self.agent_before.is_ops_managed and self.agent_before.managed_type:
                raise RuntimeError(
                    f"VCF Operations reports the agent object as '{self.agent_before.managed_type}', not Product Managed. "
                    "Refusing to retire it."
                )

            self.imported = merge_additions(
                import_managed_config(self.managed.telegraf_conf, self.managed.telegraf_d, is_windows=True), self.additions
            )
            if not self.imported.ok:
                raise RuntimeError("The managed configuration cannot be ported safely: " + "; ".join(self.imported.blocked))
            if self.monitoring is None:
                self.monitoring = self.imported.monitoring

            self.record = TakeoverRecord(
                vm_name=self.target.registered_hostname,
                vm_mor=self.target.vm_mor,
                vc_id=self.target.vc_id,
                vm_resource_id=vm_resource_id,
                target_hostname=self.target.hostname,
                ops_url=self.env.url,
                collector_address=self.env.collector.address,
                confirmation_text=self.takeover.confirmation_text,
                managed_services=sorted(self.managed.services),
                managed_telegraf_version=self.managed.telegraf_version,
                agent_object_before=self.agent_before,
            )
            self.journal.save(self.record)
            keys = self.agent_before.stat_key_count
            msg = (f"Managed agent {self.managed.summary()}; Ops object {self.agent_before.resource_id} "
                   f"({self.agent_before.managed_type}, {keys if keys is not None else '?'} stat keys)")
            details = "\n".join(self.imported.summary_lines()) if self.imported else None
            return self._result(TakeoverStage.CAPTURE, StageStatus.PASS, msg, start, details)
        except Exception as exc:
            logger.exception("Takeover capture failed")
            return self._result(TakeoverStage.CAPTURE, StageStatus.FAIL, f"Capture failed: {exc}", start)

    def _resume_from(self, previous: TakeoverRecord, start: float) -> StageResult:
        """Continue a takeover whose managed agent is already gone, from the journal and the backup."""
        if previous.target_hostname != self.target.hostname:
            raise RuntimeError(
                f"The journaled takeover for this VM was run against {previous.target_hostname}, not {self.target.hostname}. "
                "Refusing to resume on a different endpoint."
            )
        backup = Path(previous.backup_dir) if previous.backup_dir else None
        if backup is None or not (backup / "telegraf.conf").exists():
            raise RuntimeError(
                f"The journaled backup under {backup or 'the journal'} no longer holds telegraf.conf; "
                "the managed inputs cannot be ported. Enroll the endpoint with the ordinary workflow instead."
            )
        if previous.state == "retire_failed":
            # Ops may have finished the uninstall after the app gave up on it; ask before trusting the endpoint
            if not previous.uninstall_task_id:
                raise RuntimeError("The journaled retirement failed without a task id; reconcile in VCF Operations first.")
            status = self.adapter.get_agent_task_status(previous.uninstall_task_id)
            previous.uninstall_task_stage = status.stage
            if not status.finished:
                raise RuntimeError(
                    f"Ops uninstall task {previous.uninstall_task_id} is {status.stage}"
                    + (f" ({'; '.join(status.messages)})" if status.messages else "")
                    + "; the managed agent is gone from the endpoint but Ops has not finished. Reconcile in VCF Operations first."
                )
            previous.state = "retired"
            self.results[RESULT_RETIREMENT] = "PASS (task finished after the interruption)"
        self.record = previous
        self.agent_before = previous.agent_object_before
        self._resumed = True
        self.record.notes.append(f"Resumed at {local_now_formatted()} from state {previous.state} on {self.target.hostname}")
        self.journal.save(self.record)
        conf = (backup / "telegraf.conf").read_text(encoding="utf-8")
        fragments = {}
        if (backup / "telegraf.d").is_dir():
            fragments = {p.name: p.read_text(encoding="utf-8") for p in (backup / "telegraf.d").iterdir() if p.is_file()}
        self.imported = merge_additions(import_managed_config(conf, fragments, is_windows=True), self.additions)
        if not self.imported.ok:
            raise RuntimeError("The backed-up configuration cannot be ported safely: " + "; ".join(self.imported.blocked))
        if self.monitoring is None:
            self.monitoring = self.imported.monitoring
        return self._result(TakeoverStage.CAPTURE, StageStatus.WARNING,
                            f"Managed agent already retired (journal state {previous.state}); resuming the takeover", start)

    def backup(self) -> StageResult:
        start = time.monotonic()
        self.reporter.on_stage_start(TakeoverStage.BACKUP)
        if self._resumed:
            return self._result(TakeoverStage.BACKUP, StageStatus.SKIPPED, "Backup already taken before the interruption", start)
        try:
            assert self.record is not None and self.managed is not None
            files: Dict[str, str] = {}
            if self.managed.telegraf_conf is not None:
                files["telegraf.conf"] = self.managed.telegraf_conf
            for name, content in self.managed.telegraf_d.items():
                files[f"telegraf.d/{name}"] = content
            if self.managed.mandatory_tags is not None:
                files["mandatory_tags.bat"] = self.managed.mandatory_tags
            if self.managed.grains is not None:
                files["grains"] = self.managed.grains
            if "telegraf.conf" not in files:
                raise RuntimeError("Could not read the managed telegraf.conf; refusing to retire an agent whose configuration is not backed up.")
            if self.managed.read_errors:
                raise RuntimeError("Managed configuration could not be read completely: " + "; ".join(self.managed.read_errors))
            files["agent-object-before.json"] = self.agent_before.model_dump_json(indent=2) if self.agent_before else "{}"
            target = self.journal.write_backup(self.record, files)
            self.record.state = "backed_up"
            self.journal.save(self.record)
            return self._result(TakeoverStage.BACKUP, StageStatus.PASS, f"{len(files)} files saved under {target}", start)
        except Exception as exc:
            logger.exception("Takeover backup failed")
            return self._result(TakeoverStage.BACKUP, StageStatus.FAIL, f"Backup failed: {exc}", start)

    def retire(self) -> StageResult:
        start = time.monotonic()
        self.reporter.on_stage_start(TakeoverStage.RETIRE)
        if self._resumed:
            self.results[RESULT_RETIREMENT] = "PASS (before the interruption)"
            return self._result(TakeoverStage.RETIRE, StageStatus.SKIPPED, "Managed agent was already retired", start)
        try:
            assert self.record is not None
            self.cutover_started = time.time()
            self.record.cutover_started_at = local_now_formatted()
            task_id = self.adapter.uninstall_managed_agent(
                self.record.vm_resource_id or "", self.target.username or "", self.target.password or "", retain_config=False
            )
            self.record.uninstall_task_id = task_id
            self.journal.save(self.record)
            self.reporter.on_message(f"VCF Operations uninstall task {task_id} submitted; waiting for it to finish...")
            deadline = time.monotonic() + self.takeover.uninstall_timeout_seconds
            status = None
            while True:
                status = self.adapter.get_agent_task_status(task_id)
                self.record.uninstall_task_stage = status.stage
                if status.terminal:
                    break
                if time.monotonic() >= deadline:
                    self.record.state = "retire_failed"
                    self.journal.save(self.record)
                    raise RuntimeError(
                        f"Uninstall task {task_id} still {status.stage} after {self.takeover.uninstall_timeout_seconds}s. "
                        "Check the task in VCF Operations and the endpoint before retrying; the managed agent may be partially removed."
                    )
                self._sleep(self.takeover.uninstall_poll_seconds)
            if status.failed:
                self.results[RESULT_RETIREMENT] = "FAIL"
                self.record.state = "retire_failed"
                self.journal.save(self.record)
                raise RuntimeError(f"Uninstall task {task_id} reported: {'; '.join(status.messages) or status.stage}")
            self.record.state = "retired"
            self.results[RESULT_RETIREMENT] = "PASS"
            self.journal.save(self.record)
            return self._result(TakeoverStage.RETIRE, StageStatus.PASS, f"Ops uninstall task {task_id} finished", start)
        except Exception as exc:
            logger.exception("Takeover retire failed")
            self.results.setdefault(RESULT_RETIREMENT, "FAIL")
            if self.record is not None and self.record.uninstall_task_id and self.record.state != "retired":
                # Ops may still finish the task; a re-run re-polls it instead of refusing
                self.record.state = "retire_failed"
                self.journal.save(self.record)
            return self._result(TakeoverStage.RETIRE, StageStatus.FAIL, f"Retirement failed: {exc}", start)

    def clean(self) -> StageResult:
        start = time.monotonic()
        self.reporter.on_stage_start(TakeoverStage.CLEAN)
        try:
            assert self.record is not None
            after = detect_managed_installation(self.executor, read_config=True)
            if after.present:
                running = after.running_services
                raise RuntimeError(
                    f"Managed services still present after the Ops uninstall: {', '.join(sorted(after.services))}"
                    + (f" (running: {', '.join(running)})" if running else "")
                    + ". Not touching the endpoint; reconcile in VCF Operations first."
                )
            removed: List[str] = []
            left: List[str] = []
            if after.root_present:
                busy = self.executor.execute(
                    "Get-CimInstance Win32_Service | Where-Object { $_.PathName -like '*\\VMware\\UCP\\*' } | Select-Object -ExpandProperty Name",
                    timeout=15,
                )
                if not busy.success:
                    raise RuntimeError(f"Could not check for services under {MANAGED_ROOT}: {busy.stderr or busy.stdout}")
                if busy.stdout.strip():
                    raise RuntimeError(f"A service still runs from under {MANAGED_ROOT}: {busy.stdout.strip()}")
                # The Ops uninstall leaves an empty root behind; only an empty one is removed. Anything left in it
                # is the product's own files, outside the backup, and stays for the operator to look at.
                res = self.executor.execute(
                    f"$left = Get-ChildItem -Path '{MANAGED_ROOT}' -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object | Select-Object -ExpandProperty Count; "
                    f"if ($left -eq 0) {{ Remove-Item -Path '{MANAGED_ROOT}' -Recurse -Force -ErrorAction Stop; "
                    "if (Test-Path 'C:\\VMware') { if (-not (Get-ChildItem 'C:\\VMware' -Force)) { Remove-Item 'C:\\VMware' -Force } }; "
                    "Write-Output 'REMOVED' } else { Write-Output \"LEFT $left\" }",
                    timeout=60,
                )
                if not res.success:
                    raise RuntimeError(f"Could not remove {MANAGED_ROOT}: {res.stderr or res.stdout}")
                if "REMOVED" in res.stdout:
                    removed.append(MANAGED_ROOT)
                else:
                    left.append(f"{MANAGED_ROOT} ({res.stdout.strip().replace('LEFT ', '') or '?'} files left by the uninstall)")
            if after.cert_dir_present:
                res = self.executor.execute(f"Remove-Item -Path '{MANAGED_CERT_DIR}' -Recurse -Force -ErrorAction Stop", timeout=30)
                if not res.success:
                    raise RuntimeError(f"Could not remove {MANAGED_CERT_DIR}: {res.stderr or res.stdout}")
                removed.append(MANAGED_CERT_DIR)
            self.verifications["Managed services absent"] = "PASS"
            if left:
                self.verifications["Managed files removed"] = "LEFT IN PLACE (" + "; ".join(left) + ")"
            else:
                self.verifications["Managed files removed"] = "PASS" if removed else "PASS (nothing left behind)"
            self.record.state = "cleaned"
            self.journal.save(self.record)
            msg = "Endpoint clean: no managed services" + (f"; removed {', '.join(removed)}" if removed else "")
            if left:
                msg += "; left in place: " + "; ".join(left)
            return self._result(TakeoverStage.CLEAN, StageStatus.WARNING if left else StageStatus.PASS, msg, start)
        except Exception as exc:
            logger.exception("Takeover cleanup failed")
            self.verifications["Managed services absent"] = "FAIL"
            return self._result(TakeoverStage.CLEAN, StageStatus.FAIL, f"Cleanup failed: {exc}", start)

    def install(self) -> StageResult:
        start = time.monotonic()
        self.reporter.on_stage_start(TakeoverStage.INSTALL)
        try:
            assert self.record is not None and self.monitoring is not None
            opts = self.options.model_copy(update={
                "install_telegraf": True,
                "force_new_cert": True,
                "replace_inputs": True,
                "dry_run": False,
                "preview_only": False,
                "restart_service": True,
            })
            target = self.target.model_copy(update={"install_telegraf": True})
            self.install_workflow = ConfigureEndpointWorkflow(
                environment=self.env, target=target, monitoring=self.monitoring, executor=self.executor,
                adapter=self.adapter, options=opts, reporter=self.reporter,
            )
            self.install_workflow.preview_callback = self.preview_callback
            self.install_summary = self.install_workflow.run()
            self.verifications.update(self.install_summary.verifications)
            installed = self.install_summary.verifications.get("Telegraf installed", "FAIL").startswith("PASS")
            service = self.install_summary.verifications.get("Service running", "FAIL").startswith("PASS")
            self.results[RESULT_INSTALL] = "PASS" if (self.install_summary.success or (installed and service)) else "FAIL"
            if not self.install_summary.success:
                failed = next((s for s in self.install_summary.stages if s.status == StageStatus.FAIL), None)
                raise RuntimeError(failed.message if failed else "configure workflow failed")
            self.record.state = "installed"
            self.journal.save(self.record)
            return self._result(TakeoverStage.INSTALL, StageStatus.PASS, "Open-source Telegraf installed, enrolled and running", start)
        except Exception as exc:
            logger.exception("Takeover install failed")
            self.results.setdefault(RESULT_INSTALL, "FAIL")
            return self._result(
                TakeoverStage.INSTALL, StageStatus.FAIL,
                f"Installation failed after the managed agent was removed: {exc}. Monitoring is interrupted; "
                f"the backup is under {self.record.backup_dir if self.record else 'the journal'}. Re-run the takeover to resume.",
                start,
            )

    def continuity(self) -> StageResult:
        start = time.monotonic()
        self.reporter.on_stage_start(TakeoverStage.CONTINUITY)
        try:
            assert self.record is not None and self.agent_before is not None
            cutover_ms = int((self.cutover_started or time.time()) * 1000)
            deadline = time.monotonic() + self.takeover.continuity_wait_seconds
            after: Optional[AgentObjectInfo] = None
            while True:
                after = self.adapter.get_agent_object(self.target.vc_id, self.target.vm_mor, include_stat_keys=True)
                flipped = after is not None and (after.managed_type or "").strip().lower() == "open source"
                fresh = after is not None and after.last_sample_ms is not None and after.last_sample_ms > cutover_ms
                if flipped and fresh:
                    break
                if time.monotonic() >= deadline:
                    break
                remaining = int(deadline - time.monotonic())
                self.reporter.on_message(
                    f"Waiting for VCF Operations: object {'not found' if after is None else (after.managed_type or 'unknown type')}"
                    f"{', no new sample yet' if after is not None and not fresh else ''} ({remaining}s left)"
                )
                self._sleep(self.takeover.continuity_poll_seconds)

            self.agent_after = after
            self.record.agent_object_after = after
            if after is None:
                self.results[RESULT_REGISTRATION] = "PENDING (no agent object yet)"
                self.results[RESULT_INGESTION] = "PENDING"
                self.verifications["Same Ops object"] = "PENDING"
                status, msg = StageStatus.WARNING, "VCF Operations has not shown the agent object yet; re-check after a collection cycle"
            else:
                same = after.resource_id == self.agent_before.resource_id
                flipped = (after.managed_type or "").strip().lower() == "open source"
                fresh = after.last_sample_ms is not None and after.last_sample_ms > cutover_ms
                self.verifications["Same Ops object"] = "PASS" if same else f"CHANGED ({self.agent_before.resource_id} -> {after.resource_id})"
                self.verifications["Agent type Open Source"] = "PASS" if flipped else f"PENDING ({after.managed_type or 'unknown'})"
                if self.agent_before.stat_key_count is not None and after.stat_key_count is not None:
                    self.verifications["Stat keys"] = (
                        f"PASS ({after.stat_key_count} of {self.agent_before.stat_key_count})"
                        if after.stat_key_count >= self.agent_before.stat_key_count
                        else f"PENDING ({after.stat_key_count} of {self.agent_before.stat_key_count}; the win. inputs need a second interval)"
                    )
                self.results[RESULT_REGISTRATION] = ("PASS" if same and flipped else ("CHANGED OBJECT" if not same and flipped else "PENDING"))
                # Ops computes some stats itself every cycle, so a fresh timestamp alone proves nothing;
                # the type only flips to Open Source once the new agent's own samples arrive.
                self.results[RESULT_INGESTION] = (
                    "PASS" if (fresh and flipped) else "PENDING (first open-source sample takes up to two 300s intervals)"
                )
                if same and flipped and fresh:
                    status, msg = StageStatus.PASS, f"Same object {after.resource_id}, now Open Source, samples newer than the cutover"
                elif not same:
                    status, msg = StageStatus.WARNING, f"VCF Operations created a different object ({after.resource_id}); history stayed on {self.agent_before.resource_id}"
                else:
                    status, msg = StageStatus.WARNING, "VCF Operations has not finished switching the object yet; results are pending"
            self.record.results = dict(self.results)
            self.record.state = "verified" if status == StageStatus.PASS else self.record.state
            self.journal.save(self.record)
            return self._result(TakeoverStage.CONTINUITY, status, msg, start)
        except Exception as exc:
            logger.exception("Takeover continuity check failed")
            self.results.setdefault(RESULT_REGISTRATION, "UNKNOWN")
            self.results.setdefault(RESULT_INGESTION, "UNKNOWN")
            return self._result(TakeoverStage.CONTINUITY, StageStatus.WARNING, f"Continuity could not be verified: {exc}", start)

    # ------------------------------------------------------------------ run
    def run(self) -> TakeoverSummary:
        stages = [self.capture, self.backup, self.retire, self.clean, self.install, self.continuity]
        success = True
        for fn in stages:
            res = fn()
            self.stage_results.append(res)
            if res.status == StageStatus.FAIL:
                success = False
                if self.record is not None:
                    if self.record.state in ("captured", "backed_up"):
                        self.record.state = "failed"
                    self.record.results = dict(self.results)
                    self.journal.save(self.record)
                break
        for name in (RESULT_RETIREMENT, RESULT_INSTALL, RESULT_REGISTRATION, RESULT_INGESTION):
            self.results.setdefault(name, "NOT RUN")
        return TakeoverSummary(
            target_hostname=self.target.hostname,
            vm_name=self.target.registered_hostname,
            vm_mor=self.target.vm_mor,
            vc_id=self.target.vc_id,
            success=success,
            stages=self.stage_results,
            results=dict(self.results),
            verifications=dict(self.verifications),
            journal_path=str(self.journal.record_path(self.target.vc_id, self.target.vm_mor)) if self.target.vc_id and self.target.vm_mor else None,
            backup_dir=self.record.backup_dir if self.record else None,
            agent_object_before=self.agent_before,
            agent_object_after=self.agent_after,
            install_summary=self.install_summary,
            import_summary=self.imported.summary_lines() if self.imported else [],
        )
