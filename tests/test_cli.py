"""Tests for CLI entry points and standalone script execution."""

from __future__ import annotations

import subprocess
import sys
from unittest.mock import patch
from click.testing import CliRunner

from vcf_ops_telegraf_helper.cli.main import _is_windows_double_click, cli
from vcf_ops_telegraf_helper.executors.mock import MockExecutor
from vcf_ops_telegraf_helper.executors.ssh import SSHExecutor
from vcf_ops_telegraf_helper.executors.winrm import WinRMExecutor


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


def test_cli_run_with_port_and_token():
    """Verify run subcommand accepts --port and --vcf-token."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "run",
            "--vcf-url",
            "https://vcf.local",
            "--vcf-token",
            "mock-token-xyz",
            "--mock-vcf",
            "--collector",
            "10.10.10.50",
            "--target-host",
            "10.10.10.101",
            "--connection",
            "mock",
            "--port",
            "2222",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0
    assert "Operational Verification Checklist" in result.output


def test_cli_opt_in_plugin_baseline_linux():
    """Verify render subcommand defaults to Linux baseline without Windows plugins."""
    runner = CliRunner()
    result = runner.invoke(cli, ["render", "--collector", "10.10.10.50", "--target-host", "web01.local"])
    assert result.exit_code == 0
    assert "[[inputs.cpu]]" in result.output
    assert "[[inputs.mem]]" in result.output
    assert "[[inputs.disk]]" in result.output
    assert "[[inputs.net]]" in result.output
    assert "[[inputs.system]]" in result.output
    assert "[[inputs.swap]]" in result.output
    assert "[[inputs.win_perf_counters]]" not in result.output


def test_cli_opt_in_plugin_baseline_windows():
    """Verify run subcommand with Windows WinRM connection defaults to Windows baseline."""
    with patch("vcf_ops_telegraf_helper.cli.main.WinRMExecutor") as mock_winrm:
        mock_winrm.return_value = MockExecutor(connected=True, telegraf_installed=True)
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
                "172.16.3.80",
                "--connection",
                "winrm",
                "--preview",
            ],
        )
        assert result.exit_code == 0
        assert "[[inputs.win_perf_counters]]" in result.output
        assert "[[inputs.win_services]]" in result.output
        assert "[[inputs.cpu]]" not in result.output
        assert "[[inputs.mem]]" not in result.output
        assert "[[inputs.disk]]" not in result.output
        assert "[[inputs.net]]" not in result.output


def test_cli_opt_in_single_flag_win_perf():
    """Verify passing --win-perf enables only win_perf without requiring --no-cpu flags."""
    runner = CliRunner()
    result = runner.invoke(cli, ["render", "--win-perf"])
    assert result.exit_code == 0
    assert "[[inputs.win_perf_counters]]" in result.output
    assert "[[inputs.cpu]]" not in result.output
    assert "[[inputs.mem]]" not in result.output
    assert "[[inputs.disk]]" not in result.output
    assert "[[inputs.net]]" not in result.output


def test_cli_explicit_hostname_override():
    """Verify --hostname overrides the registered hostname in VCF Operations output."""
    with patch("vcf_ops_telegraf_helper.cli.main.WinRMExecutor") as mock_winrm:
        mock_winrm.return_value = MockExecutor(connected=True, telegraf_installed=True)
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
                "172.16.3.80",
                "--hostname",
                "mssqldemo",
                "--connection",
                "winrm",
                "--preview",
            ],
        )
        assert result.exit_code == 0
        assert 'hostname = "mssqldemo"' in result.output


def test_cli_render_no_swap_preserves_other_linux_baseline():
    """Verify render --no-swap keeps cpu, mem, disk, net, and system enabled."""
    runner = CliRunner()
    result = runner.invoke(cli, ["render", "--no-swap"])
    assert result.exit_code == 0
    assert "[[inputs.cpu]]" in result.output
    assert "[[inputs.mem]]" in result.output
    assert "[[inputs.disk]]" in result.output
    assert "[[inputs.net]]" in result.output
    assert "[[inputs.system]]" in result.output
    assert "[[inputs.swap]]" not in result.output


def test_cli_render_os_windows_baseline():
    """Verify render --os windows emits Windows Perfmon and Services without Linux plugins."""
    runner = CliRunner()
    result = runner.invoke(cli, ["render", "--os", "windows"])
    assert result.exit_code == 0
    assert "[[inputs.win_perf_counters]]" in result.output
    assert "[[inputs.win_services]]" in result.output
    assert "[[inputs.cpu]]" not in result.output
    assert "[[inputs.mem]]" not in result.output
    assert "[[inputs.disk]]" not in result.output
    assert "[[inputs.net]]" not in result.output


def test_cli_workload_flag_supplements_os_baseline():
    """Verify specifying a workload flag like --postgres does not disable the OS baseline."""
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
            "db01.local",
            "--connection",
            "mock",
            "--postgres",
            "host=127.0.0.1 user=postgres sslmode=disable",
            "--preview",
        ],
    )
    assert result.exit_code == 0
    assert "[[inputs.cpu]]" in result.output
    assert "[[inputs.mem]]" in result.output
    assert "[[inputs.postgresql]]" in result.output


def test_wizard_windows_monitoring_flow():
    """Verify interactive wizard with Windows target selects WinPerf baseline and Windows commands."""
    from vcf_ops_telegraf_helper.cli.wizard import run_wizard
    from unittest.mock import MagicMock

    prompt_answers = [
        "https://vcf-ops.local",  # vcf_url
        "admin",                  # vcf_user
        "password",               # vcf_pass
        "10.10.10.50",            # collector_ip
        "172.16.3.80",            # target_host
        "winrm",                  # conn_choice
        "Administrator",          # winrm user
        "password",               # winrm pass
        "1.40.1",                 # telegraf_ver
    ]

    confirm_answers = [
        False,  # verify_ssl
        False,  # winrm_ssl
        True,   # auto_install
        True,   # enable_win_perf
        True,   # enable_win_svc
        False,  # proceed (abort before execution)
    ]

    mock_console = MagicMock()
    with patch("rich.prompt.Prompt.ask", side_effect=prompt_answers), \
         patch("rich.prompt.Confirm.ask", side_effect=confirm_answers), \
         patch("vcf_ops_telegraf_helper.cli.wizard.display_preview") as mock_preview, \
         patch("vcf_ops_telegraf_helper.adapters.mock.MockVCFOpsIntegration.validate_connection", return_value=True), \
         patch("vcf_ops_telegraf_helper.cli.wizard.get_adapter") as mock_get_adapter:

        mock_adapter = MagicMock()
        mock_adapter.validate_connection.return_value = True
        mock_adapter.detect_version.return_value = "9.1.0"
        mock_get_adapter.return_value = mock_adapter

        run_wizard(console=mock_console)

        mock_preview.assert_called_once()
        _, _, _, sys_toml, vcf_toml, planned = mock_preview.call_args[0]
        assert "[[inputs.win_perf_counters]]" in sys_toml
        assert "[[inputs.win_services]]" in sys_toml
        assert "[[inputs.cpu]]" not in sys_toml
        assert "Restart-Service telegraf -Force" in planned


def test_cli_render_all_linux_baseline_cleared():
    """Verify render with all Linux baseline negated produces no core plugins."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["render", "--no-cpu", "--no-mem", "--no-disk", "--no-net", "--no-system", "--no-swap"],
    )
    assert result.exit_code == 0
    assert "[[inputs.cpu]]" not in result.output
    assert "[[inputs.mem]]" not in result.output
    assert "[[inputs.disk]]" not in result.output
    assert "[[inputs.net]]" not in result.output
    assert "[[inputs.system]]" not in result.output
    assert "[[inputs.swap]]" not in result.output


def test_cli_render_all_windows_baseline_cleared():
    """Verify render with all Windows baseline negated produces no core plugins."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["render", "--os", "windows", "--no-win-perf", "--no-win-services"],
    )
    assert result.exit_code == 0
    assert "[[inputs.win_perf_counters]]" not in result.output
    assert "[[inputs.win_services]]" not in result.output


def test_cli_render_no_baseline():
    """Verify render with --no-baseline produces empty inputs canvas."""
    runner = CliRunner()
    result = runner.invoke(cli, ["render", "--no-baseline"])
    assert result.exit_code == 0
    assert "[[inputs.cpu]]" not in result.output
    assert "[[inputs.mem]]" not in result.output
    assert "[[inputs.win_perf_counters]]" not in result.output


def test_cli_render_no_baseline_with_plugin():
    """Verify render with --no-baseline and selective flags enables only requested plugins."""
    runner = CliRunner()
    result = runner.invoke(cli, ["render", "--no-baseline", "--diskio", "--processes"])
    assert result.exit_code == 0
    assert "[[inputs.cpu]]" not in result.output
    assert "[[inputs.diskio]]" in result.output
    assert "[[inputs.processes]]" in result.output


def test_cli_render_raw_unpadded_default():
    """Verify default render command outputs raw unpadded TOML suitable for file redirection."""
    runner = CliRunner()
    result = runner.invoke(cli, ["render"])
    assert result.exit_code == 0
    assert "╭" not in result.output
    assert "─" not in result.output
    assert "[[inputs.cpu]]" in result.output


def test_cli_render_pretty_option():
    """Verify render --pretty includes styled container."""
    runner = CliRunner()
    result = runner.invoke(cli, ["render", "--pretty"])
    assert result.exit_code == 0
    assert "# vcf-helper-system.conf" in result.output
    assert "# cloudproxy-http.conf" in result.output
    assert "[[inputs.cpu]]" in result.output


def test_cli_run_no_baseline():
    """Verify run command accepts --no-baseline and executes in dry-run mode."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "run",
            "--vcf-url", "https://vcf.local",
            "--mock-vcf",
            "--collector", "10.10.10.50",
            "--target-host", "10.10.10.101",
            "--connection", "mock",
            "--no-baseline",
            "--diskio",
            "--dry-run",
            "--preview",
        ],
    )
    assert result.exit_code == 0
    assert "[[inputs.diskio]]" in result.output
    assert "[[inputs.cpu]]" not in result.output


def test_cli_password_env_fallbacks(monkeypatch):
    """Verify CLI reads passwords from environment variables when not specified in options."""
    monkeypatch.setenv("VCF_PASS", "SecretVcfEnvPass123!")
    monkeypatch.setenv("SSH_PASS", "SecretSshEnvPass123!")
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "run",
            "--vcf-url", "https://vcf.local",
            "--vcf-user", "admin",
            "--mock-vcf",
            "--collector", "10.10.10.50",
            "--target-host", "10.10.10.101",
            "--connection", "mock",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0


def test_cli_run_mode_script_generates_bundle(tmp_path):
    """Verify --mode script writes deploy-telegraf.sh and bundle files to output-dir without remote connection."""
    bundle_out = tmp_path / "test-script-bundle"
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "run",
            "--vcf-url", "https://vcf.local",
            "--mock-vcf",
            "--collector", "10.10.10.50",
            "--target-host", "node01.corp.local",
            "--mode", "script",
            "--output-dir", str(bundle_out),
        ],
    )
    assert result.exit_code == 0
    assert (bundle_out / "deploy-telegraf.sh").exists()
    script_txt = (bundle_out / "deploy-telegraf.sh").read_text(encoding="utf-8")
    assert "systemctl restart telegraf" in script_txt
    assert "MUTUAL_AUTHENTICATION" in script_txt
    assert (bundle_out / "etc" / "telegraf" / "telegraf.d" / "vcf-helper-system.conf").exists()
    assert (bundle_out / "etc" / "telegraf" / "telegraf.d" / "cloudproxy-http.conf").exists()


def test_cli_run_mode_config_only_generates_bundle(tmp_path):
    """Verify --mode config_only writes configuration files without deploy scripts."""
    bundle_out = tmp_path / "test-cfg-bundle"
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "run",
            "--vcf-url", "https://vcf.local",
            "--mock-vcf",
            "--collector", "10.10.10.50",
            "--target-host", "node01.corp.local",
            "--mode", "config_only",
            "--output-dir", str(bundle_out),
        ],
    )
    assert result.exit_code == 0
    assert not (bundle_out / "deploy-telegraf.sh").exists()
    assert (bundle_out / "etc" / "telegraf" / "telegraf.d" / "vcf-helper-system.conf").exists()
    assert (bundle_out / "etc" / "telegraf" / "telegraf.d" / "cloudproxy-http.conf").exists()


def test_cli_ssh_and_winrm_default_users(monkeypatch):
    """Verify CLI instantiates SSH and WinRM executors with correct default usernames."""
    from vcf_ops_telegraf_helper.executors.ssh import SSHExecutor
    from vcf_ops_telegraf_helper.executors.winrm import WinRMExecutor

    ssh_init_kwargs = {}
    orig_ssh_init = SSHExecutor.__init__

    def mock_ssh_init(self, *args, **kwargs):
        ssh_init_kwargs.update(kwargs)
        orig_ssh_init(self, *args, **kwargs)

    winrm_init_kwargs = {}
    orig_winrm_init = WinRMExecutor.__init__

    def mock_winrm_init(self, *args, **kwargs):
        winrm_init_kwargs.update(kwargs)
        orig_winrm_init(self, *args, **kwargs)

    monkeypatch.setattr(SSHExecutor, "__init__", mock_ssh_init)
    monkeypatch.setattr(WinRMExecutor, "__init__", mock_winrm_init)
    monkeypatch.setenv("SSH_PASS", "Secret123")
    monkeypatch.setenv("VCF_PASS", "Secret123")

    runner = CliRunner()
    # Test Linux / SSH default user is root
    runner.invoke(
        cli,
        [
            "run",
            "--vcf-url", "https://vcf.local",
            "--mock-vcf",
            "--collector", "10.10.10.50",
            "--target-host", "linux-host.local",
            "--connection", "ssh",
            "--preview",
        ],
    )
    assert ssh_init_kwargs.get("username") == "root"

    # Test Windows / WinRM default user is Administrator
    runner.invoke(
        cli,
        [
            "run",
            "--vcf-url", "https://vcf.local",
            "--mock-vcf",
            "--collector", "10.10.10.50",
            "--target-host", "win-host.local",
            "--connection", "winrm",
            "--preview",
        ],
    )
    assert winrm_init_kwargs.get("username") == "Administrator"


def test_cli_uninstall_password_env_fallback(monkeypatch):
    """Verify uninstall command uses SSH_PASS when password option is omitted."""
    from unittest.mock import MagicMock
    monkeypatch.setenv("SSH_PASS", "EnvUninstallPass123!")
    runner = CliRunner()
    with patch("vcf_ops_telegraf_helper.workflow.uninstall.UninstallEndpointWorkflow.run") as mock_run:
        mock_run.return_value = MagicMock(
            success=True,
            verifications={"Service inactive": "PASS", "Binary absent": "PASS", "Configuration absent": "PASS"},
        )
        result = runner.invoke(
            cli,
            [
                "uninstall",
                "--target", "node01.corp.local",
                "--method", "ssh",
                "--yes",
            ],
        )
        assert result.exit_code == 0


def test_cli_credential_env_precedence_ssh_vs_winrm(monkeypatch):
    """Verify WinRM prefers WINRM_PASS and SSH prefers SSH_PASS when both are set."""
    ssh_init_kwargs = {}
    orig_ssh_init = SSHExecutor.__init__

    def mock_ssh_init(self, *args, **kwargs):
        ssh_init_kwargs.update(kwargs)
        orig_ssh_init(self, *args, **kwargs)

    winrm_init_kwargs = {}
    orig_winrm_init = WinRMExecutor.__init__

    def mock_winrm_init(self, *args, **kwargs):
        winrm_init_kwargs.update(kwargs)
        orig_winrm_init(self, *args, **kwargs)

    monkeypatch.setattr(SSHExecutor, "__init__", mock_ssh_init)
    monkeypatch.setattr(WinRMExecutor, "__init__", mock_winrm_init)
    monkeypatch.setenv("SSH_PASS", "LinuxSecretPass123!")
    monkeypatch.setenv("WINRM_PASS", "WindowsSecretPass456!")
    monkeypatch.setenv("VCF_PASS", "VcfAdminPass789!")

    runner = CliRunner()
    # SSH execution should prefer SSH_PASS
    runner.invoke(
        cli,
        [
            "run",
            "--vcf-url", "https://vcf.local",
            "--mock-vcf",
            "--collector", "10.10.10.50",
            "--target-host", "linux-host.local",
            "--connection", "ssh",
            "--preview",
        ],
    )
    assert ssh_init_kwargs.get("password") == "LinuxSecretPass123!"

    # WinRM execution should prefer WINRM_PASS
    runner.invoke(
        cli,
        [
            "run",
            "--vcf-url", "https://vcf.local",
            "--mock-vcf",
            "--collector", "10.10.10.50",
            "--target-host", "win-host.local",
            "--connection", "winrm",
            "--preview",
        ],
    )
    assert winrm_init_kwargs.get("password") == "WindowsSecretPass456!"


def test_cli_uninstall_credential_env_precedence_ssh_vs_winrm(monkeypatch):
    """Verify uninstall prefers WINRM_PASS on Windows/WinRM and SSH_PASS on Linux/SSH."""
    from unittest.mock import MagicMock
    ssh_init_kwargs = {}
    orig_ssh_init = SSHExecutor.__init__

    def mock_ssh_init(self, *args, **kwargs):
        ssh_init_kwargs.update(kwargs)
        orig_ssh_init(self, *args, **kwargs)

    winrm_init_kwargs = {}
    orig_winrm_init = WinRMExecutor.__init__

    def mock_winrm_init(self, *args, **kwargs):
        winrm_init_kwargs.update(kwargs)
        orig_winrm_init(self, *args, **kwargs)

    monkeypatch.setattr(SSHExecutor, "__init__", mock_ssh_init)
    monkeypatch.setattr(WinRMExecutor, "__init__", mock_winrm_init)
    monkeypatch.setenv("SSH_PASS", "LinuxUninstallPass123!")
    monkeypatch.setenv("WINRM_PASS", "WindowsUninstallPass456!")

    runner = CliRunner()
    with patch("vcf_ops_telegraf_helper.workflow.uninstall.UninstallEndpointWorkflow.run") as mock_run:
        mock_run.return_value = MagicMock(
            success=True,
            verifications={"Service inactive": "PASS", "Binary absent": "PASS", "Configuration absent": "PASS"},
        )
        # SSH / Linux method prefers SSH_PASS
        res_ssh = runner.invoke(
            cli,
            [
                "uninstall",
                "--target", "node01.corp.local",
                "--method", "ssh",
                "--os", "linux",
                "--yes",
            ],
        )
        assert res_ssh.exit_code == 0
        assert ssh_init_kwargs.get("password") == "LinuxUninstallPass123!"

        # WinRM / Windows method prefers WINRM_PASS
        res_win = runner.invoke(
            cli,
            [
                "uninstall",
                "--target", "win01.corp.local",
                "--method", "winrm",
                "--os", "windows",
                "--yes",
            ],
        )
        assert res_win.exit_code == 0
        assert winrm_init_kwargs.get("password") == "WindowsUninstallPass456!"


def test_cli_version_option():
    """Verify --version and -v return current package version."""
    from vcf_ops_telegraf_helper import __version__
    runner = CliRunner()
    res = runner.invoke(cli, ["--version"])
    assert res.exit_code == 0
    assert __version__ in res.output

    res_short = runner.invoke(cli, ["-v"])
    assert res_short.exit_code == 0
    assert __version__ in res_short.output


def test_cli_vms_command_listing_and_filtering():
    """Verify vms command queries inventory and applies OS and query filters."""
    runner = CliRunner()
    res = runner.invoke(
        cli,
        [
            "vms",
            "--vcf-url", "https://vcf-ops.local",
            "--mock-vcf",
        ],
    )
    assert res.exit_code == 0
    assert "Virtual Machine Inventory" in res.output
    assert "dbdemo01" in res.output
    assert "webapp01" in res.output

    # Filter by OS windows
    res_win = runner.invoke(
        cli,
        [
            "vms",
            "--vcf-url", "https://vcf-ops.local",
            "--mock-vcf",
            "--os", "windows",
        ],
    )
    assert res_win.exit_code == 0
    assert "dbdemo01" in res_win.output
    assert "webapp01" not in res_win.output

    # Filter by query
    res_q = runner.invoke(
        cli,
        [
            "vms",
            "--vcf-url", "https://vcf-ops.local",
            "--mock-vcf",
            "--filter", "k8s",
        ],
    )
    assert res_q.exit_code == 0
    assert "k8s-node01" in res_q.output
    assert "dbdemo01" not in res_q.output


def test_cli_run_ca_cert_and_vm_binding(tmp_path):
    """Verify run command accepts --ca-cert, --vm-name, and --vm-id options."""
    ca_file = tmp_path / "custom-ca.pem"
    ca_file.write_text("-----BEGIN CERTIFICATE-----\nTEST-CA\n-----END CERTIFICATE-----\n")

    runner = CliRunner()
    res = runner.invoke(
        cli,
        [
            "run",
            "--vcf-url", "https://vcf-ops.local",
            "--mock-vcf",
            "--collector", "10.10.10.50",
            "--target-host", "10.10.10.101",
            "--connection", "mock",
            "--ca-cert", str(ca_file),
            "--vm-name", "corp-db-01",
            "--vm-id", "vm-1042",
            "--preview",
        ],
    )
    assert res.exit_code == 0
    assert "corp-db-01" in res.output or "10.10.10.101" in res.output


def test_cli_run_unknown_vm_id_fails_instead_of_enrolling_unmanaged():
    """A --vm-id that is not in inventory must fail, not silently enroll an unmanaged host."""
    runner = CliRunner()
    res = runner.invoke(
        cli,
        [
            "run",
            "--vcf-url", "https://vcf-ops.local",
            "--mock-vcf",
            "--collector", "10.10.10.50",
            "--target-host", "10.10.10.101",
            "--connection", "mock",
            "--vm-id", "vm-9021",
            "--preview",
        ],
    )
    assert res.exit_code != 0
    assert "vm-9021" in res.output


def test_cli_run_force_new_cert():
    """Verify run command accepts --force-new-cert flag and forwards to workflow options."""
    runner = CliRunner()
    res = runner.invoke(
        cli,
        [
            "run",
            "--vcf-url", "https://vcf-ops.local",
            "--mock-vcf",
            "--collector", "10.10.10.50",
            "--target-host", "10.10.10.101",
            "--connection", "mock",
            "--force-new-cert",
            "--preview",
        ],
    )
    assert res.exit_code == 0








def test_cli_run_vc_id_requires_vm_id():
    """--vc-id on its own is rejected rather than silently dropped."""
    runner = CliRunner()
    res = runner.invoke(
        cli,
        [
            "run",
            "--vcf-url", "https://vcf-ops.local",
            "--mock-vcf",
            "--collector", "10.10.10.50",
            "--target-host", "10.10.10.101",
            "--connection", "mock",
            "--vc-id", "vc-1",
            "--preview",
        ],
    )
    assert res.exit_code != 0
    assert "--vc-id requires --vm-id" in res.output
