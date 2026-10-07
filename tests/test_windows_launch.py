"""Regression checks for Windows desktop launch and shared terminals."""
import ctypes
import pytest
from types import SimpleNamespace
from unittest.mock import Mock

from scripts import build_binary
from vcf_ops_telegraf_helper.gui import app


def test_windows_build_hides_console_in_bootloader(monkeypatch):
    monkeypatch.setattr(build_binary, 'windows_icon', lambda: 'app.ico')
    run = Mock()
    monkeypatch.setattr(build_binary.subprocess, 'run', run)
    build_binary.build('windows')
    args = run.call_args.args[0]
    assert args[args.index('--hide-console') + 1] == 'hide-early'
    assert '--windowed' not in args  # CLI output and redirects remain available.


@pytest.mark.parametrize("frozen,count", [(False, 2), (True, 3)])
def test_hide_console_preserves_shared_terminal(monkeypatch, frozen, count):
    kernel = SimpleNamespace(GetConsoleWindow=Mock(return_value=123),
                             GetConsoleProcessList=Mock(return_value=count), FreeConsole=Mock())
    user = SimpleNamespace(ShowWindow=Mock())
    monkeypatch.setattr(app.sys, 'platform', 'win32')
    monkeypatch.setattr(ctypes, 'windll', SimpleNamespace(kernel32=kernel, user32=user), raising=False)
    monkeypatch.setattr(app.sys, "frozen", frozen, raising=False)
    app.hide_console_window()
    user.ShowWindow.assert_not_called()
    kernel.FreeConsole.assert_not_called()


def test_hide_owned_console_uses_pointer_sized_handle(monkeypatch):
    hwnd = 0x123456789
    kernel = SimpleNamespace(GetConsoleWindow=Mock(return_value=hwnd),
                             GetConsoleProcessList=Mock(return_value=2), FreeConsole=Mock())
    user = SimpleNamespace(ShowWindow=Mock())
    monkeypatch.setattr(app.sys, 'platform', 'win32')
    monkeypatch.setattr(ctypes, 'windll', SimpleNamespace(kernel32=kernel, user32=user), raising=False)
    monkeypatch.setattr(app.sys, "frozen", True, raising=False)
    app.hide_console_window()
    assert kernel.GetConsoleWindow.restype == ctypes.c_void_p
    assert user.ShowWindow.argtypes[0] == ctypes.c_void_p
    user.ShowWindow.assert_called_once_with(hwnd, 0)
    kernel.FreeConsole.assert_not_called()


@pytest.mark.parametrize("frozen,count,expected", [(False, 2, False), (False, 1, True),
                                                 (True, 2, True), (True, 3, False), (True, 0, False)])
def test_double_click_distinguishes_existing_terminal(monkeypatch, frozen, count, expected):
    from vcf_ops_telegraf_helper.cli.main import _is_windows_double_click
    kernel = SimpleNamespace(GetConsoleProcessList=Mock(return_value=count))
    monkeypatch.setattr(app.sys, 'platform', 'win32')
    monkeypatch.setattr(app.sys, 'frozen', frozen, raising=False)
    monkeypatch.setattr(app.sys.stdout, 'isatty', lambda: True)
    monkeypatch.setattr(ctypes, 'windll', SimpleNamespace(kernel32=kernel), raising=False)
    assert _is_windows_double_click() is expected
