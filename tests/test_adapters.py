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


def _vm_res(rid, name, mor, vcid, state="STARTED"):
    return {
        "identifier": rid,
        "resourceKey": {
            "name": name,
            "adapterKindKey": "VMWARE",
            "resourceKindKey": "VirtualMachine",
            "resourceIdentifiers": [
                {"identifierType": {"name": "VMEntityName"}, "value": name},
                {"identifierType": {"name": "VMEntityObjectID"}, "value": mor},
                {"identifierType": {"name": "VMEntityVCID"}, "value": vcid},
            ],
        },
        "resourceStatusStates": [{"resourceState": state, "adapterInstanceId": "vc-adapter"}],
    }


def _fake_suite_api():
    """Session whose GET answers mirror VCF Operations 9.1 response shapes for inventory calls."""
    vms = [
        _vm_res("r-win", "win-app01", "vm-201", "vc-1"),
        _vm_res("r-lin", "lin-db01", "vm-202", "vc-1"),
        _vm_res("r-tmpl", "ubuntu-template", "vm-203", "vc-1"),
        _vm_res("r-gone", "deleted-vm", "vm-204", "vc-1", state="NOT_EXISTING"),
    ]
    props = {
        "r-win": {"summary|runtime|powerState": "Powered On", "summary|config|isTemplate": "false",
                  "summary|guest|ipAddress": "192.168.1.100", "summary|guest|hostName": "win-app01.corp.local",
                  "config|guestFullName": "Microsoft Windows Server 2022 (64-bit)"},
        "r-lin": {"summary|runtime|powerState": "Powered Off", "summary|config|isTemplate": "false",
                  "summary|guest|ipAddress": "none", "config|guestFullName": "Ubuntu Linux (64-bit)"},
        "r-tmpl": {"summary|runtime|powerState": "Powered Off", "summary|config|isTemplate": "true"},
    }
    agents = [{
        "identifier": "a-1",
        "resourceKey": {
            "name": "Windows OS on win-app01", "resourceKindKey": "win",
            "resourceIdentifiers": [
                {"identifierType": {"name": "VCID"}, "value": "vc-1"},
                {"identifierType": {"name": "VMMOR"}, "value": "vm-201"},
            ],
        },
        "resourceStatusStates": [{"resourceState": "STARTED", "resourceStatus": "DATA_RECEIVING", "adapterInstanceId": "ai-1"}],
    }]

    def _resp(payload):
        r = MagicMock()
        r.status_code = 200
        r.json.return_value = payload
        return r

    def get(url, headers=None, params=None, timeout=None):
        if url.endswith("/resources/properties"):
            ids = [v for k, v in params if k == "resourceId"]
            return _resp({"resourcePropertiesList": [
                {"resourceId": i, "property": [{"name": k, "value": v} for k, v in props.get(i, {}).items()]} for i in ids
            ]})
        if url.endswith("/resources") and params.get("adapterKind") == "APPOSUCP":
            return _resp({"resourceList": agents, "pageInfo": {"totalCount": len(agents)}})
        if url.endswith("/resources"):
            return _resp({"resourceList": vms, "pageInfo": {"totalCount": len(vms)}})
        if url.endswith("/adapters"):
            return _resp({"adapterInstancesInfoDto": [{"id": "ai-1", "collectorId": 3}]})
        if url.endswith("/collectors"):
            return _resp({"collector": [
                {"id": "3", "name": "cp01", "hostName": "10.0.0.52", "type": "UNIFIED_CLOUD_PROXY"},
                {"id": "4", "name": "cp02", "hostName": "10.0.0.53", "type": "UNIFIED_CLOUD_PROXY"},
                {"id": "5", "name": "ops-node", "hostName": "10.0.0.42", "type": "INTERNAL"},
            ]})
        if url.endswith("/collectorGroups") or url.endswith("/collectorgroups"):
            return _resp({"collectorGroups": [
                {"name": "CP Group 1", "collectorId": [3, 4], "virtualIP": "10.0.0.54"},
                {"name": "Default collector group", "collectorId": [5]},
            ]})
        raise AssertionError(f"unexpected GET {url}")

    session = MagicMock(spec=requests.Session)
    session.get.side_effect = get
    return session


def _api_env():
    return VCFEnvironment(
        name="test",
        url="https://vcf-ops.corp.local",
        username="admin",
        token="test-token-123",
        collector=CollectorInfo(address="10.10.10.50"),
    )


def test_vcf91_list_virtual_machines():
    """Inventory reads guest details from bulk properties, drops templates and deleted VMs, and joins agent status."""
    adapter = VCF91OpenTelegrafIntegration(_api_env(), session=_fake_suite_api())
    vms = {vm.name: vm for vm in adapter.list_virtual_machines()}

    assert set(vms) == {"win-app01", "lin-db01"}
    win = vms["win-app01"]
    assert win.ip_address == "192.168.1.100"
    assert win.hostname == "win-app01.corp.local"
    assert win.os_family == "WINDOWS"
    assert win.is_powered_on is True
    assert win.vm_mor == "vm-201" and win.vc_id == "vc-1"
    assert win.telegraf_status == "Reporting"
    assert win.agent_registrations == 1
    assert win.collector_address == "10.0.0.52"
    assert win.collector_group == "CP Group 1"

    lin = vms["lin-db01"]
    assert lin.ip_address is None  # "none" from VMware Tools means no address
    assert lin.os_family == "LINUX"
    assert lin.is_powered_on is False
    assert lin.telegraf_status == "Not installed"
    assert lin.collector_group is None


def test_vcf91_list_collector_targets():
    """Only groups with cloud proxies are offered, via their virtual IP, plus each proxy."""
    adapter = VCF91OpenTelegrafIntegration(_api_env(), session=_fake_suite_api())
    targets = [(t.address, t.name, t.is_collector_group, t.display_name) for t in adapter.list_collector_targets()]
    assert targets == [
        ("10.0.0.54", "CP Group 1", True, None),
        ("10.0.0.52", "CP Group 1", False, "cp01"),
        ("10.0.0.53", "CP Group 1", False, "cp02"),
    ]

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


def _cert_adapter(group_name: str):
    env = VCFEnvironment(
        name="test",
        url="https://vcf-ops.corp.local",
        username="admin",
        token="test-token-123",
        collector=CollectorInfo(address="10.10.10.50"),
    )
    adapter = VCF91OpenTelegrafIntegration(env, session=MagicMock(spec=requests.Session))
    adapter.resolve_collector_group_name = MagicMock(return_value=group_name)
    adapter.detect_managed_vm = MagicMock(return_value=(False, None, None, None))
    adapter.fetch_mandatory_tag_script = MagicMock(return_value="#!/bin/sh\n")
    return adapter


_BUNDLE = {
    "ca_cert": "CA",
    "client_cert": "-----BEGIN CERTIFICATE-----\nGROUP\n-----END CERTIFICATE-----\n",
    "client_key": "KEY",
    "mutual_auth": True,
}


def test_vcf91_cert_fallback_skipped_when_group_request_succeeds():
    """The collector-address retry must not run, or fail onboarding, after the group request worked."""
    adapter = _cert_adapter("prod-collector-group")

    def _fetch(group, client_id):
        if group == "prod-collector-group":
            return dict(_BUNDLE)
        raise RuntimeError("collector address rejected")

    adapter.fetch_client_certificate_bundle = MagicMock(side_effect=_fetch)
    artifacts = adapter.prepare_telegraf_integration(target_ip="172.16.1.10", target_hostname="host10")
    assert artifacts.client_cert_content == _BUNDLE["client_cert"]
    assert adapter.fetch_client_certificate_bundle.call_count == 1


def test_vcf91_cert_fallback_used_when_group_request_fails():
    """When the group request fails, the collector address is tried."""
    adapter = _cert_adapter("prod-collector-group")

    def _fetch(group, client_id):
        if group == "10.10.10.50":
            return dict(_BUNDLE)
        raise RuntimeError("unknown group")

    adapter.fetch_client_certificate_bundle = MagicMock(side_effect=_fetch)
    artifacts = adapter.prepare_telegraf_integration(target_ip="172.16.1.10", target_hostname="host10")
    assert artifacts.client_cert_content == _BUNDLE["client_cert"]
    assert adapter.fetch_client_certificate_bundle.call_count == 2


def test_vcf91_explicit_vm_binding_skips_ip_discovery():
    """A supplied MOR and vCenter ID bind the endpoint without IP or hostname matching."""
    adapter = _cert_adapter("10.10.10.50")
    adapter.fetch_client_certificate_bundle = MagicMock(return_value=dict(_BUNDLE))
    artifacts = adapter.prepare_telegraf_integration(
        target_ip="172.16.1.10", target_hostname="host10", vm_mor="vm-201", vc_id="vc-uuid-1"
    )
    adapter.detect_managed_vm.assert_not_called()
    assert artifacts.is_managed_vm is True
    assert artifacts.client_id == "vc-uuid-1_vm-201"
    adapter.fetch_client_certificate_bundle.assert_called_once_with("10.10.10.50", "vc-uuid-1_vm-201")


def test_vcf91_vm_binding_mor_only_resolution():
    """A bare MOR resolves through inventory, and fails loudly when missing, deleted, or ambiguous."""
    import pytest

    adapter = _cert_adapter("10.10.10.50")
    adapter._fetch_paged_resources = MagicMock(return_value=[
        _vm_res("r1", "app01", "vm-201", "vc-1"),
        _vm_res("r2", "x", "vm-300", "vc-1"),
        _vm_res("r3", "gone", "vm-400", "vc-1", state="NOT_EXISTING"),
    ])
    assert adapter.resolve_bound_vm("vm-201") == (True, "app01", "vc-1", "vm-201")

    with pytest.raises(RuntimeError, match="not found"):
        adapter.resolve_bound_vm("vm-999")
    with pytest.raises(RuntimeError, match="not found"):
        adapter.resolve_bound_vm("vm-400")

    adapter._fetch_paged_resources = MagicMock(return_value=[
        _vm_res("r1", "a", "vm-201", "vc-1"),
        _vm_res("r2", "b", "vm-201", "vc-2"),
    ])
    with pytest.raises(RuntimeError, match="more than one vCenter"):
        adapter.resolve_bound_vm("vm-201")

def test_vcf91_cert_reuse_requires_matching_client_identity():
    """A cert issued for a different client identity is not reused; a matching one is."""
    adapter = _cert_adapter("10.10.10.50")
    adapter.fetch_client_certificate_bundle = MagicMock(return_value=dict(_BUNDLE))
    old = {"client_cert": "OLD-CERT", "client_key": "OLD-KEY", "client_id": "endpoint_host10"}

    artifacts = adapter.prepare_telegraf_integration(
        target_ip="172.16.1.10", target_hostname="host10", existing_cert_bundle=old,
        vm_mor="vm-201", vc_id="vc-uuid-1",
    )
    assert artifacts.client_cert_content == _BUNDLE["client_cert"]
    adapter.fetch_client_certificate_bundle.assert_called_once()

    adapter.fetch_client_certificate_bundle.reset_mock()
    same = dict(old, client_id="vc-uuid-1_vm-201")
    artifacts = adapter.prepare_telegraf_integration(
        target_ip="172.16.1.10", target_hostname="host10", existing_cert_bundle=same,
        vm_mor="vm-201", vc_id="vc-uuid-1",
    )
    assert artifacts.client_cert_content == "OLD-CERT"
    adapter.fetch_client_certificate_bundle.assert_not_called()


def test_vcf91_legacy_cert_without_identity_reminted_only_for_explicit_binding():
    """Certs from before CLIENT_ID was recorded are reused, unless a VM binding is requested."""
    adapter = _cert_adapter("10.10.10.50")
    adapter.fetch_client_certificate_bundle = MagicMock(return_value=dict(_BUNDLE))
    legacy = {"client_cert": "OLD-CERT", "client_key": "OLD-KEY"}

    artifacts = adapter.prepare_telegraf_integration(
        target_ip="172.16.1.10", target_hostname="host10", existing_cert_bundle=legacy,
    )
    assert artifacts.client_cert_content == "OLD-CERT"

    artifacts = adapter.prepare_telegraf_integration(
        target_ip="172.16.1.10", target_hostname="host10", existing_cert_bundle=legacy,
        vm_mor="vm-201", vc_id="vc-uuid-1",
    )
    assert artifacts.client_cert_content == _BUNDLE["client_cert"]


def test_vcf91_vm_binding_reports_inventory_failure_not_missing_vm():
    """An inventory API failure must not be reported as the VM being absent."""
    import pytest

    adapter = _cert_adapter("10.10.10.50")
    bad = MagicMock()
    bad.status_code = 500
    adapter.session.get.return_value = bad
    with pytest.raises(RuntimeError, match="inventory query failed") as exc:
        adapter.resolve_bound_vm("vm-201")
    assert "not found" not in str(exc.value)


def test_vcf91_vm_binding_mor_without_vc_id_message():
    """A MOR present in inventory without a vCenter ID gets a specific message."""
    import pytest

    adapter = _cert_adapter("10.10.10.50")
    res = _vm_res("r", "a", "vm-201", "vc-1")
    res["resourceKey"]["resourceIdentifiers"] = [
        i for i in res["resourceKey"]["resourceIdentifiers"] if i["identifierType"]["name"] != "VMEntityVCID"
    ]
    adapter._fetch_paged_resources = MagicMock(return_value=[res])
    with pytest.raises(RuntimeError, match="has no vCenter ID"):
        adapter.resolve_bound_vm("vm-201")


def _patched_api(overrides):
    """Fake Suite API where specific URL suffixes return a given status code or payload."""
    base = _fake_suite_api().get.side_effect

    def get(url, headers=None, params=None, timeout=None):
        for suffix, (code, payload) in overrides.items():
            key_ok = suffix(url, params) if callable(suffix) else url.endswith(suffix)
            if key_ok:
                r = MagicMock()
                r.status_code = code
                r.json.return_value = payload
                r.text = ""
                return r
        return base(url, headers=headers, params=params, timeout=timeout)

    session = MagicMock(spec=requests.Session)
    session.get.side_effect = get
    return session


def test_vcf91_agent_lookup_failure_degrades_to_unknown():
    """If agent objects cannot be read, VMs are still listed with status Unknown and a warning."""
    apposucp = lambda url, params: url.endswith("/resources") and (params or {}).get("adapterKind") == "APPOSUCP"  # noqa: E731
    adapter = VCF91OpenTelegrafIntegration(_api_env(), session=_patched_api({apposucp: (404, {})}))
    vms = adapter.list_virtual_machines(strict=True)
    assert {vm.name for vm in vms} == {"win-app01", "lin-db01"}
    assert {vm.telegraf_status for vm in vms} == {"Unknown"}
    assert "Agent status unavailable" in adapter.inventory_warning


def test_vcf91_collector_query_failure_raises_for_targets():
    """A failed collector query is an error, not an empty list of cloud proxies."""
    import pytest

    adapter = VCF91OpenTelegrafIntegration(_api_env(), session=_patched_api({"/collectors": (500, {})}))
    with pytest.raises(RuntimeError, match="collector query failed"):
        adapter.list_collector_targets()


def test_vcf91_proxy_without_address_is_skipped_and_vip_keys():
    """A proxy with no hostName is skipped instead of breaking the list; vip key variants are honoured."""
    adapter = VCF91OpenTelegrafIntegration(_api_env(), session=_patched_api({
        "/collectors": (200, {"collector": [
            {"id": "3", "name": "cp01", "hostName": "10.0.0.52", "type": "UNIFIED_CLOUD_PROXY"},
            {"id": "4", "name": "cp02", "hostName": None, "type": "UNIFIED_CLOUD_PROXY"},
        ]}),
        "/collectorGroups": (200, {"collectorGroups": [{"name": "CP Group 1", "collectorId": [3, 4], "vip": "10.0.0.54"}]}),
    }))
    targets = [(t.address, t.is_collector_group) for t in adapter.list_collector_targets()]
    assert targets == [("10.0.0.54", True), ("10.0.0.52", False)]


def test_vcf91_agent_status_uses_agent_adapter_state():
    """Agent status comes from the application monitoring adapter's state, not whichever state is listed first."""
    agent = {
        "identifier": "a-1",
        "resourceKey": {
            "name": "Windows OS on win-app01", "resourceKindKey": "win",
            "resourceIdentifiers": [
                {"identifierType": {"name": "VCID"}, "value": "vc-1"},
                {"identifierType": {"name": "VMMOR"}, "value": "vm-201"},
            ],
        },
        "resourceStatusStates": [
            {"resourceState": "STARTED", "resourceStatus": "NO_DATA_RECEIVING", "adapterInstanceId": "other"},
            {"resourceState": "STARTED", "resourceStatus": "DATA_RECEIVING", "adapterInstanceId": "ai-1"},
        ],
    }
    apposucp = lambda url, params: url.endswith("/resources") and (params or {}).get("adapterKind") == "APPOSUCP"  # noqa: E731
    adapter = VCF91OpenTelegrafIntegration(_api_env(), session=_patched_api({apposucp: (200, {"resourceList": [agent]})}))
    win = next(vm for vm in adapter.list_virtual_machines() if vm.name == "win-app01")
    assert win.telegraf_status == "Reporting"
    assert win.collector_address == "10.0.0.52"


def test_vcf91_verify_credentials_rejects_401():
    """A reachable API that answers 401 must not count as validated credentials."""
    import pytest

    adapter = VCF91OpenTelegrafIntegration(_api_env(), session=_patched_api({"/versions/current": (401, {})}))
    with pytest.raises(RuntimeError, match="rejected the credentials"):
        adapter.verify_credentials()

    ok = VCF91OpenTelegrafIntegration(_api_env(), session=_patched_api({"/versions/current": (200, {})}))
    ok.verify_credentials()


def test_vcf91_stale_agent_object_is_not_a_registration():
    """An agent object whose states are all NOT_EXISTING does not count as an installed agent."""
    agent = {
        "identifier": "a-1",
        "resourceKey": {
            "name": "Windows OS on win-app01", "resourceKindKey": "win",
            "resourceIdentifiers": [
                {"identifierType": {"name": "VCID"}, "value": "vc-1"},
                {"identifierType": {"name": "VMMOR"}, "value": "vm-201"},
            ],
        },
        "resourceStatusStates": [{"resourceState": "NOT_EXISTING", "resourceStatus": "NO_DATA_RECEIVING", "adapterInstanceId": "ai-1"}],
    }
    apposucp = lambda url, params: url.endswith("/resources") and (params or {}).get("adapterKind") == "APPOSUCP"  # noqa: E731
    adapter = VCF91OpenTelegrafIntegration(_api_env(), session=_patched_api({apposucp: (200, {"resourceList": [agent]})}))
    win = next(vm for vm in adapter.list_virtual_machines() if vm.name == "win-app01")
    assert win.telegraf_status == "Not installed"
    assert win.agent_registrations == 0
    assert win.collector_address is None


def test_vcf91_collector_group_virtualIP_accepted_at_enrollment():
    """A group VIP reported as virtualIP (the 9.1 spelling) is a valid collector target when enrolling."""
    env = VCFEnvironment(
        name="test",
        url="https://vcf-ops.corp.local",
        username="admin",
        token="test-token-123",
        collector=CollectorInfo(address="10.0.0.54"),
    )
    adapter = VCF91OpenTelegrafIntegration(env, session=MagicMock(spec=requests.Session))
    adapter.get_collector_groups = MagicMock(return_value=[
        {"name": "CP Group 1", "collectorId": [3, 4], "virtualIP": "10.0.0.54"},
        {"name": "CP Group 2", "collectorId": [7], "virtualIP": "10.0.1.54"},
    ])
    adapter.get_collectors = MagicMock(return_value=[{"id": "3", "name": "cp01", "hostName": "10.0.0.52"}])
    adapter.resolve_collector_group_name = MagicMock(return_value="CP Group 1")
    adapter.detect_managed_vm = MagicMock(return_value=(False, None, None, None))
    adapter.fetch_mandatory_tag_script = MagicMock(return_value="#!/bin/sh\n")
    adapter.fetch_client_certificate_bundle = MagicMock(return_value=dict(_BUNDLE))

    artifacts = adapter.prepare_telegraf_integration(target_ip="172.16.1.10", target_hostname="host10")
    assert artifacts.collector_address == "10.0.0.54"


def test_vcf91_unregistered_ip_not_matched_by_first_octet():
    """An address that only shares a first octet with a known collector is not accepted."""
    import pytest

    env = VCFEnvironment(
        name="test",
        url="https://vcf-ops.corp.local",
        username="admin",
        token="test-token-123",
        collector=CollectorInfo(address="10.9.9.9"),
    )
    adapter = VCF91OpenTelegrafIntegration(env, session=MagicMock(spec=requests.Session))
    adapter.get_collector_groups = MagicMock(return_value=[
        {"name": "CP Group 1", "collectorId": [3], "virtualIP": "10.0.0.54"},
        {"name": "CP Group 2", "collectorId": [7], "virtualIP": "10.0.1.54"},
    ])
    adapter.get_collectors = MagicMock(return_value=[{"id": "3", "name": "cp01", "hostName": "10.0.0.52"}])
    with pytest.raises(RuntimeError, match="is not registered"):
        adapter.prepare_telegraf_integration(target_ip="172.16.1.10", target_hostname="host10")

