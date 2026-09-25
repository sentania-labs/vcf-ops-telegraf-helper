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


def test_main_window_endpoint_detection_mock(qapp, tmp_path):
    """Verify endpoint detection with Mock executor updates UI state."""
    state_file = tmp_path / "state.json"
    store = StateStore(state_file=state_file)
    window = MainWindow(state_store=store)

    window.ep_host_input.setText("mock-host.sentania.local")
    window.ep_method_combo.setCurrentIndex(1)  # Mock (Simulated)

    window._detect_endpoint()
    assert "Connected & Discovered" in window.ep_status_label.text()
    assert "Telegraf Installed: YES" in window.ep_details_box.toPlainText()


def test_main_window_vcf_connection_mock(qapp, tmp_path):
    """Verify VCF connection test with mock adapter updates UI state."""
    state_file = tmp_path / "state.json"
    store = StateStore(state_file=state_file)
    window = MainWindow(state_store=store)

    window.mock_vcf_check.setChecked(True)
    window._test_vcf_connection()

    assert "PASS" in window.vcf_status_label.text()
