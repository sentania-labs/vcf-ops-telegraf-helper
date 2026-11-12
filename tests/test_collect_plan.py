"""What a tick turns into, and the command shown for doing the same thing from the command line.

The command is checked the hard way: it is parsed by the real command-line program and has to
produce the same target, options and monitoring settings as the run it stands for.
"""

from __future__ import annotations

import shlex
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from vcf_ops_telegraf_helper.cli.main import cli
from vcf_ops_telegraf_helper.models.vcf import CollectorInfo
from vcf_ops_telegraf_helper.renderer.renderer import TelegrafRenderer
from vcf_ops_telegraf_helper.workflow import collect as c
from vcf_ops_telegraf_helper.workflow import plan as p


def _plan(**more):
    values = dict(machine_id="res-1", name="db-pg-01", address="10.20.2.21", os="linux", vm_mor="vm-1003",
                  vc_id="423b-0003", username="svc-monitor", password="guest-secret",
                  collector=CollectorInfo(address="10.20.0.50", name="Main site", is_collector_group=True),
                  choices=c.CollectChoices(keys=list(c.USUAL_SET)))
    values.update(more)
    return p.MachinePlan(**values)


CONN = p.ConnectionChoices(url="https://ops.example.com", username="admin", password="ops-secret")


# ---------------------------------------------------------------------------------------------
# Ticks to settings and back
# ---------------------------------------------------------------------------------------------
def test_the_catalogue_is_three_named_groups_with_the_usual_set_ticked():
    assert [i.name for i in c.CATALOG if i.group == c.GROUP_BASICS][:3] == ["Processor use", "Memory use", "Disk space"]
    assert c.USUAL_SET == ["cpu", "mem", "disk", "net", "system", "swap"]
    assert c.WINDOWS_ONLY == ["winsvc", "winperf"] and c.name_of("pg") == "PostgreSQL"
    assert c.ordered(["swap", "cpu", "nope", "cpu"]) == ["cpu", "swap"]


def test_linux_ticks_become_exactly_those_inputs_and_read_back_the_same():
    choices = c.CollectChoices(keys=["cpu", "disk", "diskio", "procs", "ping"], details={"ping": {"addr": "10.0.0.1"}})
    mon = c.monitoring_for(choices, "linux")
    assert mon.cpu.enabled and mon.disk.enabled and mon.diskio.enabled and mon.processes.enabled
    assert not mon.mem.enabled and not mon.swap.enabled and not mon.win_perf_counters.enabled
    assert mon.ping.enabled and mon.ping.urls == ["10.0.0.1"]
    assert c.keys_from_monitoring(mon, "linux") == ["cpu", "disk", "diskio", "procs", "ping"]
    back = c.read_deployed_choices(TelegrafRenderer.render_system_inputs(mon), "linux")
    assert back.keys == ["cpu", "disk", "diskio", "procs", "ping"] and back.details["ping"] == {"addr": "10.0.0.1"}
    assert c.read_deployed_choices("", "linux") is None and c.read_deployed_choices(None, "linux") is None


def test_software_is_switched_on_only_where_it_runs_unless_one_machine_was_picked():
    choices = c.CollectChoices(keys=["cpu", "nginx", "docker", "ping"], details={"ping": {"addr": "gw"}})
    several = c.monitoring_for(choices, "linux", found=["nginx"], single=False,
                               web_urls={"nginx": "http://localhost/basic_status"})
    assert several.nginx.enabled and several.nginx.urls == ["http://localhost/basic_status"]
    assert not several.docker.enabled, "no Docker on this machine, so it is not switched on here"
    assert several.ping.enabled, "a ping test applies to every machine"
    alone = c.monitoring_for(choices, "linux", found=[], single=True)
    assert alone.nginx.enabled and alone.docker.enabled and alone.nginx.urls == [c.DEFAULT_WEB_URL["nginx"]]
    assert c.effective_keys(choices.keys, "linux", ["nginx"], single=False) == ["cpu", "nginx", "ping"]


def test_windows_collects_the_basics_the_windows_way_as_one_set():
    one = c.monitoring_for(c.CollectChoices(keys=["cpu"]), "windows")
    assert one.win_perf_counters.enabled and one.win_os.enabled and one.win_services.service_names == ["telegraf"]
    assert not one.cpu.enabled and not one.disk.enabled
    assert c.effective_keys(["cpu"], "windows") == c.BASIC_KEYS, "one basic stands for the whole Windows set"
    assert c.keys_from_monitoring(one, "windows") == c.BASIC_KEYS
    none = c.monitoring_for(c.CollectChoices(keys=["ping"], details={"ping": {"addr": "gw"}}), "windows")
    assert not none.win_perf_counters.enabled and not none.win_os.enabled and not none.win_services.enabled
    extra = c.CollectChoices(keys=["cpu", "winsvc", "winperf"], details={
        "winsvc": {"names": ["MSSQLSERVER", "telegraf"]},
        "winperf": {"objects": [{"object_name": "Web Service", "counters": ["Current Connections"], "measurement": "win_web"}]}})
    mon = c.monitoring_for(extra, "windows")
    assert mon.win_services.service_names == ["telegraf", "MSSQLSERVER"]
    assert [o.object_name for o in mon.win_perf_counters.additional_objects] == ["Web Service"]
    assert c.keys_from_monitoring(mon, "windows") == c.BASIC_KEYS + ["winsvc", "winperf"]
    assert c.monitoring_for(extra, "linux").win_services.enabled is False, "Linux machines skip the Windows-only items"


def test_database_passwords_are_used_but_never_kept_or_shown():
    choices = c.CollectChoices(keys=["mysql", "pg", "mssql"], details={
        "mysql": {"user": "monitor", "password": "s3cret", "port": 3306},
        "pg": {"user": "monitor", "password": "s3cret", "databases": ["orders"]},
        "mssql": {"user": "monitor", "password": "s3cret"}})
    mon = c.monitoring_for(choices, "linux")
    assert mon.mysql.servers == ["monitor:s3cret@tcp(127.0.0.1:3306)/?tls=false"]
    assert "password=s3cret" in mon.postgresql.address and "dbname=orders" in mon.postgresql.address
    assert "Password=s3cret;" in mon.mssql.servers[0]
    for key in ("mysql", "pg", "mssql"):
        shown = c.database_setting(key, choices.details[key], placeholder=True)
        assert "s3cret" not in shown and "<password>" in shown
    saved = choices.without_secrets()
    assert "s3cret" not in saved.model_dump_json() and choices.details["mysql"]["password"] == "s3cret"
    assert saved.missing_secrets() == ["mysql", "pg", "mssql"] and choices.missing_secrets() == []


def test_a_deployed_database_setting_is_kept_as_it_is_and_masked_when_shown():
    deployed = c.monitoring_for(c.CollectChoices(keys=["cpu", "mysql"], details={"mysql": {"user": "m", "password": "pw9"}}), "linux")
    back = c.read_deployed_choices(TelegrafRenderer.render_system_inputs(deployed), "linux")
    assert back.keys == ["cpu", "mysql"] and back.details["mysql"]["raw"] == "m:pw9@tcp(127.0.0.1:3306)/?tls=false"
    again = c.monitoring_for(back, "linux")
    assert again.mysql.servers == deployed.mysql.servers, "untouched, the deployed setting is applied unchanged"
    assert c.database_setting("mysql", back.details["mysql"], placeholder=True) == "m:<password>@tcp(127.0.0.1:3306)/?tls=false"
    assert "pw9" not in back.without_secrets().model_dump_json()


def test_someone_elses_additions_to_the_settings_file_are_carried_along():
    text = TelegrafRenderer.render_system_inputs(c.monitoring_for(c.CollectChoices(keys=["cpu"], custom_toml=(
        '[[inputs.http_response]]\n  urls = ["http://localhost/health"]\n')), "linux"))
    back = c.read_deployed_choices(text, "linux")
    assert back.keys == ["cpu"] and "inputs.http_response" in back.custom_toml
    assert "http_response" in TelegrafRenderer.render_system_inputs(c.monitoring_for(back, "linux"))


# ---------------------------------------------------------------------------------------------
# The command, checked by running it through the real command-line program
# ---------------------------------------------------------------------------------------------
def _parse(command: str):
    """Hand the command to the real CLI and capture what it would have run."""
    assert command.startswith("vcf-telegraf-helper ")
    argv = shlex.split(command.replace("\\\n", " "))[1:]
    seen = {}

    class Capture:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def run(self):
            raise SystemExit(0)

    with patch("vcf_ops_telegraf_helper.cli.main.ConfigureEndpointWorkflow", Capture), \
            patch("vcf_ops_telegraf_helper.cli.main.TakeoverWorkflow", Capture), \
            patch("vcf_ops_telegraf_helper.cli.main.SSHExecutor", lambda **kw: seen.setdefault("ssh", kw)), \
            patch("vcf_ops_telegraf_helper.cli.main.WinRMExecutor", lambda **kw: seen.setdefault("winrm", kw)), \
            patch("vcf_ops_telegraf_helper.cli.main.get_adapter", lambda env, **kw: seen.setdefault("adapter_env", env)):
        result = CliRunner().invoke(cli, argv)
    assert result.exit_code == 0, result.output
    return seen


def _settings(mon) -> str:
    """The settings file a monitoring configuration becomes: the thing that has to match."""
    return TelegrafRenderer.render_system_inputs(mon)


def _same_run(plan: p.MachinePlan, conn: p.ConnectionChoices = CONN):
    command = p.build_command(plan, conn)
    for secret in (plan.password, conn.password):
        assert not secret or secret not in command, "a password is never written into the command"
    seen = _parse(command)
    target, options, env = seen["target"], seen["options"], seen["environment"]
    mine = plan.target()
    for field in ("hostname", "os_family", "connection_method", "port", "username", "key_filename", "winrm_use_ssl",
                  "telegraf_version", "registered_hostname", "vm_mor", "vc_id"):
        assert getattr(target, field) == getattr(mine, field), field
    assert env.url == conn.url and env.username == conn.username and env.auth_source == conn.auth_source
    assert env.collector.address == plan.collector.address and env.collector.name == plan.collector.name
    assert env.verify_ssl == conn.verify_ssl and env.pinned_cert_sha256 == conn.pinned_cert_sha256
    assert env.agent_verify_ssl == conn.agent_verify_ssl
    return command, seen, target, options


@pytest.mark.parametrize("keys", [
    c.USUAL_SET, ["cpu", "mem"], ["cpu", "mem", "disk", "net", "system", "diskio", "procs"], ["ping"],
])
def test_linux_setup_command_reproduces_the_run(keys):
    plan = _plan(choices=c.CollectChoices(keys=list(keys), details={"ping": {"addr": "10.20.0.1"}}))
    command, seen, target, options = _same_run(plan)
    assert _settings(seen["monitoring"]) == _settings(plan.monitoring())
    assert options.install_telegraf and options.telegraf_version == "1.40.1" and not options.dry_run
    assert options.replace_inputs and "--replace-inputs" in command, "the app applies what is ticked, so the command must too"
    assert command.count("<password>") == 2 and "..." not in command


def test_change_practice_keep_and_key_file_are_all_in_the_command():
    change = _plan(kind=p.KIND_CHANGE, install=p.KEEP, key_file="/home/me/.ssh/id_ed25519", password=None,
                   choices=c.CollectChoices(keys=["cpu", "mem", "disk", "net", "system", "diskio"]))
    command, seen, target, options = _same_run(change)
    assert options.replace_inputs and not options.install_telegraf and target.key_filename == "/home/me/.ssh/id_ed25519"
    assert "--no-swap" in command and "--diskio" in command and "--ssh-pass" not in command
    assert _settings(seen["monitoring"]) == _settings(change.monitoring())
    practice = _plan(practice=True)
    command, _seen, _target, options = _same_run(practice)
    assert options.dry_run and command.splitlines()[0] == "vcf-telegraf-helper run --replace-inputs --dry-run \\"


def test_windows_setup_command_reproduces_the_run():
    plan = _plan(os="windows", name="db-sql-02", address="10.20.2.32", port=5986, winrm_ssl=True, username="administrator",
                 found=["mssql"], single=False,
                 choices=c.CollectChoices(keys=c.USUAL_SET + ["mssql", "winsvc", "winperf"], details={
                     "mssql": {"user": "monitor", "password": "db-secret"},
                     "winsvc": {"names": ["MSSQLSERVER"]},
                     "winperf": {"objects": [{"object_name": "Web Service", "counters": ["Current Connections"],
                                              "measurement": "win_web_service"}]}}))
    command, seen, target, options = _same_run(plan)
    assert "--winrm-ssl" in command and "--port 5986" in command and "db-secret" not in command
    parsed = seen["monitoring"]
    mine = plan.monitoring()
    assert parsed.mssql.servers == ['Server=127.0.0.1;Port=1433;User Id=monitor;Password="<password>";app name=telegraf;log=1;']
    parsed.mssql.servers = mine.mssql.servers
    assert _settings(parsed) == _settings(mine)


def test_sending_telegraf_a_trusted_certificate_and_other_connection_choices_travel_too():
    conn = p.ConnectionChoices(url="https://ops.example.com", username="jsmith", password="ops-secret", auth_source="corp.example.com",
                               pinned_cert_sha256="5f" * 32, agent_verify_ssl=False)
    plan = _plan(install=p.PUSH, collector=CollectorInfo(address="10.30.0.51", display_name="branch-cp-01"))
    command, _seen, _target, options = _same_run(plan, conn)
    assert options.send_telegraf and "--send-telegraf" in command
    assert f"--trust-cert-sha256 {'5f' * 32}" in command and "--no-agent-verify-ssl" in command
    assert "--vcf-auth-source corp.example.com" in command and "--collector-group" not in command
    token = p.ConnectionChoices(url="https://ops.example.com", token="tok-secret", use_token=True, verify_ssl=False)
    command = p.build_command(_plan(), token)
    assert '--vcf-token "<token>"' in command and "tok-secret" not in command and "--no-verify-ssl" in command
    assert "--vcf-user" not in command


def test_own_settings_are_named_in_the_command_and_never_kept_on_this_computer(tmp_path):
    own = '[[inputs.http_response]]\n  urls = ["http://localhost/health"]\n  password = "own-secret"\n'
    plan = _plan(choices=c.CollectChoices(keys=list(c.USUAL_SET), custom_toml=own))
    command = p.build_command(plan, CONN)
    assert '--custom-toml-file "<file with your own Telegraf settings>"' in command and "own-secret" not in command
    saved = plan.choices.without_secrets()
    assert saved.custom_toml == "" and saved.had_custom and "own-secret" not in saved.model_dump_json()
    # with the person's own file in place of the placeholder, the command reproduces the run
    file = tmp_path / "own.toml"
    file.write_text(own)
    seen = _parse(command.replace('"<file with your own Telegraf settings>"', str(file)))
    assert _settings(seen["monitoring"]) == _settings(plan.monitoring())


def test_takeover_command_names_the_machine_and_asks_for_its_name():
    plan = _plan(kind=p.KIND_TAKEOVER, os="windows", name="app-win-01", address="10.20.3.41", username="administrator")
    command = p.build_command(plan, CONN)
    assert "--take-over-managed-agent --confirm-takeover app-win-01" in command
    assert "--vm-id vm-1003" in command and "--vc-id 423b-0003" in command
    for flag in ("--install-telegraf", "--replace-inputs", "--cpu", "--win-perf", "--dry-run", "--send-telegraf"):
        assert flag not in command, flag
    sent = p.build_command(plan.model_copy(update={"install": p.PUSH}), CONN)
    assert "--send-telegraf" in sent and _parse(sent)["options"].send_telegraf, "a takeover that sent Telegraf says so"
    seen = _parse(command)
    assert seen["takeover"].confirmation_text == "--confirm-takeover app-win-01"
    assert seen["target"].vm_mor == "vm-1003" and seen["monitoring"] is None


def test_remove_command_runs_through_the_real_uninstall():
    linux = p.build_remove_command(_plan())
    assert linux.replace("\\\n", " ").split()[:2] == ["vcf-telegraf-helper", "uninstall"]
    assert '--password "<password>"' in linux and "guest-secret" not in linux and linux.rstrip().endswith("--yes")
    windows = p.build_remove_command(_plan(os="windows", port=5986, winrm_ssl=True, username="administrator"))
    assert "--method winrm" in windows and "--port 5986" in windows and "--winrm-ssl" in windows
    seen = {}

    class Capture:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def run(self):
            raise SystemExit(0)

    with patch("vcf_ops_telegraf_helper.workflow.uninstall.UninstallEndpointWorkflow", Capture), \
            patch("vcf_ops_telegraf_helper.cli.main.WinRMExecutor", lambda **kw: seen.setdefault("winrm", kw)):
        result = CliRunner().invoke(cli, shlex.split(windows.replace("\\\n", " "))[1:])
    assert result.exit_code == 0, result.output
    assert seen["winrm"]["use_ssl"] is True and seen["winrm"]["port"] == 5986
    assert seen["target"].hostname == "10.20.2.21" and seen["target"].username == "administrator"


def test_command_line_refuses_flags_that_make_no_sense_together(tmp_path):
    base = ["run", "--vcf-url", "https://ops.local", "--vcf-token", "t", "--collector", "10.0.0.1", "--target-host", "h",
            "--connection", "mock", "--mock-vcf"]
    runner = CliRunner()
    assert "--install-telegraf" in runner.invoke(cli, base + ["--send-telegraf"]).output
    assert "64 hexadecimal" in runner.invoke(cli, base + ["--trust-cert-sha256", "abcd"]).output
    both = runner.invoke(cli, base + ["--trust-cert-sha256", "ab" * 32, "--no-verify-ssl"])
    assert both.exit_code != 0 and "cannot be combined" in both.output


@pytest.mark.parametrize("key,raw,secret", [
    ("mysql", "mon:p@ssw0rd@tcp(127.0.0.1:3306)/", "p@ssw0rd"),
    ("mysql", "mon:a:b@c@unix(/run/mysqld.sock)/db", "a:b@c"),
    ("pg", 'host=x user=m password="sec ret" sslmode=disable', "sec ret"),
    ("pg", "host=x user=m password='two words' dbname=d", "two words"),
    ("pg", "host=x password=plain", "plain"),
    ("mssql", 'Server=x;User Id=sa;Password="a;b";app name=t;', "a;b"),
    ("mssql", "Server=x;User Id=sa;Password=x y;app name=t;", "x y"),
    ("mssql", "Server=x;PWD={c;d};", "c;d"),
])
def test_a_deployed_password_is_masked_whole_whatever_it_contains(key, raw, secret):
    masked = c._mask_password(key, raw)
    assert masked.count("<password>") == 1 and len(masked) < len(raw) + len("<password>"), masked
    # what is left is the setting with the whole password cut out, nothing more and nothing less
    quoted = [f'"{secret}"', f"'{secret}'", "{" + secret + "}", secret]
    assert masked in [raw.replace(form, "<password>", 1) for form in quoted if form in raw], masked
    choices = c.CollectChoices(keys=[key], details={key: {"raw": raw}})
    assert secret in c.secrets_in(choices)
    assert secret not in choices.without_secrets().model_dump_json()
    assert c.database_setting(key, choices.details[key]) == raw, "applied to the machine exactly as it was"


def test_a_setting_without_a_password_is_left_alone():
    assert c._mask_password("mysql", "tcp(127.0.0.1:3306)/") == "tcp(127.0.0.1:3306)/"
    assert c.secrets_in(c.CollectChoices(keys=["mysql"], details={"mysql": {"raw": "tcp(127.0.0.1:3306)/"}})) == []
    assert c.secrets_in(c.CollectChoices(keys=["pg"], details={"pg": {"user": "m", "password": "pw 1"}})) == ["pw 1"]


@pytest.mark.parametrize("key, password, expected", [
    ("pg", "it's a\\pw", "password='it\\'s a\\\\pw'"),
    ("pg", "plain", "password=plain"),
    ("mssql", 'a;b"c', 'Password="a;b""c";'),
    ("mssql", "plain", "Password=plain;"),
])
def test_database_password_is_quoted_so_it_reads_back_as_typed(key, password, expected):
    detail = {"user": "m", "password": password}
    setting = c.database_setting(key, detail)
    assert expected in setting
    shown = c.database_setting(key, detail, placeholder=True)
    assert password not in shown and "<password>" in shown
    assert c._mask_password(key, setting).count("<password>") == 1
    assert password in c.secrets_in(c.CollectChoices(keys=[key], details={key: detail}))


@pytest.mark.parametrize("key, password, typed", [
    ("pg", "a b'c;d", "a b\\'c;d"),
    ("mssql", 'a b"c;d', 'a b""c;d'),
])
def test_the_displayed_command_takes_a_hard_password_and_gives_the_apps_own_setting(key, password, typed):
    detail = {"user": "monitor", "password": password}
    shown = c.database_setting(key, detail, placeholder=True)
    assert password not in shown
    assert shown.replace("<password>", typed) == c.database_setting(key, detail)


def test_the_command_for_a_database_with_a_password_carries_the_quoting_line():
    plan = _plan(choices=c.CollectChoices(keys=["cpu", "pg"], details={"pg": {"user": "monitor", "password": "x y"}}),
                 found=["pg"])
    command, seen, _target, _options = _same_run(plan)
    assert seen["monitoring"].postgresql.address == "host=localhost port=5432 user=monitor password='<password>' sslmode=disable"
    assert p.quote_note_for([command]) == p.QUOTE_NOTE
    assert p.quote_note_for([_same_run(_plan())[0]]) == ""
