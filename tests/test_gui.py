"""Tests for native Lattice-styled PySide6 GUI.

Adheres to the photoflow testing pattern:
- Sets QT_QPA_PLATFORM=offscreen before importing QtWidgets so tests run headless.
- Uses pytest.importorskip to guard Qt imports.
- Tests window instantiation, step navigation, theme switching, preview rendering,
  and worker signals.
"""

from __future__ import annotations

import os
import pytest

# Ensure Qt runs offscreen in headless test environments
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication, QLabel
    from vcf_ops_telegraf_helper import __version__
    from vcf_ops_telegraf_helper.gui.main_window import MainWindow
    from vcf_ops_telegraf_helper.gui.theme import build_stylesheet
    from vcf_ops_telegraf_helper.models.endpoint import ConnectionMethod
    from vcf_ops_telegraf_helper.storage.state import StateStore
except (ImportError, OSError) as exc:
    pytest.skip(
        f"PySide6 GUI tests skipped (missing library or display dependency: {exc})",
        allow_module_level=True,
    )


@pytest.fixture(scope="session")
def qapp():
    """Session-wide QApplication instance for offscreen GUI tests."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_theme_generation():
    """Verify Lattice tokens compile into valid QSS strings."""
    dark_qss = build_stylesheet("dark")
    assert "#1b1f24" in dark_qss  # bg
    assert "#23282f" in dark_qss  # surface
    assert "#363d47" in dark_qss  # line
    assert "#199e70" in dark_qss  # ok
    assert "#d95926" in dark_qss  # bad
    assert "QComboBox::down-arrow" in dark_qss
    assert "chevron_down_dark.svg" in dark_qss

    light_qss = build_stylesheet("light")
    assert "#f6f7f9" in light_qss  # bg
    assert "#ffffff" in light_qss  # surface
    assert "#d9dee5" in light_qss  # line
    assert "QComboBox::down-arrow" in light_qss
    assert "chevron_down_light.svg" in light_qss


def test_main_window_initialization(qapp, tmp_path):
    """Verify MainWindow initializes with all 5 steps and defaults."""
    state_file = tmp_path / "state.json"
    store = StateStore(state_file=state_file)

    window = MainWindow(state_store=store)
    assert window.windowTitle() == "VCF Operations Open Telegraf Helper"
    labels = [lbl.text() for lbl in window.findChildren(QLabel)]
    assert f"VCF Operations Open Telegraf Helper v{__version__}" in labels
    assert window.step_list.count() == 5
    assert window.page_stack.count() == 5
    assert window.current_theme == "dark"


def test_main_window_step_navigation(qapp, tmp_path):
    """Verify navigating through steps changes active page and updates preview."""
    state_file = tmp_path / "state.json"
    store = StateStore(state_file=state_file)
    window = MainWindow(state_store=store)

    # Step 1 -> Step 2
    window.step_list.setCurrentRow(1)
    assert window.page_stack.currentIndex() == 1

    # Step 2 -> Step 3
    window.step_list.setCurrentRow(2)
    assert window.page_stack.currentIndex() == 2

    # Step 3 -> Step 4 (triggers preview update)
    window.step_list.setCurrentRow(3)
    assert window.page_stack.currentIndex() == 3
    assert "[[inputs.cpu]]" in window.preview_system_box.toPlainText()
    assert "[[outputs.http]]" in window.preview_output_box.toPlainText()


def test_main_window_theme_toggle(qapp, tmp_path):
    """Verify toggling theme alternates between dark and light."""
    state_file = tmp_path / "state.json"
    store = StateStore(state_file=state_file)
    window = MainWindow(state_store=store)

    assert window.current_theme == "dark"
    window._toggle_theme()
    assert window.current_theme == "light"
    window._toggle_theme()
    assert window.current_theme == "dark"


def test_main_window_endpoint_detection_linux(qapp, tmp_path):
    """Verify Linux endpoint detection updates UI state with discovered details."""
    from unittest.mock import MagicMock
    from vcf_ops_telegraf_helper.executors.base import CommandResult

    state_file = tmp_path / "state.json"
    store = StateStore(state_file=state_file)
    window = MainWindow(state_store=store)

    window.ep_host_input.setText("linux-host.corp.local")
    mock_exec = MagicMock()
    mock_exec.test_connection.return_value = True
    mock_exec.execute.side_effect = lambda cmd, **kw: CommandResult(
        exit_code=0,
        stdout='PRETTY_NAME="Ubuntu 22.04 LTS"' if "os-release" in cmd else ("active" if "systemctl" in cmd else "x86_64"),
        command=cmd,
    )
    window._create_executor = lambda target: mock_exec

    window._detect_endpoint()
    assert "Connected & Discovered" in window.ep_status_label.text()
    assert "Ubuntu 22.04 LTS" in window.ep_details_box.toPlainText()


def test_main_window_endpoint_detection_windows(qapp, tmp_path):
    """Verify Windows endpoint detection adjusts defaults and shows Windows paths."""
    state_file = tmp_path / "state.json"
    store = StateStore(state_file=state_file)
    window = MainWindow(state_store=store)

    window.ep_os_combo.setCurrentText("Windows")
    assert window.ep_user_input.text() == "Administrator"
    assert not window.ep_key_input.isEnabled()
    target = window._get_endpoint_target()
    assert target.connection_method == ConnectionMethod.WINRM
    assert window.ep_port_input.text() == "5985"
    assert window.ep_auto_install_check.isChecked()

    from unittest.mock import MagicMock
    mock_exec = MagicMock()
    mock_exec.test_connection.return_value = True
    mock_exec.file_exists.return_value = False
    window._create_executor = lambda target: mock_exec

    window._detect_endpoint()
    assert "Connected & Discovered (Windows)" in window.ep_status_label.text()
    assert "C:\\telegraf\\telegraf.d" in window.ep_details_box.toPlainText()
    assert "InfluxData" in window.ep_details_box.toPlainText()
    assert not window.ep_missing_banner.isHidden()


def test_main_window_endpoint_detection_preserves_auto_install_opt_out(qapp, tmp_path):
    """Verify endpoint detection does not re-enable auto-install if admin unchecked it."""
    state_file = tmp_path / "state.json"
    store = StateStore(state_file=state_file)
    window = MainWindow(state_store=store)

    window.ep_auto_install_check.setChecked(False)

    from unittest.mock import MagicMock
    mock_exec = MagicMock()
    mock_exec.test_connection.return_value = True
    mock_exec.file_exists.return_value = False
    mock_exec.execute.return_value = MagicMock(success=False, stdout="")
    window._create_executor = lambda target: mock_exec

    window._detect_endpoint()
    assert not window.ep_missing_banner.isHidden()
    assert not window.ep_auto_install_check.isChecked()
    assert "NO (auto-install disabled)" in window.ep_details_box.toPlainText()


def test_main_window_endpoint_detection_resets_missing_banner_on_failure(qapp, tmp_path):
    """Verify endpoint detection hides missing banner when connection fails or raises exception."""
    state_file = tmp_path / "state.json"
    store = StateStore(state_file=state_file)
    window = MainWindow(state_store=store)

    window.ep_missing_banner.setVisible(True)
    assert not window.ep_missing_banner.isHidden()

    from unittest.mock import MagicMock
    mock_exec = MagicMock()
    mock_exec.test_connection.return_value = False
    window._create_executor = lambda target: mock_exec

    window._detect_endpoint()
    assert window.ep_missing_banner.isHidden()
    assert "Connection failed" in window.ep_status_label.text()


def test_main_window_docker_endpoint_os_adaptation(qapp, tmp_path):
    """Verify Docker endpoint defaults adapt between Windows named pipe and Linux Unix socket."""
    state_file = tmp_path / "state.json"
    store = StateStore(state_file=state_file)
    window = MainWindow(state_store=store)

    window.ep_os_combo.setCurrentText("Linux")
    assert window.docker_endpoint_input.text() == "unix:///var/run/docker.sock"
    mon_linux = window._get_monitoring_config()
    assert mon_linux.docker.endpoint == "unix:///var/run/docker.sock"

    window.ep_os_combo.setCurrentText("Windows")
    assert window.docker_endpoint_input.text() == "npipe:////./pipe/docker_engine"
    mon_win = window._get_monitoring_config()
    assert mon_win.docker.endpoint == "npipe:////./pipe/docker_engine"


def test_main_window_vcf_connection(qapp, tmp_path):
    """Verify VCF connection test with adapter updates UI status."""
    from unittest.mock import MagicMock, patch

    state_file = tmp_path / "state.json"
    store = StateStore(state_file=state_file)
    window = MainWindow(state_store=store)

    mock_adapter = MagicMock()
    mock_adapter.validate_connection.return_value = True
    with patch("vcf_ops_telegraf_helper.gui.main_window.get_adapter", return_value=mock_adapter):
        window._test_vcf_connection()

    assert "PASS" in window.vcf_status_label.text()


def test_main_window_step3_plugins_and_preview(qapp, tmp_path):
    """Verify plugin catalog, selection, and preview updates."""
    state_file = tmp_path / "state.json"
    store = StateStore(state_file=state_file)
    window = MainWindow(state_store=store)

    assert window.plugin_catalog_list.count() == 18
    assert window.plugin_config_stack.count() == 18

    # Test catalog item selection switches stack
    window.plugin_catalog_list.setCurrentRow(10)  # NGINX
    assert window.plugin_config_stack.currentIndex() == 10

    window.nginx_check.setChecked(True)
    window.nginx_url_input.setText("http://127.0.0.1/status")
    window.custom_toml_input.setPlainText("[[inputs.ping]]\n  urls = ['8.8.8.8']")

    mon = window._get_monitoring_config()
    assert mon.nginx.enabled is True
    assert mon.nginx.urls == ["http://127.0.0.1/status"]
    assert "[[inputs.ping]]" in mon.custom_toml

    window.ep_auto_install_check.setChecked(True)
    window._update_preview()
    preview_txt = window.preview_system_box.toPlainText()
    assert "[[inputs.nginx]]" in preview_txt
    assert "[[inputs.ping]]" in preview_txt
    summary_linux = window.review_summary_box.toPlainText()
    assert "Install official InfluxData agent 1.40.1 via native package manager" in summary_linux
    assert "downloads/salt" not in summary_linux

    window.ep_os_combo.setCurrentText("Windows")
    window._update_preview()
    summary_win = window.review_summary_box.toPlainText()
    assert "Install official InfluxData agent release 1.40.1 package" in summary_win
    assert "downloads/salt" not in summary_win

    window.ep_auto_install_check.setChecked(False)
    window._update_preview()
    assert "Verify existing pre-installed Telegraf agent" in window.review_summary_box.toPlainText()


def test_main_window_workflow_worker(qapp):
    """Verify WorkflowWorker executes and emits stage_updated and finished signals."""
    from unittest.mock import MagicMock
    from vcf_ops_telegraf_helper.executors.mock import MockExecutor
    from vcf_ops_telegraf_helper.gui.main_window import WorkflowWorker
    from vcf_ops_telegraf_helper.models.endpoint import EndpointTarget
    from vcf_ops_telegraf_helper.models.vcf import CollectorInfo, VCFEnvironment
    from vcf_ops_telegraf_helper.models.monitoring import MonitoringConfig
    from vcf_ops_telegraf_helper.models.workflow import RunSummary, WorkflowOptions

    env = VCFEnvironment(
        name="test",
        url="https://vcf.local",
        username="admin",
        collector=CollectorInfo(address="10.10.10.50"),
    )
    target = EndpointTarget(hostname="10.10.10.101")
    mon = MonitoringConfig()
    executor = MockExecutor(connected=True, telegraf_installed=True)
    adapter = MagicMock()
    adapter.get_integration_artifacts.return_value = MagicMock(
        cluster_id="c1",
        thumbprint="th",
        ca_cert="ca",
        client_cert="cert",
        client_key="key",
    )
    opts = WorkflowOptions(dry_run=True)

    worker = WorkflowWorker(
        environment=env,
        target=target,
        monitoring=mon,
        executor=executor,
        adapter=adapter,
        options=opts,
    )

    stages_seen = []
    worker.stage_updated.connect(lambda res: stages_seen.append(res))
    results_summary = []
    worker.finished.connect(lambda sumry: results_summary.append(sumry))

    worker.run()

    assert len(stages_seen) == 8
    assert len(results_summary) == 1
    assert isinstance(results_summary[0], RunSummary)


def test_main_window_plugin_catalog_two_pane_and_presets(qapp, tmp_path):
    """Verify Approach A two-pane catalog presets and two-way synchronization."""
    from PySide6.QtCore import Qt

    state_file = tmp_path / "state.json"
    store = StateStore(state_file=state_file)
    window = MainWindow(state_store=store)

    # 1. Verify preset: Clear Workloads
    window.nginx_check.setChecked(True)
    window.mysql_check.setChecked(True)
    assert window.nginx_check.isChecked() is True

    window._clear_workload_plugins()
    assert window.nginx_check.isChecked() is False
    assert window.mysql_check.isChecked() is False
    assert window.cpu_check.isChecked() is True

    # 2. Verify preset: Select All respects target OS
    window.ep_os_combo.setCurrentText("Linux")
    window._select_all_plugins()
    assert window.nginx_check.isChecked() is True
    assert window.docker_check.isChecked() is True
    assert window.mssql_check.isChecked() is True
    assert window.sys_check.isChecked() is True
    assert window.swap_check.isChecked() is True
    assert window.win_perf_check.isChecked() is False
    assert window.win_svc_check.isChecked() is False

    window.ep_os_combo.setCurrentText("Windows")
    window._select_all_plugins()
    assert window.win_perf_check.isChecked() is True
    assert window.win_svc_check.isChecked() is True
    assert window.sys_check.isChecked() is False
    assert window.swap_check.isChecked() is False

    # 3. Verify preset: Baseline
    window.ep_os_combo.setCurrentText("Linux")
    window._apply_baseline_preset()
    assert window.cpu_check.isChecked() is True
    assert window.mem_check.isChecked() is True
    assert window.disk_check.isChecked() is True
    assert window.net_check.isChecked() is True
    assert window.sys_check.isChecked() is True
    assert window.swap_check.isChecked() is True
    assert window.win_perf_check.isChecked() is False
    assert window.nginx_check.isChecked() is False

    # 4. Baseline on Windows enables win_perf and win_svc while excluding Linux-only system/swap
    window.ep_os_combo.setCurrentText("Windows")
    window._apply_baseline_preset()
    assert window.win_perf_check.isChecked() is True
    assert window.win_svc_check.isChecked() is True
    assert window.sys_check.isChecked() is False
    assert window.swap_check.isChecked() is False

    # 5. List check state syncs to checkbox
    # Index 10 is NGINX
    nginx_item = window.plugin_catalog_list.item(10)
    assert nginx_item is not None
    nginx_item.setCheckState(Qt.Checked)
    assert window.nginx_check.isChecked() is True

    nginx_item.setCheckState(Qt.Unchecked)
    assert window.nginx_check.isChecked() is False

    # 6. Custom TOML auto-check respects manual disable
    window.custom_toml_input.setPlainText("[[inputs.mem]]")
    assert window.custom_toml_check.isChecked() is True

    window.custom_toml_check.setChecked(False)
    assert getattr(window, "_custom_toml_manually_unchecked", False) is True

    window.custom_toml_input.setPlainText("[[inputs.mem]]\n  fielddrop = [\"active\"]")
    assert window.custom_toml_check.isChecked() is False

    window.custom_toml_input.setPlainText("")
    assert window.custom_toml_check.isChecked() is False
    assert getattr(window, "_custom_toml_manually_unchecked", False) is False


def test_main_window_endpoint_detection_installed_hides_banner(qapp, tmp_path):
    """Verify missing agent banner is hidden when Telegraf is already installed."""
    from unittest.mock import MagicMock
    from vcf_ops_telegraf_helper.executors.base import CommandResult

    state_file = tmp_path / "state.json"
    store = StateStore(state_file=state_file)
    window = MainWindow(state_store=store)

    mock_exec = MagicMock()
    mock_exec.test_connection.return_value = True
    mock_exec.execute.side_effect = lambda cmd, **kw: CommandResult(
        exit_code=0,
        stdout="/usr/bin/telegraf" if "which telegraf" in cmd else "active",
        command=cmd,
    )
    window._create_executor = lambda target: mock_exec

    window._detect_endpoint()
    assert "Connected & Discovered" in window.ep_status_label.text()
    assert window.ep_missing_banner.isHidden() is True


def test_main_window_endpoint_auth_and_advanced_options_visibility(qapp, tmp_path):
    """Verify endpoint target UI toggles authentication modes and hides port by default."""
    state_file = tmp_path / "state.json"
    store = StateStore(state_file=state_file)
    window = MainWindow(state_store=store)

    # Linux default: SSH, port 22, SSH key path visible, password hidden, port hidden
    assert window.ep_os_combo.currentText() == "Linux"
    assert window._get_endpoint_target().connection_method == ConnectionMethod.SSH
    assert window.ep_port_input.text() == "22"
    assert window.ep_port_input.isHidden() is True
    assert window.ep_key_input.isHidden() is False
    assert window.ep_pass_input.isHidden() is True

    # Toggle to Password for Linux: password visible, key path hidden
    window.ep_auth_type_combo.setCurrentText("Username & Password")
    assert window.ep_key_input.isHidden() is True
    assert window.ep_pass_input.isHidden() is False

    # Switch to Windows while advanced is unchecked: updates port to 5985 and locks WinRM
    window.ep_os_combo.setCurrentText("Windows")
    assert window._get_endpoint_target().connection_method == ConnectionMethod.WINRM
    assert window.ep_port_input.text() == "5985"
    assert window.ep_key_input.isHidden() is True
    assert window.ep_pass_input.isHidden() is False
    assert window.ep_auth_type_combo.isHidden() is True

    # Expand advanced connection options: port input visible
    window.ep_advanced_check.setChecked(True)
    assert window.ep_port_input.isHidden() is False

    # Custom port preservation when advanced options is open
    window.ep_port_input.setText("5986")
    window.ep_os_combo.setCurrentText("Linux")
    assert window.ep_port_input.text() == "5986"


def test_main_window_step5_cli_command_generation_and_copy(qapp, tmp_path):
    """Verify Step 5 generates exact repeatable CLI command and supports copying to clipboard."""
    state_file = tmp_path / "state.json"
    store = StateStore(state_file=state_file)
    window = MainWindow(state_store=store)

    cmd = window.cli_command_box.toPlainText()
    assert "vcf-telegraf-helper run" in cmd
    assert "--target-host 10.10.10.101" in cmd or '--target-host "10.10.10.101"' in cmd
    assert "--connection ssh" in cmd or '--connection "ssh"' in cmd
    assert "--install-telegraf" in cmd
    assert "--dry-run" not in cmd

    # Toggling dry run updates the CLI command box immediately
    window.dry_run_check.setChecked(True)
    cmd_dry = window.cli_command_box.toPlainText()
    assert "--dry-run" in cmd_dry

    # Test copy command
    window._copy_cli_command()
    clipboard = QApplication.clipboard()
    if clipboard:
        assert clipboard.text() == cmd_dry

    # Non-standard port emits --port
    window.ep_port_input.setText("2222")
    window._update_cli_command()
    cmd_custom_port = window.cli_command_box.toPlainText()
    assert "--port 2222" in cmd_custom_port

    # API token mode emits --vcf-token placeholder
    window.vcf_auth_type_combo.setCurrentText("API Token / Key")
    window._update_cli_command()
    cmd_token = window.cli_command_box.toPlainText()
    assert '--vcf-token "<token>"' in cmd_token


def test_main_window_vcf_auth_toggle(qapp, tmp_path):
    """Verify Step 1 VCF Operations authentication toggle between API Token/Key and Username/Password."""
    state_file = tmp_path / "state.json"
    store = StateStore(state_file=state_file)
    window = MainWindow(state_store=store)

    # Default is API Token / Key: token visible, user/pass hidden
    assert window.vcf_auth_type_combo.currentText() == "API Token / Key"
    assert window.vcf_token_input.isHidden() is False
    assert window.vcf_user_input.isHidden() is True
    assert window.vcf_pass_input.isHidden() is True

    window.vcf_token_input.setText("test-secret-token")
    env = window._get_vcf_env()
    assert env.token == "test-secret-token"
    assert env.password is None

    # Switch to Username & Password: token hidden, user/pass visible
    window.vcf_auth_type_combo.setCurrentText("Username & Password")
    assert window.vcf_token_input.isHidden() is True
    assert window.vcf_user_input.isHidden() is False
    assert window.vcf_pass_input.isHidden() is False

    window.vcf_user_input.setText("opsadmin")
    window.vcf_pass_input.setText("secretpass123")
    env = window._get_vcf_env()
    assert env.token is None
    assert env.username == "opsadmin"
    assert env.password == "secretpass123"

    # Verify preference was persisted to store
    assert store.get_preference("vcf_auth_mode") == "Username & Password"

    # Verify a new window reloads the saved preference correctly
    window2 = MainWindow(state_store=store)
    assert window2.vcf_auth_type_combo.currentText() == "Username & Password"
    assert window2.vcf_token_input.isHidden() is True
    assert window2.vcf_user_input.isHidden() is False
    assert window2.vcf_pass_input.isHidden() is False


def test_main_window_uninstall_agent_button(qapp, monkeypatch):
    """Verify GUI uninstall button triggers confirmation and updates status."""
    from PySide6.QtWidgets import QMessageBox
    from vcf_ops_telegraf_helper.models.workflow import UninstallSummary

    win = MainWindow()
    assert hasattr(win, "ep_uninstall_btn")
    assert win.ep_uninstall_btn.text() == "Uninstall Agent..."

    # 1. User cancels prompt
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.No)
    win._on_uninstall_agent_clicked()
    assert "Uninstalling" not in win.ep_status_label.text()

    # 2. User confirms prompt
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "vcf_ops_telegraf_helper.workflow.uninstall.UninstallEndpointWorkflow.run",
        lambda self: UninstallSummary(target_hostname="10.10.10.101", success=True),
    )

    win._on_uninstall_agent_clicked()
    if win.uninstall_worker_thread:
        win.uninstall_worker_thread.wait(2000)
    qapp.processEvents()
    assert win.ep_status_label.text() == "Telegraf completely uninstalled"
    assert win.ep_missing_banner.isHidden() is False


def test_main_window_telegraf_version_selection(qapp, tmp_path):
    """Verify Telegraf version combobox presets, custom editing, toggle handling, and CLI export."""
    state_file = tmp_path / "state.json"
    store = StateStore(state_file=state_file)
    window = MainWindow(state_store=store)

    assert hasattr(window, "ep_version_combo")
    assert window.ep_version_combo.isEditable() is True
    assert window.ep_version_combo.count() >= 4
    assert window.ep_version_combo.minimumWidth() >= 380
    assert window.ep_version_combo.lineEdit().cursorPosition() == 0
    assert window._get_selected_telegraf_version() == "1.40.1"

    # Toggle auto-install disables combo
    window.ep_auto_install_check.setChecked(False)
    assert window.ep_version_combo.isEnabled() is False
    window.ep_auto_install_check.setChecked(True)
    assert window.ep_version_combo.isEnabled() is True

    # Select preset 1.34.0
    window.ep_version_combo.setCurrentIndex(1)
    assert window._get_selected_telegraf_version() == "1.34.0"
    target = window._get_endpoint_target()
    assert target.telegraf_version == "1.34.0"

    cli_cmd = window._build_cli_command()
    assert "--telegraf-version 1.34.0" in cli_cmd

    # Type custom version
    window.ep_version_combo.setEditText("1.39.2")
    assert window._get_selected_telegraf_version() == "1.39.2"
    target_custom = window._get_endpoint_target()
    assert target_custom.telegraf_version == "1.39.2"


def test_main_window_cli_command_cleared_linux_baseline(qapp, tmp_path):
    """Verify GUI emits explicit negative flags when Linux core plugins are cleared."""
    state_file = tmp_path / "state.json"
    store = StateStore(state_file=state_file)
    window = MainWindow(state_store=store)

    window.ep_os_combo.setCurrentText("Linux")
    window.cpu_check.setChecked(False)
    window.mem_check.setChecked(False)
    window.disk_check.setChecked(False)
    window.net_check.setChecked(False)
    window.sys_check.setChecked(False)
    window.swap_check.setChecked(False)
    window.diskio_check.setChecked(False)
    window.proc_check.setChecked(False)

    cmd = window._build_cli_command()
    assert "--no-cpu" in cmd
    assert "--no-mem" in cmd
    assert "--no-disk" in cmd
    assert "--no-net" in cmd
    assert "--no-system" in cmd
    assert "--no-swap" in cmd


def test_main_window_cli_command_cleared_windows_baseline(qapp, tmp_path):
    """Verify GUI emits explicit negative flags when Windows core plugins are cleared."""
    state_file = tmp_path / "state.json"
    store = StateStore(state_file=state_file)
    window = MainWindow(state_store=store)

    window.ep_os_combo.setCurrentText("Windows")
    window.win_perf_check.setChecked(False)
    window.win_svc_check.setChecked(False)

    cmd = window._build_cli_command()
    assert "--no-win-perf" in cmd
    assert "--no-win-services" in cmd




