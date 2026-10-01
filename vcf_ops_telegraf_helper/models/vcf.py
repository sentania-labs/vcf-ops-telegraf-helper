"""VCF Operations domain models."""

from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field


class CollectorInfo(BaseModel):
    """Information regarding a VCF Operations Cloud Proxy or Collector Group."""

    address: str = Field(description="IP address or FQDN of the Cloud Proxy or Virtual IP")
    port: int = Field(default=443, description="HTTPS port for metric ingestion and bootstrap")
    is_collector_group: bool = Field(default=False, description="True if using an HA collector group")
    name: Optional[str] = Field(default=None, description="Optional collector group or proxy name")


class AuthToken(BaseModel):
    """Temporary Suite API token acquired from VCF Operations."""

    token: str = Field(description="Bearer token string")
    valid_until: Optional[str] = Field(default=None, description="Token expiration timestamp if known")


class VCFEnvironment(BaseModel):
    """VCF Operations environment target definition."""

    name: str = Field(default="default", description="Identifier for this environment")
    url: str = Field(description="Base URL for VCF Operations, e.g. https://vcf-ops.corp.local")
    username: str = Field(description="Username for authentication")
    password: Optional[str] = Field(default=None, description="Session password, not saved in plain text")
    auth_source: str = Field(default="local", description="Authentication source: local or Active Directory")
    collector: CollectorInfo = Field(description="Cloud Proxy or Collector destination")
    verify_ssl: bool = Field(default=True, description="Verify TLS certificates")
    ca_cert_path: Optional[str] = Field(default=None, description="Custom CA certificate bundle path")
    token: Optional[str] = Field(default=None, description="Active auth token if previously acquired")


class VirtualMachineResource(BaseModel):
    """Virtual machine inventory item discovered from VCF Operations."""

    resource_id: str = Field(description="VCF Operations internal resource identifier (UUID)")
    name: str = Field(description="Canonical virtual machine name")
    ip_address: Optional[str] = Field(default=None, description="Primary IPv4 address for guest transport")
    vm_mor: Optional[str] = Field(default=None, description="vCenter VM MOR, e.g. vm-1042")
    vc_id: Optional[str] = Field(default=None, description="vCenter instance UUID")
    os_name: Optional[str] = Field(default=None, description="Guest OS name string")
    os_family: str = Field(default="LINUX", description="Normalized OS family: WINDOWS or LINUX")
    collector_group: Optional[str] = Field(default=None, description="Assigned collector group or proxy name")
    telegraf_status: str = Field(default="MISSING", description="ACTIVE, STOPPED, or MISSING")
    telegraf_version: Optional[str] = Field(default=None, description="Installed telegraf version if known")

