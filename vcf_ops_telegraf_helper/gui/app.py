"""Application launcher for the native PySide6 GUI."""

from __future__ import annotations

import os
import sys


def run_gui(theme: str = "dark") -> int:
    """Launch the native Lattice-styled PySide6 GUI application."""
    # Check for PySide6
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print(
            "Error: PySide6 is required for the native GUI.\n"
            "Install it via: pip install 'vcf-ops-telegraf-helper[gui]'\n"
            "Or: pip install 'PySide6>=6.7.2'",
            file=sys.stderr,
        )
        return 1

    # Check for display server on POSIX systems
    if sys.platform != "win32" and not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        if not os.environ.get("QT_QPA_PLATFORM"):
            print(
                "Error: No display server detected ($DISPLAY or $WAYLAND_DISPLAY is not set).\n"
                "To run the native GUI over SSH, enable X11 forwarding (ssh -X) or run directly "
                "on a workstation with an active display.\n"
                "For terminal-based interactive usage, use: vcf-telegraf-helper wizard",
                file=sys.stderr,
            )
            return 1

    from vcf_ops_telegraf_helper.gui.main_window import MainWindow

    # Re-use or instantiate QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    app.setApplicationName("VCF Operations Open Telegraf Helper")

    window = MainWindow()
    if theme in ("light", "dark"):
        window.current_theme = theme
        window._apply_theme()
    window.show()

    return app.exec()
