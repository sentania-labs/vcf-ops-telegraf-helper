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
