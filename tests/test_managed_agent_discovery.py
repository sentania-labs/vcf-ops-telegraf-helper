"""Discovery of Ops product-managed agents: Ops side (agent object) and endpoint side (services, files).

Footprint and API shapes are the ones recorded in the lab on 2026-10-09 (issue #61).
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest
import requests

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from vcf_ops_telegraf_helper.adapters.mock import MockVCFOpsIntegration
from vcf_ops_telegraf_helper.adapters.vcf91 import VCF91OpenTelegrafIntegration
from vcf_ops_telegraf_helper.executors.base import CommandResult
from vcf_ops_telegraf_helper.executors.mock import MockExecutor
from vcf_ops_telegraf_helper.models.endpoint import ConnectionMethod, EndpointTarget, OSFamily
from vcf_ops_telegraf_helper.models.vcf import CollectorInfo, VCFEnvironment, VirtualMachineResource
from vcf_ops_telegraf_helper.models.workflow import StageStatus
from vcf_ops_telegraf_helper.workflow.windows import (
    MANAGED_GRAINS,
    MANAGED_TELEGRAF_CONF,
    WindowsTelegrafDetection,
    detect_managed_installation,
    is_managed_detection,
)
from vcf_ops_telegraf_helper.gui.probes import probe_endpoint

from test_workflow import _create_test_workflow


# ---------------------------------------------------------------------------
# Ops side
# ---------------------------------------------------------------------------

AGENT_OBJECT = {
    "identifier": "43c01324-5fdb-47dd-97d7-ed47ee17325a",
    "resourceKey": {
        "name": "Windows OS on tg-w22-01", "resourceKindKey": "win",
        "resourceIdentifiers": [
            {"identifierType": {"name": "VCID"}, "value": "5f898d03-vc"},
            {"identifierType": {"name": "VMMOR"}, "value": "vm-6068"},
        ],
    },
    "resourceStatusStates": [{"resourceState": "STARTED", "resourceStatus": "DATA_RECEIVING", "adapterInstanceId": "ai-1"}],
}


def _ops_session(managed_type="Product Managed", stat_keys=137, newest_ms=1_760_000_000_000, agents=None):
    """Suite API fake covering agent objects, their properties, stat keys and latest stats."""
    agents = [AGENT_OBJECT] if agents is None else agents

    def _resp(payload, code=200):
        r = MagicMock()
        r.status_code = code
        r.json.return_value = payload
        return r

    def get(url, headers=None, params=None, timeout=None):
        if url.endswith("/resources/properties"):
            ids = [v for k, v in params if k == "resourceId"]
            return _resp({"resourcePropertiesList": [
                {"resourceId": i, "property": [{"name": "AgentManagedType", "value": managed_type}]} for i in ids
            ]})
        if url.endswith("/resources") and params.get("adapterKind") == "APPOSUCP":
            return _resp({"resourceList": agents, "pageInfo": {"totalCount": len(agents)}})
        if url.endswith("/adapters"):
            return _resp({"adapterInstancesInfoDto": [{"id": "ai-1", "collectorId": 3}]})
        if url.endswith("/collectors"):
            return _resp({"collector": [{"id": "3", "name": "cp01", "hostName": "172.27.8.52", "type": "UNIFIED_CLOUD_PROXY"}]})
        if url.endswith("/collectorGroups") or url.endswith("/collectorgroups"):
            return _resp({"collectorGroups": [{"name": "VCF Lab CP Group 1", "collectorId": [3], "virtualIP": "172.27.8.54"}]})
        if url.endswith("/statkeys"):
            return _resp({"stat-key": [{"key": f"k{i}"} for i in range(stat_keys)]})
        if url.endswith("/stats/latest"):
            return _resp({"values": [{"stat-list": {"stat": [
                {"statKey": {"key": "mem|used.percent"}, "timestamps": [newest_ms - 300_000, newest_ms], "data": [41.2, 41.5]},
                {"statKey": {"key": "cpu|usage.user"}, "timestamps": [newest_ms + 60_000], "data": [None]},
            ]}}]})
        raise AssertionError(f"unexpected GET {url}")

    session = MagicMock(spec=requests.Session)
    session.get.side_effect = get
    return session


def _env():
    return VCFEnvironment(name="t", url="https://ops.local", username="admin", token="tok",
                          collector=CollectorInfo(address="172.27.8.54"))


def test_agent_object_lookup_by_identity_reports_managed_type_keys_and_sample():
    adapter = VCF91OpenTelegrafIntegration(_env(), session=_ops_session())
    obj = adapter.get_agent_object("5f898d03-vc", "vm-6068", include_stat_keys=True)
    assert obj is not None
    assert obj.resource_id == "43c01324-5fdb-47dd-97d7-ed47ee17325a"
    assert obj.name == "Windows OS on tg-w22-01"
    assert obj.resource_kind == "win"
    assert obj.managed_type == "Product Managed" and obj.is_ops_managed
    assert obj.receiving is True
    assert obj.stat_key_count == 137
    # the null sample at a later timestamp must not count
    assert obj.last_sample_ms == 1_760_000_000_000
    assert obj.collector_address == "172.27.8.52"
    assert obj.collector_group == "VCF Lab CP Group 1"


def test_agent_object_lookup_unknown_vm_is_none():
    adapter = VCF91OpenTelegrafIntegration(_env(), session=_ops_session())
    assert adapter.get_agent_object("5f898d03-vc", "vm-9999") is None


def test_agent_object_lookup_raises_when_inventory_unreadable():
    session = _ops_session()
    base = session.get.side_effect

    def get(url, headers=None, params=None, timeout=None):
        if url.endswith("/resources"):
            r = MagicMock()
            r.status_code = 500
            r.json.return_value = {}
            return r
        return base(url, headers=headers, params=params, timeout=timeout)

    session.get.side_effect = get
    adapter = VCF91OpenTelegrafIntegration(_env(), session=session)
    with pytest.raises(RuntimeError):
        adapter.get_agent_object("5f898d03-vc", "vm-6068")


def test_inventory_marks_ops_managed_registrations():
    """The VM list carries AgentManagedType so a takeover can be offered before guest credentials exist."""
    from test_adapters import _fake_suite_api

    session = _fake_suite_api()
    base = session.get.side_effect

    def get(url, headers=None, params=None, timeout=None):
        r = base(url, headers=headers, params=params, timeout=timeout)
        if url.endswith("/resources/properties"):
            payload = r.json.return_value
            for entry in payload["resourcePropertiesList"]:
                if entry["resourceId"] == "a-1":
                    entry["property"].append({"name": "AgentManagedType", "value": "Product Managed"})
        return r

    session.get.side_effect = get
    adapter = VCF91OpenTelegrafIntegration(_env(), session=session)
    vms = {vm.name: vm for vm in adapter.list_virtual_machines()}
    assert vms["win-app01"].telegraf_status == "Reporting"
    assert vms["win-app01"].managed_type == "Product Managed"
    assert vms["win-app01"].is_ops_managed
    assert vms["lin-db01"].managed_type is None


def test_inventory_survives_managed_type_lookup_failure():
    """A failed property read leaves the agent type unknown instead of hiding the registration."""
    from test_adapters import _fake_suite_api

    session = _fake_suite_api()
    base = session.get.side_effect
    calls = {"props": 0}

    def get(url, headers=None, params=None, timeout=None):
        if url.endswith("/resources/properties"):
            calls["props"] += 1
            ids = [v for k, v in params if k == "resourceId"]
            if "a-1" in ids:
                r = MagicMock()
                r.status_code = 503
                return r
        return base(url, headers=headers, params=params, timeout=timeout)

    session.get.side_effect = get
    adapter = VCF91OpenTelegrafIntegration(_env(), session=session)
    win = next(vm for vm in adapter.list_virtual_machines() if vm.name == "win-app01")
    assert win.telegraf_status == "Reporting"
    assert win.managed_type is None and not win.is_ops_managed


def test_verify_ingestion_by_identity_pass_pending_unknown():
    fresh = VCF91OpenTelegrafIntegration(_env(), session=_ops_session(newest_ms=2_000_000))
    assert fresh.verify_ingestion("tg-w22-01", since=1_000.0, vc_id="5f898d03-vc", vm_mor="vm-6068") == "PASS"
    stale = VCF91OpenTelegrafIntegration(_env(), session=_ops_session(newest_ms=500_000))
    assert stale.verify_ingestion("tg-w22-01", since=1_000.0, vc_id="5f898d03-vc", vm_mor="vm-6068") == "PENDING"
    absent = VCF91OpenTelegrafIntegration(_env(), session=_ops_session(agents=[]))
    assert absent.verify_ingestion("tg-w22-01", since=1_000.0, vc_id="5f898d03-vc", vm_mor="vm-9999") == "UNKNOWN"


def test_verify_ingestion_by_identity_ignores_hostname():
    """A matching name on an unrelated registration must not count when the identity is known."""
    other = dict(AGENT_OBJECT, identifier="other")
    other["resourceKey"] = dict(AGENT_OBJECT["resourceKey"], resourceIdentifiers=[
        {"identifierType": {"name": "VCID"}, "value": "vc-other"},
        {"identifierType": {"name": "VMMOR"}, "value": "vm-1"},
    ])
    adapter = VCF91OpenTelegrafIntegration(_env(), session=_ops_session(agents=[other], newest_ms=2_000_000))
    assert adapter.verify_ingestion("tg-w22-01", since=1_000.0, vc_id="5f898d03-vc", vm_mor="vm-6068") == "UNKNOWN"


def test_workflow_verify_uses_vm_identity_before_hostname():
    wf = _create_test_workflow()
    wf.target.vm_mor = "vm-1020"
    wf.target.vc_id = "423b-81f0-91a2-0004"
    calls = []
    real = wf.adapter.verify_ingestion

    def spy(hostname, since=None, vc_id=None, vm_mor=None):
        calls.append((hostname, vc_id, vm_mor))
        return real(hostname, since=since, vc_id=vc_id, vm_mor=vm_mor)

    wf.adapter.verify_ingestion = spy
    summary = wf.run()
    assert summary.success
    assert calls[0][1:] == ("423b-81f0-91a2-0004", "vm-1020")
    assert wf.verifications["VCF Ops ingestion"] == "PASS"


def test_mock_adapter_agent_object_and_managed_vm():
    adapter = MockVCFOpsIntegration(_env())
    managed = next(vm for vm in adapter.list_virtual_machines() if vm.is_ops_managed)
    assert managed.name == "dbdemo01" and managed.os_family == "WINDOWS"
    obj = adapter.get_agent_object(managed.vc_id, managed.vm_mor)
    assert obj is not None and obj.is_ops_managed and obj.resource_kind == "win"
    assert obj.stat_key_count is None
    assert adapter.get_agent_object(managed.vc_id, managed.vm_mor, include_stat_keys=True).stat_key_count == 137
    assert adapter.get_agent_object("nope", "vm-0") is None


# ---------------------------------------------------------------------------
# Endpoint side (Windows Server 2022, VCF Operations 9.1 footprint)
# ---------------------------------------------------------------------------

MANAGED_SERVICES = (
    "salt-minion|Running|C:\\VMware\\UCP\\salt\\nssm.exe\n"
    "ucp-minion|Running|C:\\VMware\\UCP\\salt\\nssm.exe\n"
    "ucp-telegraf|Running|C:\\VMware\\UCP\\ucp-telegraf\\telegraf.exe --config C:\\VMware\\UCP\\ucp-telegraf\\telegraf.conf --config-directory C:\\VMware\\UCP\\ucp-telegraf\\telegraf.d\n"
)
MANAGED_CONF = '[agent]\n  interval = "300s"\n[[outputs.http]]\n  url = "https://172.27.8.54/arc/metric"\n'
MANAGED_GRAINS_TEXT = "arc_fqdn: collector02.lab\narc_virtual_ip: 172.27.8.54\nminion_id: 5f898d03_vm-6068\nvc_id: 5f898d03\nvm_id: vm-6068\n"


class _ManagedEndpoint(MockExecutor):
    """A Windows endpoint carrying the product-managed agent."""

    def __init__(self, with_fragments=True):
        super().__init__(telegraf_installed=True, telegraf_version="Telegraf 1.39.0 (git: vmware-latest-telegraf-arc-fips@abc)")
        self.files = {
            MANAGED_TELEGRAF_CONF: MANAGED_CONF,
            MANAGED_GRAINS: MANAGED_GRAINS_TEXT,
            "C:\\VMware\\UCP\\ucp-telegraf\\mandatory_tags.bat": "@echo off\r\n",
        }
        if with_fragments:
            self.files["C:\\VMware\\UCP\\ucp-telegraf\\telegraf.d\\app.conf"] = "[[inputs.win_services]]\n"

    def execute(self, command, timeout=30):
        self.executed_commands.append(command)
        if "Win32_Service" in command and "ucp-telegraf" in command and "-in @(" in command:
            return CommandResult(exit_code=0, stdout=MANAGED_SERVICES, command=command)
        if "Win32_Service" in command:
            # detect_windows_telegraf: the running managed telegraf is the first hit
            return CommandResult(exit_code=0, stdout=MANAGED_SERVICES.splitlines()[2] + "\n", command=command)
        if "Get-ChildItem" in command and "telegraf.d" in command:
            names = "\n".join(p.rsplit("\\", 1)[1] for p in self.files if "\\telegraf.d\\" in p)
            return CommandResult(exit_code=0, stdout=names + "\n", command=command)
        return super().execute(command, timeout)

    def file_exists(self, path):
        return path in self.files

    def download(self, path):
        return self.files[path]


def test_detect_managed_installation_reads_footprint():
    ex = _ManagedEndpoint()
    found = detect_managed_installation(ex)
    assert found.present
    assert sorted(found.services) == ["salt-minion", "ucp-minion", "ucp-telegraf"]
    assert found.running_services == ["salt-minion", "ucp-minion", "ucp-telegraf"]
    assert found.telegraf_version.startswith("Telegraf 1.39.0")
    assert found.telegraf_conf == MANAGED_CONF
    assert found.telegraf_d == {"app.conf": "[[inputs.win_services]]\n"}
    assert found.mandatory_tags == "@echo off\r\n"
    assert found.grain_values["vm_id"] == "vm-6068"
    assert found.grain_values["vc_id"] == "5f898d03"
    assert found.grain_values["arc_virtual_ip"] == "172.27.8.54"
    assert "C:\\VMware\\UCP" in found.cleanup_paths
    assert "ucp-telegraf (Running)" in found.summary()
    assert not found.read_errors


def test_detect_managed_installation_without_config_read_touches_no_files():
    ex = _ManagedEndpoint()
    found = detect_managed_installation(ex, read_config=False)
    assert found.present and found.telegraf_conf is None and found.telegraf_d == {}
    assert not any("Get-ChildItem" in c for c in ex.executed_commands)


def test_detect_managed_installation_absent_on_plain_oss_endpoint():
    ex = MockExecutor(telegraf_installed=True)
    found = detect_managed_installation(ex, WindowsTelegrafDetection(installed=True, binary_path=r"C:\telegraf\telegraf.exe", service_name="telegraf"))
    assert not found.present
    assert found.summary() == "No Ops-managed agent"


def test_detect_managed_installation_ignores_unrelated_service_lines():
    """Only the known control services or binaries under C:\\VMware\\UCP count."""
    ex = MockExecutor(custom_responses={"Win32_Service": CommandResult(exit_code=0, stdout="vcf-telegraf|Running|C:\\Ops\\telegraf.exe\n", command="")})
    assert not detect_managed_installation(ex, read_config=False).present


def test_is_managed_detection_by_service_or_path():
    assert is_managed_detection(WindowsTelegrafDetection(installed=True, service_name="ucp-telegraf"))
    assert is_managed_detection(WindowsTelegrafDetection(installed=True, binary_path=r"C:\VMware\UCP\ucp-telegraf\telegraf.exe", service_name="telegraf"))
    assert not is_managed_detection(WindowsTelegrafDetection(installed=True, binary_path=r"C:\telegraf\telegraf.exe", service_name="telegraf"))


def test_engine_refuses_managed_endpoint_by_service_name_and_records_it():
    wf = _create_test_workflow()
    wf.target.os_family = OSFamily.WINDOWS
    wf.executor = _ManagedEndpoint()
    res = wf.detect_telegraf()
    assert res.status == StageStatus.FAIL
    assert "managed by VCF Operations" in res.message
    assert "salt-minion, ucp-minion, ucp-telegraf" in res.message
    assert wf.managed_installation is not None and wf.managed_installation.present
    assert not wf.executor.uploaded_files


def test_probe_reports_managed_installation_instead_of_raising():
    target = EndpointTarget(hostname="tg-w22-01", os_family=OSFamily.WINDOWS, connection_method=ConnectionMethod.WINRM, username="a")
    found = probe_endpoint(target, _ManagedEndpoint())
    assert found["managed"] is not None and found["managed"].present
    assert found["installed"] is True
    plain = probe_endpoint(target, MockExecutor(telegraf_installed=True))
    assert plain["managed"] is None


def test_gui_detection_blocks_managed_endpoint_with_service_names(tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from vcf_ops_telegraf_helper.gui.main_window import MainWindow
    from vcf_ops_telegraf_helper.storage.state import StateStore

    QApplication.instance() or QApplication([])
    window = MainWindow(state_store=StateStore(state_file=tmp_path / "state.json"))
    window.ep_os_combo.setCurrentText("Windows")
    window.ep_host_input.setText("tg-w22-01")
    window.ep_user_input.setText("administrator")
    window.ep_pass_input.setText("x")
    monkeypatch.setattr(window, "_create_executor", lambda target: _ManagedEndpoint())
    window._detect_endpoint()
    assert window._endpoint_detected is False
    assert "VCF Operations owns this agent" in window.ep_status_label.text()
    assert "ucp-telegraf" in window.ep_status_label.text()
    assert window.managed_installation is not None
    window.close()


def test_agent_status_text_marks_ops_managed():
    from vcf_ops_telegraf_helper.gui.main_window import MainWindow

    vm = VirtualMachineResource(resource_id="r", name="tg-w22-01", telegraf_status="Reporting", managed_type="Product Managed", agent_registrations=1)
    assert MainWindow._agent_status_text(vm) == "Reporting, Ops managed"
    oss = VirtualMachineResource(resource_id="r", name="x", telegraf_status="Reporting", managed_type="Open Source", agent_registrations=2)
    assert MainWindow._agent_status_text(oss) == "Reporting (2 registrations)"


def test_stock_saltstack_minion_is_not_an_ops_agent():
    """A site-managed SaltStack minion shares the salt-minion name; only the Ops one under C:\\VMware\\UCP counts."""
    line = "salt-minion|Running|C:\\Program Files\\Salt Project\\Salt\\bin\\ssm.exe\n"
    ex = MockExecutor(custom_responses={"-in @(": CommandResult(exit_code=0, stdout=line, command="")})
    found = detect_managed_installation(ex, WindowsTelegrafDetection(installed=True, binary_path=r"C:\Program Files\Telegraf\telegraf.exe", service_name="telegraf"))
    assert not found.present
    wf = _create_test_workflow()
    wf.target.os_family = OSFamily.WINDOWS
    wf.executor = ex
    assert wf.detect_telegraf().status != StageStatus.FAIL


def test_managed_found_by_service_query_alone():
    """Control services under C:\\VMware\\UCP mark the endpoint managed even when no telegraf service is visible."""
    lines = (
        "salt-minion|Running|C:\\VMware\\UCP\\salt\\nssm.exe\n"
        "ucp-minion|Stopped|C:\\VMware\\UCP\\salt\\nssm.exe\n"
    )
    ex = MockExecutor(telegraf_installed=False, custom_responses={"-in @(": CommandResult(exit_code=0, stdout=lines, command="")})
    found = detect_managed_installation(ex, WindowsTelegrafDetection(installed=False), read_config=False)
    assert found.present and sorted(found.services) == ["salt-minion", "ucp-minion"]
    assert found.running_services == ["salt-minion"]
    # no config read requested: no directory probes or version calls
    assert not any("Test-Path" in c for c in ex.executed_commands)


def test_managed_detection_tolerates_non_json_stats():
    session = _ops_session()
    base = session.get.side_effect

    def get(url, headers=None, params=None, timeout=None):
        r = base(url, headers=headers, params=params, timeout=timeout)
        if url.endswith("/stats/latest"):
            r.json.side_effect = ValueError("not json")
        return r

    session.get.side_effect = get
    adapter = VCF91OpenTelegrafIntegration(_env(), session=session)
    obj = adapter.get_agent_object("5f898d03-vc", "vm-6068")
    assert obj is not None and obj.last_sample_ms is None
    assert adapter.verify_ingestion("x", since=1.0, vc_id="5f898d03-vc", vm_mor="vm-6068") == "PENDING"
