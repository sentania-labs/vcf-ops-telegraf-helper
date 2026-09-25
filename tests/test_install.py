"""Tests for Telegraf agent missing detection and auto-install bootstrap."""

from __future__ import annotations

from unittest.mock import MagicMock

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
from vcf_ops_telegraf_helper.workflow.engine import ConfigureEndpointWorkflow


def test_missing_telegraf_without_auto_install_fails_push():
    """Verify workflow halts when Telegraf is missing and install_telegraf is False."""
    env = VCFEnvironment(name="test", url="https://vcf.local", username="admin", collector=CollectorInfo(address="10.10.10.50"))
    target = EndpointTarget(
        hostname="target.local",
        os_family=OSFamily.LINUX,
        connection_method=ConnectionMethod.SSH,
        install_telegraf=False,
    )
    mon = MonitoringConfig()

    mock_exec = MagicMock()
    mock_exec.execute.side_effect = lambda cmd, **kw: CommandResult(
        exit_code=1 if "which telegraf" in cmd else 0,
        stdout="" if "which telegraf" in cmd else "x86_64",
        command=cmd,
    )
    mock_adapter = MagicMock()

    wf = ConfigureEndpointWorkflow(
        environment=env,
        target=target,
        monitoring=mon,
        executor=mock_exec,
        adapter=mock_adapter,
        options=WorkflowOptions(mode=DeploymentMode.PUSH, install_telegraf=False),
    )

    res = wf.detect_telegraf()
    assert res.status == StageStatus.FAIL
    assert "Telegraf not installed" in res.message


def test_missing_telegraf_with_auto_install_warns_and_proceeds():
    """Verify workflow continues when Telegraf is missing and install_telegraf is True."""
    env = VCFEnvironment(name="test", url="https://vcf.local", username="admin", collector=CollectorInfo(address="10.10.10.50"))
    target = EndpointTarget(
        hostname="target.local",
        os_family=OSFamily.LINUX,
        connection_method=ConnectionMethod.SSH,
        install_telegraf=True,
    )
    mon = MonitoringConfig()

    mock_exec = MagicMock()
    mock_exec.execute.side_effect = lambda cmd, **kw: CommandResult(
        exit_code=1 if "which telegraf" in cmd else 0,
        stdout="" if "which telegraf" in cmd else "x86_64",
        command=cmd,
    )
    mock_adapter = MagicMock()

    wf = ConfigureEndpointWorkflow(
        environment=env,
        target=target,
        monitoring=mon,
        executor=mock_exec,
        adapter=mock_adapter,
        options=WorkflowOptions(mode=DeploymentMode.PUSH, install_telegraf=True),
    )

    res = wf.detect_telegraf()
    assert res.status == StageStatus.WARNING
    assert "auto-install" in res.message


def test_apply_executes_linux_bootstrap_install():
    """Verify apply stage runs curl bootstrap script when Telegraf is missing."""
    env = VCFEnvironment(name="test", url="https://vcf.local", username="admin", collector=CollectorInfo(address="10.10.10.50"))
    target = EndpointTarget(
        hostname="target.local",
        os_family=OSFamily.LINUX,
        connection_method=ConnectionMethod.SSH,
        install_telegraf=True,
    )
    mon = MonitoringConfig()

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
        monitoring=mon,
        executor=mock_exec,
        adapter=mock_adapter,
        options=WorkflowOptions(mode=DeploymentMode.PUSH, install_telegraf=True),
    )
    wf.artifacts = mock_adapter.prepare_telegraf_integration()

    wf.discovery = EndpointDiscoveryResult(
        hostname=target.hostname,
        os_name="Linux",
        os_version="Ubuntu",
        telegraf_installed=False,
    )

    res = wf.apply()
    assert res.status == StageStatus.PASS
    assert wf.discovery.telegraf_installed is True

    # Check that bootstrap script command was executed
    executed_cmds = [call[0][0] for call in mock_exec.execute.call_args_list]
    assert any("telegraf-utils.sh" in cmd for cmd in executed_cmds)


def test_apply_executes_windows_bootstrap_install():
    """Verify apply stage runs PowerShell bootstrap script on Windows when Telegraf is missing."""
    env = VCFEnvironment(name="test", url="https://vcf.local", username="admin", collector=CollectorInfo(address="10.10.10.50"))
    target = EndpointTarget(
        hostname="win-target.local",
        os_family=OSFamily.WINDOWS,
        connection_method=ConnectionMethod.WINRM,
        install_telegraf=True,
    )
    mon = MonitoringConfig()

    mock_exec = MagicMock()
    mock_exec.execute.return_value = CommandResult(exit_code=0, stdout="success", command="cmd")
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
        monitoring=mon,
        executor=mock_exec,
        adapter=mock_adapter,
        options=WorkflowOptions(mode=DeploymentMode.PUSH, install_telegraf=True),
    )
    wf.artifacts = mock_adapter.prepare_telegraf_integration()

    wf.discovery = EndpointDiscoveryResult(
        hostname=target.hostname,
        os_name="Windows",
        os_version="Microsoft Windows Server 2022",
        telegraf_installed=False,
        config_dir="C:\\telegraf\\telegraf.d",
    )

    res = wf.apply()
    assert res.status == StageStatus.PASS
    assert wf.discovery.telegraf_installed is True

    executed_cmds = [call[0][0] for call in mock_exec.execute.call_args_list]
    assert any("telegraf-utils.ps1" in cmd for cmd in executed_cmds)
