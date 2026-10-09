"""Porting the Ops product-managed telegraf.conf into the helper's monitoring model (issue #61)."""

from __future__ import annotations

from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib

from vcf_ops_telegraf_helper.adapters.vcf91 import VCF91OpenTelegrafIntegration
from vcf_ops_telegraf_helper.models.monitoring import MonitoringConfig, WindowsOsInputConfig
from vcf_ops_telegraf_helper.renderer.renderer import TelegrafRenderer
from vcf_ops_telegraf_helper.workflow.managed_config import RETAINED_HEADER, import_managed_config, mask_secrets

FIXTURES = Path(__file__).parent / "fixtures"
MANAGED_CONF = (FIXTURES / "managed-telegraf-vcf91-windows.conf").read_text()
VENDOR_BAT = (FIXTURES / "managed-mandatory_tags-vcf91.bat").read_text()


def test_stock_managed_config_maps_onto_baseline_plus_os_totals():
    """The 9.1 stock config is the helper's counter set plus the three win.-prefixed inputs plus w3wp."""
    imported = import_managed_config(MANAGED_CONF)
    mon = imported.monitoring
    assert mon.win_perf_counters.enabled
    assert mon.win_perf_counters.process_instances == ["_Total", "telegraf", "w3wp"]
    assert mon.win_perf_counters.additional_objects == []
    assert mon.win_os == WindowsOsInputConfig(enabled=True, cpu=True, mem=True, swap=True)
    # Linux-style plugins stay off; nothing is retained verbatim
    assert not any(getattr(mon, n).enabled for n in ("cpu", "mem", "disk", "net", "system", "swap"))
    assert mon.custom_toml == ""
    assert imported.retained == []
    assert any("mandatory_tags" in d for d in imported.dropped)
    assert any("/arc/metric" in d for d in imported.dropped)
    assert any("[agent]" in d for d in imported.dropped)
    assert any("win_perf_counters]] 11 baseline objects" in p for p in imported.preserved)
    assert any("win_services" in a for a in imported.added)
    assert not imported.warnings


def test_rendered_port_carries_every_managed_counter_and_the_prefixed_inputs():
    """Rendering the imported model yields the same counters per object as the managed file."""
    imported = import_managed_config(MANAGED_CONF)
    rendered = TelegrafRenderer.render_system_inputs(imported.monitoring)
    ours = tomllib.loads(rendered)["inputs"]
    theirs = tomllib.loads(MANAGED_CONF)["inputs"]
    our_objects = {o["ObjectName"]: o for o in ours["win_perf_counters"][0]["object"]}
    for obj in theirs["win_perf_counters"][0]["object"]:
        mine = our_objects[obj["ObjectName"]]
        assert set(obj["Counters"]) <= set(mine["Counters"]), obj["ObjectName"]
        assert set(obj["Instances"]) <= set(mine["Instances"]), obj["ObjectName"]
        assert mine["Measurement"].replace("_", ".") == obj["Measurement"]
    for plugin in ("cpu", "mem", "swap"):
        assert ours[plugin][0]["name_prefix"] == "win."
    assert ours["cpu"][0]["report_active"] is True
    assert "exec" not in ours


def test_extra_app_plugins_are_mapped_or_retained_without_loss():
    conf = MANAGED_CONF + '''
[[inputs.win_services]]
  service_names = ["MSSQLSERVER", "telegraf"]

[[inputs.sqlserver]]
  servers = ["Server=127.0.0.1;Port=1433;User Id=sa;Password=hunter2;app name=telegraf;log=1;"]

[[inputs.procstat]]
  pattern = "w3wp"
  name_prefix = "win."

[[processors.rename]]
  [[processors.rename.replace]]
    tag = "host"
    dest = "hostname"
'''
    imported = import_managed_config(conf, {"app.conf": '[[inputs.http_response]]\n  urls = ["http://localhost:8080/health"]\n'})
    assert imported.ok
    mon = imported.monitoring
    assert mon.win_services.enabled and mon.win_services.service_names == ["MSSQLSERVER", "telegraf"]
    assert mon.mssql.enabled and "hunter2" in mon.mssql.servers[0]
    assert mon.custom_toml.startswith(RETAINED_HEADER)
    retained = tomllib.loads(mon.custom_toml)
    assert retained["inputs"]["procstat"][0] == {"pattern": "w3wp", "name_prefix": "win."}
    assert retained["inputs"]["http_response"][0]["urls"] == ["http://localhost:8080/health"]
    assert retained["processors"]["rename"][0]["replace"][0]["dest"] == "hostname"
    assert any(r.startswith('[[inputs.procstat]] name_prefix "win."') for r in imported.retained)
    assert "[processors] table" in imported.retained
    assert imported.source_files == ["telegraf.conf", "telegraf.d\\app.conf"]
    assert not any("win_services" in a for a in imported.added)
    # the summary never shows the SQL password
    summary = "\n".join(imported.summary_lines())
    assert "hunter2" not in summary and "Password=***" in mask_secrets(mon.mssql.servers[0])
    # the whole thing still renders to valid TOML with every retained plugin present once
    rendered = tomllib.loads(TelegrafRenderer.render_system_inputs(mon))
    assert len(rendered["inputs"]["procstat"]) == 1 and len(rendered["inputs"]["sqlserver"]) == 1


def test_settings_the_catalog_cannot_express_are_retained_not_stripped():
    """Review finding: a known plugin with extra options, or a second instance, must keep every setting."""
    conf = MANAGED_CONF + '''
[[inputs.sqlserver]]
  servers = ["Server=db;User Id=sa;Password=x;"]
  database_type = "SQLServer"
  interval = "60s"

[[inputs.nginx]]
  urls = ["http://a/status"]

[[inputs.nginx]]
  urls = ["http://b/status"]
  response_timeout = "2s"

[[inputs.win_perf_counters]]
  name_prefix = "app."
  interval = "10s"
  [[inputs.win_perf_counters.object]]
    ObjectName = "Process"
    Counters = ["% Processor Time"]
    Instances = ["sqlservr"]
    Measurement = "sqlproc"
'''
    imported = import_managed_config(conf)
    assert imported.ok
    mon = imported.monitoring
    assert not mon.mssql.enabled
    assert mon.nginx.enabled and mon.nginx.urls == ["http://a/status"]
    # the stock counter set is still structured, with the stock process instances intact
    assert mon.win_perf_counters.enabled and mon.win_perf_counters.process_instances == ["_Total", "telegraf", "w3wp"]
    retained = tomllib.loads(mon.custom_toml)["inputs"]
    assert retained["sqlserver"][0]["database_type"] == "SQLServer" and retained["sqlserver"][0]["interval"] == "60s"
    assert retained["nginx"][0] == {"urls": ["http://b/status"], "response_timeout": "2s"}
    assert retained["win_perf_counters"][0]["name_prefix"] == "app."
    assert retained["win_perf_counters"][0]["object"][0]["Measurement"] == "sqlproc"
    rendered = tomllib.loads(TelegrafRenderer.render_system_inputs(mon))
    assert len(rendered["inputs"]["win_perf_counters"]) == 2 and len(rendered["inputs"]["nginx"]) == 2
    assert "Password=***" in "\n".join(imported.summary_lines()) or "Password=x" not in "\n".join(imported.summary_lines())


def test_fragments_merge_retained_tables_instead_of_overwriting():
    conf = MANAGED_CONF.replace("[global_tags]\n", '[global_tags]\n  site = "dc1"\n') + '''
[[processors.rename]]
  [[processors.rename.replace]]
    tag = "host"
    dest = "hostname"
'''
    fragment = '''
[global_tags]
  env = "prod"

[[processors.converter]]
  [processors.converter.fields]
    integer = ["count"]
'''
    imported = import_managed_config(conf, {"extra.conf": fragment})
    retained = tomllib.loads(imported.monitoring.custom_toml)
    assert retained["global_tags"] == {"site": "dc1", "env": "prod"}
    assert {"rename", "converter"} <= set(retained["processors"])


def test_perf_object_options_and_duplicate_names_are_not_lost():
    conf = MANAGED_CONF + '''
[[inputs.win_perf_counters.object]]
  ObjectName = "SQLServer:Buffer Manager"
  Counters = ["Page life expectancy"]
  Instances = ["------"]
  Measurement = "win.sql"
  UseRawValues = true
  WarnOnMissing = true
'''
    imported = import_managed_config(conf)
    extra = {o.object_name: o for o in imported.monitoring.win_perf_counters.additional_objects}
    assert extra["SQLServer:Buffer Manager"].options == {"UseRawValues": True, "WarnOnMissing": True}
    rendered = tomllib.loads(TelegrafRenderer.render_system_inputs(imported.monitoring))
    sql = next(o for o in rendered["inputs"]["win_perf_counters"][0]["object"] if o["ObjectName"] == "SQLServer:Buffer Manager")
    assert sql["UseRawValues"] is True and sql["Instances"] == ["------"]

    dup = MANAGED_CONF + '''
[[inputs.win_perf_counters.object]]
  ObjectName = "Web Service"
  Counters = ["Current Connections"]
  Instances = ["*"]
  Measurement = "iis_a"
[[inputs.win_perf_counters.object]]
  ObjectName = "Web Service"
  Counters = ["Bytes Total/sec"]
  Instances = ["*"]
  Measurement = "iis_b"
'''
    imported = import_managed_config(dup)
    assert not imported.monitoring.win_perf_counters.enabled
    retained = tomllib.loads(imported.monitoring.custom_toml)["inputs"]["win_perf_counters"][0]["object"]
    assert [o["Measurement"] for o in retained if o["ObjectName"] == "Web Service"] == ["iis_a", "iis_b"]
    assert any("repeated" in w for w in imported.warnings)


def test_include_total_difference_is_reported_as_changed():
    conf = MANAGED_CONF.replace('Measurement = "win.net"\n  IncludeTotal = true', 'Measurement = "win.net"\n  IncludeTotal = false')
    imported = import_managed_config(conf)
    assert any("Network Interface: IncludeTotal False becomes True" in c for c in imported.changed)


def test_mask_secrets_handles_toml_strings_and_urls():
    assert mask_secrets('password = "abc"') == 'password = "***"'
    assert mask_secrets("token = 'abc'") == "token = '***'"
    assert mask_secrets("https://user:hunter2@influx:8086") == "https://user:***@influx:8086"
    assert mask_secrets("Server=x;Password=hunter2;log=1") == "Server=x;Password=***;log=1"


def test_counter_and_interval_differences_are_reported():
    conf = MANAGED_CONF.replace('interval = "300s"', 'interval = "60s"').replace(
        'Counters = ["% Usage"]', 'Counters = ["% Usage", "% Usage Peak"]'
    )
    imported = import_managed_config(conf)
    assert any("interval 60s becomes 300s" in c for c in imported.changed)
    extra = [o for o in imported.monitoring.win_perf_counters.additional_objects if o.object_name == "Paging File"]
    assert extra and extra[0].counters == ["% Usage Peak"]
    rendered = tomllib.loads(TelegrafRenderer.render_system_inputs(imported.monitoring))
    paging = next(o for o in rendered["inputs"]["win_perf_counters"][0]["object"] if o["ObjectName"] == "Paging File")
    assert "% Usage Peak" in paging["Counters"] and "% Usage" in paging["Counters"]


def test_unknown_perf_object_is_added():
    conf = MANAGED_CONF + '''
[[inputs.win_perf_counters.object]]
  ObjectName = "Web Service"
  Counters = ["Current Connections"]
  Instances = ["*"]
  Measurement = "win.iis"
'''
    # the extra object must be attached to the array table; append it under the last win_perf_counters
    imported = import_managed_config(conf)
    extra = {o.object_name: o for o in imported.monitoring.win_perf_counters.additional_objects}
    assert extra["Web Service"].counters == ["Current Connections"]
    assert extra["Web Service"].measurement == "win.iis"


def test_non_empty_global_tags_are_retained():
    conf = MANAGED_CONF.replace("[global_tags]\n", '[global_tags]\n  dc = "lab"\n')
    imported = import_managed_config(conf)
    assert "[global_tags] table" in imported.retained
    assert tomllib.loads(imported.monitoring.custom_toml)["global_tags"] == {"dc": "lab"}


def test_other_outputs_are_retained_with_a_warning():
    conf = MANAGED_CONF + '\n[[outputs.file]]\n  files = ["C:\\\\metrics.out"]\n'
    imported = import_managed_config(conf)
    assert any("[[outputs.file]]" in r for r in imported.retained)
    assert any("retained: check" in w for w in imported.warnings)
    assert tomllib.loads(imported.monitoring.custom_toml)["outputs"]["file"][0]["files"] == ["C:\\metrics.out"]


def test_unparseable_fragment_blocks_the_import():
    imported = import_managed_config(MANAGED_CONF, {"broken.conf": "[[inputs.x]\nnot toml"})
    assert not imported.ok
    assert any("broken.conf" in b for b in imported.blocked)
    assert "not toml" not in imported.monitoring.custom_toml
    assert imported.summary_lines()[0] == "Blocked:"


def test_empty_import_falls_back_to_baseline():
    imported = import_managed_config(None)
    assert imported.monitoring == MonitoringConfig.baseline_for_os(True)
    assert imported.warnings


def test_windows_baseline_renders_os_totals_by_default():
    rendered = tomllib.loads(TelegrafRenderer.render_system_inputs(MonitoringConfig.baseline_for_os(True)))
    assert rendered["cpu"] if False else rendered["inputs"]["cpu"][0]["name_prefix"] == "win."
    assert rendered["inputs"]["mem"][0]["name_prefix"] == "win."
    assert rendered["inputs"]["swap"][0]["name_prefix"] == "win."
    linux = tomllib.loads(TelegrafRenderer.render_system_inputs(MonitoringConfig.baseline_for_os(False)))
    assert "name_prefix" not in linux["inputs"]["cpu"][0]


def test_vendor_tags_script_patch_quotes_the_binary_path():
    """Issue #62: the 9.1 vendor script runs %TELEGRAF_BIN_PATH% unquoted and %1 keeps the caller's quotes."""
    patched = VCF91OpenTelegrafIntegration._patch_vendor_tag_script(VENDOR_BAT)
    assert "'\"%TELEGRAF_BIN_PATH%\" --version'" in patched
    assert "set TELEGRAF_BIN_PATH=%~1" in patched
    # idempotent: a second pass adds no more quotes
    assert VCF91OpenTelegrafIntegration._patch_vendor_tag_script(patched) == patched
    # the helper's own script is already quoted and stays byte-identical
    own = VCF91OpenTelegrafIntegration._get_windows_mandatory_tag_script()
    assert VCF91OpenTelegrafIntegration._patch_vendor_tag_script(own) == own


def test_tags_script_default_binary_is_set_for_the_endpoint():
    """The exec line passes no argument, so the script's default line must name this endpoint's binary."""
    from vcf_ops_telegraf_helper.workflow.windows import set_windows_tags_binary

    own = VCF91OpenTelegrafIntegration._get_windows_mandatory_tag_script()
    patched = set_windows_tags_binary(own, r"C:\Program Files\Telegraf\telegraf.exe")
    assert 'set TELEGRAF_BIN_PATH="C:\\Program Files\\Telegraf\\telegraf.exe"\r\n' in patched
    assert patched.count("set TELEGRAF_BIN_PATH=") == 2  # the default line and the %~1 override
    assert patched.count('"%TELEGRAF_BIN_PATH%"') == own.count('"%TELEGRAF_BIN_PATH%"')
    vendor = set_windows_tags_binary(VENDOR_BAT, "C:/telegraf/telegraf.exe")
    assert 'set TELEGRAF_BIN_PATH="C:\\telegraf\\telegraf.exe"' in vendor
    assert "C:\\VMware\\UCP\\ucp-telegraf\\telegraf.exe" not in vendor
    # a script with no default line gets one after @echo off
    bare = "@echo off\r\necho hi\r\n"
    assert set_windows_tags_binary(bare, "C:\\t\\telegraf.exe").split("\r\n")[1] == 'set TELEGRAF_BIN_PATH="C:\\t\\telegraf.exe"'


def test_workflow_renders_exec_without_argument_and_patches_the_script():
    from test_workflow import _create_test_workflow
    from vcf_ops_telegraf_helper.models.endpoint import OSFamily

    wf = _create_test_workflow()
    wf.target.os_family = OSFamily.WINDOWS
    wf.detect_telegraf()
    wf.configure_vcf_output()
    wf.artifacts.mandatory_tags_content = VCF91OpenTelegrafIntegration._get_windows_mandatory_tag_script()
    wf.render_inputs()
    cmd = tomllib.loads(wf.vcf_conf_content)["inputs"]["exec"][0]["commands"][0]
    assert cmd == f'cmd.exe /c "{wf.discovery.config_dir.replace(chr(92), "/")}/mandatory_tags.bat"'
    assert f'set TELEGRAF_BIN_PATH="{wf.discovery.telegraf_bin_path}"' in wf.artifacts.mandatory_tags_content


def test_windows_exec_line_is_quoted_and_passes_no_argument():
    out = TelegrafRenderer.render_vcf_output(
        collector_address="172.27.8.54", hostname="tg-w22-01", vm_mor="vm-6068", vc_id="vc",
        mandatory_tags_path=r"C:\Program Files\Telegraf\telegraf.d\mandatory_tags.bat",
        telegraf_bin_path=r"C:\Program Files\Telegraf\telegraf.exe", is_windows=True,
    )
    cmd = tomllib.loads(out)["inputs"]["exec"][0]["commands"][0]
    assert cmd == 'cmd.exe /c "C:/Program Files/Telegraf/telegraf.d/mandatory_tags.bat"'
    assert cmd.count('"') == 2
