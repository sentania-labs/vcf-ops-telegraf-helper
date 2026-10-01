"""Package and script generation executor for manual or auditable deployment."""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional, Union

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
            stdout="",
            command=command,
        )

    def _normalize_rel_path(self, path: str) -> Path:
        clean = path.replace("\\", "/")
        if len(clean) >= 2 and clean[1] == ":":
            clean = clean[2:]
        clean = clean.lstrip("/")
        return Path(clean)

    def upload(self, source_content: Union[str, bytes], destination_path: str, mode: int = 0o644) -> None:
        dest = self.output_dir / self._normalize_rel_path(destination_path)
        dest.parent.mkdir(parents=True, exist_ok=True)

        if isinstance(source_content, str):
            dest.write_text(source_content, encoding="utf-8")
        else:
            dest.write_bytes(source_content)
        try:
            os.chmod(dest, mode)
        except OSError:
            pass
        self.staged_files.append(dest)

    def download(self, source_path: str) -> str:
        target = self.output_dir / self._normalize_rel_path(source_path)
        return target.read_text(encoding="utf-8")

    def file_exists(self, path: str) -> bool:
        return (self.output_dir / self._normalize_rel_path(path)).exists()

    def generate_deploy_script(self, is_windows: bool = False, telegraf_bin: Optional[str] = None) -> Path:
        """Create a self-contained deployment script inside the bundle."""
        if is_windows:
            bin_path = telegraf_bin or "C:\\telegraf\\telegraf.exe"
            script_path = self.output_dir / "deploy-telegraf.ps1"
            script_content = f"""# ------------------------------------------------------------------------------
# VCF Operations Open Telegraf Helper: Windows Standalone Deployment Script
# Generated for offline or change-controlled environments
# ------------------------------------------------------------------------------
$ErrorActionPreference = "Stop"

$scriptDir = $PSScriptRoot
$destDir = "C:\\telegraf"
$confDir = "C:\\telegraf\\telegraf.d"
$binPath = "{bin_path}"

Write-Host "==> Preparing Telegraf directories..."
if (-not (Test-Path $destDir)) {{ New-Item -ItemType Directory -Path $destDir -Force | Out-Null }}
if (-not (Test-Path $confDir)) {{ New-Item -ItemType Directory -Path $confDir -Force | Out-Null }}

# Backup base configuration
$mainConf = "$destDir\\telegraf.conf"
$hadMainConf = Test-Path $mainConf
if ($hadMainConf -and -not (Test-Path "$mainConf.orig")) {{
    if (-not (Select-String -Path $mainConf -Pattern "Managed by VCF Operations" -SimpleMatch -Quiet)) {{
        Copy-Item -Path $mainConf -Destination "$mainConf.orig" -Force
        Write-Host "  [+] Preserved unmanaged telegraf.conf as telegraf.conf.orig"
    }}
}}
if ($hadMainConf) {{
    Copy-Item -Path $mainConf -Destination "$mainConf.bak" -Force
}}

$filesToCopy = @(
    "vcf-helper-system.conf", "cloudproxy-http.conf",
    "ca.pem", "cert.pem", "key.pem",
    "master.pub", "IP", "MUTUAL_AUTHENTICATION",
    "mandatory_tags.bat"
)

# Backup existing files in confDir
$backedUpFiles = @{{}}
foreach ($f in $filesToCopy) {{
    $destFile = Join-Path $confDir $f
    if (Test-Path $destFile) {{
        Copy-Item -Path $destFile -Destination "$destFile.bak" -Force
        $backedUpFiles[$f] = $true
    }}
}}

function Invoke-Rollback {{
    Write-Warning "==> Rolling back configuration changes..."
    if (Test-Path "$mainConf.bak") {{
        Move-Item -Path "$mainConf.bak" -Destination $mainConf -Force
    }} elseif (-not (Test-Path "$mainConf.orig")) {{
        Remove-Item -Path $mainConf -Force -ErrorAction SilentlyContinue
    }}
    foreach ($f in $filesToCopy) {{
        $destFile = Join-Path $confDir $f
        if ($backedUpFiles.ContainsKey($f)) {{
            Move-Item -Path "$destFile.bak" -Destination $destFile -Force
        }} else {{
            Remove-Item -Path $destFile -Force -ErrorAction SilentlyContinue
        }}
    }}
    Remove-Item -Path "$confDir\\*.bak" -Force -ErrorAction SilentlyContinue
    Write-Host "  [+] Rollback complete."
}}

# Copy base stub if present
$srcBase = Join-Path $scriptDir "telegraf\\telegraf.conf"
if (-not (Test-Path $srcBase)) {{ $srcBase = Join-Path $scriptDir "telegraf.conf" }}
if (Test-Path $srcBase) {{
    Copy-Item -Path $srcBase -Destination $mainConf -Force
    Write-Host "  [+] Updated telegraf.conf base stub"
}}

# Copy managed configuration fragments and certificates
$srcConfDir = Join-Path $scriptDir "telegraf\\telegraf.d"
if (-not (Test-Path $srcConfDir)) {{ $srcConfDir = Join-Path $scriptDir "telegraf.d" }}
if (-not (Test-Path $srcConfDir)) {{ $srcConfDir = $scriptDir }}

foreach ($f in $filesToCopy) {{
    $sourceFile = Join-Path $srcConfDir $f
    if (-not (Test-Path $sourceFile)) {{ $sourceFile = Join-Path $scriptDir $f }}
    if (Test-Path $sourceFile) {{
        Copy-Item -Path $sourceFile -Destination "$confDir\\$f" -Force
        Write-Host "  [+] Copied $f"
    }}
}}

Write-Host "==> Validating configuration with Telegraf..."
if (Test-Path $binPath) {{
    $testOutput = & $binPath --test --config $mainConf --config-directory $confDir 2>&1
    if ($LASTEXITCODE -ne 0) {{
        Write-Host ($testOutput | Out-String)
        Write-Error "Telegraf configuration validation failed. Rolling back changes."
        Invoke-Rollback
        exit 1
    }}
    Write-Host "  [+] Configuration validated successfully."
}} else {{
    Write-Warning "Telegraf binary not found at $binPath. Skipping binary test."
}}

Write-Host "==> Restarting Telegraf service..."
$svc = Get-Service -Name telegraf -ErrorAction SilentlyContinue
if ($svc) {{
    try {{
        Restart-Service telegraf -Force
        $svc.Refresh()
        Write-Host "  [+] Telegraf service is $($svc.Status)."
    }} catch {{
        Write-Error "Failed to restart Telegraf service: $_. Rolling back changes."
        Invoke-Rollback
        Restart-Service telegraf -Force -ErrorAction SilentlyContinue
        exit 1
    }}
}} else {{
    Write-Warning "Telegraf service is not registered on this system."
}}

Remove-Item -Path "$mainConf.bak" -Force -ErrorAction SilentlyContinue
Remove-Item -Path "$confDir\\*.bak" -Force -ErrorAction SilentlyContinue
Write-Host "==> Deployment complete."
"""
            script_path.write_text(script_content, encoding="utf-8")
            return script_path

        # Linux deploy script
        bin_path = telegraf_bin or "/usr/bin/telegraf"
        script_path = self.output_dir / "deploy-telegraf.sh"
        script_content = f"""#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# VCF Operations Open Telegraf Helper: Linux Standalone Deployment Script
# Generated for offline or change-controlled environments
# ------------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
CONF_DIR="/etc/telegraf/telegraf.d"
MAIN_CONF="/etc/telegraf/telegraf.conf"
BIN_PATH="{bin_path}"

echo "==> Preparing Telegraf directories..."
mkdir -p "$CONF_DIR"

# Backup original configuration
HAD_MAIN_CONF=0
if [ -f "$MAIN_CONF" ]; then
    HAD_MAIN_CONF=1
    if [ ! -f "$MAIN_CONF.orig" ]; then
        if ! grep -q "Managed by VCF Operations" "$MAIN_CONF" 2>/dev/null; then
            cp "$MAIN_CONF" "$MAIN_CONF.orig"
            echo "  [+] Preserved unmanaged telegraf.conf as telegraf.conf.orig"
        fi
    fi
    cp "$MAIN_CONF" "$MAIN_CONF.bak"
fi

FILES_TO_COPY=(vcf-helper-system.conf cloudproxy-http.conf ca.pem cert.pem key.pem master.pub IP MUTUAL_AUTHENTICATION mandatory_tags.sh)

# Backup existing files in CONF_DIR
for f in "${{FILES_TO_COPY[@]}}"; do
    if [ -f "$CONF_DIR/$f" ]; then
        cp "$CONF_DIR/$f" "$CONF_DIR/$f.bak"
    fi
done

rollback() {{
    echo "==> Rolling back configuration changes..."
    if [ -f "$MAIN_CONF.bak" ]; then
        mv -f "$MAIN_CONF.bak" "$MAIN_CONF"
    elif [ "$HAD_MAIN_CONF" -eq 0 ] && [ ! -f "$MAIN_CONF.orig" ]; then
        rm -f "$MAIN_CONF"
    fi
    for f in "${{FILES_TO_COPY[@]}}"; do
        if [ -f "$CONF_DIR/$f.bak" ]; then
            mv -f "$CONF_DIR/$f.bak" "$CONF_DIR/$f"
        else
            rm -f "$CONF_DIR/$f"
        fi
    done
    rm -f "$CONF_DIR"/*.bak "$MAIN_CONF.bak" 2>/dev/null || true
    echo "  [+] Rollback complete."
}}

# Copy base stub if present
SRC_BASE="$SCRIPT_DIR/etc/telegraf/telegraf.conf"
if [ ! -f "$SRC_BASE" ]; then SRC_BASE="$SCRIPT_DIR/telegraf.conf"; fi
if [ -f "$SRC_BASE" ]; then
    cp "$SRC_BASE" "$MAIN_CONF"
    echo "  [+] Updated telegraf.conf base stub"
fi

# Copy managed configuration fragments and certificates
SRC_CONF_DIR="$SCRIPT_DIR/etc/telegraf/telegraf.d"
if [ ! -d "$SRC_CONF_DIR" ]; then SRC_CONF_DIR="$SCRIPT_DIR/telegraf.d"; fi
if [ ! -d "$SRC_CONF_DIR" ]; then SRC_CONF_DIR="$SCRIPT_DIR"; fi

for f in "${{FILES_TO_COPY[@]}}"; do
    if [ -f "$SRC_CONF_DIR/$f" ]; then
        cp "$SRC_CONF_DIR/$f" "$CONF_DIR/$f"
        echo "  [+] Copied $f"
    elif [ -f "$SCRIPT_DIR/$f" ]; then
        cp "$SCRIPT_DIR/$f" "$CONF_DIR/$f"
        echo "  [+] Copied $f"
    fi
done

# Secure permissions
chown -R root:telegraf /etc/telegraf 2>/dev/null || true
chmod 755 /etc/telegraf "$CONF_DIR" 2>/dev/null || true
chmod 644 "$MAIN_CONF" "$CONF_DIR"/*.conf "$CONF_DIR"/ca.pem "$CONF_DIR"/cert.pem "$CONF_DIR"/master.pub "$CONF_DIR"/IP "$CONF_DIR"/MUTUAL_AUTHENTICATION 2>/dev/null || true
if [ -f "$CONF_DIR/key.pem" ]; then
    chmod 640 "$CONF_DIR/key.pem" 2>/dev/null || true
fi
if [ -f "$CONF_DIR/mandatory_tags.sh" ]; then
    chmod 755 "$CONF_DIR/mandatory_tags.sh" 2>/dev/null || true
fi

echo "==> Validating configuration with Telegraf..."
if command -v "$BIN_PATH" >/dev/null 2>&1; then
    if ! "$BIN_PATH" --test --config "$MAIN_CONF" --config-directory "$CONF_DIR"; then
        echo "  [x] Telegraf configuration validation failed. Rolling back changes."
        rollback
        exit 1
    fi
    echo "  [+] Configuration validated successfully."
else
    echo "  [!] Warning: $BIN_PATH not found. Skipping binary test."
fi

echo "==> Restarting Telegraf service..."
if command -v systemctl >/dev/null 2>&1; then
    if ! systemctl restart telegraf; then
        echo "  [x] Failed to restart Telegraf service. Rolling back changes."
        rollback
        systemctl restart telegraf 2>/dev/null || true
        exit 1
    fi
    systemctl is-active telegraf && echo "  [+] Telegraf service is active."
fi

rm -f "$MAIN_CONF.bak" "$CONF_DIR"/*.bak 2>/dev/null || true
echo "==> Deployment complete."
"""
        script_path.write_text(script_content, encoding="utf-8")
        try:
            os.chmod(script_path, 0o755)
        except OSError:
            pass

        # Also maintain apply.sh as compatibility alias
        apply_path = self.output_dir / "apply.sh"
        apply_path.write_text(script_content, encoding="utf-8")
        try:
            os.chmod(apply_path, 0o755)
        except OSError:
            pass

        return script_path
