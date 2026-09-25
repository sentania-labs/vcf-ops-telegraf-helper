"""Tests for Telegraf configuration renderer."""

from __future__ import annotations

import sys

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
    assert 'hostname = "app01.corp.local"' in rendered

    parsed = tomllib.loads(rendered)
    assert parsed["agent"]["interval"] == "300s"
    assert parsed["outputs"]["http"][0]["data_format"] == "wavefront"
    headers = parsed["outputs"]["http"][0]["headers"]
    assert headers["hostname"] == "app01.corp.local"


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

