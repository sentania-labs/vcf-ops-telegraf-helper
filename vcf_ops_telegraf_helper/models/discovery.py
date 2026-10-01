"""Live guest host and workload discovery domain models."""

from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field


class DiscoveredService(BaseModel):
    """Running or installed host service discovered on target endpoint."""

    name: str = Field(description="Internal service name, e.g. telegraf, MSSQLSERVER")
    display_name: Optional[str] = Field(default=None, description="Human-friendly service description")
    status: str = Field(default="Running", description="Current execution status: Running, Stopped")
    start_type: Optional[str] = Field(default=None, description="Service startup type: Automatic, Manual, Disabled")


class DiscoveredPerfmonSet(BaseModel):
    """Windows Perfmon counter set discovered on target host."""

    name: str = Field(description="Counter set object name, e.g. Processor, SQLServer:General Statistics")
    description: Optional[str] = Field(default=None, description="Explanation of counter object telemetry")
    counters: list[str] = Field(default_factory=list, description="List of counter names available in this set")


class DiscoveredDatabase(BaseModel):
    """Database catalog discovered from database server instance."""

    name: str = Field(description="Database or catalog name, e.g. master, OperationsDB")
    state: str = Field(default="ONLINE", description="Database state: ONLINE, OFFLINE, RESTORING")
    db_type: Optional[str] = Field(default="user", description="System or user catalog")
    size_mb: Optional[float] = Field(default=None, description="Reported database storage allocation in MB")


class DiscoveredVolume(BaseModel):
    """Storage volume or filesystem mount discovered on host."""

    mount_point: str = Field(description="Drive letter (C:, D:) on Windows or mount point (/, /var) on Linux")
    label: Optional[str] = Field(default=None, description="Filesystem volume label")
    fs_type: Optional[str] = Field(default=None, description="Filesystem format: NTFS, ReFS, ext4, xfs")
    free_mb: float = Field(default=0.0, description="Available free storage in megabytes")
    total_mb: float = Field(default=0.0, description="Total storage capacity in megabytes")
