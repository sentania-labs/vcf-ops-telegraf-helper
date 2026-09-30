"""Tests for discrete validation checks."""

from __future__ import annotations

from vcf_ops_telegraf_helper.executors.mock import MockExecutor
from vcf_ops_telegraf_helper.models.monitoring import (
    CpuInputConfig,
    DiskInputConfig,
    MemInputConfig,
    MonitoringConfig,
    NetInputConfig,
)
from vcf_ops_telegraf_helper.validation.validator import Validator


def test_validate_structured_config():
    """Verify structured config validation catches empty configs."""
    # Config with everything disabled
    empty_cfg = MonitoringConfig(
        cpu=CpuInputConfig(enabled=False),
        mem=MemInputConfig(enabled=False),
        disk=DiskInputConfig(enabled=False),
        net=NetInputConfig(enabled=False),
    )
    empty_cfg.system.enabled = False
    empty_cfg.swap.enabled = False
    empty_cfg.diskio.enabled = False
    empty_cfg.processes.enabled = False

    res = Validator.validate_structured_config(empty_cfg)
    assert not res.is_valid
    assert res.domain == "Structured Configuration"

    # Valid config
    valid_cfg = MonitoringConfig(cpu=CpuInputConfig(enabled=True))
    res_valid = Validator.validate_structured_config(valid_cfg)
    assert res_valid.is_valid


def test_validate_toml_syntax():
    """Verify TOML syntax validation distinguishes valid and invalid fragments."""
    valid_toml = """
    [[inputs.cpu]]
      percpu = true
    """
    res = Validator.validate_toml_syntax(valid_toml, "Test")
    assert res.is_valid

    invalid_toml = """
    [[inputs.cpu
      percpu = true
    """
    res_bad = Validator.validate_toml_syntax(invalid_toml, "Test Bad")
    assert not res_bad.is_valid
    assert "invalid TOML syntax" in res_bad.message


def test_validate_endpoint_connection():
    """Verify endpoint connectivity validation detects connection failures."""
    good_exec = MockExecutor(connected=True)
    assert Validator.validate_endpoint_connection(good_exec).is_valid

    bad_exec = MockExecutor(connected=False)
    assert not Validator.validate_endpoint_connection(bad_exec).is_valid


def test_validate_collector_reachability():
    """Verify collector reachability checks port access."""
    reachable_exec = MockExecutor(collector_reachable=True)
    res_pass = Validator.validate_collector_reachability(reachable_exec, "10.10.10.50")
    assert res_pass.is_valid

    unreachable_exec = MockExecutor(collector_reachable=False)
    res_fail = Validator.validate_collector_reachability(unreachable_exec, "10.10.10.50")
    assert not res_fail.is_valid
    assert "unreachable" in res_fail.message


def test_validate_structured_config_workloads():
    """Verify structured config validation passes when workload or custom plugins are enabled without host plugins."""
    from vcf_ops_telegraf_helper.models.monitoring import (
        DiskIoInputConfig,
        NginxInputConfig,
        ProcessesInputConfig,
        SwapInputConfig,
        SystemInputConfig,
    )

    nginx_cfg = MonitoringConfig(
        cpu=CpuInputConfig(enabled=False),
        mem=MemInputConfig(enabled=False),
        disk=DiskInputConfig(enabled=False),
        net=NetInputConfig(enabled=False),
        system=SystemInputConfig(enabled=False),
        swap=SwapInputConfig(enabled=False),
        diskio=DiskIoInputConfig(enabled=False),
        processes=ProcessesInputConfig(enabled=False),
        nginx=NginxInputConfig(enabled=True),
    )
    res = Validator.validate_structured_config(nginx_cfg)
    assert res.is_valid

    custom_cfg = MonitoringConfig(
        cpu=CpuInputConfig(enabled=False),
        mem=MemInputConfig(enabled=False),
        disk=DiskInputConfig(enabled=False),
        net=NetInputConfig(enabled=False),
        system=SystemInputConfig(enabled=False),
        swap=SwapInputConfig(enabled=False),
        diskio=DiskIoInputConfig(enabled=False),
        processes=ProcessesInputConfig(enabled=False),
        custom_toml="[[inputs.test]]",
    )
    res_custom = Validator.validate_structured_config(custom_cfg)
    assert res_custom.is_valid


def test_validate_collector_reachability_windows():
    """Verify collector reachability runs .NET check on Windows executors."""
    from unittest.mock import MagicMock
    from vcf_ops_telegraf_helper.executors.base import CommandResult

    mock_exec = MagicMock()
    mock_exec.execute.return_value = CommandResult(exit_code=0, stdout="True\r\n", stderr="")
    res = Validator.validate_collector_reachability(mock_exec, "10.10.10.50", port=443, is_windows=True)
    assert res.is_valid
    cmd = mock_exec.execute.call_args[0][0]
    assert "System.Net.Sockets.TcpClient" in cmd


def test_validate_cloudproxy_mtls_metric_probe_linux_success():
    """Verify probe succeeds when curl returns HTTP 200 on Linux."""
    from unittest.mock import MagicMock
    from vcf_ops_telegraf_helper.executors.base import CommandResult

    mock_exec = MagicMock()
    mock_exec.execute.return_value = CommandResult(exit_code=0, stdout="200\n", stderr="")

    res = Validator.validate_cloudproxy_mtls_metric_probe(
        executor=mock_exec,
        collector_address="10.10.10.50",
        cert_path="/etc/telegraf/telegraf.d/cert.pem",
        key_path="/etc/telegraf/telegraf.d/key.pem",
        ca_cert_path="/etc/telegraf/telegraf.d/ca.pem",
        headers={"vmId": "vm-1005", "vcid": "vc-1"},
        hostname="test-host",
        is_windows=False,
        verify_ssl=False,
    )
    assert res.is_valid is True
    assert "HTTP 200" in res.message
    cmd = mock_exec.execute.call_args[0][0]
    assert "curl" in cmd
    assert "--cert /etc/telegraf/telegraf.d/cert.pem" in cmd
    assert "-k" in cmd


def test_validate_cloudproxy_mtls_metric_probe_linux_403_forbidden():
    """Verify probe fails with 403 Forbidden diagnostics when Cloud Proxy rejects credentials."""
    from unittest.mock import MagicMock
    from vcf_ops_telegraf_helper.executors.base import CommandResult

    mock_exec = MagicMock()
    mock_exec.execute.return_value = CommandResult(exit_code=0, stdout="403\n", stderr="")

    res = Validator.validate_cloudproxy_mtls_metric_probe(
        executor=mock_exec,
        collector_address="10.10.10.50",
        cert_path="/etc/telegraf/telegraf.d/cert.pem",
        key_path="/etc/telegraf/telegraf.d/key.pem",
        ca_cert_path="/etc/telegraf/telegraf.d/ca.pem",
        is_windows=False,
    )
    assert res.is_valid is False
    assert "HTTP 403 Forbidden" in res.message


def test_validate_cloudproxy_mtls_metric_probe_connection_error_not_treated_as_port_http_code():
    """Verify connection errors mentioning port 443 are NOT parsed as HTTP 443 status code."""
    from unittest.mock import MagicMock
    from vcf_ops_telegraf_helper.executors.base import CommandResult

    mock_exec = MagicMock()
    mock_exec.execute.return_value = CommandResult(
        exit_code=7,
        stdout="",
        stderr="curl: (7) Failed to connect to 10.10.10.50 port 443: Connection refused",
    )

    res = Validator.validate_cloudproxy_mtls_metric_probe(
        executor=mock_exec,
        collector_address="10.10.10.50",
        cert_path="/etc/telegraf/telegraf.d/cert.pem",
        key_path="/etc/telegraf/telegraf.d/key.pem",
        ca_cert_path="/etc/telegraf/telegraf.d/ca.pem",
        is_windows=False,
    )
    assert res.is_valid is False
    assert "HTTP 443" not in res.message
    assert "probe failed" in res.message


def test_validate_cloudproxy_mtls_metric_probe_windows_curl_missing():
    """Verify probe cleanly fails when curl.exe is not available on Windows."""
    from unittest.mock import MagicMock
    from vcf_ops_telegraf_helper.executors.base import CommandResult

    mock_exec = MagicMock()
    mock_exec.execute.return_value = CommandResult(exit_code=0, stdout="CURL_NOT_FOUND\r\n", stderr="")

    res = Validator.validate_cloudproxy_mtls_metric_probe(
        executor=mock_exec,
        collector_address="10.10.10.50",
        cert_path="C:\\telegraf\\telegraf.d\\cert.pem",
        key_path="C:\\telegraf\\telegraf.d\\key.pem",
        ca_cert_path="C:\\telegraf\\telegraf.d\\ca.pem",
        is_windows=True,
    )
    assert res.is_valid is False
    assert "curl.exe is not available" in res.message


def test_validate_cloudproxy_mtls_metric_probe_windows_schannel_pem_fallback_success():
    """Verify probe succeeds when Windows curl hits Schannel PEM error but port 443 and service are active."""
    from unittest.mock import MagicMock
    from vcf_ops_telegraf_helper.executors.base import CommandResult

    mock_exec = MagicMock()
    mock_exec.execute.side_effect = [
        CommandResult(
            exit_code=35,
            stdout="curl: (35) schannel: Failed to open cert or key file by pathname: 0x80092002",
            stderr="",
        ),
        CommandResult(
            exit_code=0,
            stdout="TCP:True;SVC:Running\r\n",
            stderr="",
        ),
    ]

    res = Validator.validate_cloudproxy_mtls_metric_probe(
        executor=mock_exec,
        collector_address="10.10.10.50",
        cert_path="C:\\telegraf\\telegraf.d\\cert.pem",
        key_path="C:\\telegraf\\telegraf.d\\key.pem",
        ca_cert_path="C:\\telegraf\\telegraf.d\\ca.pem",
        is_windows=True,
    )
    assert res.is_valid is True
    assert "port 443 reachable" in res.message
    assert "Schannel PEM limitation bypassed" in res.message


def test_validate_cloudproxy_mtls_metric_probe_windows_schannel_pem_fallback_tcp_failure():
    """Verify probe reports port 443 unreachable when Windows curl hits Schannel PEM error and TCP test fails."""
    from unittest.mock import MagicMock
    from vcf_ops_telegraf_helper.executors.base import CommandResult

    mock_exec = MagicMock()
    mock_exec.execute.side_effect = [
        CommandResult(
            exit_code=35,
            stdout="curl: (35) schannel: Failed to open cert or key file by pathname: 0x80092002",
            stderr="",
        ),
        CommandResult(
            exit_code=0,
            stdout="TCP:False;SVC:Stopped\r\n",
            stderr="",
        ),
    ]

    res = Validator.validate_cloudproxy_mtls_metric_probe(
        executor=mock_exec,
        collector_address="10.10.10.50",
        cert_path="C:\\telegraf\\telegraf.d\\cert.pem",
        key_path="C:\\telegraf\\telegraf.d\\key.pem",
        ca_cert_path="C:\\telegraf\\telegraf.d\\ca.pem",
        is_windows=True,
    )
    assert res.is_valid is False
    assert "port 443 unreachable" in res.message


def test_validate_cloudproxy_mtls_metric_probe_windows_schannel_pem_fallback_service_stopped():
    """Verify probe reports failure when port 443 is reachable but Telegraf service is stopped."""
    from unittest.mock import MagicMock
    from vcf_ops_telegraf_helper.executors.base import CommandResult

    mock_exec = MagicMock()
    mock_exec.execute.side_effect = [
        CommandResult(
            exit_code=35,
            stdout="curl: (35) schannel: Failed to open cert or key file by pathname: 0x80092002",
            stderr="",
        ),
        CommandResult(
            exit_code=0,
            stdout="TCP:True;SVC:Stopped\r\n",
            stderr="",
        ),
    ]

    res = Validator.validate_cloudproxy_mtls_metric_probe(
        executor=mock_exec,
        collector_address="10.10.10.50",
        cert_path="C:\\telegraf\\telegraf.d\\cert.pem",
        key_path="C:\\telegraf\\telegraf.d\\key.pem",
        ca_cert_path="C:\\telegraf\\telegraf.d\\ca.pem",
        is_windows=True,
    )
    assert res.is_valid is False
    assert "service is stopped or failed" in res.message


