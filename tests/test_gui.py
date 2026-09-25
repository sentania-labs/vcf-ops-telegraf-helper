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
    from PySide6.QtWidgets import QApplication
    from vcf_ops_telegraf_helper.gui.main_window import MainWindow
    from vcf_ops_telegraf_helper.gui.theme import build_stylesheet
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

    light_qss = build_stylesheet("light")
    assert "#f6f7f9" in light_qss  # bg
    assert "#ffffff" in light_qss  # surface
    assert "#d9dee5" in light_qss  # line


def test_main_window_initialization(qapp, tmp_path):
    """Verify MainWindow initializes with all 5 steps and defaults."""
    state_file = tmp_path / "state.json"
    store = StateStore(state_file=state_file)

    window = MainWindow(state_store=store)
    assert window.windowTitle() == "VCF Operations Open Telegraf Helper"
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
    assert window.ep_method_combo.currentText() == "WinRM (Windows Remote)"
    assert window.ep_port_input.text() == "5985"
    assert window.ep_auto_install_check.isChecked()

    from unittest.mock import MagicMock
    mock_exec = MagicMock()
    mock_exec.test_connection.return_value = True
    window._create_executor = lambda target: mock_exec

    window._detect_endpoint()
    assert "Connected & Discovered (Windows)" in window.ep_status_label.text()
    assert "C:\\telegraf\\telegraf.d" in window.ep_details_box.toPlainText()
    assert "telegraf-utils.ps1" in window.ep_details_box.toPlainText()


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
    """Verify plugin tabs, selection, and preview updates."""
    state_file = tmp_path / "state.json"
    store = StateStore(state_file=state_file)
    window = MainWindow(state_store=store)

    assert window.plugin_tabs.count() == 4
    assert window.plugin_tabs.tabText(0) == "Host OS Telemetry"
    assert window.plugin_tabs.tabText(1) == "Windows Metrics"
    assert window.plugin_tabs.tabText(2) == "Applications & Workloads"
    assert window.plugin_tabs.tabText(3) == "Custom TOML"

    window.nginx_check.setChecked(True)
    window.nginx_url_input.setText("http://127.0.0.1/status")
    window.custom_toml_input.setPlainText("[[inputs.ping]]\n  urls = ['8.8.8.8']")

    mon = window._get_monitoring_config()
    assert mon.nginx.enabled is True
    assert mon.nginx.urls == ["http://127.0.0.1/status"]
    assert "[[inputs.ping]]" in mon.custom_toml

    window._update_preview()
    preview_txt = window.preview_system_box.toPlainText()
    assert "[[inputs.nginx]]" in preview_txt
    assert "[[inputs.ping]]" in preview_txt
    assert "Auto-Install Telegraf: YES" in window.review_summary_box.toPlainText()


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

