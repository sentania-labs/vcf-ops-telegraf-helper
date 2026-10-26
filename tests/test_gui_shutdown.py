"""Check native ownership and process exit, after Qt has reported success."""

import os
import subprocess
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")


@pytest.mark.parametrize("kind", ["window", "services", "perfmon", "databases",
                                  "mssql", "postgresql", "mysql"])
def test_window_children_are_owned_by_qt(kind, tmp_path):
    """A native parent must own its children before PySide's exit sweep."""
    import shiboken6
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QApplication
    from vcf_ops_telegraf_helper.gui.main_window import MainWindow
    from vcf_ops_telegraf_helper.gui.discovery_dialogs import (
        DatabaseConnectDialog, DatabaseDiscoveryDialog,
        PerfmonDiscoveryDialog, ServicesDiscoveryDialog,
    )
    from vcf_ops_telegraf_helper.storage.state import StateStore

    app = QApplication.instance() or QApplication([])
    if kind == "window":
        window = MainWindow(StateStore(state_file=tmp_path / "state.json"))
    elif kind == "services":
        window = ServicesDiscoveryDialog(None, [])
    elif kind == "perfmon":
        window = PerfmonDiscoveryDialog(None, [])
    elif kind == "databases":
        window = DatabaseDiscoveryDialog(None, "mssql", [])
    else:
        window = DatabaseConnectDialog(None, kind, 1234)
    try:
        window.show()
        app.processEvents()
        # findChildren() can repair Shiboken's ownership as a side effect of
        # wrapping native pointers, masking the bug. Inspect held wrappers.
        python_owned = [
            name
            for name, child in vars(window).items()
            if isinstance(child, QObject) and shiboken6.ownedByPython(child)
        ]
        assert python_owned == []
    finally:
        shiboken6.delete(window)


@pytest.mark.parametrize("seed", ["1", "4", "18"])
def test_gui_smoke_exits_cleanly(seed, tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "vcf_ops_telegraf_helper", "gui-smoke"],
        cwd=tmp_path,
        env=dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONHASHSEED=seed),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "GUI smoke passed" in result.stdout


@pytest.mark.parametrize("pending_update", [False, True])
def test_gui_event_loop_renders_and_exits_cleanly(pending_update, tmp_path):
    report = tmp_path / "window.png"
    result = subprocess.run(
        [sys.executable, "-c", """
import sys
from threading import Event, enumerate
from vcf_ops_telegraf_helper import updates
from vcf_ops_telegraf_helper.gui.app import run_gui
pending = sys.argv[1] == "True"
updates.check_release = lambda *args: Event().wait(60) if pending else None
result = run_gui()
assert any(t.name == "release-check" and t.is_alive() for t in enumerate()) == pending
raise SystemExit(result)
""", str(pending_update)],
        cwd=tmp_path,
        env=dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONHASHSEED="1",
                 VCF_HELPER_GUI_SMOKE_REPORT=str(report)),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert report.stat().st_size > 100
