"""Tests for VCF Operations integration adapters."""

from __future__ import annotations

from unittest.mock import MagicMock
import requests

from vcf_ops_telegraf_helper.adapters.vcf91 import VCF91OpenTelegrafIntegration
from vcf_ops_telegraf_helper.models.vcf import CollectorInfo, VCFEnvironment


def test_vcf91_validate_connection_success():
    """Verify validate_connection returns True when Suite API returns HTTP 200."""
    env = VCFEnvironment(
        name="test",
        url="https://vcf-ops.corp.local",
        username="admin",
        collector=CollectorInfo(address="10.10.10.50"),
    )
    mock_session = MagicMock(spec=requests.Session)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_session.get.return_value = mock_resp

    adapter = VCF91OpenTelegrafIntegration(env, session=mock_session)
    assert adapter.validate_connection()
    mock_session.get.assert_called_with(
        "https://vcf-ops.corp.local/suite-api/api/versions",
        timeout=10,
        headers={"Accept": "application/json"},
    )


def test_vcf91_validate_connection_failure():
    """Verify validate_connection returns False when host is unreachable."""
    env = VCFEnvironment(
        name="test",
        url="https://vcf-ops.corp.local",
        username="admin",
        collector=CollectorInfo(address="10.10.10.50"),
    )
    mock_session = MagicMock(spec=requests.Session)
    mock_session.get.side_effect = requests.ConnectionError("Connection refused")

    adapter = VCF91OpenTelegrafIntegration(env, session=mock_session)
    assert not adapter.validate_connection()


def test_vcf91_detect_version():
    """Verify version detection parses release info from Suite API."""
    env = VCFEnvironment(
        name="test",
        url="https://vcf-ops.corp.local",
        username="admin",
        collector=CollectorInfo(address="10.10.10.50"),
    )
    mock_session = MagicMock(spec=requests.Session)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"releaseName": "9.1.1.0"}
    mock_session.get.return_value = mock_resp

    adapter = VCF91OpenTelegrafIntegration(env, session=mock_session)
    version = adapter.detect_version()
    assert version == "9.1.1.0"


def test_vcf91_acquire_token():
    """Verify token acquisition issues POST to Suite API token acquire path."""
    env = VCFEnvironment(
        name="test",
        url="https://vcf-ops.corp.local",
        username="admin",
        collector=CollectorInfo(address="10.10.10.50"),
    )
    mock_session = MagicMock(spec=requests.Session)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"token": "suite-token-12345"}
    mock_session.post.return_value = mock_resp

    adapter = VCF91OpenTelegrafIntegration(env, session=mock_session)
    token_obj = adapter.acquire_token("admin", "secretpass")

    assert token_obj.token == "suite-token-12345"
    assert env.token == "suite-token-12345"


def test_vcf91_prepare_artifacts():
    """Verify artifacts preparation builds official Broadcom URLs."""
    env = VCFEnvironment(
        name="test",
        url="https://vcf-ops.corp.local",
        username="admin",
        token="existing-token-999",
        collector=CollectorInfo(address="10.10.10.50"),
        verify_ssl=False,
    )
    adapter = VCF91OpenTelegrafIntegration(env)
    artifacts = adapter.prepare_telegraf_integration()

    assert artifacts.token == "existing-token-999"
    assert artifacts.collector_address == "10.10.10.50"
    assert artifacts.script_url == "https://10.10.10.50/downloads/salt/telegraf-utils.sh"
    assert artifacts.output_url == "https://10.10.10.50/opensource/default/metric"
    assert artifacts.skip_certificate is True
