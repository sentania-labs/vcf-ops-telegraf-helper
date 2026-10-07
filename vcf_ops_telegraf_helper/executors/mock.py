"""Mock endpoint executor for deterministic testing and dry-run simulations."""

from __future__ import annotations

from typing import Dict, List, Optional, Union
from vcf_ops_telegraf_helper.executors.base import CommandResult, EndpointExecutor
from vcf_ops_telegraf_helper.models.discovery import (
    DiscoveredDatabase,
    DiscoveredPerfmonSet,
    DiscoveredService,
)


class MockExecutor(EndpointExecutor):
    """Simulated endpoint executor for testing without network dependencies."""

    def __init__(
        self,
        connected: bool = True,
        telegraf_installed: bool = True,
        telegraf_version: str = "Telegraf 1.30.0",
        service_active: bool = True,
        collector_reachable: bool = True,
        custom_responses: Optional[Dict[str, CommandResult]] = None,
    ):
        self.connected = connected
        self.telegraf_installed = telegraf_installed
        self.telegraf_version = telegraf_version
        self.service_active = service_active
        self.collector_reachable = collector_reachable
        self.custom_responses = custom_responses or {}

        self.executed_commands: List[str] = []
        self.uploaded_files: Dict[str, str] = {}

    def test_connection(self) -> bool:
        return self.connected

    def execute(self, command: str, timeout: int = 30) -> CommandResult:
        self.executed_commands.append(command)

        # Check custom overrides first
        for pattern, res in self.custom_responses.items():
            if pattern in command:
                return res

        cmd_lower = command.lower()

        # Target inspection commands
        if "uname -s" in cmd_lower:
            return CommandResult(exit_code=0, stdout="Linux\n", command=command)
        if "uname -m" in cmd_lower:
            return CommandResult(exit_code=0, stdout="x86_64\n", command=command)
        if "/etc/os-release" in cmd_lower:
            return CommandResult(
                exit_code=0,
                stdout='NAME="Ubuntu"\nVERSION="24.04 LTS (Noble Numbat)"\nID=ubuntu\n',
                command=command,
            )

        # Target inspection commands (Windows)
        if "$env:computername" in cmd_lower:
            return CommandResult(exit_code=0, stdout="WIN-HOST\n", command=command)
        if "win32_operatingsystem" in cmd_lower:
            return CommandResult(exit_code=0, stdout="Microsoft Windows Server 2022 Datacenter\n", command=command)
        if "win32_computersystemproduct" in cmd_lower:
            return CommandResult(exit_code=0, stdout="mock-win-uuid\n", command=command)
        if "get-netipaddress" in cmd_lower:
            return CommandResult(exit_code=0, stdout="172.16.3.80\n", command=command)

        # Telegraf detection commands
        if "test-path" in cmd_lower:
            out = "True\n" if self.telegraf_installed else "False\n"
            return CommandResult(exit_code=0, stdout=out, command=command)

        if "which telegraf" in cmd_lower:
            if self.telegraf_installed:
                return CommandResult(exit_code=0, stdout="/usr/bin/telegraf\n", command=command)
            return CommandResult(exit_code=1, stderr="telegraf not found\n", command=command)

        if "telegraf version" in cmd_lower or "telegraf --version" in cmd_lower or "telegraf.exe' version" in cmd_lower:
            if self.telegraf_installed:
                return CommandResult(exit_code=0, stdout=f"{self.telegraf_version}\n", command=command)
            return CommandResult(exit_code=127, stderr="command not found\n", command=command)

        # Service commands
        if "systemctl is-active telegraf" in cmd_lower or "systemctl status telegraf" in cmd_lower:
            if self.service_active:
                return CommandResult(exit_code=0, stdout="active\n", command=command)
            return CommandResult(exit_code=3, stdout="inactive\n", command=command)

        if "systemctl list-unit-files" in cmd_lower:
            out = "telegraf.service enabled\n" if self.telegraf_installed else ""
            return CommandResult(exit_code=0, stdout=out, command=command)

        if "systemctl restart telegraf" in cmd_lower:
            self.service_active = True
            return CommandResult(exit_code=0, stdout="", command=command)

        # Validation commands
        if "--test" in cmd_lower:
            return CommandResult(
                exit_code=0,
                stdout="Loaded inputs: cpu disk mem net system swap\n",
                command=command,
            )

        if "get-service" in cmd_lower:
            if "present" in cmd_lower and "absent" in cmd_lower:
                out = "PRESENT\n" if self.telegraf_installed else "ABSENT\n"
                return CommandResult(exit_code=0, stdout=out, command=command)
            if self.telegraf_installed:
                st = "Running" if self.service_active else "Stopped"
                return CommandResult(exit_code=0, stdout=f"{st} telegraf\n", command=command)
            return CommandResult(exit_code=1, stderr="Cannot find service\n", command=command)

        if "systemctl stop telegraf" in cmd_lower or "stop-service" in cmd_lower:
            self.service_active = False
            return CommandResult(exit_code=0, stdout="", command=command)

        if "packages=$(dpkg-query -W" in command:
            return CommandResult(exit_code=0, stdout="PRESENT" if self.telegraf_installed else "ABSENT", command=command)

        if (
            "apt-get purge" in cmd_lower
            or "dnf remove" in cmd_lower
            or "service uninstall" in cmd_lower
            or "sc.exe delete" in cmd_lower
            or "sc delete" in cmd_lower
        ):
            self.telegraf_installed = False
            self.service_active = False
            return CommandResult(exit_code=0, stdout="", command=command)

        if "rm -rf /etc/telegraf" in cmd_lower or "remove-item" in cmd_lower:
            self.uploaded_files = {k: v for k, v in self.uploaded_files.items() if not k.startswith(("/etc/telegraf", "C:\\telegraf"))}
            return CommandResult(exit_code=0, stdout="", command=command)

        # Directory creation
        if "mkdir -p" in cmd_lower:
            return CommandResult(exit_code=0, stdout="", command=command)

        # Network connectivity / Collector check
        if (
            "curl" in cmd_lower
            or "nc" in cmd_lower
            or "/dev/tcp" in cmd_lower
            or "tcpclient" in cmd_lower
            or "test-netconnection" in cmd_lower
        ):
            if self.collector_reachable:
                if "tcpclient" in cmd_lower or "test-netconnection" in cmd_lower:
                    return CommandResult(exit_code=0, stdout="True\n", command=command)
                if "%{http_code}" in cmd_lower or "-w" in cmd_lower:
                    return CommandResult(exit_code=0, stdout="200\n", command=command)
                return CommandResult(exit_code=0, stdout="HTTP/1.1 200 OK\n", command=command)
            return CommandResult(exit_code=7, stderr="Failed to connect to host\n", command=command)

        # Default success
        return CommandResult(exit_code=0, stdout="", command=command)

    def upload(self, source_content: Union[str, bytes], destination_path: str, mode: int = 0o644) -> None:
        if isinstance(source_content, bytes):
            self.uploaded_files[destination_path] = source_content.decode("utf-8", errors="replace")
        else:
            self.uploaded_files[destination_path] = source_content

    def download(self, source_path: str) -> str:
        if source_path in self.uploaded_files:
            return self.uploaded_files[source_path]
        raise FileNotFoundError(f"File not found in mock store: {source_path}")

    def file_exists(self, path: str) -> bool:
        norm = path.rstrip("/\\")
        return any(k == norm or k.startswith(f"{norm}/") or k.startswith(f"{norm}\\") for k in self.uploaded_files)

    def discover_services(self) -> list[DiscoveredService]:
        """Return simulated running services for mock testing."""
        return [
            DiscoveredService(name="telegraf", display_name="Telegraf Data Collector Service", status="Running", start_type="Automatic"),
            DiscoveredService(name="MSSQLSERVER", display_name="SQL Server (MSSQLSERVER)", status="Running", start_type="Automatic"),
            DiscoveredService(name="SQLServerAgent", display_name="SQL Server Agent (MSSQLSERVER)", status="Running", start_type="Manual"),
            DiscoveredService(name="W3SVC", display_name="World Wide Web Publishing Service", status="Running", start_type="Automatic"),
            DiscoveredService(name="WinRM", display_name="Windows Remote Management (WS-Management)", status="Running", start_type="Automatic"),
        ]

    def discover_perfmon_sets(self) -> list[DiscoveredPerfmonSet]:
        """Return simulated Perfmon counter sets for mock testing."""
        return [
            DiscoveredPerfmonSet(name="Processor", description="CPU core and package utilization", counters=["% Processor Time", "% Privileged Time", "% User Time"]),
            DiscoveredPerfmonSet(name="Memory", description="Physical and virtual RAM counters", counters=["Available Bytes", "Committed Bytes", "% Committed Bytes In Use"]),
            DiscoveredPerfmonSet(name="LogicalDisk", description="Storage volume metrics per drive letter", counters=["% Free Space", "Free Megabytes", "Current Disk Queue Length"]),
            DiscoveredPerfmonSet(name="SQLServer:General Statistics", description="Database engine connections and logins", counters=["User Connections", "Logical Connections", "Logins/sec"]),
        ]

    def discover_databases(
        self,
        db_type: str = "mssql",
        auth_mode: str = "integrated",
        username: Optional[str] = None,
        password: Optional[str] = None,
        port: int = 1433,
    ) -> list[DiscoveredDatabase]:
        """Return simulated database catalogs for mock testing."""
        return [
            DiscoveredDatabase(name="master", state="ONLINE", db_type="system", size_mb=12.0),
            DiscoveredDatabase(name="OperationsDB", state="ONLINE", db_type="user", size_mb=25000.0),
            DiscoveredDatabase(name="BillingDB", state="ONLINE", db_type="user", size_mb=120000.0),
        ]

    def get_free_disk_space_mb(self, path: Optional[str] = None) -> int:
        """Return simulated free disk space in megabytes."""
        return 20480

    def close(self) -> None:
        pass

