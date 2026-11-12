"""What a machine can collect, in plain names, and how the ticks become Telegraf settings.

The screens work with a short list of named choices in three groups. This module owns that
list and the two translations: ticked choices to a MonitoringConfig for one machine, and a
MonitoringConfig read back from a machine to the choices it stands for.

Two rules live here because every screen depends on them:

* An item for particular software is switched on only on machines where that software was
  found, unless the run is for a single machine (then a tick is the person's own decision).
* On Windows the basics are collected "the Windows way": VCF Operations' standard counter
  template, which is one set. Ticking any basic collects all of them there.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from pydantic import BaseModel, Field

from vcf_ops_telegraf_helper.models.monitoring import (
    ApacheInputConfig,
    CpuInputConfig,
    DiskInputConfig,
    DiskIoInputConfig,
    DockerInputConfig,
    MemInputConfig,
    MonitoringConfig,
    MssqlInputConfig,
    MysqlInputConfig,
    NetInputConfig,
    NginxInputConfig,
    PerfmonObject,
    PingInputConfig,
    PostgresqlInputConfig,
    ProcessesInputConfig,
    SwapInputConfig,
    SystemInputConfig,
    WindowsOsInputConfig,
    WinPerfCountersInputConfig,
    WinServicesInputConfig,
)

GROUP_BASICS = "basics"
GROUP_SOFTWARE = "soft"
GROUP_WINDOWS = "win"


@dataclass(frozen=True)
class CatalogItem:
    key: str
    group: str
    name: str
    hint: str
    default: bool = False
    ask: Optional[str] = None  # the small dialog this choice opens: web, db, ping, svc, perf


CATALOG: List[CatalogItem] = [
    CatalogItem("cpu", GROUP_BASICS, "Processor use", "How busy the processors are.", True),
    CatalogItem("mem", GROUP_BASICS, "Memory use", "How much memory is in use and free.", True),
    CatalogItem("disk", GROUP_BASICS, "Disk space", "Used and free space on each disk.", True),
    CatalogItem("net", GROUP_BASICS, "Network traffic", "Data sent and received, and errors.", True),
    CatalogItem("system", GROUP_BASICS, "Load and uptime", "How loaded it is, and time since restart.", True),
    CatalogItem("swap", GROUP_BASICS, "Swap use", "Use of the overflow memory on disk.", True),
    CatalogItem("diskio", GROUP_BASICS, "Disk activity", "Reads and writes per disk."),
    CatalogItem("procs", GROUP_BASICS, "Running programs", "How many are running or stuck."),
    CatalogItem("nginx", GROUP_SOFTWARE, "NGINX web server", "Requests and connections.", ask="web"),
    CatalogItem("apache", GROUP_SOFTWARE, "Apache web server", "Requests, workers and traffic.", ask="web"),
    CatalogItem("mysql", GROUP_SOFTWARE, "MySQL or MariaDB", "Queries, connections and speed.", ask="db"),
    CatalogItem("pg", GROUP_SOFTWARE, "PostgreSQL", "Queries, connections and speed.", ask="db"),
    CatalogItem("mssql", GROUP_SOFTWARE, "SQL Server", "Microsoft's database: queries and speed.", ask="db"),
    CatalogItem("docker", GROUP_SOFTWARE, "Docker containers", "Each container's use of the machine."),
    CatalogItem("ping", GROUP_SOFTWARE, "Ping test", "Whether another address answers.", ask="ping"),
    CatalogItem("winsvc", GROUP_WINDOWS, "Windows services", "Whether the services you pick are running.", ask="svc"),
    CatalogItem("winperf", GROUP_WINDOWS, "Extra Windows counters", "More Windows figures, picked from a list.", ask="perf"),
]

_BY_KEY = {item.key: item for item in CATALOG}
BASIC_KEYS = [item.key for item in CATALOG if item.group == GROUP_BASICS]
USUAL_SET = [item.key for item in CATALOG if item.default]
SOFTWARE_KEYS = [item.key for item in CATALOG if item.group == GROUP_SOFTWARE]
WINDOWS_ONLY = [item.key for item in CATALOG if item.group == GROUP_WINDOWS]
# Software items that are only switched on where the software itself was found
FOUND_ONLY = ["nginx", "apache", "mysql", "pg", "mssql", "docker"]
DATABASE_KEYS = ["mysql", "pg", "mssql"]
DATABASE_PORTS = {"mysql": 3306, "pg": 5432, "mssql": 1433}
DATABASE_ENGINES = {"mysql": "mysql", "pg": "postgresql", "mssql": "mssql"}

DEFAULT_WEB_URL = {"nginx": "http://localhost/nginx_status", "apache": "http://localhost/server-status?auto"}
PASSWORD_PLACEHOLDER = "<password>"
SELF_CHECK_SERVICE = "telegraf"


def item(key: str) -> CatalogItem:
    return _BY_KEY[key]


def name_of(key: str) -> str:
    found = _BY_KEY.get(key)
    return found.name if found else key


def names_of(keys: Iterable[str]) -> List[str]:
    return [name_of(k) for k in ordered(keys)]


def ordered(keys: Iterable[str]) -> List[str]:
    """Catalogue order, each key once, unknown keys dropped."""
    wanted = set(keys)
    return [item.key for item in CATALOG if item.key in wanted]


class CollectChoices(BaseModel):
    """What the person ticked, with the answers the small dialogs collected."""

    keys: List[str] = Field(default_factory=list)
    details: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    notes: Dict[str, str] = Field(default_factory=dict, description="The line shown under a ticked item")
    custom_toml: str = ""
    had_custom: bool = Field(default=False, description="The saved choices came with own settings that were not kept")

    def has(self, key: str) -> bool:
        return key in self.keys

    def add(self, key: str) -> None:
        if key not in self.keys:
            self.keys = ordered(self.keys + [key])

    def remove(self, key: str) -> None:
        self.keys = [k for k in self.keys if k != key]
        self.details.pop(key, None)
        self.notes.pop(key, None)

    def without_secrets(self) -> "CollectChoices":
        """A copy safe to write to disk: a database password is dropped, never stored."""
        copy = self.model_copy(deep=True)
        for key, detail in copy.details.items():
            detail.pop("password", None)
            if detail.get("raw") and key in DATABASE_KEYS:
                detail["raw"] = _mask_password(key, detail["raw"])
            if detail.get("url") and key in DEFAULT_WEB_URL:
                detail["url"] = mask_web_address(str(detail["url"]))
        # Someone's own Telegraf settings may hold anything, a password included, so they are not kept
        copy.had_custom = bool(self.custom_toml.strip())
        copy.custom_toml = ""
        return copy

    def missing_secrets(self) -> List[str]:
        """Ticked database items whose password is not held (for example after the app was reopened)."""
        missing = []
        for key in self.keys:
            detail = self.details.get(key, {})
            if key not in DATABASE_KEYS:
                continue
            if detail.get("user") and "password" not in detail:
                missing.append(key)
            elif PASSWORD_PLACEHOLDER in str(detail.get("raw", "")):
                missing.append(key)
        return missing


def applies_to(key: str, os_family: str, found: Iterable[str], single: bool) -> bool:
    """Whether a ticked item is switched on for a machine of this kind."""
    windows = os_family == "windows"
    if key in WINDOWS_ONLY:
        return windows
    if key in FOUND_ONLY:
        return single or key in set(found)
    return True


def effective_keys(keys: Iterable[str], os_family: str, found: Iterable[str] = (), single: bool = True) -> List[str]:
    """The choices as they take effect on one machine.

    On Windows any ticked basic stands for the whole Windows counter template, which collects
    every basic, so they are reported as a set.
    """
    wanted = [k for k in ordered(keys) if applies_to(k, os_family, found, single)]
    if os_family == "windows" and any(k in BASIC_KEYS for k in wanted):
        wanted = ordered(set(wanted) | set(BASIC_KEYS))
    return wanted


def monitoring_for(
    choices: CollectChoices,
    os_family: str,
    found: Iterable[str] = (),
    single: bool = True,
    web_urls: Optional[Dict[str, str]] = None,
) -> MonitoringConfig:
    """Telegraf settings for one machine from the ticked choices."""
    windows = os_family == "windows"
    keys = set(effective_keys(choices.keys, os_family, found, single))
    web_urls = web_urls or {}
    basics = any(k in keys for k in BASIC_KEYS)

    def detail(key: str) -> Dict[str, Any]:
        return choices.details.get(key, {})

    services = [SELF_CHECK_SERVICE]
    if "winsvc" in keys:
        services += [s for s in detail("winsvc").get("names", []) if s and s not in services]
    extra_objects = [PerfmonObject.model_validate(o) for o in detail("winperf").get("objects", [])] if "winperf" in keys else []

    return MonitoringConfig(
        cpu=CpuInputConfig(enabled=not windows and "cpu" in keys),
        mem=MemInputConfig(enabled=not windows and "mem" in keys),
        disk=DiskInputConfig(enabled=not windows and "disk" in keys),
        net=NetInputConfig(enabled=not windows and "net" in keys),
        system=SystemInputConfig(enabled=not windows and "system" in keys),
        swap=SwapInputConfig(enabled=not windows and "swap" in keys),
        diskio=DiskIoInputConfig(enabled=not windows and "diskio" in keys),
        processes=ProcessesInputConfig(enabled=not windows and "procs" in keys),
        win_perf_counters=WinPerfCountersInputConfig(
            enabled=windows and (basics or bool(extra_objects)), additional_objects=extra_objects
        ),
        win_os=WindowsOsInputConfig(enabled=windows and basics),
        win_services=WinServicesInputConfig(
            enabled=windows and (basics or "winsvc" in keys), service_names=services
        ),
        nginx=NginxInputConfig(enabled="nginx" in keys, urls=[web_address("nginx", detail("nginx"), web_urls)]),
        apache=ApacheInputConfig(enabled="apache" in keys, urls=[web_address("apache", detail("apache"), web_urls)]),
        mysql=MysqlInputConfig(enabled="mysql" in keys, servers=[database_setting("mysql", detail("mysql"))]),
        postgresql=PostgresqlInputConfig(enabled="pg" in keys, address=database_setting("pg", detail("pg"))),
        mssql=MssqlInputConfig(enabled="mssql" in keys, servers=[database_setting("mssql", detail("mssql"))]),
        docker=DockerInputConfig(
            enabled="docker" in keys,
            endpoint="npipe:////./pipe/docker_engine" if windows else "unix:///var/run/docker.sock",
        ),
        ping=PingInputConfig(enabled="ping" in keys, urls=[detail("ping").get("addr") or "127.0.0.1"]),
        custom_toml=choices.custom_toml or "",
    )


def web_address(key: str, detail: Dict[str, Any], web_urls: Optional[Dict[str, str]] = None) -> str:
    """The status page a web server is read from.

    The address already working on the machine comes first, then the one the app found there,
    then the usual one. A saved address whose password was left out cannot be used again.
    """
    deployed = str(detail.get("url") or "")
    if deployed and PASSWORD_PLACEHOLDER not in deployed:
        return deployed
    return (web_urls or {}).get(key) or DEFAULT_WEB_URL[key]


def database_setting(key: str, detail: Dict[str, Any], placeholder: bool = False) -> str:
    """The connection setting Telegraf needs for a database; the password can be a placeholder."""
    raw = detail.get("raw")
    if raw and not detail.get("user"):
        # Read back from the machine and not changed: keep it exactly as deployed
        return _mask_password(key, raw, for_command=True) if placeholder else raw
    user = detail.get("user") or ""
    password = placeholder_for(key) if placeholder else quoted_password(key, detail.get("password") or "")
    port = int(detail.get("port") or DATABASE_PORTS[key])
    databases = detail.get("databases") or []
    if key == "mysql":
        who = f"{user}:{password}@" if user else ""
        return f"{who}tcp(127.0.0.1:{port})/?tls=false"
    if key == "pg":
        secret = f" password={password}" if (password and user) else ""
        name = f" dbname={databases[0]}" if databases else ""
        return f"host=localhost port={port} user={user or 'postgres'}{secret} sslmode=disable{name}"
    who = f"User Id={user};Password={password};" if user else ""
    return f"Server=127.0.0.1;Port={port};{who}app name=telegraf;log=1;"


def database_scope(key: str, detail: Dict[str, Any]) -> str:
    """In words, which databases a ticked database item watches. It states what happens; it chooses nothing."""
    name = name_of(key)
    if detail.get("raw") and not detail.get("user"):
        return f"{name}: watched the way it is on the machine today."
    found = [str(d) for d in (detail.get("databases") or [])]
    listed = ""
    if found:
        shown = found[:4] + ([f"{len(found) - 4} more"] if len(found) > 4 else [])
        listed = " (" + (", ".join(shown[:-1]) + " and " + shown[-1] if len(shown) > 1 else shown[0]) + ")"
    if key == "pg":
        through = f' Telegraf signs in through "{found[0]}".' if found else ""
        return f"{name}: every database on that server is watched{listed}, not one of them alone.{through}"
    return f"{name}: the whole server is watched, with every database on it{listed}."


def database_scopes(choices: "CollectChoices", keys: Iterable[str]) -> List[str]:
    wanted = set(keys)
    return [database_scope(key, choices.details.get(key, {})) for key in DATABASE_KEYS if key in wanted]


def placeholder_for(key: str) -> str:
    """The placeholder written the way that database needs a password quoted, so a real one drops straight in."""
    quotes = {"pg": "'", "mssql": '"'}.get(key, "")
    return f"{quotes}{PASSWORD_PLACEHOLDER}{quotes}"


def _escaped_password(key: str, password: str) -> str:
    if key == "pg":
        return password.replace("\\", "\\\\").replace("'", "\\'")
    if key == "mssql":
        return password.replace('"', '""')
    return password


def quoted_password(key: str, password: str) -> str:
    """The password as the database setting needs it written, quoted when it holds anything a parser would split on."""
    import re

    if key == "pg" and password and not re.fullmatch(r"[^\s'\\]+", password):
        return f"'{_escaped_password(key, password)}'"
    if key == "mssql" and password and not re.fullmatch(r"[^\s;\"'{}]+", password):
        return f'"{_escaped_password(key, password)}"'
    return password


# scheme://user:password@host: the password runs to the last "@", so one that holds an "@" is still covered
_URI_PASSWORD = r"^\s*[A-Za-z][A-Za-z0-9+.-]*://[^:/?#@]*:(?P<pw>.*)@"
_URI_QUERY_PASSWORD = r"[?&]password=(?P<pw>[^&#\s]*)"


def _is_uri(raw: str) -> bool:
    import re

    return bool(re.match(r"^\s*[A-Za-z][A-Za-z0-9+.-]*://", raw))


def _secret_spans(key: str, raw: str) -> List[tuple]:
    """Where the password sits in a deployed setting: (start, end, quoted) for each place.

    A password may itself contain "@", ";" or spaces, so each form is matched by its own structure
    rather than by what a password usually looks like. `quoted` says the place takes a quoted value
    (key=value forms), as opposed to a bare one (inside an address).
    """
    import re

    spans: List[tuple] = []

    def take(pattern: str, quoted: bool, flags: int = re.IGNORECASE | re.DOTALL, once: bool = False) -> bool:
        hit = False
        for match in re.finditer(pattern, raw, flags):
            start, end = match.span("pw")
            if end > start and not any(s < end and start < e for s, e, _q in spans):
                spans.append((start, end, quoted))
                hit = True
            if once:
                break
        return hit

    if _is_uri(raw):
        # postgres://user:password@host/db, sqlserver://user:password@host, http://user:password@host/status
        take(_URI_PASSWORD, False, once=True)
        take(_URI_QUERY_PASSWORD, False)
    elif key == "mysql":
        # user:password@protocol(address): the password runs to the last "@" before the protocol
        if not take(r"^[^:@/]*:(?P<pw>.*)@(?=[A-Za-z0-9_]*\()", False, once=True):
            # user:password@/database, with no protocol named
            take(r"^[^:@/]*:(?P<pw>.*)@(?=/)", False, once=True)
    elif key == "pg":
        take(r"password\s*=\s*(?P<pw>'(?:[^'\\]|\\.)*'|\"[^\"]*\"|[^\s]+)", True)
    elif key == "mssql":
        take(r"(?:Password|PWD)\s*=\s*(?P<pw>\"(?:[^\"]|\"\")*\"|'(?:[^']|'')*'|\{[^}]*\}|[^;]*)", True)
    return sorted(spans)


def _mask_password(key: str, raw: str, for_command: bool = False) -> str:
    """A deployed setting with its password replaced by the placeholder.

    `for_command` writes the placeholder the way the shown command needs it: already quoted
    where the setting takes a quoted value, so a real password drops straight in.
    """
    out = raw
    for start, end, quoted in reversed(_secret_spans(key, raw)):
        shown = placeholder_for(key) if (for_command and quoted) else PASSWORD_PLACEHOLDER
        out = out[:start] + shown + out[end:]
    return out


def mask_web_address(url: str) -> str:
    """A status page address with any password in it replaced by the placeholder."""
    return _mask_password("web", url) if url else url


def secrets_in(choices: "CollectChoices") -> List[str]:
    """Every password these choices hold, for redacting anything that is written down."""
    found: List[str] = []

    def held_in(key: str, raw: str) -> None:
        for start, end, _quoted in _secret_spans(key, raw):
            secret = raw[start:end]
            found.append(secret.strip("'\"{}") or secret)
            found.append(secret)

    for key in DATABASE_KEYS:
        detail = choices.details.get(key, {})
        if detail.get("password"):
            found.append(str(detail["password"]))
            found.append(_escaped_password(key, str(detail["password"])))
        held_in(key, str(detail.get("raw") or ""))
    for key in DEFAULT_WEB_URL:
        held_in("web", str(choices.details.get(key, {}).get("url") or ""))
    return [s for s in dict.fromkeys(found) if s]


def keys_from_monitoring(mon: MonitoringConfig, os_family: str) -> List[str]:
    """The choices a machine's current settings stand for."""
    windows = os_family == "windows"
    keys: List[str] = []
    if windows:
        if mon.win_perf_counters.enabled or mon.win_os.enabled:
            keys += BASIC_KEYS
        if mon.win_services.enabled and any(s != SELF_CHECK_SERVICE for s in mon.win_services.service_names):
            keys.append("winsvc")
        if mon.win_perf_counters.enabled and mon.win_perf_counters.additional_objects:
            keys.append("winperf")
    else:
        for key, plugin in (("cpu", mon.cpu), ("mem", mon.mem), ("disk", mon.disk), ("net", mon.net),
                            ("system", mon.system), ("swap", mon.swap), ("diskio", mon.diskio), ("procs", mon.processes)):
            if plugin.enabled:
                keys.append(key)
    for key, plugin in (("nginx", mon.nginx), ("apache", mon.apache), ("mysql", mon.mysql), ("pg", mon.postgresql),
                        ("mssql", mon.mssql), ("docker", mon.docker), ("ping", mon.ping)):
        if plugin.enabled:
            keys.append(key)
    return ordered(keys)


def choices_from_monitoring(mon: MonitoringConfig, os_family: str) -> CollectChoices:
    """Choices, with their answers, read back from a machine's current settings."""
    keys = keys_from_monitoring(mon, os_family)
    details: Dict[str, Dict[str, Any]] = {}
    notes: Dict[str, str] = {}
    if "winsvc" in keys:
        names = [s for s in mon.win_services.service_names if s != SELF_CHECK_SERVICE]
        details["winsvc"] = {"names": names}
        notes["winsvc"] = f"{len(names)} service{'s' if len(names) != 1 else ''} watched."
    if "winperf" in keys:
        objects = [o.model_dump() for o in mon.win_perf_counters.additional_objects]
        details["winperf"] = {"objects": objects}
        notes["winperf"] = f"{len(objects)} group{'s' if len(objects) != 1 else ''} of counters added."
    if "ping" in keys and mon.ping.urls:
        details["ping"] = {"addr": mon.ping.urls[0]}
        notes["ping"] = f"Pings {mon.ping.urls[0]}."
    for key, urls in (("nginx", mon.nginx.urls), ("apache", mon.apache.urls)):
        if key in keys and urls and urls[0]:
            # The address that works on this machine today is kept, whatever else is changed
            details[key] = {"url": urls[0]}
    for key, raw in (("mysql", mon.mysql.servers[0] if mon.mysql.servers else ""),
                     ("pg", mon.postgresql.address),
                     ("mssql", mon.mssql.servers[0] if mon.mssql.servers else "")):
        if key in keys:
            # The deployed setting is kept as it is; the app never reads a database password back
            details[key] = {"raw": raw}
            notes[key] = "Already watched."
    choices = CollectChoices(keys=keys, details=details, notes=notes)
    return choices


def read_deployed_choices(fragment_text: Optional[str], os_family: str) -> Optional[CollectChoices]:
    """Choices from the settings file this app writes on a machine; None when there is none."""
    if not fragment_text or not fragment_text.strip():
        return None
    from vcf_ops_telegraf_helper.workflow.managed_config import import_managed_config

    imported = import_managed_config(None, {"vcf-helper-system.conf": fragment_text}, is_windows=os_family == "windows")
    if not imported.ok:
        return None
    choices = choices_from_monitoring(imported.monitoring, os_family)
    # Settings outside the catalogue (someone's own additions) are carried along, never dropped
    from vcf_ops_telegraf_helper.workflow.managed_config import RETAINED_HEADER

    choices.custom_toml = (imported.monitoring.custom_toml or "").replace(RETAINED_HEADER + "\n", "").strip()
    return choices
