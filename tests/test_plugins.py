"""Tests for expanded monitoring plugin models and Telegraf TOML rendering."""

from __future__ import annotations

from vcf_ops_telegraf_helper.models.monitoring import (
    ApacheInputConfig,
    CpuInputConfig,
    DiskIoInputConfig,
    DockerInputConfig,
    MemInputConfig,
    MonitoringConfig,
    MssqlInputConfig,
    MysqlInputConfig,
    NginxInputConfig,
    PingInputConfig,
    PostgresqlInputConfig,
    ProcessesInputConfig,
    WinPerfCountersInputConfig,
    WinServicesInputConfig,
)
from vcf_ops_telegraf_helper.renderer.renderer import TelegrafRenderer
from vcf_ops_telegraf_helper.validation.validator import Validator


def test_render_all_plugins_valid_toml():
    """Verify all plugins render compliant, valid TOML configurations."""
    mon = MonitoringConfig(
        cpu=CpuInputConfig(enabled=True),
        mem=MemInputConfig(enabled=True),
        diskio=DiskIoInputConfig(enabled=True, devices=["sda", "sdb"]),
        processes=ProcessesInputConfig(enabled=True),
        win_perf_counters=WinPerfCountersInputConfig(enabled=True),
        win_services=WinServicesInputConfig(enabled=True, service_names=["telegraf", "wuauserv"]),
        nginx=NginxInputConfig(enabled=True, urls=["http://127.0.0.1/nginx_status"]),
        apache=ApacheInputConfig(enabled=True, urls=["http://127.0.0.1/server-status?auto"]),
        mysql=MysqlInputConfig(enabled=True, servers=["tcp(127.0.0.1:3306)/"]),
        postgresql=PostgresqlInputConfig(enabled=True, address="host=127.0.0.1 user=postgres"),
        mssql=MssqlInputConfig(enabled=True, servers=["Server=127.0.0.1;Port=1433;User Id=sa;Password=secret;"]),
        docker=DockerInputConfig(enabled=True, endpoint="unix:///var/run/docker.sock"),
        ping=PingInputConfig(enabled=True, urls=["1.1.1.1"], count=2),
        custom_toml='[[inputs.net_response]]\n  protocol = "tcp"\n  address = "127.0.0.1:80"',
    )

    rendered = TelegrafRenderer.render_system_inputs(mon)
    assert "[[inputs.cpu]]" in rendered
    assert "[[inputs.diskio]]" in rendered
    assert 'devices = ["sda", "sdb"]' in rendered
    assert "[[inputs.win_perf_counters]]" in rendered
    assert "[[inputs.win_services]]" in rendered
    assert 'service_names = ["telegraf", "wuauserv"]' in rendered
    assert "[[inputs.nginx]]" in rendered
    assert "[[inputs.apache]]" in rendered
    assert "[[inputs.mysql]]" in rendered
    assert "[[inputs.postgresql]]" in rendered
    assert "[[inputs.sqlserver]]" in rendered
    assert "[[inputs.docker]]" in rendered
    assert "[[inputs.ping]]" in rendered
    assert "[[inputs.net_response]]" in rendered

    val_res = Validator.validate_toml_syntax(rendered, "All Plugins TOML")
    assert val_res.is_valid is True


def test_render_selective_plugins():
    """Verify disabled plugins are not included in output TOML."""
    mon = MonitoringConfig(
        cpu=CpuInputConfig(enabled=False),
        mem=MemInputConfig(enabled=True),
        nginx=NginxInputConfig(enabled=True, urls=["http://localhost/status"]),
    )

    rendered = TelegrafRenderer.render_system_inputs(mon)
    assert "[[inputs.cpu]]" not in rendered
    assert "[[inputs.mem]]" in rendered
    assert "[[inputs.nginx]]" in rendered
    assert "[[inputs.apache]]" not in rendered
