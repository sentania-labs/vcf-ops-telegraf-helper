"""Tests for workflow engine orchestration and safety gates."""

from __future__ import annotations

from vcf_ops_telegraf_helper.adapters.mock import MockVCFOpsIntegration
from vcf_ops_telegraf_helper.executors.mock import MockExecutor
from vcf_ops_telegraf_helper.models.endpoint import (
    ConnectionMethod,
    EndpointTarget,
    OSFamily,
)
from vcf_ops_telegraf_helper.models.monitoring import MonitoringConfig
from vcf_ops_telegraf_helper.models.vcf import CollectorInfo, VCFEnvironment
from vcf_ops_telegraf_helper.models.workflow import (
    StageStatus,
    WorkflowOptions,
    WorkflowStage,
)
from vcf_ops_telegraf_helper.workflow.engine import ConfigureEndpointWorkflow


def _create_test_workflow(
    connected: bool = True,
    telegraf_installed: bool = True,
    vcf_connected: bool = True,
    options: WorkflowOptions | None = None,
) -> ConfigureEndpointWorkflow:
    env = VCFEnvironment(
        name="test-env",
        url="https://vcf-ops.local",
        username="admin",
        collector=CollectorInfo(address="10.10.10.50"),
    )
    target = EndpointTarget(
        hostname="node01.corp.local",
        os_family=OSFamily.LINUX,
        connection_method=ConnectionMethod.MOCK,
    )
    monitoring = MonitoringConfig()
    executor = MockExecutor(connected=connected, telegraf_installed=telegraf_installed)
    adapter = MockVCFOpsIntegration(env=env, connected=vcf_connected)

    return ConfigureEndpointWorkflow(
        environment=env,
        target=target,
        monitoring=monitoring,
        executor=executor,
        adapter=adapter,
        options=options or WorkflowOptions(),
    )


def test_workflow_full_success_sequence():
    """Verify all 8 stages execute in order and succeed under normal conditions."""
    wf = _create_test_workflow()
    summary = wf.run()

    assert summary.success
    assert len(summary.stages) == 8

    # Verify stage order
    expected_stages = [
        WorkflowStage.CONNECT,
        WorkflowStage.DETECT,
        WorkflowStage.PREPARE_VCF,
        WorkflowStage.RENDER_INPUTS,
        WorkflowStage.VALIDATE,
        WorkflowStage.APPLY,
        WorkflowStage.RESTART,
        WorkflowStage.VERIFY,
    ]
    for actual, expected in zip(summary.stages, expected_stages):
        assert actual.stage == expected
        assert actual.status in (StageStatus.PASS, StageStatus.SKIPPED)

    # Verification checklist
    assert summary.verifications["Telegraf installed"] == "PASS"
    assert summary.verifications["Config valid"] == "PASS"
    assert summary.verifications["Service running"] == "PASS"
    assert summary.verifications["Collector reachable"] == "PASS"



def test_workflow_connection_failure_aborts_early():
    """Verify connection failure at stage 1 halts the pipeline before applying any changes."""
    wf = _create_test_workflow(connected=False)
    summary = wf.run()

    assert not summary.success
    assert len(summary.stages) == 1
    assert summary.stages[0].stage == WorkflowStage.CONNECT
    assert summary.stages[0].status == StageStatus.FAIL

    # Confirm apply was never executed
    assert len(summary.managed_files) == 0


def test_workflow_dry_run_skips_mutation():
    """Verify dry-run mode executes validation but skips apply and restart."""
    opts = WorkflowOptions(dry_run=True)
    wf = _create_test_workflow(options=opts)
    summary = wf.run()

    assert summary.success
    apply_stage = next(s for s in summary.stages if s.stage == WorkflowStage.APPLY)
    restart_stage = next(s for s in summary.stages if s.stage == WorkflowStage.RESTART)

    assert apply_stage.status == StageStatus.SKIPPED
    assert restart_stage.status == StageStatus.SKIPPED
    assert len(summary.managed_files) == 0


def test_workflow_honest_unknown_telemetry():
    """Verify that when telemetry cannot be confirmed immediately, UNKNOWN is reported honestly."""
    env = VCFEnvironment(
        name="test-env",
        url="https://vcf-ops.local",
        username="admin",
        collector=CollectorInfo(address="10.10.10.50"),
    )
    target = EndpointTarget(
        hostname="node01.corp.local",
        os_family=OSFamily.LINUX,
        connection_method=ConnectionMethod.MOCK,
    )
    monitoring = MonitoringConfig()
    executor = MockExecutor(connected=True, telegraf_installed=True)
    # Mock VCF Ops reports UNKNOWN for telemetry ingestion
    adapter = MockVCFOpsIntegration(env=env, connected=True, ingestion_status="UNKNOWN")

    wf = ConfigureEndpointWorkflow(
        environment=env,
        target=target,
        monitoring=monitoring,
        executor=executor,
        adapter=adapter,
    )
    summary = wf.run()
    assert summary.verifications["VCF Ops ingestion"] == "UNKNOWN"


def test_workflow_windows_schannel_with_confirmed_ingestion():
    """Verify Windows Schannel limitation reports PASS when VCF Ops ingestion is confirmed."""
    from unittest.mock import MagicMock
    from vcf_ops_telegraf_helper.executors.base import CommandResult

    env = VCFEnvironment(
        name="test-env",
        url="https://vcf-ops.local",
        username="admin",
        collector=CollectorInfo(address="10.10.10.50"),
    )
    target = EndpointTarget(
        hostname="win01.corp.local",
        os_family=OSFamily.WINDOWS,
        connection_method=ConnectionMethod.WINRM,
    )
    monitoring = MonitoringConfig()
    adapter = MockVCFOpsIntegration(env=env, connected=True, ingestion_status="PASS")

    mock_exec = MagicMock()

    def _exec(cmd, **kw):
        if "schannel" in cmd or "curl.exe" in cmd:
            return CommandResult(
                exit_code=35,
                stdout="curl: (35) schannel: Failed to open cert or key file by pathname: 0x80092002",
                command=cmd,
            )
        if "TCP:" in cmd or "Test-NetConnection" in cmd:
            return CommandResult(exit_code=0, stdout="TCP:True;SVC:Running\r\n", command=cmd)
        if "Test-Path" in cmd:
            return CommandResult(exit_code=0, stdout="True\r\nTrue", command=cmd)
        if "Get-WinEvent" in cmd:
            return CommandResult(exit_code=0, stdout="", command=cmd)
        if "Get-Service" in cmd:
            return CommandResult(exit_code=0, stdout="Running", command=cmd)
        if "PROCESSOR_ARCH" in cmd:
            return CommandResult(exit_code=0, stdout="AMD64", command=cmd)
        if "telegraf.exe" in cmd and "version" in cmd:
            return CommandResult(exit_code=0, stdout="Telegraf 1.40.1", command=cmd)
        if "test" in cmd:
            return CommandResult(exit_code=0, stdout="cpu,host=win01 value=1", command=cmd)
        return CommandResult(exit_code=0, stdout="", command=cmd)

    mock_exec.execute.side_effect = _exec
    mock_exec.file_exists.return_value = True

    wf = ConfigureEndpointWorkflow(
        environment=env,
        target=target,
        monitoring=monitoring,
        executor=mock_exec,
        adapter=adapter,
    )
    summary = wf.run()
    assert summary.success is True
    assert summary.verifications["Metrics transmission"] == "PASS (verified via VCF Ops ingestion)"


def test_workflow_windows_schannel_with_unknown_ingestion_fails():
    """Verify Windows Schannel limitation fails Stage 8 when VCF Ops ingestion cannot be confirmed."""
    from unittest.mock import MagicMock
    from vcf_ops_telegraf_helper.executors.base import CommandResult

    env = VCFEnvironment(
        name="test-env",
        url="https://vcf-ops.local",
        username="admin",
        collector=CollectorInfo(address="10.10.10.50"),
    )
    target = EndpointTarget(
        hostname="win01.corp.local",
        os_family=OSFamily.WINDOWS,
        connection_method=ConnectionMethod.WINRM,
    )
    monitoring = MonitoringConfig()
    # Ingestion status is UNKNOWN
    adapter = MockVCFOpsIntegration(env=env, connected=True, ingestion_status="UNKNOWN")

    mock_exec = MagicMock()

    def _exec(cmd, **kw):
        if "schannel" in cmd or "curl.exe" in cmd:
            return CommandResult(
                exit_code=35,
                stdout="curl: (35) schannel: Failed to open cert or key file by pathname: 0x80092002",
                command=cmd,
            )
        if "TCP:" in cmd or "Test-NetConnection" in cmd:
            return CommandResult(exit_code=0, stdout="TCP:True;SVC:Running\r\n", command=cmd)
        if "Test-Path" in cmd:
            return CommandResult(exit_code=0, stdout="True\r\nTrue", command=cmd)
        if "Get-WinEvent" in cmd:
            return CommandResult(exit_code=0, stdout="", command=cmd)
        if "Get-Service" in cmd:
            return CommandResult(exit_code=0, stdout="Running", command=cmd)
        if "PROCESSOR_ARCH" in cmd:
            return CommandResult(exit_code=0, stdout="AMD64", command=cmd)
        if "telegraf.exe" in cmd and "version" in cmd:
            return CommandResult(exit_code=0, stdout="Telegraf 1.40.1", command=cmd)
        if "test" in cmd:
            return CommandResult(exit_code=0, stdout="cpu,host=win01 value=1", command=cmd)
        return CommandResult(exit_code=0, stdout="", command=cmd)

    mock_exec.execute.side_effect = _exec
    mock_exec.file_exists.return_value = True

    wf = ConfigureEndpointWorkflow(
        environment=env,
        target=target,
        monitoring=monitoring,
        executor=mock_exec,
        adapter=adapter,
    )
    summary = wf.run()
    assert summary.success is False
    assert "FAIL" in summary.verifications["Metrics transmission"]
    assert "Schannel backend cannot load detached PEM" in summary.verifications["Metrics transmission"]


def test_workflow_options_telegraf_version_precedence():
    """Verify WorkflowOptions telegraf_version takes precedence over default EndpointTarget."""
    env = VCFEnvironment(
        name="test-env",
        url="https://vcf-ops.local",
        username="admin",
        collector=CollectorInfo(address="10.10.10.50"),
    )
    target = EndpointTarget(
        hostname="win02.corp.local",
        os_family=OSFamily.WINDOWS,
        connection_method=ConnectionMethod.WINRM,
        install_telegraf=True,
    )
    opts = WorkflowOptions(install_telegraf=True, telegraf_version="1.34.0")
    adapter = MockVCFOpsIntegration(env=env, connected=True)
    executor = MockExecutor(connected=True, telegraf_installed=False)

    wf = ConfigureEndpointWorkflow(
        environment=env,
        target=target,
        monitoring=MonitoringConfig(),
        executor=executor,
        adapter=adapter,
        options=opts,
    )
    det_res = wf.detect_telegraf()
    assert "1.34.0" in det_res.message
