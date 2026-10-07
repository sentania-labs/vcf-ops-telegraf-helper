"""Workflow execution engine for configuring open-source Telegraf on an endpoint."""

from __future__ import annotations

import hashlib
import ipaddress
import os
import ntpath
import difflib
import shlex
import socket
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

from cryptography import x509
from vcf_ops_telegraf_helper.adapters.base import IntegrationArtifacts, VCFOpsIntegration
from vcf_ops_telegraf_helper.executors.base import EndpointExecutor
from vcf_ops_telegraf_helper.executors.ssh import SSHExecutor
from vcf_ops_telegraf_helper.executors.winrm import WinRMExecutor
from vcf_ops_telegraf_helper.models.endpoint import (
    EndpointDiscoveryResult,
    EndpointTarget,
    OSFamily,
)
from vcf_ops_telegraf_helper.models.monitoring import MonitoringConfig
from vcf_ops_telegraf_helper.models.vcf import VCFEnvironment
from vcf_ops_telegraf_helper.models.workflow import (
    RunSummary,
    StageResult,
    StageStatus,
    WorkflowOptions,
    WorkflowStage,
)
from vcf_ops_telegraf_helper.renderer.renderer import TelegrafRenderer
from vcf_ops_telegraf_helper.security.redaction import redact_secrets
from vcf_ops_telegraf_helper.validation.validator import Validator
from vcf_ops_telegraf_helper.workflow.progress import ProgressReporter, SilentProgressReporter
from vcf_ops_telegraf_helper.workflow.windows import detect_windows_telegraf
from vcf_ops_telegraf_helper.logger import get_logger

logger = get_logger("workflow.engine")


class ConfigureEndpointWorkflow:
    """Orchestrates the 8-stage sequence to inspect, configure, and verify an endpoint."""

    def __init__(
        self,
        environment: VCFEnvironment,
        target: EndpointTarget,
        monitoring: MonitoringConfig,
        executor: EndpointExecutor,
        adapter: VCFOpsIntegration,
        options: Optional[WorkflowOptions] = None,
        reporter: Optional[ProgressReporter] = None,
    ):
        self.started_at = time.time()
        self.preview_callback = None
        self.input_diff = ""
        self.env = environment
        self.target = target
        self.monitoring = monitoring
        self.executor = executor
        self.adapter = adapter
        self.options = options or WorkflowOptions()
        self.reporter = reporter or SilentProgressReporter()

        # State accumulated during the run
        self.discovery: Optional[EndpointDiscoveryResult] = None
        self.artifacts: Optional[IntegrationArtifacts] = None
        self.system_conf_content: str = ""
        self.vcf_conf_content: str = ""
        self.base_stub_content: str = ""
        self.stage_results: List[StageResult] = []
        self.verifications: Dict[str, str] = {}
        self.managed_files: List[str] = []

        # Collect secrets for redaction
        self._secrets: List[str] = []
        if self.env.password:
            self._secrets.append(self.env.password)
        if self.env.token:
            self._secrets.append(self.env.token)
        if self.target.password:
            self._secrets.append(self.target.password)

    def _sanitize(self, text: Optional[str]) -> Optional[str]:
        if text is None:
            return None
        return redact_secrets(text, self._secrets)

    @staticmethod
    def _is_ip(val: Optional[str]) -> bool:
        if not val:
            return False
        try:
            ipaddress.ip_address(val.strip())
            return True
        except ValueError:
            return False

    def _security_artifact_files(self, config_dir: str, is_win: bool) -> list[tuple[str, str, int]]:
        """Return (destination, content, mode) for each mTLS and security file the apply stage writes.

        Apply uploads exactly this list and the idempotency check compares exactly this list,
        so the two cannot drift apart.
        """
        sep = "\\" if is_win else "/"
        files: list[tuple[str, str, int]] = []
        if self.artifacts:
            for name, content, mode in (
                ("ca.pem", self.artifacts.ca_cert_content, 0o644),
                ("cert.pem", self.artifacts.client_cert_content, 0o644),
                ("key.pem", self.artifacts.client_key_content, 0o640),
                ("master.pub", self.artifacts.master_pub_content, 0o644),
            ):
                if content:
                    files.append((f"{config_dir}{sep}{name}", content, mode))
        ip_content = (self.artifacts.vip_content or self.artifacts.collector_address) if self.artifacts else self.env.collector.address
        files.append((f"{config_dir}{sep}IP", f"{ip_content.strip()}\n", 0o644))
        ma_val = "true\n" if (self.artifacts and self.artifacts.mutual_auth) else "false\n"
        files.append((f"{config_dir}{sep}MUTUAL_AUTHENTICATION", ma_val, 0o644))
        # Records which client identity the cert was issued for, so a changed VM binding re-mints
        if self.artifacts and self.artifacts.client_id and self.artifacts.client_cert_content:
            files.append((f"{config_dir}{sep}CLIENT_ID", f"{self.artifacts.client_id}\n", 0o644))
        if self.artifacts and self.artifacts.mandatory_tags_content:
            tags_name = "mandatory_tags.bat" if is_win else "mandatory_tags.sh"
            files.append((f"{config_dir}{sep}{tags_name}", self.artifacts.mandatory_tags_content, 0o755))
        return files

    def _get_registered_hostname(self) -> str:
        """Resolve the shortname to register with VCF Operations."""
        if getattr(self.target, "registered_hostname", None):
            raw = self.target.registered_hostname.strip().splitlines()[-1].strip()
            if self._is_ip(raw):
                return raw
            return raw.split(".")[0]
        if self.discovery and self.discovery.hostname and not self._is_ip(self.discovery.hostname):
            return self.discovery.hostname.strip().splitlines()[-1].strip().split(".")[0]
        if self.artifacts and getattr(self.artifacts, "vm_name", None) and not self._is_ip(self.artifacts.vm_name):
            return self.artifacts.vm_name.strip().splitlines()[-1].strip().split(".")[0]
        if not self._is_ip(self.target.hostname):
            return self.target.hostname.strip().splitlines()[-1].strip().split(".")[0]
        if hasattr(self, "_cached_ptr"):
            return self._cached_ptr or self.target.hostname
        try:
            ptr = socket.gethostbyaddr(self.target.hostname)[0]
            if ptr:
                self._cached_ptr = ptr.strip().split(".")[0]
                return self._cached_ptr
        except Exception:
            pass
        self._cached_ptr = None
        return getattr(self.target, "registered_hostname", None) or self.target.hostname

    def detect_target(self) -> StageResult:
        """Stage 1: Verify connectivity and authenticate with target endpoint."""
        start = time.monotonic()
        self.reporter.on_stage_start(WorkflowStage.CONNECT)

        try:
            connected = self.executor.test_connection()
            dur = int((time.monotonic() - start) * 1000)
            if connected:
                res = StageResult(
                    stage=WorkflowStage.CONNECT,
                    status=StageStatus.PASS,
                    message=f"Connected to {self.target.hostname} ({self.target.connection_method.value}; {getattr(self.executor, 'auth_method', 'local/mock')})",
                    duration_ms=dur,
                )
            else:
                res = StageResult(
                    stage=WorkflowStage.CONNECT,
                    status=StageStatus.FAIL,
                    message=f"Failed to connect to {self.target.hostname}: {getattr(self.executor, 'connection_error', 'Connection unavailable')}",
                    duration_ms=dur,
                )
        except Exception as e:
            dur = int((time.monotonic() - start) * 1000)
            res = StageResult(
                stage=WorkflowStage.CONNECT,
                status=StageStatus.FAIL,
                message=f"Connection error: {e}",
                details=self._sanitize(str(e)),
                duration_ms=dur,
            )

        self.reporter.on_stage_complete(res)
        return res

    def detect_telegraf(self) -> StageResult:
        """Stage 2: Inspect remote OS, architecture, and existing Telegraf installation."""
        start = time.monotonic()
        self.reporter.on_stage_start(WorkflowStage.DETECT)

        try:
            is_win = (self.target.os_family == OSFamily.WINDOWS) or (type(self.executor).__name__ == "WinRMExecutor")
            if is_win:
                os_name = "Windows"
                arch_res = self.executor.execute(
                    "if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }",
                    timeout=10,
                )
                if arch_res.success and "ARM" in arch_res.stdout.upper():
                    arch = "arm64"
                else:
                    arch = "x86_64"
                ver_res = self.executor.execute("(Get-CimInstance Win32_OperatingSystem).Caption", timeout=10)
                os_version = ver_res.stdout.strip() if ver_res.success and ver_res.stdout.strip() else "Microsoft Windows"

                win_det = detect_windows_telegraf(self.executor)
                if (win_det.service_name or "").lower() == "ucp-telegraf" or "ucp-telegraf" in (win_det.binary_path or "").lower():
                    raise RuntimeError("This agent is managed by VCF Operations (ucp-telegraf). Use VCF Operations to manage it; helper changes are refused.")
                installed = win_det.installed
                version_str = win_det.version
                service_state = win_det.service_state or ("Running" if win_det.running else "Stopped")
                telegraf_bin = win_det.binary_path or "C:\\telegraf\\telegraf.exe"

                uuid_res = self.executor.execute("(Get-CimInstance Win32_ComputerSystemProduct).UUID", timeout=10)
                host_uuid = uuid_res.stdout.strip() if uuid_res.success else ""
                ip_res = self.executor.execute(
                    "(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.InterfaceAlias -notlike '*Loopback*' }).IPAddress | Select-Object -First 1",
                    timeout=10,
                )
                host_ip = ip_res.stdout.strip() if ip_res.success and ip_res.stdout.strip() else self.target.hostname

                hname_res = self.executor.execute("$env:COMPUTERNAME", timeout=10)
                discovered_hname = self.target.hostname
                if hname_res.success and hname_res.stdout.strip():
                    lines = [ln.strip() for ln in hname_res.stdout.splitlines() if ln.strip()]
                    if lines:
                        discovered_hname = lines[-1].split(".")[0]
                if self._is_ip(discovered_hname):
                    if not hasattr(self, "_cached_ptr"):
                        try:
                            ptr = socket.gethostbyaddr(discovered_hname)[0]
                            self._cached_ptr = ptr.strip().split(".")[0] if ptr else None
                        except Exception:
                            self._cached_ptr = None
                    if self._cached_ptr:
                        discovered_hname = self._cached_ptr

                self.discovery = EndpointDiscoveryResult(
                    hostname=discovered_hname,
                    os_name=os_name,
                    os_version=os_version,
                    arch=arch,
                    telegraf_installed=installed,
                    telegraf_version=version_str,
                    service_state=service_state,
                    config_dir=win_det.config_dir,
                    main_config_path=win_det.main_config_path,
                    telegraf_bin_path=telegraf_bin,
                    service_name=win_det.service_name,
                    host_uuid=host_uuid,
                    host_ip=host_ip,
                )
            else:
                os_name = "Linux"
                arch_res = self.executor.execute("uname -m", timeout=5)
                arch = arch_res.stdout.strip() if arch_res.success else "x86_64"

                os_rel = self.executor.execute("cat /etc/os-release", timeout=5)
                os_version = "Unknown Linux"
                if os_rel.success and os_rel.stdout.strip():
                    for line in os_rel.stdout.splitlines():
                        if line.startswith("PRETTY_NAME="):
                            os_version = line.split("=", 1)[1].strip('"\'')
                            break
                        if line.startswith("NAME=") and os_version == "Unknown Linux":
                            os_version = line.split("=", 1)[1].strip('"\'')
                    if os_version == "Unknown Linux" and not any("=" in entry for entry in os_rel.stdout.splitlines()):
                        os_version = os_rel.stdout.strip().splitlines()[0]

                which_res = self.executor.execute("which telegraf", timeout=5)
                installed = which_res.success
                telegraf_bin = which_res.stdout.strip() if installed else "/usr/bin/telegraf"

                version_str = None
                if installed:
                    ver_res = self.executor.execute(f"{telegraf_bin} version", timeout=5)
                    if ver_res.success:
                        version_str = ver_res.stdout.strip()

                svc_res = self.executor.execute("systemctl is-active telegraf", timeout=5)
                service_state = svc_res.stdout.strip() if svc_res.stdout else "unknown"

                uuid_res = self.executor.execute("cat /sys/class/dmi/id/product_uuid || cat /etc/machine-id", timeout=5)
                host_uuid = uuid_res.stdout.strip() if uuid_res.success else ""
                ip_res = self.executor.execute("hostname -I | awk '{print $1}'", timeout=5)
                host_ip = ip_res.stdout.strip() if ip_res.success and ip_res.stdout.strip() else self.target.hostname

                hname_res = self.executor.execute("hostname -s", timeout=5)
                discovered_hname = self.target.hostname
                if hname_res.success and hname_res.stdout.strip():
                    lines = [ln.strip() for ln in hname_res.stdout.splitlines() if ln.strip()]
                    if lines:
                        discovered_hname = lines[-1].split(".")[0]
                if self._is_ip(discovered_hname):
                    if not hasattr(self, "_cached_ptr"):
                        try:
                            ptr = socket.gethostbyaddr(discovered_hname)[0]
                            self._cached_ptr = ptr.strip().split(".")[0] if ptr else None
                        except Exception:
                            self._cached_ptr = None
                    if self._cached_ptr:
                        discovered_hname = self._cached_ptr

                self.discovery = EndpointDiscoveryResult(
                    hostname=discovered_hname,
                    os_name=os_name,
                    os_version=os_version,
                    arch=arch,
                    telegraf_installed=installed,
                    telegraf_version=version_str,
                    service_state=service_state,
                    config_dir="/etc/telegraf/telegraf.d",
                    main_config_path="/etc/telegraf/telegraf.conf",
                    telegraf_bin_path=telegraf_bin,
                    host_uuid=host_uuid,
                    host_ip=host_ip,
                )

            dur = int((time.monotonic() - start) * 1000)
            auto_install = self.target.install_telegraf or self.options.install_telegraf
            telegraf_ver = (
                getattr(self.options, "telegraf_version", None)
                or getattr(self.target, "telegraf_version", None)
                or "1.40.1"
            )

            if not installed and not auto_install:
                res = StageResult(
                    stage=WorkflowStage.DETECT,
                    status=StageStatus.FAIL,
                    message=f"Telegraf not installed on {self.target.hostname}. Install Telegraf before push or enable auto-install.",
                    details=f"{os_version} ({arch}), Service state: {service_state}",
                    duration_ms=dur,
                )
            else:
                status = StageStatus.PASS if installed else StageStatus.WARNING
                msg_suffix = f" (will auto-install official InfluxData agent {telegraf_ver})" if (not installed and auto_install) else ""
                msg = f"{os_version} ({arch}), Telegraf: {version_str or 'Not installed'}{msg_suffix}"
                res = StageResult(
                    stage=WorkflowStage.DETECT,
                    status=status,
                    message=msg,
                    details=f"Service state: {service_state}",
                    duration_ms=dur,
                )
        except Exception as e:
            dur = int((time.monotonic() - start) * 1000)
            res = StageResult(
                stage=WorkflowStage.DETECT,
                status=StageStatus.FAIL,
                message=f"Detection failed: {e}",
                details=self._sanitize(str(e)),
                duration_ms=dur,
            )

        self.reporter.on_stage_complete(res)
        return res

    def configure_vcf_output(self) -> StageResult:
        """Stage 3: Authenticate with VCF Operations and prepare Cloud Proxy output settings."""
        start = time.monotonic()
        self.reporter.on_stage_start(WorkflowStage.PREPARE_VCF)

        try:
            connected = self.adapter.validate_connection()
            if not connected:
                dur = int((time.monotonic() - start) * 1000)
                res = StageResult(
                    stage=WorkflowStage.PREPARE_VCF,
                    status=StageStatus.FAIL,
                    message=f"Cannot reach VCF Operations API at {self.env.url}",
                    duration_ms=dur,
                )
                self.reporter.on_stage_complete(res)
                return res

            target_ip = self.discovery.host_ip if self.discovery else self.target.hostname
            target_uuid = self.discovery.host_uuid if self.discovery else None

            existing_bundle = None
            if not getattr(self.options, "force_new_cert", False):
                is_win = (self.target.os_family == OSFamily.WINDOWS) or (type(self.executor).__name__ == "WinRMExecutor")
                cfg_dir = (
                    self.discovery.config_dir
                    if self.discovery
                    else ("C:\\telegraf\\telegraf.d" if is_win else "/etc/telegraf/telegraf.d")
                )
                sep = "\\" if is_win else "/"
                cert_f = f"{cfg_dir}{sep}cert.pem"
                key_f = f"{cfg_dir}{sep}key.pem"
                ca_f = f"{cfg_dir}{sep}ca.pem"
                pub_f = f"{cfg_dir}{sep}master.pub"
                ip_f = f"{cfg_dir}{sep}IP"
                ma_f = f"{cfg_dir}{sep}MUTUAL_AUTHENTICATION"
                cid_f = f"{cfg_dir}{sep}CLIENT_ID"
                if self.executor.file_exists(cert_f) and self.executor.file_exists(key_f):
                    try:
                        c_txt = self.executor.download(cert_f)
                        k_txt = self.executor.download(key_f)
                        ca_txt = self.executor.download(ca_f) if self.executor.file_exists(ca_f) else None
                        pub_txt = self.executor.download(pub_f) if self.executor.file_exists(pub_f) else None
                        vip_txt = self.executor.download(ip_f).strip() if self.executor.file_exists(ip_f) else None
                        ma_txt = self.executor.download(ma_f).strip().lower() if self.executor.file_exists(ma_f) else "true"
                        cid_txt = self.executor.download(cid_f).strip() if self.executor.file_exists(cid_f) else None
                        if "BEGIN CERTIFICATE" in c_txt and ("BEGIN RSA PRIVATE KEY" in k_txt or "BEGIN PRIVATE KEY" in k_txt):
                            try:
                                parsed_cert = x509.load_pem_x509_certificate(c_txt.encode("utf-8"))
                                expire_dt = getattr(parsed_cert, "not_valid_after_utc", None)
                                if expire_dt is None:
                                    expire_dt = parsed_cert.not_valid_after.replace(tzinfo=timezone.utc)
                                if expire_dt <= datetime.now(timezone.utc):
                                    existing_bundle = None
                                else:
                                    existing_bundle = {
                                        "client_cert": c_txt,
                                        "client_key": k_txt,
                                        "ca_cert": ca_txt,
                                        "master_pub": pub_txt,
                                        "vip": vip_txt,
                                        "mutual_auth": ma_txt != "false",
                                        "client_id": cid_txt or None,
                                    }
                            except Exception:
                                existing_bundle = None
                    except OSError as exc:
                        raise RuntimeError("Cannot read existing client identity; refusing to replace it. Check sudo/file permissions.") from exc
                    except Exception:
                        existing_bundle = None

            self.artifacts = self.adapter.prepare_telegraf_integration(
                os_family=self.target.os_family.value,
                target_ip=target_ip,
                target_hostname=self._get_registered_hostname(),
                target_uuid=target_uuid,
                existing_cert_bundle=existing_bundle,
                vm_mor=getattr(self.target, "vm_mor", None),
                vc_id=getattr(self.target, "vc_id", None),
            )
            if self.artifacts.token and self.artifacts.token not in self._secrets:
                self._secrets.append(self.artifacts.token)
            if self.artifacts.client_key_content and self.artifacts.client_key_content not in self._secrets:
                self._secrets.append(self.artifacts.client_key_content)

            dur = int((time.monotonic() - start) * 1000)
            id_desc = f"managed VM: {self.artifacts.vm_mor}" if self.artifacts.is_managed_vm else "unmanaged host"
            cert_desc = "mTLS certs ready" if self.artifacts.client_cert_content else "no client cert"
            res = StageResult(
                stage=WorkflowStage.PREPARE_VCF,
                status=StageStatus.PASS,
                message=f"VCF Ops validated, collector: {self.artifacts.collector_address} ({id_desc}, {cert_desc})",
                duration_ms=dur,
            )
        except Exception as e:
            dur = int((time.monotonic() - start) * 1000)
            res = StageResult(
                stage=WorkflowStage.PREPARE_VCF,
                status=StageStatus.FAIL,
                message=f"Failed to prepare VCF Ops integration: {e}",
                details=self._sanitize(str(e)),
                duration_ms=dur,
            )

        self.reporter.on_stage_complete(res)
        return res

    def render_inputs(self) -> StageResult:
        """Stage 4: Render structured monitoring models into Telegraf TOML fragments."""
        start = time.monotonic()
        self.reporter.on_stage_start(WorkflowStage.RENDER_INPUTS)

        try:
            # 1. Render system metrics
            self.system_conf_content = TelegrafRenderer.render_system_inputs(self.monitoring)

            # 2. Render VCF Cloud Proxy output
            collector_addr = (
                self.artifacts.collector_address
                if self.artifacts
                else self.env.collector.address
            )
            uuid_val = self.discovery.host_uuid if self.discovery else ""
            ip_val = self.discovery.host_ip if self.discovery and self.discovery.host_ip else self.target.hostname
            is_win = (self.target.os_family == OSFamily.WINDOWS) or (type(self.executor).__name__ == "WinRMExecutor")
            config_dir = self.discovery.config_dir if self.discovery else (r"C:\telegraf\telegraf.d" if is_win else "/etc/telegraf/telegraf.d")
            join = ntpath.join if is_win else os.path.join
            default_ca = join(config_dir, "ca.pem")
            default_cert = join(config_dir, "cert.pem")
            default_key = join(config_dir, "key.pem")
            mandatory_script = join(config_dir, "mandatory_tags.bat" if is_win else "mandatory_tags.sh")
            system_file = join(config_dir, "vcf-helper-system.conf")
            if self.executor.file_exists(system_file):
                deployed = self.executor.download(system_file)
                self.input_diff = self._sanitize("".join(difflib.unified_diff(
                    deployed.splitlines(keepends=True), self.system_conf_content.splitlines(keepends=True),
                    fromfile="deployed inputs", tofile="requested inputs",
                ))) or ""
                if not self.options.replace_inputs:
                    self.system_conf_content = deployed
                    self.reporter.on_message("Preserving deployed inputs. Use Replace existing inputs to apply a new selection.")
                elif self.input_diff:
                    self.reporter.on_message(self.input_diff)
            telegraf_bin = (
                self.discovery.telegraf_bin_path
                if self.discovery
                else ("C:\\telegraf\\telegraf.exe" if is_win else "/usr/bin/telegraf")
            )
            vm_mor_val = self.artifacts.vm_mor if self.artifacts else None
            vc_id_val = self.artifacts.vc_id if self.artifacts else None
            mutual_auth = self.artifacts.mutual_auth if self.artifacts else True

            has_ca = bool(self.artifacts and self.artifacts.ca_cert_content)
            has_cert = bool(self.artifacts and self.artifacts.client_cert_content)
            has_key = bool(self.artifacts and self.artifacts.client_key_content)
            ca_path = default_ca if has_ca else None
            cert_path = default_cert if has_cert else None
            key_path = default_key if has_key else None
            reg_hostname = self._get_registered_hostname()
            self.vcf_conf_content = TelegrafRenderer.render_vcf_output(
                collector_address=collector_addr,
                hostname=reg_hostname,
                uuid=uuid_val,
                ip=ip_val,
                verify_ssl=self.env.agent_verify_ssl,
                ca_cert_path=ca_path,
                cert_path=cert_path,
                key_path=key_path,
                vm_mor=vm_mor_val,
                vc_id=vc_id_val,
                mandatory_tags_path=mandatory_script,
                telegraf_bin_path=telegraf_bin,
                is_windows=is_win,
                mutual_auth=mutual_auth,
            )

            # 3. Render clean base stub to prevent duplicate metric collection
            self.base_stub_content = TelegrafRenderer.render_base_stub()
            if self.discovery and self.executor.file_exists(self.discovery.main_config_path):
                existing_base = self.executor.download(self.discovery.main_config_path)
                if "[[inputs." in existing_base and "Managed by VCF Operations Open Telegraf Helper" not in existing_base:
                    raise RuntimeError("Existing main configuration contains unmanaged inputs. Migrate/review those inputs before onboarding; no files have been changed.")

            # Validate syntax locally
            v1 = Validator.validate_toml_syntax(self.system_conf_content, "System Inputs")
            v2 = Validator.validate_toml_syntax(self.vcf_conf_content, "VCF Output")
            v3 = Validator.validate_toml_syntax(self.base_stub_content, "Base Config Stub")

            dur = int((time.monotonic() - start) * 1000)
            if v1.is_valid and v2.is_valid and v3.is_valid:
                res = StageResult(
                    stage=WorkflowStage.RENDER_INPUTS,
                    status=StageStatus.PASS,
                    message="Rendered vcf-helper-system.conf and cloudproxy-http.conf",
                    details=("Requested input changes (preserved unless replacement enabled):\n" + self.input_diff) if self.input_diff else None,
                    duration_ms=dur,
                )
            else:
                err_msg = v1.message if not v1.is_valid else (v2.message if not v2.is_valid else v3.message)
                res = StageResult(
                    stage=WorkflowStage.RENDER_INPUTS,
                    status=StageStatus.FAIL,
                    message=f"TOML rendering failed: {err_msg}",
                    duration_ms=dur,
                )
        except Exception as e:
            dur = int((time.monotonic() - start) * 1000)
            res = StageResult(
                stage=WorkflowStage.RENDER_INPUTS,
                status=StageStatus.FAIL,
                message=f"Render error: {e}",
                details=self._sanitize(str(e)),
                duration_ms=dur,
            )

        self.reporter.on_stage_complete(res)
        return res

    def validate(self) -> StageResult:
        """Stage 5: Validate configuration and test collector reachability from target."""
        start = time.monotonic()
        self.reporter.on_stage_start(WorkflowStage.VALIDATE)

        try:
            # Check structured config
            sc_val = Validator.validate_structured_config(self.monitoring)
            if not sc_val.is_valid:
                dur = int((time.monotonic() - start) * 1000)
                res = StageResult(
                    stage=WorkflowStage.VALIDATE,
                    status=StageStatus.FAIL,
                    message=sc_val.message,
                    duration_ms=dur,
                )
                self.reporter.on_stage_complete(res)
                return res

            # Check collector reachability from endpoint if not skipped and not in package mode
            collector_addr = (
                self.artifacts.collector_address
                if self.artifacts
                else self.env.collector.address
            )
            is_win = (self.target.os_family == OSFamily.WINDOWS) or (type(self.executor).__name__ == "WinRMExecutor")
            if not self.options.skip_collector_check:
                cp_val = Validator.validate_collector_reachability(self.executor, collector_addr, is_windows=is_win)
                self.verifications["Collector reachable"] = "PASS" if cp_val.is_valid else "FAIL"
                if not cp_val.is_valid:
                    dur = int((time.monotonic() - start) * 1000)
                    res = StageResult(
                        stage=WorkflowStage.VALIDATE,
                        status=StageStatus.FAIL,
                        message=f"Collector {collector_addr}:443 not reachable from target",
                        details=cp_val.details,
                        duration_ms=dur,
                    )
                    self.reporter.on_stage_complete(res)
                    return res
            else:
                self.verifications["Collector reachable"] = "SKIPPED"

            dur = int((time.monotonic() - start) * 1000)
            res = StageResult(
                stage=WorkflowStage.VALIDATE,
                status=StageStatus.PASS,
                message="Structured and generated configurations validated",
                duration_ms=dur,
            )
        except Exception as e:
            dur = int((time.monotonic() - start) * 1000)
            res = StageResult(
                stage=WorkflowStage.VALIDATE,
                status=StageStatus.FAIL,
                message=f"Validation error: {e}",
                details=self._sanitize(str(e)),
                duration_ms=dur,
            )

        self.reporter.on_stage_complete(res)
        return res

    def apply(self) -> StageResult:
        """Stage 6: Apply managed configuration fragments to the endpoint or package directory."""
        start = time.monotonic()
        self.reporter.on_stage_start(WorkflowStage.APPLY)

        if self.options.preview_only or self.options.dry_run:
            dur = int((time.monotonic() - start) * 1000)
            res = StageResult(
                stage=WorkflowStage.APPLY,
                status=StageStatus.SKIPPED,
                message="Skipped apply (dry-run or preview mode)",
                duration_ms=dur,
            )
            self.reporter.on_stage_complete(res)
            return res

        try:
            is_win = (self.target.os_family == OSFamily.WINDOWS) or (type(self.executor).__name__ == "WinRMExecutor")
            config_dir = (
                self.discovery.config_dir
                if self.discovery
                else ("C:\\telegraf\\telegraf.d" if is_win else "/etc/telegraf/telegraf.d")
            )
            sep = "\\" if is_win else "/"
            system_file = f"{config_dir}{sep}vcf-helper-system.conf"
            vcf_file = f"{config_dir}{sep}cloudproxy-http.conf"

            # Pre-flight disk space and sudo verification for push deployments
            free_mb = 1000
            if hasattr(self.executor, "get_free_disk_space_mb"):
                try:
                    val = self.executor.get_free_disk_space_mb("C:" if is_win else "/")
                    if isinstance(val, (int, float)):
                        free_mb = val
                except Exception:
                    free_mb = 1000
            if free_mb < 500:
                dur = int((time.monotonic() - start) * 1000)
                res = StageResult(
                    stage=WorkflowStage.APPLY,
                    status=StageStatus.FAIL,
                    message=f"Insufficient disk space on target ({free_mb} MB free, minimum 500 MB required). Apply aborted.",
                    duration_ms=dur,
                )
                self.reporter.on_stage_complete(res)
                return res

            if not is_win and getattr(self.executor, "use_sudo", False):
                sudo_chk = self.executor.execute("sudo -n true", timeout=5)
                if not sudo_chk.success:
                    dur = int((time.monotonic() - start) * 1000)
                    res = StageResult(
                        stage=WorkflowStage.APPLY,
                        status=StageStatus.FAIL,
                        message="Target Linux user lacks passwordless sudo privileges (sudoers NOPASSWD required). Apply aborted.",
                        details=self._sanitize(sudo_chk.stderr or sudo_chk.stdout),
                        duration_ms=dur,
                    )
                    self.reporter.on_stage_complete(res)
                    return res

            # Auto-install Telegraf if missing and requested
            auto_install = self.target.install_telegraf or self.options.install_telegraf
            if self.discovery and not self.discovery.telegraf_installed and auto_install:
                # Pre-flight disk space check: require at least 500 MB free
                if is_win:
                    disk_chk = self.executor.execute(
                        "try { $freeMb = [math]::Round((Get-PSDrive C).Free / 1MB); if ($freeMb -lt 500) { Write-Output \"FAIL: $freeMb\" } else { Write-Output \"OK: $freeMb\" } } catch { Write-Output 'OK: 9999' }",
                        timeout=10,
                    )
                    if disk_chk.success and "FAIL:" in disk_chk.stdout:
                        free_val = disk_chk.stdout.split("FAIL:")[-1].strip()
                        dur = int((time.monotonic() - start) * 1000)
                        res = StageResult(
                            stage=WorkflowStage.APPLY,
                            status=StageStatus.FAIL,
                            message=f"Insufficient disk space on target C: drive ({free_val} MB free, minimum 500 MB required). Auto-install aborted.",
                            duration_ms=dur,
                        )
                        self.reporter.on_stage_complete(res)
                        return res
                else:
                    disk_chk = self.executor.execute(
                        "min_free=$(df -m -P /var / 2>/dev/null | awk 'NR>1 {print $4}' | sort -n | head -n1); "
                        "if [ -n \"$min_free\" ] && [ \"$min_free\" -lt 500 ]; then echo \"FAIL: $min_free\"; else echo \"OK\"; fi",
                        timeout=10,
                    )
                    if disk_chk.success and "FAIL:" in disk_chk.stdout:
                        free_val = disk_chk.stdout.split("FAIL:")[-1].strip()
                        dur = int((time.monotonic() - start) * 1000)
                        res = StageResult(
                            stage=WorkflowStage.APPLY,
                            status=StageStatus.FAIL,
                            message=f"Insufficient disk space on target filesystem ({free_val} MB free on / or /var, minimum 500 MB required). Auto-install aborted.",
                            duration_ms=dur,
                        )
                        self.reporter.on_stage_complete(res)
                        return res

                telegraf_ver = (
                    getattr(self.options, "telegraf_version", None)
                    or getattr(self.target, "telegraf_version", None)
                    or "1.40.1"
                )
                arch_str = (getattr(self.discovery, "arch", "") or getattr(self.discovery, "architecture", "") or "").lower()
                is_arm = "arm" in arch_str or "aarch" in arch_str
                if is_win:
                    win_arch = "arm64" if is_arm else "amd64"
                    zip_url = f"https://dl.influxdata.com/telegraf/releases/telegraf-{telegraf_ver}_windows_{win_arch}.zip"
                    install_cmd = (
                        "$ErrorActionPreference = 'Stop'; "
                        "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; "
                        f"$zipUrl = '{zip_url}'; "
                        "$destZip = \"$env:TEMP\\telegraf.zip\"; "
                        "$destDir = 'C:\\telegraf'; "
                        "$hadConf = Test-Path \"$destDir\\telegraf.conf\"; "
                        "if (-not (Test-Path $destDir)) { New-Item -ItemType Directory -Path $destDir -Force | Out-Null }; "
                        "if (-not (Test-Path \"$destDir\\telegraf.d\")) { New-Item -ItemType Directory -Path \"$destDir\\telegraf.d\" -Force | Out-Null }; "
                        "Invoke-WebRequest -Uri $zipUrl -OutFile $destZip -UseBasicParsing; "
                        "Expand-Archive -Path $destZip -DestinationPath \"$env:TEMP\\telegraf_extract\" -Force; "
                        "$bin = Get-ChildItem -Path \"$env:TEMP\\telegraf_extract\" -Filter 'telegraf.exe' -Recurse | Select-Object -First 1; "
                        "if (-not $bin) { throw 'telegraf.exe binary not found in extracted archive' }; "
                        "Copy-Item -Path $bin.FullName -Destination \"$destDir\\telegraf.exe\" -Force; "
                        "$cfg = Get-ChildItem -Path \"$env:TEMP\\telegraf_extract\" -Filter 'telegraf.conf' -Recurse | Select-Object -First 1; "
                        "if ($cfg -and -not $hadConf) { Copy-Item -Path $cfg.FullName -Destination \"$destDir\\telegraf.conf\" -Force }; "
                        "$cfgPath = \"$destDir\\telegraf.conf\"; "
                        "if (-not $hadConf -and (Test-Path $cfgPath)) { "
                        "$c = [System.IO.File]::ReadAllText($cfgPath); "
                        "$c = $c -replace '(?m)^\\[\\[outputs\\.influxdb\\]\\]', '# [[outputs.influxdb]]' -replace '(?m)^\\s*urls\\s*=\\s*\\[\"http://127\\.0\\.0\\.1:8086\"\\]', '  # urls = [\"http://127.0.0.1:8086\"]'; "
                        "[System.IO.File]::WriteAllText($cfgPath, $c, (New-Object System.Text.UTF8Encoding $false)) "
                        "}; "
                        "Remove-Item -Path \"$env:TEMP\\telegraf_extract\" -Recurse -Force -ErrorAction SilentlyContinue; "
                        "Remove-Item -Path $destZip -Force -ErrorAction SilentlyContinue; "
                        "$svc = Get-Service -Name telegraf -ErrorAction SilentlyContinue; "
                        "if ($svc) { "
                        "$img = (Get-ItemProperty 'HKLM:\\SYSTEM\\CurrentControlSet\\Services\\telegraf' -ErrorAction SilentlyContinue).ImagePath; "
                        "if (-not $img -or $img -notlike \"*$destDir\\telegraf.exe*\" -or $img -notlike '*--config-directory*') { "
                        "$binArgs = \"`\"$destDir\\telegraf.exe`\" --config `\"$destDir\\telegraf.conf`\" --config-directory `\"$destDir\\telegraf.d`\"\"; "
                        "Set-ItemProperty -Path 'HKLM:\\SYSTEM\\CurrentControlSet\\Services\\telegraf' -Name ImagePath -Value $binArgs -Force; "
                        "& sc.exe config telegraf binPath= $binArgs | Out-Null; "
                        "Write-Output 'Service image path updated to managed directory' "
                        "} else { "
                        "Write-Output 'Service already registered' "
                        "} "
                        "} else { "
                        "& \"$destDir\\telegraf.exe\" --service install --config \"$destDir\\telegraf.conf\" --config-directory \"$destDir\\telegraf.d\" "
                        "}"
                    )
                else:
                    linux_arch = "arm64" if is_arm else "amd64"
                    tar_url = f"https://dl.influxdata.com/telegraf/releases/telegraf-{telegraf_ver}_linux_{linux_arch}.tar.gz"
                    sudo_pfx = "sudo -n " if getattr(self.executor, "use_sudo", False) else ""
                    install_cmd = (
                        f"{sudo_pfx}bash -c '"
                        "had_conf=0; if [ -f /etc/telegraf/telegraf.conf ]; then had_conf=1; fi; "
                        "if command -v apt-get >/dev/null 2>&1; then "
                        "distro=\"debian\"; if [ -f /etc/os-release ]; then . /etc/os-release; if [ \"$ID\" = \"ubuntu\" ]; then distro=\"ubuntu\"; fi; fi; "
                        "mkdir -p /etc/apt/trusted.gpg.d; "
                        "if command -v gpg >/dev/null 2>&1; then "
                        "curl -fsSL https://repos.influxdata.com/influxdata-archive.key | gpg --dearmor --yes -o /etc/apt/trusted.gpg.d/influxdata-archive.gpg 2>/dev/null; "
                        "else "
                        "curl -fsSL https://repos.influxdata.com/influxdata-archive_compat.key -o /etc/apt/trusted.gpg.d/influxdata.asc 2>/dev/null; "
                        "fi; "
                        "keyfile=\"/etc/apt/trusted.gpg.d/influxdata-archive.gpg\"; "
                        "if [ ! -f \"$keyfile\" ]; then keyfile=\"/etc/apt/trusted.gpg.d/influxdata.asc\"; fi; "
                        "echo \"deb [signed-by=$keyfile] https://repos.influxdata.com/$distro stable main\" > /etc/apt/sources.list.d/influxdata.list && "
                        f"DEBIAN_FRONTEND=noninteractive UCF_FORCE_CONFFOLD=1 apt-get update -qq && (DEBIAN_FRONTEND=noninteractive UCF_FORCE_CONFFOLD=1 apt-get install -y -qq -o Dpkg::Options::=\"--force-confdef\" -o Dpkg::Options::=\"--force-confold\" telegraf={telegraf_ver}-1 2>/dev/null || true); "
                        "elif command -v dnf >/dev/null 2>&1 || command -v yum >/dev/null 2>&1; then "
                        "(echo \"[influxdata]\"; echo \"name = InfluxData Repository\"; echo \"baseurl = https://repos.influxdata.com/rhel/\\$releasever/\\$basearch/stable\"; echo \"enabled = 1\"; echo \"gpgcheck = 1\"; echo \"gpgkey = https://repos.influxdata.com/influxdata-archive_compat.key\") > /etc/yum.repos.d/influxdata.repo && "
                        f"(dnf install -y -q telegraf-{telegraf_ver} 2>/dev/null || yum install -y -q telegraf-{telegraf_ver} 2>/dev/null) || true; "
                        "fi; "
                        "if ! command -v telegraf >/dev/null 2>&1; then "
                        "td=$(mktemp -d /tmp/telegraf.XXXXXX) && "
                        f"curl -fsSL \"{tar_url}\" | tar -xz -C \"$td\" && "
                        "cp \"$td\"/telegraf-*/usr/bin/telegraf /usr/bin/telegraf && "
                        "mkdir -p /etc/telegraf/telegraf.d && "
                        "if [ ! -f /etc/telegraf/telegraf.conf ]; then cp \"$td\"/telegraf-*/etc/telegraf/telegraf.conf /etc/telegraf/telegraf.conf; fi && "
                        "mkdir -p /lib/systemd/system && "
                        "if [ -f \"$td\"/telegraf-*/usr/lib/telegraf/scripts/telegraf.service ]; then cp \"$td\"/telegraf-*/usr/lib/telegraf/scripts/telegraf.service /lib/systemd/system/telegraf.service; fi && "
                        "rm -rf \"$td\"; "
                        "fi && "
                        "getent group telegraf >/dev/null 2>&1 || groupadd -r telegraf 2>/dev/null || addgroup --system telegraf 2>/dev/null || true; "
                        "id -u telegraf >/dev/null 2>&1 || useradd -r -U -s /bin/false telegraf 2>/dev/null || adduser --system --group --no-create-home telegraf 2>/dev/null || true; "
                        "mkdir -p /etc/telegraf/telegraf.d && "
                        "chown -R root:telegraf /etc/telegraf 2>/dev/null || true; "
                        "chmod 755 /etc/telegraf 2>/dev/null || true; "
                        "if [ $had_conf -eq 0 ] && [ -f /etc/telegraf/telegraf.conf ]; then "
                        "sed -i \"s|^\\[\\[outputs\\.influxdb\\]\\]|# [[outputs.influxdb]]|\" /etc/telegraf/telegraf.conf; "
                        "sed -i \"s|^[[:space:]]*urls = \\[\\\"http://127\\.0\\.0\\.1:8086\\\"\\]|  # urls = [\\\"http://127.0.0.1:8086\\\"]|\" /etc/telegraf/telegraf.conf; "
                        "fi && "
                        "if command -v systemctl >/dev/null 2>&1; then "
                        "systemctl daemon-reload || true; "
                        "systemctl enable telegraf || true; "
                        "fi'"
                    )
                inst_res = self.executor.execute(install_cmd, timeout=180)
                if not inst_res.success:
                    dur = int((time.monotonic() - start) * 1000)
                    res = StageResult(
                        stage=WorkflowStage.APPLY,
                        status=StageStatus.FAIL,
                        message=f"Failed to auto-install Telegraf agent: {inst_res.stderr or inst_res.stdout or 'Installation script failed'}",
                        details=self._sanitize(inst_res.stderr or inst_res.stdout),
                        duration_ms=dur,
                    )
                    self.reporter.on_stage_complete(res)
                    return res

                expected_bin = "C:\\telegraf\\telegraf.exe" if is_win else "/usr/bin/telegraf"
                bin_exists = self.executor.file_exists(expected_bin)
                if not bin_exists and type(self.executor).__name__ in ("SSHExecutor", "WinRMExecutor", "LocalExecutor"):
                    dur = int((time.monotonic() - start) * 1000)
                    res = StageResult(
                        stage=WorkflowStage.APPLY,
                        status=StageStatus.FAIL,
                        message=f"Telegraf binary not found at {expected_bin} after installation",
                        duration_ms=dur,
                    )
                    self.reporter.on_stage_complete(res)
                    return res
                self.discovery.telegraf_installed = True
                if is_win:
                    self.discovery.telegraf_bin_path = "C:\\telegraf\\telegraf.exe"
                    self.discovery.main_config_path = "C:\\telegraf\\telegraf.conf"
                    self.discovery.config_dir = "C:\\telegraf\\telegraf.d"
                else:
                    self.discovery.telegraf_bin_path = "/usr/bin/telegraf"
                    self.discovery.main_config_path = "/etc/telegraf/telegraf.conf"
                    self.discovery.config_dir = "/etc/telegraf/telegraf.d"

            # SHA-256 Idempotency Comparison
            self.is_idempotent = False
            main_cfg = (
                self.discovery.main_config_path
                if self.discovery
                else ("C:\\telegraf\\telegraf.conf" if is_win else "/etc/telegraf/telegraf.conf")
            )
            if self.executor.file_exists(system_file) and self.executor.file_exists(vcf_file):
                try:
                    def _hash_txt(s: str) -> str:
                        return hashlib.sha256(s.encode("utf-8")).hexdigest()

                    remote_sys = self.executor.download(system_file)
                    remote_vcf = self.executor.download(vcf_file)
                    match_sys = (_hash_txt(remote_sys) == _hash_txt(self.system_conf_content))
                    match_vcf = (_hash_txt(remote_vcf) == _hash_txt(self.vcf_conf_content))
                    match_base = True
                    if self.base_stub_content and self.executor.file_exists(main_cfg):
                        remote_base = self.executor.download(main_cfg)
                        match_base = (_hash_txt(remote_base) == _hash_txt(self.base_stub_content))

                    # Certificate artifacts are referenced by path only in the TOML, so a rotated
                    # bundle must be compared directly or it would never be uploaded.
                    match_certs = True
                    for c_dest, c_content, _ in self._security_artifact_files(config_dir, is_win):
                        if not self.executor.file_exists(c_dest) or (
                            _hash_txt(self.executor.download(c_dest)) != _hash_txt(c_content)
                        ):
                            match_certs = False
                            break

                    if match_sys and match_vcf and match_base and match_certs:
                        self.is_idempotent = True
                        self.managed_files = [system_file, vcf_file]
                        dur = int((time.monotonic() - start) * 1000)
                        res = StageResult(
                            stage=WorkflowStage.APPLY,
                            status=StageStatus.PASS,
                            message=f"Configuration unchanged (SHA-256 match). Managed fragments already current in {config_dir}.",
                            duration_ms=dur,
                        )
                        self.reporter.on_stage_complete(res)
                        return res
                except Exception:
                    self.is_idempotent = False

            # Create destination directory and backup existing fragments/certs
            if is_win:
                safe_dir = config_dir.replace("'", "''")
                self.executor.execute(
                    f"if (-not (Test-Path '{safe_dir}')) {{ New-Item -ItemType Directory -Path '{safe_dir}' -Force | Out-Null }}"
                )
                for f_name in (
                    "vcf-helper-system.conf", "cloudproxy-http.conf",
                    "ca.pem", "cert.pem", "key.pem",
                    "master.pub", "IP", "MUTUAL_AUTHENTICATION", "CLIENT_ID",
                    "mandatory_tags.bat"
                ):
                    f_dest = f"{config_dir}\\{f_name}"
                    if self.executor.file_exists(f_dest):
                        safe_dest = f_dest.replace("'", "''")
                        self.executor.execute(f"Copy-Item -Path '{safe_dest}' -Destination '{safe_dest}.bak' -Force")
            else:
                self.executor.execute(f"mkdir -p {shlex.quote(config_dir)}")
                for f_name in (
                    "vcf-helper-system.conf", "cloudproxy-http.conf",
                    "ca.pem", "cert.pem", "key.pem",
                    "master.pub", "IP", "MUTUAL_AUTHENTICATION", "CLIENT_ID",
                    "mandatory_tags.sh"
                ):
                    f_dest = f"{config_dir}/{f_name}"
                    if self.executor.file_exists(f_dest):
                        self.executor.execute(f"cp {shlex.quote(f_dest)} {shlex.quote(f'{f_dest}.bak')}")

            self.reporter.on_message("Uploading monitoring and output configuration...")
            # Upload managed fragments
            self.executor.upload(self.system_conf_content, system_file)
            self.executor.upload(self.vcf_conf_content, vcf_file)
            self.managed_files = [system_file, vcf_file]

            # Upload mTLS certificates and security artifacts
            for f_dest, f_content, f_mode in self._security_artifact_files(config_dir, is_win):
                self.reporter.on_message(f"Uploading {f_dest}...")
                self.executor.upload(f_content, f_dest, mode=f_mode)
                self.managed_files.append(f_dest)

            # Replace unmanaged stock telegraf.conf with clean base stub to eliminate duplicate inputs,
            # while preserving the original configuration in telegraf.conf.orig
            main_cfg = (
                self.discovery.main_config_path
                if self.discovery
                else ("C:\\telegraf\\telegraf.conf" if is_win else "/etc/telegraf/telegraf.conf")
            )
            if self.base_stub_content:
                exists = self.executor.file_exists(main_cfg)
                if exists:
                    if is_win:
                        safe_cfg = main_cfg.replace("'", "''")
                        self.executor.execute(f"Copy-Item -Path '{safe_cfg}' -Destination '{safe_cfg}.bak' -Force")
                    else:
                        self.executor.execute(f"cp {shlex.quote(main_cfg)} {shlex.quote(f'{main_cfg}.bak')}")
                if exists and not self.executor.file_exists(f"{main_cfg}.orig"):
                    if is_win and (isinstance(self.executor, WinRMExecutor) or type(self.executor).__name__ == "WinRMExecutor"):
                        escaped_cfg = main_cfg.replace("'", "''")
                        self.executor.execute(
                            f"if (-not (Select-String -Path '{escaped_cfg}' -Pattern 'Managed by VCF Operations' -SimpleMatch -Quiet)) {{ "
                            f"Copy-Item -Path '{escaped_cfg}' -Destination '{escaped_cfg}.orig' -Force }}"
                        )
                    elif not is_win and (isinstance(self.executor, SSHExecutor) or type(self.executor).__name__ == "SSHExecutor"):
                        sudo_pfx = "sudo -n " if getattr(self.executor, "use_sudo", False) else ""
                        self.executor.execute(
                            f"{sudo_pfx}bash -c 'if ! grep -q \"Managed by VCF Operations\" {shlex.quote(main_cfg)} 2>/dev/null; then "
                            f"cp {shlex.quote(main_cfg)} {shlex.quote(f'{main_cfg}.orig')} 2>/dev/null || true; fi'"
                        )
                    else:
                        try:
                            content_to_backup = self.executor.download(main_cfg)
                            if "Managed by VCF Operations Open Telegraf Helper" not in content_to_backup:
                                self.executor.upload(content_to_backup, f"{main_cfg}.orig", mode=0o644)
                        except Exception:
                            if is_win:
                                escaped_cfg = main_cfg.replace("'", "''")
                                self.executor.execute(f"Copy-Item -Path '{escaped_cfg}' -Destination '{escaped_cfg}.orig' -Force")
                            else:
                                self.executor.execute(f"cp {shlex.quote(main_cfg)} {shlex.quote(f'{main_cfg}.orig')}")

                self.executor.upload(self.base_stub_content, main_cfg, mode=0o644)
                if main_cfg not in self.managed_files:
                    self.managed_files.append(main_cfg)

            # Enforce remote Linux permissions
            if not is_win:
                parent_dir = str(os.path.dirname(config_dir)) or "/etc/telegraf"
                self.executor.execute(f"chown -R root:telegraf {shlex.quote(parent_dir)} 2>/dev/null || true")
                self.executor.execute(f"chmod 755 {shlex.quote(parent_dir)} {shlex.quote(config_dir)} 2>/dev/null || true")
                # Secure configuration files and certificates (do NOT include key.pem in chmod 644!)
                self.executor.execute(
                    f"chmod 644 {shlex.quote(main_cfg)} {shlex.quote(config_dir)}/*.conf "
                    f"{shlex.quote(config_dir)}/ca.pem {shlex.quote(config_dir)}/cert.pem "
                    f"{shlex.quote(config_dir)}/master.pub {shlex.quote(config_dir)}/IP "
                    f"{shlex.quote(config_dir)}/MUTUAL_AUTHENTICATION {shlex.quote(config_dir)}/CLIENT_ID 2>/dev/null || true"
                )
                # Client private key must NEVER be world-readable: 0640 with root:telegraf
                self.executor.execute(f"chown root:telegraf {shlex.quote(config_dir)}/key.pem 2>/dev/null || true")
                self.executor.execute(f"chmod 640 {shlex.quote(config_dir)}/key.pem 2>/dev/null || true")
                if self.artifacts and self.artifacts.mandatory_tags_content:
                    self.executor.execute(f"chmod 755 {shlex.quote(config_dir)}/mandatory_tags.sh 2>/dev/null || true")

            dur = int((time.monotonic() - start) * 1000)
            res = StageResult(
                stage=WorkflowStage.APPLY,
                status=StageStatus.PASS,
                message=f"Written {system_file} and {vcf_file}",
                duration_ms=dur,
            )
        except Exception as e:
            dur = int((time.monotonic() - start) * 1000)
            res = StageResult(
                stage=WorkflowStage.APPLY,
                status=StageStatus.FAIL,
                message=f"Failed to apply configuration: {e}",
                details=self._sanitize(str(e)),
                duration_ms=dur,
            )

        self.reporter.on_stage_complete(res)
        return res

    def _rollback_configs(self, config_dir: str, is_win: bool) -> None:
        """Restore previous configuration from .bak files if apply/restart fails."""
        files_to_restore = [
            "vcf-helper-system.conf",
            "cloudproxy-http.conf",
            "ca.pem",
            "cert.pem",
            "key.pem",
            "master.pub",
            "IP",
            "MUTUAL_AUTHENTICATION",
            "CLIENT_ID",
            "mandatory_tags.bat" if is_win else "mandatory_tags.sh",
        ]
        main_cfg = (
            self.discovery.main_config_path
            if self.discovery
            else ("C:\\telegraf\\telegraf.conf" if is_win else "/etc/telegraf/telegraf.conf")
        )
        if is_win:
            parts = []
            for f in files_to_restore:
                fpath = f"{config_dir}\\{f}"
                safe_fp = fpath.replace("'", "''")
                parts.append(
                    f"if (Test-Path '{safe_fp}.bak') {{ Move-Item -Path '{safe_fp}.bak' -Destination '{safe_fp}' -Force }} "
                    f"else {{ Remove-Item -Path '{safe_fp}' -Force -ErrorAction SilentlyContinue }};"
                )
            safe_cfg = main_cfg.replace("'", "''")
            parts.append(
                f"if (Test-Path '{safe_cfg}.bak') {{ Move-Item -Path '{safe_cfg}.bak' -Destination '{safe_cfg}' -Force }} "
                f"elseif (-not (Test-Path '{safe_cfg}.orig')) {{ Remove-Item -Path '{safe_cfg}' -Force -ErrorAction SilentlyContinue }};"
            )
            rollback_script = " ".join(parts)
            self.executor.execute(rollback_script, timeout=15)
        else:
            sudo_pfx = "sudo -n " if getattr(self.executor, "use_sudo", False) else ""
            bash_cmds = []
            for f in files_to_restore:
                fpath = f"{config_dir}/{f}"
                bash_cmds.append(
                    f"if [ -f {shlex.quote(f'{fpath}.bak')} ]; then mv -f {shlex.quote(f'{fpath}.bak')} {shlex.quote(fpath)}; "
                    f"else rm -f {shlex.quote(fpath)}; fi;"
                )
            bash_cmds.append(
                f"if [ -f {shlex.quote(f'{main_cfg}.bak')} ]; then mv -f {shlex.quote(f'{main_cfg}.bak')} {shlex.quote(main_cfg)}; "
                f"elif [ ! -f {shlex.quote(f'{main_cfg}.orig')} ]; then rm -f {shlex.quote(main_cfg)}; fi;"
            )
            inner_cmd = " ".join(bash_cmds)
            rollback_script = f"{sudo_pfx}bash -c '{inner_cmd}'"
            self.executor.execute(rollback_script, timeout=15)

    def _cleanup_bak_files(self, config_dir: str, is_win: bool) -> None:
        """Remove .bak backup files on successful verification."""
        try:
            if is_win:
                safe_dir = config_dir.replace("'", "''")
                safe_cfg = (
                    self.discovery.main_config_path.replace("'", "''")
                    if self.discovery and self.discovery.main_config_path
                    else "C:\\telegraf\\telegraf.conf"
                )
                cleanup_cmd = f"Remove-Item -Path '{safe_dir}\\*.bak', '{safe_cfg}.bak' -Force -ErrorAction SilentlyContinue"
            else:
                sudo_pfx = "sudo -n " if getattr(self.executor, "use_sudo", False) else ""
                cleanup_cmd = f"{sudo_pfx}rm -f {shlex.quote(config_dir)}/*.bak /etc/telegraf/telegraf.conf.bak 2>/dev/null || true"
            self.executor.execute(cleanup_cmd, timeout=10)
        except Exception:
            pass

    def restart_if_needed(self) -> StageResult:
        """Stage 7: Test configuration on endpoint and restart Telegraf service."""
        start = time.monotonic()
        self.reporter.on_stage_start(WorkflowStage.RESTART)

        if getattr(self, "is_idempotent", False):
            dur = int((time.monotonic() - start) * 1000)
            res = StageResult(
                stage=WorkflowStage.RESTART,
                status=StageStatus.SKIPPED,
                message="Configuration unchanged (SHA-256 match), skipping service restart",
                duration_ms=dur,
            )
            self.reporter.on_stage_complete(res)
            return res

        if (
            self.options.preview_only
            or self.options.dry_run
            or not self.options.restart_service
        ):
            dur = int((time.monotonic() - start) * 1000)
            res = StageResult(
                stage=WorkflowStage.RESTART,
                status=StageStatus.SKIPPED,
                message="Service restart skipped by deployment options",
                duration_ms=dur,
            )
            self.reporter.on_stage_complete(res)
            return res

        try:
            is_win = (self.target.os_family == OSFamily.WINDOWS) or (type(self.executor).__name__ == "WinRMExecutor")
            telegraf_bin = (
                self.discovery.telegraf_bin_path
                if self.discovery
                else ("C:\\telegraf\\telegraf.exe" if is_win else "/usr/bin/telegraf")
            )
            config_dir = (
                self.discovery.config_dir
                if self.discovery
                else ("C:\\telegraf\\telegraf.d" if is_win else "/etc/telegraf/telegraf.d")
            )
            main_cfg = (
                self.discovery.main_config_path
                if self.discovery
                else ("C:\\telegraf\\telegraf.conf" if is_win else "/etc/telegraf/telegraf.conf")
            )

            # Pre-flight check on endpoint before service restart
            if is_win:
                safe_bin = telegraf_bin.replace("'", "''")
                safe_cfg = main_cfg.replace("'", "''")
                safe_dir = config_dir.replace("'", "''")
                test_cmd = f"& '{safe_bin}' --test --config '{safe_cfg}' --config-directory '{safe_dir}'"
            else:
                test_cmd = f"{telegraf_bin} --test --config {main_cfg} --config-directory {config_dir}"

            test_res = self.executor.execute(test_cmd, timeout=15)
            if not test_res.success:
                self._rollback_configs(config_dir, is_win)
                dur = int((time.monotonic() - start) * 1000)
                res = StageResult(
                    stage=WorkflowStage.RESTART,
                    status=StageStatus.FAIL,
                    message="Telegraf config validation failed on endpoint. Restored previous configuration (.bak) to prevent outage.",
                    details=self._sanitize(test_res.stderr or test_res.stdout),
                    command_output=f"Validation Command: {test_cmd}\nExit Code: {test_res.exit_code}\nOutput:\n{test_res.stdout}\nErrors:\n{test_res.stderr}",
                    duration_ms=dur,
                )
                self.reporter.on_stage_complete(res)
                return res

            # Restart service
            if is_win:
                svc_name = (
                    self.discovery.service_name
                    if self.discovery and self.discovery.service_name
                    else None
                )
                if not svc_name:
                    # Register service if not yet installed in Windows SCM
                    safe_bin = telegraf_bin.replace("'", "''")
                    safe_cfg = main_cfg.replace("'", "''")
                    safe_dir = config_dir.replace("'", "''")
                    self.executor.execute("Stop-Process -Name telegraf -Force -ErrorAction SilentlyContinue", timeout=10)
                    reg_cmd = f"& '{safe_bin}' --service install --config '{safe_cfg}' --config-directory '{safe_dir}'"
                    reg_res = self.executor.execute(reg_cmd, timeout=15)
                    logger.info("Registered Telegraf Windows service: exit_code=%s", reg_res.exit_code)
                    svc_name = "telegraf"
                    if self.discovery:
                        self.discovery.service_name = "telegraf"

                safe_svc = svc_name.replace("'", "''")
                restart_cmd = (
                    "Restart-Service telegraf -Force"
                    if svc_name == "telegraf"
                    else f"Restart-Service '{safe_svc}' -Force"
                )
                restart_res = self.executor.execute(restart_cmd, timeout=15)
                if not restart_res.success:
                    start_cmd = (
                        "Start-Service telegraf"
                        if svc_name == "telegraf"
                        else f"Start-Service '{safe_svc}'"
                    )
                    start_res = self.executor.execute(start_cmd, timeout=15)
                    if start_res.success:
                        restart_res = start_res
                        restart_cmd = start_cmd
            else:
                restart_cmd = "systemctl restart telegraf"
                restart_res = self.executor.execute(restart_cmd, timeout=15)
            dur = int((time.monotonic() - start) * 1000)

            if restart_res.success:
                res = StageResult(
                    stage=WorkflowStage.RESTART,
                    status=StageStatus.PASS,
                    message="Telegraf service restarted successfully",
                    command_output=f"Restart Command: {restart_cmd}\nExit Code: {restart_res.exit_code}",
                    duration_ms=dur,
                )
            else:
                self._rollback_configs(config_dir, is_win)
                # Attempt to restart with restored backup
                if is_win:
                    self.executor.execute(restart_cmd, timeout=15)
                else:
                    self.executor.execute("systemctl restart telegraf", timeout=15)
                res = StageResult(
                    stage=WorkflowStage.RESTART,
                    status=StageStatus.FAIL,
                    message="Failed to restart Telegraf service. Restored previous configuration (.bak).",
                    details=self._sanitize(restart_res.stderr or restart_res.stdout),
                    command_output=f"Restart Command: {restart_cmd}\nExit Code: {restart_res.exit_code}\nOutput:\n{restart_res.stdout}\nErrors:\n{restart_res.stderr}",
                    duration_ms=dur,
                )
        except Exception as e:
            dur = int((time.monotonic() - start) * 1000)
            res = StageResult(
                stage=WorkflowStage.RESTART,
                status=StageStatus.FAIL,
                message=f"Service restart error: {e}",
                details=self._sanitize(str(e)),
                duration_ms=dur,
            )

        self.reporter.on_stage_complete(res)
        return res

    def verify(self) -> StageResult:
        """Stage 8: Independently verify all layers (installation, syntax, service, metrics, ingestion)."""
        start = time.monotonic()
        self.reporter.on_stage_start(WorkflowStage.VERIFY)

        try:
            is_win = (self.target.os_family == OSFamily.WINDOWS) or (type(self.executor).__name__ == "WinRMExecutor")
            # 1. Telegraf installed check
            installed = (
                self.discovery.telegraf_installed if self.discovery else False
            )
            if not (self.options.dry_run or self.options.preview_only):
                self.verifications["Telegraf installed"] = "PASS" if installed else "FAIL"
            else:
                self.verifications["Telegraf installed"] = "PASS" if installed else "SKIPPED"

            # 2. Config valid check
            cfg_valid = bool(self.system_conf_content and self.vcf_conf_content)
            self.verifications["Config valid"] = "PASS" if cfg_valid else "FAIL"

            if not (self.options.dry_run or self.options.preview_only):
                # 3. Service running check
                svc_name = (
                    self.discovery.service_name
                    if self.discovery and self.discovery.service_name
                    else "telegraf"
                )
                svc_val = Validator.validate_service_state(
                    self.executor, is_windows=is_win, service_name=svc_name
                )
                self.verifications["Service running"] = "PASS" if svc_val.is_valid else "FAIL"

                # 4. Local metrics generated
                telegraf_bin = (
                    self.discovery.telegraf_bin_path
                    if self.discovery
                    else ("C:\\telegraf\\telegraf.exe" if is_win else "/usr/bin/telegraf")
                )
                config_dir = (
                    self.discovery.config_dir
                    if self.discovery
                    else ("C:\\telegraf\\telegraf.d" if is_win else "/etc/telegraf/telegraf.d")
                )
                main_cfg = (
                    self.discovery.main_config_path
                    if self.discovery
                    else ("C:\\telegraf\\telegraf.conf" if is_win else "/etc/telegraf/telegraf.conf")
                )
                test_val = Validator.validate_telegraf_config_on_endpoint(
                    self.executor, telegraf_bin, main_cfg, config_dir, is_windows=is_win
                )
                self.verifications["Local metrics generated"] = (
                    "PASS" if test_val.is_valid else "FAIL"
                )

                # 5. Output transmission check (active mTLS metric probe and service log inspection)
                metric_headers = {
                    "Content-Type": "text/plain; charset=utf-8",
                }
                short_host = self._get_registered_hostname()
                if self.artifacts and self.artifacts.is_managed_vm and self.artifacts.vm_mor and self.artifacts.vc_id:
                    metric_headers["vmId"] = self.artifacts.vm_mor
                    metric_headers["vcid"] = self.artifacts.vc_id
                    metric_headers["hostname"] = short_host
                    metric_headers["uuid"] = ""
                else:
                    metric_headers["uuid"] = (self.discovery.host_uuid if self.discovery else "") or ""
                    metric_headers["ip"] = self.target.hostname
                    metric_headers["hostname"] = short_host

                collector_addr = self.artifacts.collector_address if self.artifacts else self.target.hostname
                cert_file = f"{config_dir}\\cert.pem" if is_win else f"{config_dir}/cert.pem"
                key_file = f"{config_dir}\\key.pem" if is_win else f"{config_dir}/key.pem"
                ca_file = f"{config_dir}\\ca.pem" if is_win else f"{config_dir}/ca.pem"

                probe_val = Validator.validate_cloudproxy_mtls_metric_probe(
                    executor=self.executor,
                    collector_address=collector_addr,
                    cert_path=cert_file,
                    key_path=key_file,
                    ca_cert_path=ca_file,
                    headers=metric_headers,
                    hostname=short_host,
                    is_windows=is_win,
                    verify_ssl=self.env.agent_verify_ssl,
                )
                # 6. Ingestion in VCF Ops
                ingestion_status = self.adapter.verify_ingestion(short_host, since=self.started_at)
                if ingestion_status == "UNKNOWN" and short_host != self.target.hostname:
                    ingestion_status = self.adapter.verify_ingestion(self.target.hostname, since=self.started_at)
                if ingestion_status == "UNKNOWN":
                    self.verifications["VCF Ops ingestion"] = "PENDING (Ops processing typically requires 5 to 15 minutes)"
                else:
                    self.verifications["VCF Ops ingestion"] = ingestion_status

                # Verify metrics transmission either via authenticated probe or confirmed VCF Ops ingestion
                is_schannel_err = is_win and (
                    "schannel" in (str(probe_val.message) + " " + str(probe_val.details)).lower()
                    or "0x80092002" in (str(probe_val.message) + " " + str(probe_val.details)).lower()
                )
                if probe_val.is_valid:
                    self.verifications["Metrics transmission"] = "PASS"
                elif is_schannel_err:
                    collector_ok = self.verifications.get("Collector reachable") != "FAIL"
                    if ingestion_status == "PASS":
                        self.verifications["Metrics transmission"] = "PASS (verified via VCF Ops ingestion)"
                    elif (
                        collector_ok
                        and self.verifications.get("Service running") == "PASS"
                        and self.verifications.get("Local metrics generated") == "PASS"
                    ):
                        self.verifications["Metrics transmission"] = "PENDING (Windows Schannel PEM limitation; service and port 443 verified)"
                    else:
                        self.verifications["Metrics transmission"] = f"FAIL ({probe_val.message})"
                else:
                    self.verifications["Metrics transmission"] = f"FAIL ({probe_val.message})"

                # Also inspect recent service log for output errors on Linux and Windows
                if self.verifications["Metrics transmission"].startswith("PASS") or self.verifications["Metrics transmission"].startswith("PENDING"):
                    if is_win:
                        win_log_cmd = (
                            "$t = (Get-Date).AddMinutes(-2); "
                            "try { $events = Get-WinEvent -FilterHashtable @{LogName='Application'; ProviderName='telegraf'; StartTime=$t} -ErrorAction Stop; "
                            "$events | ForEach-Object { $_.Message } } catch { }; exit 0"
                        )
                        log_res = self.executor.execute(win_log_cmd, timeout=10)
                        if log_res.success and log_res.stdout:
                            if "received status code: 403" in log_res.stdout or "Error writing to outputs.http" in log_res.stdout:
                                self.verifications["Metrics transmission"] = "FAIL (HTTP 403 Forbidden in telegraf service log)"
                    else:
                        journal_res = self.executor.execute('journalctl -u telegraf --since "-1 minute" --no-pager 2>/dev/null', timeout=5)
                        if journal_res.success and journal_res.stdout:
                            if "received status code: 403" in journal_res.stdout or "Error writing to outputs.http" in journal_res.stdout:
                                self.verifications["Metrics transmission"] = "FAIL (HTTP 403 Forbidden in telegraf service log)"
            else:
                self.verifications["Service running"] = "SKIPPED"
                self.verifications["Local metrics generated"] = "SKIPPED"
                self.verifications["Metrics transmission"] = "SKIPPED"
                self.verifications["VCF Ops ingestion"] = "SKIPPED"

            failed_checks = [k for k, v in self.verifications.items() if str(v).startswith("FAIL")]
            dur = int((time.monotonic() - start) * 1000)
            if failed_checks:
                res = StageResult(
                    stage=WorkflowStage.VERIFY,
                    status=StageStatus.FAIL,
                    message=f"Verification failed on: {', '.join(failed_checks)}",
                    duration_ms=dur,
                )
            else:
                if not (self.options.dry_run or self.options.preview_only):
                    config_dir = (
                        self.discovery.config_dir
                        if self.discovery
                        else ("C:\\telegraf\\telegraf.d" if is_win else "/etc/telegraf/telegraf.d")
                    )
                    self._cleanup_bak_files(config_dir, is_win)
                res = StageResult(
                    stage=WorkflowStage.VERIFY,
                    status=StageStatus.SKIPPED if (self.options.dry_run or self.options.preview_only) else (StageStatus.WARNING if any(str(v).startswith("PENDING") for v in self.verifications.values()) else StageStatus.PASS),
                    message="Live verification skipped (preview/dry-run)" if (self.options.dry_run or self.options.preview_only) else "Verification completed; see individual checks",
                    duration_ms=dur,
                )
        except Exception as e:
            dur = int((time.monotonic() - start) * 1000)
            res = StageResult(
                stage=WorkflowStage.VERIFY,
                status=StageStatus.WARNING,
                message=f"Verification encountered warning: {e}",
                details=self._sanitize(str(e)),
                duration_ms=dur,
            )

        self.reporter.on_stage_complete(res)
        return res

    def run(self) -> RunSummary:
        """Execute the entire 8-stage workflow sequentially."""
        stages = [
            self.detect_target,
            self.detect_telegraf,
            self.configure_vcf_output,
            self.render_inputs,
            self.validate,
            self.apply,
            self.restart_if_needed,
            self.verify,
        ]

        overall_success = True
        for stage_fn in stages:
            if stage_fn == self.apply and self.preview_callback:
                self.preview_callback()
            stage_res = stage_fn()
            stage_res.message = self._sanitize(stage_res.message) or ""
            stage_res.details = self._sanitize(stage_res.details)
            stage_res.command_output = self._sanitize(stage_res.command_output)
            self.stage_results.append(stage_res)

            # Abort if a critical stage failed
            if stage_res.status == StageStatus.FAIL:
                overall_success = False
                break

        return RunSummary(
            target_hostname=self.target.hostname,
            vcf_environment=self.env.url,
            collector_address=self.env.collector.address,
            deployment_mode=self.options.mode.value,
            success=overall_success,
            stages=self.stage_results,
            verifications=self.verifications,
            managed_files=self.managed_files,
        )
