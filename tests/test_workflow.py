"""Tests for workflow engine orchestration and safety gates."""

from __future__ import annotations

from vcf_ops_telegraf_helper.adapters.mock import MockVCFOpsIntegration
from vcf_ops_telegraf_helper.executors.base import CommandResult
from vcf_ops_telegraf_helper.executors.mock import MockExecutor
from vcf_ops_telegraf_helper.executors.package import PackageExecutor
from vcf_ops_telegraf_helper.models.endpoint import (
    ConnectionMethod,
    EndpointTarget,
    OSFamily,
)
from vcf_ops_telegraf_helper.models.monitoring import MonitoringConfig
from vcf_ops_telegraf_helper.models.vcf import CollectorInfo, VCFEnvironment
from vcf_ops_telegraf_helper.models.workflow import (
    DeploymentMode,
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
    assert "PENDING" in summary.verifications["VCF Ops ingestion"]
    assert "5 to 15 minutes" in summary.verifications["VCF Ops ingestion"]


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


def test_workflow_windows_schannel_with_unknown_ingestion_reports_pending():
    """Verify Windows Schannel limitation reports PENDING and avoids false failure when service and port 443 are verified."""
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
    assert summary.success is True
    assert "PENDING (Windows Schannel PEM limitation; service and port 443 verified)" in summary.verifications["Metrics transmission"]
    assert "PENDING" in summary.verifications["VCF Ops ingestion"]


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


def test_workflow_resolves_os_shortname_for_ip_target():
    """Verify target IP address resolves to discovered OS shortname in rendered VCF output."""
    env = VCFEnvironment(
        name="test-env",
        url="https://vcf-ops.local",
        username="admin",
        collector=CollectorInfo(address="10.10.10.50"),
    )
    target = EndpointTarget(
        hostname="172.16.3.80",
        os_family=OSFamily.WINDOWS,
        connection_method=ConnectionMethod.WINRM,
    )
    mock_exec = MockExecutor(connected=True, telegraf_installed=True)
    # Configure mock executor command response for $env:COMPUTERNAME
    mock_exec.custom_responses["$env:COMPUTERNAME"] = CommandResult(
        exit_code=0, stdout="mssqldemo\r\n", command="$env:COMPUTERNAME"
    )

    wf = ConfigureEndpointWorkflow(
        environment=env,
        target=target,
        monitoring=MonitoringConfig(),
        executor=mock_exec,
        adapter=MockVCFOpsIntegration(env=env, connected=True),
    )
    wf.detect_target()
    wf.detect_telegraf()
    assert wf.discovery.hostname == "mssqldemo"

    wf.configure_vcf_output()
    wf.render_inputs()
    assert 'hostname = "mssqldemo"' in wf.vcf_conf_content


def test_workflow_resolves_vm_name_fallback():
    """Verify VM name from VCF Operations is used when guest discovery returns IP."""
    env = VCFEnvironment(
        name="test-env",
        url="https://vcf-ops.local",
        username="admin",
        collector=CollectorInfo(address="10.10.10.50"),
    )
    target = EndpointTarget(
        hostname="172.16.3.80",
        os_family=OSFamily.WINDOWS,
        connection_method=ConnectionMethod.WINRM,
    )
    mock_exec = MockExecutor(connected=True, telegraf_installed=True)
    # Mock executor returns empty for $env:COMPUTERNAME
    mock_exec.custom_responses["$env:COMPUTERNAME"] = CommandResult(
        exit_code=0, stdout="", command="$env:COMPUTERNAME"
    )

    adapter = MockVCFOpsIntegration(env=env, connected=True)
    wf = ConfigureEndpointWorkflow(
        environment=env,
        target=target,
        monitoring=MonitoringConfig(),
        executor=mock_exec,
        adapter=adapter,
    )
    wf.detect_target()
    wf.detect_telegraf()
    wf.configure_vcf_output()
    # Inject discovered VM name into artifacts
    wf.artifacts.vm_name = "mssqldemo"
    wf.render_inputs()
    assert 'hostname = "mssqldemo"' in wf.vcf_conf_content


def test_workflow_strips_domain_to_shortname():
    """Verify FQDN target host is stripped to shortname in rendered VCF output."""
    env = VCFEnvironment(
        name="test-env",
        url="https://vcf-ops.local",
        username="admin",
        collector=CollectorInfo(address="10.10.10.50"),
    )
    target = EndpointTarget(
        hostname="console.int.sentania.net",
        os_family=OSFamily.LINUX,
        connection_method=ConnectionMethod.SSH,
    )
    mock_exec = MockExecutor(connected=True, telegraf_installed=True)
    mock_exec.custom_responses["hostname -s"] = CommandResult(
        exit_code=0, stdout="console\n", command="hostname -s"
    )

    wf = ConfigureEndpointWorkflow(
        environment=env,
        target=target,
        monitoring=MonitoringConfig(),
        executor=mock_exec,
        adapter=MockVCFOpsIntegration(env=env, connected=True),
    )
    wf.detect_target()
    wf.detect_telegraf()
    wf.configure_vcf_output()
    wf.render_inputs()
    assert 'hostname = "console"' in wf.vcf_conf_content


def test_workflow_prepare_telegraf_integration_passes_shortname_for_vm_matching():
    """Verify prepare_telegraf_integration receives discovered shortname instead of target IP."""
    from unittest.mock import MagicMock

    env = VCFEnvironment(
        name="test-env",
        url="https://vcf-ops.local",
        username="admin",
        collector=CollectorInfo(address="10.10.10.50"),
    )
    target = EndpointTarget(
        hostname="172.16.3.80",
        os_family=OSFamily.WINDOWS,
        connection_method=ConnectionMethod.WINRM,
    )
    mock_exec = MockExecutor(connected=True, telegraf_installed=True)
    mock_exec.custom_responses["$env:COMPUTERNAME"] = CommandResult(
        exit_code=0, stdout="mssqldemo\r\n", command="$env:COMPUTERNAME"
    )

    adapter = MockVCFOpsIntegration(env=env, connected=True)
    original_prepare = adapter.prepare_telegraf_integration
    mock_prepare = MagicMock(side_effect=original_prepare)
    adapter.prepare_telegraf_integration = mock_prepare

    wf = ConfigureEndpointWorkflow(
        environment=env,
        target=target,
        monitoring=MonitoringConfig(),
        executor=mock_exec,
        adapter=adapter,
    )
    wf.detect_target()
    wf.detect_telegraf()
    wf.configure_vcf_output()

    mock_prepare.assert_called_once()
    called_kwargs = mock_prepare.call_args.kwargs
    assert called_kwargs["target_hostname"] == "mssqldemo"
    assert called_kwargs["target_ip"] == "172.16.3.80"


def test_workflow_rollback_on_test_validation_failure():
    """Verify that when telegraf --test fails on the endpoint, rollback restores .bak files."""
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
    executor = MockExecutor(connected=True, telegraf_installed=True)
    # Simulate telegraf --test syntax validation failure
    executor.custom_responses["/usr/bin/telegraf --test --config /etc/telegraf/telegraf.conf --config-directory /etc/telegraf/telegraf.d"] = CommandResult(
        exit_code=1,
        stdout="",
        stderr="Error parsing /etc/telegraf/telegraf.d/vcf-helper-system.conf: line 5: syntax error",
        command="telegraf --test",
    )

    wf = ConfigureEndpointWorkflow(
        environment=env,
        target=target,
        monitoring=MonitoringConfig(),
        executor=executor,
        adapter=MockVCFOpsIntegration(env=env, connected=True),
        options=WorkflowOptions(restart_service=True),
    )
    summary = wf.run()

    assert not summary.success
    # Stage 7 should fail and report rollback
    restart_stage = next(s for s in summary.stages if s.stage == WorkflowStage.RESTART)
    assert restart_stage.status == StageStatus.FAIL
    assert "Restored previous configuration (.bak)" in restart_stage.message
    # Check that rollback script command was executed
    assert any("mv -f" in cmd or ".bak" in cmd for cmd in executor.executed_commands)


def test_workflow_script_mode_skips_remote_connection():
    """Verify that deployment mode SCRIPT skips target connection and detection stages."""
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
    executor = MockExecutor(connected=False, telegraf_installed=False)

    wf = ConfigureEndpointWorkflow(
        environment=env,
        target=target,
        monitoring=MonitoringConfig(),
        executor=executor,
        adapter=MockVCFOpsIntegration(env=env, connected=True),
        options=WorkflowOptions(mode=DeploymentMode.SCRIPT),
    )
    # Stage 1 and 2 should be skipped rather than failing due to connected=False
    conn_res = wf.detect_target()
    assert conn_res.status == StageStatus.SKIPPED
    assert "skipped for script deployment mode" in conn_res.message

    detect_res = wf.detect_telegraf()
    assert detect_res.status == StageStatus.PASS
    assert "offline bundle generation" in detect_res.message
    assert "target inspection skipped" in detect_res.details


def test_workflow_linux_preflight_disk_check_var_and_root():
    """Verify Linux pre-flight disk check inspects /var and / before auto-install."""
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
        install_telegraf=True,
    )
    executor = MockExecutor(connected=True, telegraf_installed=False)
    # Simulate df -m -P reporting 320 MB free on /var
    executor.custom_responses["min_free=$(df -m -P /var / 2>/dev/null | awk 'NR>1 {print $4}' | sort -n | head -n1); if [ -n \"$min_free\" ] && [ \"$min_free\" -lt 500 ]; then echo \"FAIL: $min_free\"; else echo \"OK\"; fi"] = CommandResult(
        exit_code=0, stdout="FAIL: 320", command="df"
    )

    wf = ConfigureEndpointWorkflow(
        environment=env,
        target=target,
        monitoring=MonitoringConfig(),
        executor=executor,
        adapter=MockVCFOpsIntegration(env=env, connected=True),
        options=WorkflowOptions(install_telegraf=True),
    )
    wf.detect_target()
    wf.detect_telegraf()
    wf.configure_vcf_output()
    wf.render_inputs()

    apply_res = wf.apply()
    assert apply_res.status == StageStatus.FAIL
    assert "Insufficient disk space on target filesystem (320 MB free on / or /var, minimum 500 MB required)" in apply_res.message


def test_package_executor_deploy_scripts_include_rollback(tmp_path):
    """Verify package deploy scripts include backup and rollback routines."""
    pkg = PackageExecutor(output_dir=tmp_path / "bundle")

    # Linux deploy script
    sh_path = pkg.generate_deploy_script(is_windows=False)
    sh_txt = sh_path.read_text(encoding="utf-8")
    assert "rollback()" in sh_txt
    assert "$MAIN_CONF.bak" in sh_txt
    assert "$CONF_DIR" in sh_txt

    # Windows deploy script
    ps1_path = pkg.generate_deploy_script(is_windows=True)
    ps1_txt = ps1_path.read_text(encoding="utf-8")
    assert "Invoke-Rollback" in ps1_txt
    assert "$mainConf.bak" in ps1_txt
    assert "2>&1" in ps1_txt


