"""Tests for endpoint uninstallation workflow and CLI."""

from __future__ import annotations

from unittest.mock import MagicMock
from click.testing import CliRunner

from vcf_ops_telegraf_helper.cli.main import cli
from vcf_ops_telegraf_helper.executors.base import CommandResult
from vcf_ops_telegraf_helper.executors.mock import MockExecutor
from vcf_ops_telegraf_helper.models.endpoint import (
    ConnectionMethod,
    EndpointTarget,
    OSFamily,
)
from vcf_ops_telegraf_helper.models.workflow import (
    StageStatus,
    UninstallOptions,
    UninstallStage,
)
from vcf_ops_telegraf_helper.workflow.uninstall import UninstallEndpointWorkflow


def test_uninstall_workflow_linux_mock_success():
    """Verify 5-stage uninstallation on Linux stops service, purges /etc/telegraf, and verifies clean state."""
    target = EndpointTarget(
        hostname="linux-node01.int.sentania.net",
        os_family=OSFamily.LINUX,
        connection_method=ConnectionMethod.MOCK,
    )
    executor = MockExecutor(connected=True, telegraf_installed=True)
    executor.uploaded_files["/etc/telegraf/telegraf.conf"] = "[agent]\n"
    executor.uploaded_files["/etc/telegraf/telegraf.d/cloudproxy-http.conf"] = "[[outputs.http]]\n"

    workflow = UninstallEndpointWorkflow(
        target=target,
        executor=executor,
        options=UninstallOptions(purge_packages=True, purge_repositories=True),
    )
    summary = workflow.run()

    assert summary.success is True
    assert len(summary.stages) == 5

    # Check stage results
    assert summary.stages[0].stage == UninstallStage.CONNECT
    assert summary.stages[0].status == StageStatus.PASS

    assert summary.stages[1].stage == UninstallStage.STOP_SERVICE
    assert summary.stages[1].status == StageStatus.PASS

    assert summary.stages[2].stage == UninstallStage.REMOVE_CONFIG
    assert summary.stages[2].status == StageStatus.PASS

    assert summary.stages[3].stage == UninstallStage.REMOVE_PACKAGE
    assert summary.stages[3].status == StageStatus.PASS

    assert summary.stages[4].stage == UninstallStage.VERIFY
    assert summary.stages[4].status == StageStatus.PASS

    # Verify executed teardown commands
    assert any("systemctl stop telegraf" in cmd for cmd in executor.executed_commands)
    assert any("systemctl disable telegraf" in cmd for cmd in executor.executed_commands)
    assert any("rm -rf /etc/telegraf" in cmd for cmd in executor.executed_commands)
    assert any("apt-get purge" in cmd for cmd in executor.executed_commands)
    assert any("sources.list.d/influxdata.list" in cmd for cmd in executor.executed_commands)
    assert any("userdel" in cmd for cmd in executor.executed_commands)

    # Verify endpoint clean verifications
    assert summary.verifications["Service inactive"] == "PASS"
    assert summary.verifications["Binary absent"] == "PASS"
    assert summary.verifications["Configuration absent"] == "PASS"


def test_uninstall_workflow_windows_mock_success():
    """Verify Windows teardown stops service, removes C:\\telegraf, and verifies clean state."""
    target = EndpointTarget(
        hostname="win-srv01.int.sentania.net",
        os_family=OSFamily.WINDOWS,
        connection_method=ConnectionMethod.MOCK,
    )
    executor = MockExecutor(connected=True, telegraf_installed=True)
    executor.uploaded_files["C:\\telegraf\\telegraf.exe"] = "binary"
    executor.uploaded_files["C:\\telegraf\\telegraf.conf"] = "[agent]\n"

    workflow = UninstallEndpointWorkflow(
        target=target,
        executor=executor,
        options=UninstallOptions(purge_packages=True),
    )
    summary = workflow.run()

    assert summary.success is True
    assert any("Stop-Service" in cmd for cmd in executor.executed_commands)
    assert any("service uninstall" in cmd or "delete telegraf" in cmd for cmd in executor.executed_commands)
    assert any("Remove-Item" in cmd and "C:\\telegraf" in cmd for cmd in executor.executed_commands)


def test_uninstall_workflow_connection_failure():
    """Verify workflow halts at Stage 1 when endpoint is unreachable."""
    target = EndpointTarget(
        hostname="unreachable.local",
        os_family=OSFamily.LINUX,
        connection_method=ConnectionMethod.MOCK,
    )
    executor = MockExecutor(connected=False, telegraf_installed=False)

    workflow = UninstallEndpointWorkflow(target=target, executor=executor)
    summary = workflow.run()

    assert summary.success is False
    assert len(summary.stages) == 1
    assert summary.stages[0].stage == UninstallStage.CONNECT
    assert summary.stages[0].status == StageStatus.FAIL


def test_cli_uninstall_interactive_cancel():
    """Verify CLI prompt cancellation aborts uninstallation safely."""
    runner = CliRunner()
    result = runner.invoke(cli, ["uninstall", "--target", "node01.corp.local"], input="n\n")
    assert result.exit_code == 0
    assert "Uninstallation cancelled by user" in result.output


def test_cli_uninstall_with_yes_flag(monkeypatch):
    """Verify CLI --yes executes uninstallation non-interactively."""
    runner = CliRunner()

    mock_summary = MagicMock(
        success=True,
        verifications={
            "Service inactive": "PASS",
            "Binary absent": "PASS",
            "Configuration absent": "PASS",
        },
    )

    with monkeypatch.context() as m:
        m.setattr(
            "vcf_ops_telegraf_helper.workflow.uninstall.UninstallEndpointWorkflow.run",
            lambda self: mock_summary,
        )
        result = runner.invoke(
            cli,
            [
                "uninstall",
                "--target",
                "console.int.sentania.net",
                "--method",
                "local",
                "--yes",
            ],
        )
        assert result.exit_code == 0
        assert "Telegraf uninstalled successfully" in result.output
        assert "Service inactive: PASS" in result.output


def test_uninstall_workflow_packages_preserved():
    """Verify uninstall verification passes when purge_packages is False and packages are preserved."""
    target = EndpointTarget(
        hostname="linux-srv02.corp.local",
        os_family=OSFamily.LINUX,
        connection_method=ConnectionMethod.MOCK,
    )
    executor = MockExecutor(connected=True, telegraf_installed=True)
    # telegraf binary is intentionally still present
    executor.custom_responses["which telegraf 2>/dev/null"] = CommandResult(
        exit_code=0, stdout="/usr/bin/telegraf", command="which"
    )

    workflow = UninstallEndpointWorkflow(
        target=target,
        executor=executor,
        options=UninstallOptions(purge_packages=False, purge_repositories=False),
    )
    summary = workflow.run()

    assert summary.success is True
    assert summary.verifications["Service inactive"] == "PASS"
    assert "packages preserved" in summary.verifications["Binary absent"]
    assert summary.verifications["Configuration absent"] == "PASS"

