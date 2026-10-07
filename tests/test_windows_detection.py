"""Unit tests for Windows Telegraf agent detection (issue #32)."""

import os
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from vcf_ops_telegraf_helper.executors.base import CommandResult
from vcf_ops_telegraf_helper.models.endpoint import (
    ConnectionMethod,
    EndpointDiscoveryResult,
    EndpointTarget,
    OSFamily,
)
from vcf_ops_telegraf_helper.models.monitoring import MonitoringConfig
from vcf_ops_telegraf_helper.models.vcf import CollectorInfo, VCFEnvironment
from vcf_ops_telegraf_helper.models.workflow import (
    DeploymentMode,
    StageStatus,
    WorkflowOptions,
)
from vcf_ops_telegraf_helper.storage.state import StateStore
from vcf_ops_telegraf_helper.gui.main_window import MainWindow
from vcf_ops_telegraf_helper.workflow.engine import ConfigureEndpointWorkflow
from vcf_ops_telegraf_helper.workflow.windows import (
    WindowsTelegrafDetection,
    _extract_binary_path,
    detect_windows_telegraf,
)
import pytest

try:
    from PySide6.QtWidgets import QApplication
    has_pyside6 = True
except (ImportError, OSError):
    has_pyside6 = False


@pytest.fixture(scope="session")
def qapp():
    """Session-wide QApplication instance for offscreen GUI tests."""
    if not has_pyside6:
        pytest.skip("PySide6 not installed")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_extract_binary_path_variants():
    """Verify path extraction from unquoted, quoted, and argument-bearing strings."""
    assert _extract_binary_path(r"C:\telegraf\telegraf.exe") == r"C:\telegraf\telegraf.exe"
    assert (
        _extract_binary_path(r'"C:\Program Files\VMware\vcf-telegraf\telegraf.exe" --config foo')
        == r"C:\Program Files\VMware\vcf-telegraf\telegraf.exe"
    )
    assert (
        _extract_binary_path(r"'C:\Ops\telegraf.exe' service run")
        == r"C:\Ops\telegraf.exe"
    )
    assert (
        _extract_binary_path(r"C:\CustomPath\telegraf.exe --config C:\CustomPath\telegraf.conf")
        == r"C:\CustomPath\telegraf.exe"
    )
    assert _extract_binary_path("") == ""


def test_detect_windows_telegraf_service_path_outside_c_telegraf():
    """Mocked service output with a PathName outside C:\telegraf reports installed with that path and service name."""
    mock_exec = MagicMock()

    def _exec(cmd, **kw):
        if "Win32_Service" in cmd:
            return CommandResult(
                exit_code=0,
                stdout="vcf-telegraf|Running|C:\\Program Files\\VMware\\vcf-telegraf\\telegraf.exe\n",
                command=cmd,
            )
        if "version" in cmd:
            return CommandResult(exit_code=0, stdout="Telegraf 1.30.0 (git: abc1234)\n", command=cmd)
        return CommandResult(exit_code=0, stdout="", command=cmd)

    mock_exec.execute.side_effect = _exec
    mock_exec.file_exists.return_value = False

    det = detect_windows_telegraf(mock_exec)
    assert isinstance(det, WindowsTelegrafDetection)
    assert det.installed is True
    assert det.binary_path == r"C:\Program Files\VMware\vcf-telegraf\telegraf.exe"
    assert det.service_name == "vcf-telegraf"
    assert det.version == "Telegraf 1.30.0 (git: abc1234)"
    assert det.running is True

    # Test tuple unpacking: (binary_path, service_name, version)
    bin_path, svc_name, ver = det
    assert bin_path == r"C:\Program Files\VMware\vcf-telegraf\telegraf.exe"
    assert svc_name == "vcf-telegraf"
    assert ver == "Telegraf 1.30.0 (git: abc1234)"


def test_detect_windows_telegraf_running_process():
    """Step 2: detect via running process when service PathName is not found."""
    mock_exec = MagicMock()

    def _exec(cmd, **kw):
        if "Get-Process" in cmd:
            return CommandResult(exit_code=0, stdout="D:\\Agents\\telegraf.exe\n", command=cmd)
        if "version" in cmd:
            return CommandResult(exit_code=0, stdout="Telegraf 1.29.1\n", command=cmd)
        return CommandResult(exit_code=0, stdout="", command=cmd)

    mock_exec.execute.side_effect = _exec
    mock_exec.file_exists.return_value = False

    det = detect_windows_telegraf(mock_exec)
    assert det.installed is True
    assert det.binary_path == r"D:\Agents\telegraf.exe"
    assert det.service_name is None
    assert det.service_state == "Standalone"
    assert det.running is True
    assert det.version == "Telegraf 1.29.1"


def test_detect_windows_telegraf_path_environment():
    """Step 3: detect via PATH environment lookup."""
    mock_exec = MagicMock()

    def _exec(cmd, **kw):
        if "Get-Command" in cmd:
            return CommandResult(exit_code=0, stdout="C:\\Tools\\telegraf.exe\n", command=cmd)
        if "version" in cmd:
            return CommandResult(exit_code=0, stdout="Telegraf 1.28.0\n", command=cmd)
        return CommandResult(exit_code=0, stdout="", command=cmd)

    mock_exec.execute.side_effect = _exec
    mock_exec.file_exists.return_value = False

    det = detect_windows_telegraf(mock_exec)
    assert det.installed is True
    assert det.binary_path == r"C:\Tools\telegraf.exe"


def test_detect_windows_telegraf_registry_image_path():
    """Step 4: detect via registry ImagePath for service 'telegraf'."""
    mock_exec = MagicMock()

    def _exec(cmd, **kw):
        if "HKLM:\\SYSTEM\\CurrentControlSet\\Services\\telegraf" in cmd:
            return CommandResult(exit_code=0, stdout="C:\\Custom\\telegraf.exe\n", command=cmd)
        if "version" in cmd:
            return CommandResult(exit_code=0, stdout="Telegraf 1.27.0\n", command=cmd)
        return CommandResult(exit_code=0, stdout="", command=cmd)

    mock_exec.execute.side_effect = _exec
    mock_exec.file_exists.return_value = False

    det = detect_windows_telegraf(mock_exec)
    assert det.installed is True
    assert det.binary_path == r"C:\Custom\telegraf.exe"
    assert det.service_name == "telegraf"


def test_detect_windows_telegraf_known_folder():
    """Step 5: detect via known folder path fallback."""
    mock_exec = MagicMock()

    def _exec(cmd, **kw):
        if "Test-Path" in cmd and r"C:\telegraf\telegraf.exe" in cmd:
            return CommandResult(exit_code=0, stdout="True\n", command=cmd)
        if "version" in cmd:
            return CommandResult(exit_code=0, stdout="Telegraf 1.26.0\n", command=cmd)
        return CommandResult(exit_code=0, stdout="", command=cmd)

    mock_exec.execute.side_effect = _exec
    mock_exec.file_exists.return_value = False

    det = detect_windows_telegraf(mock_exec)
    assert det.installed is True
    assert det.binary_path == r"C:\telegraf\telegraf.exe"


def test_detect_windows_telegraf_not_installed():
    """Nothing found reports not installed."""
    mock_exec = MagicMock()
    mock_exec.execute.return_value = CommandResult(exit_code=0, stdout="", command="")
    mock_exec.file_exists.return_value = False

    det = detect_windows_telegraf(mock_exec)
    assert det.installed is False
    assert det.binary_path is None
    assert det.running is False


def test_gui_and_engine_consistent_on_custom_path(qapp, tmp_path):
    """GUI and engine return the same result for the same mocked responses."""
    def _create_canned_executor():
        mock = MagicMock()
        mock.test_connection.return_value = True
        mock.file_exists.return_value = False

        def _exec(cmd, **kw):
            if "Win32_Service" in cmd and "telegraf" in cmd:
                return CommandResult(
                    exit_code=0,
                    stdout="vcf-telegraf|Running|C:\\Program Files\\VMware\\vcf-telegraf\\telegraf.exe\n",
                    command=cmd,
                )
            if "version" in cmd:
                return CommandResult(exit_code=0, stdout="Telegraf 1.30.0\n", command=cmd)
            if "Win32_OperatingSystem" in cmd:
                return CommandResult(exit_code=0, stdout="Microsoft Windows Server 2022\n", command=cmd)
            if "$env:COMPUTERNAME" in cmd:
                return CommandResult(exit_code=0, stdout="DCINT1\n", command=cmd)
            return CommandResult(exit_code=0, stdout="", command=cmd)

        mock.execute.side_effect = _exec
        return mock

    # 1. Engine check
    env = VCFEnvironment(name="test", url="https://vcf.local", username="admin", collector=CollectorInfo(address="10.10.10.50"))
    target = EndpointTarget(
        hostname="dcint1.sentania.local",
        os_family=OSFamily.WINDOWS,
        connection_method=ConnectionMethod.WINRM,
        install_telegraf=True,
    )
    mon = MonitoringConfig()
    engine_exec = _create_canned_executor()
    wf = ConfigureEndpointWorkflow(
        environment=env,
        target=target,
        monitoring=mon,
        executor=engine_exec,
        adapter=MagicMock(),
        options=WorkflowOptions(mode=DeploymentMode.PUSH, install_telegraf=True),
    )
    stage_res = wf.detect_telegraf()
    assert stage_res.status == StageStatus.PASS
    assert wf.discovery.telegraf_installed is True
    assert wf.discovery.telegraf_bin_path == r"C:\Program Files\VMware\vcf-telegraf\telegraf.exe"

    # 2. GUI check
    gui_exec = _create_canned_executor()
    state_file = tmp_path / "state.json"
    store = StateStore(state_file=state_file)
    window = MainWindow(state_store=store)
    window.ep_os_combo.setCurrentText("Windows")
    window._create_executor = lambda t: gui_exec

    window.ep_user_input.setText("operator")
    window._detect_endpoint()
    assert window._endpoint_detected is True
    assert window.ep_missing_banner.isHidden() is True
    details = window.ep_details_box.toPlainText()
    assert "Telegraf Installed: YES" in details
    assert r"Telegraf Binary: C:\Program Files\VMware\vcf-telegraf\telegraf.exe" in details
    assert "Service Name: vcf-telegraf" in details


def test_detect_windows_telegraf_rejects_non_telegraf_service():
    """Verify service mentioning telegraf in arguments is rejected if binary is not telegraf.exe."""
    mock_exec = MagicMock()
    mock_exec.file_exists.return_value = False

    def _exec(cmd, **kw):
        if "Win32_Service" in cmd:
            return CommandResult(
                exit_code=0,
                stdout="watchdog|Running|C:\\Tools\\watchdog.exe --config C:\\telegraf\\telegraf.conf\n",
                command=cmd,
            )
        return CommandResult(exit_code=0, stdout="", command=cmd)

    mock_exec.execute.side_effect = _exec
    det = detect_windows_telegraf(mock_exec)
    assert det.installed is False


def test_detect_windows_telegraf_escapes_quotes_in_version():
    """Verify single quotes in binary path are properly doubled to prevent PowerShell injection."""
    mock_exec = MagicMock()
    mock_exec.file_exists.return_value = False

    executed_commands = []

    def _exec(cmd, **kw):
        executed_commands.append(cmd)
        if "Win32_Service" in cmd:
            return CommandResult(
                exit_code=0,
                stdout="telegraf|Running|C:\\Scott's Tools\\telegraf.exe\n",
                command=cmd,
            )
        if "version" in cmd:
            return CommandResult(exit_code=0, stdout="Telegraf 1.30.0\n", command=cmd)
        return CommandResult(exit_code=0, stdout="", command=cmd)

    mock_exec.execute.side_effect = _exec
    det = detect_windows_telegraf(mock_exec)
    assert det.installed is True
    version_cmds = [c for c in executed_commands if "version" in c]
    assert any("& 'C:\\Scott''s Tools\\telegraf.exe' version" in c for c in version_cmds)


def test_detect_windows_telegraf_sc_fallback_uses_double_quotes():
    """Verify sc.exe query fallback quotes service name with double quotes."""
    mock_exec = MagicMock()
    mock_exec.file_exists.return_value = False

    executed_commands = []

    def _exec(cmd, **kw):
        executed_commands.append(cmd)
        if "Get-Process" in cmd:
            return CommandResult(exit_code=0, stdout="C:\\telegraf\\telegraf.exe\n", command=cmd)
        if "Get-Service" in cmd:
            return CommandResult(exit_code=1, stdout="", stderr="Error", command=cmd)
        if "sc.exe query" in cmd:
            return CommandResult(exit_code=0, stdout="STATE : 4  RUNNING\n", command=cmd)
        if "version" in cmd:
            return CommandResult(exit_code=0, stdout="Telegraf 1.30.0\n", command=cmd)
        return CommandResult(exit_code=0, stdout="", command=cmd)

    mock_exec.execute.side_effect = _exec
    det = detect_windows_telegraf(mock_exec)
    assert det.installed is True
    assert det.running is True
    sc_cmds = [c for c in executed_commands if "sc.exe query" in c]
    assert any('sc.exe query "telegraf"' in c for c in sc_cmds)


def test_workflow_restart_uses_discovered_custom_service():
    """Verify workflow restart uses discovered service_name instead of hardcoding 'telegraf'."""
    env = VCFEnvironment(
        name="test",
        url="https://vcf.local",
        username="admin",
        collector=CollectorInfo(address="10.10.10.50"),
    )
    target = EndpointTarget(
        hostname="win-custom.local",
        os_family=OSFamily.WINDOWS,
        connection_method=ConnectionMethod.WINRM,
        install_telegraf=True,
    )
    mock_exec = MagicMock()
    executed_commands = []

    def _exec(cmd, **kw):
        executed_commands.append(cmd)
        if "Win32_Service" in cmd and "telegraf" in cmd:
            return CommandResult(
                exit_code=0,
                stdout="vcf-telegraf|Running|C:\\Ops\\telegraf.exe\n",
                command=cmd,
            )
        if "version" in cmd:
            return CommandResult(exit_code=0, stdout="Telegraf 1.30.0\n", command=cmd)
        if "Restart-Service" in cmd:
            return CommandResult(exit_code=0, stdout="Restarted", command=cmd)
        return CommandResult(exit_code=0, stdout="", command=cmd)

    mock_exec.execute.side_effect = _exec
    wf = ConfigureEndpointWorkflow(
        environment=env,
        target=target,
        monitoring=MonitoringConfig(),
        executor=mock_exec,
        adapter=MagicMock(),
        options=WorkflowOptions(mode=DeploymentMode.PUSH, install_telegraf=True),
    )
    wf.detect_telegraf()
    assert wf.discovery.service_name == "vcf-telegraf"

    restart_res = wf.restart_if_needed()
    assert restart_res.status == StageStatus.PASS
    assert any("Restart-Service 'vcf-telegraf' -Force" in c for c in executed_commands)


def test_windows_paths_with_apostrophe_escaped_in_workflow():
    """Verify paths with apostrophes are escaped in restart and validation commands."""
    from vcf_ops_telegraf_helper.validation.validator import Validator

    mock_exec = MagicMock()
    executed_commands = []

    def _exec(cmd, **kw):
        executed_commands.append(cmd)
        return CommandResult(exit_code=0, stdout="All plugins initialized", command=cmd)

    mock_exec.execute.side_effect = _exec

    # 1. Validator test mode
    val_res = Validator.validate_telegraf_config_on_endpoint(
        executor=mock_exec,
        telegraf_bin=r"C:\Scott's Tools\telegraf.exe",
        config_path=r"C:\Scott's Tools\telegraf.conf",
        config_dir=r"C:\Scott's Tools\telegraf.d",
        is_windows=True,
    )
    assert val_res.is_valid is True
    assert (
        r"& 'C:\Scott''s Tools\telegraf.exe' --test --config 'C:\Scott''s Tools\telegraf.conf' --config-directory 'C:\Scott''s Tools\telegraf.d'"
        in executed_commands[-1]
    )

    # 2. Workflow restart pre-flight check
    env = VCFEnvironment(name="test", url="https://vcf.local", username="admin", collector=CollectorInfo(address="10.10.10.50"))
    target = EndpointTarget(
        hostname="win-app01.local",
        os_family=OSFamily.WINDOWS,
        connection_method=ConnectionMethod.WINRM,
        install_telegraf=True,
    )
    wf = ConfigureEndpointWorkflow(
        environment=env,
        target=target,
        monitoring=MonitoringConfig(),
        executor=mock_exec,
        adapter=MagicMock(),
        options=WorkflowOptions(mode=DeploymentMode.PUSH, install_telegraf=True),
    )
    wf.discovery = EndpointDiscoveryResult(
        hostname="win-app01",
        telegraf_installed=True,
        telegraf_bin_path=r"C:\Scott's Tools\telegraf.exe",
        main_config_path=r"C:\Scott's Tools\telegraf.conf",
        config_dir=r"C:\Scott's Tools\telegraf.d",
        service_name="telegraf",
    )
    restart_res = wf.restart_if_needed()
    assert restart_res.status == StageStatus.PASS
    assert any(
        r"& 'C:\Scott''s Tools\telegraf.exe' --test --config 'C:\Scott''s Tools\telegraf.conf' --config-directory 'C:\Scott''s Tools\telegraf.d'"
        in c
        for c in executed_commands
    )


def test_standalone_process_registers_service_on_restart():
    """When a standalone process exists without a Windows service, register and start the service."""
    mock_exec = MagicMock()
    executed_commands = []

    def _exec(cmd, **kw):
        executed_commands.append(cmd)
        if "version" in cmd:
            return CommandResult(exit_code=0, stdout="Telegraf 1.30.0\n", command=cmd)
        if "--service install" in cmd:
            return CommandResult(exit_code=0, stdout="Service telegraf installed", command=cmd)
        if "Restart-Service" in cmd:
            return CommandResult(exit_code=0, stdout="Restarted", command=cmd)
        return CommandResult(exit_code=0, stdout="", command=cmd)

    mock_exec.execute.side_effect = _exec

    env = VCFEnvironment(name="test", url="https://vcf.local", username="admin", collector=CollectorInfo(address="10.10.10.50"))
    target = EndpointTarget(
        hostname="win-app02.local",
        os_family=OSFamily.WINDOWS,
        connection_method=ConnectionMethod.WINRM,
    )
    wf = ConfigureEndpointWorkflow(
        environment=env,
        target=target,
        monitoring=MonitoringConfig(),
        executor=mock_exec,
        adapter=MagicMock(),
        options=WorkflowOptions(mode=DeploymentMode.PUSH),
    )
    wf.discovery = EndpointDiscoveryResult(
        hostname="win-app02",
        telegraf_installed=True,
        telegraf_bin_path=r"C:\telegraf\telegraf.exe",
        main_config_path=r"C:\telegraf\telegraf.conf",
        config_dir=r"C:\telegraf\telegraf.d",
        service_name=None,  # No service registered
    )
    restart_res = wf.restart_if_needed()
    assert restart_res.status == StageStatus.PASS
    assert wf.discovery.service_name == "telegraf"
    assert any("--service install" in c for c in executed_commands)
    assert any("Restart-Service telegraf -Force" in c for c in executed_commands)




def test_service_configuration_arguments_are_retained():
    from vcf_ops_telegraf_helper.workflow.windows import WindowsTelegrafDetection
    detected = WindowsTelegrafDetection(
        installed=True, binary_path=r'C:\Program Files\Telegraf\telegraf.exe',
        service_name='telegraf',
        command_line=r'"C:\Program Files\Telegraf\telegraf.exe" --service run --config "D:\Agent Config\main.conf" --config-directory="D:\Agent Config\inputs"',
    )
    assert detected.main_config_path == r'D:\Agent Config\main.conf'
    assert detected.config_dir == r'D:\Agent Config\inputs'
