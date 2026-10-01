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
    """Verify artifacts preparation builds official Broadcom URLs and extracts security artifacts."""
    import io
    import zipfile

    env = VCFEnvironment(
        name="test",
        url="https://vcf-ops.corp.local",
        username="admin",
        token="existing-token-999",
        collector=CollectorInfo(address="10.10.10.50"),
        verify_ssl=False,
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("ca.cert.pem", "-----BEGIN CERTIFICATE-----\nCA-DATA\n-----END CERTIFICATE-----\n")
        zf.writestr("client123.cert.pem", "-----BEGIN CERTIFICATE-----\nCLIENT-CERT\n-----END CERTIFICATE-----\n")
        zf.writestr("client123.key", "-----BEGIN RSA PRIVATE KEY-----\nCLIENT-KEY\n-----END RSA PRIVATE KEY-----\n")
        zf.writestr("master.pub", "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQC master-pub\n")
        zf.writestr("IP", "10.10.10.50\n")
        zf.writestr("MUTUAL_AUTHENTICATION", "true\n")
    zip_bytes = buf.getvalue()

    mock_session = MagicMock(spec=requests.Session)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = zip_bytes
    mock_resp.text = "#!/bin/bash\n# tag script"
    mock_resp.json.return_value = {"collectorGroup": [{"name": "Default Group", "id": "1"}]}
    mock_session.get.return_value = mock_resp

    adapter = VCF91OpenTelegrafIntegration(env, session=mock_session)
    artifacts = adapter.prepare_telegraf_integration()

    assert artifacts.token == "existing-token-999"
    assert artifacts.collector_address == "10.10.10.50"
    assert artifacts.script_url == "https://10.10.10.50/downloads/salt/telegraf-utils.sh"
    assert artifacts.output_url == "https://10.10.10.50/opensource/default/metric"
    assert artifacts.skip_certificate is True
    assert "CA-DATA" in (artifacts.ca_cert_content or "")
    assert "CLIENT-CERT" in (artifacts.client_cert_content or "")
    assert "CLIENT-KEY" in (artifacts.client_key_content or "")
    assert "master-pub" in (artifacts.master_pub_content or "")
    assert artifacts.vip_content == "10.10.10.50"
    assert artifacts.collector_group == "Default Group"
    assert artifacts.mutual_auth is True


def test_vcf91_resolve_collector_group_name():
    """Verify resolve_collector_group_name matches collector IP to its collector group."""
    env = VCFEnvironment(
        name="test",
        url="https://vcf-ops.corp.local",
        username="admin",
        token="existing-token-999",
        collector=CollectorInfo(address="172.27.8.54"),
    )
    mock_session = MagicMock(spec=requests.Session)

    def mock_get(url, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        if "collectorGroups" in url or "collectorgroups" in url:
            resp.json.return_value = {
                "collectorGroup": [
                    {"name": "Sentania-CP-Group", "id": "grp-1", "collectorId": ["col-1"]},
                    {"name": "Other-Group", "id": "grp-2", "collectorId": ["col-2"]},
                ]
            }
        elif "collectors" in url:
            resp.json.return_value = {
                "collector": [
                    {"id": "col-1", "name": "cp-01", "ipAddress": "172.27.8.54", "collectorGroupId": "grp-1"},
                    {"id": "col-2", "name": "cp-02", "ipAddress": "10.10.10.20", "collectorGroupId": "grp-2"},
                ]
            }
        return resp

    mock_session.get.side_effect = mock_get
    adapter = VCF91OpenTelegrafIntegration(env, session=mock_session)

    resolved = adapter.resolve_collector_group_name("172.27.8.54")
    assert resolved == "Sentania-CP-Group"


def test_vcf91_resolve_collector_group_name_by_vip():
    """Verify resolve_collector_group_name matches HA VIP on collector group."""
    env = VCFEnvironment(
        name="test",
        url="https://vcf-ops.corp.local",
        username="admin",
        token="existing-token-999",
        collector=CollectorInfo(address="172.27.8.100"),
    )
    mock_session = MagicMock(spec=requests.Session)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "collectorGroup": [
            {"name": "HA-VIP-Group", "id": "grp-vip", "vip": "172.27.8.100"},
        ]
    }
    mock_session.get.return_value = mock_resp
    adapter = VCF91OpenTelegrafIntegration(env, session=mock_session)

    assert adapter.resolve_collector_group_name("172.27.8.100") == "HA-VIP-Group"


def test_vcf91_resolve_collector_group_name_explicit_override():
    """Verify resolve_collector_group_name respects explicit collector name without API calls."""
    env = VCFEnvironment(
        name="test",
        url="https://vcf-ops.corp.local",
        username="admin",
        token="existing-token-999",
        collector=CollectorInfo(address="172.27.8.54", name="ExplicitGroupName"),
    )
    mock_session = MagicMock(spec=requests.Session)
    adapter = VCF91OpenTelegrafIntegration(env, session=mock_session)

    assert adapter.resolve_collector_group_name("172.27.8.54") == "ExplicitGroupName"
    mock_session.get.assert_not_called()


def test_vcf91_fetch_client_certificate_bundle_handles_varied_cert_filenames():
    """Verify bundle parsing correctly identifies client_certificate.pem and does not confuse with CA cert."""
    import io
    import zipfile

    env = VCFEnvironment(
        name="test",
        url="https://vcf-ops.corp.local",
        username="admin",
        token="token-123",
        collector=CollectorInfo(address="10.10.10.50"),
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("ca.cert.pem", "CA-CERT-CONTENT\n")
        # Filename has 'certificate' which contains 'ca' as a substring
        zf.writestr("client_certificate.pem", "CLIENT-CERT-CONTENT\n")
        zf.writestr("client.key", "KEY-CONTENT\n")
        zf.writestr("master.pub", "PUBKEY\n")
        zf.writestr("IP", "10.10.10.50\n")
        zf.writestr("MUTUAL_AUTHENTICATION", "true\n")

    mock_session = MagicMock(spec=requests.Session)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = buf.getvalue()
    mock_session.get.return_value = mock_resp

    adapter = VCF91OpenTelegrafIntegration(env, session=mock_session)
    bundle = adapter.fetch_client_certificate_bundle("TestGroup", "client1")

    assert bundle["ca_cert"] == "CA-CERT-CONTENT\n"
    assert bundle["client_cert"] == "CLIENT-CERT-CONTENT\n"
    assert bundle["client_key"] == "KEY-CONTENT\n"
    assert bundle["master_pub"] == "PUBKEY\n"
    assert bundle["vip"] == "10.10.10.50"
    assert bundle["mutual_auth"] is True


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
    assert "reg query" in script_bat
    assert "BIOS_VERSION" in script_bat
    assert "BOOTSTRAP_FQDN" in script_bat


def test_vcf91_list_virtual_machines():
    """Verify list_virtual_machines parses Suite API resources and identifier properties."""
    env = VCFEnvironment(
        name="test",
        url="https://vcf-ops.corp.local",
        username="admin",
        token="test-token-123",
        collector=CollectorInfo(address="10.10.10.50"),
    )
    mock_session = MagicMock(spec=requests.Session)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "resourceList": [
            {
                "identifier": "res-uuid-001",
                "resourceKey": {
                    "name": "win-app01",
                    "adapterKindKey": "VMWARE",
                    "resourceKindKey": "VirtualMachine",
                    "resourceIdentifiers": [
                        {"identifierType": {"name": "VMEntityName"}, "value": "win-app01"},
                        {"identifierType": {"name": "VMEntityObjectID"}, "value": "vm-201"},
                        {"identifierType": {"name": "VMEntityVCID"}, "value": "vc-uuid-1"},
                    ],
                },
                "resourceProperties": [
                    {"name": "summary|guest|operatingSystem", "value": "Microsoft Windows Server 2022"},
                    {"name": "summary|guest|ipAddress", "value": "192.168.1.100"},
                ],
            }
        ]
    }
    mock_session.get.return_value = mock_resp

    adapter = VCF91OpenTelegrafIntegration(env, session=mock_session)
    vms = adapter.list_virtual_machines()

    assert len(vms) == 1
    assert vms[0].resource_id == "res-uuid-001"
    assert vms[0].name == "win-app01"
    assert vms[0].ip_address == "192.168.1.100"
    assert vms[0].vm_mor == "vm-201"
    assert vms[0].vc_id == "vc-uuid-1"
    assert vms[0].os_family == "WINDOWS"


def test_vcf91_reuse_existing_cert_bundle():
    """Verify prepare_telegraf_integration reuses valid existing cert bundle to avoid cert minting."""
    env = VCFEnvironment(
        name="test",
        url="https://vcf-ops.corp.local",
        username="admin",
        token="test-token-123",
        collector=CollectorInfo(address="10.10.10.50"),
    )
    mock_session = MagicMock(spec=requests.Session)
    adapter = VCF91OpenTelegrafIntegration(env, session=mock_session)

    existing = {
        "client_cert": "-----BEGIN CERTIFICATE-----\nEXISTING-CERT\n-----END CERTIFICATE-----\n",
        "client_key": "-----BEGIN RSA PRIVATE KEY-----\nEXISTING-KEY\n-----END RSA PRIVATE KEY-----\n",
        "ca_cert": "-----BEGIN CERTIFICATE-----\nCA-BUNDLE\n-----END CERTIFICATE-----\n",
        "master_pub": "ssh-rsa PUBKEY",
        "vip": "10.10.10.50",
        "mutual_auth": True,
    }

    artifacts = adapter.prepare_telegraf_integration(
        os_family="linux",
        target_ip="172.16.1.10",
        target_hostname="host10",
        existing_cert_bundle=existing,
    )

    assert artifacts.client_cert_content == existing["client_cert"]
    assert artifacts.client_key_content == existing["client_key"]
    assert artifacts.ca_cert_content == existing["ca_cert"]
    assert artifacts.mutual_auth is True
    # Ensure Suite API cert bundle endpoint was not called
    assert not any("collector-certificate" in str(c) for c in mock_session.get.call_args_list)


def test_mock_adapter_vm_inventory():
    """Verify MockVCFOpsIntegration returns simulated VM resources."""
    from vcf_ops_telegraf_helper.adapters.mock import MockVCFOpsIntegration

    env = VCFEnvironment(
        name="test",
        url="https://vcf-ops.local",
        username="admin",
        collector=CollectorInfo(address="10.10.10.50"),
    )
    adapter = MockVCFOpsIntegration(env)
    vms = adapter.list_virtual_machines()
    assert len(vms) >= 3
    assert any(vm.os_family == "WINDOWS" for vm in vms)
    assert any(vm.os_family == "LINUX" for vm in vms)
    assert any(vm.vm_mor == "vm-1042" for vm in vms)
