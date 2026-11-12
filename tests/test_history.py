"""The history of what the app has done, and the plain words built from it."""

from __future__ import annotations

import json
import time

from vcf_ops_telegraf_helper.models.vcf import VirtualMachineResource
from vcf_ops_telegraf_helper.models.workflow import StageResult, StageStatus, TakeoverStage, WorkflowStage
from vcf_ops_telegraf_helper.storage.history import ActivityEntry, ActivityRow, HistoryStore, format_when
from vcf_ops_telegraf_helper.workflow import plain


def _row(machine="m1", **more):
    return ActivityRow(machine_id=machine, name=f"name-{machine}", **more)


def test_entries_are_kept_newest_first_and_survive_reopening(tmp_path):
    path = tmp_path / "state" / "history.json"
    store = HistoryStore(path)
    assert store.entries() == []
    store.add(ActivityEntry(id="old", at=100.0, title="Set up monitoring", kind="setup", rows=[_row()]))
    store.add(ActivityEntry(id="new", at=200.0, title="Test connection", kind="test", rows=[_row()]))
    again = HistoryStore(path)
    assert [e.id for e in again.entries()] == ["new", "old"]
    assert again.get("old").title == "Set up monitoring" and again.get("missing") is None
    assert oct(path.stat().st_mode & 0o777) == "0o600"


def test_an_entry_with_the_same_id_replaces_the_earlier_one(tmp_path):
    store = HistoryStore(tmp_path / "history.json")
    store.add(ActivityEntry(id="t1", title="Test connection", kind="test", rows=[_row(ok=False)]))
    store.add(ActivityEntry(id="t1", title="Test connection", kind="test", rows=[_row(ok=True)]))
    assert len(store.entries()) == 1 and store.entries()[0].rows[0].ok


def test_counts_separate_worked_failed_and_not_done():
    entry = ActivityEntry(title="Set up monitoring", kind="setup", rows=[
        _row("a"), _row("b", pending=True), _row("c", ok=False), _row("d", left=True), _row("e", skipped=True)])
    assert (entry.total, entry.worked, entry.failed, entry.not_done) == (5, 2, 1, 2)


def test_last_change_ignores_tests_practice_runs_and_machines_left_out(tmp_path):
    store = HistoryStore(tmp_path / "history.json")
    store.add(ActivityEntry(id="setup", at=10.0, title="Set up monitoring", kind="setup", rows=[_row(ok=False)]))
    store.add(ActivityEntry(id="test", at=20.0, title="Test connection", kind="test", rows=[_row()]))
    store.add(ActivityEntry(id="practice", at=30.0, title="Practice run", kind="setup", practice=True, rows=[_row()]))
    store.add(ActivityEntry(id="left", at=40.0, title="Set up monitoring", kind="setup", rows=[_row(left=True)]))
    assert store.last_change("m1").id == "setup"
    assert [e.id for e in store.for_machine("m1")] == ["left", "practice", "test", "setup"]
    assert store.last_change("other") is None


def test_facts_are_merged_and_a_none_forgets(tmp_path):
    store = HistoryStore(tmp_path / "history.json")
    store.remember("m1", collecting=["cpu"], telegraf="1.40.1")
    store.remember("m1", collector="the collector", telegraf=None)
    assert store.facts("m1") == {"collecting": ["cpu"], "collector": "the collector"}
    assert store.facts("unknown") == {}
    store.add(ActivityEntry(title="x", kind="setup", rows=[_row()]))
    store.clear()
    assert store.entries() == [] and store.facts("m1"), "clearing the history keeps what is known about machines"


def test_a_damaged_file_is_set_aside_not_fatal(tmp_path):
    path = tmp_path / "history.json"
    path.write_text("{not json")
    store = HistoryStore(path)
    assert store.entries() == []
    assert (tmp_path / "history.damaged.json").exists()
    store.add(ActivityEntry(title="x", kind="setup", rows=[_row()]))
    assert json.loads(path.read_text())["entries"][0]["title"] == "x"


def test_when_reads_as_today_yesterday_or_a_date():
    now = time.mktime((2026, 11, 12, 15, 0, 0, 0, 0, -1))
    assert format_when(now - 3600, now) == "Today, 2:00 PM"
    assert format_when(now - 86400, now) == "Yesterday, 3:00 PM"
    assert format_when(time.mktime((2026, 11, 3, 10, 20, 0, 0, 0, -1)), now) == "Nov 3, 10:20 AM"
    assert format_when(time.mktime((2025, 12, 31, 0, 5, 0, 0, 0, -1)), now) == "Dec 31, 2025, 12:05 AM"


# ---------------------------------------------------------------------------------------------
# The six statuses
# ---------------------------------------------------------------------------------------------
def _vm(status="Not installed", managed=None, registrations=0):
    return VirtualMachineResource(resource_id="m1", name="name-m1", telegraf_status=status, managed_type=managed,
                                  agent_registrations=registrations)


def _entry(kind="setup", age=60.0, **row):
    return ActivityEntry(at=time.time() - age, title="x", kind=kind, rows=[_row(**row)])


def test_status_comes_from_vcf_operations_first():
    assert plain.machine_status(_vm()).status == plain.STATUS_NONE
    assert plain.machine_status(_vm("Reporting", "Open Source", 1)).status == plain.STATUS_SENDING
    assert plain.machine_status(_vm("Reporting", "Product Managed", 1)).status == plain.STATUS_MANAGED
    # the app remembers a failure, but VCF Operations says data is arriving: VCF Operations wins
    assert plain.machine_status(_vm("Reporting", "Open Source", 1), _entry(ok=False)).status == plain.STATUS_SENDING


def test_history_adds_needs_attention_and_waiting():
    failed = plain.machine_status(_vm(), _entry(ok=False, msg="It broke.", todo="Do this."))
    assert failed.status == plain.STATUS_ATTENTION and failed.problem.what == "It broke." and failed.problem.todo == "Do this."
    assert plain.machine_status(_vm(), _entry(pending=True)).status == plain.STATUS_WAITING
    assert plain.machine_status(_vm(), _entry()).status == plain.STATUS_WAITING
    stale = plain.machine_status(_vm(), _entry(age=2 * 3600.0, pending=True))
    assert stale.status == plain.STATUS_ATTENTION, "waiting for first data does not last for ever"
    orphan = plain.machine_status(_vm("No data", "Open Source", 1))
    assert orphan.status == plain.STATUS_ATTENTION and "no data is arriving" in orphan.problem.what


def test_removed_machine_is_not_set_up_even_while_vcf_operations_catches_up():
    removed = _entry(kind="remove")
    assert plain.machine_status(_vm("Reporting", "Open Source", 1), removed).status == plain.STATUS_NONE
    assert plain.machine_status(_vm("No data", "Open Source", 1), removed).status == plain.STATUS_NONE
    long_ago = _entry(kind="remove", age=3 * 3600.0)
    assert plain.machine_status(_vm("Reporting", "Open Source", 1), long_ago).status == plain.STATUS_SENDING


def test_takeover_records_drive_interrupted_and_waiting():
    managed = _vm("Reporting", "Product Managed", 1)
    assert plain.machine_status(managed, None, "retired").status == plain.STATUS_INTERRUPTED
    assert plain.machine_status(_vm(), None, "retire_failed").status == plain.STATUS_INTERRUPTED
    assert plain.machine_status(_vm(), None, "installed").status == plain.STATUS_WAITING
    assert plain.machine_status(_vm(), None, "verified").status == plain.STATUS_NONE
    # just taken over: VCF Operations may still describe the old agent on this reading
    assert plain.machine_status(managed, _entry(kind="takeover")).status == plain.STATUS_WAITING
    assert plain.machine_status(managed, _entry(kind="takeover", age=2 * 3600.0)).status == plain.STATUS_MANAGED


# ---------------------------------------------------------------------------------------------
# From a failed stage to a reason and something to do
# ---------------------------------------------------------------------------------------------
def _failed(stage, message, details=None):
    return [StageResult(stage=stage, status=StageStatus.FAIL, message=message, details=details)]


def test_every_failure_gets_a_reason_and_a_remedy_in_plain_words():
    cases = [
        (WorkflowStage.CONNECT, "Failed to connect to h: Authentication failed", "did not accept that user name or password"),
        (WorkflowStage.CONNECT, "Failed to connect to h: timed out", "could not reach the machine"),
        (WorkflowStage.DETECT, "Detection failed: This agent is managed by VCF Operations (ucp-telegraf)", "own agent"),
        (WorkflowStage.PREPARE_VCF, "Failed to prepare VCF Ops integration: HTTP 403", "did not hand over"),
        (WorkflowStage.RENDER_INPUTS, "Render error: Existing main configuration contains unmanaged inputs", "its own settings"),
        (WorkflowStage.VALIDATE, "Collector 10.0.0.5:443 not reachable from target", "could not reach the collector"),
        (WorkflowStage.APPLY, "Insufficient disk space on target (120 MB free, minimum 500 MB required).", "120 MB free"),
        (WorkflowStage.APPLY, "Target Linux user lacks passwordless sudo privileges", "not allowed to install software"),
        (WorkflowStage.APPLY, "Failed to auto-install Telegraf agent: curl: (6) Could not resolve host", "could not download Telegraf"),
        (WorkflowStage.APPLY, "Failed to auto-install Telegraf agent: The Telegraf package sent from this computer did not "
                              "arrive intact (checksum mismatch)", "did not arrive on the machine intact"),
        (WorkflowStage.APPLY, "Telegraf 1.40.1 could not be sent from this computer: HTTP 503", "could not be sent"),
        (WorkflowStage.RESTART, "Telegraf config validation failed on endpoint. Restored previous configuration (.bak)", "put the previous settings back"),
        (WorkflowStage.VERIFY, "Verification failed on: Metrics transmission", "refused its data"),
        (TakeoverStage.CAPTURE, "Capture failed: No Ops-managed agent found on this endpoint", "was not found"),
        (TakeoverStage.PREFLIGHT, "Replacement cannot be prepared: Collector 10.0.0.5:443 not reachable from target", "cannot reach the collector"),
        (TakeoverStage.RETIRE, "Retirement failed: task still SUBMITTING", "did not finish removing"),
        (TakeoverStage.INSTALL, "Installation failed after the managed agent was removed", "could not be installed"),
    ]
    for stage, message, expected in cases:
        problem = plain.explain_failure(_failed(stage, message))
        assert expected in problem.what, (stage, problem.what)
        assert problem.todo and "stage" not in problem.todo.lower(), problem.todo
    nothing = plain.explain_failure([StageResult(stage=WorkflowStage.CONNECT, status=StageStatus.PASS, message="ok")])
    assert nothing.what and nothing.todo


def test_a_failed_push_install_is_not_blamed_on_the_machines_internet():
    stages = _failed(WorkflowStage.APPLY, "Failed to auto-install Telegraf agent: tar: short read")
    pulled = plain.explain_failure(stages)
    pushed = plain.explain_failure(stages, pushed=True)
    assert "no way out to the internet" in pulled.what
    assert "internet" not in pushed.what and "sent from this computer" in pushed.what
    assert "offer to download" not in pushed.todo
    intact = _failed(WorkflowStage.APPLY, "The Telegraf package sent from this computer did not arrive intact (checksum mismatch)")
    assert plain.explain_failure(intact, pushed=True) == plain.explain_failure(intact)


def test_step_words_match_what_really_happens_to_the_machine():
    assert plain.step_label("setup", 2) == "Installing Telegraf"
    assert plain.step_label("setup", 2, install="keep") == "Keeping the Telegraf that is already installed"
    assert plain.step_label("setup", 2, install="update") == "Updating Telegraf"
    assert plain.step_label("setup", 2, install="push") == "Sending Telegraf from this computer and installing it"
    assert plain.step_label("change", 2) == "Writing the new settings"
    assert "Installing" not in " ".join(plain.PRACTICE_STEPS), "a practice run installs nothing, so it never says it does"
    assert plain.step_for_stage(WorkflowStage.VALIDATE) == 1 and plain.step_for_stage(WorkflowStage.APPLY) == 2
    assert plain.SAFE_STEPS == 2
    assert plain.takeover_step_for_stage(TakeoverStage.PREFLIGHT) == 0 and plain.takeover_step_for_stage(TakeoverStage.RETIRE) == 1
    assert len(plain.takeover_steps("1.40.1")) == 4 and "1.40.1" in plain.takeover_steps("1.40.1")[2]
    assert plain.plural(1, "machine") == "1 machine" and plain.plural(3, "machine is", "machines are") == "3 machines are"
    assert plain.join_names(["a", "b", "c"]) == "a, b and c"
