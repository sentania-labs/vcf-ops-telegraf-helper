"""Reattach the windowed Windows binary to the terminal that launched it."""

from __future__ import annotations

import os
import sys


def attach_parent_console() -> bool:
    """Give the frozen Windows GUI-subsystem binary the parent terminal's console.

    The binary is built without a console so a double-click opens no window; run from
    cmd or PowerShell it attaches to that terminal so CLI output still appears.
    Returns True when a parent console was attached.
    """
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return False
    try:
        import ctypes

        if not ctypes.windll.kernel32.AttachConsole(-1):  # ATTACH_PARENT_PROCESS
            return False
        if sys.stdout is None:
            sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
        if sys.stderr is None:
            sys.stderr = open("CONOUT$", "w", encoding="utf-8", buffering=1)
        if sys.stdin is None:
            sys.stdin = open("CONIN$", "r", encoding="utf-8")
        os.environ.setdefault("PYTHONIOENCODING", "utf-8")
        return True
    except Exception:
        return False
