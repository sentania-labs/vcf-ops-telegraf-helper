"""Package and script generation executor for manual or auditable deployment."""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Union

from vcf_ops_telegraf_helper.executors.base import CommandResult, EndpointExecutor


class PackageExecutor(EndpointExecutor):
    """Generates auditable scripts and configuration bundles without remote execution."""

    def __init__(self, output_dir: str = "./vcf-telegraf-bundle"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.staged_files: List[Path] = []
        self.planned_commands: List[str] = []

    def test_connection(self) -> bool:
        return True

    def execute(self, command: str, timeout: int = 30) -> CommandResult:
        self.planned_commands.append(command)
        return CommandResult(
            exit_code=0,
            stdout="[STAGED FOR MANUAL SCRIPT EXECUTION]\n",
            command=command,
        )

    def upload(self, source_content: Union[str, bytes], destination_path: str, mode: int = 0o644) -> None:
        rel_path = destination_path.lstrip("/")
        dest = self.output_dir / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)

        if isinstance(source_content, str):
            dest.write_text(source_content, encoding="utf-8")
        else:
            dest.write_bytes(source_content)
        os.chmod(dest, mode)
        self.staged_files.append(dest)

    def download(self, source_path: str) -> str:
        rel_path = source_path.lstrip("/")
        target = self.output_dir / rel_path
        return target.read_text(encoding="utf-8")

    def file_exists(self, path: str) -> bool:
        rel_path = path.lstrip("/")
        return (self.output_dir / rel_path).exists()

    def generate_deploy_script(self, telegraf_bin: str = "/usr/bin/telegraf") -> Path:
        """Create a self-contained apply.sh script inside the bundle."""
        script_path = self.output_dir / "apply.sh"
        script_content = f"""#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# VCF Operations Open Telegraf Helper: Manual Deployment Script
# Generated for auditable offline deployment
# ------------------------------------------------------------------------------
set -euo pipefail

echo "==> Deploying managed Telegraf configuration fragments..."
mkdir -p /etc/telegraf/telegraf.d

if [ -f "$(dirname "$0")/etc/telegraf/telegraf.d/vcf-helper-system.conf" ]; then
    cp "$(dirname "$0")/etc/telegraf/telegraf.d/vcf-helper-system.conf" /etc/telegraf/telegraf.d/
    echo "  [+] Copied vcf-helper-system.conf"
fi

if [ -f "$(dirname "$0")/etc/telegraf/telegraf.d/cloudproxy-http.conf" ]; then
    cp "$(dirname "$0")/etc/telegraf/telegraf.d/cloudproxy-http.conf" /etc/telegraf/telegraf.d/
    echo "  [+] Copied cloudproxy-http.conf"
fi

echo "==> Validating configuration with Telegraf..."
if command -v {telegraf_bin} >/dev/null 2>&1; then
    if ! {telegraf_bin} --test --config /etc/telegraf/telegraf.conf --config-directory /etc/telegraf/telegraf.d; then
        echo "  [x] Telegraf configuration validation failed. Aborting service restart."
        exit 1
    fi
    echo "  [+] Configuration validated successfully."
else
    echo "  [!] Warning: {telegraf_bin} not found. Skipping binary test."
fi

echo "==> Restarting Telegraf service..."
if command -v systemctl >/dev/null 2>&1; then
    systemctl restart telegraf
    systemctl is-active telegraf && echo "  [+] Telegraf service is active."
fi

echo "==> Deployment complete."
"""
        script_path.write_text(script_content, encoding="utf-8")
        os.chmod(script_path, 0o755)
        return script_path
