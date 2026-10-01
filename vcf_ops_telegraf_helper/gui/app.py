"""Application launcher for the native PySide6 GUI."""

from __future__ import annotations

import os
import sys


def hide_console_window() -> None:
    """Hide and detach background Windows console window."""
    if sys.platform == "win32":
        try:
            import ctypes

            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
            ctypes.windll.kernel32.FreeConsole()
        except Exception:
            pass


def run_gui(theme: str = "dark") -> int:
    """Launch the native Lattice-styled PySide6 GUI application."""
    hide_console_window()

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

    # Pre-flight check for libxcb-cursor on Linux to avoid C++ qFatal abort
    if sys.platform.startswith("linux") and (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        import ctypes.util

        if not ctypes.util.find_library("xcb-cursor"):
            print(
                "Error: Missing system library 'libxcb-cursor0' required by Qt 6.\n"
                "Install it on Debian/Ubuntu with:\n"
                "  sudo apt install libxcb-cursor0\n"
                "Or on RHEL/CentOS with:\n"
                "  sudo dnf install xcb-util-cursor",
                file=sys.stderr,
            )
            return 1

    from vcf_ops_telegraf_helper.gui.main_window import MainWindow

    # Re-use or instantiate QApplication
    try:
        app = QApplication.instance()
        if app is None:
            app = QApplication(sys.argv)
    except Exception as exc:
        print(
            f"Error initializing Qt GUI: {exc}\n"
            "On Debian/Ubuntu systems, Qt 6 requires libxcb-cursor0.\n"
            "You can install it with: sudo apt install libxcb-cursor0",
            file=sys.stderr,
        )
        return 1

    app.setApplicationName("VCF Operations Open Telegraf Helper")
    hide_console_window()


    try:
        window = MainWindow()
        if theme in ("light", "dark"):
            window.current_theme = theme
            window._apply_theme()
        window.show()
        return app.exec()
    except Exception as exc:
        print(
            f"Error launching GUI window: {exc}\n"
            "If running on Linux, verify that required XCB packages (like libxcb-cursor0) are installed.",
            file=sys.stderr,
        )
        return 1
