"""Workflow package."""

from vcf_ops_telegraf_helper.workflow.engine import ConfigureEndpointWorkflow
from vcf_ops_telegraf_helper.workflow.progress import ProgressReporter, SilentProgressReporter
from vcf_ops_telegraf_helper.workflow.uninstall import UninstallEndpointWorkflow
from vcf_ops_telegraf_helper.workflow.windows import (
    WindowsTelegrafDetection,
    detect_windows_telegraf,
)

__all__ = [
    "ConfigureEndpointWorkflow",
    "UninstallEndpointWorkflow",
    "ProgressReporter",
    "SilentProgressReporter",
    "WindowsTelegrafDetection",
    "detect_windows_telegraf",
]
