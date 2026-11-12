"""The check a machine gets before anything is done to it. It changes nothing.

For one machine: sign in (working out how to reach it), see what is installed and what
software is running, test the collector it would send to and, when that one cannot be
reached, every other collector, and test whether the machine can download Telegraf. The
answer is a set of facts plus, when something is in the way, what happened, what to do and
which fix to offer.
"""

from __future__ import annotations

import re
import time
from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel, Field

from vcf_ops_telegraf_helper.executors.base import EndpointExecutor
from vcf_ops_telegraf_helper.logger import get_logger
from vcf_ops_telegraf_helper.models.endpoint import ConnectionMethod, EndpointTarget, OSFamily
from vcf_ops_telegraf_helper.models.vcf import CollectorInfo
from vcf_ops_telegraf_helper.utils import UpgradeDecision, evaluate_telegraf_upgrade, parse_telegraf_version
from vcf_ops_telegraf_helper.validation.validator import Validator
from vcf_ops_telegraf_helper.workflow.collect import (
    DEFAULT_WEB_URL,
    CollectChoices,
    keys_from_monitoring,
    read_deployed_choices,
)
from vcf_ops_telegraf_helper.workflow.packages import DOWNLOAD_HOST
from vcf_ops_telegraf_helper.workflow.plain import COLLECTOR_IS, join_names

logger = get_logger("checks")

KIND_SETUP = "setup"
KIND_TEST = "test"
KIND_TAKEOVER = "takeover"
KIND_CHANGE = "change"
KIND_RESUME = "resume"

OK = "ok"
BAD = "bad"
SKIP = "skip"

FIX_SIGNIN = "cred"       # offer "Use a different sign-in"
FIX_COLLECTOR = "coll"    # offer "Pick a collector it can reach"
FIX_SEND = "push"         # offer "Send it from this computer"
FIX_AGAIN = "none"        # only "Test again" helps

WIN_AUTO = "auto"
WIN_SECURE = "secure"
WIN_PLAIN = "plain"

_AUTH_FAILURE = re.compile(
    r"authenticat|password|credential|access is denied|permission denied|logon|rejected|unauthori[sz]ed|\b401\b|invalid user",
    re.IGNORECASE,
)

_LINUX_PROCESSES = {
    "nginx": "nginx", "apache2": "apache", "httpd": "apache", "mysqld": "mysql", "mariadbd": "mysql",
    "postgres": "pg", "dockerd": "docker", "sqlservr": "mssql",
}
_WEB_CANDIDATES = {
    "nginx": (["http://localhost/nginx_status", "http://localhost/status", "http://localhost/basic_status",
               "http://127.0.0.1:8080/nginx_status"], "Active connections"),
    "apache": (["http://localhost/server-status?auto", "http://127.0.0.1:8080/server-status?auto"], "Total Accesses"),
}


def collector_label(collector: CollectorInfo) -> str:
    """A collector in words: 'the collector group "Main site"' or 'the collector "cp-01"'."""
    if collector.is_collector_group:
        return f'the collector group "{collector.name or collector.address}"'
    return f'the collector "{collector.display_name or collector.name or collector.address}"'


def collector_kind(collector: CollectorInfo) -> str:
    if collector.is_collector_group:
        return "A group of collectors (recommended: it keeps working if one of them is down)"
    if collector.name:
        return f'One collector, in the group "{collector.name}"'
    return "One collector"


def same_collector(a: Optional[CollectorInfo], b: Optional[CollectorInfo]) -> bool:
    return bool(a and b and a.address == b.address and a.is_collector_group == b.is_collector_group)


class CollectorReach(BaseModel):
    collector: CollectorInfo
    ok: bool


class MachineRef(BaseModel):
    """What the check needs to know about a machine from the inventory."""

    machine_id: str
    name: str
    address: str
    os: str = "linux"
    vm_mor: Optional[str] = None
    vc_id: Optional[str] = None
    collector_address: Optional[str] = None
    collector_group: Optional[str] = None


class CheckRequest(BaseModel):
    machine: MachineRef
    kind: str = KIND_SETUP
    username: str = ""
    password: Optional[str] = Field(default=None, exclude=True)
    key_file: Optional[str] = None
    many: bool = False
    collectors: List[CollectorInfo] = Field(default_factory=list)
    chosen: Optional[CollectorInfo] = Field(default=None, description="A collector the person or a setting chose")
    version: str = "1.40.1"
    update: bool = False
    send: bool = Field(default=False, description="Telegraf will be sent from this computer, so no download is needed")
    win_mode: str = WIN_AUTO
    ssh_port: int = 22
    known_port: Optional[int] = None
    known_ssl: Optional[bool] = None


class CheckResult(BaseModel):
    machine_id: str
    state: str = OK
    what: str = ""
    todo: str = ""
    fix: str = ""
    refused: bool = Field(default=False, description="The machine refused the sign-in itself")
    signed_in: bool = False
    os_name: str = ""
    hostname: str = ""
    port: Optional[int] = None
    winrm_ssl: bool = False
    installed_version: Optional[str] = None
    can_update: bool = False
    running: bool = False
    managed: bool = False
    monitored: bool = Field(default=False, description="This app's settings file is already on the machine")
    current: Optional[CollectChoices] = None
    collector: Optional[CollectorInfo] = None
    collectors: List[CollectorReach] = Field(default_factory=list)
    can_download: Optional[bool] = None
    found: List[str] = Field(default_factory=list)
    web_urls: Dict[str, str] = Field(default_factory=dict)
    web_missing: List[str] = Field(default_factory=list)
    takeover_keys: List[str] = Field(default_factory=list)
    takeover_adds_self_check: bool = False
    tested_at: float = Field(default_factory=time.time)
    same_as_before: bool = False

    @property
    def ok(self) -> bool:
        return self.state == OK


ExecutorFactory = Callable[[EndpointTarget], EndpointExecutor]


def _target(request: CheckRequest, port: int, ssl: bool) -> EndpointTarget:
    machine = request.machine
    windows = machine.os == "windows"
    return EndpointTarget(
        hostname=machine.address,
        os_family=OSFamily.WINDOWS if windows else OSFamily.LINUX,
        connection_method=ConnectionMethod.WINRM if windows else ConnectionMethod.SSH,
        port=port,
        username=request.username or None,
        password=None if request.key_file else request.password,
        key_filename=request.key_file,
        winrm_use_ssl=windows and ssl,
        registered_hostname=machine.name,
        vm_mor=machine.vm_mor,
        vc_id=machine.vc_id,
    )


def _attempts(request: CheckRequest) -> List[tuple]:
    """(port, encrypted) to try, in order. What worked before is tried first."""
    if request.machine.os != "windows":
        return [(request.known_port or request.ssh_port or 22, False)]
    if request.win_mode == WIN_SECURE:
        ordered = [(5986, True)]
    elif request.win_mode == WIN_PLAIN:
        ordered = [(5985, False)]
    else:
        ordered = [(5986, True), (5985, False)]
    if request.known_port and request.known_ssl is not None:
        known = (request.known_port, request.known_ssl)
        if known in ordered:
            ordered = [known] + [a for a in ordered if a != known]
    return ordered


def _connect(request: CheckRequest, make_executor: ExecutorFactory, result: CheckResult):
    """Sign in, trying the secure way first on Windows. Returns the connected executor or None."""
    last_error = ""
    for port, ssl in _attempts(request):
        executor = make_executor(_target(request, port, ssl))
        try:
            connected = executor.test_connection()
            error = str(getattr(executor, "connection_error", "") or "")
        except Exception as exc:
            connected, error = False, str(exc)
        if connected:
            result.port, result.winrm_ssl, result.signed_in = port, ssl, True
            return executor
        last_error = error
        try:
            executor.close()
        except Exception:
            pass
        if _AUTH_FAILURE.search(error):
            # A refused sign-in is not retried another way: repeated refusals can lock the account
            break
    one = "the machine" if not request.many else "this machine"
    if _AUTH_FAILURE.search(last_error):
        result.refused = True
        result.state, result.fix = BAD, FIX_SIGNIN
        result.what = "The machine did not accept that user name or password."
        result.todo = ("This machine probably uses a different account from the others. Give the one that works on it."
                       if request.many else "Check the user name and the password, then give the sign-in again.")
    else:
        result.state, result.fix = BAD, FIX_AGAIN
        result.what = f"The app could not reach {one} to sign in."
        result.todo = "Check that it is switched on and that this computer can reach it on the network." + (
            " On a Windows machine, remote management (WinRM) must be switched on." if request.machine.os == "windows" else ""
        ) + " Then press Test again."
    logger.info("Check of %s: sign-in failed (%s)", request.machine.name, last_error[:200])
    return None


def _may_install(executor: EndpointExecutor, windows: bool) -> bool:
    """Whether the signed-in account can install software."""
    if windows:
        res = executor.execute(
            "([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent())"
            ".IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)", timeout=10)
        return not (res.success and res.stdout.strip().lower() == "false")
    if not getattr(executor, "use_sudo", False):
        return True
    return executor.execute("sudo -n true", timeout=10).success


def can_reach(executor: EndpointExecutor, collector: CollectorInfo, windows: bool) -> bool:
    return Validator.validate_collector_reachability(
        executor, collector.address, port=collector.port or 443, is_windows=windows
    ).is_valid


def can_download(executor: EndpointExecutor, windows: bool) -> bool:
    """Whether the machine can reach the site Telegraf is downloaded from."""
    if windows:
        res = executor.execute(
            f"try {{ $c = New-Object System.Net.Sockets.TcpClient; $c.Connect('{DOWNLOAD_HOST}', 443); $c.Connected; $c.Close() }} "
            "catch { 'False' }", timeout=15)
        return res.success and "True" in res.stdout
    res = executor.execute(
        f"curl -sS -m 8 -o /dev/null -I https://{DOWNLOAD_HOST}/telegraf/releases/ 2>/dev/null "
        f"|| timeout 6 bash -c '</dev/tcp/{DOWNLOAD_HOST}/443' 2>/dev/null", timeout=20)
    return res.success


def find_software(executor: EndpointExecutor, windows: bool) -> List[str]:
    """Catalogue keys of the listed software that is running on the machine."""
    found: List[str] = []
    if windows:
        try:
            services = executor.discover_services()
        except Exception:
            services = []
        for service in services:
            if (service.status or "").lower() != "running":
                continue
            name = (service.name or "").lower()
            key = None
            if name == "mssqlserver" or name.startswith("mssql$"):
                key = "mssql"
            elif name.startswith("nginx"):
                key = "nginx"
            elif name.startswith("apache"):
                key = "apache"
            elif name.startswith(("mysql", "mariadb")):
                key = "mysql"
            elif name.startswith("postgresql"):
                key = "pg"
            elif name in ("docker", "com.docker.service"):
                key = "docker"
            if key and key not in found:
                found.append(key)
        return found
    names = " ".join(_LINUX_PROCESSES)
    res = executor.execute(
        f'for p in {names}; do if pgrep -x "$p" >/dev/null 2>&1; then echo "proc:$p"; fi; done; true', timeout=15)
    for line in res.stdout.splitlines():
        if line.startswith("proc:"):
            key = _LINUX_PROCESSES.get(line[5:].strip())
            if key and key not in found:
                found.append(key)
    return found


def find_status_pages(executor: EndpointExecutor, keys: List[str]) -> Dict[str, str]:
    """The built-in status page of each found web server, where it answers."""
    urls: Dict[str, str] = {}
    for key in keys:
        if key not in _WEB_CANDIDATES:
            continue
        candidates, marker = _WEB_CANDIDATES[key]
        loop = " ".join(f'"{u}"' for u in candidates)
        res = executor.execute(
            f'for u in {loop}; do if curl -s -m 4 "$u" 2>/dev/null | head -c 400 | grep -q "{marker}"; '
            f'then echo "web:$u"; break; fi; done; true', timeout=30)
        for line in res.stdout.splitlines():
            if line.startswith("web:"):
                urls[key] = line[4:].strip()
                break
    return urls


def _system_file(config_dir: Optional[str], windows: bool) -> str:
    folder = config_dir or ("C:\\telegraf\\telegraf.d" if windows else "/etc/telegraf/telegraf.d")
    return f"{folder}\\vcf-helper-system.conf" if windows else f"{folder}/vcf-helper-system.conf"


def read_current(executor: EndpointExecutor, config_dir: Optional[str], os_family: str) -> Optional[CollectChoices]:
    """What the machine collects today, from the settings file this app writes; None when it is not there."""
    path = _system_file(config_dir, os_family == "windows")
    try:
        if not executor.file_exists(path):
            return None
        return read_deployed_choices(executor.download(path), os_family)
    except Exception as exc:
        logger.warning("Could not read %s: %s", path, exc)
        return None


def preferred_collector(machine: MachineRef, collectors: List[CollectorInfo]) -> Optional[CollectorInfo]:
    """The collector the app picks: where the machine already reports, otherwise the recommended group."""
    if machine.collector_address or machine.collector_group:
        group = next((c for c in collectors if c.is_collector_group and c.name and c.name == machine.collector_group), None)
        if group:
            return group
        proxy = next((c for c in collectors if not c.is_collector_group and c.address == machine.collector_address), None)
        if proxy:
            return proxy
    return next((c for c in collectors if c.is_collector_group), None) or (collectors[0] if collectors else None)


def _check_collector(request: CheckRequest, executor: EndpointExecutor, result: CheckResult) -> bool:
    windows = request.machine.os == "windows"
    if not request.collectors:
        result.state, result.fix = BAD, FIX_AGAIN
        result.what = "VCF Operations has no collector for this machine to send its data to."
        result.todo = (COLLECTOR_IS + ' Ask whoever runs VCF Operations to set one up (it is called a "cloud proxy" there), '
                       "then press Test again.")
        return False
    wanted = request.chosen or preferred_collector(request.machine, request.collectors)
    if wanted is not None and can_reach(executor, wanted, windows):
        result.collector = wanted
        result.collectors = [CollectorReach(collector=wanted, ok=True)]
        return True
    reach = []
    for collector in request.collectors:
        ok = False if same_collector(collector, wanted) else can_reach(executor, collector, windows)
        reach.append(CollectorReach(collector=collector, ok=ok))
    result.collectors = reach
    reachable = [r.collector for r in reach if r.ok]
    result.state = BAD
    result.what = f"Signed in, but this machine cannot reach {collector_label(wanted) if wanted else 'the collector'}."
    if not reachable:
        result.fix = FIX_AGAIN
        result.todo = (COLLECTOR_IS + " A firewall is usually in the way. The app tested every collector from this machine "
                       "and it cannot reach any of them. Ask for the way from this machine to the collector to be opened "
                       "(port 443), then press Test again.")
    elif request.kind == KIND_TEST:
        result.fix = FIX_AGAIN
        names = join_names([collector_label(c) for c in reachable])
        result.todo = (COLLECTOR_IS + f" A firewall is usually in the way. The app tested the others too: this machine can "
                       f"reach {names}. When you set the machine up, the app will offer that.")
    else:
        result.fix = FIX_COLLECTOR
        result.todo = (COLLECTOR_IS + " A firewall is usually in the way. The app has tested the other collectors from "
                       "this machine: pick one it can reach.")
    return False


def _check_takeover(request: CheckRequest, probe: Dict[str, Any], result: CheckResult) -> bool:
    from vcf_ops_telegraf_helper.workflow.managed_config import import_managed_config

    managed = probe.get("managed")
    machine = request.machine
    if managed is None:
        result.state, result.fix = BAD, FIX_AGAIN
        result.what = "VCF Operations' own agent was not found on this machine."
        result.todo = ("Nothing was changed. Go back and press Refresh list on Machines: the machine may already have been "
                       "taken over, or changed by someone else.")
        return False
    grains = getattr(managed, "grain_values", {}) or {}
    bound_mor, bound_vc = grains.get("vm_id"), grains.get("vc_id")
    if bound_mor and bound_vc and machine.vm_mor and machine.vc_id and (bound_mor != machine.vm_mor or bound_vc != machine.vc_id):
        result.state, result.fix = BAD, FIX_AGAIN
        result.what = "The agent on this machine belongs to a different machine record in VCF Operations."
        result.todo = ("Nothing was changed. Check that the address VCF Operations has for this machine is right, "
                       "then press Test again.")
        return False
    imported = import_managed_config(managed.telegraf_conf, managed.telegraf_d, is_windows=machine.os == "windows")
    if not imported.ok:
        result.state, result.fix = BAD, FIX_AGAIN
        result.what = "The machine's current monitoring settings contain something the app cannot carry over safely."
        result.todo = ("Nothing was changed. Ask whoever looks after the machine to look at: "
                       + "; ".join(imported.blocked)[:400] + ". Then press Test again.")
        return False
    result.takeover_keys = keys_from_monitoring(imported.monitoring, machine.os)
    result.takeover_adds_self_check = any("win_services" in line for line in imported.added)
    return True


def check_machine(request: CheckRequest, make_executor: ExecutorFactory) -> CheckResult:
    """Sign in to one machine and look around. Nothing on the machine is changed."""
    from vcf_ops_telegraf_helper.workflow.probe import probe_endpoint

    machine = request.machine
    windows = machine.os == "windows"
    result = CheckResult(machine_id=machine.machine_id)
    executor = _connect(request, make_executor, result)
    if executor is None:
        return result
    try:
        if request.kind != KIND_RESUME and not _may_install(executor, windows):
            result.state, result.fix = BAD, FIX_SIGNIN
            result.what = "Signed in, but this account is not allowed to install software on the machine."
            result.todo = ("Give an administrator account." if windows else
                           'Give the "root" account, or one with full "sudo" rights that is not asked for a password.')
            return result
        try:
            probe = probe_endpoint(_target(request, result.port or 22, result.winrm_ssl), executor)
        except Exception as exc:
            logger.warning("Check of %s: could not look around: %s", machine.name, exc)
            result.state, result.fix = BAD, FIX_AGAIN
            result.what = "Signed in, but the app could not look around on the machine."
            result.todo = "Press Test again. If it happens again, check that the account is allowed to run commands on the machine."
            return result
        result.os_name = str(probe.get("os") or "")
        result.hostname = str(probe.get("hostname") or "")
        result.running = bool(probe.get("running"))
        result.managed = probe.get("managed") is not None
        version = parse_telegraf_version(probe.get("version")) if probe.get("installed") else None
        if version and not result.managed:
            result.installed_version = ".".join(str(part) for part in version)
            decision, _ = evaluate_telegraf_upgrade(result.installed_version, request.version)
            result.can_update = decision == UpgradeDecision.UPGRADE

        if request.kind == KIND_RESUME:
            return result
        if request.kind == KIND_TAKEOVER:
            if not _check_takeover(request, probe, result):
                return result
        elif result.managed:
            if request.kind == KIND_TEST:
                return result
            result.state = SKIP
            result.what = "This machine is monitored by VCF Operations' own agent, so it is left out of this run."
            result.todo = ('Replacing that agent is a takeover, done one machine at a time: use "Take over" on its row '
                           "in the Machines list.")
            return result

        if request.kind != KIND_TAKEOVER:
            result.current = read_current(executor, probe.get("config_dir"), machine.os)
            result.monitored = result.current is not None
        if request.kind == KIND_CHANGE:
            if result.current is None:
                result.state, result.fix = BAD, FIX_AGAIN
                result.what = "This app's settings were not found on the machine, so there is nothing here for it to change."
                result.todo = ('The machine was set up some other way. Go back and use "Set up monitoring" on it instead; '
                               "that replaces what it collects with the choices you make.")
            return result

        if not _check_collector(request, executor, result):
            return result

        will_install = result.installed_version is None or (request.update and result.can_update)
        if will_install and not request.send:
            result.can_download = can_download(executor, windows)
            if not result.can_download:
                result.state = BAD
                result.what = "Signed in, but this machine cannot download Telegraf. It has no way out to the internet."
                if request.kind == KIND_TEST:
                    result.fix = FIX_AGAIN
                    result.todo = ("When you set the machine up, the app will offer to download Telegraf on this computer "
                                   "and send it across instead.")
                else:
                    result.fix = FIX_SEND
                    result.todo = "The app can download Telegraf on this computer and send it across instead."
                return result

        result.found = find_software(executor, windows)
        if not windows:
            web = [k for k in result.found if k in DEFAULT_WEB_URL]
            result.web_urls = find_status_pages(executor, web)
            result.web_missing = [k for k in web if k not in result.web_urls]
        return result
    finally:
        try:
            executor.close()
        except Exception:
            pass
