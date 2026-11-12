"""The check a machine gets before anything is done to it, and the runner that works on several at once."""

from __future__ import annotations

import hashlib
import threading

import pytest

from vcf_ops_telegraf_helper.adapters.mock import MockVCFOpsIntegration
from vcf_ops_telegraf_helper.executors.base import CommandResult
from vcf_ops_telegraf_helper.executors.mock import MockExecutor
from vcf_ops_telegraf_helper.models.endpoint import ConnectionMethod, EndpointTarget
from vcf_ops_telegraf_helper.models.vcf import CollectorInfo
from vcf_ops_telegraf_helper.models.workflow import StageStatus, WorkflowOptions, WorkflowStage
from vcf_ops_telegraf_helper.renderer.renderer import TelegrafRenderer
from vcf_ops_telegraf_helper.storage.journal import TakeoverJournal
from vcf_ops_telegraf_helper.workflow import batch, checks
from vcf_ops_telegraf_helper.workflow import collect as c
from vcf_ops_telegraf_helper.workflow import plan as p
from vcf_ops_telegraf_helper.workflow.engine import ConfigureEndpointWorkflow
from vcf_ops_telegraf_helper.workflow.packages import fetch_telegraf_package, package_name

from test_takeover import VC_ID, VM_MOR, ManagedWindowsEndpoint, OpsWithUninstall

GROUP = CollectorInfo(address="10.10.10.50", name="Main", is_collector_group=True)
ONE = CollectorInfo(address="10.10.10.51", name="Main", display_name="cp-01")
OTHER = CollectorInfo(address="10.30.0.51", display_name="branch")
COLLECTORS = [GROUP, ONE, OTHER]
CONN = p.ConnectionChoices(url="https://ops.example.com", username="admin", password="ops-secret")


def machine(os="linux", **more):
    values = dict(machine_id="res-1", name="db-01", address="10.20.2.21", os=os, vm_mor="vm-1004", vc_id="423b-81f0-91a2-0003")
    values.update(more)
    return checks.MachineRef(**values)


def request(executor_os="linux", **more):
    values = dict(machine=machine(executor_os), username="svc", password="pw", collectors=COLLECTORS)
    values.update(more)
    return checks.CheckRequest(**values)


class Selective(MockExecutor):
    """A machine that reaches only some addresses, and may have no way out to the internet."""

    def __init__(self, reach=(), internet=True, procs=(), **kwargs):
        super().__init__(**kwargs)
        self.reach, self.internet, self.procs = set(reach), internet, list(procs)

    def execute(self, command, timeout=30):
        if "dl.influxdata.com" in command and "-I https://" in command:
            return CommandResult(exit_code=0 if self.internet else 7, command=command)
        for address in ("10.10.10.50", "10.10.10.51", "10.30.0.51"):
            if f"https://{address}:443/" in command:
                ok = address in self.reach
                return CommandResult(exit_code=0 if ok else 7, stdout="200\n" if ok else "", command=command)
        if "pgrep -x" in command:
            return CommandResult(exit_code=0, stdout="".join(f"proc:{name}\n" for name in self.procs), command=command)
        if 'echo "web:$u"' in command:
            return CommandResult(exit_code=0, stdout="web:http://localhost/nginx_status\n" if "nginx" in self.procs else "",
                                 command=command)
        return super().execute(command, timeout)


# ---------------------------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------------------------
def test_a_ready_machine_reports_what_the_app_found_out_for_itself():
    executor = Selective(reach={"10.10.10.50"}, procs=["nginx", "postgres"], telegraf_installed=False)
    result = checks.check_machine(request(), lambda target: executor)
    assert result.ok and result.signed_in and result.port == 22
    assert result.collector == GROUP, "the recommended group is picked without asking"
    assert result.installed_version is None and result.can_download is True
    assert result.found == ["nginx", "pg"] and result.web_urls == {"nginx": "http://localhost/nginx_status"}
    assert not result.monitored and result.current is None
    assert not executor.uploaded_files, "a check writes nothing to the machine"


def test_a_machine_keeps_the_collector_it_already_reports_to():
    ref = machine(collector_address="10.10.10.51", collector_group="Elsewhere")
    assert checks.preferred_collector(ref, COLLECTORS) == ONE
    assert checks.preferred_collector(machine(collector_group="Main"), COLLECTORS) == GROUP
    assert checks.preferred_collector(machine(), [OTHER]) == OTHER and checks.preferred_collector(machine(), []) is None


def test_refused_sign_in_and_unreachable_machine_are_different_problems():
    refused = MockExecutor(connected=False)
    refused.connection_error = "Authentication failed."
    result = checks.check_machine(request(many=True), lambda target: refused)
    assert result.state == checks.BAD and result.fix == checks.FIX_SIGNIN and result.refused
    assert "did not accept that user name or password" in result.what and "different account" in result.todo
    gone = MockExecutor(connected=False)
    gone.connection_error = "timed out"
    result = checks.check_machine(request(), lambda target: gone)
    assert result.fix == checks.FIX_AGAIN and not result.refused and "could not reach" in result.what


def test_unreachable_collector_offers_the_ones_the_machine_can_reach():
    executor = Selective(reach={"10.30.0.51"}, telegraf_installed=False)
    result = checks.check_machine(request(), lambda target: executor)
    assert result.state == checks.BAD and result.fix == checks.FIX_COLLECTOR
    assert 'cannot reach the collector group "Main"' in result.what and "pick one it can reach" in result.todo
    assert [(r.collector.address, r.ok) for r in result.collectors] == [
        ("10.10.10.50", False), ("10.10.10.51", False), ("10.30.0.51", True)]
    chosen = checks.check_machine(request(chosen=OTHER), lambda target: executor)
    assert chosen.ok and chosen.collector == OTHER
    tested = checks.check_machine(request(kind=checks.KIND_TEST), lambda target: executor)
    assert tested.fix == checks.FIX_AGAIN and 'can reach the collector "branch"' in tested.todo
    nowhere = checks.check_machine(request(), lambda target: Selective(telegraf_installed=False))
    assert nowhere.fix == checks.FIX_AGAIN and "cannot reach any of them" in nowhere.todo
    none = checks.check_machine(request(collectors=[]), lambda target: executor)
    assert none.state == checks.BAD and "has no collector" in none.what


def test_no_way_to_download_offers_to_send_telegraf_from_this_computer():
    executor = Selective(reach={"10.10.10.50"}, internet=False, telegraf_installed=False)
    result = checks.check_machine(request(), lambda target: executor)
    assert result.state == checks.BAD and result.fix == checks.FIX_SEND and "cannot download Telegraf" in result.what
    assert checks.check_machine(request(send=True), lambda target: executor).ok
    # a machine that keeps the Telegraf it has needs no download at all
    has_it = Selective(reach={"10.10.10.50"}, internet=False, telegraf_installed=True, telegraf_version="Telegraf 1.30.0")
    kept = checks.check_machine(request(), lambda target: has_it)
    assert kept.ok and kept.installed_version == "1.30.0" and kept.can_update and kept.can_download is None
    assert checks.check_machine(request(update=True), lambda target: has_it).fix == checks.FIX_SEND
    newer = Selective(reach={"10.10.10.50"}, telegraf_installed=True, telegraf_version="Telegraf 1.41.0")
    assert not checks.check_machine(request(update=True), lambda target: newer).can_update, "a version is never moved down"


def test_an_account_that_may_not_install_is_named_on_the_check():
    class NoSudo(Selective):
        use_sudo = True

        def execute(self, command, timeout=30):
            if command == "sudo -n true":
                return CommandResult(exit_code=1, stderr="sudo: a password is required", command=command)
            return super().execute(command, timeout)

    result = checks.check_machine(request(), lambda target: NoSudo(reach={"10.10.10.50"}))
    assert result.state == checks.BAD and result.fix == checks.FIX_SIGNIN
    assert "not allowed to install software" in result.what and "sudo" in result.todo


def test_change_reads_what_the_machine_collects_today():
    executor = Selective(reach={"10.10.10.50"}, telegraf_installed=True)
    choices = c.CollectChoices(keys=["cpu", "mem", "diskio"])
    executor.uploaded_files["/etc/telegraf/telegraf.d/vcf-helper-system.conf"] = TelegrafRenderer.render_system_inputs(
        c.monitoring_for(choices, "linux"))
    result = checks.check_machine(request(kind=checks.KIND_CHANGE), lambda target: executor)
    assert result.ok and result.monitored and result.current.keys == ["cpu", "mem", "diskio"]
    bare = checks.check_machine(request(kind=checks.KIND_CHANGE), lambda target: Selective(telegraf_installed=True))
    assert bare.state == checks.BAD and "nothing here for it to change" in bare.what


def test_windows_is_tried_the_secure_way_first_then_the_plain_way():
    tried = []

    def make(target: EndpointTarget):
        tried.append((target.port, target.winrm_use_ssl, target.connection_method))
        executor = MockExecutor(connected=not target.winrm_use_ssl, telegraf_installed=False)
        executor.connection_error = "connection refused"
        return executor

    result = checks.check_machine(request("windows"), make)
    assert tried[:2] == [(5986, True, ConnectionMethod.WINRM), (5985, False, ConnectionMethod.WINRM)]
    assert result.ok and (result.port, result.winrm_ssl) == (5985, False)
    assert result.found == ["mssql"], "running services name the software"
    tried.clear()
    checks.check_machine(request("windows", known_port=5985, known_ssl=False), make)
    assert tried[0] == (5985, False, ConnectionMethod.WINRM), "what worked before is tried first"
    tried.clear()
    only = checks.check_machine(request("windows", win_mode=checks.WIN_SECURE), make)
    assert tried == [(5986, True, ConnectionMethod.WINRM)] and only.state == checks.BAD


def test_a_refused_windows_sign_in_is_not_retried_another_way():
    tried = []

    def make(target: EndpointTarget):
        tried.append(target.port)
        executor = MockExecutor(connected=False)
        executor.connection_error = "the specified credentials were rejected by the server"
        return executor

    result = checks.check_machine(request("windows"), make)
    assert tried == [5986] and result.refused, "a second refusal could lock the account"


def test_takeover_check_needs_the_managed_agent_and_reads_what_it_collects():
    endpoint = ManagedWindowsEndpoint()
    ref = machine("windows", vm_mor=VM_MOR, vc_id=VC_ID, name="dbdemo01")
    ready = checks.check_machine(request(machine=ref, kind=checks.KIND_TAKEOVER), lambda target: endpoint)
    assert ready.ok and ready.managed and "cpu" in ready.takeover_keys and ready.takeover_adds_self_check
    wrong = checks.check_machine(request(machine=machine("windows", vm_mor="vm-9", vc_id="other"), kind=checks.KIND_TAKEOVER),
                                 lambda target: endpoint)
    assert wrong.state == checks.BAD and "different machine record" in wrong.what
    gone = checks.check_machine(request("windows", kind=checks.KIND_TAKEOVER), lambda target: MockExecutor())
    assert gone.state == checks.BAD and "was not found" in gone.what


# ---------------------------------------------------------------------------------------------
# The engine hooks the runner relies on
# ---------------------------------------------------------------------------------------------
def _plan(**more):
    values = dict(machine_id="res-1", name="db-01", address="10.20.2.21", os="linux", vm_mor="vm-1004",
                  vc_id="423b-81f0-91a2-0003", username="svc", password="pw", collector=GROUP,
                  choices=c.CollectChoices(keys=list(c.USUAL_SET)))
    values.update(more)
    return p.MachinePlan(**values)


def _workflow(executor, plan=None, **options):
    plan = plan or _plan()
    env = CONN.environment(plan.collector)
    opts = plan.options().model_copy(update=options)
    return ConfigureEndpointWorkflow(environment=env, target=plan.target(), monitoring=plan.monitoring(), executor=executor,
                                     adapter=MockVCFOpsIntegration(env), options=opts)


def test_a_stop_is_honoured_before_the_machine_is_changed_and_never_after():
    executor = MockExecutor(telegraf_installed=False)
    workflow = _workflow(executor)
    asked = []
    workflow.should_stop = lambda: asked.append(1) or len(asked) > 5   # all five preparing stages run, then stop
    summary = workflow.run()
    assert summary.stopped and not summary.success
    assert [s.stage for s in summary.stages] == [WorkflowStage.CONNECT, WorkflowStage.DETECT, WorkflowStage.PREPARE_VCF,
                                                WorkflowStage.RENDER_INPUTS, WorkflowStage.VALIDATE]
    assert not executor.uploaded_files and not executor.telegraf_installed
    assert "STOPPED BEFORE ANY CHANGE" in summary.to_markdown()
    # once the apply stage has begun the run finishes
    executor = MockExecutor(telegraf_installed=False)
    workflow = _workflow(executor)
    phases = []
    workflow.phase_callback = phases.append
    stop = threading.Event()
    workflow.should_stop = stop.is_set
    original = workflow.apply
    workflow.apply = lambda: (stop.set(), original())[1]
    summary = workflow.run()
    assert summary.success and not summary.stopped and phases == ["install", "write"]
    # a practice run can be stopped at any stage
    workflow = _workflow(MockExecutor(telegraf_installed=False), dry_run=True)
    seen = []
    workflow.should_stop = lambda: seen.append(1) or len(seen) > 6
    assert workflow.run().stopped


def test_telegraf_sent_from_this_computer_is_checked_on_the_machine_before_it_is_installed(tmp_path):
    class Receives(MockExecutor):
        """A machine that installs Telegraf from the copy it was sent."""

        def execute(self, command, timeout=30):
            if ("sha256sum -c" in command and "tar -xzf" in command) or "Get-FileHash" in command:
                self.executed_commands.append(command)
                self.telegraf_installed, self.service_active, self.telegraf_version = True, True, "Telegraf 1.40.1"
                return CommandResult(exit_code=0, command=command)
            return super().execute(command, timeout)

    package = tmp_path / package_name("1.40.1", False, False)
    package.write_bytes(b"pretend archive")
    sha = hashlib.sha256(b"pretend archive").hexdigest()
    executor = Receives(telegraf_installed=False)
    workflow = _workflow(executor, _plan(install=p.PUSH))
    asked = []
    workflow.package_fetcher = lambda version, windows, arm: asked.append((version, windows, arm)) or (package, sha)
    summary = workflow.run()
    assert summary.success and asked == [("1.40.1", False, False)]
    remote = f"/tmp/{package.name}"
    assert executor.uploaded_files[remote] == "pretend archive"
    install = next(cmd for cmd in executor.executed_commands if "sha256sum -c" in cmd)
    assert f'echo "{sha}  {remote}" | sha256sum -c -' in install and f'tar -xzf "{remote}"' in install
    assert "curl" not in install and "apt-get" not in install, "the machine downloads nothing"
    # Windows: the same, with the Windows tools
    win = Receives(telegraf_installed=False)
    workflow = _workflow(win, _plan(os="windows", install=p.PUSH, port=5985))
    workflow.package_fetcher = lambda version, windows, arm: (package, sha)
    assert workflow.run().success
    install = next(cmd for cmd in win.executed_commands if "Get-FileHash" in cmd)
    assert sha in install and "Invoke-WebRequest" not in install
    # and when this computer cannot get the package, the run says so and the machine is untouched
    broken = MockExecutor(telegraf_installed=False)
    workflow = _workflow(broken, _plan(install=p.PUSH))
    workflow.package_fetcher = lambda *a: (_ for _ in ()).throw(RuntimeError("the download site answered HTTP 503"))
    summary = workflow.run()
    assert not summary.success and "could not be sent from this computer" in summary.stages[-1].message
    assert not broken.uploaded_files


def test_package_download_is_checked_for_size_and_kept_for_next_time(tmp_path):
    class Response:
        def __init__(self, body, status=200, length=None):
            self.body, self.status_code = body, status
            self.headers = {"Content-Length": str(len(body) if length is None else length)}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def iter_content(self, chunk_size=1):
            yield self.body

    class Session:
        def __init__(self, response):
            self.response, self.urls = response, []

        def get(self, url, **kwargs):
            self.urls.append(url)
            return self.response

    session = Session(Response(b"archive-bytes"))
    path, sha = fetch_telegraf_package("1.40.1", False, False, directory=tmp_path, session=session)
    assert path.name == "telegraf-1.40.1_linux_amd64.tar.gz" and path.read_bytes() == b"archive-bytes"
    assert sha == hashlib.sha256(b"archive-bytes").hexdigest()
    assert session.urls == ["https://dl.influxdata.com/telegraf/releases/telegraf-1.40.1_linux_amd64.tar.gz"]
    again = Session(Response(b"never asked"))
    assert fetch_telegraf_package("1.40.1", False, False, directory=tmp_path, session=again) == (path, sha) and not again.urls
    assert package_name("1.34.0", True, True) == "telegraf-1.34.0_windows_arm64.zip"
    with pytest.raises(RuntimeError, match="cut short"):
        fetch_telegraf_package("1.34.0", True, False, directory=tmp_path, session=Session(Response(b"half", length=99)))
    assert not (tmp_path / "telegraf-1.34.0_windows_amd64.zip").exists()
    with pytest.raises(RuntimeError, match="HTTP 404"):
        fetch_telegraf_package("9.9.9", False, False, directory=tmp_path, session=Session(Response(b"", status=404)))


# ---------------------------------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------------------------------
def _runner(plans, executors, **more):
    return batch.BatchRunner(plans, CONN, lambda env: MockVCFOpsIntegration(env), lambda target: executors[target.hostname], **more)


def test_each_machine_gets_its_own_outcome_and_one_failure_does_not_stop_the_others():
    good, bad, slow = MockExecutor(telegraf_installed=False), MockExecutor(telegraf_installed=False, collector_reachable=False), \
        MockExecutor(telegraf_installed=False)
    plans = [_plan(machine_id="a", name="a", address="h-a"), _plan(machine_id="b", name="b", address="h-b"),
             _plan(machine_id="c", name="c", address="h-c", found=["nginx"])]
    steps, done, changing = [], [], []
    runner = _runner(plans, {"h-a": good, "h-b": bad, "h-c": slow}, on_step=lambda m, s: steps.append((m, s)),
                     on_done=lambda m, o: done.append(m), on_changing=changing.append)
    outcomes = runner.run()
    assert outcomes["a"].ok and outcomes["a"].msg == "Set up. Data is arriving in VCF Operations."
    assert outcomes["a"].collecting == c.USUAL_SET and outcomes["a"].telegraf == "1.40.1"
    assert not outcomes["b"].ok and "could not reach the collector" in outcomes["b"].msg and "Try again" in outcomes["b"].todo
    assert outcomes["c"].ok and outcomes["c"].note and "NGINX web server was found here but is not watched" in outcomes["c"].msg
    assert sorted(done) == ["a", "b", "c"] and sorted(changing) == ["a", "c"]
    assert [s for m, s in steps if m == "a"] == sorted(s for m, s in steps if m == "a") and ("a", 4) in steps
    assert not bad.uploaded_files and "[5/8 Validating config] FAIL" in outcomes["b"].log
    entry = batch.build_entry("setup", plans, outcomes, CONN, left_out=[batch.LeftOut(machine_id="d", name="d", why="You left it out")])
    assert (entry.total, entry.worked, entry.failed, entry.not_done) == (4, 2, 1, 1)
    assert entry.row_for("d").msg == "Left out. You left it out. Nothing was done to it." and not entry.row_for("d").cmd
    assert entry.row_for("b").cmd.startswith("vcf-telegraf-helper run"), "a failed run still shows the command that was tried"
    assert "ops-secret" not in entry.model_dump_json() and "pw" not in entry.report.split()
    assert "| a | **Worked.**" in entry.report and "## Do this again from the command line" in entry.report
    assert entry.choices["collect"]["keys"] == c.USUAL_SET


def test_first_data_not_yet_shown_is_waiting_not_success():
    executor = MockExecutor(telegraf_installed=False)
    runner = batch.BatchRunner([_plan()], CONN, lambda env: MockVCFOpsIntegration(env, ingestion_status="UNKNOWN"),
                               lambda target: executor)
    outcome = runner.run()["res-1"]
    assert outcome.ok and outcome.pending and "Waiting for first data" in outcome.msg and "arriving" not in outcome.msg


def test_stop_skips_machines_not_started_and_two_run_at_a_time():
    running, peak, lock = [0], [0], threading.Lock()
    release = threading.Event()

    class Tracked(MockExecutor):
        def test_connection(self):
            with lock:
                running[0] += 1
                peak[0] = max(peak[0], running[0])
            release.wait(5)
            with lock:
                running[0] -= 1
            return True

    plans = [_plan(machine_id=str(i), name=f"m{i}", address=f"h{i}") for i in range(5)]
    executors = {f"h{i}": Tracked(telegraf_installed=False) for i in range(5)}
    runner = _runner(plans, executors)
    thread = threading.Thread(target=runner.run)
    thread.start()
    while peak[0] < 2:
        pass
    runner.stop()
    release.set()
    thread.join(20)
    assert peak[0] == 2, "two machines are worked on at the same moment, never more"
    skipped = [m for m, o in runner.outcomes.items() if o.skipped]
    assert len(skipped) == 5 and all(not e.uploaded_files for e in executors.values())
    assert runner.outcomes["4"].msg == batch.STOPPED_TEXT
    entry = batch.build_entry("setup", plans, runner.outcomes, CONN)
    assert entry.worked == 0 and entry.not_done == 5 and not any(r.cmd for r in entry.rows)


def test_practice_run_changes_nothing_and_change_is_confirmed_by_reading_back():
    executor = MockExecutor(telegraf_installed=False)
    outcome = _runner([_plan(practice=True)], {"10.20.2.21": executor}).run()["res-1"]
    assert outcome.ok and outcome.msg == "Would be set up without problems."
    assert not executor.uploaded_files and "practice run: nothing was written" in outcome.log
    assert batch.run_title("setup", practice=True) == "Practice run: Set up monitoring"
    assert batch.run_title("takeover", resume=True) == "Finish a takeover"
    # a real setup, then a change that is read back from the machine
    _runner([_plan()], {"10.20.2.21": executor}).run()
    change = _plan(kind=p.KIND_CHANGE, install=p.KEEP, choices=c.CollectChoices(keys=["cpu", "mem", "diskio"]))
    outcome = _runner([change], {"10.20.2.21": executor}).run()["res-1"]
    assert outcome.ok and outcome.msg.startswith("Change applied. The app read the settings back from the machine")
    deployed = executor.uploaded_files["/etc/telegraf/telegraf.d/vcf-helper-system.conf"]
    assert "[[inputs.diskio]]" in deployed and "[[inputs.swap]]" not in deployed
    same = _runner([change], {"10.20.2.21": executor}).run()["res-1"]
    assert same.ok and "already had exactly these settings" in same.msg, "nothing restarted, so it does not say it did"

    class Forgetful(MockExecutor):
        def download(self, path):
            text = super().download(path)
            return text.replace("[[inputs.diskio]]", "") if path.endswith("vcf-helper-system.conf") else text

    lying = Forgetful(telegraf_installed=True)
    outcome = _runner([change], {"10.20.2.21": lying}).run()["res-1"]
    assert not outcome.ok and "not what was asked for" in outcome.msg


def test_takeover_through_the_runner_and_a_practice_run_that_touches_no_record(tmp_path):
    endpoint = ManagedWindowsEndpoint()
    adapters = []

    def adapter(env):
        adapters.append(OpsWithUninstall(env, endpoint, polls_to_finish=1, flip_after_polls=0))
        return adapters[-1]

    plan = _plan(kind=p.KIND_TAKEOVER, os="windows", name="dbdemo01", address="172.17.0.2", vm_mor=VM_MOR, vc_id=VC_ID,
                 username="administrator", port=5985)
    waits = {"uninstall_poll_seconds": 0, "continuity_poll_seconds": 0, "continuity_wait_seconds": 5}
    journal = tmp_path / "takeovers"
    steps = []
    practice = batch.BatchRunner([plan.model_copy(update={"practice": True})], CONN, adapter, lambda target: endpoint,
                                 on_step=lambda m, s: steps.append(s), journal_dir=journal, takeover_waits=waits)
    outcome = practice.run()["res-1"]
    assert outcome.ok and outcome.msg == "Would be taken over without problems."
    assert sorted(set(steps)) == [0, 1, 2], "reading, saving and validating are reported as they happen"
    assert endpoint.managed and not adapters[-1].uninstall_calls, "a practice run removes nothing"
    assert TakeoverJournal(journal).list_records() == [], "and leaves no record of a takeover"
    rehearsal = batch.build_entry("takeover", [plan.model_copy(update={"practice": True})], practice.outcomes, CONN, practice=True)
    assert not rehearsal.rows[0].cmd and "--take-over-managed-agent" not in rehearsal.report, \
        "the command line has no practice run for a takeover, so a practice run never hands out the real command"

    changing = []
    real = batch.BatchRunner([plan], CONN, adapter, lambda target: endpoint, on_changing=changing.append,
                             confirm_text="Typed 'dbdemo01'", journal_dir=journal, takeover_waits=waits)
    outcome = real.run()["res-1"]
    assert outcome.ok and not outcome.pending and "sending data to the same machine record" in outcome.msg
    assert changing == ["res-1"] and not endpoint.managed
    assert [state for _text, state in outcome.checks] == ["ok", "ok", "ok", "ok"] and outcome.backup_dir
    record = TakeoverJournal(journal).list_records()[0]
    assert record.confirmation_text == "Typed 'dbdemo01'" and record.state == "verified"
    entry = batch.build_entry("takeover", [plan], real.outcomes, CONN)
    assert entry.choices["takeover_checks"][0] == ["The old agent is gone from the machine", "ok"]
    assert "--take-over-managed-agent --confirm-takeover dbdemo01" in entry.rows[0].cmd
    assert batch.takeover_checks({"Managed agent retirement": "PASS", "Fresh metric ingestion": "PENDING (x)"})[3][1] == "wait"


def test_takeover_that_cannot_be_prepared_changes_nothing_and_says_so(tmp_path):
    endpoint = ManagedWindowsEndpoint()
    endpoint.collector_reachable = False
    plan = _plan(kind=p.KIND_TAKEOVER, os="windows", name="dbdemo01", address="172.17.0.2", vm_mor=VM_MOR, vc_id=VC_ID,
                 username="administrator", port=5985)
    runner = batch.BatchRunner([plan], CONN, lambda env: OpsWithUninstall(env, endpoint), lambda target: endpoint,
                               confirm_text="x", journal_dir=tmp_path / "takeovers")
    outcome = runner.run()["res-1"]
    assert not outcome.ok and "cannot reach the collector" in outcome.msg and "own agent is still running" in outcome.todo
    assert endpoint.managed
    assert outcome.checks[0][1] == "bad"


def test_stage_results_keep_their_status_for_the_log():
    summary = _workflow(MockExecutor(telegraf_installed=False), dry_run=True).run()
    assert summary.stages[-1].status == StageStatus.SKIPPED and WorkflowOptions().send_telegraf is False


def test_data_is_arriving_is_said_only_when_vcf_operations_confirmed_it():
    class Unsure(MockVCFOpsIntegration):
        def verify_ingestion(self, *args, **kwargs):
            raise RuntimeError("HTTP 502 from VCF Operations")

    executor = MockExecutor(telegraf_installed=False)
    outcome = batch.BatchRunner([_plan()], CONN, lambda env: Unsure(env), lambda target: executor).run()["res-1"]
    assert outcome.ok and outcome.pending, "set up, but nobody saw data arrive"
    assert "Waiting for first data" in outcome.msg and "Data is arriving" not in outcome.msg


def test_a_database_password_never_reaches_the_log_the_report_or_the_history(tmp_path):
    from vcf_ops_telegraf_helper.storage.history import HistoryStore

    secret, old_secret = "p@ss w;rd", "0ld@pa ss"
    executor = MockExecutor(telegraf_installed=True)
    before = c.CollectChoices(keys=["cpu", "mysql", "pg"], details={
        "mysql": {"user": "mon", "password": old_secret}, "pg": {"user": "mon", "password": old_secret}})
    executor.uploaded_files["/etc/telegraf/telegraf.d/vcf-helper-system.conf"] = TelegrafRenderer.render_system_inputs(
        c.monitoring_for(before, "linux"))
    change = _plan(kind=p.KIND_CHANGE, install=p.KEEP, choices=c.CollectChoices(keys=["cpu", "mem", "mysql", "pg", "mssql"], details={
        "mysql": {"user": "mon", "password": secret}, "pg": {"user": "mon", "password": secret},
        "mssql": {"user": "mon", "password": secret}}))
    runner = _runner([change], {"10.20.2.21": executor})
    outcome = runner.run()["res-1"]
    assert outcome.ok, outcome.msg
    deployed = executor.uploaded_files["/etc/telegraf/telegraf.d/vcf-helper-system.conf"]
    assert secret in deployed, "the machine itself does get the password"
    entry = batch.build_entry("change", [change], runner.outcomes, CONN)
    store = HistoryStore(tmp_path / "history.json")
    store.add(entry)
    written = (tmp_path / "history.json").read_text()
    for text in (outcome.log, outcome.report, entry.report, entry.log, entry.rows[0].cmd, written):
        for word in (secret, old_secret, "w;rd", "pa ss", "p@ss", "0ld@pa"):
            assert word not in text, f"{word!r} was written down"
    assert "Requested input changes" in outcome.report and 'servers = "[REDACTED]"' in outcome.report


def test_two_machines_can_be_sent_the_same_package_at_once(tmp_path):
    import time as clock

    class Slow:
        status_code = 200
        headers = {"Content-Length": "9"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def iter_content(self, chunk_size=1):
            clock.sleep(0.05)
            yield b"archive-1"

    class Session:
        calls = 0

        def get(self, url, **kwargs):
            Session.calls += 1
            return Slow()

    results, errors = [], []

    def fetch():
        try:
            results.append(fetch_telegraf_package("1.40.1", False, False, directory=tmp_path, session=Session()))
        except Exception as exc:  # the test reports it below
            errors.append(exc)

    threads = [threading.Thread(target=fetch) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert not errors and len(set(results)) == 1 and results[0][0].read_bytes() == b"archive-1"
    assert Session.calls == 1, "the first download is kept; the others use it"
    assert not list(tmp_path.glob("*.part"))


def test_the_package_goes_to_a_folder_only_this_sign_in_can_write_to(tmp_path):
    package = tmp_path / package_name("1.40.1", False, False)
    package.write_bytes(b"pretend archive")
    sha = hashlib.sha256(b"pretend archive").hexdigest()

    class Receives(MockExecutor):
        def execute(self, command, timeout=30):
            if command.startswith("mktemp -d /tmp/vcf-telegraf."):
                return CommandResult(exit_code=0, stdout="/tmp/vcf-telegraf.Ab12Cd\n", command=command)
            if "sha256sum -c" in command:
                self.executed_commands.append(command)
                self.telegraf_installed, self.service_active, self.telegraf_version = True, True, "Telegraf 1.40.1"
                return CommandResult(exit_code=0, command=command)
            return super().execute(command, timeout)

    executor = Receives(telegraf_installed=False)
    workflow = _workflow(executor, _plan(install=p.PUSH))
    workflow.package_fetcher = lambda version, windows, arm: (package, sha)
    assert workflow.run().success
    remote = f"/tmp/vcf-telegraf.Ab12Cd/{package.name}"
    assert remote in executor.uploaded_files
    install = next(cmd for cmd in executor.executed_commands if "sha256sum -c" in cmd)
    assert f'"{sha}  {remote}"' in install and 'rmdir "/tmp/vcf-telegraf.Ab12Cd"' in install
    # a Windows temp folder with an apostrophe in it does not break the install script
    win = MockExecutor(telegraf_installed=False, custom_responses={"$env:TEMP": CommandResult(exit_code=0, stdout="C:\\Users\\O'Brien\\Temp\n")})
    workflow = _workflow(win, _plan(os="windows", install=p.PUSH, port=5985))
    workflow.package_fetcher = lambda version, windows, arm: (package, sha)
    workflow.run()
    script = next(cmd for cmd in win.executed_commands if "Get-FileHash" in cmd)
    assert "$destZip = 'C:\\Users\\O''Brien\\Temp\\" in script
