"""Monitoring input configuration models."""

from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel, Field


class CpuInputConfig(BaseModel):
    """Configuration for Telegraf CPU input plugin."""

    enabled: bool = True
    percpu: bool = True
    totalcpu: bool = True
    collect_cpu_time: bool = True
    report_active: bool = True


class MemInputConfig(BaseModel):
    """Configuration for Telegraf memory input plugin."""

    enabled: bool = True


class DiskInputConfig(BaseModel):
    """Configuration for Telegraf disk usage input plugin."""

    enabled: bool = True
    mount_points: Optional[List[str]] = None
    ignore_fs: List[str] = Field(
        default_factory=lambda: [
            "tmpfs",
            "devtmpfs",
            "devfs",
            "iso9660",
            "overlay",
            "aufs",
            "squashfs",
        ]
    )


class NetInputConfig(BaseModel):
    """Configuration for Telegraf network interface input plugin."""

    enabled: bool = True
    interfaces: Optional[List[str]] = None


class SystemInputConfig(BaseModel):
    """Configuration for Telegraf system load and uptime plugin."""

    enabled: bool = True


class SwapInputConfig(BaseModel):
    """Configuration for Telegraf swap memory plugin."""

    enabled: bool = True


class DiskIoInputConfig(BaseModel):
    """Configuration for Telegraf disk I/O plugin."""

    enabled: bool = False
    devices: Optional[List[str]] = None


class ProcessesInputConfig(BaseModel):
    """Configuration for Telegraf processes count plugin."""

    enabled: bool = False


class WinPerfCountersInputConfig(BaseModel):
    """Configuration for Windows Performance Counters plugin."""

    enabled: bool = False


class WinServicesInputConfig(BaseModel):
    """Configuration for Windows Services status plugin."""

    enabled: bool = False
    service_names: List[str] = Field(default_factory=lambda: ["*"])


class NginxInputConfig(BaseModel):
    """Configuration for NGINX stub_status plugin."""

    enabled: bool = False
    urls: List[str] = Field(default_factory=lambda: ["http://localhost/status"])


class ApacheInputConfig(BaseModel):
    """Configuration for Apache server-status plugin."""

    enabled: bool = False
    urls: List[str] = Field(default_factory=lambda: ["http://localhost/server-status?auto"])


class MysqlInputConfig(BaseModel):
    """Configuration for MySQL/MariaDB server plugin."""

    enabled: bool = False
    servers: List[str] = Field(default_factory=lambda: ["tcp(127.0.0.1:3306)/"])


class PostgresqlInputConfig(BaseModel):
    """Configuration for PostgreSQL server plugin."""

    enabled: bool = False
    address: str = "host=localhost user=postgres sslmode=disable"


class MssqlInputConfig(BaseModel):
    """Configuration for Microsoft SQL Server plugin."""

    enabled: bool = False
    servers: List[str] = Field(
        default_factory=lambda: [
            "Server=127.0.0.1;Port=1433;User Id=sa;Password=;app name=telegraf;log=1;"
        ]
    )


class DockerInputConfig(BaseModel):
    """Configuration for Docker container metrics plugin."""

    enabled: bool = False
    endpoint: str = "unix:///var/run/docker.sock"


class PingInputConfig(BaseModel):
    """Configuration for ICMP/Ping reachability plugin."""

    enabled: bool = False
    urls: List[str] = Field(default_factory=lambda: ["10.10.10.1"])
    count: int = 1


class MonitoringConfig(BaseModel):
    """Aggregated monitoring configuration for an endpoint."""

    cpu: CpuInputConfig = Field(default_factory=CpuInputConfig)
    mem: MemInputConfig = Field(default_factory=MemInputConfig)
    disk: DiskInputConfig = Field(default_factory=DiskInputConfig)
    net: NetInputConfig = Field(default_factory=NetInputConfig)
    system: SystemInputConfig = Field(default_factory=SystemInputConfig)
    swap: SwapInputConfig = Field(default_factory=SwapInputConfig)
    diskio: DiskIoInputConfig = Field(default_factory=DiskIoInputConfig)
    processes: ProcessesInputConfig = Field(default_factory=ProcessesInputConfig)
    win_perf_counters: WinPerfCountersInputConfig = Field(default_factory=WinPerfCountersInputConfig)
    win_services: WinServicesInputConfig = Field(default_factory=WinServicesInputConfig)
    nginx: NginxInputConfig = Field(default_factory=NginxInputConfig)
    apache: ApacheInputConfig = Field(default_factory=ApacheInputConfig)
    mysql: MysqlInputConfig = Field(default_factory=MysqlInputConfig)
    postgresql: PostgresqlInputConfig = Field(default_factory=PostgresqlInputConfig)
    mssql: MssqlInputConfig = Field(default_factory=MssqlInputConfig)
    docker: DockerInputConfig = Field(default_factory=DockerInputConfig)
    ping: PingInputConfig = Field(default_factory=PingInputConfig)
    custom_toml: str = Field(default="", description="Optional custom TOML fragment injected into configuration")
