"""Tests for Telegraf agent version comparison, upgrade decision logic, and workflow execution."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from vcf_ops_telegraf_helper.adapters.base import IntegrationArtifacts
from vcf_ops_telegraf_helper.executors.base import CommandResult
from vcf_ops_telegraf_helper.models.endpoint import (
    ConnectionMethod,
    EndpointDiscoveryResult,
    EndpointTarget,
    OSFamily,
)
from vcf_ops_telegraf_helper.models.monitoring import MonitoringConfig
from vcf_ops_telegraf_helper.models.vcf import CollectorInfo, VCFEnvironment
from vcf_ops_telegraf_helper.models.workflow import DeploymentMode, StageStatus, WorkflowOptions
from vcf_ops_telegraf_helper.utils import (
    UpgradeDecision,
    evaluate_telegraf_upgrade,
    parse_telegraf_version,
)
from vcf_ops_telegraf_helper.validation.validator import ValidationResult
from vcf_ops_telegraf_helper.workflow.engine import ConfigureEndpointWorkflow


def test_parse_telegraf_version():
    """Verify version parsing across varied string formats and edge cases."""
    assert parse_telegraf_version("1.40.2") == (1, 40, 2)
    assert parse_telegraf_version("v1.40.2") == (1, 40, 2)
    assert parse_telegraf_version("Telegraf 1.32.1 (git: HEAD@c7c00e62)") == (1, 32, 1)
    assert parse_telegraf_version("Telegraf 1.30.0") == (1, 30, 0)
    assert parse_telegraf_version("Telegraf 1.39.0 synthetic\n") == (1, 39, 0)
    assert parse_telegraf_version("1.40") == (1, 40, 0)
    assert parse_telegraf_version(None) is None
    assert parse_telegraf_version("") is None
    assert parse_telegraf_version("unknown") is None
    assert parse_telegraf_version("no_version_here") is None


def test_evaluate_telegraf_upgrade_cases():
    """Verify upgrade evaluation logic for upgrade, equality, downgrade refusal, and unparseable."""
    # Older -> Upgrade
    decision, reason = evaluate_telegraf_upgrade("Telegraf 1.32.1", "1.40.2")
    assert decision == UpgradeDecision.UPGRADE
    assert "older than requested" in reason

    # Equal -> Unchanged
    decision, reason = evaluate_telegraf_upgrade("Telegraf 1.40.2", "1.40.2")
    assert decision == UpgradeDecision.UNCHANGED_EQUAL
    assert "matches requested" in reason

    # Equal with prefix / format differences
    decision, reason = evaluate_telegraf_upgrade("Telegraf 1.40.1 (git: HEAD@abc)", "v1.40.1")
    assert decision == UpgradeDecision.UNCHANGED_EQUAL

    # Newer -> Unchanged (downgrade not permitted)
    decision, reason = evaluate_telegraf_upgrade("Telegraf 1.40.2", "1.32.1")
    assert decision == UpgradeDecision.UNCHANGED_NEWER
    assert "downgrade not permitted" in reason

    # Unparseable
    decision, reason = evaluate_telegraf_upgrade("custom-build", "1.40.2")
    assert decision == UpgradeDecision.UNKNOWN


def _mock_linux_detect_exec(installed_version: str):
    def _handler(cmd, **kw):
        if "--property=Id,LoadState" in cmd:
            return CommandResult(exit_code=0, stdout="", command=cmd)
        if "which telegraf" in cmd:
            return CommandResult(exit_code=0, stdout="/usr/bin/telegraf", command=cmd)
        if "version" in cmd:
            return CommandResult(exit_code=0, stdout=f"{installed_version}\n", command=cmd)
        if "is-active" in cmd:
            return CommandResult(exit_code=0, stdout="active\n", command=cmd)
        return CommandResult(exit_code=0, stdout="x86_64", command=cmd)
    return _handler


def test_detect_stage_announces_upgrade_when_installed_is_older():
    """Verify detection stage announces pending upgrade when target agent is older than picked."""
    env = VCFEnvironment(name="test", url="https://vcf.local", username="admin", collector=CollectorInfo(address="10.10.10.50"))
    target = EndpointTarget(
        hostname="ubuntu-node.local",
        os_family=OSFamily.LINUX,
        connection_method=ConnectionMethod.SSH,
        install_telegraf=True,
        telegraf_version="1.40.2",
    )
    mock_exec = MagicMock()
    mock_exec.execute.side_effect = _mock_linux_detect_exec("Telegraf 1.32.1")
    mock_adapter = MagicMock()

    wf = ConfigureEndpointWorkflow(
        environment=env,
        target=target,
        monitoring=MonitoringConfig(),
        executor=mock_exec,
        adapter=mock_adapter,
        options=WorkflowOptions(mode=DeploymentMode.PUSH, install_telegraf=True, telegraf_version="1.40.2"),
    )

    res = wf.detect_telegraf()
    assert res.status == StageStatus.PASS
    assert "Telegraf 1.32.1" in res.message
    assert "will upgrade to 1.40.2" in res.message


def test_detect_stage_reports_unchanged_when_versions_match():
    """Verify detection stage plainly states version is unchanged when installed matches requested."""
    env = VCFEnvironment(name="test", url="https://vcf.local", username="admin", collector=CollectorInfo(address="10.10.10.50"))
    target = EndpointTarget(
        hostname="ubuntu-node.local",
        os_family=OSFamily.LINUX,
        connection_method=ConnectionMethod.SSH,
        install_telegraf=True,
        telegraf_version="1.40.2",
    )
    mock_exec = MagicMock()
    mock_exec.execute.side_effect = _mock_linux_detect_exec("Telegraf 1.40.2")
    mock_adapter = MagicMock()

    wf = ConfigureEndpointWorkflow(
        environment=env,
        target=target,
        monitoring=MonitoringConfig(),
        executor=mock_exec,
        adapter=mock_adapter,
        options=WorkflowOptions(mode=DeploymentMode.PUSH, install_telegraf=True, telegraf_version="1.40.2"),
    )

    res = wf.detect_telegraf()
    assert res.status == StageStatus.PASS
    assert "Telegraf 1.40.2" in res.message
    assert "version unchanged: matches requested 1.40.2" in res.message


def test_detect_stage_reports_unchanged_when_installed_is_newer():
    """Verify detection stage refuses downgrade when installed agent is newer than requested."""
    env = VCFEnvironment(name="test", url="https://vcf.local", username="admin", collector=CollectorInfo(address="10.10.10.50"))
    target = EndpointTarget(
        hostname="ubuntu-node.local",
        os_family=OSFamily.LINUX,
        connection_method=ConnectionMethod.SSH,
        install_telegraf=True,
        telegraf_version="1.32.1",
    )
    mock_exec = MagicMock()
    mock_exec.execute.side_effect = _mock_linux_detect_exec("Telegraf 1.40.2")
    mock_adapter = MagicMock()

    wf = ConfigureEndpointWorkflow(
        environment=env,
        target=target,
        monitoring=MonitoringConfig(),
        executor=mock_exec,
        adapter=mock_adapter,
        options=WorkflowOptions(mode=DeploymentMode.PUSH, install_telegraf=True, telegraf_version="1.32.1"),
    )

    res = wf.detect_telegraf()
    assert res.status == StageStatus.PASS
    assert "Telegraf 1.40.2" in res.message
    assert "version unchanged: installed version is newer than requested 1.32.1; downgrade not permitted" in res.message


def test_apply_executes_linux_upgrade_when_installed_is_older():
    """Verify apply stage executes upgrade and updates discovery when agent is older."""
    env = VCFEnvironment(name="test", url="https://vcf.local", username="admin", collector=CollectorInfo(address="10.10.10.50"))
    target = EndpointTarget(
        hostname="ubuntu-node.local",
        os_family=OSFamily.LINUX,
        connection_method=ConnectionMethod.SSH,
        install_telegraf=True,
        telegraf_version="1.40.2",
    )
    mock_exec = MagicMock()

    def _exec_handler(cmd, **kw):
        if "version" in cmd:
            return CommandResult(exit_code=0, stdout="Telegraf 1.40.2\n", command=cmd)
        return CommandResult(exit_code=0, stdout="success", command=cmd)

    mock_exec.execute.side_effect = _exec_handler
    mock_exec.file_exists.return_value = False
    mock_adapter = MagicMock()
    mock_adapter.prepare_telegraf_integration.return_value = IntegrationArtifacts(
        collector_address="10.10.10.50",
        script_url="https://10.10.10.50/downloads/salt/telegraf-utils.sh",
        output_url="https://10.10.10.50/opensource/default/metric",
        token="test-token-xyz",
    )

    wf = ConfigureEndpointWorkflow(
        environment=env,
        target=target,
        monitoring=MonitoringConfig(),
        executor=mock_exec,
        adapter=mock_adapter,
        options=WorkflowOptions(mode=DeploymentMode.PUSH, install_telegraf=True, telegraf_version="1.40.2"),
    )
    wf.artifacts = mock_adapter.prepare_telegraf_integration()
    wf.discovery = EndpointDiscoveryResult(
        hostname=target.hostname,
        os_name="Linux",
        os_version="Ubuntu 24.04",
        telegraf_installed=True,
        telegraf_version="Telegraf 1.32.1",
        architecture="x86_64",
    )

    res = wf.apply()
    assert res.status == StageStatus.PASS
    assert wf.telegraf_upgraded is True
    assert wf.discovery.telegraf_version == "Telegraf 1.40.2"

    executed_cmds = [call[0][0] for call in mock_exec.execute.call_args_list]
    upgrade_cmd = next(cmd for cmd in executed_cmds if "influxdata" in cmd.lower())
    assert "telegraf=1.40.2-1" in upgrade_cmd
    assert "telegraf-1.40.2_linux_amd64.tar.gz" in upgrade_cmd


def test_apply_reads_upgraded_version_from_installed_binary_not_old_path():
    """Verify the post-upgrade version comes from where the script installs, not the pre-upgrade binary path."""
    env = VCFEnvironment(name="test", url="https://vcf.local", username="admin", collector=CollectorInfo(address="10.10.10.50"))
    target = EndpointTarget(
        hostname="ubuntu-node.local",
        os_family=OSFamily.LINUX,
        connection_method=ConnectionMethod.SSH,
        install_telegraf=True,
        telegraf_version="1.40.2",
    )
    mock_exec = MagicMock()

    def _exec_handler(cmd, **kw):
        if cmd == '"/usr/local/bin/telegraf" version':
            return CommandResult(exit_code=0, stdout="Telegraf 1.32.1\n", command=cmd)
        if cmd == '"/usr/bin/telegraf" version':
            return CommandResult(exit_code=0, stdout="Telegraf 1.40.2\n", command=cmd)
        return CommandResult(exit_code=0, stdout="success", command=cmd)

    mock_exec.execute.side_effect = _exec_handler
    mock_exec.file_exists.return_value = False
    mock_adapter = MagicMock()
    mock_adapter.prepare_telegraf_integration.return_value = IntegrationArtifacts(
        collector_address="10.10.10.50",
        script_url="https://10.10.10.50/downloads/salt/telegraf-utils.sh",
        output_url="https://10.10.10.50/opensource/default/metric",
        token="test-token-xyz",
    )

    wf = ConfigureEndpointWorkflow(
        environment=env,
        target=target,
        monitoring=MonitoringConfig(),
        executor=mock_exec,
        adapter=mock_adapter,
        options=WorkflowOptions(mode=DeploymentMode.PUSH, install_telegraf=True, telegraf_version="1.40.2"),
    )
    wf.artifacts = mock_adapter.prepare_telegraf_integration()
    wf.discovery = EndpointDiscoveryResult(
        hostname=target.hostname,
        os_name="Linux",
        os_version="Ubuntu 24.04",
        telegraf_installed=True,
        telegraf_version="Telegraf 1.32.1",
        telegraf_bin_path="/usr/local/bin/telegraf",
        architecture="x86_64",
    )

    res = wf.apply()
    assert res.status == StageStatus.PASS
    assert wf.discovery.telegraf_bin_path == "/usr/bin/telegraf"
    assert wf.discovery.telegraf_version == "Telegraf 1.40.2"


def _upgrade_workflow(os_family, method, discovery, version_for):
    env = VCFEnvironment(name="test", url="https://vcf.local", username="admin", collector=CollectorInfo(address="10.10.10.50"))
    target = EndpointTarget(
        hostname="node.local",
        os_family=os_family,
        connection_method=method,
        install_telegraf=True,
        telegraf_version="1.40.2",
    )
    mock_exec = MagicMock()

    def _exec_handler(cmd, **kw):
        out = version_for(cmd)
        return CommandResult(exit_code=0, stdout=out if out is not None else "success", command=cmd)

    mock_exec.execute.side_effect = _exec_handler
    mock_exec.file_exists.return_value = False
    mock_adapter = MagicMock()
    mock_adapter.prepare_telegraf_integration.return_value = IntegrationArtifacts(
        collector_address="10.10.10.50",
        script_url="https://10.10.10.50/downloads/salt/telegraf-utils.ps1",
        output_url="https://10.10.10.50/opensource/default/metric",
        token="test-token-xyz",
    )
    wf = ConfigureEndpointWorkflow(
        environment=env,
        target=target,
        monitoring=MonitoringConfig(),
        executor=mock_exec,
        adapter=mock_adapter,
        options=WorkflowOptions(mode=DeploymentMode.PUSH, install_telegraf=True, telegraf_version="1.40.2"),
    )
    wf.artifacts = mock_adapter.prepare_telegraf_integration()
    wf.discovery = discovery
    return wf, mock_exec


def test_windows_upgrade_replaces_binary_in_place_for_custom_install_dir():
    """A Windows agent outside C:\\telegraf is upgraded where it lives; its service and config stay put."""
    custom_dir = "C:\\Program Files\\Telegraf"
    wf, mock_exec = _upgrade_workflow(
        OSFamily.WINDOWS,
        ConnectionMethod.WINRM,
        EndpointDiscoveryResult(
            hostname="node.local",
            os_name="Windows",
            os_version="Microsoft Windows Server 2025",
            telegraf_installed=True,
            telegraf_version="Telegraf 1.32.1",
            architecture="x86_64",
            telegraf_bin_path=f"{custom_dir}\\telegraf.exe",
            main_config_path=f"{custom_dir}\\telegraf.conf",
            config_dir=f"{custom_dir}\\telegraf.d",
        ),
        lambda cmd: "Telegraf 1.40.2\n" if f'& "{custom_dir}\\telegraf.exe" version' == cmd else None,
    )

    res = wf.apply()
    assert res.status == StageStatus.PASS
    assert wf.telegraf_upgraded is True
    assert wf.discovery.telegraf_version == "Telegraf 1.40.2"
    assert wf.discovery.telegraf_bin_path == f"{custom_dir}\\telegraf.exe"
    assert wf.discovery.config_dir == f"{custom_dir}\\telegraf.d"

    win_cmd = next(c[0][0] for c in mock_exec.execute.call_args_list if "Expand-Archive" in c[0][0])
    assert f"$destDir = '{custom_dir}'" in win_cmd
    assert "Set-ItemProperty" not in win_cmd
    assert "sc.exe config" not in win_cmd


def test_upgrade_that_leaves_old_version_is_not_reported_as_upgraded():
    """If the installed version is still older after the upgrade script, do not claim success."""
    wf, mock_exec = _upgrade_workflow(
        OSFamily.LINUX,
        ConnectionMethod.SSH,
        EndpointDiscoveryResult(
            hostname="node.local",
            os_name="Linux",
            os_version="Ubuntu 24.04",
            telegraf_installed=True,
            telegraf_version="Telegraf 1.32.1",
            architecture="x86_64",
        ),
        lambda cmd: "Telegraf 1.32.1\n" if cmd == '"/usr/bin/telegraf" version' else None,
    )
    messages = []
    wf.reporter = MagicMock()
    wf.reporter.on_message.side_effect = messages.append

    wf.apply()
    assert getattr(wf, "telegraf_upgraded", False) is False
    assert not any("upgraded to" in m for m in messages)
    assert any("did not take effect" in m and "1.32.1" in m for m in messages)


def test_apply_skips_upgrade_when_installed_is_equal():
    """Verify apply stage does not run upgrade commands when installed equals requested."""
    env = VCFEnvironment(name="test", url="https://vcf.local", username="admin", collector=CollectorInfo(address="10.10.10.50"))
    target = EndpointTarget(
        hostname="ubuntu-node.local",
        os_family=OSFamily.LINUX,
        connection_method=ConnectionMethod.SSH,
        install_telegraf=True,
        telegraf_version="1.40.2",
    )
    mock_exec = MagicMock()
    mock_exec.execute.return_value = CommandResult(exit_code=0, stdout="success", command="cmd")
    mock_exec.file_exists.return_value = False
    mock_adapter = MagicMock()
    mock_adapter.prepare_telegraf_integration.return_value = IntegrationArtifacts(
        collector_address="10.10.10.50",
        script_url="https://10.10.10.50/downloads/salt/telegraf-utils.sh",
        output_url="https://10.10.10.50/opensource/default/metric",
        token="test-token-xyz",
    )

    wf = ConfigureEndpointWorkflow(
        environment=env,
        target=target,
        monitoring=MonitoringConfig(),
        executor=mock_exec,
        adapter=mock_adapter,
        options=WorkflowOptions(mode=DeploymentMode.PUSH, install_telegraf=True, telegraf_version="1.40.2"),
    )
    wf.artifacts = mock_adapter.prepare_telegraf_integration()
    wf.discovery = EndpointDiscoveryResult(
        hostname=target.hostname,
        os_name="Linux",
        os_version="Ubuntu 24.04",
        telegraf_installed=True,
        telegraf_version="Telegraf 1.40.2",
        architecture="x86_64",
    )

    res = wf.apply()
    assert res.status == StageStatus.PASS
    assert getattr(wf, "telegraf_upgraded", False) is False

    executed_cmds = [call[0][0] for call in mock_exec.execute.call_args_list]
    assert not any("influxdata" in cmd.lower() for cmd in executed_cmds)


def test_apply_executes_windows_upgrade_when_installed_is_older():
    """Verify Windows apply stage executes upgrade archive extraction and stops service."""
    env = VCFEnvironment(name="test", url="https://vcf.local", username="admin", collector=CollectorInfo(address="10.10.10.50"))
    target = EndpointTarget(
        hostname="win-server2025.local",
        os_family=OSFamily.WINDOWS,
        connection_method=ConnectionMethod.WINRM,
        install_telegraf=True,
        telegraf_version="1.40.2",
    )
    mock_exec = MagicMock()

    def _exec_handler(cmd, **kw):
        if "version" in cmd:
            return CommandResult(exit_code=0, stdout="Telegraf 1.40.2\n", command=cmd)
        return CommandResult(exit_code=0, stdout="success", command=cmd)

    mock_exec.execute.side_effect = _exec_handler
    mock_exec.file_exists.return_value = False
    mock_adapter = MagicMock()
    mock_adapter.prepare_telegraf_integration.return_value = IntegrationArtifacts(
        collector_address="10.10.10.50",
        script_url="https://10.10.10.50/downloads/salt/telegraf-utils.ps1",
        output_url="https://10.10.10.50/opensource/default/metric",
        token="test-token-xyz",
    )

    wf = ConfigureEndpointWorkflow(
        environment=env,
        target=target,
        monitoring=MonitoringConfig(),
        executor=mock_exec,
        adapter=mock_adapter,
        options=WorkflowOptions(mode=DeploymentMode.PUSH, install_telegraf=True, telegraf_version="1.40.2"),
    )
    wf.artifacts = mock_adapter.prepare_telegraf_integration()
    wf.discovery = EndpointDiscoveryResult(
        hostname=target.hostname,
        os_name="Windows",
        os_version="Microsoft Windows Server 2025",
        telegraf_installed=True,
        telegraf_version="Telegraf 1.32.1",
        architecture="x86_64",
        config_dir="C:\\telegraf\\telegraf.d",
    )

    res = wf.apply()
    assert res.status == StageStatus.PASS
    assert wf.telegraf_upgraded is True
    assert wf.discovery.telegraf_version == "Telegraf 1.40.2"

    executed_cmds = [call[0][0] for call in mock_exec.execute.call_args_list]
    win_cmd = next(cmd for cmd in executed_cmds if "telegraf.exe" in cmd)
    assert "telegraf-1.40.2_windows_amd64.zip" in win_cmd
    assert "Stop-Service -Name telegraf" in win_cmd
    assert "Stop-Process -Force" in win_cmd


def test_upgraded_agent_forces_restart_despite_config_hash_match():
    """Verify service restart is executed and not skipped when agent was upgraded."""
    env = VCFEnvironment(name="test", url="https://vcf.local", username="admin", collector=CollectorInfo(address="10.10.10.50"))
    target = EndpointTarget(
        hostname="node.local",
        os_family=OSFamily.LINUX,
        connection_method=ConnectionMethod.SSH,
        install_telegraf=True,
        telegraf_version="1.40.2",
    )
    mock_exec = MagicMock()
    mock_exec.execute.return_value = CommandResult(exit_code=0, stdout="active", command="cmd")
    mock_adapter = MagicMock()

    wf = ConfigureEndpointWorkflow(
        environment=env,
        target=target,
        monitoring=MonitoringConfig(),
        executor=mock_exec,
        adapter=mock_adapter,
        options=WorkflowOptions(mode=DeploymentMode.PUSH, restart_service=True),
    )
    wf.discovery = EndpointDiscoveryResult(
        hostname=target.hostname,
        os_name="Linux",
        telegraf_installed=True,
        telegraf_version="Telegraf 1.40.2",
    )
    wf.is_idempotent = True
    wf.telegraf_upgraded = True

    res = wf.restart_if_needed()
    # Should not be SKIPPED because telegraf_upgraded is True
    assert res.status == StageStatus.PASS
    assert "restarted successfully" in res.message


def test_verify_stage_checks_upgraded_version():
    """Verify verification stage independently confirms upgraded version on endpoint."""
    env = VCFEnvironment(name="test", url="https://vcf.local", username="admin", collector=CollectorInfo(address="10.10.10.50"))
    target = EndpointTarget(
        hostname="node.local",
        os_family=OSFamily.LINUX,
        connection_method=ConnectionMethod.SSH,
        install_telegraf=True,
        telegraf_version="1.40.2",
    )
    mock_exec = MagicMock()
    mock_exec.execute.return_value = CommandResult(exit_code=0, stdout="active", command="cmd")
    mock_adapter = MagicMock()
    mock_adapter.verify_ingestion.return_value = "PASS"

    wf = ConfigureEndpointWorkflow(
        environment=env,
        target=target,
        monitoring=MonitoringConfig(),
        executor=mock_exec,
        adapter=mock_adapter,
        options=WorkflowOptions(mode=DeploymentMode.PUSH, install_telegraf=True, telegraf_version="1.40.2"),
    )
    wf.system_conf_content = "[agent]\ninterval = '10s'\n"
    wf.vcf_conf_content = "[[outputs.http]]\n"
    wf.discovery = EndpointDiscoveryResult(
        hostname=target.hostname,
        os_name="Linux",
        telegraf_installed=True,
        telegraf_version="Telegraf 1.40.2",
    )
    wf.telegraf_upgraded = True

    with patch(
        "vcf_ops_telegraf_helper.validation.validator.Validator.validate_cloudproxy_mtls_metric_probe",
        return_value=ValidationResult(domain="probe", is_valid=True, message="Probe success"),
    ), patch(
        "vcf_ops_telegraf_helper.validation.validator.Validator.validate_telegraf_config_on_endpoint",
        return_value=ValidationResult(domain="config", is_valid=True, message="Config syntax valid"),
    ):
        res = wf.verify()
    assert res.status == StageStatus.PASS
    assert "PASS" in wf.verifications["Telegraf version"]
    assert "Telegraf 1.40.2" in wf.verifications["Telegraf version"]
