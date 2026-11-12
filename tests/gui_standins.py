"""Stand-ins for running the GUI without anything real: a pretend VCF Operations and pretend machines.

Nothing here contacts a network. Every name and address is synthetic. The window under test is
the real one; only its three seams are replaced: the VCF Operations adapter, the machine
executors, and the certificate reader. The GUI tests use these, and so does a throwaway run of
the app for a walkthrough.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional

from vcf_ops_telegraf_helper.adapters.base import CertificateNotTrusted, CredentialsRejected
from vcf_ops_telegraf_helper.adapters.mock import MockVCFOpsIntegration
from vcf_ops_telegraf_helper.executors.base import CommandResult
from vcf_ops_telegraf_helper.executors.mock import MockExecutor
from vcf_ops_telegraf_helper.models.discovery import DiscoveredDatabase, DiscoveredPerfmonSet, DiscoveredService
from vcf_ops_telegraf_helper.models.vcf import AgentObjectInfo, AgentTaskStatus, CollectorInfo, VirtualMachineResource
from vcf_ops_telegraf_helper.renderer.renderer import TelegrafRenderer
from vcf_ops_telegraf_helper.security.certs import PresentedCertificate
from vcf_ops_telegraf_helper.storage.history import ActivityEntry, ActivityRow
from vcf_ops_telegraf_helper.workflow.collect import USUAL_SET, CollectChoices, monitoring_for
from test_takeover import ManagedWindowsEndpoint

DELAY = float(os.environ.get("STANDIN_DELAY", "0"))
CERT_SHA = "5f" * 32
GROUP = CollectorInfo(address="10.20.0.50", name="Main site", is_collector_group=True)
MAIN_CP = CollectorInfo(address="10.20.0.51", name="Main site", display_name="main-cp-01")
BRANCH = CollectorInfo(address="10.30.0.51", display_name="branch-cp-01")
COLLECTORS = [GROUP, MAIN_CP, BRANCH]
ALL = {c.address for c in COLLECTORS}

# name, address, system, os family, extras
MACHINES = [
    ("web-prod-01", "10.20.1.11", "Ubuntu 24.04", "LINUX", {}),
    ("web-prod-02", "10.20.1.12", "Ubuntu 24.04", "LINUX", {}),
    ("db-pg-01", "10.20.2.21", "Red Hat Enterprise Linux 9", "LINUX", {}),
    ("db-sql-02", "10.20.2.32", "Windows Server 2025", "WINDOWS", {}),
    ("app-win-01", "10.20.3.41", "Windows Server 2022", "WINDOWS", {"managed": True}),
    ("k8s-node-01", "10.20.4.51", "Ubuntu 22.04", "LINUX", {}),
    ("build-runner-03", "10.20.5.63", "Ubuntu 24.04", "LINUX", {}),
    ("files-01", "10.20.6.71", "Windows Server 2019", "WINDOWS", {}),
    ("legacy-app-01", "10.20.7.80", "Ubuntu 20.04", "LINUX", {"off": True}),
    ("vCLS-4f2a9c1e", "10.20.0.11", "vSphere cluster service machine", "LINUX", {"platform": "vCLS cluster agent VM"}),
    ("SupervisorControlPlaneVM (1)", "10.20.0.21", "Supervisor control machine", "LINUX",
     {"platform": "Supervisor control plane VM"}),
]


def vc_id(index: int) -> str:
    return f"423b-81f0-91a2-{index:04d}"


class World:
    """Everything the stand-ins share. One per throwaway run."""

    def __init__(self) -> None:
        self.connect = "ok"            # ok | unreachable | signin-once | cert | none
        self.refused_once = False
        self.reporting = {"web-prod-01"}
        self.pending: set = set()      # set up, first data not shown yet
        self.pending_reads: Dict[str, int] = {}
        self.taken_over = False
        self.retired = False
        self.task_polls = 0
        self.object_polls = 0
        self.executors: Dict[str, MockExecutor] = {}
        self.build()

    def build(self) -> None:
        usual = TelegrafRenderer.render_system_inputs(monitoring_for(CollectChoices(keys=list(USUAL_SET)), "linux"))
        for index, (name, address, _system, family, extra) in enumerate(MACHINES, start=1):
            if extra.get("managed"):
                grains = (f"arc_virtual_ip: {GROUP.address}\nminion_id: {vc_id(index)}_vm-{1000 + index}\n"
                          f"vc_id: {vc_id(index)}\nvm_id: vm-{1000 + index}\nvm_name: {name}\n")
                executor = ManagedWindows(self, name, grains)
            elif family == "WINDOWS":
                executor = Windows(self, name)
            else:
                executor = Linux(self, name)
            self.executors[address] = executor
        web1 = self.executors["10.20.1.11"]
        web1.telegraf_installed, web1.telegraf_version = True, "Telegraf 1.40.1"
        web1.uploaded_files["/etc/telegraf/telegraf.d/vcf-helper-system.conf"] = usual
        web1.procs = ["nginx"]
        web1.web = {"nginx": "http://localhost/nginx_status"}
        web2 = self.executors["10.20.1.12"]
        web2.telegraf_installed, web2.telegraf_version = True, "Telegraf 1.30.0"
        web2.procs = ["nginx"]
        web2.web = {"nginx": "http://localhost/nginx_status"}
        self.executors["10.20.2.21"].procs = ["postgres"]
        self.executors["10.20.2.21"].telegraf_installed = False
        self.executors["10.20.4.51"].reach = {BRANCH.address}
        self.executors["10.20.4.51"].telegraf_installed = False
        runner = self.executors["10.20.5.63"]
        runner.accepts = {"runner"}
        runner.telegraf_installed = False
        self.executors["10.20.2.32"].services = [("MSSQLSERVER", "SQL Server (MSSQLSERVER)"), ("SQLServerAgent", "SQL Server Agent"),
                                                 ("W3SVC", "World Wide Web Publishing Service"),
                                                 ("WinRM", "Windows Remote Management")]
        self.executors["10.20.6.71"].services = [("LanmanServer", "Server (file sharing)"), ("WinRM", "Windows Remote Management")]

    def machine(self, name: str) -> MockExecutor:
        return next(e for e in self.executors.values() if e.name == name)


class _Machine(MockExecutor):
    """A pretend machine. It remembers what was done to it for the whole run."""

    def __init__(self, world: World, name: str, **kwargs):
        super().__init__(telegraf_installed=False, **kwargs)
        self.world = world
        self.name = name
        self.accepts: Optional[set] = None   # None: any user name
        self.reach = set(ALL)
        self.internet = True
        self.user = ""
        self.password = ""
        self.connection_error = ""

    def sign_in_as(self, user: Optional[str], password: Optional[str], key: Optional[str]) -> None:
        self.user, self.password, self.key = user or "", password or "", key

    def test_connection(self) -> bool:
        time.sleep(DELAY * 6)
        if not self.user or (self.accepts is not None and self.user not in self.accepts) or self.password == "wrong":
            self.connection_error = "Authentication failed: the user name or password was not accepted"
            return False
        self.connection_error = ""
        return True

    def _reach(self, command: str) -> Optional[CommandResult]:
        if "dl.influxdata.com" in command and ("-I https://" in command or "TcpClient" in command):
            if self.internet:
                return CommandResult(exit_code=0, stdout="True\n", command=command)
            return CommandResult(exit_code=7, stdout="False\n", stderr="Could not resolve host", command=command)
        found = re.search(r"https://([0-9.]+):443/|Connect\('([0-9.]+)', 443\)", command)
        if found:
            address = found.group(1) or found.group(2)
            if address in self.reach:
                return CommandResult(exit_code=0, stdout="True\n200\n", command=command)
            return CommandResult(exit_code=7, stdout="False\n", stderr="Failed to connect", command=command)
        return None


class Linux(_Machine):
    def __init__(self, world: World, name: str):
        super().__init__(world, name)
        self.procs: List[str] = []
        self.web: Dict[str, str] = {}

    def execute(self, command, timeout=30):
        time.sleep(DELAY)
        reach = self._reach(command)
        if reach is not None:
            self.executed_commands.append(command)
            return reach
        if "pgrep -x" in command:
            return CommandResult(exit_code=0, stdout="".join(f"proc:{p}\n" for p in self.procs), command=command)
        if 'echo "web:$u"' in command:
            for key, url in self.web.items():
                if key in command.lower() or "Active connections" in command and key == "nginx":
                    return CommandResult(exit_code=0, stdout=f"web:{url}\n", command=command)
            return CommandResult(exit_code=0, stdout="", command=command)
        if "sha256sum -c" in command and "tar -xzf" in command:
            time.sleep(DELAY * 20)
            self.telegraf_installed, self.service_active = True, True
            found = re.search(r"telegraf-([0-9.]+)_linux", command)
            if found:
                self.telegraf_version = f"Telegraf {found.group(1)}"
            self.executed_commands.append(command)
            return CommandResult(exit_code=0, stdout="", command=command)
        if ("apt-get install" in command or "tar -xz" in command) and "telegraf" in command:
            time.sleep(DELAY * 20)
            if not self.internet:
                self.executed_commands.append(command)
                return CommandResult(exit_code=6, stderr="curl: (6) Could not resolve host: dl.influxdata.com", command=command)
        if "apt-get purge" in command or "dnf remove" in command:
            self.world.reporting.discard(self.name)
        return super().execute(command, timeout)

    def discover_databases(self, db_type="postgresql", auth_mode="password", username=None, password=None, port=5432):
        time.sleep(DELAY * 10)
        if password == "wrong" or not username:
            return []
        return [DiscoveredDatabase(name="orders"), DiscoveredDatabase(name="billing"), DiscoveredDatabase(name="postgres", db_type="system")]


class Windows(_Machine):
    def __init__(self, world: World, name: str):
        super().__init__(world, name)
        self.services: List[tuple] = []

    def execute(self, command, timeout=30):
        time.sleep(DELAY)
        reach = self._reach(command)
        if reach is not None:
            self.executed_commands.append(command)
            return reach
        if "IsInRole" in command:
            return CommandResult(exit_code=0, stdout="True\n", command=command)
        if "Get-FileHash" in command and "telegraf" in command:
            time.sleep(DELAY * 20)
            self.telegraf_installed, self.service_active = True, True
            self.executed_commands.append(command)
            return CommandResult(exit_code=0, stdout="", command=command)
        if "Invoke-WebRequest" in command and "telegraf" in command:
            time.sleep(DELAY * 20)
            if not self.internet:
                self.executed_commands.append(command)
                return CommandResult(exit_code=1, stderr="The remote name could not be resolved: 'dl.influxdata.com'", command=command)
        if "service uninstall" in command or "sc.exe delete" in command:
            self.world.reporting.discard(self.name)
        return super().execute(command, timeout)

    def discover_services(self):
        time.sleep(DELAY * 8)
        return [DiscoveredService(name=n, display_name=d, status="Running", start_type="Automatic") for n, d in self.services] + [
            DiscoveredService(name="telegraf", display_name="Telegraf", status="Running")]

    def discover_perfmon_sets(self):
        time.sleep(DELAY * 8)
        return [DiscoveredPerfmonSet(name="SQLServer:General Statistics", description="connections and logins", counters=["User Connections", "Logins/sec"]),
                DiscoveredPerfmonSet(name="Web Service", description="requests", counters=["Current Connections", "Total Method Requests/sec"]),
                DiscoveredPerfmonSet(name="PhysicalDisk", description="disk queues", counters=["Current Disk Queue Length"]),
                DiscoveredPerfmonSet(name="Network Adapter", description="adapters in detail", counters=["Bytes Total/sec"])]

    def discover_databases(self, db_type="mssql", auth_mode="sql", username=None, password=None, port=1433):
        time.sleep(DELAY * 10)
        if password == "wrong" or not username:
            return []
        return [DiscoveredDatabase(name="OperationsDB"), DiscoveredDatabase(name="BillingDB"), DiscoveredDatabase(name="master", db_type="system")]


class ManagedWindows(ManagedWindowsEndpoint):
    """app-win-01: carries VCF Operations' own agent until the takeover retires it."""

    def __init__(self, world: World, name: str, grains: str):
        super().__init__(grains=grains)
        self.world = world
        self.name = name
        self.reach = set(ALL)
        self.internet = True
        self.accepts = None
        self.user = self.password = ""
        self.services: List[tuple] = [("W3SVC", "World Wide Web Publishing Service")]

    sign_in_as = _Machine.sign_in_as
    test_connection = _Machine.test_connection
    _reach = _Machine._reach
    discover_services = Windows.discover_services

    def execute(self, command, timeout=30):
        time.sleep(DELAY)
        reach = self._reach(command)
        if reach is not None:
            self.executed_commands.append(command)
            return reach
        if "IsInRole" in command:
            return CommandResult(exit_code=0, stdout="True\n", command=command)
        if "Invoke-WebRequest" in command and "telegraf" in command:
            time.sleep(DELAY * 30)
        return super().execute(command, timeout)


class StandInOps(MockVCFOpsIntegration):
    """A pretend VCF Operations 9.1 backed by the shared World."""

    world: World

    def __init__(self, env, world: World):
        super().__init__(env)
        self.world = world

    def validate_connection(self) -> bool:
        time.sleep(DELAY * 10)
        mode = self.world.connect
        if mode == "unreachable":
            return False
        if mode == "cert" and self.env.verify_ssl:
            pinned = (self.env.pinned_cert_sha256 or "").lower()
            file_ok = bool(self.env.ca_cert_path and Path(self.env.ca_cert_path).name == "company-root-ca.pem")
            if pinned != CERT_SHA and not file_ok:
                raise CertificateNotTrusted("TLS certificate verification failed for VCF Operations")
        return True

    def verify_credentials(self) -> None:
        time.sleep(DELAY * 6)
        if self.env.password == "wrong" or self.env.token == "wrong":
            raise CredentialsRejected("VCF Operations rejected the credentials (HTTP 401)")
        if self.world.connect == "signin-once" and not self.world.refused_once:
            self.world.refused_once = True
            raise CredentialsRejected("VCF Operations rejected the credentials (HTTP 401)")

    def list_auth_sources(self):
        return ["corp.example.com"]

    def list_virtual_machines(self, strict: bool = False):
        time.sleep(DELAY * 12)
        world = self.world
        if world.connect == "none":
            return []
        # Data that was pending shows up a little later: on the second reading of the list after the setup
        for name in list(world.pending):
            world.pending_reads[name] = world.pending_reads.get(name, 0) + 1
            if world.pending_reads[name] >= 2:
                world.pending.discard(name)
                world.pending_reads.pop(name, None)
                world.reporting.add(name)
        found = []
        for index, (name, address, system, family, extra) in enumerate(MACHINES, start=1):
            reporting = name in world.reporting
            managed = bool(extra.get("managed")) and not world.taken_over
            status = "Reporting" if (reporting or managed or (extra.get("managed") and world.taken_over)) else "Not installed"
            found.append(VirtualMachineResource(
                resource_id=f"res-vm-{index:03d}", name=name, ip_address=address, hostname=name, vm_mor=f"vm-{1000 + index}",
                vc_id=vc_id(index), os_name=system, os_family=family,
                power_state="Powered Off" if extra.get("off") else "Powered On",
                collector_group="Main site" if status == "Reporting" else None,
                collector_address=MAIN_CP.address if status == "Reporting" else None,
                telegraf_status=status, agent_registrations=1 if status == "Reporting" else 0,
                managed_type=("Product Managed" if managed else "Open Source") if status == "Reporting" else None,
                platform_kind=extra.get("platform"),
            ))
        return found

    def list_collector_targets(self):
        return list(COLLECTORS)

    def verify_ingestion(self, target_hostname, since=None, vc_id=None, vm_mor=None):
        world = self.world
        name = next((n for i, (n, *_r) in enumerate(MACHINES, start=1) if f"vm-{1000 + i}" == vm_mor), target_hostname)
        family = next((f for n, _a, _s, f, _e in MACHINES if n == name), "LINUX")
        if name in world.reporting:
            return "PASS"
        if family == "WINDOWS" and name not in world.pending:
            world.pending.add(name)   # a Windows machine's first data takes a few minutes
            return "UNKNOWN"
        if name in world.pending:
            world.pending.discard(name)
        world.reporting.add(name)
        return "PASS"

    def resolve_vm_resource_id(self, vc_id, vm_mor):
        return next((f"res-vm-{i:03d}" for i in range(1, len(MACHINES) + 1) if f"vm-{1000 + i}" == vm_mor), None)

    def uninstall_managed_agent(self, vm_resource_id, guest_username, guest_password, retain_config=False):
        self.world.task_polls = 0
        return "task-standin-1"

    def get_agent_task_status(self, task_id):
        world = self.world
        world.task_polls += 1
        if world.task_polls >= 3:
            if not world.retired:
                world.retired = True
                world.machine("app-win-01").retire()
            return AgentTaskStatus(task_id=task_id, stage="FINISHED")
        return AgentTaskStatus(task_id=task_id, stage="SUBMITTING")

    def get_agent_object(self, vc_id, vm_mor, include_stat_keys=False):
        world = self.world
        if vm_mor != "vm-1005":
            return None
        flipped = False
        if world.retired:
            world.object_polls += 1
            flipped = world.object_polls > 2
            if flipped:
                world.taken_over = True
                world.reporting.add("app-win-01")
        return AgentObjectInfo(
            resource_id="agent-vm-1005", name="Windows OS on app-win-01", resource_kind="win",
            managed_type="Open Source" if flipped else "Product Managed", receiving=True,
            stat_key_count=137 if include_stat_keys else None,
            last_sample_ms=int(time.time() * 1000) + (5_000 if flipped else -600_000),
            collector_address=MAIN_CP.address, collector_group="Main site",
        )


def fake_certificate(url: str) -> PresentedCertificate:
    host = re.sub(r"^https?://", "", url).split("/")[0]
    return PresentedCertificate(host=host, issued_to=host, issued_by="Synthetic Corp Internal CA",
                                valid_until="March 4, 2028", sha256=CERT_SHA, self_signed=False)


def fake_package(version: str, is_windows: bool, is_arm: bool):
    folder = Path(tempfile.gettempdir()) / "standin-packages"
    folder.mkdir(exist_ok=True)
    name = f"telegraf-{version}_{'windows' if is_windows else 'linux'}_{'arm64' if is_arm else 'amd64'}.{'zip' if is_windows else 'tar.gz'}"
    path = folder / name
    path.write_bytes(b"stand-in package " + version.encode())
    time.sleep(DELAY * 20)
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def seed_history(window, world: World) -> None:
    """The past the mockup assumes: one machine set up on Nov 3, one that failed yesterday."""
    command = ("vcf-telegraf-helper run --replace-inputs \\\n  --vcf-url https://ops.example.com --vcf-user admin --vcf-pass \"<password>\" \\\n"
               "  --vm-name {name} --vm-id {mor} --vc-id {vc} \\\n  --target-host {addr} --os linux --connection ssh \\\n"
               "  --ssh-user svc-monitor --ssh-pass \"<password>\" \\\n"
               "  --collector 10.20.0.50 --collector-group 'Main site' --install-telegraf --telegraf-version 1.40.1 \\\n"
               "  --cpu --mem --disk --net --system --swap")
    choices = {"collect": CollectChoices(keys=list(USUAL_SET)).model_dump()}
    now = time.time()
    nov3 = time.mktime(time.strptime(f"{time.localtime().tm_year}-11-03 10:20", "%Y-%m-%d %H:%M"))
    if nov3 > now - 2 * 86400:
        nov3 = now - 9 * 86400
    detail = ('Collecting: Processor use, Memory use, Disk space, Network traffic, Load and uptime, Swap use. '
              'Data goes to the collector group "Main site".')
    window.history.add(ActivityEntry(
        id="seed-nov3", at=nov3, title="Set up monitoring", kind="setup", detail=detail, choices=choices,
        log="== web-prod-01 ==\n[1/8 Connecting] PASS    Connected to 10.20.1.11 (ssh)\n[8/8 Verifying] PASS    Verification completed",
        report="# Telegraf Helper: Set up monitoring\n",
        rows=[ActivityRow(machine_id="res-vm-001", name="web-prod-01", os="linux", ok=True,
                          msg="Set up. Data is arriving in VCF Operations.", collecting=list(USUAL_SET),
                          collector='the collector group "Main site"', telegraf="1.40.1",
                          cmd=command.format(name="web-prod-01", mor="vm-1001", vc=vc_id(1), addr="10.20.1.11"))]))
    window.history.remember("res-vm-001", collecting=list(USUAL_SET), collector='the collector group "Main site"', telegraf="1.40.1")
    window.history.add(ActivityEntry(
        id="seed-yesterday", at=now - 86400 + 3600, title="Set up monitoring", kind="setup",
        detail=detail.replace("Collecting", "Would have collected").replace("goes", "was to go"), choices=choices,
        log="== k8s-node-01 ==\n[5/8 Validating config] FAIL    Collector 10.20.0.50:443 not reachable from target",
        report="# Telegraf Helper: Set up monitoring\n",
        rows=[ActivityRow(machine_id="res-vm-006", name="k8s-node-01", os="linux", ok=False,
                          msg="The machine could not reach the collector (where its data is sent).",
                          todo="A collector is the part of VCF Operations that receives the measurements. A firewall between "
                               "the machine and the collector is the usual cause. Press Try again: the app tests every collector "
                               "from this machine and offers one it can reach.",
                          cmd=command.format(name="k8s-node-01", mor="vm-1006", vc=vc_id(6), addr="10.20.4.51"))]))


def _with_reports(window) -> None:
    from vcf_ops_telegraf_helper.workflow.batch import _report

    for entry in window.history.entries():
        if entry.id.startswith("seed-"):
            entry.report = _report(entry.title, entry.rows, entry.detail, [])
            window.history.add(entry)


def make_window(home: Path, world: Optional[World] = None, seed: bool = True, waits: Optional[Dict[str, int]] = None):
    """The real window with its three seams pointed at the stand-ins, keeping all its files under `home`."""
    from vcf_ops_telegraf_helper.gui.main_window import MainWindow
    from vcf_ops_telegraf_helper.storage.state import StateStore

    world = world or World()
    window = MainWindow(StateStore(state_file=Path(home) / "state" / "state.json"))
    window.world = world
    window.make_adapter = lambda env: StandInOps(env, world)

    def make_executor(target):
        executor = world.executors[target.hostname]
        executor.sign_in_as(target.username, target.password, target.key_filename)
        return executor

    window._create_executor = make_executor
    window.fetch_certificate = fake_certificate
    window.package_fetcher = fake_package
    window.takeover_waits = waits or {"uninstall_poll_seconds": 1, "continuity_poll_seconds": 1, "continuity_wait_seconds": 40}
    if seed and not window.history.entries():
        seed_history(window, world)
        _with_reports(window)
    return window, world
