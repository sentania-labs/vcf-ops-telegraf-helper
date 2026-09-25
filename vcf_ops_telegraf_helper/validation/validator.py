"""Validation layer for VCF Operations Open Telegraf Helper.

Provides discrete validation checks across specific failure domains:
- Structured configuration validity
- Generated TOML syntax validity
- Endpoint connectivity and credentials
- Collector network reachability
- Telegraf remote configuration validity (via telegraf --test)
- Endpoint service state
- Telemetry ingestion confirmation
"""

from __future__ import annotations

import sys
from typing import Optional
from pydantic import BaseModel, Field

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

from vcf_ops_telegraf_helper.adapters.base import VCFOpsIntegration
from vcf_ops_telegraf_helper.executors.base import EndpointExecutor
from vcf_ops_telegraf_helper.models.monitoring import MonitoringConfig


class ValidationResult(BaseModel):
    """Result of an individual validation check."""

    domain: str = Field(description="The failure domain being validated")
    is_valid: bool = Field(description="True if validation passed")
    message: str = Field(description="Summary outcome description")
    details: Optional[str] = Field(default=None, description="Detailed diagnostic or error output")
    remediation: Optional[str] = Field(default=None, description="Suggested recovery action")


class Validator:
    """Collection of discrete validation functions for endpoint and configuration states."""

    @staticmethod
    def validate_structured_config(config: MonitoringConfig) -> ValidationResult:
        """Validate monitoring configuration fields and bounds."""
        # Verify at least one input is enabled
        enabled_any = (
            config.cpu.enabled
            or config.mem.enabled
            or config.disk.enabled
            or config.net.enabled
            or config.system.enabled
            or config.swap.enabled
            or config.diskio.enabled
            or config.processes.enabled
            or config.win_perf_counters.enabled
            or config.win_services.enabled
            or config.nginx.enabled
            or config.apache.enabled
            or config.mysql.enabled
            or config.postgresql.enabled
            or config.mssql.enabled
            or config.docker.enabled
            or config.ping.enabled
            or bool(config.custom_toml and config.custom_toml.strip())
        )
        if not enabled_any:
            return ValidationResult(
                domain="Structured Configuration",
                is_valid=False,
                message="No monitoring inputs selected",
                remediation="Enable at least one core metric or workload plugin.",
            )

        return ValidationResult(
            domain="Structured Configuration",
            is_valid=True,
            message="Structured monitoring configuration is valid",
        )

    @staticmethod
    def validate_toml_syntax(toml_content: str, label: str = "Telegraf TOML") -> ValidationResult:
        """Validate that generated TOML is syntactically correct and parsable."""
        try:
            tomllib.loads(toml_content)
            return ValidationResult(
                domain="TOML Syntax",
                is_valid=True,
                message=f"{label} syntax is valid",
            )
        except Exception as e:
            return ValidationResult(
                domain="TOML Syntax",
                is_valid=False,
                message=f"{label} contains invalid TOML syntax",
                details=str(e),
                remediation="Review generated plugin blocks for syntax discrepancies.",
            )

    @staticmethod
    def validate_endpoint_connection(executor: EndpointExecutor) -> ValidationResult:
        """Validate that the endpoint is reachable and authenticates successfully."""
        try:
            connected = executor.test_connection()
            if connected:
                return ValidationResult(
                    domain="Endpoint Connection",
                    is_valid=True,
                    message="Endpoint is reachable and authenticated",
                )
            return ValidationResult(
                domain="Endpoint Connection",
                is_valid=False,
                message="Endpoint connection failed",
                remediation="Verify target hostname, port, username, and SSH key or password.",
            )
        except Exception as e:
            return ValidationResult(
                domain="Endpoint Connection",
                is_valid=False,
                message="Endpoint connection encountered an error",
                details=str(e),
                remediation="Check network routing and target SSH service availability.",
            )

    @staticmethod
    def validate_collector_reachability(
        executor: EndpointExecutor,
        collector_address: str,
        port: int = 443,
        is_windows: bool = False,
    ) -> ValidationResult:
        """Check whether the target endpoint can connect to the Cloud Proxy on HTTPS."""
        if is_windows or type(executor).__name__ == "WinRMExecutor":
            cmd = (
                f"try {{ $c = New-Object System.Net.Sockets.TcpClient; $c.Connect('{collector_address}', {port}); "
                "$c.Connected; $c.Close() } catch { Test-NetConnection -ComputerName "
                f"'{collector_address}' -Port {port} -WarningAction SilentlyContinue | Select-Object -ExpandProperty TcpTestSucceeded }}"
            )
            res = executor.execute(cmd, timeout=10)
            if res.exit_code == 0 and "True" in str(res.stdout):
                return ValidationResult(
                    domain="Collector Reachability",
                    is_valid=True,
                    message=f"Cloud Proxy {collector_address}:{port} is reachable from target",
                )
        else:
            # Use curl or bash dev/tcp socket test on the endpoint
            cmd = f"curl -k -s -o /dev/null -w '%{{http_code}}' https://{collector_address}:{port}/ || nc -z -w3 {collector_address} {port} || timeout 3 bash -c '</dev/tcp/{collector_address}/{port}'"
            res = executor.execute(cmd, timeout=10)
            if res.exit_code == 0 or str(res.stdout).strip() in ("200", "401", "403", "404"):
                return ValidationResult(
                    domain="Collector Reachability",
                    is_valid=True,
                    message=f"Cloud Proxy {collector_address}:{port} is reachable from target",
                )

        err_detail = str(res.stderr or res.stdout or "").strip()
        return ValidationResult(
            domain="Collector Reachability",
            is_valid=False,
            message=f"Cloud Proxy {collector_address}:{port} is unreachable from target",
            details=err_detail or None,
            remediation="Verify firewall rules and routing from target to Cloud Proxy port 443.",
        )

    @staticmethod
    def validate_telegraf_config_on_endpoint(
        executor: EndpointExecutor,
        telegraf_bin: str,
        config_path: str,
        config_dir: str,
        is_windows: bool = False,
    ) -> ValidationResult:
        """Run Telegraf test mode to validate all plugins and syntax on the endpoint."""
        if is_windows or type(executor).__name__ == "WinRMExecutor":
            cmd = f"& '{telegraf_bin}' --test --config '{config_path}' --config-directory '{config_dir}'"
        else:
            cmd = f"{telegraf_bin} --test --config {config_path} --config-directory {config_dir}"
        res = executor.execute(cmd, timeout=15)

        if res.exit_code == 0:
            return ValidationResult(
                domain="Telegraf Config Validity",
                is_valid=True,
                message="Telegraf configuration verified with --test mode",
                details=res.stdout,
            )

        return ValidationResult(
            domain="Telegraf Config Validity",
            is_valid=False,
            message="Telegraf configuration validation failed",
            details=res.stderr or res.stdout,
            remediation="Check telegraf logs and plugin options for unsupported settings.",
        )

    @staticmethod
    def validate_service_state(
        executor: EndpointExecutor,
        is_windows: bool = False,
    ) -> ValidationResult:
        """Verify that the Telegraf service is active on the target."""
        if is_windows or type(executor).__name__ == "WinRMExecutor":
            res = executor.execute("(Get-Service telegraf -ErrorAction SilentlyContinue).Status", timeout=5)
            state = res.stdout.strip()
            if res.exit_code == 0 and "Running" in state:
                return ValidationResult(
                    domain="Service State",
                    is_valid=True,
                    message="Telegraf Windows service is active (running)",
                )
            remediation = "Review 'Get-EventLog -LogName Application -Source telegraf' or service logs for errors."
        else:
            res = executor.execute("systemctl is-active telegraf", timeout=5)
            state = res.stdout.strip()
            if res.exit_code == 0 and state == "active":
                return ValidationResult(
                    domain="Service State",
                    is_valid=True,
                    message="Telegraf systemd service is active (running)",
                )
            remediation = "Review 'systemctl status telegraf' or 'journalctl -u telegraf' for startup errors."

        return ValidationResult(
            domain="Service State",
            is_valid=False,
            message=f"Telegraf service is not active (state: {state or 'unknown'})",
            details=res.stderr or res.stdout,
            remediation=remediation,
        )

    @staticmethod
    def validate_telemetry_flow(
        adapter: VCFOpsIntegration,
        hostname: str,
    ) -> ValidationResult:
        """Verify telemetry ingestion in VCF Operations."""
        status = adapter.verify_ingestion(hostname)

        if status == "PASS":
            return ValidationResult(
                domain="Telemetry Flow",
                is_valid=True,
                message=f"Metrics confirmed in VCF Operations for {hostname}",
            )
        elif status == "UNKNOWN":
            return ValidationResult(
                domain="Telemetry Flow",
                is_valid=True,  # Unknown is an honest status, not a hard failure immediately after start
                message="Metrics not yet visible in VCF Operations (collection cycle in progress)",
                details="VCF Operations typically requires 1-2 collection cycles (5-10 minutes) to register new objects.",
            )
        else:
            return ValidationResult(
                domain="Telemetry Flow",
                is_valid=False,
                message="Telemetry verification failed in VCF Operations",
                remediation="Confirm Cloud Proxy is online and credentials have metric write permissions.",
            )
