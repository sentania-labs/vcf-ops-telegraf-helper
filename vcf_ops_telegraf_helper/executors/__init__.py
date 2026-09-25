"""Executors package."""

from vcf_ops_telegraf_helper.executors.base import CommandResult, EndpointExecutor
from vcf_ops_telegraf_helper.executors.local import LocalExecutor
from vcf_ops_telegraf_helper.executors.mock import MockExecutor
from vcf_ops_telegraf_helper.executors.package import PackageExecutor
from vcf_ops_telegraf_helper.executors.ssh import SSHExecutor
from vcf_ops_telegraf_helper.executors.winrm import WinRMExecutor

__all__ = [
    "CommandResult",
    "EndpointExecutor",
    "LocalExecutor",
    "MockExecutor",
    "PackageExecutor",
    "SSHExecutor",
    "WinRMExecutor",
]
