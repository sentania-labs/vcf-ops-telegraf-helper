"""Port the Ops product-managed agent's Telegraf configuration into the helper's model.

The managed agent on VCF Operations 9.1 ships a single telegraf.conf (telegraf.d empty by
default) with an [agent] table, an outputs.http to https://<collector>/arc/metric, an
inputs.exec running mandatory_tags.bat, the Windows perf counter set, and inputs.cpu/mem/swap
prefixed "win.". Everything the helper renders itself is dropped and replaced; everything it
can express structurally is mapped; the rest is retained verbatim as a custom TOML fragment so
nothing is silently lost.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

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

_SECRET_KEY = re.compile(r"(password|passwd|pwd|token|secret|api_key)\s*=\s*([^;,\s\"']+)", re.IGNORECASE)


def mask_secrets(text: str) -> str:
    """Mask password-like values in connection strings for display."""
    return _SECRET_KEY.sub(lambda m: f"{m.group(1)}=***", text)


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
    source_files: List[str] = field(default_factory=list)

    def summary_lines(self) -> List[str]:
        lines = []
        for title, items in (
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

    sources: List[tuple[str, str]] = []
    if telegraf_conf:
        sources.append(("telegraf.conf", telegraf_conf))
    for name in sorted(fragments):
        sources.append((f"telegraf.d\\{name}", fragments[name]))
    if not sources:
        result.warnings.append("No managed configuration was read; starting from the OS baseline.")
        result.monitoring = MonitoringConfig.baseline_for_os(is_windows)
        return result

    retained: Dict[str, Any] = {}
    perf_objects: List[Dict[str, Any]] = []
    win_perf_seen = False

    for source_name, text in sources:
        try:
            parsed = tomllib.loads(text)
        except Exception as exc:
            result.warnings.append(f"{source_name}: could not parse TOML ({exc}); fragment retained verbatim")
            result.retained.append(f"{source_name} (unparsed)")
            retained.setdefault("_verbatim", []).append((source_name, text))
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
                            retained.setdefault("outputs", {}).setdefault(plugin, []).append(inst)
                continue
            if table == "inputs":
                for plugin, instances in (value or {}).items():
                    for inst in _as_list(instances):
                        _map_input(plugin, inst, result, retained, perf_objects)
                        if plugin == "win_perf_counters":
                            win_perf_seen = True
                continue
            # processors, aggregators, global_tags, anything else; an empty table carries nothing
            if not value:
                continue
            result.retained.append(f"[{table}] table")
            retained[table] = value

    if win_perf_seen:
        _map_perf_objects(perf_objects, result)

    # Helper additions the managed stock config lacks
    if is_windows and not result.monitoring.win_services.enabled:
        result.monitoring.win_services = WinServicesInputConfig(enabled=True, service_names=["telegraf"])
        result.added.append("[[inputs.win_services]] telegraf (agent self-check)")

    verbatim = retained.pop("_verbatim", [])
    custom_parts: List[str] = []
    if retained:
        custom_parts.append(tomli_w.dumps(retained).strip())
    for source_name, text in verbatim:
        custom_parts.append(f"# {source_name}\n{text.strip()}")
    if custom_parts:
        result.monitoring.custom_toml = RETAINED_HEADER + "\n" + "\n\n".join(custom_parts) + "\n"
    return result


def _map_input(plugin: str, inst: Dict[str, Any], result: ImportedConfig, retained: Dict[str, Any],
               perf_objects: List[Dict[str, Any]]) -> None:
    mon = result.monitoring
    prefix = str(inst.get("name_prefix", ""))
    if plugin == "exec":
        commands = " ".join(str(c) for c in inst.get("commands", []))
        if "mandatory_tags" in commands.lower():
            result.dropped.append("[[inputs.exec]] mandatory_tags (replaced by the helper's own tag script)")
            return
    if plugin == "win_perf_counters":
        perf_objects.extend(_as_list(inst.get("object")))
        return
    if plugin in ("cpu", "mem", "swap") and prefix == "win.":
        mon.win_os.enabled = True
        setattr(mon.win_os, plugin, True)
        result.preserved.append(f'[[inputs.{plugin}]] name_prefix "win." (Windows OS totals)')
        return
    if plugin in ("cpu", "mem", "disk", "net", "system", "swap", "diskio", "processes") and not prefix:
        getattr(mon, plugin).enabled = True
        result.preserved.append(f"[[inputs.{plugin}]]")
        extras = {k: v for k, v in inst.items() if k not in ("percpu", "totalcpu", "collect_cpu_time", "report_active")}
        if plugin == "disk" and "mount_points" in extras:
            mon.disk.mount_points = list(extras.pop("mount_points"))
        if plugin == "disk" and "ignore_fs" in extras:
            mon.disk.ignore_fs = list(extras.pop("ignore_fs"))
        if plugin == "net" and "interfaces" in extras:
            mon.net.interfaces = list(extras.pop("interfaces"))
        if plugin == "diskio" and "devices" in extras:
            mon.diskio.devices = list(extras.pop("devices"))
        if extras:
            result.changed.append(f"[[inputs.{plugin}]] options not carried: {', '.join(sorted(extras))}")
        return
    if plugin == "win_services":
        names = [str(n) for n in inst.get("service_names", [])] or ["telegraf"]
        mon.win_services = WinServicesInputConfig(enabled=True, service_names=names)
        result.preserved.append(f"[[inputs.win_services]] {', '.join(names)}")
        return
    if plugin == "nginx" and inst.get("urls"):
        mon.nginx = NginxInputConfig(enabled=True, urls=[str(u) for u in inst["urls"]])
        result.preserved.append("[[inputs.nginx]]")
        return
    if plugin == "apache" and inst.get("urls"):
        mon.apache = ApacheInputConfig(enabled=True, urls=[str(u) for u in inst["urls"]])
        result.preserved.append("[[inputs.apache]]")
        return
    if plugin == "mysql" and inst.get("servers"):
        mon.mysql = MysqlInputConfig(enabled=True, servers=[str(s) for s in inst["servers"]])
        result.preserved.append("[[inputs.mysql]]")
        return
    if plugin == "postgresql" and inst.get("address"):
        mon.postgresql = PostgresqlInputConfig(enabled=True, address=str(inst["address"]))
        result.preserved.append("[[inputs.postgresql]]")
        return
    if plugin == "sqlserver" and inst.get("servers"):
        mon.mssql = MssqlInputConfig(enabled=True, servers=[str(s) for s in inst["servers"]])
        result.preserved.append("[[inputs.sqlserver]]")
        return
    if plugin == "docker" and inst.get("endpoint"):
        mon.docker = DockerInputConfig(enabled=True, endpoint=str(inst["endpoint"]))
        result.preserved.append("[[inputs.docker]]")
        return
    if plugin == "ping" and inst.get("urls"):
        mon.ping = PingInputConfig(enabled=True, urls=[str(u) for u in inst["urls"]], count=int(inst.get("count", 1)))
        result.preserved.append("[[inputs.ping]]")
        return
    # Anything else is carried verbatim
    result.retained.append(f"[[inputs.{plugin}]]" + (f' name_prefix "{prefix}"' if prefix else ""))
    retained.setdefault("inputs", {}).setdefault(plugin, []).append(inst)


def _map_perf_objects(objects: List[Dict[str, Any]], result: ImportedConfig) -> None:
    """Compare the managed counter set with the helper's baseline, object by object."""
    baseline = _baseline_perf_objects()
    mon = result.monitoring
    mon.win_perf_counters = WinPerfCountersInputConfig(enabled=True)
    additional: List[PerfmonObject] = []
    matched = 0
    for obj in objects:
        name = str(obj.get("ObjectName", ""))
        counters = [str(c) for c in obj.get("Counters", [])]
        instances = [str(i) for i in obj.get("Instances", ["*"])]
        measurement = str(obj.get("Measurement", ""))
        base = baseline.get(name)
        if base is None:
            additional.append(PerfmonObject(object_name=name, counters=counters, instances=instances,
                                            measurement=measurement or f"win_{name.lower().replace(' ', '_')}"))
            result.preserved.append(f"win_perf_counters object {name} (added to the baseline set)")
            continue
        if name == "Process":
            mon.win_perf_counters.process_instances = list(dict.fromkeys(instances))
        extra_counters = [c for c in counters if c not in base.get("Counters", [])]
        extra_instances = [i for i in instances if i not in base.get("Instances", []) and name != "Process"]
        missing = [c for c in base.get("Counters", []) if c not in counters]
        if _measurement_key(measurement) != _measurement_key(base.get("Measurement", "")) and measurement:
            result.changed.append(f"win_perf_counters {name}: measurement {measurement} becomes {base.get('Measurement')} (same series in Ops)")
        if extra_counters or extra_instances:
            additional.append(PerfmonObject(object_name=name, counters=extra_counters or [],
                                            instances=extra_instances or [], measurement=base.get("Measurement", measurement)))
            result.changed.append(f"win_perf_counters {name}: extra counters/instances carried ({', '.join(extra_counters + extra_instances)})")
        if missing:
            result.added.append(f"win_perf_counters {name}: baseline counters the managed agent lacked ({', '.join(missing)})")
        matched += 1
    mon.win_perf_counters.additional_objects = [a for a in additional if a.counters or a.instances or a.object_name not in baseline]
    if matched:
        result.preserved.append(f"[[inputs.win_perf_counters]] {matched} baseline objects")
