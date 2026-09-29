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


def test_vcf91_detect_managed_vm_found():
    """Verify managed VM detection parses vCenter identifiers from Suite API."""
    env = VCFEnvironment(
        name="test",
        url="https://vcf-ops.corp.local",
        username="admin",
        token="test-token",
        collector=CollectorInfo(address="10.10.10.50"),
    )
    mock_session = MagicMock(spec=requests.Session)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "resourceList": [
            {
                "resourceKey": {
                    "name": "worker",
                    "resourceIdentifiers": [
                        {"identifierType": {"name": "VMEntityName"}, "value": "worker"},
                        {"identifierType": {"name": "VMEntityVCID"}, "value": "vc-uuid-12345"},
                        {"identifierType": {"name": "VMEntityObjectID"}, "value": "vm-31164"},
                    ],
                }
            }
        ]
    }
    mock_session.post.return_value = mock_resp

    adapter = VCF91OpenTelegrafIntegration(env, session=mock_session)
    is_managed, vm_name, vcid, vm_mor = adapter.detect_managed_vm(target_ip="172.16.3.87")

    assert is_managed is True
    assert vm_name == "worker"
    assert vcid == "vc-uuid-12345"
    assert vm_mor == "vm-31164"


def test_vcf91_detect_managed_vm_not_found():
    """Verify detect_managed_vm returns False when endpoint is unmanaged physical host or absent in vCenter."""
    env = VCFEnvironment(
        name="test",
        url="https://vcf-ops.corp.local",
        username="admin",
        token="test-token",
        collector=CollectorInfo(address="10.10.10.50"),
    )
    mock_session = MagicMock(spec=requests.Session)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"resourceList": []}
    mock_session.post.return_value = mock_resp
    mock_session.get.return_value = mock_resp

    adapter = VCF91OpenTelegrafIntegration(env, session=mock_session)
    is_managed, vm_name, vcid, vm_mor = adapter.detect_managed_vm(target_ip="10.10.10.99", target_hostname="baremetal")

    assert is_managed is False
    assert vcid is None
    assert vm_mor is None


def test_vcf91_fetch_client_certificate_bundle_success():
    """Verify fetch_client_certificate_bundle extracts CA cert, client cert, key, and mutual auth."""
    import io
    import zipfile

    env = VCFEnvironment(
        name="test",
        url="https://vcf-ops.corp.local",
        username="admin",
        token="test-token",
        collector=CollectorInfo(address="10.10.10.50"),
    )

    # Build mock zip archive in memory
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("ca.cert.pem", "-----BEGIN CERTIFICATE-----\nCA-DATA\n-----END CERTIFICATE-----\n")
        zf.writestr("client123.cert.pem", "-----BEGIN CERTIFICATE-----\nCLIENT-CERT\n-----END CERTIFICATE-----\n")
        zf.writestr("client123.key", "-----BEGIN RSA PRIVATE KEY-----\nCLIENT-KEY\n-----END RSA PRIVATE KEY-----\n")
        zf.writestr("MUTUAL_AUTHENTICATION", "true\n")
    zip_bytes = buf.getvalue()

    mock_session = MagicMock(spec=requests.Session)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = zip_bytes
    mock_session.get.return_value = mock_resp

    adapter = VCF91OpenTelegrafIntegration(env, session=mock_session)
    bundle = adapter.fetch_client_certificate_bundle("collector-vip", "client123")

    assert "CA-DATA" in bundle["ca_cert"]
    assert "CLIENT-CERT" in bundle["client_cert"]
    assert "CLIENT-KEY" in bundle["client_key"]
    assert bundle["mutual_auth"] is True


def test_vcf91_fetch_mandatory_tag_script_fallback():
    """Verify fetch_mandatory_tag_script returns robust embedded fallback when Cloud Proxy download fails."""
    env = VCFEnvironment(
        name="test",
        url="https://vcf-ops.corp.local",
        username="admin",
        collector=CollectorInfo(address="10.10.10.50"),
    )
    mock_session = MagicMock(spec=requests.Session)
    mock_session.get.side_effect = requests.ConnectionError("Offline")

    adapter = VCF91OpenTelegrafIntegration(env, session=mock_session)
    script_sh = adapter.fetch_mandatory_tag_script("10.10.10.50", os_family="linux")
    assert "mandatory.tag" in script_sh
    assert "TELEGRAF_VERSION" in script_sh

    script_bat = adapter.fetch_mandatory_tag_script("10.10.10.50", os_family="windows")
    assert "mandatory.tag" in script_bat
    assert "TELEGRAF_VERSION" in script_bat
