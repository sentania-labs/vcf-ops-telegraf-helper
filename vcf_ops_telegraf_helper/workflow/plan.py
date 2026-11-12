"""What will be done to one machine, decided before anything runs.

A MachinePlan records every choice of a run for one machine: how it is reached, where its
data goes, what happens to Telegraf, and what it collects. The engine inputs (target,
environment, options, monitoring) and the command that does the same thing from the command
line are both built from the plan, so the command always matches what was really done.
"""

from __future__ import annotations

import re
import shlex
import sys
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from vcf_ops_telegraf_helper.models.endpoint import ConnectionMethod, EndpointTarget, OSFamily
from vcf_ops_telegraf_helper.models.monitoring import MonitoringConfig
from vcf_ops_telegraf_helper.models.vcf import CollectorInfo, VCFEnvironment
from vcf_ops_telegraf_helper.models.workflow import WorkflowOptions
from vcf_ops_telegraf_helper.workflow.collect import (
    SELF_CHECK_SERVICE,
    CollectChoices,
    database_setting,
    monitoring_for,
    placeholder_for,
)

INSTALL = "install"      # Telegraf is not on the machine: install the chosen version
KEEP = "keep"            # Telegraf is on the machine: leave that version alone
UPDATE = "update"        # Telegraf is on the machine and older: move it up to the chosen version
PUSH = "push"            # the machine cannot download Telegraf: send it from this computer

KIND_SETUP = "setup"
KIND_CHANGE = "change"
KIND_TAKEOVER = "takeover"

DEFAULT_TELEGRAF_VERSION = "1.40.1"
TELEGRAF_VERSIONS = ["1.40.1", "1.34.0", "1.32.1", "1.30.0"]


class ConnectionChoices(BaseModel):
    """How this app reaches VCF Operations. The password and token are held in memory only."""

    url: str
    username: str = ""
    password: Optional[str] = Field(default=None, exclude=True)
    token: Optional[str] = Field(default=None, exclude=True)
    use_token: bool = False
    auth_source: str = "local"
    verify_ssl: bool = True
    ca_cert_path: Optional[str] = None
    pinned_cert_sha256: Optional[str] = None
    agent_verify_ssl: bool = True

    def environment(self, collector: Optional[CollectorInfo] = None) -> VCFEnvironment:
        return VCFEnvironment(
            url=self.url,
            username="admin" if self.use_token else self.username,
            password=None if self.use_token else self.password,
            token=self.token if self.use_token else None,
            auth_source="local" if self.use_token else (self.auth_source or "local"),
            collector=collector or CollectorInfo(address=""),
            verify_ssl=self.verify_ssl,
            agent_verify_ssl=self.agent_verify_ssl,
            ca_cert_path=self.ca_cert_path if (self.verify_ssl and not self.pinned_cert_sha256) else None,
            pinned_cert_sha256=self.pinned_cert_sha256 if self.verify_ssl else None,
        )


class MachinePlan(BaseModel):
    """Every choice of a run, for one machine."""

    machine_id: str
    name: str
    address: str
    os: str = "linux"
    vm_mor: Optional[str] = None
    vc_id: Optional[str] = None
    kind: str = KIND_SETUP
    username: str = ""
    password: Optional[str] = Field(default=None, exclude=True)
    key_file: Optional[str] = None
    port: Optional[int] = None
    winrm_ssl: bool = False
    collector: CollectorInfo = Field(default_factory=lambda: CollectorInfo(address=""))
    collector_label: str = ""
    install: str = INSTALL
    version: str = DEFAULT_TELEGRAF_VERSION
    installed_version: Optional[str] = None
    choices: CollectChoices = Field(default_factory=CollectChoices)
    found: List[str] = Field(default_factory=list)
    web_urls: Dict[str, str] = Field(default_factory=dict)
    single: bool = True
    practice: bool = False
    resume: bool = False

    @property
    def windows(self) -> bool:
        return self.os == "windows"

    def default_port(self) -> int:
        if self.windows:
            return 5986 if self.winrm_ssl else 5985
        return 22

    def target(self) -> EndpointTarget:
        return EndpointTarget(
            hostname=self.address,
            os_family=OSFamily.WINDOWS if self.windows else OSFamily.LINUX,
            connection_method=ConnectionMethod.WINRM if self.windows else ConnectionMethod.SSH,
            port=self.port or self.default_port(),
            username=self.username or None,
            password=None if self.key_file else self.password,
            key_filename=self.key_file,
            winrm_use_ssl=self.windows and self.winrm_ssl,
            install_telegraf=self.install in (INSTALL, UPDATE, PUSH),
            telegraf_version=self.version,
            registered_hostname=self.name,
            vm_mor=self.vm_mor,
            vc_id=self.vc_id,
        )

    def options(self) -> WorkflowOptions:
        return WorkflowOptions(
            dry_run=self.practice,
            restart_service=True,
            install_telegraf=self.install in (INSTALL, UPDATE, PUSH),
            telegraf_version=self.version,
            # What is ticked is what is applied: an existing selection is always replaced
            replace_inputs=True,
            send_telegraf=self.install == PUSH,
        )

    def monitoring(self) -> MonitoringConfig:
        return monitoring_for(self.choices, self.os, self.found, self.single, self.web_urls)


QUOTE_NOTE = "Keep the quotes around <password> in a database setting."


def _without_sign_in_passwords(command: str) -> str:
    return re.sub(r"--[a-z-]*pass(?:word)? \"<password>\"", "", command)


def quote_note_for(commands) -> str:
    """The extra line the command box needs when a database password sits in quotes."""
    quoted = (placeholder_for("pg"), placeholder_for("mssql"))
    needed = any(any(form in _without_sign_in_passwords(cmd) for form in quoted) for cmd in commands)
    return QUOTE_NOTE if needed else ""


def _quoter():
    if sys.platform == "win32":
        return lambda value: "'" + str(value).replace("'", "''") + "'"
    return lambda value: shlex.quote(str(value))


def _join(lines: List[str]) -> str:
    lines = [ln for ln in lines if ln]
    if sys.platform == "win32":
        return " ".join(lines)
    return " \\\n  ".join(lines)


def _connection_flags(conn: ConnectionChoices, quote) -> List[str]:
    parts = [f"--vcf-url {quote(conn.url)}"]
    if conn.use_token:
        parts.append('--vcf-token "<token>"')
    else:
        parts.append(f"--vcf-user {quote(conn.username)}")
        if conn.auth_source and conn.auth_source.lower() != "local":
            parts.append(f"--vcf-auth-source {quote(conn.auth_source)}")
        parts.append('--vcf-pass "<password>"')
    lines = [" ".join(parts)]
    tls = []
    if not conn.verify_ssl:
        tls.append("--no-verify-ssl")
    elif conn.pinned_cert_sha256:
        tls.append(f"--trust-cert-sha256 {conn.pinned_cert_sha256}")
    elif conn.ca_cert_path:
        tls.append(f"--ca-cert {quote(conn.ca_cert_path)}")
    if not conn.agent_verify_ssl:
        tls.append("--no-agent-verify-ssl")
    if tls:
        lines.append(" ".join(tls))
    return lines


OWN_SETTINGS_PLACEHOLDER = '"<file with your own Telegraf settings>"'


def collect_flags(mon: MonitoringConfig, windows: bool, quote=None) -> str:
    """The command-line flags that select exactly this monitoring configuration."""
    quote = quote or _quoter()
    parts: List[str] = []
    if windows:
        core = [mon.win_perf_counters.enabled, mon.win_os.enabled, mon.win_services.enabled]
        if not any(core):
            parts.append("--no-baseline")
        else:
            parts.append("--win-perf" if mon.win_perf_counters.enabled else "--no-win-perf")
            parts.append("--win-os" if mon.win_os.enabled else "--no-win-os")
            if not mon.win_services.enabled:
                parts.append("--no-win-services")
            elif mon.win_services.service_names != [SELF_CHECK_SERVICE]:
                parts.append(f"--win-services {quote(','.join(mon.win_services.service_names))}")
        if mon.win_perf_counters.enabled:
            for obj in mon.win_perf_counters.additional_objects:
                parts.append(f"--win-perf-object {quote(obj.model_dump_json())}")
    else:
        basics = [("cpu", mon.cpu.enabled), ("mem", mon.mem.enabled), ("disk", mon.disk.enabled),
                  ("net", mon.net.enabled), ("system", mon.system.enabled), ("swap", mon.swap.enabled)]
        if not any(on for _, on in basics):
            parts.append("--no-baseline")
        else:
            parts.extend(f"--{name}" if on else f"--no-{name}" for name, on in basics)
        if mon.diskio.enabled:
            parts.append("--diskio")
        if mon.processes.enabled:
            parts.append("--processes")
    # Own settings are never written to this computer by the app: the command names where they go
    own = [f"--custom-toml-file {OWN_SETTINGS_PLACEHOLDER}"] if (mon.custom_toml or "").strip() else []
    return " ".join(parts + software_flags(mon, quote) + own)


def software_flags(mon: MonitoringConfig, quote=None) -> List[str]:
    quote = quote or _quoter()
    parts: List[str] = []
    if mon.nginx.enabled and mon.nginx.urls:
        parts.append(f"--nginx {quote(mon.nginx.urls[0])}")
    if mon.apache.enabled and mon.apache.urls:
        parts.append(f"--apache {quote(mon.apache.urls[0])}")
    if mon.mysql.enabled and mon.mysql.servers:
        parts.append(f"--mysql {quote(mon.mysql.servers[0])}")
    if mon.postgresql.enabled and mon.postgresql.address:
        parts.append(f"--postgres {quote(mon.postgresql.address)}")
    if mon.mssql.enabled and mon.mssql.servers:
        parts.append(f"--mssql {quote(mon.mssql.servers[0])}")
    if mon.docker.enabled and mon.docker.endpoint:
        parts.append(f"--docker {quote(mon.docker.endpoint)}")
    if mon.ping.enabled and mon.ping.urls:
        parts.append(f"--ping {quote(mon.ping.urls[0])}")
    return parts


def placeholder_monitoring(plan: MachinePlan) -> MonitoringConfig:
    """The plan's monitoring settings with every database password replaced by a placeholder."""
    mon = plan.monitoring().model_copy(deep=True)
    details = plan.choices.details
    if mon.mysql.enabled:
        mon.mysql.servers = [database_setting("mysql", details.get("mysql", {}), placeholder=True)]
    if mon.postgresql.enabled:
        mon.postgresql.address = database_setting("pg", details.get("pg", {}), placeholder=True)
    if mon.mssql.enabled:
        mon.mssql.servers = [database_setting("mssql", details.get("mssql", {}), placeholder=True)]
    return mon


def _machine_flags(plan: MachinePlan, quote) -> List[str]:
    where = [f"--vm-name {quote(plan.name)}"]
    if plan.vm_mor:
        where.append(f"--vm-id {quote(plan.vm_mor)}")
        if plan.vc_id:
            where.append(f"--vc-id {quote(plan.vc_id)}")
    reach = [f"--target-host {quote(plan.address)}", f"--os {'windows' if plan.windows else 'linux'}",
             f"--connection {'winrm' if plan.windows else 'ssh'}"]
    port = plan.port or plan.default_port()
    if port != (5985 if plan.windows else 22):
        reach.append(f"--port {port}")
    if plan.windows and plan.winrm_ssl:
        reach.append("--winrm-ssl")
    signin = [f"--ssh-user {quote(plan.username)}"] if plan.username else []
    signin.append(f"--ssh-key {quote(plan.key_file)}" if plan.key_file else '--ssh-pass "<password>"')
    return [" ".join(where), " ".join(reach), " ".join(signin)]


def build_command(plan: MachinePlan, conn: ConnectionChoices) -> str:
    """The complete command that does to this machine what the plan describes. Passwords are placeholders."""
    quote = _quoter()
    takeover = plan.kind == KIND_TAKEOVER
    first = "vcf-telegraf-helper run"
    if not takeover:
        # The app always applies what is ticked, so the command must replace what is deployed too
        first += " --replace-inputs"
    if plan.practice and not takeover:
        first += " --dry-run"
    lines = [first] + _connection_flags(conn, quote) + _machine_flags(plan, quote)
    where = f"--collector {quote(plan.collector.address)}"
    if plan.collector.name:
        where += f" --collector-group {quote(plan.collector.name)}"
    if not takeover and plan.install in (INSTALL, UPDATE, PUSH):
        where += f" --install-telegraf --telegraf-version {quote(plan.version)}"
        if plan.install == PUSH:
            where += " --send-telegraf"
    elif takeover:
        if plan.version != DEFAULT_TELEGRAF_VERSION:
            where += f" --telegraf-version {quote(plan.version)}"
        if plan.install == PUSH:
            where += " --send-telegraf"
    lines.append(where)
    if takeover:
        lines.append(f"--take-over-managed-agent --confirm-takeover {quote(plan.name)}")
    else:
        lines.append(collect_flags(placeholder_monitoring(plan), plan.windows, quote))
    return _join(lines)


def build_remove_command(plan: MachinePlan) -> str:
    """The command that removes monitoring from this machine. Passwords are placeholders."""
    quote = _quoter()
    first = (f"vcf-telegraf-helper uninstall --target {quote(plan.address)} "
             f"--os {'windows' if plan.windows else 'linux'} --method {'winrm' if plan.windows else 'ssh'}")
    port = plan.port or plan.default_port()
    if port != (5985 if plan.windows else 22):
        first += f" --port {port}"
    if plan.windows and plan.winrm_ssl:
        first += " --winrm-ssl"
    signin = f"--user {quote(plan.username)} " if plan.username else ""
    signin += f"--key-path {quote(plan.key_file)}" if plan.key_file else '--password "<password>"'
    return _join([first, signin + " --yes"])
