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
    assert window.ep_auth_radio_widget.isHidden() is True
    target = window._get_endpoint_target()
    assert target.connection_method == ConnectionMethod.WINRM
    assert window.ep_port_input.text() == "5985"
    assert "Auto-install" in window.ep_version_combo.currentText()

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
    """Verify endpoint detection does not re-enable auto-install if admin selected Do Not Install."""
    state_file = tmp_path / "state.json"
    store = StateStore(state_file=state_file)
    window = MainWindow(state_store=store)

    window.ep_version_combo.setCurrentText("Do Not Install (Use Existing Host Agent)")

    from unittest.mock import MagicMock
    mock_exec = MagicMock()
    mock_exec.test_connection.return_value = True
    mock_exec.file_exists.return_value = False
    mock_exec.execute.return_value = MagicMock(success=False, stdout="")
    window._create_executor = lambda target: mock_exec

    window._detect_endpoint()
    assert not window.ep_missing_banner.isHidden()
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
    window.ep_version_combo.setCurrentIndex(0)
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

    window.ep_version_combo.setCurrentText("Do Not Install (Use Existing Host Agent)")
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

    # Selecting Do Not Install sets install_telegraf to False
    window.ep_version_combo.setCurrentText("Do Not Install (Use Existing Host Agent)")
    assert window._get_endpoint_target().install_telegraf is False
    window.ep_version_combo.setCurrentIndex(0)
    assert window._get_endpoint_target().install_telegraf is True

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
    assert "--no-baseline" in cmd


def test_main_window_cli_command_cleared_windows_baseline(qapp, tmp_path):
    """Verify GUI emits --no-baseline when Windows core plugins are cleared."""
    state_file = tmp_path / "state.json"
    store = StateStore(state_file=state_file)
    window = MainWindow(state_store=store)

    window.ep_os_combo.setCurrentText("Windows")
    window.win_perf_check.setChecked(False)
    window.win_svc_check.setChecked(False)

    cmd = window._build_cli_command()
    assert "--no-baseline" in cmd


def test_main_window_cli_command_selective_flags(qapp, tmp_path):
    """Verify GUI emits selective negative and positive flags when some plugins are toggled."""
    state_file = tmp_path / "state.json"
    store = StateStore(state_file=state_file)
    window = MainWindow(state_store=store)

    window.ep_os_combo.setCurrentText("Linux")
    window.swap_check.setChecked(False)
    window.diskio_check.setChecked(True)

    cmd = window._build_cli_command()
    assert "--no-swap" in cmd
    assert "--diskio" in cmd


def test_main_window_worker_finished_summary_handling(qapp, tmp_path):
    """Verify _on_worker_finished handles RunSummary without AttributeError on verifications."""
    from vcf_ops_telegraf_helper.models.workflow import RunSummary, StageResult, StageStatus, WorkflowStage

    state_file = tmp_path / "state.json"
    store = StateStore(state_file=state_file)
    window = MainWindow(state_store=store)

    summary = RunSummary(
        target_hostname="10.10.10.101",
        vcf_environment="https://vcf-ops.local",
        collector_address="10.10.10.50",
        success=True,
        stages=[
            StageResult(stage=WorkflowStage.CONNECT, status=StageStatus.PASS, message="Connected"),
            StageResult(stage=WorkflowStage.RESTART, status=StageStatus.PASS, message="Restarted"),
        ],
        verifications={
            "Telegraf installed": "PASS",
            "Config valid": "PASS",
            "Service running": "PASS",
            "Collector reachable": "PASS",
        },
        managed_files=["/etc/telegraf/telegraf.d/vcf-helper-system.conf"],
    )

    # Should not raise AttributeError: 'dict' object has no attribute 'collector_reachable'
    window._on_worker_finished(summary)
    assert window.execute_btn.isEnabled()
    assert window.export_md_btn.isEnabled()
    assert window.export_json_btn.isEnabled()
    log_text = window.stage_list_box.toPlainText()
    assert "OPERATIONAL VERIFICATION: PASS" in log_text
    assert "Telegraf installed" in log_text
    assert "Collector reachable" in log_text


def test_discovery_dialogs_instantiation_and_selection(qapp):
    """Verify live discovery modal dialogs populate tables and accept selections."""
    from vcf_ops_telegraf_helper.gui.discovery_dialogs import (
        DatabaseConnectDialog,
        DatabaseDiscoveryDialog,
        PerfmonDiscoveryDialog,
        ServicesDiscoveryDialog,
    )
    from vcf_ops_telegraf_helper.models.discovery import (
        DiscoveredDatabase,
        DiscoveredPerfmonSet,
        DiscoveredService,
    )

    # Services dialog
    services = [
        DiscoveredService(name="telegraf", display_name="Telegraf Data Collector", status="running"),
        DiscoveredService(name="w3svc", display_name="World Wide Web Publishing", status="running"),
    ]
    svc_dlg = ServicesDiscoveryDialog(None, services, initial_selected=["telegraf"])
    assert svc_dlg.table.rowCount() == 2
    svc_dlg._on_accept()
    assert "telegraf" in svc_dlg.selected_services

    # Perfmon dialog
    p_sets = [
        DiscoveredPerfmonSet(name="Processor", description="CPU performance", counters=["% Processor Time"]),
    ]
    p_dlg = PerfmonDiscoveryDialog(None, p_sets)
    assert p_dlg.table.rowCount() == 1

    # Database connect dialog
    conn_dlg = DatabaseConnectDialog(None, "Microsoft SQL Server", default_port=1433)
    assert conn_dlg.port == 1433
    assert conn_dlg.auth_mode == "integrated"
    # The display title must still enable the SQL Server auth choices and report "sql"
    assert conn_dlg.engine_name == "mssql"
    conn_dlg.radio_sql.setChecked(True)
    conn_dlg.user_input.setText("telegraf")
    conn_dlg.pass_input.setText("pw")
    conn_dlg._on_submit()
    assert conn_dlg.auth_mode == "sql"
    assert conn_dlg.username == "telegraf"

    # Database discovery dialog
    dbs = [
        DiscoveredDatabase(name="master", state="ONLINE", db_type="system", size_mb=10.0),
        DiscoveredDatabase(name="ProductionDB", state="ONLINE", db_type="user", size_mb=500.0),
    ]
    db_dlg = DatabaseDiscoveryDialog(None, "MSSQL", dbs)
    assert db_dlg.table.rowCount() == 2
    db_dlg._on_accept()
    assert "ProductionDB" in db_dlg.selected_databases


def _mock_window(qapp, tmp_path, monkeypatch):
    """MainWindow wired to the mock VCF adapter and a mock endpoint executor."""
    from vcf_ops_telegraf_helper.adapters.mock import MockVCFOpsIntegration
    from vcf_ops_telegraf_helper.executors.mock import MockExecutor
    from vcf_ops_telegraf_helper.gui import main_window as mw

    monkeypatch.setattr(mw, "get_adapter", lambda env, session=None: MockVCFOpsIntegration(env=env, connected=True))
    window = MainWindow(state_store=StateStore(state_file=tmp_path / "state.json"))
    window._create_executor = lambda target: MockExecutor(connected=True, telegraf_installed=True)
    return window


def _select_vm(window, name):
    row = next(r for r in range(window.vm_table.rowCount()) if window.vm_table.item(r, 0).text() == name)
    window.vm_table.selectRow(row)


def _unlock_all(window):
    window._test_vcf_connection()
    _select_vm(window, "oraclesrv01")
    window.ep_pass_input.setText("secret")
    window._detect_endpoint()


def test_main_window_initialization(qapp, tmp_path):
    """MainWindow starts with six steps, push-only, and only Step 1 unlocked."""
    window = MainWindow(state_store=StateStore(state_file=tmp_path / "state.json"))
    assert window.windowTitle() == "VCF Operations Open Telegraf Helper"
    labels = [lbl.text() for lbl in window.findChildren(QLabel)]
    assert f"VCF Operations Open Telegraf Helper v{__version__}" in labels
    assert window.step_list.count() == 6
    assert window.page_stack.count() == 6
    assert window.current_theme == "dark"
    assert not hasattr(window, "deployment_mode_combo")
    assert window._max_unlocked_step() == window.STEP_CONNECT


def test_main_window_step_gating(qapp, tmp_path, monkeypatch):
    """Each step stays locked until the one before it has what it needs."""
    window = _mock_window(qapp, tmp_path, monkeypatch)

    window.step_list.setCurrentRow(1)
    assert window.page_stack.currentIndex() == 0
    assert window._next_buttons[0].isEnabled() is False

    window._test_vcf_connection()
    assert window._max_unlocked_step() == window.STEP_SELECT_VM
    window.step_list.setCurrentRow(2)
    assert window.page_stack.currentIndex() == 0

    _select_vm(window, "oraclesrv01")
    assert window._max_unlocked_step() == window.STEP_TARGET
    window.step_list.setCurrentRow(3)
    assert window.page_stack.currentIndex() == 0

    window.ep_pass_input.setText("secret")
    window._detect_endpoint()
    assert window._max_unlocked_step() == window.STEP_EXECUTE

    # Changing how we reach the endpoint re-locks everything after Step 3
    window.ep_pass_input.setText("other")
    assert window._max_unlocked_step() == window.STEP_TARGET
    window._detect_endpoint()

    # No monitoring inputs locks Review and Execute
    for *_, chk in window.catalog_items:
        chk.setChecked(False)
    assert window._max_unlocked_step() == window.STEP_MONITORING
    assert "monitoring input" in window._next_buttons[window.STEP_MONITORING].toolTip()
    window._apply_baseline_preset()

    window.step_list.setCurrentRow(window.STEP_REVIEW)
    assert window.page_stack.currentIndex() == window.STEP_REVIEW
    assert "[[inputs.cpu]]" in window.preview_system_box.toPlainText()
    assert "[[outputs.http]]" in window.preview_output_box.toPlainText()

    # Editing the connection settings re-locks everything after Step 1
    window.vcf_url_input.setText("https://other.example")
    assert window._max_unlocked_step() == window.STEP_CONNECT


def test_main_window_ca_bundle_follows_tls_verification(qapp, tmp_path):
    """The enterprise CA bundle field is only shown when certificate verification is on."""
    window = MainWindow(state_store=StateStore(state_file=tmp_path / "state.json"))
    window.vcf_ssl_check.setChecked(True)
    assert window.vcf_ca_widget.isHidden() is False
    window.vcf_ssl_check.setChecked(False)
    assert window.vcf_ca_widget.isHidden() is True


def test_main_window_endpoint_auth_and_advanced_options_visibility(qapp, tmp_path):
    """Target page toggles auth modes, WinRM SSL, and hides the port by default."""
    window = MainWindow(state_store=StateStore(state_file=tmp_path / "state.json"))

    assert window.ep_os_combo.currentText() == "Linux"
    assert window._get_endpoint_target().connection_method == ConnectionMethod.SSH
    assert window.ep_port_input.text() == "22"
    assert window.ep_port_input.isHidden() is True
    assert window.ep_winrm_ssl_check.isHidden() is True
    assert window.ep_auth_radio_pass.isChecked() is True
    assert window.ep_pass_label.text() == "Password:"

    window.ep_auth_radio_key.setChecked(True)
    assert window.ep_pass_label.text() == "SSH Key Path:"

    window.ep_os_combo.setCurrentText("Windows")
    assert window._get_endpoint_target().connection_method == ConnectionMethod.WINRM
    assert window.ep_port_input.text() == "5985"
    assert window.ep_auth_radio_widget.isHidden() is True
    assert window.ep_winrm_ssl_check.isHidden() is False

    window.ep_winrm_ssl_check.setChecked(True)
    assert window.ep_port_input.text() == "5986"
    assert window._get_endpoint_target().winrm_use_ssl is True
    window.ep_winrm_ssl_check.setChecked(False)
    assert window.ep_port_input.text() == "5985"
    assert window._get_endpoint_target().winrm_use_ssl is False

    window.ep_advanced_check.setChecked(True)
    assert window.ep_port_input.isHidden() is False

    # A custom port survives an OS switch while advanced options are open
    window.ep_port_input.setText("2222")
    window.ep_os_combo.setCurrentText("Linux")
    assert window.ep_port_input.text() == "2222"


def test_main_window_cli_command_generation_and_copy(qapp, tmp_path, monkeypatch):
    """Step 6 shows a push-only CLI command matching the selections, and copies it."""
    window = _mock_window(qapp, tmp_path, monkeypatch)
    _unlock_all(window)
    window._update_cli_command()

    cmd = window.cli_command_box.toPlainText()
    assert "vcf-telegraf-helper run" in cmd
    assert "--target-host 172.18.2.14" in cmd
    assert "--connection ssh" in cmd
    assert "--collector 10.10.10.50" in cmd
    assert "--collector-group 'Simulated CP Group'" in cmd
    assert "--vm-id vm-1004" in cmd
    assert "--install-telegraf" in cmd
    assert "--mode" not in cmd and "--output-dir" not in cmd
    assert "--dry-run" not in cmd

    window.dry_run_check.setChecked(True)
    cmd_dry = window.cli_command_box.toPlainText()
    assert "--dry-run" in cmd_dry

    window._copy_cli_command()
    clipboard = QApplication.clipboard()
    if clipboard:
        assert clipboard.text() == cmd_dry

    window.ep_port_input.setText("2222")
    window._update_cli_command()
    assert "--port 2222" in window.cli_command_box.toPlainText()

    window.vcf_auth_type_combo.setCurrentText("API Token / Key")
    window._update_cli_command()
    assert '--vcf-token "<token>"' in window.cli_command_box.toPlainText()


def test_main_window_vm_inventory_filter_and_binding(qapp, tmp_path, monkeypatch):
    """Inventory hides powered-off VMs by default, filters by agent state, and binds the selection."""
    window = _mock_window(qapp, tmp_path, monkeypatch)
    window._test_vcf_connection()

    assert window.vm_table.rowCount() == 6
    assert window.vm_table.columnCount() == 6
    headers = [window.vm_table.horizontalHeaderItem(c).text() for c in range(6)]
    assert "Collector Group" not in headers and "Power" in headers
    assert "5 / 6 VMs" in window.vm_count_label.text()

    window.vm_show_off_check.setChecked(True)
    assert "6 / 6 VMs" in window.vm_count_label.text()
    window.vm_show_off_check.setChecked(False)

    window.vm_status_filter.setCurrentText("Reporting")
    assert "1 / 6 VMs" in window.vm_count_label.text()
    window.vm_status_filter.setCurrentText("All Agent States")

    window.vm_os_filter.setCurrentText("Windows")
    assert "2 / 6 VMs" in window.vm_count_label.text()
    window.vm_os_filter.setCurrentText("All OS Families")

    # Selecting a Windows VM binds it and sets OS and address from VCF Operations
    _select_vm(window, "mssqldemo2")
    assert window.bound_vm.name == "mssqldemo2"
    assert window.ep_os_combo.currentText() == "Windows"
    assert window.ep_host_input.text() == "172.16.3.80"
    target = window._get_endpoint_target()
    assert target.vm_mor == "vm-1042"
    assert target.vc_id == "423b-81f0-91a2-0002"

    # Editing the address keeps the binding: the VM identity comes from Step 2
    window.ep_host_input.setText("mssqldemo2.corp.local")
    assert window._get_endpoint_target().vm_mor == "vm-1042"

    # A VM with an existing agent preselects the collector group it reports through
    _select_vm(window, "webapp01")
    assert window._selected_collector().is_collector_group is True
    assert window._selected_collector().name == "Simulated CP Group"
    window._update_target_summary()
    assert "webapp01" in window.target_summary_label.text()
    assert "10.10.10.51" in window.target_summary_label.text()


def test_main_window_switching_vcf_instance_clears_vm(qapp, tmp_path, monkeypatch):
    """Validating a different VCF Operations URL drops the VM bound against the previous one."""
    window = _mock_window(qapp, tmp_path, monkeypatch)
    _unlock_all(window)
    assert window._max_unlocked_step() == window.STEP_EXECUTE

    window.vcf_url_input.setText("https://other-ops.example")
    window._test_vcf_connection()
    assert window.bound_vm is None
    assert window._get_endpoint_target().vm_mor is None
    assert window._max_unlocked_step() == window.STEP_SELECT_VM


def test_main_window_revalidation_keeps_collector_choice(qapp, tmp_path, monkeypatch):
    """Re-validating the same instance must not silently move the chosen collector."""
    window = _mock_window(qapp, tmp_path, monkeypatch)
    _unlock_all(window)
    window.ep_collector_combo.setCurrentIndex(2)
    chosen = window._selected_collector().address

    window._test_vcf_connection()
    assert window._selected_collector().address == chosen
    assert window.bound_vm is not None and window.bound_vm.name == "oraclesrv01"


def test_main_window_refresh_drops_vm_missing_from_inventory(qapp, tmp_path, monkeypatch):
    """If the bound VM disappears from a refreshed inventory, the binding and later steps are cleared."""
    from vcf_ops_telegraf_helper.adapters.mock import MockVCFOpsIntegration

    window = _mock_window(qapp, tmp_path, monkeypatch)
    _unlock_all(window)
    remaining = [vm for vm in MockVCFOpsIntegration(env=window._get_vcf_env()).list_virtual_machines() if vm.name != "oraclesrv01"]
    adapter = MockVCFOpsIntegration(env=window._get_vcf_env())
    adapter._vms = remaining
    window._fetch_vcf_inventory(adapter)
    assert window.bound_vm is None
    assert window._max_unlocked_step() == window.STEP_SELECT_VM


def test_main_window_inventory_error_is_shown_not_empty(qapp, tmp_path, monkeypatch):
    """An inventory failure is reported in the UI rather than shown as zero VMs."""
    window = _mock_window(qapp, tmp_path, monkeypatch)
    window._test_vcf_connection()

    class Broken:
        inventory_warning = None

        def list_virtual_machines(self, strict=False):
            raise RuntimeError("inventory query failed on page 1 with HTTP 503")

    window._fetch_vcf_inventory(Broken())
    assert "Query error" in window.vm_count_label.text()
    assert "503" in window.vm_count_label.text()


def test_main_window_rejected_credentials_do_not_unlock(qapp, tmp_path, monkeypatch):
    """A reachable instance that rejects the credentials leaves Step 2 locked."""
    from vcf_ops_telegraf_helper.adapters.mock import MockVCFOpsIntegration
    from vcf_ops_telegraf_helper.gui import main_window as mw

    class Rejecting(MockVCFOpsIntegration):
        def verify_credentials(self):
            raise RuntimeError("VCF Operations rejected the credentials (HTTP 401)")

    monkeypatch.setattr(mw, "get_adapter", lambda env, session=None: Rejecting(env=env, connected=True))
    window = MainWindow(state_store=StateStore(state_file=tmp_path / "state.json"))
    window._test_vcf_connection()
    assert window._vcf_validated is False
    assert "rejected the credentials" in window.vcf_status_label.text()
    assert window._max_unlocked_step() == window.STEP_CONNECT


def test_main_window_execute_button_in_nav_bar(qapp, tmp_path):
    """Execute sits at the right end of the Step 6 navigation bar, like every other advance button."""
    window = MainWindow(state_store=StateStore(state_file=tmp_path / "state.json"))
    execute_bar = window.execute_btn.parentWidget()
    next_bar = window._next_buttons[window.STEP_REVIEW].parentWidget()
    assert execute_bar.property("class") == next_bar.property("class") == "lattice-card"
    layout = execute_bar.layout()
    assert layout.itemAt(layout.count() - 1).widget() is window.execute_btn
    assert window.execute_btn.property("class") == "primary"


def test_main_window_nav_labels_show_ampersand(qapp, tmp_path):
    """Step names containing '&' render literally on navigation buttons, not as a shortcut underline."""
    window = MainWindow(state_store=StateStore(state_file=tmp_path / "state.json"))
    assert window._next_buttons[window.STEP_MONITORING].text() == "Next: Review && Preview ->"
    assert window._next_buttons[window.STEP_REVIEW].text() == "Next: Execute && Verify ->"


def test_gui_ampersand_texts_escaped(qapp, tmp_path):
    """Buttons and checkboxes that contain '&' escape it so Qt shows it instead of a shortcut underline."""
    from PySide6.QtWidgets import QAbstractButton, QDialogButtonBox

    from vcf_ops_telegraf_helper.gui.discovery_dialogs import DatabaseConnectDialog

    window = MainWindow(state_store=StateStore(state_file=tmp_path / "state.json"))
    dlg = DatabaseConnectDialog(None, "Microsoft SQL Server", default_port=1433)
    buttons = window.findChildren(QAbstractButton) + dlg.findChildren(QAbstractButton)
    texts = [b.text() for b in buttons if "&" in b.text()]
    assert texts, "expected some buttons with ampersands"
    for text in texts:
        assert "&" not in text.replace("&&", ""), f"unescaped ampersand in {text!r}"
    assert window.sys_check.text() == "Enable System Load && Uptime"
    assert dlg.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok).text() == "Connect && Discover"


def test_main_window_execute_recovers_when_setup_fails(qapp, tmp_path, monkeypatch):
    """If the run fails before the worker starts, Execute is usable again and the error is shown (#33)."""
    from vcf_ops_telegraf_helper.gui import main_window as mw

    window = MainWindow(state_store=StateStore(state_file=tmp_path / "state.json"))
    monkeypatch.setattr(mw.QMessageBox, "critical", lambda *a, **k: None)
    monkeypatch.setattr(window, "_create_executor", lambda target: (_ for _ in ()).throw(RuntimeError("no route to host")))
    window._run_workflow()
    assert window.execute_btn.isEnabled() is True
    assert "no route to host" in window.stage_list_box.toPlainText()


def test_main_window_pages_fit_at_minimum_size(qapp, tmp_path):
    """At the smallest allowed window size no step, and no plugin card, needs a sideways scroll."""
    from PySide6.QtWidgets import QScrollArea

    window = MainWindow(state_store=StateStore(state_file=tmp_path / "state.json"))
    window.show()
    window.resize(window.minimumSize())
    overflowing = []
    for page in range(window.page_stack.count()):
        window.page_stack.setCurrentIndex(page)
        rows = range(window.plugin_catalog_list.count()) if page == window.STEP_MONITORING else [None]
        for row in rows:
            if row is not None:
                window.plugin_catalog_list.setCurrentRow(row)
            qapp.processEvents()
            scroll = window.page_stack.currentWidget().findChild(QScrollArea)
            if scroll.horizontalScrollBar().isVisible():
                overflowing.append((page + 1, row))
    assert window.plugin_catalog_list.horizontalScrollBar().isVisible() is False
    assert overflowing == []


def test_main_window_login_source(qapp, tmp_path, monkeypatch):
    """The login source is offered from the instance, sent for username auth, and kept in the CLI command."""
    from vcf_ops_telegraf_helper.adapters.mock import MockVCFOpsIntegration
    from vcf_ops_telegraf_helper.gui import main_window as mw

    class WithSources(MockVCFOpsIntegration):
        def list_auth_sources(self):
            return ["VCF SSO"]

    monkeypatch.setattr(mw, "get_adapter", lambda env, session=None: WithSources(env=env, connected=True))
    window = MainWindow(state_store=StateStore(state_file=tmp_path / "state.json"))
    window.vcf_url_input.setText("https://ops.corp.local")
    window._load_auth_sources(sync=True)
    assert [window.vcf_auth_source_combo.itemText(i) for i in range(window.vcf_auth_source_combo.count())] == ["Local", "VCF SSO"]

    window.vcf_auth_type_combo.setCurrentText("Username & Password")
    assert window.vcf_auth_source_combo.isHidden() is False
    assert window._get_vcf_env().auth_source == "local"
    window.vcf_auth_source_combo.setCurrentText("VCF SSO")
    assert window._get_vcf_env().auth_source == "VCF SSO"
    assert "--vcf-auth-source 'VCF SSO'" in window._build_cli_command()

    # Token auth has no login source
    window.vcf_auth_type_combo.setCurrentText("API Token / Key")
    assert window.vcf_auth_source_combo.isHidden() is True
    assert window._get_vcf_env().auth_source == "local"


def test_main_window_passwords_are_not_trimmed(qapp, tmp_path):
    """Leading or trailing spaces can be part of a real password."""
    window = MainWindow(state_store=StateStore(state_file=tmp_path / "state.json"))
    window.vcf_auth_type_combo.setCurrentText("Username & Password")
    window.vcf_pass_input.setText(" pw with spaces ")
    window.ep_pass_input.setText(" pw2 ")
    assert window._get_vcf_env().password == " pw with spaces "
    assert window._get_endpoint_target().password == " pw2 "


def test_main_window_login_source_async(qapp, tmp_path, monkeypatch):
    """Auth sources load asynchronously off the GUI thread when editing completes or saved state loads."""
    from vcf_ops_telegraf_helper.adapters.mock import MockVCFOpsIntegration
    from vcf_ops_telegraf_helper.gui import main_window as mw

    class WithSources(MockVCFOpsIntegration):
        def list_auth_sources(self):
            return ["Corp Active Directory"]

    monkeypatch.setattr(mw, "get_adapter", lambda env, session=None: WithSources(env=env, connected=True))
    window = MainWindow(state_store=StateStore(state_file=tmp_path / "state.json"))
    window.vcf_url_input.setText("https://ops.corp.local")
    window._load_auth_sources()  # runs async
    assert window.auth_sources_thread is not None
    window.auth_sources_thread.wait(5000)
    qapp.processEvents()
    assert [window.vcf_auth_source_combo.itemText(i) for i in range(window.vcf_auth_source_combo.count())] == [
        "Local",
        "Corp Active Directory",
    ]


