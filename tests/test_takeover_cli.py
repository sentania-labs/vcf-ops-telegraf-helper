"""Takeover from the command line: confirmation, imported inputs, additions, exit codes."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from vcf_ops_telegraf_helper.storage.journal import TakeoverJournal

from test_takeover import VC_ID, VM_MOR, ManagedWindowsEndpoint, OpsWithUninstall


@pytest.fixture
def journal_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("vcf_ops_telegraf_helper.storage.journal.DEFAULT_JOURNAL_DIR", tmp_path / "journal")
    return tmp_path / "journal"


class _RecordingEndpoint(ManagedWindowsEndpoint):
    last_system_conf = ""

    def upload(self, source_content, destination_path, mode=0o644):
        if destination_path.endswith("vcf-helper-system.conf"):
            self.last_system_conf = source_content if isinstance(source_content, str) else source_content.decode()
        super().upload(source_content, destination_path, mode)


def test_cli_takeover_requires_confirmation_and_runs(monkeypatch, journal_dir):
    from click.testing import CliRunner
    from vcf_ops_telegraf_helper.cli.main import cli

    endpoint = _RecordingEndpoint()
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
        assert "[[inputs.nginx]]" in endpoint.last_system_conf
        report = (journal_dir.parent / "takeover.md").read_text()
        assert "Agent Takeover Summary" in report and "requested addition" in report
        assert TakeoverJournal().load(VC_ID, VM_MOR).confirmation_text == "--confirm-takeover dbdemo01"


def test_cli_takeover_refuses_ignored_flags_resolves_vc_id_and_exit_codes(monkeypatch, journal_dir):
    from click.testing import CliRunner
    from vcf_ops_telegraf_helper.cli.main import cli

    endpoint = ManagedWindowsEndpoint()
    adapters = []

    def make_adapter(env, **kw):
        adapter = OpsWithUninstall(env, endpoint, flip_after_polls=0, **kw)
        adapters.append(adapter)
        return adapter

    base = ["run", "--vcf-url", "https://ops.local", "--vcf-token", "tok", "--mock-vcf", "--collector", "10.10.10.50",
            "--target-host", "172.17.0.2", "--connection", "winrm", "--ssh-user", "administrator@lab.local", "--ssh-pass", "pw",
            "--vm-id", VM_MOR, "--vm-name", "dbdemo01", "--continuity-wait", "0",
            "--take-over-managed-agent", "--confirm-takeover", "dbdemo01"]
    with patch("vcf_ops_telegraf_helper.cli.main.WinRMExecutor", return_value=endpoint), \
         patch("vcf_ops_telegraf_helper.cli.main.MockVCFOpsIntegration", side_effect=lambda env: make_adapter(env)):
        runner = CliRunner()
        for flag in (["--no-win-perf"], ["--replace-inputs"], ["--no-baseline"], ["--force-new-cert"], ["--win-services", "x"]):
            res = runner.invoke(cli, base + flag)
            assert res.exit_code != 0 and "do not apply" in res.output, flag
        assert endpoint.managed and not adapters

        # no --vc-id: resolved from inventory (dbdemo01 is vm-1001 in the mock inventory)
        res = runner.invoke(cli, base)
        assert res.exit_code == 0, res.output
        assert "Resolved vCenter id" in res.output and adapters[-1].uninstall_calls[0][0] == "res-vm-001"
        assert TakeoverJournal().load(VC_ID, VM_MOR).vc_id == VC_ID

    # a different object: applied, labelled, exit code 3 (fresh journal: each scenario is its own VM history)
    endpoint = ManagedWindowsEndpoint()
    monkeypatch.setattr("vcf_ops_telegraf_helper.storage.journal.DEFAULT_JOURNAL_DIR", journal_dir / "changed")
    with patch("vcf_ops_telegraf_helper.cli.main.WinRMExecutor", return_value=endpoint), \
         patch("vcf_ops_telegraf_helper.cli.main.MockVCFOpsIntegration", side_effect=lambda env: make_adapter(env, new_object=True)):
        res = CliRunner().invoke(cli, base + ["--vc-id", VC_ID])
        assert res.exit_code == 3 and "DIFFERENT OBJECT" in res.output and "CHANGED OBJECT" in res.output, res.output[-1200:]

    # Ops slow: applied, pending, exit 0 but not called COMPLETED
    endpoint = ManagedWindowsEndpoint()
    monkeypatch.setattr("vcf_ops_telegraf_helper.storage.journal.DEFAULT_JOURNAL_DIR", journal_dir / "pending")
    with patch("vcf_ops_telegraf_helper.cli.main.WinRMExecutor", return_value=endpoint), \
         patch("vcf_ops_telegraf_helper.cli.main.MockVCFOpsIntegration", side_effect=lambda env: OpsWithUninstall(env, endpoint, flip_after_polls=99)):
        res = CliRunner().invoke(cli, base + ["--vc-id", VC_ID])
        assert res.exit_code == 0 and "CONFIRMATION PENDING" in res.output and "Takeover: COMPLETED" not in res.output
        assert "VCF Ops ingestion" not in res.output
