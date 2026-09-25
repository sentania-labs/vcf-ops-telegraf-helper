"""Endpoint domain models."""

from __future__ import annotations

from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, model_validator


class OSFamily(str, Enum):
    """Supported operating system families."""

    LINUX = "linux"
    WINDOWS = "windows"


class ConnectionMethod(str, Enum):
    """Supported endpoint connectivity methods."""

    LOCAL = "local"
    MOCK = "mock"
    SSH = "ssh"
    WINRM = "winrm"
    PACKAGE = "package"


class EndpointTarget(BaseModel):
    """Specification of an endpoint to monitor."""

    hostname: str = Field(description="Target hostname or IP address")
    os_family: OSFamily = Field(default=OSFamily.LINUX, description="Target OS family")
    connection_method: ConnectionMethod = Field(
        default=ConnectionMethod.SSH, description="Connection mechanism"
    )
    port: int = Field(default=22, description="Remote port, e.g. 22 for SSH, 5985 for WinRM")
    username: Optional[str] = Field(default=None, description="Username for remote access")
    password: Optional[str] = Field(default=None, description="Password for remote access (session-only)")
    key_filename: Optional[str] = Field(default=None, description="Path to SSH private key file")
    sudo: bool = Field(default=True, description="Execute commands using sudo if non-root")
    winrm_use_ssl: bool = Field(default=False, description="Use HTTPS/SSL for WinRM transport")
    install_telegraf: bool = Field(default=False, description="Install Telegraf agent if missing")

    @model_validator(mode="after")
    def set_winrm_default_port(self) -> EndpointTarget:
        """Default port to 5985 (or 5986 if SSL) when WinRM is selected without explicit custom port."""
        if self.connection_method == ConnectionMethod.WINRM and self.port == 22:
            self.port = 5986 if self.winrm_use_ssl else 5985
        return self


class EndpointDiscoveryResult(BaseModel):
    """Results from inspecting an endpoint."""

    hostname: str
    os_name: str = Field(default="Linux")
    os_version: str = Field(default="")
    arch: str = Field(default="x86_64")
    telegraf_installed: bool = Field(default=False)
    telegraf_version: Optional[str] = Field(default=None)
    service_state: Optional[str] = Field(default=None, description="Service status e.g. active, inactive")
    config_dir: str = Field(default="/etc/telegraf/telegraf.d")
    main_config_path: str = Field(default="/etc/telegraf/telegraf.conf")
    telegraf_bin_path: str = Field(default="/usr/bin/telegraf")
    host_uuid: str = Field(default="", description="Target DMI UUID or machine-id")
    host_ip: str = Field(default="", description="Primary IP address of target")
