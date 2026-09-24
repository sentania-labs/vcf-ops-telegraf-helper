"""Workflow execution engine for configuring open-source Telegraf on an endpoint."""

from __future__ import annotations

import time
from typing import Dict, List, Optional

from vcf_ops_telegraf_helper.adapters.base import IntegrationArtifacts, VCFOpsIntegration
from vcf_ops_telegraf_helper.executors.base import EndpointExecutor
from vcf_ops_telegraf_helper.models.endpoint import (
    EndpointDiscoveryResult,
    EndpointTarget,
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
            # Query OS details
            os_name = "Linux"
            arch_res = self.executor.execute("uname -m", timeout=5)
            arch = arch_res.stdout.strip() if arch_res.success else "x86_64"

            os_rel = self.executor.execute("cat /etc/os-release", timeout=5)
            os_version = "Unknown Linux"
            if os_rel.success:
                for line in os_rel.stdout.splitlines():
                    if line.startswith("PRETTY_NAME="):
                        os_version = line.split("=", 1)[1].strip('"')
                        break

            # Check Telegraf binary
            which_res = self.executor.execute("which telegraf", timeout=5)
            installed = which_res.success
            telegraf_bin = which_res.stdout.strip() if installed else "/usr/bin/telegraf"

            version_str: Optional[str] = None
            if installed:
                ver_res = self.executor.execute(f"{telegraf_bin} version", timeout=5)
                if ver_res.success:
                    version_str = ver_res.stdout.strip()

            # Check service status
            svc_res = self.executor.execute("systemctl is-active telegraf", timeout=5)
            service_state = svc_res.stdout.strip() if svc_res.stdout else "unknown"

            # Query host uuid and ip for unmanaged VCF mapping
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
            if not installed and self.options.mode == DeploymentMode.PUSH:
                res = StageResult(
                    stage=WorkflowStage.DETECT,
                    status=StageStatus.FAIL,
                    message=f"Telegraf not installed on {self.target.hostname}. Install Telegraf before push, or use script mode.",
                    details=f"{os_version} ({arch}), Service state: {service_state}",
                    duration_ms=dur,
                )
            else:
                msg = f"{os_version} ({arch}), Telegraf: {version_str or 'Not installed'}"
                status = StageStatus.PASS if installed else StageStatus.WARNING
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

            self.artifacts = self.adapter.prepare_telegraf_integration()
            if self.artifacts.token and self.artifacts.token not in self._secrets:
                self._secrets.append(self.artifacts.token)

            dur = int((time.monotonic() - start) * 1000)
            res = StageResult(
                stage=WorkflowStage.PREPARE_VCF,
                status=StageStatus.PASS,
                message=f"VCF Ops validated, collector: {self.artifacts.collector_address}",
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
            self.vcf_conf_content = TelegrafRenderer.render_vcf_output(
                collector_address=collector_addr,
                hostname=self.target.hostname,
                uuid=uuid_val,
                ip=ip_val,
                verify_ssl=self.env.verify_ssl,
                ca_cert_path=self.env.ca_cert_path or "/etc/telegraf/telegraf.d/ca.pem",
            )

            # Validate syntax locally
            v1 = Validator.validate_toml_syntax(self.system_conf_content, "System Inputs")
            v2 = Validator.validate_toml_syntax(self.vcf_conf_content, "VCF Output")

            dur = int((time.monotonic() - start) * 1000)
            if v1.is_valid and v2.is_valid:
                res = StageResult(
                    stage=WorkflowStage.RENDER_INPUTS,
                    status=StageStatus.PASS,
                    message="Rendered vcf-helper-system.conf and cloudproxy-http.conf",
                    duration_ms=dur,
                )
            else:
                err_msg = v1.message if not v1.is_valid else v2.message
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
            if not self.options.skip_collector_check and self.options.mode == DeploymentMode.PUSH:
                cp_val = Validator.validate_collector_reachability(self.executor, collector_addr)
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
            config_dir = (
                self.discovery.config_dir
                if self.discovery
                else "/etc/telegraf/telegraf.d"
            )
            system_file = f"{config_dir}/vcf-helper-system.conf"
            vcf_file = f"{config_dir}/cloudproxy-http.conf"

            # Create destination directory
            self.executor.execute(f"mkdir -p {config_dir}")

            # Create backup copies of existing managed fragments before overwriting
            if self.executor.file_exists(system_file):
                self.executor.execute(f"cp {system_file} {system_file}.bak")
            if self.executor.file_exists(vcf_file):
                self.executor.execute(f"cp {vcf_file} {vcf_file}.bak")

            # Upload managed fragments
            self.executor.upload(self.system_conf_content, system_file)
            self.executor.upload(self.vcf_conf_content, vcf_file)

            self.managed_files = [system_file, vcf_file]

            if hasattr(self.executor, "generate_deploy_script"):
                telegraf_bin = (
                    self.discovery.telegraf_bin_path
                    if self.discovery
                    else "/usr/bin/telegraf"
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
            telegraf_bin = (
                self.discovery.telegraf_bin_path
                if self.discovery
                else "/usr/bin/telegraf"
            )
            config_dir = (
                self.discovery.config_dir
                if self.discovery
                else "/etc/telegraf/telegraf.d"
            )
            main_cfg = (
                self.discovery.main_config_path
                if self.discovery
                else "/etc/telegraf/telegraf.conf"
            )

            # Pre-flight check on endpoint before service restart
            test_res = self.executor.execute(
                f"{telegraf_bin} --test --config {main_cfg} --config-directory {config_dir}",
                timeout=15,
            )
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
                svc_val = Validator.validate_service_state(self.executor)
                self.verifications["Service running"] = "PASS" if svc_val.is_valid else "FAIL"

                # 4. Local metrics generated
                telegraf_bin = (
                    self.discovery.telegraf_bin_path
                    if self.discovery
                    else "/usr/bin/telegraf"
                )
                config_dir = (
                    self.discovery.config_dir
                    if self.discovery
                    else "/etc/telegraf/telegraf.d"
                )
                main_cfg = (
                    self.discovery.main_config_path
                    if self.discovery
                    else "/etc/telegraf/telegraf.conf"
                )
                test_val = Validator.validate_telegraf_config_on_endpoint(
                    self.executor, telegraf_bin, main_cfg, config_dir
                )
                self.verifications["Local metrics generated"] = (
                    "PASS" if test_val.is_valid else "FAIL"
                )

                # 5. Ingestion in VCF Ops
                ingestion_status = self.adapter.verify_ingestion(self.target.hostname)
                self.verifications["VCF Ops ingestion"] = ingestion_status
            else:
                self.verifications["Service running"] = "SKIPPED"
                self.verifications["Local metrics generated"] = "SKIPPED"
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
