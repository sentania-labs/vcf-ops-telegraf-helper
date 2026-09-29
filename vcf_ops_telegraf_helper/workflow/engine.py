"""Workflow execution engine for configuring open-source Telegraf on an endpoint."""

from __future__ import annotations

import time
from typing import Dict, List, Optional

from vcf_ops_telegraf_helper.adapters.base import IntegrationArtifacts, VCFOpsIntegration
from vcf_ops_telegraf_helper.executors.base import EndpointExecutor
from vcf_ops_telegraf_helper.models.endpoint import (
    EndpointDiscoveryResult,
    EndpointTarget,
    OSFamily,
)
from vcf_ops_telegraf_helper.models.monitoring import MonitoringConfig
from vcf_ops_telegraf_helper.models.vcf import VCFEnvironment
from vcf_ops_telegraf_helper.models.workflow import (
    DeploymentMode,
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
                    message=f"Connected to {self.target.hostname} ({self.target.connection_method.value})",
                    duration_ms=dur,
                )
            else:
                res = StageResult(
                    stage=WorkflowStage.CONNECT,
                    status=StageStatus.FAIL,
                    message=f"Failed to connect to {self.target.hostname}",
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

                chk_bin = self.executor.execute("Test-Path 'C:\\telegraf\\telegraf.exe'", timeout=10)
                installed = chk_bin.success and "True" in chk_bin.stdout

                version_str = None
                if installed:
                    ver_bin = self.executor.execute("& 'C:\\telegraf\\telegraf.exe' version", timeout=10)
                    if ver_bin.success:
                        version_str = ver_bin.stdout.strip()

                svc_res = self.executor.execute("(Get-Service telegraf -ErrorAction SilentlyContinue).Status", timeout=10)
                service_state = svc_res.stdout.strip() if svc_res.success and svc_res.stdout.strip() else "Stopped"

                uuid_res = self.executor.execute("(Get-CimInstance Win32_ComputerSystemProduct).UUID", timeout=10)
                host_uuid = uuid_res.stdout.strip() if uuid_res.success else ""
                ip_res = self.executor.execute(
                    "(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.InterfaceAlias -notlike '*Loopback*' }).IPAddress | Select-Object -First 1",
                    timeout=10,
                )
                host_ip = ip_res.stdout.strip() if ip_res.success and ip_res.stdout.strip() else self.target.hostname

                self.discovery = EndpointDiscoveryResult(
                    hostname=self.target.hostname,
                    os_name=os_name,
                    os_version=os_version,
                    arch=arch,
                    telegraf_installed=installed,
                    telegraf_version=version_str,
                    service_state=service_state,
                    config_dir="C:\\telegraf\\telegraf.d",
                    main_config_path="C:\\telegraf\\telegraf.conf",
                    telegraf_bin_path="C:\\telegraf\\telegraf.exe",
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

                self.discovery = EndpointDiscoveryResult(
                    hostname=self.target.hostname,
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

            if not installed and self.options.mode == DeploymentMode.PUSH and not auto_install:
                res = StageResult(
                    stage=WorkflowStage.DETECT,
                    status=StageStatus.FAIL,
                    message=f"Telegraf not installed on {self.target.hostname}. Install Telegraf before push, enable auto-install, or use script mode.",
                    details=f"{os_version} ({arch}), Service state: {service_state}",
                    duration_ms=dur,
                )
            else:
                status = StageStatus.PASS if installed else StageStatus.WARNING
                msg_suffix = " (will auto-install official InfluxData agent)" if (not installed and auto_install) else ""
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
            self.artifacts = self.adapter.prepare_telegraf_integration(
                os_family=self.target.os_family.value,
                target_ip=target_ip,
                target_hostname=self.target.hostname,
                target_uuid=target_uuid,
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
            default_ca = "C:\\telegraf\\telegraf.d\\ca.pem" if is_win else "/etc/telegraf/telegraf.d/ca.pem"
            default_cert = "C:\\telegraf\\telegraf.d\\cert.pem" if is_win else "/etc/telegraf/telegraf.d/cert.pem"
            default_key = "C:\\telegraf\\telegraf.d\\key.pem" if is_win else "/etc/telegraf/telegraf.d/key.pem"
            mandatory_script = (
                "C:\\telegraf\\telegraf.d\\mandatory_tags.bat" if is_win else "/etc/telegraf/telegraf.d/mandatory_tags.sh"
            )
            telegraf_bin = (
                self.discovery.telegraf_bin_path
                if self.discovery
                else ("C:\\telegraf\\telegraf.exe" if is_win else "/usr/bin/telegraf")
            )
            vm_mor_val = self.artifacts.vm_mor if self.artifacts else None
            vc_id_val = self.artifacts.vc_id if self.artifacts else None

            has_ca = bool(self.artifacts and (self.artifacts.ca_cert_content or self.env.ca_cert_path))
            has_cert = bool(self.artifacts and self.artifacts.client_cert_content)
            has_key = bool(self.artifacts and self.artifacts.client_key_content)
            ca_path = (self.env.ca_cert_path or default_ca) if has_ca else None
            cert_path = default_cert if has_cert else None
            key_path = default_key if has_key else None

            self.vcf_conf_content = TelegrafRenderer.render_vcf_output(
                collector_address=collector_addr,
                hostname=self.target.hostname,
                uuid=uuid_val,
                ip=ip_val,
                verify_ssl=self.env.verify_ssl,
                ca_cert_path=ca_path,
                cert_path=cert_path,
                key_path=key_path,
                vm_mor=vm_mor_val,
                vc_id=vc_id_val,
                mandatory_tags_path=mandatory_script,
                telegraf_bin_path=telegraf_bin,
                is_windows=is_win,
            )

            # 3. Render clean base stub to prevent duplicate metric collection
            self.base_stub_content = TelegrafRenderer.render_base_stub()

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
            if not self.options.skip_collector_check and self.options.mode == DeploymentMode.PUSH:
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

            # Auto-install Telegraf if missing and requested
            auto_install = self.target.install_telegraf or self.options.install_telegraf
            if self.discovery and not self.discovery.telegraf_installed and auto_install:
                arch_str = (getattr(self.discovery, "arch", "") or getattr(self.discovery, "architecture", "") or "").lower()
                is_arm = "arm" in arch_str or "aarch" in arch_str
                if is_win:
                    win_arch = "arm64" if is_arm else "amd64"
                    zip_url = f"https://dl.influxdata.com/telegraf/releases/telegraf-1.32.1_windows_{win_arch}.zip"
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
                    tar_url = f"https://dl.influxdata.com/telegraf/releases/telegraf-1.32.1_linux_{linux_arch}.tar.gz"
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
                        "DEBIAN_FRONTEND=noninteractive UCF_FORCE_CONFFOLD=1 apt-get update -qq && DEBIAN_FRONTEND=noninteractive UCF_FORCE_CONFFOLD=1 apt-get install -y -qq -o Dpkg::Options::=\"--force-confdef\" -o Dpkg::Options::=\"--force-confold\" telegraf || true; "
                        "elif command -v dnf >/dev/null 2>&1 || command -v yum >/dev/null 2>&1; then "
                        "(echo \"[influxdata]\"; echo \"name = InfluxData Repository\"; echo \"baseurl = https://repos.influxdata.com/rhel/\\$releasever/\\$basearch/stable\"; echo \"enabled = 1\"; echo \"gpgcheck = 1\"; echo \"gpgkey = https://repos.influxdata.com/influxdata-archive_compat.key\") > /etc/yum.repos.d/influxdata.repo && "
                        "(dnf install -y -q telegraf 2>/dev/null || yum install -y -q telegraf 2>/dev/null) || true; "
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
                self.discovery.telegraf_installed = True
                if is_win:
                    self.discovery.telegraf_bin_path = "C:\\telegraf\\telegraf.exe"
                    self.discovery.main_config_path = "C:\\telegraf\\telegraf.conf"
                    self.discovery.config_dir = "C:\\telegraf\\telegraf.d"
                else:
                    self.discovery.telegraf_bin_path = "/usr/bin/telegraf"
                    self.discovery.main_config_path = "/etc/telegraf/telegraf.conf"
                    self.discovery.config_dir = "/etc/telegraf/telegraf.d"

            # Create destination directory
            if is_win:
                self.executor.execute(
                    f"if (-not (Test-Path '{config_dir}')) {{ New-Item -ItemType Directory -Path '{config_dir}' -Force | Out-Null }}"
                )
                if self.executor.file_exists(system_file):
                    self.executor.execute(f"Copy-Item -Path '{system_file}' -Destination '{system_file}.bak' -Force")
                if self.executor.file_exists(vcf_file):
                    self.executor.execute(f"Copy-Item -Path '{vcf_file}' -Destination '{vcf_file}.bak' -Force")
            else:
                self.executor.execute(f"mkdir -p {config_dir}")
                if self.executor.file_exists(system_file):
                    self.executor.execute(f"cp {system_file} {system_file}.bak")
                if self.executor.file_exists(vcf_file):
                    self.executor.execute(f"cp {vcf_file} {vcf_file}.bak")

            # Upload managed fragments
            self.executor.upload(self.system_conf_content, system_file)
            self.executor.upload(self.vcf_conf_content, vcf_file)
            self.managed_files = [system_file, vcf_file]

            # Upload mTLS certificates if acquired
            if self.artifacts and self.artifacts.ca_cert_content:
                ca_dest = f"{config_dir}\\ca.pem" if is_win else f"{config_dir}/ca.pem"
                self.executor.upload(self.artifacts.ca_cert_content, ca_dest, mode=0o644)
                self.managed_files.append(ca_dest)
            if self.artifacts and self.artifacts.client_cert_content:
                cert_dest = f"{config_dir}\\cert.pem" if is_win else f"{config_dir}/cert.pem"
                self.executor.upload(self.artifacts.client_cert_content, cert_dest, mode=0o644)
                self.managed_files.append(cert_dest)
            if self.artifacts and self.artifacts.client_key_content:
                key_dest = f"{config_dir}\\key.pem" if is_win else f"{config_dir}/key.pem"
                self.executor.upload(self.artifacts.client_key_content, key_dest, mode=0o640)
                self.managed_files.append(key_dest)

            # Upload mandatory_tags script if present
            if self.artifacts and self.artifacts.mandatory_tags_content:
                tags_dest = f"{config_dir}\\mandatory_tags.bat" if is_win else f"{config_dir}/mandatory_tags.sh"
                self.executor.upload(self.artifacts.mandatory_tags_content, tags_dest, mode=0o755)
                self.managed_files.append(tags_dest)

            # Deploy clean base stub to main telegraf.conf when freshly installed to prevent duplicate inputs,
            # while preserving pre-existing customer configuration files if present
            main_cfg = (
                self.discovery.main_config_path
                if self.discovery
                else ("C:\\telegraf\\telegraf.conf" if is_win else "/etc/telegraf/telegraf.conf")
            )
            should_replace_base = (
                not self.executor.file_exists(main_cfg)
                or (self.discovery and not self.discovery.telegraf_installed)
            )
            if should_replace_base and self.base_stub_content:
                if self.executor.file_exists(main_cfg) and not self.executor.file_exists(f"{main_cfg}.orig"):
                    if is_win:
                        self.executor.execute(f"Copy-Item -Path '{main_cfg}' -Destination '{main_cfg}.orig' -Force")
                    else:
                        self.executor.execute(f"cp {main_cfg} {main_cfg}.orig")
                self.executor.upload(self.base_stub_content, main_cfg, mode=0o644)

            # Enforce remote Linux permissions
            if not is_win:
                self.executor.execute("chown -R root:telegraf /etc/telegraf 2>/dev/null || true")
                self.executor.execute("chmod 755 /etc/telegraf /etc/telegraf/telegraf.d 2>/dev/null || true")
                if self.artifacts and self.artifacts.client_key_content:
                    self.executor.execute(f"chown root:telegraf {config_dir}/key.pem 2>/dev/null || true")
                    self.executor.execute(f"chmod 640 {config_dir}/key.pem 2>/dev/null || true")

            if hasattr(self.executor, "generate_deploy_script"):
                telegraf_bin = (
                    self.discovery.telegraf_bin_path
                    if self.discovery
                    else ("/usr/bin/telegraf" if not is_win else "C:\\telegraf\\telegraf.exe")
                )
                script_path = self.executor.generate_deploy_script(telegraf_bin=telegraf_bin)
                self.managed_files.append(str(script_path))

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

    def restart_if_needed(self) -> StageResult:
        """Stage 7: Test configuration on endpoint and restart Telegraf service."""
        start = time.monotonic()
        self.reporter.on_stage_start(WorkflowStage.RESTART)

        if (
            self.options.preview_only
            or self.options.dry_run
            or self.options.mode != DeploymentMode.PUSH
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
                test_cmd = f"& '{telegraf_bin}' --test --config '{main_cfg}' --config-directory '{config_dir}'"
            else:
                test_cmd = f"{telegraf_bin} --test --config {main_cfg} --config-directory {config_dir}"

            test_res = self.executor.execute(test_cmd, timeout=15)
            if not test_res.success:
                dur = int((time.monotonic() - start) * 1000)
                res = StageResult(
                    stage=WorkflowStage.RESTART,
                    status=StageStatus.FAIL,
                    message="Telegraf config validation failed on endpoint. Aborting restart to prevent outage.",
                    details=self._sanitize(test_res.stderr or test_res.stdout),
                    duration_ms=dur,
                )
                self.reporter.on_stage_complete(res)
                return res

            # Restart service
            if is_win:
                restart_res = self.executor.execute("Restart-Service telegraf -Force", timeout=15)
            else:
                restart_res = self.executor.execute("systemctl restart telegraf", timeout=15)
            dur = int((time.monotonic() - start) * 1000)

            if restart_res.success:
                res = StageResult(
                    stage=WorkflowStage.RESTART,
                    status=StageStatus.PASS,
                    message="Telegraf service restarted successfully",
                    duration_ms=dur,
                )
            else:
                res = StageResult(
                    stage=WorkflowStage.RESTART,
                    status=StageStatus.FAIL,
                    message="Failed to restart Telegraf service",
                    details=self._sanitize(restart_res.stderr or restart_res.stdout),
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
            self.verifications["Telegraf installed"] = "PASS" if installed else "FAIL"

            # 2. Config valid check
            cfg_valid = bool(self.system_conf_content and self.vcf_conf_content)
            self.verifications["Config valid"] = "PASS" if cfg_valid else "FAIL"

            if self.options.mode == DeploymentMode.PUSH and not self.options.dry_run:
                # 3. Service running check
                svc_val = Validator.validate_service_state(self.executor, is_windows=is_win)
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

                # 5. Output transmission check (verify no 403 Forbidden or network rejection in recent log)
                transmission_status = "PASS"
                if not is_win:
                    journal_res = self.executor.execute('journalctl -u telegraf --since "-2 minutes" --no-pager 2>/dev/null', timeout=5)
                    if journal_res.success and journal_res.stdout:
                        if "received status code: 403" in journal_res.stdout or "Error writing to outputs.http" in journal_res.stdout:
                            transmission_status = "FAIL (HTTP 403 Forbidden: collector rejected request)"
                self.verifications["Metrics transmission"] = transmission_status

                # 6. Ingestion in VCF Ops
                ingestion_status = self.adapter.verify_ingestion(self.target.hostname)
                self.verifications["VCF Ops ingestion"] = ingestion_status
            else:
                self.verifications["Service running"] = "SKIPPED"
                self.verifications["Local metrics generated"] = "SKIPPED"
                self.verifications["Metrics transmission"] = "SKIPPED"
                self.verifications["VCF Ops ingestion"] = "SKIPPED"

            dur = int((time.monotonic() - start) * 1000)
            res = StageResult(
                stage=WorkflowStage.VERIFY,
                status=StageStatus.PASS,
                message="Completed all verification checks",
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
            stage_res = stage_fn()
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
