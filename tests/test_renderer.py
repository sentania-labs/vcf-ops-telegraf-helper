"""Tests for Telegraf configuration renderer."""

from __future__ import annotations

import sys
import pytest

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

from vcf_ops_telegraf_helper.models.monitoring import (
    CpuInputConfig,
    DiskInputConfig,
    MemInputConfig,
    MonitoringConfig,
    NetInputConfig,
)
from vcf_ops_telegraf_helper.renderer.renderer import MANAGED_HEADER, TelegrafRenderer


def test_render_system_inputs_valid_toml():
    """Verify default monitoring config produces valid, parsable TOML."""
    config = MonitoringConfig(
        cpu=CpuInputConfig(enabled=True),
        mem=MemInputConfig(enabled=True),
        disk=DiskInputConfig(enabled=True),
        net=NetInputConfig(enabled=True),
    )
    rendered = TelegrafRenderer.render_system_inputs(config)

    assert MANAGED_HEADER.strip() in rendered
    assert "[[inputs.cpu]]" in rendered
    assert "[[inputs.mem]]" in rendered
    assert "[[inputs.disk]]" in rendered
    assert "[[inputs.net]]" in rendered

    # Broadcom required settings for Linux OS metrics
    assert "percpu = true" in rendered
    assert "totalcpu = true" in rendered
    assert "collect_cpu_time = true" in rendered
    assert "report_active = true" in rendered

    # Parse with standard tomllib to guarantee validity
    parsed = tomllib.loads(rendered)
    assert "inputs" in parsed
    assert "cpu" in parsed["inputs"]
    assert "mem" in parsed["inputs"]
    assert "disk" in parsed["inputs"]
    assert "net" in parsed["inputs"]


def test_render_vcf_output_valid_toml():
    """Verify VCF Operations Wavefront HTTP output produces valid TOML."""
    rendered = TelegrafRenderer.render_vcf_output(
        collector_address="192.168.1.100",
        hostname="app01.corp.local",
        ip="192.168.1.50",
        verify_ssl=True,
    )

    assert "[[outputs.http]]" in rendered
    assert 'url = "https://192.168.1.100/opensource/default/metric"' in rendered
    assert 'data_format = "wavefront"' in rendered
    assert 'hostname = "app01"' in rendered

    parsed = tomllib.loads(rendered)
    assert parsed["agent"]["interval"] == "300s"
    assert parsed["agent"]["omit_hostname"] is True
    assert parsed["outputs"]["http"][0]["data_format"] == "wavefront"
    headers = parsed["outputs"]["http"][0]["headers"]
    assert headers["hostname"] == "app01"


def test_renderer_idempotency():
    """Verify that multiple renderings with identical input yield identical output."""
    config = MonitoringConfig(
        cpu=CpuInputConfig(enabled=True, percpu=True),
        mem=MemInputConfig(enabled=True),
        disk=DiskInputConfig(enabled=True, mount_points=["/"]),
        net=NetInputConfig(enabled=True, interfaces=["eth0"]),
    )

    run1 = TelegrafRenderer.render_system_inputs(config)
    run2 = TelegrafRenderer.render_system_inputs(config)
    assert run1 == run2

    out1 = TelegrafRenderer.render_vcf_output(
        collector_address="10.0.0.1",
        hostname="node01",
    )
    out2 = TelegrafRenderer.render_vcf_output(
        collector_address="10.0.0.1",
        hostname="node01",
    )
    assert out1 == out2


def test_render_vcf_output_managed_vm():
    """Verify managed vCenter VM output formatting with vmId and vcid headers."""
    rendered = TelegrafRenderer.render_vcf_output(
        collector_address="10.10.10.10",
        hostname="vm-finance-01",
        vm_mor="vm-42",
        vc_id="vc-dc1-prod",
    )

    parsed = tomllib.loads(rendered)
    headers = parsed["outputs"]["http"][0]["headers"]
    assert headers["vmId"] == "vm-42"
    assert headers["vcid"] == "vc-dc1-prod"
    assert headers["hostname"] == "vm-finance-01"


def test_render_vcf_output_windows_paths():
    """Verify Windows backslash paths in TLS parameters produce valid parseable TOML."""
    rendered = TelegrafRenderer.render_vcf_output(
        collector_address="10.10.10.10",
        hostname="win-srv-01",
        ca_cert_path="C:\\telegraf\\telegraf.d\\ca.pem",
        cert_path="C:\\telegraf\\telegraf.d\\cert.pem",
        key_path="C:\\telegraf\\telegraf.d\\key.pem",
    )
    assert 'tls_ca = "C:\\\\telegraf\\\\telegraf.d\\\\ca.pem"' in rendered
    parsed = tomllib.loads(rendered)
    http_out = parsed["outputs"]["http"][0]
    assert http_out["tls_ca"] == "C:\\telegraf\\telegraf.d\\ca.pem"
    assert http_out["tls_cert"] == "C:\\telegraf\\telegraf.d\\cert.pem"
    assert http_out["tls_key"] == "C:\\telegraf\\telegraf.d\\key.pem"


def test_render_system_inputs_windows_escaping():
    """Verify backslash escaping in MSSQL instances and Windows services parses properly in tomllib."""
    from vcf_ops_telegraf_helper.models.monitoring import (
        MssqlInputConfig,
        WinServicesInputConfig,
    )

    cfg = MonitoringConfig(
        mssql=MssqlInputConfig(
            enabled=True,
            servers=["Server=10.0.0.5\\SQLEXPRESS;Port=1433;User Id=sa;Password=secret;"],
        ),
        win_services=WinServicesInputConfig(
            enabled=True,
            service_names=["W32Time", "LanmanServer\\test"],
        ),
    )
    rendered = TelegrafRenderer.render_system_inputs(cfg)
    parsed = tomllib.loads(rendered)
    assert parsed["inputs"]["sqlserver"][0]["servers"][0] == "Server=10.0.0.5\\SQLEXPRESS;Port=1433;User Id=sa;Password=secret;"
    assert parsed["inputs"]["win_services"][0]["service_names"] == ["W32Time", "LanmanServer\\test"]


def test_render_base_stub():
    """Verify render_base_stub produces a valid minimal TOML configuration without inputs."""
    stub = TelegrafRenderer.render_base_stub()
    assert "[agent]" in stub
    assert "omit_hostname = true" in stub
    assert "[[inputs." not in stub

    parsed = tomllib.loads(stub)
    assert parsed["agent"]["interval"] == "300s"
    assert parsed["agent"]["omit_hostname"] is True
    assert "inputs" not in parsed


def test_render_vcf_output_mtls_always_present_when_verify_ssl_false():
    """Verify tls_ca, tls_cert, tls_key, and TLS13 are emitted even when verify_ssl is False (insecure_skip_verify=True)."""
    rendered = TelegrafRenderer.render_vcf_output(
        collector_address="172.27.8.54",
        hostname="console.int.sentania.net",
        ip="172.16.3.87",
        verify_ssl=False,
        ca_cert_path="/etc/telegraf/telegraf.d/ca.pem",
        cert_path="/etc/telegraf/telegraf.d/cert.pem",
        key_path="/etc/telegraf/telegraf.d/key.pem",
    )
    parsed = tomllib.loads(rendered)
    http_out = parsed["outputs"]["http"][0]
    assert http_out["insecure_skip_verify"] is True
    assert http_out["tls_ca"] == "/etc/telegraf/telegraf.d/ca.pem"
    assert http_out["tls_cert"] == "/etc/telegraf/telegraf.d/cert.pem"
    assert http_out["tls_key"] == "/etc/telegraf/telegraf.d/key.pem"
    assert http_out["tls_min_version"] == "TLS13"
    assert http_out["headers"]["hostname"] == "console"


def test_render_vcf_output_with_mandatory_tags():
    """Verify mandatory_tags input is appended and command string is properly escaped."""
    rendered = TelegrafRenderer.render_vcf_output(
        collector_address="172.27.8.54",
        hostname="worker",
        mandatory_tags_path="/etc/telegraf/telegraf.d/mandatory_tags.sh",
        telegraf_bin_path="/usr/bin/telegraf",
        is_windows=False,
    )
    parsed = tomllib.loads(rendered)
    exec_inputs = parsed["inputs"]["exec"]
    assert len(exec_inputs) == 1
    assert exec_inputs[0]["commands"] == ['/bin/bash "/etc/telegraf/telegraf.d/mandatory_tags.sh" "/usr/bin/telegraf"']
    assert exec_inputs[0]["data_format"] == "influx"


def test_render_vcf_output_ip_hostname_not_truncated():
    """Verify IP address hostname is not truncated to first octet."""
    rendered = TelegrafRenderer.render_vcf_output(
        collector_address="172.27.8.54",
        hostname="10.10.10.101",
    )
    parsed = tomllib.loads(rendered)
    assert parsed["agent"]["hostname"] == "10.10.10.101"
    assert parsed["outputs"]["http"][0]["headers"]["hostname"] == "10.10.10.101"


def test_render_vcf_output_windows_cmd_quoting():
    """Verify Windows mandatory_tags command uses forward slashes to avoid backslash escaping issues."""
    rendered = TelegrafRenderer.render_vcf_output(
        collector_address="172.27.8.54",
        hostname="win-node",
        mandatory_tags_path=r"C:\telegraf\telegraf.d\mandatory_tags.bat",
        telegraf_bin_path=r"C:\telegraf\telegraf.exe",
        is_windows=True,
    )
    parsed = tomllib.loads(rendered)
    exec_cmd = parsed["inputs"]["exec"][0]["commands"][0]
    assert exec_cmd == "cmd.exe /c C:/telegraf/telegraf.d/mandatory_tags.bat C:/telegraf/telegraf.exe"


def test_render_vcf_output_windows_cmd_quoting_with_spaces():
    """Verify Windows mandatory_tags command quotes paths containing spaces."""
    rendered = TelegrafRenderer.render_vcf_output(
        collector_address="172.27.8.54",
        hostname="win-node",
        mandatory_tags_path=r"C:\Program Files\Telegraf\mandatory_tags.bat",
        telegraf_bin_path=r"C:\Program Files\Telegraf\telegraf.exe",
        is_windows=True,
    )
    parsed = tomllib.loads(rendered)
    exec_cmd = parsed["inputs"]["exec"][0]["commands"][0]
    assert exec_cmd == 'cmd.exe /c "C:/Program Files/Telegraf/mandatory_tags.bat" "C:/Program Files/Telegraf/telegraf.exe"'


def test_render_vcf_output_omits_tls_cert_when_not_provided():
    """Verify tls_cert and tls_key are omitted when mutual_auth is disabled and certificates not configured."""
    rendered = TelegrafRenderer.render_vcf_output(
        collector_address="172.27.8.54",
        hostname="console",
        cert_path=None,
        key_path=None,
        ca_cert_path=None,
        mutual_auth=False,
    )
    parsed = tomllib.loads(rendered)
    http_out = parsed["outputs"]["http"][0]
    assert "tls_cert" not in http_out
    assert "tls_key" not in http_out


def test_render_vcf_output_emits_five_tls_lines_when_mutual_auth_true():
    """Verify all five tls_ lines are emitted when mutual_auth is true, even with verify_ssl=False."""
    rendered = TelegrafRenderer.render_vcf_output(
        collector_address="172.27.8.54",
        hostname="console.int.sentania.net",
        verify_ssl=False,
        mutual_auth=True,
    )
    parsed = tomllib.loads(rendered)
    http_out = parsed["outputs"]["http"][0]
    assert http_out["insecure_skip_verify"] is True
    assert http_out["tls_ca"] == "/etc/telegraf/telegraf.d/ca.pem"
    assert http_out["tls_cert"] == "/etc/telegraf/telegraf.d/cert.pem"
    assert http_out["tls_key"] == "/etc/telegraf/telegraf.d/key.pem"
    assert http_out["tls_min_version"] == "TLS13"


def test_render_vcf_output_multiline_banner_hostname_valid_toml():
    """Verify hostname containing banner text or newlines does not generate invalid TOML."""
    dirty_hostname = "Authorized uses only.\nWelcome to node01\nnode01.corp.local\n"
    rendered = TelegrafRenderer.render_vcf_output(
        collector_address="172.27.8.54",
        hostname=dirty_hostname,
    )
    parsed = tomllib.loads(rendered)
    assert parsed["agent"]["hostname"] == "node01"
    assert parsed["outputs"]["http"][0]["headers"]["hostname"] == "node01"


def test_render_win_perf_print_valid_and_escaping():
    """Verify WinPerfCounters respects print_valid and escapes process instances."""
    from vcf_ops_telegraf_helper.models.monitoring import (
        MonitoringConfig,
        WinPerfCountersInputConfig,
    )
    cfg = MonitoringConfig(
        win_perf_counters=WinPerfCountersInputConfig(
            enabled=True,
            print_valid=False,
            process_instances=["_Total", "telegraf", 'custom"instance'],
        )
    )
    rendered = TelegrafRenderer.render_system_inputs(cfg)
    assert "PrintValid = false" in rendered
    parsed = tomllib.loads(rendered)
    win_perf = parsed["inputs"]["win_perf_counters"][0]
    assert win_perf["PrintValid"] is False
    # Check that the object with Measurement win_process has the escaped instance
    proc_obj = next(o for o in win_perf["object"] if o.get("Measurement") == "win_process")
    assert 'custom"instance' in proc_obj["Instances"]






def test_additional_perfmon_merges_baseline_and_escapes_counter_names():
    from vcf_ops_telegraf_helper.models.monitoring import WinPerfCountersInputConfig, PerfmonObject
    config = MonitoringConfig(win_perf_counters=WinPerfCountersInputConfig(
        enabled=True, additional_objects=[
            PerfmonObject(object_name='Processor', counters=['% Processor Time', 'Extra "counter"'], measurement='unused'),
            PerfmonObject(object_name='Custom\\Object', counters=['one', 'two'], measurement='custom'),
        ]))
    rendered = TelegrafRenderer.render_system_inputs(config)
    plugins = tomllib.loads(rendered)['inputs']['win_perf_counters']
    assert len(plugins) == 1
    objects = plugins[0]['object']
    processor = [item for item in objects if item['ObjectName'] == 'Processor']
    assert len(processor) == 1
    assert processor[0]['Measurement'] == 'win_cpu'
    assert processor[0]['Counters'].count('% Processor Time') == 1
    assert 'Extra "counter"' in processor[0]['Counters']
    assert any(item['ObjectName'] == 'Custom\\Object' for item in objects)


@pytest.mark.parametrize('instances', [['*'], ['worker']])
def test_additional_process_perfmon_preserves_requested_instances(instances):
    from vcf_ops_telegraf_helper.models.monitoring import WinPerfCountersInputConfig, PerfmonObject
    config = MonitoringConfig(win_perf_counters=WinPerfCountersInputConfig(
        enabled=True, additional_objects=[PerfmonObject(object_name='Process', measurement='win_process', counters=['Extra'], instances=instances)]))
    objects = tomllib.loads(TelegrafRenderer.render_system_inputs(config))['inputs']['win_perf_counters'][0]['object']
    process = next(item for item in objects if item['ObjectName'] == 'Process')
    assert 'Extra' in process['Counters']
    if instances == ['*']:
        assert process['Instances'] == ['*']
    else:
        assert set(process['Instances']) == {'_Total', 'telegraf', 'worker'}
