"""Tests for CLI entry points and standalone script execution."""

from __future__ import annotations

import subprocess
import sys
from unittest.mock import patch
from click.testing import CliRunner

from vcf_ops_telegraf_helper.cli.main import _is_windows_double_click, cli


def test_cli_help_runner():
    """Verify click CLI runner outputs help text."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Usage:" in result.output
    assert "render" in result.output
    assert "wizard" in result.output


def test_cli_terminal_no_args_displays_help():
    """Verify standard terminal invocation without subcommands shows banner and help."""
    runner = CliRunner()
    result = runner.invoke(cli, [])
    assert result.exit_code == 0
    assert "VCF Operations Open Telegraf Helper" in result.output
    assert "Usage:" in result.output


def test_is_windows_double_click_non_win32(monkeypatch):
    """Verify non-win32 platforms never report as Windows double click."""
    monkeypatch.setattr(sys, "platform", "linux")
    assert not _is_windows_double_click()


def test_cli_windows_double_click_launches_gui(monkeypatch):
    """Verify double-clicking on Windows invokes run_gui()."""
    monkeypatch.setattr("vcf_ops_telegraf_helper.cli.main._is_windows_double_click", lambda: True)
    with patch("vcf_ops_telegraf_helper.gui.app.run_gui", return_value=0) as mock_gui:
        runner = CliRunner()
        result = runner.invoke(cli, [])
        assert result.exit_code == 0
        mock_gui.assert_called_once()


def test_cli_windows_double_click_handles_gui_error(monkeypatch):
    """Verify GUI startup error during double-click prints diagnostic message."""
    monkeypatch.setattr("vcf_ops_telegraf_helper.cli.main._is_windows_double_click", lambda: True)
    with patch("vcf_ops_telegraf_helper.gui.app.run_gui", side_effect=RuntimeError("display error")):
        with patch("builtins.input", return_value=""):
            runner = CliRunner()
            result = runner.invoke(cli, [])
            assert result.exit_code == 1
            assert "Failed to launch GUI: display error" in result.output


def test_package_main_execution():
    """Verify invoking python -m vcf_ops_telegraf_helper executes without warnings or errors."""
    cmd = [sys.executable, "-Werror", "-m", "vcf_ops_telegraf_helper", "--help"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0
    assert "Usage:" in proc.stdout
    assert len(proc.stdout.strip()) > 0


def test_main_script_direct_execution():
    """Verify invoking main.py directly executes the CLI rather than exiting silently."""
    cmd = [sys.executable, "vcf_ops_telegraf_helper/cli/main.py", "--help"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0
    assert "Usage:" in proc.stdout
    assert len(proc.stdout.strip()) > 0


def test_cli_run_with_install_and_mock():
    """Verify run subcommand with --mock-vcf, --install-telegraf, and mock connection executes cleanly."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "run",
            "--vcf-url",
            "https://vcf.local",
            "--mock-vcf",
            "--collector",
            "10.10.10.50",
            "--target-host",
            "10.10.10.101",
            "--connection",
            "mock",
            "--install-telegraf",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0
    assert "Operational Verification Checklist" in result.output

