"""Workflow domain models and run summary reporting."""

from __future__ import annotations

import json
from enum import Enum
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field

from vcf_ops_telegraf_helper.utils import local_now_formatted


class DeploymentMode(str, Enum):
    """Supported deployment modes."""

    PUSH = "push"
    SCRIPT = "script"
    CONFIG_ONLY = "config_only"


class WorkflowStage(str, Enum):
    """Explicit sequential workflow stages."""

    CONNECT = "1/8 Connecting"
    DETECT = "2/8 Detecting Telegraf"
    PREPARE_VCF = "3/8 Preparing VCF Ops integration"
    RENDER_INPUTS = "4/8 Rendering inputs"
    VALIDATE = "5/8 Validating config"
    APPLY = "6/8 Applying config"
    RESTART = "7/8 Restarting Telegraf"
    VERIFY = "8/8 Verifying"


class UninstallStage(str, Enum):
    """Explicit sequential stages for endpoint uninstallation."""

    CONNECT = "1/5 Connecting"
    STOP_SERVICE = "2/5 Stopping Telegraf service"
    REMOVE_CONFIG = "3/5 Removing configuration and certificates"
    REMOVE_PACKAGE = "4/5 Purging agent package and repositories"
    VERIFY = "5/5 Verifying clean endpoint state"


class StageStatus(str, Enum):
    """Status outcome for a workflow stage."""

    PASS = "PASS"
    FAIL = "FAIL"
    WARNING = "WARNING"
    SKIPPED = "SKIPPED"


class StageResult(BaseModel):
    """Result of a single workflow stage."""

    stage: Union[WorkflowStage, UninstallStage, str]
    status: StageStatus
    message: str
    details: Optional[str] = None
    command_output: Optional[str] = None
    duration_ms: int = 0


class UninstallOptions(BaseModel):
    """Operational parameters for endpoint uninstallation."""

    purge_packages: bool = True
    purge_repositories: bool = True


class UninstallSummary(BaseModel):
    """Execution summary of uninstallation workflow."""

    target_hostname: str
    success: bool
    stages: List[StageResult] = Field(default_factory=list)
    verifications: Dict[str, str] = Field(default_factory=dict)
    purged_paths: List[str] = Field(default_factory=list)


class WorkflowOptions(BaseModel):
    """Operational parameters for a workflow execution."""

    mode: DeploymentMode = DeploymentMode.PUSH
    dry_run: bool = False
    output_dir: Optional[str] = None
    restart_service: bool = True
    skip_collector_check: bool = False
    preview_only: bool = False
    install_telegraf: bool = False
    telegraf_version: Optional[str] = None


class RunSummary(BaseModel):
    """Structured summary of a workflow run."""

    timestamp: str = Field(default_factory=local_now_formatted)
    target_hostname: str
    vcf_environment: str
    collector_address: str
    deployment_mode: str = "push"
    success: bool
    stages: List[StageResult] = Field(default_factory=list)
    verifications: Dict[str, str] = Field(default_factory=dict)
    managed_files: List[str] = Field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert summary to dictionary representation."""
        return self.model_dump()

    def to_json(self, indent: int = 2) -> str:
        """Serialize run summary to JSON format."""
        return json.dumps(self.to_dict(), indent=indent)

    def to_markdown(self) -> str:
        """Format run summary as clean Markdown."""
        lines = [
            "# VCF Operations Open Telegraf Helper: Run Summary",
            "",
            f"**Timestamp (Local):** {self.timestamp}  ",
            f"**Target Host:** `{self.target_hostname}`  ",
            f"**VCF Environment:** `{self.vcf_environment}`  ",
            f"**Collector Destination:** `{self.collector_address}`  ",
            f"**Deployment Mode:** `{self.deployment_mode}`  ",
            f"**Overall Status:** `{'SUCCESS' if self.success else 'FAILED'}`  ",
            "",
            "## Stage Execution",
            "",
            "| Stage | Status | Message | Duration |",
            "| --- | --- | --- | --- |",
        ]

        for s in self.stages:
            badge = f"**{s.status.value}**"
            lines.append(f"| {s.stage.value} | {badge} | {s.message} | {s.duration_ms} ms |")

        if self.verifications:
            lines.extend([
                "",
                "## Verification Checklist",
                "",
                "| Check | Status |",
                "| --- | --- |",
            ])
            for check, status in self.verifications.items():
                lines.append(f"| {check} | `{status}` |")

        if self.managed_files:
            lines.extend([
                "",
                "## Managed Configuration Files",
                "",
            ])
            for mf in self.managed_files:
                lines.append(f"* `{mf}`")

        lines.append("")
        return "\n".join(lines)
