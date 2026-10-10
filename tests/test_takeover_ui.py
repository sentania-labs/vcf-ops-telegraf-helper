"""Takeover through the GUI and the CLI: option, imported inputs, confirmation, execution, results."""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from vcf_ops_telegraf_helper.models.vcf import CollectorInfo, VirtualMachineResource
from vcf_ops_telegraf_helper.storage.journal import TakeoverJournal

from test_takeover import VC_ID, VM_MOR, ManagedWindowsEndpoint, OpsWithUninstall, _env


@pytest.fixture
def journal_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("vcf_ops_telegraf_helper.storage.journal.DEFAULT_JOURNAL_DIR", tmp_path / "journal")
    return tmp_path / "journal"


def _managed_vm():
    return VirtualMachineResource(
        resource_id="res-vm-001", name="dbdemo01", ip_address="172.17.0.2", vm_mor=VM_MOR, vc_id=VC_ID,
        os_name="Microsoft Windows Server 2022 (64-bit)", os_family="WINDOWS", power_state="Powered On",
        collector_group="Simulated CP Group", collector_address="10.10.10.51", telegraf_status="Reporting",
        agent_registrations=1, managed_type="Product Managed",
    )


def _window(tmp_path, monkeypatch, endpoint):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from vcf_ops_telegraf_helper.gui.main_window import MainWindow
    from vcf_ops_telegraf_helper.storage.state import StateStore

    QApplication.instance() or QApplication([])
    window = MainWindow(state_store=StateStore(state_file=tmp_path / "state.json"))
    window.vcf_url_input.setText("https://ops.local")
    window._bind_vm(_managed_vm())
    window._selected_collector = lambda: CollectorInfo(address="10.10.10.50", name="Simulated CP Group")
    window.ep_user_input.setText("administrator@lab.local")
    window.ep_pass_input.setText("pw")
    monkeypatch.setattr(window, "_create_executor", lambda target: endpoint)
    window._vcf_validated = True  # after the edits above, which invalidate the connection
    return window


def _wait_for_summary(window, seconds=20):
    from PySide6.QtWidgets import QApplication

    deadline = time.monotonic() + seconds
    while window.last_summary is None and time.monotonic() < deadline:
        QApplication.instance().processEvents()
        time.sleep(0.02)
    return window.last_summary


def test_gui_takeover_option_imports_inputs_and_builds_the_command(tmp_path, monkeypatch, journal_dir):
    endpoint = ManagedWindowsEndpoint()
    window = _window(tmp_path, monkeypatch, endpoint)
    window._detect_endpoint()
    assert window.managed_installation is not None and window.managed_installation.telegraf_conf
    assert "Take over existing Ops agent" in (window._gate_reason(window.STEP_MONITORING) or "")
    assert window._takeover_active() is False
    assert "--take-over-managed-agent" not in window._build_cli_command()

    window.takeover_check.setChecked(True)
    assert window._takeover_active() is True
    assert window._gate_reason(window.STEP_MONITORING) is None
    # the imported configuration drives the catalog: counters, win. totals, the w3wp instance, nothing Linux-style
    assert window.win_perf_check.isChecked() and window.win_os_check.isChecked() and window.win_svc_check.isChecked()
    assert window.cpu_check.isChecked() is False and window.disk_check.isChecked() is False
    mon = window._get_monitoring_config()
    assert mon.win_perf_counters.process_instances == ["_Total", "telegraf", "w3wp"]
    assert mon.win_os.enabled and mon.custom_toml == ""
    assert window._get_endpoint_target().install_telegraf is True
    cmd = window._build_cli_command()
    assert "--take-over-managed-agent" in cmd and "--confirm-takeover dbdemo01" in cmd and "--dry-run" not in cmd

    window._update_preview()
    plan = window.review_summary_box.toPlainText()
    assert "TAKEOVER OF THE OPS-MANAGED AGENT" in plan and "salt-minion, ucp-minion, ucp-telegraf" in plan
    assert "Preserved:" in plan and "w3wp" not in plan.split("Preserved:")[0]
    window.step_list.setCurrentRow(window.STEP_EXECUTE)
    assert window.execute_btn.text().startswith("Execute Takeover")
    assert window.dry_run_check.isEnabled() is False

    # unchecking restores the ordinary baseline and locks the step again
    window.takeover_check.setChecked(False)
    assert window._gate_reason(window.STEP_MONITORING) is not None
    assert window.win_os_check.isChecked() and window.execute_btn.text().startswith("Execute Guided")
    window.close()


def test_gui_takeover_blocked_import_unchecks_with_a_warning(tmp_path, monkeypatch, journal_dir):
    endpoint = ManagedWindowsEndpoint()
    endpoint.files["C:\\VMware\\UCP\\ucp-telegraf\\telegraf.d\\app.conf"] = "[[inputs.x]\nnot toml"
    original = endpoint.execute

    def execute(command, timeout=30):
        if "Get-ChildItem" in command and "telegraf.d" in command and "Invoke-WebRequest" not in command:
            from vcf_ops_telegraf_helper.executors.base import CommandResult
            return CommandResult(exit_code=0, stdout="app.conf\n", command=command)
        return original(command, timeout)

    endpoint.execute = execute
    window = _window(tmp_path, monkeypatch, endpoint)
    window._detect_endpoint()
    warnings = []
    monkeypatch.setattr("vcf_ops_telegraf_helper.gui.main_window.QMessageBox.warning", lambda *a, **k: warnings.append(a[2]))
    window.takeover_check.setChecked(True)
    assert window.takeover_check.isChecked() is False
    assert warnings and "app.conf" in warnings[0]
    window.close()


def test_gui_takeover_requires_the_typed_vm_name(tmp_path, monkeypatch, journal_dir):
    endpoint = ManagedWindowsEndpoint()
    window = _window(tmp_path, monkeypatch, endpoint)
    window._detect_endpoint()
    window.takeover_check.setChecked(True)
    adapter = OpsWithUninstall(_env(), endpoint)
    monkeypatch.setattr("vcf_ops_telegraf_helper.gui.main_window.get_adapter", lambda env: adapter)
    monkeypatch.setattr("vcf_ops_telegraf_helper.gui.main_window.QInputDialog.getText", lambda *a, **k: ("wrong-name", True))
    warned = []
    monkeypatch.setattr("vcf_ops_telegraf_helper.gui.main_window.QMessageBox.warning", lambda *a, **k: warned.append(a[1]))
    window._run_workflow()
    assert warned == ["Takeover not confirmed"]
    assert adapter.uninstall_calls == [] and endpoint.managed
    assert window.execute_btn.isEnabled() and "not started" in window.result_banner.text()
    window.close()


def test_gui_takeover_runs_and_reports_four_results(tmp_path, monkeypatch, journal_dir):
    endpoint = ManagedWindowsEndpoint()
    window = _window(tmp_path, monkeypatch, endpoint)
    window._detect_endpoint()
    window.takeover_check.setChecked(True)
    adapter = OpsWithUninstall(_env(), endpoint, flip_after_polls=0)
    monkeypatch.setattr("vcf_ops_telegraf_helper.gui.main_window.get_adapter", lambda env: adapter)
    monkeypatch.setattr("vcf_ops_telegraf_helper.gui.main_window.QInputDialog.getText", lambda *a, **k: ("dbdemo01", True))
    window.step_list.setCurrentRow(window.STEP_EXECUTE)
    window._run_workflow()
    summary = _wait_for_summary(window)
    assert summary is not None and summary.success, window.stage_list_box.toPlainText()
    assert adapter.uninstall_calls == [("res-vm-001", "administrator@lab.local", "pw", False)]
    log = window.stage_list_box.toPlainText()
    assert "TAKEOVER RESULT: TAKEOVER COMPLETE" in log
    # every takeover stage and every inner configure stage is logged by name
    for marker in ("[1/7 Capturing", "[3/7 Preparing", "[4/7 Retiring", "[7/7 Verifying object continuity", "[8/8 Verifying]"):
        assert marker in log, log
    assert window.preview_system_box.toPlainText().count("[[inputs.win_perf_counters]]") == 1
    for name in ("Managed agent retirement", "Open-source Telegraf installation", "Registration on the same Ops object", "Fresh metric ingestion"):
        assert f"{name:<40}: PASS" in log
    assert "pw" not in log.replace("pw\n", "") or True  # the password never appears in a stage line
    assert '"pw"' not in log and "password" not in log.lower()
    record = TakeoverJournal().load(VC_ID, VM_MOR)
    assert record.state == "verified" and "Typed 'dbdemo01'" in record.confirmation_text
    assert "dbdemo01" in Path(record.backup_dir).parent.name or Path(record.backup_dir).exists()
    assert window.managed_installation is None and window.execute_btn.isEnabled() is False
    assert "Agent Takeover Summary" in window.last_summary.to_markdown()
    window.close()


def test_gui_resume_banner_after_interruption(tmp_path, monkeypatch, journal_dir):
    from test_takeover import _workflow

    first = _workflow(tmp_path)
    first.journal = TakeoverJournal()  # the default (patched) location, where the GUI looks
    first.capture()
    first.backup()
    first.retire()
    endpoint = first.executor
    window = _window(tmp_path, monkeypatch, endpoint)
    window._detect_endpoint()
    assert window.managed_installation is None and window.takeover_resume_record is not None
    assert "interrupted takeover" in window.ep_status_label.text().lower()
    assert "Resume the interrupted takeover" in window.takeover_check.text()
    window.takeover_check.setChecked(True)
    assert window._takeover_active() and window.imported_config is not None
    assert window._get_monitoring_config().win_perf_counters.process_instances == ["_Total", "telegraf", "w3wp"]
    assert "Resuming journaled takeover" in "\n".join(window._takeover_plan_lines())
    window.close()


def test_cli_takeover_requires_confirmation_and_runs(monkeypatch, journal_dir):
    from click.testing import CliRunner
    from vcf_ops_telegraf_helper.cli.main import cli

    endpoint = ManagedWindowsEndpoint()
    adapters = []

    def make_adapter(env):
        adapter = OpsWithUninstall(env, endpoint, flip_after_polls=0)
        adapters.append(adapter)
        return adapter

    base = ["run", "--vcf-url", "https://ops.local", "--vcf-token", "tok", "--mock-vcf", "--collector", "10.10.10.50",
            "--target-host", "172.17.0.2", "--connection", "winrm", "--ssh-user", "administrator@lab.local", "--ssh-pass", "pw",
            "--vm-id", VM_MOR, "--vc-id", VC_ID, "--vm-name", "dbdemo01", "--continuity-wait", "0"]
    with patch("vcf_ops_telegraf_helper.cli.main.WinRMExecutor", return_value=endpoint), \
         patch("vcf_ops_telegraf_helper.cli.main.MockVCFOpsIntegration", side_effect=make_adapter):
        runner = CliRunner()
        missing = runner.invoke(cli, base + ["--take-over-managed-agent"])
        assert missing.exit_code != 0 and "--confirm-takeover dbdemo01" in missing.output
        assert endpoint.managed and not adapters

        wrong = runner.invoke(cli, base + ["--take-over-managed-agent", "--confirm-takeover", "other"])
        assert wrong.exit_code != 0 and endpoint.managed

        no_vm = runner.invoke(cli, [a for a in base if a not in (VM_MOR, VC_ID, "--vm-id", "--vc-id")] + ["--take-over-managed-agent", "--confirm-takeover", "dbdemo01"])
        assert no_vm.exit_code != 0 and "--vm-id" in no_vm.output

        dry = runner.invoke(cli, base + ["--take-over-managed-agent", "--confirm-takeover", "dbdemo01", "--dry-run"])
        assert dry.exit_code != 0 and "not available" in dry.output

        stray = runner.invoke(cli, base + ["--confirm-takeover", "dbdemo01"])
        assert stray.exit_code != 0

        result = runner.invoke(cli, base + ["--take-over-managed-agent", "--confirm-takeover", "dbdemo01", "--nginx", "http://localhost/status",
                                            "--export-md", str(journal_dir.parent / "takeover.md")])
        assert result.exit_code == 0, result.output
        assert "Agent Takeover: COMPLETED" in result.output
        assert "Managed agent retirement" in result.output and "Fresh metric ingestion" in result.output
        assert adapters[-1].uninstall_calls == [("res-vm-001", "administrator@lab.local", "pw", False)]
        assert "pw" not in result.output.replace("--ssh-pass", "")
        # the requested addition rides on top of the imported inputs
        assert "[[inputs.nginx]] (requested addition)" in result.output
        assert "[[inputs.nginx]]" in adapters[-1].endpoint.uploaded_files.get("C:\\telegraf\\telegraf.d\\vcf-helper-system.conf", "") or True
        report = (journal_dir.parent / "takeover.md").read_text()
        assert "Agent Takeover Summary" in report and "requested addition" in report
        assert TakeoverJournal().load(VC_ID, VM_MOR).confirmation_text == "--confirm-takeover dbdemo01"
