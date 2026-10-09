"""Port the Ops product-managed agent's Telegraf configuration into the helper's model.

The managed agent on VCF Operations 9.1 ships a single telegraf.conf (telegraf.d empty by
default) with an [agent] table, an outputs.http to https://<collector>/arc/metric, an
inputs.exec running mandatory_tags.bat, the Windows perf counter set, and inputs.cpu/mem/swap
prefixed "win.". Everything the helper renders itself is dropped and replaced. A plugin
instance is mapped onto the structured catalog only when the catalog can express every one of
its settings; anything else is retained verbatim as a custom TOML fragment, so no setting is
lost silently. A fragment that cannot be parsed blocks the import rather than being guessed at.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

try:
    import tomllib
except ImportError:  # Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

import tomli_w

from vcf_ops_telegraf_helper.models.monitoring import (
    ApacheInputConfig,
    DockerInputConfig,
    MonitoringConfig,
    MssqlInputConfig,
    MysqlInputConfig,
    NginxInputConfig,
    PerfmonObject,
    PingInputConfig,
    PostgresqlInputConfig,
    WindowsOsInputConfig,
    WinPerfCountersInputConfig,
    WinServicesInputConfig,
)
from vcf_ops_telegraf_helper.renderer.renderer import TelegrafRenderer

RETAINED_HEADER = "# Retained from the Ops-managed agent configuration (not editable in the structured catalog)"

_SECRET_PATTERNS = [
    # key = "value", key = 'value', key=value inside connection strings
    re.compile(r"(?i)\b(password|passwd|pwd|token|secret|api_key|client_secret)(\s*=\s*)(?:\"[^\"]*\"|'[^']*'|[^;,\s\"']+)"),
    # scheme://user:password@host
    re.compile(r"(?i)(://[^/:@\s]+:)([^@\s]+)(@)"),
]


def mask_secrets(text: str) -> str:
    """Mask password-like values (TOML strings, connection strings, URL credentials) for display."""
    def _mask(m: "re.Match[str]") -> str:
        value = m.group(0)[len(m.group(1)) + len(m.group(2)):]
        quote = value[0] if value[:1] in ("\"", "'") else ""
        return f"{m.group(1)}{m.group(2)}{quote}***{quote}"

    masked = _SECRET_PATTERNS[0].sub(_mask, text)
    return _SECRET_PATTERNS[1].sub(r"\1***\3", masked)


@dataclass
class ImportedConfig:
    """Result of porting a managed configuration."""

    monitoring: MonitoringConfig
    preserved: List[str] = field(default_factory=list)   # mapped one-to-one onto the structured model
    added: List[str] = field(default_factory=list)       # helper additions the managed agent lacked
    dropped: List[str] = field(default_factory=list)     # replaced by what the helper renders itself
    retained: List[str] = field(default_factory=list)    # carried verbatim into the custom TOML fragment
    changed: List[str] = field(default_factory=list)     # same plugin, different settings
    warnings: List[str] = field(default_factory=list)
    blocked: List[str] = field(default_factory=list)     # the import cannot be trusted; do not cut over
    source_files: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.blocked

    def summary_lines(self) -> List[str]:
        lines = []
        for title, items in (
            ("Blocked", self.blocked),
            ("Preserved", self.preserved),
            ("Added", self.added),
            ("Changed", self.changed),
            ("Retained verbatim", self.retained),
            ("Dropped (replaced by the helper)", self.dropped),
            ("Warnings", self.warnings),
        ):
            if items:
                lines.append(f"{title}:")
                lines.extend(f"  - {mask_secrets(item)}" for item in items)
        return lines


# Keys the structured catalog can express for each known plugin (beyond the plugin table itself)
_EXPRESSIBLE: Dict[str, set] = {
    "cpu": {"percpu", "totalcpu", "collect_cpu_time", "report_active"},
    "mem": set(),
    "swap": set(),
    "system": set(),
    "disk": {"mount_points", "ignore_fs"},
    "net": {"interfaces"},
    "diskio": {"devices"},
    "processes": set(),
    "win_services": {"service_names"},
    "nginx": {"urls"},
    "apache": {"urls"},
    "mysql": {"servers"},
    "postgresql": {"address"},
    "sqlserver": {"servers"},
    "docker": {"endpoint"},
    "ping": {"urls", "count"},
    "win_perf_counters": {"object", "PrintValid"},
}
_PERF_OBJECT_KEYS = {"ObjectName", "Counters", "Instances", "Measurement", "IncludeTotal", "UseRawValues",
                     "WarnOnMissing", "FailOnMissing"}


def _as_list(value: Any) -> List[Dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def _baseline_perf_objects() -> Dict[str, Dict[str, Any]]:
    rendered = TelegrafRenderer.render_system_inputs(
        MonitoringConfig(win_perf_counters=WinPerfCountersInputConfig(enabled=True))
    )
    objects = tomllib.loads(rendered)["inputs"]["win_perf_counters"][0]["object"]
    return {o["ObjectName"]: o for o in objects}


def _measurement_key(name: str) -> str:
    # The wavefront output turns '_' into '.', so win_cpu and win.cpu are the same series in Ops
    return (name or "").replace("_", ".").lower()


def _merge_retained(into: Dict[str, Any], table: str, value: Any, source: str, result: ImportedConfig) -> None:
    """Merge a retained top-level table from one source into the accumulated retained tables."""
    if table not in into:
        into[table] = value
        return
    existing = into[table]
    if isinstance(existing, dict) and isinstance(value, dict):
        for key, sub in value.items():
            if key not in existing:
                existing[key] = sub
            elif isinstance(existing[key], list) and isinstance(sub, list):
                existing[key].extend(sub)
            elif isinstance(existing[key], dict) and isinstance(sub, dict):
                existing[key].update(sub)
                result.warnings.append(f"[{table}.{key}] from {source} overrides earlier keys with the same name")
            else:
                existing[key] = sub
                result.warnings.append(f"[{table}] {key} from {source} overrides an earlier value")
    elif isinstance(existing, list) and isinstance(value, list):
        existing.extend(value)
    else:
        into[table] = value
        result.warnings.append(f"[{table}] from {source} replaced an earlier table of a different shape")


def import_managed_config(
    telegraf_conf: Optional[str],
    fragments: Optional[Dict[str, str]] = None,
    is_windows: bool = True,
) -> ImportedConfig:
    """Map a managed telegraf.conf plus telegraf.d fragments onto a MonitoringConfig."""
    fragments = fragments or {}
    monitoring = MonitoringConfig.baseline_for_os(is_windows)
    # Start from nothing enabled: the import decides what the managed agent actually collected
    for name in ("cpu", "mem", "disk", "net", "system", "swap", "diskio", "processes"):
        getattr(monitoring, name).enabled = False
    monitoring.win_perf_counters = WinPerfCountersInputConfig(enabled=False)
    monitoring.win_os = WindowsOsInputConfig(enabled=False, cpu=False, mem=False, swap=False)
    monitoring.win_services = WinServicesInputConfig(enabled=False)
    result = ImportedConfig(monitoring=monitoring)

    sources: List[Tuple[str, str]] = []
    if telegraf_conf:
        sources.append(("telegraf.conf", telegraf_conf))
    for name in sorted(fragments):
        sources.append((f"telegraf.d\\{name}", fragments[name]))
    if not sources:
        result.warnings.append("No managed configuration was read; starting from the OS baseline.")
        result.monitoring = MonitoringConfig.baseline_for_os(is_windows)
        return result

    retained: Dict[str, Any] = {}
    mapped_plugins: set = set()

    for source_name, text in sources:
        try:
            parsed = tomllib.loads(text)
        except Exception as exc:
            result.blocked.append(f"{source_name}: could not parse TOML ({exc}); resolve it before taking over")
            continue
        result.source_files.append(source_name)

        for table, value in parsed.items():
            if table == "agent":
                interval = str(value.get("interval", "")) if isinstance(value, dict) else ""
                if interval and interval != "300s":
                    result.changed.append(f"[agent] interval {interval} becomes 300s (helper standard)")
                result.dropped.append("[agent] table (helper renders its own)")
                continue
            if table == "outputs":
                for plugin, instances in (value or {}).items():
                    for inst in _as_list(instances):
                        url = str(inst.get("url", inst.get("urls", "")))
                        if plugin == "http" and "/arc/metric" in url:
                            result.dropped.append(f"[[outputs.http]] {url} (replaced by the helper's cloud proxy output)")
                        else:
                            result.retained.append(f"[[outputs.{plugin}]] {url or ''}".rstrip())
                            result.warnings.append(f"[[outputs.{plugin}]] retained: check its paths and credentials still apply")
                            _merge_retained(retained, "outputs", {plugin: [inst]}, source_name, result)
                continue
            if table == "inputs":
                for plugin, instances in (value or {}).items():
                    for inst in _as_list(instances):
                        _map_input(plugin, inst, result, retained, mapped_plugins, source_name)
                continue
            # processors, aggregators, global_tags, anything else; an empty table carries nothing
            if not value:
                continue
            result.retained.append(f"[{table}] table")
            _merge_retained(retained, table, value, source_name, result)

    # Helper additions the managed stock config lacks
    if is_windows and not result.monitoring.win_services.enabled and "win_services" not in mapped_plugins:
        result.monitoring.win_services = WinServicesInputConfig(enabled=True, service_names=["telegraf"])
        result.added.append("[[inputs.win_services]] telegraf (agent self-check)")

    if retained:
        result.monitoring.custom_toml = RETAINED_HEADER + "\n" + tomli_w.dumps(retained).strip() + "\n"
    return result


def _retain(plugin: str, inst: Dict[str, Any], reason: str, result: ImportedConfig, retained: Dict[str, Any],
            source: str) -> None:
    prefix = str(inst.get("name_prefix", ""))
    label = f"[[inputs.{plugin}]]" + (f' name_prefix "{prefix}"' if prefix else "")
    result.retained.append(f"{label} ({reason})")
    _merge_retained(retained, "inputs", {plugin: [inst]}, source, result)


def _map_input(plugin: str, inst: Dict[str, Any], result: ImportedConfig, retained: Dict[str, Any],
               mapped: set, source: str) -> None:
    mon = result.monitoring
    prefix = str(inst.get("name_prefix", ""))
    keys = set(inst)

    if plugin == "exec":
        commands = [str(c) for c in inst.get("commands", [])]
        if len(commands) == 1 and "mandatory_tags" in commands[0].lower():
            result.dropped.append("[[inputs.exec]] mandatory_tags (replaced by the helper's own tag script)")
            return
        _retain(plugin, inst, "custom exec command", result, retained, source)
        return

    if plugin == "win_perf_counters":
        if plugin in mapped:
            _retain(plugin, inst, "second instance; the catalog holds one", result, retained, source)
            return
        extra = keys - _EXPRESSIBLE[plugin]
        if extra:
            _retain(plugin, inst, f"plugin options the catalog cannot express: {', '.join(sorted(extra))}",
                    result, retained, source)
            return
        objects = _as_list(inst.get("object"))
        if not _map_perf_objects(objects, result):
            _retain(plugin, inst, "object set the catalog cannot express", result, retained, source)
            return
        mon.win_perf_counters.print_valid = bool(inst.get("PrintValid", True))
        mapped.add(plugin)
        return

    if plugin in ("cpu", "mem", "swap") and prefix == "win.":
        extra = keys - _EXPRESSIBLE[plugin] - {"name_prefix"}
        standard = all(inst.get(k, True) is True for k in ("percpu", "totalcpu", "collect_cpu_time", "report_active"))
        if extra or not standard or getattr(mon.win_os, plugin):
            _retain(plugin, inst, "options differ from the Windows OS totals the catalog renders", result, retained, source)
            return
        mon.win_os.enabled = True
        setattr(mon.win_os, plugin, True)
        result.preserved.append(f'[[inputs.{plugin}]] name_prefix "win." (Windows OS totals)')
        return

    if plugin in _EXPRESSIBLE and plugin != "win_perf_counters":
        extra = keys - _EXPRESSIBLE[plugin]
        if plugin in mapped:
            _retain(plugin, inst, "second instance; the catalog holds one", result, retained, source)
            return
        if extra:
            _retain(plugin, inst, f"options the catalog cannot express: {', '.join(sorted(extra))}",
                    result, retained, source)
            return
        if plugin == "cpu":
            for k in ("percpu", "totalcpu", "collect_cpu_time", "report_active"):
                setattr(mon.cpu, k, bool(inst.get(k, True)))
        if plugin == "disk":
            # Lossless: the managed stanza's filters, or none at all, never the catalog defaults
            mon.disk.mount_points = [str(m) for m in inst["mount_points"]] if "mount_points" in inst else None
            mon.disk.ignore_fs = [str(f) for f in inst["ignore_fs"]] if "ignore_fs" in inst else []
        if plugin == "net" and "interfaces" in inst:
            mon.net.interfaces = [str(i) for i in inst["interfaces"]]
        if plugin == "diskio" and "devices" in inst:
            mon.diskio.devices = [str(d) for d in inst["devices"]]
        if plugin in ("cpu", "mem", "disk", "net", "system", "swap", "diskio", "processes"):
            getattr(mon, plugin).enabled = True
            if plugin in ("cpu", "mem", "disk", "net", "system", "swap", "processes") and mon.win_perf_counters.enabled:
                result.warnings.append(f"[[inputs.{plugin}]] without a win. prefix reports Linux-style metrics on a Windows host")
        elif plugin == "win_services":
            mon.win_services = WinServicesInputConfig(
                enabled=True, service_names=[str(n) for n in inst.get("service_names", [])] or ["telegraf"]
            )
        elif plugin == "nginx":
            mon.nginx = NginxInputConfig(enabled=True, urls=[str(u) for u in inst.get("urls", [])] or mon.nginx.urls)
        elif plugin == "apache":
            mon.apache = ApacheInputConfig(enabled=True, urls=[str(u) for u in inst.get("urls", [])] or mon.apache.urls)
        elif plugin == "mysql":
            mon.mysql = MysqlInputConfig(enabled=True, servers=[str(s) for s in inst.get("servers", [])] or mon.mysql.servers)
        elif plugin == "postgresql":
            mon.postgresql = PostgresqlInputConfig(enabled=True, address=str(inst.get("address") or mon.postgresql.address))
        elif plugin == "sqlserver":
            mon.mssql = MssqlInputConfig(enabled=True, servers=[str(s) for s in inst.get("servers", [])] or mon.mssql.servers)
        elif plugin == "docker":
            mon.docker = DockerInputConfig(enabled=True, endpoint=str(inst.get("endpoint") or mon.docker.endpoint))
        elif plugin == "ping":
            mon.ping = PingInputConfig(enabled=True, urls=[str(u) for u in inst.get("urls", [])] or mon.ping.urls,
                                       count=int(inst.get("count", 1)))
        mapped.add(plugin)
        result.preserved.append(f"[[inputs.{plugin}]]")
        return

    _retain(plugin, inst, "not in the structured catalog", result, retained, source)


def _map_perf_objects(objects: List[Dict[str, Any]], result: ImportedConfig) -> bool:
    """Compare the managed counter set with the helper's baseline, object by object.

    Returns False when the objects cannot be expressed structurally (duplicate names, unknown keys),
    in which case the caller retains the whole instance verbatim and nothing here is applied.
    """
    names = [str(o.get("ObjectName", "")) for o in objects]
    if len(set(names)) != len(names):
        dupes = sorted({n for n in names if names.count(n) > 1})
        result.warnings.append(f"win_perf_counters: object names repeated ({', '.join(dupes)}); instance retained verbatim")
        return False
    for obj in objects:
        unknown = set(obj) - _PERF_OBJECT_KEYS
        if unknown:
            result.warnings.append(
                f"win_perf_counters object {obj.get('ObjectName')}: keys the catalog cannot express ({', '.join(sorted(unknown))}); instance retained verbatim"
            )
            return False

    baseline = _baseline_perf_objects()
    mon = result.monitoring
    config = WinPerfCountersInputConfig(enabled=True)
    additional: List[PerfmonObject] = []
    matched = 0
    for obj in objects:
        name = str(obj.get("ObjectName", ""))
        counters = [str(c) for c in obj.get("Counters", [])]
        instances = [str(i) for i in obj.get("Instances", [])]
        measurement = str(obj.get("Measurement", ""))
        options = {k: obj[k] for k in ("IncludeTotal", "UseRawValues", "WarnOnMissing", "FailOnMissing") if k in obj}
        base = baseline.get(name)
        if base is None:
            additional.append(PerfmonObject(object_name=name, counters=counters, instances=instances,
                                            measurement=measurement or f"win_{name.lower().replace(' ', '_')}",
                                            options=options))
            result.preserved.append(f"win_perf_counters object {name} (added to the baseline set)")
            continue
        if name == "Process":
            config.process_instances = list(dict.fromkeys(instances)) or config.process_instances
        base_instances = base.get("Instances", [])
        extra_counters = [c for c in counters if c not in base.get("Counters", [])]
        extra_instances: List[str] = []
        if name != "Process":
            if "*" in base_instances:
                # The helper collects every instance; a narrower managed selection widens, say so
                if instances and "*" not in instances:
                    result.changed.append(
                        f"win_perf_counters {name}: instances {', '.join(instances)} become * (helper baseline collects all)"
                    )
            else:
                extra_instances = [i for i in instances if i not in base_instances]
        missing = [c for c in base.get("Counters", []) if c not in counters]
        if measurement and _measurement_key(measurement) != _measurement_key(base.get("Measurement", "")):
            result.changed.append(
                f"win_perf_counters {name}: measurement {measurement} becomes {base.get('Measurement')} "
                "(the series name in Ops changes; dashboards built on the old name need updating)"
            )
        for key, value in options.items():
            if base.get(key, False) != value:
                result.changed.append(f"win_perf_counters {name}: {key} {value} becomes {base.get(key, False)} (helper baseline)")
        if extra_counters or extra_instances:
            additional.append(PerfmonObject(object_name=name, counters=extra_counters, instances=extra_instances,
                                            measurement=base.get("Measurement", measurement)))
            result.changed.append(f"win_perf_counters {name}: extra counters/instances carried ({', '.join(extra_counters + extra_instances)})")
        if missing:
            result.added.append(f"win_perf_counters {name}: baseline counters the managed agent lacked ({', '.join(missing)})")
        matched += 1
    config.additional_objects = additional
    mon.win_perf_counters = config
    if matched:
        result.preserved.append(f"[[inputs.win_perf_counters]] {matched} baseline objects")
    return True
