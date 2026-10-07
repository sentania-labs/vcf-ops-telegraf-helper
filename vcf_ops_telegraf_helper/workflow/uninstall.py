"""Endpoint uninstallation workflow.

Completely stops, disables, and purges Telegraf agent, configurations, certificates,
and package repositories from a target endpoint.
"""

from __future__ import annotations

import shlex
import time
from typing import List, Optional

from vcf_ops_telegraf_helper.executors.base import EndpointExecutor
from vcf_ops_telegraf_helper.models.endpoint import EndpointTarget, OSFamily
from vcf_ops_telegraf_helper.models.workflow import (
    StageResult,
    StageStatus,
    UninstallOptions,
    UninstallStage,
    UninstallSummary,
)
from vcf_ops_telegraf_helper.workflow.progress import ProgressReporter, SilentProgressReporter


class UninstallEndpointWorkflow:
    """Orchestrates safe uninstallation and teardown of Telegraf on a target endpoint."""

    def __init__(
        self,
        target: EndpointTarget,
        executor: EndpointExecutor,
        reporter: Optional[ProgressReporter] = None,
        options: Optional[UninstallOptions] = None,
    ):
        self.target = target
        self.executor = executor
        self.reporter = reporter or SilentProgressReporter()
        self.options = options or UninstallOptions()
        self.stage_results: List[StageResult] = []
        self.verifications: dict[str, str] = {}
        self.purged_paths: List[str] = []

    def connect(self) -> StageResult:
        """Stage 1: Establish connectivity with the remote endpoint."""
        start = time.monotonic()
        self.reporter.on_stage_start(UninstallStage.CONNECT)

        try:
            connected = self.executor.test_connection()
            dur = int((time.monotonic() - start) * 1000)

            if connected:
                res = StageResult(
                    stage=UninstallStage.CONNECT,
                    status=StageStatus.PASS,
                    message=f"Connected to {self.target.hostname} ({self.target.connection_method.value})",
                    duration_ms=dur,
                )
            else:
                res = StageResult(
                    stage=UninstallStage.CONNECT,
                    status=StageStatus.FAIL,
                    message=f"Failed to connect to {self.target.hostname}",
                    duration_ms=dur,
                )
        except Exception as e:
            dur = int((time.monotonic() - start) * 1000)
            res = StageResult(
                stage=UninstallStage.CONNECT,
                status=StageStatus.FAIL,
                message=f"Connection error: {e}",
                duration_ms=dur,
            )

        self.reporter.on_stage_complete(res)
        return res

    def stop_service(self) -> StageResult:
        """Stage 2: Stop and disable the Telegraf service."""
        start = time.monotonic()
        self.reporter.on_stage_start(UninstallStage.STOP_SERVICE)

        try:
            is_win = (self.target.os_family == OSFamily.WINDOWS) or (type(self.executor).__name__ == "WinRMExecutor")

            if is_win:
                check_svc = self.executor.execute("Get-Service -Name telegraf -ErrorAction SilentlyContinue", timeout=5)
                if check_svc.success and "telegraf" in check_svc.stdout.lower():
                    self.executor.execute("Stop-Service -Name telegraf -Force -ErrorAction SilentlyContinue", timeout=15)
                    self.executor.execute("& 'C:\\telegraf\\telegraf.exe' --service uninstall 2>$null", timeout=15)
                    self.executor.execute("sc.exe delete telegraf 2>$null", timeout=10)
                    msg = "Telegraf service stopped and unregistered"
                else:
                    msg = "Telegraf service was not installed (nothing to stop)"
            else:
                check_svc = self.executor.execute("systemctl list-unit-files telegraf.service 2>/dev/null", timeout=5)
                if check_svc.success and "telegraf.service" in check_svc.stdout:
                    self.executor.execute("systemctl stop telegraf 2>/dev/null || true", timeout=15)
                    self.executor.execute("systemctl disable telegraf 2>/dev/null || true", timeout=15)
                    msg = "Telegraf service stopped and disabled"
                else:
                    msg = "Telegraf service was not installed (nothing to stop)"

            dur = int((time.monotonic() - start) * 1000)
            res = StageResult(
                stage=UninstallStage.STOP_SERVICE,
                status=StageStatus.PASS,
                message=msg,
                duration_ms=dur,
            )
        except Exception as e:
            dur = int((time.monotonic() - start) * 1000)
            res = StageResult(
                stage=UninstallStage.STOP_SERVICE,
                status=StageStatus.WARNING,
                message=f"Service stop completed with warning: {e}",
                duration_ms=dur,
            )

        self.reporter.on_stage_complete(res)
        return res

    def remove_config(self) -> StageResult:
        """Stage 3: Remove Telegraf configurations, fragments, and certificates."""
        start = time.monotonic()
        self.reporter.on_stage_start(UninstallStage.REMOVE_CONFIG)

        try:
            is_win = (self.target.os_family == OSFamily.WINDOWS) or (type(self.executor).__name__ == "WinRMExecutor")

            if is_win:
                cfg_path = "C:\\telegraf"
                exists = self.executor.file_exists(cfg_path)
                if exists:
                    self.executor.execute(
                        f"Remove-Item -Path '{cfg_path}\\telegraf.d' -Recurse -Force -ErrorAction SilentlyContinue",
                        timeout=15,
                    )
                    self.executor.execute(
                        f"Remove-Item -Path '{cfg_path}\\*.conf*' -Force -ErrorAction SilentlyContinue",
                        timeout=15,
                    )
                    self.executor.execute(
                        f"Remove-Item -Path '{cfg_path}\\*.pem' -Force -ErrorAction SilentlyContinue",
                        timeout=15,
                    )
                    self.purged_paths.append("C:\\telegraf configuration and certs")
                    msg = f"Removed Telegraf configuration directory ({cfg_path})"
                else:
                    msg = f"Telegraf configuration directory was not present ({cfg_path})"
            else:
                cfg_path = "/etc/telegraf"
                check_dir = self.executor.execute(f"test -d {cfg_path}", timeout=5)
                if check_dir.success:
                    self.executor.execute(f"rm -rf {cfg_path}", timeout=15)
                    self.purged_paths.append(cfg_path)
                    msg = f"Removed Telegraf configuration directory ({cfg_path})"
                else:
                    msg = f"Telegraf configuration directory was not present ({cfg_path})"

            dur = int((time.monotonic() - start) * 1000)
            res = StageResult(
                stage=UninstallStage.REMOVE_CONFIG,
                status=StageStatus.PASS,
                message=msg,
                duration_ms=dur,
            )
        except Exception as e:
            dur = int((time.monotonic() - start) * 1000)
            res = StageResult(
                stage=UninstallStage.REMOVE_CONFIG,
                status=StageStatus.FAIL,
                message=f"Failed to remove configuration directory: {e}",
                duration_ms=dur,
            )

        self.reporter.on_stage_complete(res)
        return res

    def remove_package(self) -> StageResult:
        """Stage 4: Purge Telegraf package, binaries, repositories, and service unit files."""
        start = time.monotonic()
        self.reporter.on_stage_start(UninstallStage.REMOVE_PACKAGE)

        try:
            is_win = (self.target.os_family == OSFamily.WINDOWS) or (type(self.executor).__name__ == "WinRMExecutor")

            if is_win:
                if self.options.purge_packages:
                    self.executor.execute(
                        "Remove-Item -Path 'C:\\telegraf' -Recurse -Force -ErrorAction SilentlyContinue",
                        timeout=30,
                    )
                    self.purged_paths.append("C:\\telegraf directory")
            else:
                if self.options.purge_packages:
                    # Run the whole script under sudo, including env assignments and fallbacks.
                    script = """set -e
if command -v apt-get >/dev/null; then
    if dpkg-query -W telegraf >/dev/null 2>&1; then
        env DEBIAN_FRONTEND=noninteractive apt-get purge -y -qq telegraf
    fi
elif command -v dnf >/dev/null; then
    if rpm -q telegraf >/dev/null 2>&1; then dnf remove -y -q telegraf; fi
elif command -v yum >/dev/null; then
    if rpm -q telegraf >/dev/null 2>&1; then yum remove -y -q telegraf; fi
fi
rm -f /usr/bin/telegraf /usr/local/bin/telegraf /lib/systemd/system/telegraf.service /etc/systemd/system/telegraf.service
rm -rf /etc/default/telegraf /usr/lib/telegraf
if id telegraf >/dev/null 2>&1; then userdel telegraf; fi
if getent group telegraf >/dev/null; then groupdel telegraf; fi
systemctl daemon-reload
"""
                    if self.options.purge_repositories:
                        script += "rm -f /etc/apt/sources.list.d/influxdata.list* /etc/apt/trusted.gpg.d/influxdata* /etc/yum.repos.d/influxdata.repo\n"
                    result = self.executor.execute("bash -c " + shlex.quote(script), timeout=120)
                    if not result.success:
                        raise RuntimeError(result.stderr.strip() or "Package purge failed")
                    package = self.executor.execute(self._package_check(), timeout=10)
                    if not package.success or package.stdout.strip() != "ABSENT":
                        raise RuntimeError("Package manager still reports Telegraf, or absence could not be verified")
                    self.purged_paths.append("Telegraf package, binary, system account and requested repository files")

            dur = int((time.monotonic() - start) * 1000)
            res = StageResult(
                stage=UninstallStage.REMOVE_PACKAGE,
                status=StageStatus.PASS,
                message="Purged Telegraf package, binaries, and repository sources",
                duration_ms=dur,
            )
        except Exception as e:
            dur = int((time.monotonic() - start) * 1000)
            res = StageResult(
                stage=UninstallStage.REMOVE_PACKAGE,
                status=StageStatus.FAIL,
                message=f"Failed during package removal: {e}",
                duration_ms=dur,
            )

        self.reporter.on_stage_complete(res)
        return res

    @staticmethod
    def _package_check() -> str:
        return "bash -c " + shlex.quote("""if command -v dpkg-query >/dev/null; then
packages=$(dpkg-query -W -f='${binary:Package}\t${db:Status-Status}\n') || exit 1
if printf '%s\n' "$packages" | grep -Eq '^telegraf(:[^[:space:]]+)?[[:space:]]'; then echo PRESENT; else echo ABSENT; fi
elif command -v rpm >/dev/null; then
packages=$(rpm -qa --qf '%{NAME}\n') || exit 1
if printf '%s\n' "$packages" | grep -qx telegraf; then echo PRESENT; else echo ABSENT; fi
else echo ABSENT; fi""")

    def verify(self) -> StageResult:
        """Stage 5: Verify clean endpoint state (service inactive, binary absent, config removed)."""
        start = time.monotonic()
        self.reporter.on_stage_start(UninstallStage.VERIFY)

        try:
            is_win = (self.target.os_family == OSFamily.WINDOWS) or (type(self.executor).__name__ == "WinRMExecutor")

            if is_win:
                svc_res = self.executor.execute(
                    "if (Get-Service -Name telegraf -ErrorAction SilentlyContinue) { 'PRESENT' } else { 'ABSENT' }",
                    timeout=5,
                )
                svc_absent = (svc_res.stdout.strip() == "ABSENT") or (not svc_res.success)
                if self.options.purge_packages:
                    bin_absent = not self.executor.file_exists("C:\\telegraf\\telegraf.exe")
                    cfg_absent = not self.executor.file_exists("C:\\telegraf")
                else:
                    bin_absent = True
                    cfg_absent = not self.executor.file_exists("C:\\telegraf\\telegraf.d")
            else:
                svc_res = self.executor.execute("systemctl is-active telegraf 2>/dev/null", timeout=5)
                svc_absent = svc_res.stdout.strip() not in ("active", "activating")
                if self.options.purge_packages:
                    which_res = self.executor.execute("which telegraf 2>/dev/null", timeout=5)
                    bin_absent = not which_res.success or not which_res.stdout.strip()
                    cfg_absent = not self.executor.file_exists("/etc/telegraf")
                else:
                    bin_absent = True
                    cfg_absent = not self.executor.file_exists("/etc/telegraf/telegraf.d")

            self.verifications["Service inactive"] = "PASS" if svc_absent else "FAIL"
            if self.options.purge_packages:
                self.verifications["Binary absent"] = "PASS" if bin_absent else "FAIL"
            else:
                self.verifications["Binary absent"] = "SKIPPED (packages preserved)"
            self.verifications["Configuration absent"] = "PASS" if cfg_absent else "FAIL"

            package_absent = True
            if not is_win and self.options.purge_packages:
                package = self.executor.execute(self._package_check(), timeout=10)
                package_absent = package.success and package.stdout.strip() == "ABSENT"
                self.verifications["Package absent"] = "PASS" if package_absent else "FAIL"
            all_clean = svc_absent and bin_absent and cfg_absent and package_absent
            dur = int((time.monotonic() - start) * 1000)

            if all_clean:
                res = StageResult(
                    stage=UninstallStage.VERIFY,
                    status=StageStatus.PASS,
                    message="Endpoint verified clean: Telegraf is completely uninstalled",
                    duration_ms=dur,
                )
            else:
                issues = []
                if not svc_absent:
                    issues.append("Service still reported active")
                if not bin_absent:
                    issues.append("Binary still present")
                if not cfg_absent:
                    issues.append("Configuration directory still exists")
                res = StageResult(
                    stage=UninstallStage.VERIFY,
                    status=StageStatus.WARNING,
                    message=f"Teardown completed with leftover artifacts: {', '.join(issues)}",
                    duration_ms=dur,
                )
        except Exception as e:
            dur = int((time.monotonic() - start) * 1000)
            res = StageResult(
                stage=UninstallStage.VERIFY,
                status=StageStatus.WARNING,
                message=f"Verification check error: {e}",
                duration_ms=dur,
            )

        self.reporter.on_stage_complete(res)
        return res

    def run(self) -> UninstallSummary:
        """Execute the entire 5-stage uninstallation workflow sequentially."""
        stages = [
            self.connect,
            self.stop_service,
            self.remove_config,
            self.remove_package,
            self.verify,
        ]

        overall_success = True
        for stage_fn in stages:
            stage_res = stage_fn()
            self.stage_results.append(stage_res)
            if stage_res.status == StageStatus.FAIL:
                overall_success = False
                break

        return UninstallSummary(
            target_hostname=self.target.hostname,
            success=overall_success,
            stages=self.stage_results,
            verifications=self.verifications,
            purged_paths=self.purged_paths,
        )
