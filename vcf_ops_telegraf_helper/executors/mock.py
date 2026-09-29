"""Mock endpoint executor for deterministic testing and dry-run simulations."""

from __future__ import annotations

from typing import Dict, List, Optional, Union
from vcf_ops_telegraf_helper.executors.base import CommandResult, EndpointExecutor


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

        # Telegraf detection commands
        if "which telegraf" in cmd_lower:
            if self.telegraf_installed:
                return CommandResult(exit_code=0, stdout="/usr/bin/telegraf\n", command=command)
            return CommandResult(exit_code=1, stderr="telegraf not found\n", command=command)

        if "telegraf version" in cmd_lower or "telegraf --version" in cmd_lower:
            if self.telegraf_installed:
                return CommandResult(exit_code=0, stdout=f"{self.telegraf_version}\n", command=command)
            return CommandResult(exit_code=127, stderr="command not found\n", command=command)

        # Service commands
        if "systemctl is-active telegraf" in cmd_lower or "systemctl status telegraf" in cmd_lower:
            if self.service_active:
                return CommandResult(exit_code=0, stdout="active\n", command=command)
            return CommandResult(exit_code=3, stdout="inactive\n", command=command)

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
        if "curl" in cmd_lower or "nc" in cmd_lower or "/dev/tcp" in cmd_lower:
            if self.collector_reachable:
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

    def close(self) -> None:
        pass
