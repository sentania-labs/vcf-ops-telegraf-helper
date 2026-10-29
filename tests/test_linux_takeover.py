"""Synthetic Linux agent layout, encrypted capture, cleanup and complete simulated takeover."""
import base64
import json
import shlex
import subprocess
from pathlib import Path

import pytest

from vcf_ops_telegraf_helper.executors.base import CommandResult
from vcf_ops_telegraf_helper.executors.mock import MockExecutor
from vcf_ops_telegraf_helper.models.endpoint import OSFamily, ConnectionMethod
from vcf_ops_telegraf_helper.models.workflow import StageStatus
from vcf_ops_telegraf_helper.workflow.managed import LINUX_PATHS as P
from vcf_ops_telegraf_helper.workflow.managed_linux import (
    ROOT, REGISTRY, clean_linux_managed_installation, detect_linux_managed_installation,
    vendor_uninstall_commands,
)
from vcf_ops_telegraf_helper.workflow.managed_config import import_managed_config
from test_managed_encryption import encrypted
from test_takeover import GRAINS, OpsWithUninstall, _env, _target, _workflow

CONF = '''[agent]
interval = "60s"
[[inputs.cpu]]
percpu = true
[[inputs.mem]]
[[inputs.disk]]
ignore_fs = ["tmpfs"]
[[inputs.net]]
interfaces = ["eth0"]
[[inputs.system]]
[[inputs.processes]]
[[inputs.exec]]
commands = ["/opt/vmware/ucp/ucp-telegraf/mandatory_tags.sh"]
data_format = "influx"
'''
FRAGMENT = '[[inputs.http_response]]\nurls = ["http://localhost/health"]\n'


def unit(name, path, state="active"):
    return f"Id={name}.service\nLoadState=loaded\nActiveState={state}\nExecStart={{ path={path} ; }}\nFragmentPath=/etc/systemd/system/{name}.service\n"


class ManagedLinuxEndpoint(MockExecutor):
    def __init__(self, salt="salt-minion", encrypted_only=True):
        super().__init__(telegraf_installed=False)
        self.services = {name: unit(name, ROOT + "/" + name + "/bin/service")
                         for name in (salt, "ucp-minion", "ucp-telegraf")}
        self.files = {P.grains: GRAINS, P.tags: "#!/bin/sh\nprintf 'synthetic tags'\n",
                      P.fragments + "/health.conf": FRAGMENT,
                      REGISTRY: json.dumps({"agents": [{"name": "ucp-telegraf", "uninstallCommand":
                                                          ROOT + "/tmp/telegraf_post_install_linux.sh uninstall"}]}),
                      ROOT + "/tmp/telegraf_post_install_linux.sh": "#!/bin/sh\n"}
        if encrypted_only:
            self.files[P.key] = b"synthetic key"
            self.files[P.encrypted_conf] = encrypted(CONF.encode())
        else:
            self.files[P.conf] = CONF
        self.query_fails = False
        self.process_survives = False
        self.unit_survives = False
        self.account_present = True
        self.install_fails = False
        self.vendor_called = False

    def retire(self):
        # Simulate Ops leaving its units/files for the checked cleanup fallback.
        self.telegraf_installed = False

    def execute(self, command, timeout=30):
        self.executed_commands.append(command)
        script = shlex.split(command)[2] if command.startswith("bash -c ") else command
        def result(stdout="", code=0):
            return CommandResult(exit_code=code, stdout=stdout, command=command)
        if "--property=Id,LoadState" in script:
            return result("\n".join(self.services.values()), 1 if self.query_fails else 0)
        if script == ROOT + "/ucp-telegraf/usr/bin/telegraf version":
            return result("Telegraf 1.39.0 synthetic")
        if script.startswith("base64 -- "):
            path = shlex.split(script)[2]
            return result(base64.b64encode(self.files[path]).decode())
        if "-printf" in script:
            return result("\n".join(p.rsplit("/", 1)[1] for p in self.files if p.startswith(P.fragments + "/")))
        if script.startswith(ROOT + "/tmp/"):
            self.vendor_called = True
            return result()
        if "systemctl stop" in script:
            if not self.unit_survives:
                name = next(line for line in script.splitlines() if line.startswith("unit=")).split("=", 1)[1].removesuffix(".service")
                self.services.pop(name, None)
            return result()
        if "ancestors=" in script:
            if self.process_survives:
                return result("Managed process remains", 1)
            self.files = {p: c for p, c in self.files.items() if not any(p.startswith(ROOT + "/" + name + "/")
                          for name in ("salt-minion", "ucp-salt-minion", "ucp-minion", "ucp-telegraf", "certkeys"))}
            return result()
        if "getent passwd" in script:
            return result("RETAINED" if self.account_present else "")
        if "tar " in script and "telegraf" in script:
            if self.install_fails:
                return result(code=1)
            self.telegraf_installed = True
            return result()
        return super().execute(command, timeout)

    def file_exists(self, path):
        return any(p == path or p.startswith(path.rstrip("/") + "/") for p in self.files) or super().file_exists(path)

    def download(self, path):
        return self.files[path] if path in self.files else super().download(path)


class LinuxOps(OpsWithUninstall):
    def get_agent_object(self, *args, **kwargs):
        result = super().get_agent_object(*args, **kwargs)
        return result.model_copy(update={"resource_kind": "linux", "name": "Linux OS on demo"}) if result else None


def linux_workflow(tmp_path, endpoint=None, **options):
    endpoint = endpoint or ManagedLinuxEndpoint()
    return _workflow(tmp_path, endpoint=endpoint, adapter=LinuxOps(_env(), endpoint),
                     target=_target(os_family=OSFamily.LINUX, connection_method=ConnectionMethod.SSH, username="operator"), **options)


@pytest.mark.parametrize("salt", ["salt-minion", "ucp-salt-minion"])
@pytest.mark.parametrize("encrypted_only", [True, False])
def test_linux_capture_and_import(salt, encrypted_only):
    endpoint = ManagedLinuxEndpoint(salt, encrypted_only)
    found = detect_linux_managed_installation(endpoint)
    assert found.query_ok and found.present and not found.read_errors
    assert salt in found.running_services and found.telegraf_conf == CONF
    assert found.telegraf_version == "Telegraf 1.39.0 synthetic"
    assert found.mandatory_tags.startswith("#!/bin/sh")
    assert found.extra_files["uaf/agents-registry.json"]
    imported = import_managed_config(found.telegraf_conf, found.telegraf_d, is_windows=False)
    assert imported.ok
    for name in ("cpu", "mem", "disk", "net", "system", "processes"):
        assert getattr(imported.monitoring, name).enabled
    assert not imported.monitoring.win_services.enabled
    assert "http_response" in imported.monitoring.custom_toml
    assert "mandatory_tags" not in imported.monitoring.custom_toml


def test_stock_salt_is_not_managed_and_query_failure_is_not_absence():
    endpoint = ManagedLinuxEndpoint()
    endpoint.services = {"salt-minion": unit("salt-minion", "/usr/bin/salt-minion")}
    assert not detect_linux_managed_installation(endpoint).present
    endpoint.query_fails = True
    failed = detect_linux_managed_installation(endpoint)
    assert not failed.query_ok
    assert "sudo rights" in failed.query_error and "detect the endpoint again" in failed.query_error


def test_only_named_units_under_the_managed_root_are_detected():
    endpoint = ManagedLinuxEndpoint()
    endpoint.services = {
        "custom": unit("custom", ROOT + "/ucp-minion/bin/ucp-minion", "inactive"),
        "ucp-other": unit("ucp-other", "/usr/bin/other"),
        "ucp-minion": unit("ucp-minion", ROOT + "/ucp-minion/bin/ucp-minion", "inactive"),
    }
    assert set(detect_linux_managed_installation(endpoint).services) == {"ucp-minion"}


def test_full_linux_takeover_and_backup(tmp_path):
    workflow = linux_workflow(tmp_path)
    result = workflow.run()
    assert result.success, [(s.stage, s.message) for s in result.stages]
    assert len(result.stages) == 7
    assert all(value == "PASS" for value in result.results.values())
    assert result.agent_object_after.resource_kind == "linux"
    backup = Path(result.backup_dir)
    assert (backup / "telegraf.conf").read_text() == CONF
    assert (backup / "mandatory_tags.sh").exists()
    assert (backup / "uaf/agents-registry.json").exists()
    assert not list(backup.rglob("key.pem"))
    assert workflow.executor.vendor_called
    assert "arcuser" in result.verifications["Retained items"]
    assert len(result.retained_items) == 2
    assert workflow.record.retained_items == result.retained_items
    assert all("by hand" in item for item in result.retained_items)
    assert workflow.adapter.uninstall_calls[0][1] == "operator"
    output = workflow.executor.uploaded_files["/etc/telegraf/telegraf.d/cloudproxy-http.conf"]
    assert workflow.target.vm_mor in output and workflow.target.vc_id in output


@pytest.mark.parametrize("failure", ["process_survives", "unit_survives", "query_fails"])
def test_cleanup_refuses_unverified_endpoint(failure):
    endpoint = ManagedLinuxEndpoint()
    setattr(endpoint, failure, True)
    with pytest.raises(RuntimeError):
        clean_linux_managed_installation(endpoint, endpoint.files[REGISTRY])
    assert not endpoint.telegraf_installed


def test_partial_capture_stops_before_retirement(tmp_path):
    endpoint = ManagedLinuxEndpoint()
    del endpoint.files[P.key]
    workflow = linux_workflow(tmp_path, endpoint)
    result = workflow.run()
    assert not result.success and not workflow.adapter.uninstall_calls
    assert "key.pem" in result.stages[-1].message


@pytest.mark.parametrize("entry", ["directory", "fragment"])
@pytest.mark.parametrize("target_kind", ["inside", "outside", "missing"])
def test_symlinked_fragments_stop_capture_before_retirement(tmp_path, entry, target_kind):
    """Run the emitted listing shell against real symlinks, including dangling ones."""
    root = tmp_path / "ucp"
    directory = root / Path(P.fragments).relative_to(ROOT)
    directory.parent.mkdir(parents=True)
    target = (root if target_kind == "inside" else tmp_path) / "linked-config"
    if target_kind != "missing":
        if entry == "directory":
            target.mkdir()
            (target / "health.conf").write_text(FRAGMENT)
        else:
            target.write_text(FRAGMENT)
    if entry == "directory":
        link = directory
    else:
        directory.mkdir()
        link = directory / "health.conf"
    link.symlink_to(target, target_is_directory=entry == "directory")

    class LocalListingEndpoint(ManagedLinuxEndpoint):
        def execute(self, command, timeout=30):
            if "-printf" in command:
                completed = subprocess.run(
                    shlex.split(command.replace(ROOT, str(root))),
                    capture_output=True, text=True, timeout=timeout,
                )
                return CommandResult(exit_code=completed.returncode, stdout=completed.stdout,
                                     stderr=completed.stderr, command=command)
            return super().execute(command, timeout)

    workflow = linux_workflow(tmp_path, LocalListingEndpoint())
    result = workflow.run()
    assert not result.success
    assert not workflow.adapter.uninstall_calls
    assert not workflow.executor.vendor_called
    assert "symbolic link" in result.stages[-1].message
    assert str(link) in result.stages[-1].message
    assert link.is_symlink()
    if target_kind != "missing":
        assert (target / "health.conf" if entry == "directory" else target).read_text() == FRAGMENT


def test_linux_resume_after_retirement_uses_linux_import(tmp_path):
    endpoint = ManagedLinuxEndpoint()
    workflow = linux_workflow(tmp_path, endpoint)
    for stage in (workflow.capture, workflow.backup, workflow.preflight, workflow.retire, workflow.clean):
        assert stage().status != StageStatus.FAIL
    resumed = linux_workflow(tmp_path, endpoint)
    resumed.adapter.retired = True
    result = resumed.run()
    assert result.success, [(s.stage, s.message) for s in result.stages]
    assert not resumed.monitoring.win_services.enabled
    assert not resumed.adapter.uninstall_calls


def test_ordinary_configure_refuses_linux_managed_agent(tmp_path):
    workflow = linux_workflow(tmp_path)
    assert workflow.capture().status == StageStatus.PASS
    assert workflow.preflight().status == StageStatus.PASS
    ordinary = workflow.install_workflow
    ordinary.options.allow_managed_agent = False
    assert ordinary.detect_telegraf().status == StageStatus.FAIL


@pytest.mark.parametrize("command", ["rm -rf /", ROOT + "/tmp/hook; touch /tmp/unsafe", "bash /etc/hook", ROOT + "/../hook"])
def test_registry_rejects_commands_outside_vendor_scope(command):
    with pytest.raises(ValueError):
        vendor_uninstall_commands(json.dumps({"uninstallCommand": command}))


def test_linux_gui_import_plan_and_confirmation(tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    from test_takeover_ui import _managed_vm, _window, _wait_for_summary
    endpoint = ManagedLinuxEndpoint()
    window = _window(tmp_path, monkeypatch, endpoint)
    vm = _managed_vm().model_copy(update={"os_family": "LINUX", "os_name": "Ubuntu Linux", "name": "linux-demo"})
    window.bound_vm = None
    window._bind_vm(vm)
    window.ep_user_input.setText("operator")
    window.ep_pass_input.setText("synthetic-password")
    window._vcf_validated = True
    window._detect_endpoint()
    assert window.managed_installation is not None
    assert window.ep_uninstall_btn.isEnabled() is False
    window.takeover_check.setChecked(True)
    assert window._takeover_active()
    assert window.cpu_check.isChecked() and not window.win_svc_check.isChecked()
    window.step_list.setCurrentRow(window.STEP_MONITORING)
    assert window.cpu_check.isEnabled() and not window.win_perf_check.isEnabled()
    for index, (key, _, _, checkbox) in enumerate(window.catalog_items):
        item = window.plugin_catalog_list.item(index)
        if key in ("cpu", "mem", "disk", "net", "system", "processes"):
            assert checkbox.isEnabled() and not item.isHidden()
        if key in ("win_perf", "win_os", "win_svc"):
            assert not checkbox.isEnabled() and item.isHidden()
    window.cpu_check.click()
    assert not window._get_monitoring_config().cpu.enabled
    window.cpu_check.click()
    assert window._get_monitoring_config().cpu.enabled
    assert not window._get_monitoring_config().win_services.enabled
    assert "--confirm-takeover linux-demo" in window._build_cli_command()
    window._update_preview()
    assert ROOT in window.review_summary_box.toPlainText()
    assert "http_response" in window._get_monitoring_config().custom_toml
    adapter = LinuxOps(_env(), endpoint, polls_to_finish=1, flip_after_polls=0)
    monkeypatch.setattr("vcf_ops_telegraf_helper.storage.journal.DEFAULT_JOURNAL_DIR", tmp_path / "journal")
    monkeypatch.setattr("vcf_ops_telegraf_helper.gui.main_window.get_adapter", lambda env: adapter)
    monkeypatch.setattr("vcf_ops_telegraf_helper.gui.main_window.QInputDialog.getText", lambda *a, **k: ("linux-demo", True))
    window.step_list.setCurrentRow(window.STEP_EXECUTE)
    window._run_workflow()
    summary = _wait_for_summary(window)
    assert summary is not None and summary.success, window.stage_list_box.toPlainText()
    assert "2 items were left on the VM for you to review" in window.result_banner.text()
    assert "#d95926" in window.result_banner.styleSheet()
    assert "remove them by hand if nothing else uses them" in window.stage_list_box.toPlainText()
    window.close()


def test_fragment_read_failure_blocks_backup(tmp_path):
    endpoint = ManagedLinuxEndpoint()
    original = endpoint.download
    def download(path):
        if path.endswith("health.conf"):
            raise IOError("synthetic read failure")
        return original(path)
    endpoint.download = download
    workflow = linux_workflow(tmp_path, endpoint)
    result = workflow.run()
    assert not result.success and not workflow.adapter.uninstall_calls
    assert "health.conf" in result.stages[-1].message


def test_vendor_uninstall_runs_before_fallback_cleanup():
    endpoint = ManagedLinuxEndpoint()
    clean_linux_managed_installation(endpoint, endpoint.files[REGISTRY])
    commands = [shlex.split(c)[2] for c in endpoint.executed_commands if c.startswith("bash -c ")]
    vendor = next(i for i, c in enumerate(commands) if c.startswith(ROOT + "/tmp/"))
    fallback = next(i for i, c in enumerate(commands) if "systemctl stop" in c)
    assert vendor < fallback
    assert not any("userdel" in c or "groupdel" in c for c in commands)


def test_generated_linux_shell_is_valid_bash():
    import subprocess
    endpoint = ManagedLinuxEndpoint()
    detect_linux_managed_installation(endpoint)
    clean_linux_managed_installation(endpoint, endpoint.files[REGISTRY])
    for command in endpoint.executed_commands:
        if command.startswith("bash -c "):
            script = shlex.split(command)[2]
            checked = subprocess.run(["bash", "-n"], input=script, text=True, capture_output=True)
            assert checked.returncode == 0, checked.stderr


def _cleanup_sandbox(tmp_path, hook_body=None, ghost=None):
    """Build a filesystem sandbox, fake systemctl and an endpoint that runs the emitted shell in it, without sudo."""
    import os
    import subprocess
    root = tmp_path / "managed"
    units = tmp_path / "units"
    bins = tmp_path / "bin"
    units.mkdir()
    bins.mkdir()
    (tmp_path / "sudoers").write_text("arcuser ALL=(ALL) NOPASSWD: ALL\n")
    for name in ("salt-minion", "ucp-minion", "ucp-telegraf", "certkeys"):
        folder = root / name
        folder.mkdir(parents=True)
        (folder / "payload").write_text("synthetic")
    for name in ("ucp-minion", "ucp-telegraf"):
        suffix = ".ghost" if name == ghost else ".service"
        (units / (name + suffix)).write_text(unit(name, str(root / name / "payload")))
    (units / "salt-minion.service").write_text(unit("salt-minion", "/usr/bin/salt-minion"))
    (units / "ucp-other.service").write_text(unit("ucp-other", "/usr/bin/other"))
    (root / "unknown.txt").write_text("preserve me")
    vendor = root / "tmp/hook"
    vendor.parent.mkdir()
    marker = tmp_path / "vendor-ran"
    vendor.write_text("#!/bin/sh\ntouch " + shlex.quote(str(marker)) + "\n" + (hook_body or ""))
    vendor.chmod(0o700)
    systemctl = bins / "systemctl"
    systemctl.write_text('''#!/bin/bash
set -e
case "$1" in
list-unit-files|list-units)
    echo "getty@.service enabled"
    for unit in "$FAKE_UNITS"/*.service "$FAKE_UNITS"/*.ghost; do
        [ -f "$unit" ] || continue
        name=${unit##*/}
        echo "${name%.ghost}.service enabled" | sed 's/.service.service/.service/'
    done ;;
show)
    [[ "$*" != *getty@.service* ]] || exit 2
    if [[ "$*" == *--property=Id,LoadState* ]]; then
        for unit in "$FAKE_UNITS"/*.service "$FAKE_UNITS"/*.ghost; do if [ -f "$unit" ]; then cat "$unit"; echo; fi; done
    elif [[ "$*" == *--property=LoadState* ]]; then
        if [ -f "$FAKE_UNITS/$2" ] || [ -f "$FAKE_UNITS/${2%.service}.ghost" ]; then echo loaded; else echo not-found; fi
    elif [[ "$*" == *--property=FragmentPath* ]]; then echo "$FAKE_UNITS/$2"
    else exit 2; fi ;;
disable)
    [ -f "$FAKE_UNITS/$2" ] || { echo "Unit file $2 does not exist." >&2; exit 1; } ;;
daemon-reload) rm -f "$FAKE_UNITS"/*.ghost ;;
stop) : ;;
*) exit 2 ;;
esac
''')
    systemctl.chmod(0o700)
    (bins / "getent").write_text("#!/bin/sh\nexit 2\n")
    (bins / "getent").chmod(0o700)

    class SandboxEndpoint:
        def execute(self, command, timeout=30):
            script = shlex.split(command)[2]
            # Every mutation path is redirected into tmp_path. System commands resolve to fakes.
            for source, destination in ((ROOT, str(root)), ("/etc/sudoers", str(tmp_path / "sudoers")), ("/etc/systemd/system", str(units)),
                                        ("/usr/lib/systemd/system", str(tmp_path / "usr-units")),
                                        ("/lib/systemd/system", str(tmp_path / "lib-units"))):
                script = script.replace(source, destination)
            env = dict(os.environ, PATH=str(bins) + os.pathsep + os.environ["PATH"], FAKE_UNITS=str(units))
            result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=timeout, env=env)
            return CommandResult(exit_code=result.returncode, stdout=result.stdout.replace(str(root), ROOT), stderr=result.stderr, command=command)

        def file_exists(self, path):
            return Path(path.replace(ROOT, str(root))).exists()

    return root, units, marker, SandboxEndpoint()


def test_cleanup_shell_removes_synthetic_tree_and_preserves_stock_salt(tmp_path):
    root, units, marker, endpoint = _cleanup_sandbox(tmp_path)
    left = clean_linux_managed_installation(endpoint, json.dumps({"uninstallCommand": ROOT + "/tmp/hook"}))
    assert marker.exists()
    assert (units / "salt-minion.service").exists()
    assert (units / "ucp-other.service").exists()
    assert not (units / "ucp-minion.service").exists()
    assert not (root / "ucp-telegraf").exists()
    assert not (root / "certkeys").exists()
    assert (root / "unknown.txt").read_text() == "preserve me"
    assert any(ROOT in item for item in left)
    assert any("sudoers" in item for item in left)
    assert (tmp_path / "sudoers").exists()


def test_cleanup_removes_loaded_unit_whose_fragment_is_gone(tmp_path):
    root, units, marker, endpoint = _cleanup_sandbox(tmp_path, ghost="ucp-telegraf")
    assert not (units / "ucp-telegraf.service").exists()
    clean_linux_managed_installation(endpoint, json.dumps({"uninstallCommand": ROOT + "/tmp/hook"}))
    assert sorted(path.name for path in units.iterdir()) == ["salt-minion.service", "ucp-other.service"]
    assert not (root / "ucp-telegraf").exists()


def test_cleanup_refuses_root_redirected_after_vendor_uninstall(tmp_path):
    redirected = tmp_path / "redirected"
    sentinel = redirected / "ucp-telegraf" / "payload"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("outside the managed tree")
    managed, original = tmp_path / "managed", tmp_path / "managed.orig"
    hook = f"mv {shlex.quote(str(managed))} {shlex.quote(str(original))}\nln -s {shlex.quote(str(redirected))} {shlex.quote(str(managed))}\n"
    _, _, marker, endpoint = _cleanup_sandbox(tmp_path, hook_body=hook)
    with pytest.raises(RuntimeError, match="could not be removed safely"):
        clean_linux_managed_installation(endpoint, json.dumps({"uninstallCommand": ROOT + "/tmp/hook"}))
    assert marker.exists() and managed.is_symlink()
    assert sentinel.read_text() == "outside the managed tree"
    assert (original / "certkeys" / "payload").exists()


def test_linux_cli_takeover(tmp_path, monkeypatch):
    from click.testing import CliRunner
    from vcf_ops_telegraf_helper.cli.main import cli
    from test_takeover import VC_ID, VM_MOR
    endpoint = ManagedLinuxEndpoint()
    monkeypatch.setattr("vcf_ops_telegraf_helper.storage.journal.DEFAULT_JOURNAL_DIR", tmp_path / "journal")
    monkeypatch.setattr("vcf_ops_telegraf_helper.cli.main.SSHExecutor", lambda **kwargs: endpoint)
    monkeypatch.setattr("vcf_ops_telegraf_helper.cli.main.MockVCFOpsIntegration",
                        lambda env: LinuxOps(env, endpoint, polls_to_finish=1, flip_after_polls=0))
    result = CliRunner().invoke(cli, [
        "run", "--vcf-url", "https://ops.local", "--vcf-token", "tok", "--mock-vcf",
        "--collector", "10.10.10.50", "--target-host", "linux-demo", "--os", "linux",
        "--connection", "ssh", "--ssh-user", "operator", "--ssh-pass", "synthetic-password",
        "--vm-id", VM_MOR, "--vc-id", VC_ID, "--vm-name", "linux-demo", "--continuity-wait", "0",
        "--take-over-managed-agent", "--confirm-takeover", "linux-demo",
    ])
    assert result.exit_code == 0, result.output
    assert "Agent Takeover: COMPLETED" in result.output
    assert "synthetic-password" not in result.output
    assert "inputs.win_services" not in endpoint.uploaded_files["/etc/telegraf/telegraf.d/vcf-helper-system.conf"]


def test_linux_resume_rejects_windows_journal(tmp_path):
    endpoint = ManagedLinuxEndpoint()
    workflow = linux_workflow(tmp_path, endpoint)
    for stage in (workflow.capture, workflow.backup):
        assert stage().status == StageStatus.PASS
    workflow.record.state = "retired"
    workflow.record.os_family = "windows"
    workflow.journal.save(workflow.record)
    endpoint.services = {}
    resumed = linux_workflow(tmp_path, endpoint)
    result = resumed.run()
    assert not result.success and "different operating system" in result.stages[0].message
    assert not resumed.adapter.uninstall_calls


def test_cleanup_scope_does_not_repeat_a_nested_directory():
    from vcf_ops_telegraf_helper.workflow.managed import ManagedInstallation, WINDOWS_PATHS
    linux = ManagedInstallation(P)
    linux.root_present = linux.cert_dir_present = True
    assert linux.cleanup_paths == [ROOT]
    linux.root_present = False
    assert linux.cleanup_paths == [P.cert_dir]
    windows = ManagedInstallation(WINDOWS_PATHS)
    windows.root_present = windows.data_root_present = windows.cert_dir_present = True
    assert windows.cleanup_paths == [WINDOWS_PATHS.root, WINDOWS_PATHS.data_root]
    windows.data_root_present = False
    assert windows.cleanup_paths == [WINDOWS_PATHS.root, WINDOWS_PATHS.cert_dir]


def test_retained_items_survive_continuity_resume(tmp_path):
    endpoint = ManagedLinuxEndpoint()
    workflow = linux_workflow(tmp_path, endpoint, continuity_wait_seconds=0)
    workflow.adapter.flip_after_polls = 100
    first = workflow.run()
    assert first.success and workflow.record.state == "installed"
    resumed = linux_workflow(tmp_path, endpoint, continuity_wait_seconds=0)
    resumed.adapter.retired = True
    resumed.adapter.flip_after_polls = 0
    final = resumed.run()
    assert final.success and final.retained_items == first.retained_items
    assert final.verifications["Retained items"] == first.verifications["Retained items"]


class RetiredLinuxEndpoint(ManagedLinuxEndpoint):
    def __init__(self):
        super().__init__(encrypted_only=False)
        self.services = {}
        self.files = {}
        self.check_error = ""
        self.bootstrap = 'PRESENT\n{"status":"SUCCESS","message":"Success - uninstall","stage":"7"}'

    def execute(self, command, timeout=30):
        script = shlex.split(command)[2] if command.startswith("bash -c ") else command
        if 'for unit in ' in script and 'Unit loaded:' in script:
            return CommandResult(command=command, exit_code=1 if self.check_error else 0, stdout=self.check_error)
        if "uaf-bootstrap-results" in script:
            return CommandResult(command=command, exit_code=0, stdout=self.bootstrap)
        return super().execute(command, timeout)


def pending_linux_resume(tmp_path, stage="SUBMITTING", messages=None):
    from vcf_ops_telegraf_helper.models.vcf import AgentTaskStatus
    from vcf_ops_telegraf_helper.storage.journal import TakeoverJournal
    endpoint = ManagedLinuxEndpoint(encrypted_only=False)
    wf = linux_workflow(tmp_path, endpoint, uninstall_timeout_seconds=0)
    assert wf.capture().status != StageStatus.FAIL
    assert wf.backup().status != StageStatus.FAIL
    record = TakeoverJournal(tmp_path / "journal").load(wf.target.vc_id, wf.target.vm_mor)
    record.state = "retire_failed"
    record.uninstall_task_id = "stuck-task"
    record.cutover_started_epoch = 1
    TakeoverJournal(tmp_path / "journal").save(record)
    endpoint = RetiredLinuxEndpoint()
    resumed = linux_workflow(tmp_path, endpoint, accept_endpoint_state=True)
    resumed.adapter.get_agent_task_status = lambda task_id: AgentTaskStatus(task_id=task_id, stage=stage, messages=messages or [])
    return resumed


@pytest.mark.parametrize("stage", ["SUBMITTING", "RUNNING"])
def test_accepts_clean_linux_endpoint_with_pending_task(tmp_path, stage):
    wf = pending_linux_resume(tmp_path, stage)
    result = wf.capture()
    assert result.status == StageStatus.WARNING
    assert "Ops never confirmed" in wf.record.notes[-2]
    assert "stuck-task" in wf.record.notes[-2]
    assert "bootstrap result SUCCESS" in wf.record.notes[-2]


@pytest.mark.parametrize("reason", ["Unit loaded", "Unit on disk", "Managed tree remains", "Managed process remains"])
def test_pending_task_refuses_failed_endpoint_checks(tmp_path, reason):
    wf = pending_linux_resume(tmp_path)
    wf.executor.check_error = reason
    result = wf.capture()
    assert result.status == StageStatus.FAIL and reason in result.message
    assert wf.install_summary is None


@pytest.mark.parametrize("result", ['PRESENT\n{"status":"FAILED"}', 'PRESENT\nnot json'])
def test_pending_task_refuses_bad_bootstrap_result(tmp_path, result):
    wf = pending_linux_resume(tmp_path)
    wf.executor.bootstrap = result
    assert wf.capture().status == StageStatus.FAIL


def test_pending_task_accepts_absent_result_and_records_late_completion(tmp_path):
    from vcf_ops_telegraf_helper.models.vcf import AgentTaskStatus
    wf = pending_linux_resume(tmp_path)
    wf.executor.bootstrap = "ABSENT\n"
    statuses = iter([AgentTaskStatus(task_id="stuck-task", stage="SUBMITTING"),
                     AgentTaskStatus(task_id="stuck-task", stage="FINISHED")])
    wf.adapter.get_agent_task_status = lambda _: next(statuses)
    assert wf.capture().status == StageStatus.WARNING
    assert wf.record.uninstall_task_stage == "FINISHED"
    assert any("finished at acceptance" in note for note in wf.record.notes)
    assert not any("never confirmed" in note for note in wf.record.notes)


def test_pending_task_requires_acceptance_and_empty_submitting_messages(tmp_path):
    wf = pending_linux_resume(tmp_path, messages=["Guest credentials rejected"])
    assert wf.capture().status == StageStatus.FAIL
    wf = pending_linux_resume(tmp_path / "second")
    wf.takeover.accept_endpoint_state = False
    assert wf.capture().status == StageStatus.FAIL


def test_endpoint_verification_runs_shell_and_checks_tree_and_result(tmp_path, monkeypatch):
    import os
    import vcf_ops_telegraf_helper.workflow.managed_linux as linux
    root = tmp_path / "ucp"
    bootstrap = root / "bootstrap"
    bootstrap.mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    systemctl = bin_dir / "systemctl"
    systemctl.write_text("#!/bin/sh\nprintf 'not-found\\n'\n")
    systemctl.chmod(0o755)
    monkeypatch.setattr(linux, "ROOT", str(root))
    class LocalExecutor:
        def execute(self, command, timeout=30):
            env = dict(os.environ, PATH=str(bin_dir) + ":" + os.environ["PATH"])
            proc = subprocess.run(command, shell=True, text=True, capture_output=True, timeout=timeout, env=env)
            return CommandResult(command=command, exit_code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)
    executor = LocalExecutor()
    assert linux.verify_linux_retired_endpoint(executor, [])[-1] == "bootstrap result absent"
    result = bootstrap / "uaf-bootstrap-results"
    result.write_text('{"status":"SUCCESS"}')
    assert linux.verify_linux_retired_endpoint(executor, [])[-1] == "bootstrap result SUCCESS"
    result.write_text('{"status":"FAILED"}')
    with pytest.raises(RuntimeError, match="not SUCCESS"):
        linux.verify_linux_retired_endpoint(executor, [])
    result.write_text('{"status":"SUCCESS"}')
    (root / "ucp-minion").mkdir()
    with pytest.raises(RuntimeError, match="Managed tree remains"):
        linux.verify_linux_retired_endpoint(executor, [])


def retired_linux_resume(tmp_path, stage):
    from vcf_ops_telegraf_helper.storage.journal import TakeoverJournal
    endpoint = ManagedLinuxEndpoint(encrypted_only=False)
    wf = linux_workflow(tmp_path, endpoint)
    assert wf.capture().status != StageStatus.FAIL
    assert wf.backup().status != StageStatus.FAIL
    journal = TakeoverJournal(tmp_path / "journal")
    record = journal.load(wf.target.vc_id, wf.target.vm_mor)
    record.state = "retired"
    record.uninstall_task_id = "done-task"
    record.uninstall_task_stage = stage
    record.cutover_started_epoch = 1
    journal.save(record)
    resumed = linux_workflow(tmp_path, RetiredLinuxEndpoint())

    def unavailable(task_id):
        raise RuntimeError("HTTP 404")

    resumed.adapter.get_agent_task_status = unavailable
    return resumed


def test_retired_linux_resume_does_not_need_ops_status(tmp_path):
    wf = retired_linux_resume(tmp_path, "FINISHED")
    assert wf.capture().status == StageStatus.WARNING
    assert wf.record.state == "retired"


def test_unconfirmed_retired_linux_resume_accepts_clean_endpoint_when_status_unavailable(tmp_path):
    wf = retired_linux_resume(tmp_path, "SUBMITTING")
    wf.takeover.accept_endpoint_state = True
    wf.takeover.uninstall_timeout_seconds = 0
    assert wf.capture().status == StageStatus.WARNING
    assert any("Ops never confirmed" in note for note in wf.record.notes)


def test_endpoint_verification_ignores_unmanaged_salt_unit(tmp_path, monkeypatch):
    import os
    import vcf_ops_telegraf_helper.workflow.managed_linux as linux
    root = tmp_path / "ucp"
    (root / "bootstrap").mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    systemctl = bin_dir / "systemctl"
    systemctl.write_text('#!/bin/sh\ncase "$*" in *salt-minion.service*--value*) printf "loaded\\n" ;; *--value*) printf "not-found\\n" ;; esac\n')
    systemctl.chmod(0o755)
    monkeypatch.setattr(linux, "ROOT", str(root))

    class LocalExecutor:
        def execute(self, command, timeout=30):
            env = dict(os.environ, PATH=str(bin_dir) + ":" + os.environ["PATH"])
            proc = subprocess.run(command, shell=True, text=True, capture_output=True, timeout=timeout, env=env)
            return CommandResult(command=command, exit_code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)

    assert linux.verify_linux_retired_endpoint(LocalExecutor(), ["ucp-minion"])[-1] == "bootstrap result absent"
    with pytest.raises(RuntimeError, match="Unit loaded: salt-minion"):
        linux.verify_linux_retired_endpoint(LocalExecutor(), ["salt-minion"])


def test_eligible_pending_task_without_consent_points_at_the_flag(tmp_path):
    wf = pending_linux_resume(tmp_path)
    wf.takeover.accept_endpoint_state = False
    result = wf.capture()
    assert result.status == StageStatus.FAIL
    assert "--accept-endpoint-state" in result.message
