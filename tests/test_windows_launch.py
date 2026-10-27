"""Regression checks for Windows desktop launch and shared terminals."""
import ctypes
import pytest
from types import SimpleNamespace
from unittest.mock import Mock

from scripts import build_binary
from vcf_ops_telegraf_helper.gui import app


def test_windows_build_opens_no_console_and_reattaches_for_cli(monkeypatch):
    monkeypatch.setattr(build_binary, 'windows_icon', lambda: 'app.ico')
    run = Mock()
    monkeypatch.setattr(build_binary.subprocess, 'run', run)
    build_binary.build('windows')
    args = run.call_args.args[0]
    assert '--hide-console' not in args  # This can minimize unrelated Terminal tabs.
    assert '--windowed' in args  # Double-click opens no console; the CLI attaches to the parent's.


@pytest.mark.parametrize("frozen,count", [(False, 2), (True, 3)])
def test_hide_console_preserves_shared_terminal(monkeypatch, frozen, count):
    kernel = SimpleNamespace(GetConsoleWindow=Mock(return_value=123),
                             GetConsoleProcessList=Mock(return_value=count), FreeConsole=Mock())
    user = SimpleNamespace(ShowWindow=Mock(), IsWindowVisible=Mock(return_value=True))
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
    user = SimpleNamespace(ShowWindow=Mock(), IsWindowVisible=Mock(return_value=True))
    monkeypatch.setattr(app.sys, 'platform', 'win32')
    monkeypatch.setattr(ctypes, 'windll', SimpleNamespace(kernel32=kernel, user32=user), raising=False)
    monkeypatch.setattr(app.sys, "frozen", True, raising=False)
    app.hide_console_window()
    assert kernel.GetConsoleWindow.restype == ctypes.c_void_p
    assert user.ShowWindow.argtypes[0] == ctypes.c_void_p
    user.ShowWindow.assert_called_once_with(hwnd, 0)
    kernel.FreeConsole.assert_not_called()


@pytest.mark.parametrize("frozen,count,expected", [(False, 2, False), (False, 1, True),
                                                 (True, 2, True), (True, 3, True), (True, 0, True)])
def test_double_click_distinguishes_existing_terminal(monkeypatch, frozen, count, expected):
    from vcf_ops_telegraf_helper.cli.main import _is_windows_double_click
    from vcf_ops_telegraf_helper.cli import main as cli_main
    kernel = SimpleNamespace(GetConsoleProcessList=Mock(return_value=count))
    monkeypatch.setattr(app.sys, 'platform', 'win32')
    monkeypatch.setattr(app.sys, 'frozen', frozen, raising=False)
    monkeypatch.setattr(cli_main, '_parent_console_attached', False)
    monkeypatch.setattr(app.sys.stdout, 'isatty', lambda: True)
    monkeypatch.setattr(ctypes, 'windll', SimpleNamespace(kernel32=kernel), raising=False)
    assert _is_windows_double_click() is expected


def test_hide_skips_windows_terminal_message_only_handle(monkeypatch):
    kernel = SimpleNamespace(GetConsoleWindow=Mock(return_value=123),
                             GetConsoleProcessList=Mock(return_value=2))
    user = SimpleNamespace(ShowWindow=Mock(), IsWindowVisible=Mock(return_value=False))
    monkeypatch.setattr(app.sys, 'platform', 'win32')
    monkeypatch.setattr(app.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(ctypes, 'windll', SimpleNamespace(kernel32=kernel, user32=user), raising=False)
    app.hide_console_window()
    user.ShowWindow.assert_not_called()


def test_macos_and_linux_builds_keep_their_options(monkeypatch):
    monkeypatch.setattr(build_binary, 'mac_icon', lambda: 'app.icns')
    run = Mock()
    monkeypatch.setattr(build_binary.subprocess, 'run', run)
    build_binary.build('linux')
    args = run.call_args.args[0]
    assert '--onefile' in args and '--windowed' not in args


def test_parent_console_attached_for_frozen_windows_cli(monkeypatch):
    from vcf_ops_telegraf_helper.cli import console
    kernel = SimpleNamespace(AttachConsole=Mock(return_value=1))
    monkeypatch.setattr(console.sys, 'platform', 'win32')
    monkeypatch.setattr(console.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(ctypes, 'windll', SimpleNamespace(kernel32=kernel), raising=False)
    monkeypatch.setattr(console.sys, 'stdout', object())
    monkeypatch.setattr(console.sys, 'stderr', object())
    monkeypatch.setattr(console.sys, 'stdin', object())
    assert console.attach_parent_console() is True
    kernel.AttachConsole.assert_called_once_with(-1)
    kernel.AttachConsole.return_value = 0
    assert console.attach_parent_console() is False


def test_unverified_certificate_logged_once_per_host(caplog):
    import warnings
    from vcf_ops_telegraf_helper.security import tls
    tls._unverified_hosts.clear()
    with caplog.at_level('WARNING'):
        tls.note_unverified_host('https://ops.example.com/suite-api')
        tls.note_unverified_host('https://ops.example.com/ui')
    assert [r.getMessage() for r in caplog.records if r.name.endswith('tls')] == [
        'Certificate verification is disabled for ops.example.com']
    import urllib3
    with warnings.catch_warnings(record=True) as caught:
        warnings.warn('x', urllib3.exceptions.InsecureRequestWarning)
    assert not caught
