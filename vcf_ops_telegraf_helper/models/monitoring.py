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
