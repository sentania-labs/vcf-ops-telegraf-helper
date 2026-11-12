"""Check native ownership and process exit, after Qt has reported success."""

import os
import subprocess
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")


def _owned_by_python(widget):
    """Widgets under `widget` that Python, not Qt, would delete at exit although Qt is their parent."""
    import gc

    import shiboken6
    from PySide6.QtWidgets import QWidget

    # findChildren() can repair Shiboken's ownership as a side effect of
    # wrapping native pointers, masking the bug. Inspect the wrappers Python holds.
    gc.collect()
    found = []
    for candidate in gc.get_objects():
        if not isinstance(candidate, QWidget) or candidate is widget:
            continue
        if not shiboken6.isValid(candidate) or not shiboken6.ownedByPython(candidate):
            continue
        parent = candidate.parentWidget()
        while parent is not None and parent is not widget:
            parent = parent.parentWidget()
        if parent is widget:
            found.append(f"{type(candidate).__name__}:{candidate.objectName()}")
    return found


@pytest.mark.parametrize("screen", ["connect", "machines", "machine", "activity", "settings", "dialog"])
def test_window_children_are_owned_by_qt(screen, tmp_path):
    """A native parent must own its children before PySide's exit sweep."""
    import shiboken6
    from PySide6.QtWidgets import QApplication

    import gui_standins as standins
    from gui_driver import Driver
    from vcf_ops_telegraf_helper.gui.widgets import Dialog

    app = QApplication.instance() or QApplication([])
    window, _world = standins.make_window(tmp_path)
    dialog = None
    try:
        window.show()
        driver = Driver(window, tmp_path / "shots")
        driver.settle()
        if screen != "connect":
            driver.type("connect-address", "ops.example.com")
            driver.type("connect-user", "admin")
            driver.type("connect-password", "pw")
            driver.click("connect-go")
        if screen == "machine":
            driver.click("open-web-prod-01")
        elif screen == "activity":
            driver.click("nav-activity")
        elif screen == "settings":
            driver.click("nav-settings")
        elif screen == "dialog":
            dialog = Dialog(window, "A question")
            dialog.add_button("Close", dialog.accept, "primary")
            dialog.show()
        app.processEvents()
        assert _owned_by_python(window) == []
        if dialog is not None:
            assert _owned_by_python(dialog) == []
    finally:
        window.close()
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
