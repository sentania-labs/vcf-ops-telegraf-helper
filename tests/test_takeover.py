"""Takeover of the Ops product-managed agent: orchestration, journal, resume and failure handling."""

from __future__ import annotations

from pathlib import Path

import pytest

from vcf_ops_telegraf_helper.adapters.mock import MockVCFOpsIntegration
from vcf_ops_telegraf_helper.executors.base import CommandResult
from vcf_ops_telegraf_helper.executors.mock import MockExecutor
from vcf_ops_telegraf_helper.models.endpoint import ConnectionMethod, EndpointTarget, OSFamily
from vcf_ops_telegraf_helper.models.vcf import AgentObjectInfo, AgentTaskStatus, CollectorInfo, VCFEnvironment
from vcf_ops_telegraf_helper.models.workflow import StageStatus
from vcf_ops_telegraf_helper.storage.journal import TakeoverJournal, TakeoverRecord
from vcf_ops_telegraf_helper.workflow.takeover import (
    RESULT_INGESTION,
    RESULT_INSTALL,
    RESULT_REGISTRATION,
    RESULT_RETIREMENT,
    TakeoverOptions,
    TakeoverStage,
    TakeoverWorkflow,
)
from vcf_ops_telegraf_helper.workflow.windows import MANAGED_GRAINS, MANAGED_TELEGRAF_CONF

FIXTURE = Path(__file__).parent / "fixtures" / "managed-telegraf-vcf91-windows.conf"

VC_ID = "423b-81f0-91a2-0001"
VM_MOR = "vm-1001"  # dbdemo01 in the mock inventory, Product Managed
GRAINS = f"arc_virtual_ip: 10.10.10.50\nminion_id: {VC_ID}_{VM_MOR}\nvc_id: {VC_ID}\nvm_id: {VM_MOR}\nvm_name: dbdemo01\n"

MANAGED_SERVICES = (
    "salt-minion|Running|C:\\VMware\\UCP\\salt\\nssm.exe\n"
    "ucp-minion|Running|C:\\VMware\\UCP\\salt\\nssm.exe\n"
    "ucp-telegraf|Running|C:\\VMware\\UCP\\ucp-telegraf\\telegraf.exe --config C:\\VMware\\UCP\\ucp-telegraf\\telegraf.conf\n"
)


class ManagedWindowsEndpoint(MockExecutor):
    """A Windows VM carrying the Ops-managed agent; `retire()` is what the Ops uninstall does to it."""

    def __init__(self, grains: str = GRAINS):
        super().__init__(telegraf_installed=True, telegraf_version="Telegraf 1.39.0 (git: vmware-latest-telegraf-arc-fips@abc)")
        self.managed = True
        self.remnant_root = False
        self.remnant_files = 0
        self.install_fails = False
        self.service_query_fails = False
        self.files = {MANAGED_TELEGRAF_CONF: FIXTURE.read_text(), MANAGED_GRAINS: grains,
                      "C:\\VMware\\UCP\\ucp-telegraf\\mandatory_tags.bat": "@echo off\r\n"}
        self.removed: list[str] = []

    def retire(self) -> None:
        self.managed = False
        self.remnant_root = True
        self.files = {}
        self.telegraf_installed = False  # the Ops uninstall takes its telegraf with it

    def execute(self, command, timeout=30):
        self.executed_commands.append(command)
        if "Invoke-WebRequest" in command and "telegraf" in command:
            if self.install_fails:
                return CommandResult(exit_code=1, stderr="download failed: 404", command=command)
            # the auto-install puts the open-source agent and its service in place
            self.telegraf_installed = True
            self.service_active = True
            return CommandResult(exit_code=0, stdout="", command=command)
        if "Win32_Service" in command and "-in @(" in command:
            if self.service_query_fails:
                return CommandResult(exit_code=1, stderr="Access denied", command=command)
            return CommandResult(exit_code=0, stdout=MANAGED_SERVICES if self.managed else "", command=command)
        if "Win32_Service" in command and "PathName -like '*\\VMware\\UCP\\*'" in command and "ExpandProperty Name" in command:
            return CommandResult(exit_code=0, stdout="", command=command)
        if "Win32_Service" in command:
            if self.managed:
                return CommandResult(exit_code=0, stdout=MANAGED_SERVICES.splitlines()[2] + "\n", command=command)
            return CommandResult(exit_code=0, stdout="", command=command)
        if command.startswith("Test-Path -Path 'C:\\VMware\\UCP'"):
            return CommandResult(exit_code=0, stdout="True\n" if (self.managed or self.remnant_root) else "False\n", command=command)
        if command.startswith("Test-Path -Path 'C:\\ProgramData\\VMware\\UCP\\certkeys'"):
            return CommandResult(exit_code=0, stdout="True\n" if self.managed else "False\n", command=command)
        if "Remove-Item -Path 'C:\\VMware\\UCP'" in command:
            if self.remnant_files:
                return CommandResult(exit_code=0, stdout=f"LEFT {self.remnant_files}\n", command=command)
            self.removed.append("C:\\VMware\\UCP")
            self.remnant_root = False
            return CommandResult(exit_code=0, stdout="REMOVED\n", command=command)
        if "Get-ChildItem" in command and "telegraf.d" in command:
            return CommandResult(exit_code=0, stdout="", command=command)
        return super().execute(command, timeout)

    def file_exists(self, path):
        return path in self.files or super().file_exists(path)

    def download(self, path):
        if path in self.files:
            return self.files[path]
        return super().download(path)


class OpsWithUninstall(MockVCFOpsIntegration):
    """Mock Ops whose uninstall task actually retires the endpoint and flips the object."""

    def __init__(self, env, endpoint, polls_to_finish=2, flip_after_polls=1, fail_task=False, new_object=False):
        super().__init__(env)
        self.endpoint = endpoint
        self.polls_to_finish = polls_to_finish
        self.flip_after_polls = flip_after_polls
        self.fail_task = fail_task
        self.new_object = new_object
        self.uninstall_calls = []
        self._task_polls = 0
        self._object_polls = 0
        self.retired = False

    def uninstall_managed_agent(self, vm_resource_id, guest_username, guest_password, retain_config=False):
        self.uninstall_calls.append((vm_resource_id, guest_username, guest_password, retain_config))
        return "task-1"

    def get_agent_task_status(self, task_id):
        self._task_polls += 1
        if self.fail_task:
            return AgentTaskStatus(task_id=task_id, stage="SUBMITTING", messages=["Guest credentials rejected"])
        if self._task_polls >= self.polls_to_finish:
            if not self.retired:
                self.retired = True
                self.endpoint.retire()
            return AgentTaskStatus(task_id=task_id, stage="FINISHED")
        return AgentTaskStatus(task_id=task_id, stage="SUBMITTING")

    def get_agent_object(self, vc_id, vm_mor, include_stat_keys=False):
        base = super().get_agent_object(vc_id, vm_mor, include_stat_keys=include_stat_keys)
        if base is None:
            return None
        if not self.retired:
            return base
        self._object_polls += 1
        import time
        flipped = self._object_polls > self.flip_after_polls
        return AgentObjectInfo(
            resource_id="agent-new" if self.new_object else base.resource_id,
            name=base.name, resource_kind="win",
            managed_type="Open Source" if flipped else "Product Managed",
            receiving=True,
            stat_key_count=137 if include_stat_keys else None,
            last_sample_ms=int(time.time() * 1000) + 5_000 if flipped else int(time.time() * 1000) - 600_000,
        )


def _env():
    return VCFEnvironment(name="t", url="https://ops.local", username="admin", token="tok",
                          collector=CollectorInfo(address="10.10.10.50"))


def _target(**overrides):
    base = dict(hostname="172.17.0.2", os_family=OSFamily.WINDOWS, connection_method=ConnectionMethod.WINRM,
                username="administrator@lab.local", password="pw", vm_mor=VM_MOR, vc_id=VC_ID,
                registered_hostname="dbdemo01")
    base.update(overrides)
    return EndpointTarget(**base)


def _workflow(tmp_path, endpoint=None, adapter=None, target=None, **opts):
    env = _env()
    endpoint = endpoint or ManagedWindowsEndpoint()
    adapter = adapter or OpsWithUninstall(env, endpoint)
    settings = dict(confirmation_text="take over dbdemo01", journal_dir=tmp_path / "journal",
                    uninstall_poll_seconds=0, continuity_poll_seconds=0, continuity_wait_seconds=5)
    settings.update(opts)
    options = TakeoverOptions(**settings)
    return TakeoverWorkflow(environment=env, target=target or _target(), monitoring=None, executor=endpoint,
                            adapter=adapter, takeover=options, sleep=lambda s: None)


def test_full_takeover_reports_four_results_and_keeps_the_object(tmp_path):
    wf = _workflow(tmp_path)
    summary = wf.run()
    assert summary.success, [(s.stage, s.message) for s in summary.stages]
    assert [s.status for s in summary.stages] == [StageStatus.PASS] * 7
    assert summary.results == {
        RESULT_RETIREMENT: "PASS", RESULT_INSTALL: "PASS", RESULT_REGISTRATION: "PASS", RESULT_INGESTION: "PASS",
    }
    # the guest credential went to Ops as typed, with full removal requested
    assert wf.adapter.uninstall_calls == [("res-vm-001", "administrator@lab.local", "pw", False)]
    # the imported configuration drove the install: counters, win. totals and the w3wp instance
    system = wf.install_workflow.system_conf_content
    assert "[[inputs.win_perf_counters]]" in system and 'name_prefix = "win."' in system and '"w3wp"' in system
    output = wf.install_workflow.vcf_conf_content
    assert wf.install_summary.managed_files[0] == "C:\\telegraf\\telegraf.d\\vcf-helper-system.conf"
    assert f'vmId = "{VM_MOR}"' in output and f'vcid = "{VC_ID}"' in output
    assert "C:\\VMware\\UCP" in wf.executor.removed
    assert summary.agent_object_before.resource_id == summary.agent_object_after.resource_id
    assert summary.verifications["Same Ops object"] == "PASS"
    assert summary.verifications["Agent type Open Source"] == "PASS"
    assert summary.verifications["Managed services absent"] == "PASS"
    # journal and backup, without secrets
    record = TakeoverJournal(tmp_path / "journal").load(VC_ID, VM_MOR)
    assert record.state == "verified" and record.confirmation_text == "take over dbdemo01"
    assert record.uninstall_task_id == "task-1" and record.managed_services == ["salt-minion", "ucp-minion", "ucp-telegraf"]
    everything = summary.to_markdown() + summary.to_json() + "".join(
        p.read_text(errors="ignore") for p in Path(summary.journal_path).parent.rglob("*") if p.is_file()
    )
    for secret in ("pw", "tok"):
        assert f'"{secret}"' not in everything and f"={secret}" not in everything and f" {secret}\n" not in everything
    assert '"password"' not in everything and "token" not in Path(summary.journal_path).read_text()
    assert (Path(record.backup_dir) / "telegraf.conf").read_text() == FIXTURE.read_text()
    assert (Path(record.backup_dir) / "grains").exists()
    assert "Preserved:" in summary.to_markdown()


def test_declining_or_missing_identity_changes_nothing(tmp_path):
    wf = _workflow(tmp_path, target=_target(vm_mor=None, vc_id=None))
    summary = wf.run()
    assert not summary.success and summary.stages[0].status == StageStatus.FAIL
    assert "inventory" in summary.stages[0].message
    assert wf.adapter.uninstall_calls == [] and not wf.executor.uploaded_files
    assert summary.results[RESULT_RETIREMENT] == "NOT RUN"
    assert not (tmp_path / "journal").exists()


def test_grains_bound_to_another_vm_blocks_before_retire(tmp_path):
    endpoint = ManagedWindowsEndpoint(grains=GRAINS.replace(VM_MOR, "vm-9999"))
    wf = _workflow(tmp_path, endpoint=endpoint)
    summary = wf.run()
    assert not summary.success and "ambiguous" in summary.stages[0].message
    assert wf.adapter.uninstall_calls == []


def test_open_source_object_is_not_retired(tmp_path):
    endpoint = ManagedWindowsEndpoint()
    adapter = OpsWithUninstall(_env(), endpoint)
    # pretend Ops already thinks the object is open source
    adapter._vms = [vm.model_copy(update={"managed_type": "Open Source"}) if vm.vm_mor == VM_MOR else vm
                    for vm in adapter.list_virtual_machines()]
    wf = _workflow(tmp_path, endpoint=endpoint, adapter=adapter)
    summary = wf.run()
    assert not summary.success and "not Product Managed" in summary.stages[0].message
    assert adapter.uninstall_calls == []


def test_no_managed_agent_and_no_journal_is_refused(tmp_path):
    endpoint = ManagedWindowsEndpoint()
    endpoint.managed = False
    wf = _workflow(tmp_path, endpoint=endpoint)
    summary = wf.run()
    assert not summary.success and "nothing to take over" in summary.stages[0].message


def test_unreadable_config_blocks_before_retire(tmp_path):
    endpoint = ManagedWindowsEndpoint()
    del endpoint.files[MANAGED_TELEGRAF_CONF]
    wf = _workflow(tmp_path, endpoint=endpoint)
    summary = wf.run()
    assert not summary.success
    assert summary.stages[1].stage == TakeoverStage.BACKUP and summary.stages[1].status == StageStatus.FAIL
    assert wf.adapter.uninstall_calls == []
    assert TakeoverJournal(tmp_path / "journal").load(VC_ID, VM_MOR).state == "failed"


def test_failed_uninstall_task_blocks_replacement(tmp_path):
    endpoint = ManagedWindowsEndpoint()
    adapter = OpsWithUninstall(_env(), endpoint, fail_task=True)
    wf = _workflow(tmp_path, endpoint=endpoint, adapter=adapter)
    summary = wf.run()
    assert not summary.success
    assert summary.stages[3].stage == TakeoverStage.RETIRE and "Guest credentials rejected" in summary.stages[3].message
    assert summary.results[RESULT_RETIREMENT] == "FAIL" and summary.results[RESULT_INSTALL] == "NOT RUN"
    assert not endpoint.uploaded_files and endpoint.managed


def test_uninstall_timeout_does_not_retry_blindly(tmp_path):
    endpoint = ManagedWindowsEndpoint()
    adapter = OpsWithUninstall(_env(), endpoint, polls_to_finish=99)
    wf = _workflow(tmp_path, endpoint=endpoint, adapter=adapter, uninstall_timeout_seconds=0)
    summary = wf.run()
    assert not summary.success and "still SUBMITTING" in summary.stages[3].message
    assert len(adapter.uninstall_calls) == 1
    record = TakeoverJournal(tmp_path / "journal").load(VC_ID, VM_MOR)
    assert record.uninstall_task_id == "task-1" and record.uninstall_task_stage == "SUBMITTING"


def test_services_left_behind_stop_before_install(tmp_path):
    endpoint = ManagedWindowsEndpoint()

    class StickyOps(OpsWithUninstall):
        def get_agent_task_status(self, task_id):
            return AgentTaskStatus(task_id=task_id, stage="FINISHED")  # Ops says done, endpoint still has services

    wf = _workflow(tmp_path, endpoint=endpoint, adapter=StickyOps(_env(), endpoint))
    summary = wf.run()
    assert not summary.success
    assert summary.stages[4].stage == TakeoverStage.CLEAN and "still present" in summary.stages[4].message
    assert not endpoint.uploaded_files and not endpoint.removed


def test_resume_after_interruption_skips_retire_and_installs(tmp_path):
    first = _workflow(tmp_path)
    first.capture()
    first.backup()
    first.retire()  # the app "dies" after the Ops uninstall
    assert first.record.state == "retired" and not first.executor.managed

    second = _workflow(tmp_path, endpoint=first.executor, adapter=first.adapter)
    summary = second.run()
    assert summary.success, [(s.stage, s.message) for s in summary.stages]
    assert summary.stages[0].status == StageStatus.WARNING and "resuming" in summary.stages[0].message
    assert summary.stages[1].status == StageStatus.SKIPPED and summary.stages[3].status == StageStatus.SKIPPED
    assert summary.stages[2].stage == TakeoverStage.PREFLIGHT and summary.stages[2].status == StageStatus.PASS
    assert summary.results[RESULT_RETIREMENT].startswith("PASS")
    assert summary.results[RESULT_INSTALL] == "PASS"
    assert len(first.adapter.uninstall_calls) == 1
    # the imported inputs came from the backup, not the (now gone) endpoint
    assert '"w3wp"' in second.install_workflow.system_conf_content
    record = TakeoverJournal(tmp_path / "journal").load(VC_ID, VM_MOR)
    assert any("Resumed" in n for n in record.notes)


def test_install_failure_reports_interruption_and_keeps_backup(tmp_path):
    endpoint = ManagedWindowsEndpoint()
    endpoint.install_fails = True
    wf = _workflow(tmp_path, endpoint=endpoint)
    summary = wf.run()
    assert not summary.success
    assert summary.stages[5].stage == TakeoverStage.INSTALL and "Monitoring is interrupted" in summary.stages[5].message
    assert summary.results[RESULT_RETIREMENT] == "PASS" and summary.results[RESULT_INSTALL] == "FAIL"
    record = TakeoverJournal(tmp_path / "journal").load(VC_ID, VM_MOR)
    assert record.state == "cleaned" and Path(record.backup_dir, "telegraf.conf").exists()


def test_slow_ops_reports_pending_not_failure(tmp_path):
    endpoint = ManagedWindowsEndpoint()
    adapter = OpsWithUninstall(_env(), endpoint, flip_after_polls=99)
    wf = _workflow(tmp_path, endpoint=endpoint, adapter=adapter, continuity_wait_seconds=0)
    summary = wf.run()
    assert summary.success
    assert summary.stages[6].status == StageStatus.WARNING
    assert summary.results[RESULT_REGISTRATION] == "PENDING"
    assert summary.results[RESULT_INGESTION].startswith("PENDING")
    assert summary.verifications["Same Ops object"] == "PASS"


def test_new_object_is_reported_as_changed(tmp_path):
    endpoint = ManagedWindowsEndpoint()
    adapter = OpsWithUninstall(_env(), endpoint, new_object=True)
    wf = _workflow(tmp_path, endpoint=endpoint, adapter=adapter)
    summary = wf.run()
    assert summary.success and summary.stages[6].status == StageStatus.WARNING
    assert summary.results[RESULT_REGISTRATION] == "CHANGED OBJECT"
    assert summary.verifications["Same Ops object"].startswith("CHANGED")


def test_linux_target_is_refused(tmp_path):
    wf = _workflow(tmp_path, target=_target(os_family=OSFamily.LINUX, connection_method=ConnectionMethod.SSH))
    summary = wf.run()
    assert not summary.success and "Windows" in summary.stages[0].message


def test_journal_backup_rejects_path_escapes(tmp_path):
    journal = TakeoverJournal(tmp_path / "j")
    record = TakeoverRecord(vm_mor="vm-1", vc_id="vc/1", target_hostname="h", ops_url="u")
    journal.write_backup(record, {"../../escape.txt": "x", "telegraf.d/app.conf": "y"})
    assert all(Path(f).resolve().is_relative_to((tmp_path / "j").resolve()) for f in record.backup_files)
    assert journal.load("vc/1", "vm-1") is None
    journal.save(record)
    assert journal.load("vc/1", "vm-1").vm_mor == "vm-1"
    assert journal.list_records()[0].target_hostname == "h"


@pytest.mark.parametrize("retain", [True, False])
def test_vcf91_uninstall_and_task_status_shapes(retain):
    """The real adapter sends the documented body and reads taskID (capital D) and bootstrapObjectStatuses."""
    from unittest.mock import MagicMock
    import requests
    from vcf_ops_telegraf_helper.adapters.vcf91 import VCF91OpenTelegrafIntegration

    session = MagicMock(spec=requests.Session)
    delete_resp = MagicMock(status_code=200)
    delete_resp.json.return_value = {"taskStatuses": [{"taskID": "c22ca66a", "resources": ["10c3dbdd"]}]}
    session.delete.return_value = delete_resp
    status_resp = MagicMock(status_code=200)
    status_resp.json.return_value = {"taskId": "c22ca66a", "name": "Bootstrap virtual machines",
                                     "bootstrapObjectStatuses": [{"id": "10c3dbdd", "stage": "FINISHED", "messages": []}]}
    session.get.return_value = status_resp
    adapter = VCF91OpenTelegrafIntegration(_env(), session=session)
    task = adapter.uninstall_managed_agent("10c3dbdd", "navani@lab.local", "s3cret", retain_config=retain)
    assert task == "c22ca66a"
    call = session.delete.call_args
    assert call.args[0].endswith("/suite-api/api/applications/agents")
    assert call.kwargs["json"] == {"resourceCredentials": [{"resourceId": "10c3dbdd", "username": "navani@lab.local", "password": "s3cret"}],
                                   "retainTelegrafConf": retain}
    assert call.kwargs["headers"]["Content-Type"] == "application/json"
    status = adapter.get_agent_task_status(task)
    assert status.finished and status.terminal and not status.failed
    assert session.get.call_args.args[0].endswith("/suite-api/api/applications/agents/c22ca66a/status")


def test_vcf91_uninstall_rejection_and_failed_task():
    from unittest.mock import MagicMock
    import requests
    from vcf_ops_telegraf_helper.adapters.vcf91 import VCF91OpenTelegrafIntegration

    session = MagicMock(spec=requests.Session)
    session.delete.return_value = MagicMock(status_code=400, text="bad credential", json=lambda: {"message": "Invalid credentials"})
    adapter = VCF91OpenTelegrafIntegration(_env(), session=session)
    with pytest.raises(RuntimeError, match="HTTP 400.*Invalid credentials"):
        adapter.uninstall_managed_agent("r", "u", "p")
    failed = MagicMock(status_code=200)
    failed.json.return_value = {"taskId": "t", "bootstrapObjectStatuses": [{"id": "r", "stage": "SUBMITTING", "messages": ["Connection refused"]}]}
    session.get.return_value = failed
    status = adapter.get_agent_task_status("t")
    assert status.failed and status.terminal and not status.finished


def test_vcf91_resolve_vm_resource_id():
    from test_adapters import _fake_suite_api
    from vcf_ops_telegraf_helper.adapters.vcf91 import VCF91OpenTelegrafIntegration

    adapter = VCF91OpenTelegrafIntegration(_env(), session=_fake_suite_api())
    assert adapter.resolve_vm_resource_id("vc-1", "vm-201") == "r-win"
    assert adapter.resolve_vm_resource_id("vc-1", "vm-204") is None  # deleted in vCenter
    assert adapter.resolve_vm_resource_id("vc-1", "vm-999") is None


def test_blocked_import_stops_before_retire(tmp_path):
    """Review finding: a fragment that cannot be parsed must stop the takeover before the Ops uninstall."""
    endpoint = ManagedWindowsEndpoint()
    endpoint.files["C:\\VMware\\UCP\\ucp-telegraf\\telegraf.d\\app.conf"] = "[[inputs.x]\nnot toml"
    original_execute = endpoint.execute

    def execute(command, timeout=30):
        if "Get-ChildItem" in command and "telegraf.d" in command and "Invoke-WebRequest" not in command:
            endpoint.executed_commands.append(command)
            return CommandResult(exit_code=0, stdout="app.conf\n", command=command)
        return original_execute(command, timeout)

    endpoint.execute = execute
    wf = _workflow(tmp_path, endpoint=endpoint)
    summary = wf.run()
    assert not summary.success
    assert summary.stages[0].status == StageStatus.FAIL and "cannot be ported safely" in summary.stages[0].message
    assert wf.adapter.uninstall_calls == [] and endpoint.managed


def test_finished_task_with_informational_message_is_success():
    assert AgentTaskStatus(task_id="t", stage="FINISHED", messages=["Agent uninstalled"]).failed is False
    assert AgentTaskStatus(task_id="t", stage="SUBMITTING", messages=["Connection refused"]).failed is True
    assert AgentTaskStatus(task_id="t", stage="FAILED").failed is True


def test_retire_timeout_then_ops_finishes_is_resumable(tmp_path):
    """Review finding: Ops may finish the uninstall after the app gave up; the re-run re-polls the task."""
    endpoint = ManagedWindowsEndpoint()
    adapter = OpsWithUninstall(_env(), endpoint, polls_to_finish=3)
    first = _workflow(tmp_path, endpoint=endpoint, adapter=adapter, uninstall_timeout_seconds=0)
    summary = first.run()
    assert not summary.success and summary.stages[3].status == StageStatus.FAIL
    record = TakeoverJournal(tmp_path / "journal").load(VC_ID, VM_MOR)
    assert record.state == "retire_failed" and record.uninstall_task_id == "task-1"

    # Ops finishes the task in the background and removes the agent
    adapter.get_agent_task_status("task-1")
    adapter.get_agent_task_status("task-1")
    assert endpoint.managed is False

    second = _workflow(tmp_path, endpoint=endpoint, adapter=adapter)
    summary = second.run()
    assert summary.success, [(s.stage, s.message) for s in summary.stages]
    assert summary.results[RESULT_RETIREMENT].startswith("PASS")
    assert len(adapter.uninstall_calls) == 1
    assert TakeoverJournal(tmp_path / "journal").load(VC_ID, VM_MOR).state == "verified"


def test_retire_failed_with_task_still_running_is_refused(tmp_path):
    endpoint = ManagedWindowsEndpoint()
    adapter = OpsWithUninstall(_env(), endpoint, polls_to_finish=99)
    first = _workflow(tmp_path, endpoint=endpoint, adapter=adapter, uninstall_timeout_seconds=0)
    first.run()
    endpoint.retire()  # the agent disappears while Ops still reports SUBMITTING
    second = _workflow(tmp_path, endpoint=endpoint, adapter=adapter)
    summary = second.run()
    assert not summary.success and "Reconcile in VCF Operations" in summary.stages[0].message
    assert len(adapter.uninstall_calls) == 1


def test_resume_refuses_a_different_endpoint(tmp_path):
    first = _workflow(tmp_path)
    first.capture()
    first.backup()
    first.retire()
    other = _workflow(tmp_path, endpoint=first.executor, adapter=first.adapter, target=_target(hostname="172.17.0.99"))
    summary = other.run()
    assert not summary.success and "different endpoint" in summary.stages[0].message
    assert other.install_summary is None


def test_resume_refuses_when_the_backup_is_gone(tmp_path):
    import shutil

    first = _workflow(tmp_path)
    first.capture()
    first.backup()
    first.retire()
    shutil.rmtree(first.record.backup_dir)
    second = _workflow(tmp_path, endpoint=first.executor, adapter=first.adapter)
    summary = second.run()
    assert not summary.success and "no longer holds telegraf.conf" in summary.stages[0].message
    assert second.install_summary is None


def test_missing_grains_identity_is_refused(tmp_path):
    endpoint = ManagedWindowsEndpoint(grains="arc_virtual_ip: 10.10.10.50\n")
    wf = _workflow(tmp_path, endpoint=endpoint)
    summary = wf.run()
    assert not summary.success and "grains do not name" in summary.stages[0].message
    assert wf.adapter.uninstall_calls == []


def test_non_empty_remnant_root_is_left_in_place(tmp_path):
    endpoint = ManagedWindowsEndpoint()
    endpoint.remnant_files = 7
    wf = _workflow(tmp_path, endpoint=endpoint)
    summary = wf.run()
    assert summary.success
    assert summary.stages[4].status == StageStatus.WARNING and "left in place" in summary.stages[4].message
    assert summary.verifications["Managed files removed"].startswith("LEFT IN PLACE")
    assert endpoint.removed == []


def test_fresh_sample_without_type_flip_is_not_ingestion(tmp_path):
    endpoint = ManagedWindowsEndpoint()

    class OpsStillManaged(OpsWithUninstall):
        def get_agent_object(self, vc_id, vm_mor, include_stat_keys=False):
            obj = super().get_agent_object(vc_id, vm_mor, include_stat_keys)
            if obj is not None and self.retired:
                import time
                obj.managed_type = "Product Managed"
                obj.last_sample_ms = int(time.time() * 1000) + 5_000  # Ops-computed stat, not the new agent
            return obj

    wf = _workflow(tmp_path, endpoint=endpoint, adapter=OpsStillManaged(_env(), endpoint), continuity_wait_seconds=0)
    summary = wf.run()
    assert summary.results[RESULT_INGESTION].startswith("PENDING")
    assert summary.results[RESULT_REGISTRATION] == "PENDING"


def test_backup_directory_is_fresh_per_takeover(tmp_path):
    journal = TakeoverJournal(tmp_path / "j")
    record = TakeoverRecord(vm_mor="vm-1", vc_id="vc-1", target_hostname="h", ops_url="u")
    journal.write_backup(record, {"telegraf.conf": "a", "telegraf.d/old.conf": "x"})
    journal.write_backup(record, {"telegraf.conf": "b"})
    assert not (Path(record.backup_dir) / "telegraf.d" / "old.conf").exists()
    assert (Path(record.backup_dir) / "telegraf.conf").read_text() == "b"


def test_unreachable_collector_fails_preflight_and_leaves_the_agent(tmp_path):
    """Codex review: everything the replacement needs is checked before the Ops uninstall."""
    endpoint = ManagedWindowsEndpoint()
    endpoint.collector_reachable = False
    wf = _workflow(tmp_path, endpoint=endpoint)
    summary = wf.run()
    assert not summary.success
    assert summary.stages[2].stage == TakeoverStage.PREFLIGHT and summary.stages[2].status == StageStatus.FAIL
    assert "managed agent was not touched" in summary.stages[2].message
    assert wf.adapter.uninstall_calls == [] and endpoint.managed and not endpoint.uploaded_files
    assert summary.results[RESULT_RETIREMENT] == "NOT RUN"
    assert TakeoverJournal(tmp_path / "journal").load(VC_ID, VM_MOR).state == "failed"


def test_preflight_plans_a_fresh_install_not_the_managed_paths(tmp_path):
    wf = _workflow(tmp_path)
    wf.capture()
    wf.backup()
    res = wf.preflight()
    assert res.status == StageStatus.PASS
    disc = wf.install_workflow.discovery
    assert disc.telegraf_installed is False and disc.config_dir == "C:\\telegraf\\telegraf.d"
    assert "C:\\VMware\\UCP" not in wf.install_workflow.vcf_conf_content
    assert wf.install_workflow.artifacts.client_cert_content
    assert wf.adapter.uninstall_calls == [] and wf.executor.managed


def test_service_query_failure_fails_closed_before_and_after_retire(tmp_path):
    """Codex review: a failed service query is not an empty service set."""
    endpoint = ManagedWindowsEndpoint()
    endpoint.service_query_fails = True
    wf = _workflow(tmp_path, endpoint=endpoint)
    summary = wf.run()
    assert not summary.success and "Could not query Windows services" in summary.stages[0].message
    assert wf.adapter.uninstall_calls == []

    endpoint = ManagedWindowsEndpoint()

    class OpsBreakingTheQuery(OpsWithUninstall):
        def get_agent_task_status(self, task_id):
            status = super().get_agent_task_status(task_id)
            if status.finished:
                self.endpoint.service_query_fails = True
            return status

    wf = _workflow(tmp_path, endpoint=endpoint, adapter=OpsBreakingTheQuery(_env(), endpoint))
    summary = wf.run()
    assert not summary.success
    assert summary.stages[4].stage == TakeoverStage.CLEAN and "state is unknown" in summary.stages[4].message
    assert not endpoint.uploaded_files


def test_retire_failed_with_services_present_repolls_instead_of_resubmitting(tmp_path):
    """Codex review: a journaled uninstall task is reconciled before any second uninstall."""
    endpoint = ManagedWindowsEndpoint()
    adapter = OpsWithUninstall(_env(), endpoint, polls_to_finish=99)
    first = _workflow(tmp_path, endpoint=endpoint, adapter=adapter, uninstall_timeout_seconds=0)
    first.run()
    assert endpoint.managed and TakeoverJournal(tmp_path / "journal").load(VC_ID, VM_MOR).state == "retire_failed"

    # still running in Ops: refuse, do not resubmit
    second = _workflow(tmp_path, endpoint=endpoint, adapter=adapter)
    summary = second.run()
    assert not summary.success and "still SUBMITTING" in summary.stages[0].message
    assert len(adapter.uninstall_calls) == 1

    # Ops says finished but the services are still there: refuse, reconcile
    adapter.polls_to_finish = adapter._task_polls + 1
    adapter.get_agent_task_status("task-1")
    endpoint.managed = True  # the fake retire ran; pretend the services survived
    endpoint.files[MANAGED_TELEGRAF_CONF] = FIXTURE.read_text()
    endpoint.files[MANAGED_GRAINS] = GRAINS
    third = _workflow(tmp_path, endpoint=endpoint, adapter=adapter)
    summary = third.run()
    assert not summary.success and "services are still present" in summary.stages[0].message
    assert len(adapter.uninstall_calls) == 1

    # the task ended in failure: a fresh takeover may start, and the journal says why
    adapter.fail_task = True
    adapter.retired = False  # the fake flips the object once "retired"; the real one is still Product Managed
    fourth = _workflow(tmp_path, endpoint=endpoint, adapter=adapter)
    fourth.capture()
    assert fourth.record is not None and any("starting over" in n for n in fourth.record.notes)


def test_resume_after_install_only_verifies_continuity(tmp_path):
    """Codex review: an interruption during continuity must not report 'nothing to take over'."""
    first = _workflow(tmp_path)
    for stage in (first.capture, first.backup, first.preflight, first.retire, first.clean, first.install):
        assert stage().status in (StageStatus.PASS, StageStatus.WARNING)
    assert first.record.state == "installed"

    second = _workflow(tmp_path, endpoint=first.executor, adapter=first.adapter)
    summary = second.run()
    assert summary.success, [(s.stage, s.message) for s in summary.stages]
    assert [s.status for s in summary.stages[1:6]] == [StageStatus.SKIPPED] * 5
    assert summary.results[RESULT_INSTALL].startswith("PASS") and summary.results[RESULT_INGESTION] == "PASS"
    assert len(first.adapter.uninstall_calls) == 1
    assert TakeoverJournal(tmp_path / "journal").load(VC_ID, VM_MOR).state == "verified"
