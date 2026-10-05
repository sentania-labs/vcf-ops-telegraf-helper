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

import shlex
import sys
import time
from typing import Dict, Optional
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
        service_name: Optional[str] = None,
    ) -> ValidationResult:
        """Verify that the Telegraf service is active on the target."""
        svc = service_name or "telegraf"
        if is_windows or type(executor).__name__ == "WinRMExecutor":
            safe_svc = svc.replace("'", "''")
            cmd = (
                "(Get-Service telegraf -ErrorAction SilentlyContinue).Status"
                if svc == "telegraf"
                else f"(Get-Service '{safe_svc}' -ErrorAction SilentlyContinue).Status"
            )
            res = executor.execute(cmd, timeout=5)
            state = res.stdout.strip()
            if res.exit_code == 0 and "Running" in state:
                return ValidationResult(
                    domain="Service State",
                    is_valid=True,
                    message=f"Telegraf Windows service '{svc}' is active (running)",
                )
            remediation = f"Review 'Get-EventLog -LogName Application -Source {svc}' or service logs for errors."
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

    @staticmethod
    def validate_cloudproxy_mtls_metric_probe(
        executor: EndpointExecutor,
        collector_address: str,
        cert_path: str,
        key_path: str,
        ca_cert_path: str,
        headers: Optional[Dict[str, str]] = None,
        hostname: str = "localhost",
        is_windows: bool = False,
        verify_ssl: bool = False,
    ) -> ValidationResult:
        """Actively test metric ingestion against the Cloud Proxy endpoint using client mTLS credentials."""
        hdrs = headers or {}
        probe_metric = f"vcf_helper.probe 1.0 {int(time.time())} host={hostname}"
        target_url = f"https://{collector_address}/opensource/default/metric"

        if is_windows or type(executor).__name__ == "WinRMExecutor":
            def ps_quote(s: str) -> str:
                return "'" + s.replace("'", "''") + "'"

            args = [
                "-s",
                "-S",
                "-o", "NUL",
                "-w", ps_quote(r"\n%{http_code}"),
                "-X", "POST",
                ps_quote(target_url),
                "--cert", ps_quote(cert_path),
                "--key", ps_quote(key_path),
                "--cacert", ps_quote(ca_cert_path),
            ]
            if not verify_ssl:
                args.append("-k")
            for k, v in hdrs.items():
                if v and str(v).strip():
                    args.extend(["-H", ps_quote(f"{k}: {str(v).strip()}")])
                else:
                    args.extend(["-H", ps_quote(f"{k};")])
            args.extend(["--data", ps_quote(probe_metric)])
            arg_str = " ".join(args)

            cmd = (
                f"if (Get-Command curl.exe -ErrorAction SilentlyContinue) {{ "
                f"& curl.exe {arg_str} 2>&1 "
                f"}} else {{ 'CURL_NOT_FOUND' }}"
            )
        else:
            cmd_parts = [
                "curl",
                "-s",
                "-S",
                "-o", "/dev/null",
                "-w", "\n%{http_code}",
                "-X", "POST",
                target_url,
                "--cert", cert_path,
                "--key", key_path,
                "--cacert", ca_cert_path,
            ]
            if not verify_ssl:
                cmd_parts.append("-k")
            for k, v in hdrs.items():
                if v and str(v).strip():
                    cmd_parts.extend(["-H", f"{k}: {str(v).strip()}"])
                else:
                    cmd_parts.extend(["-H", f"{k};"])
            cmd_parts.extend(["--data", probe_metric])

            cmd = " ".join(shlex.quote(p) for p in cmd_parts) + " 2>&1"

        res = executor.execute(cmd, timeout=15)
        out = (res.stdout or "").strip()
        combined_out = f"{out}\n{res.stderr or ''}".strip()

        if out == "CURL_NOT_FOUND":
            return ValidationResult(
                domain="Metrics Transmission",
                is_valid=False,
                message="curl.exe is not available on Windows target to perform mTLS metric verification probe",
                details="curl.exe was not found in system PATH",
                remediation="Ensure curl.exe is available in Windows System32.",
            )

        # The last non-empty line contains the HTTP status code if curl executed HTTP request
        lines = [line.strip() for line in out.splitlines() if line.strip()]
        last_line = lines[-1] if lines else ""

        http_code = ""
        if last_line.isdigit() and len(last_line) == 3:
            http_code = last_line
        elif "200 ok" in last_line.lower() or "http/1.1 200" in last_line.lower():
            http_code = "200"

        if http_code in ("200", "202", "204"):
            return ValidationResult(
                domain="Metrics Transmission",
                is_valid=True,
                message=f"Cloud Proxy accepted mutual TLS metric transmission (HTTP {http_code})",
            )

        if (is_windows or type(executor).__name__ == "WinRMExecutor") and (
            "0x80092002" in combined_out.lower()
            or "schannel" in combined_out.lower()
            or "failed to open cert or key" in combined_out.lower()
            or "failed to import cert" in combined_out.lower()
        ):
            # Built-in Windows curl.exe uses Schannel, which cannot load detached PEM private keys.
            # Telegraf uses Go's native crypto/tls stack which loads PEM certificates directly.
            # Fall back to verifying TCP port 443 connectivity to Cloud Proxy and Telegraf service state.
            probe_cmd = (
                "$ProgressPreference = 'SilentlyContinue'; "
                f"$tcp = (Test-NetConnection -ComputerName '{collector_address}' -Port 443 -WarningAction SilentlyContinue).TcpTestSucceeded; "
                "$svc = (Get-Service -Name telegraf -ErrorAction SilentlyContinue).Status; "
                '"TCP:$tcp;SVC:$svc"'
            )
            probe_res = executor.execute(probe_cmd, timeout=15)
            probe_out = (probe_res.stdout or "").strip()
            tcp_ok = "tcp:true" in probe_out.lower()
            svc_running = "svc:running" in probe_out.lower()

            if tcp_ok and svc_running:
                return ValidationResult(
                    domain="Metrics Transmission",
                    is_valid=False,
                    message="Windows curl Schannel backend cannot load detached PEM client certificates; mTLS probe unverified",
                    details=f"curl output: {out}; probe: {probe_out}",
                    remediation="Windows built-in curl.exe uses Schannel and cannot load PEM client certificates for mTLS probe. Verify metrics ingestion in VCF Operations or check Telegraf service logs.",
                )
            elif tcp_ok and not svc_running:
                return ValidationResult(
                    domain="Metrics Transmission",
                    is_valid=False,
                    message="Cloud Proxy port 443 reachable but Telegraf service is stopped or failed",
                    details=f"curl output: {out}; probe: {probe_out}",
                    remediation="Start the Telegraf service using 'Start-Service telegraf' and verify Windows Application Event Log.",
                )
            else:
                return ValidationResult(
                    domain="Metrics Transmission",
                    is_valid=False,
                    message=f"Cloud Proxy port 443 unreachable from Windows endpoint ({collector_address}:443)",
                    details=f"curl output: {out}; probe: {probe_out}",
                    remediation=f"Verify network firewall, routing, and security groups between Windows host and Cloud Proxy {collector_address}:443.",
                )

        if http_code == "403" or "403 forbidden" in out.lower():
            return ValidationResult(
                domain="Metrics Transmission",
                is_valid=False,
                message="HTTP 403 Forbidden: Cloud Proxy rejected transmission (mutual TLS client certificate missing, untrusted, or expired)",
                details=out,
                remediation="Ensure the collector group client certificate bundle was properly acquired and deployed into telegraf.d/.",
            )

        return ValidationResult(
            domain="Metrics Transmission",
            is_valid=False,
            message=f"Cloud Proxy metric transmission probe failed (HTTP {http_code or 'error'})",
            details=out or res.stderr,
            remediation="Verify network routing to Cloud Proxy, port 443 reachability, and client certificates.",
        )
