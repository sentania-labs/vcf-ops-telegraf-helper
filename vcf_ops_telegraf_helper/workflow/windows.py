"""Windows Telegraf agent detection logic."""

from __future__ import annotations

import re
from typing import Optional

from vcf_ops_telegraf_helper.executors.base import EndpointExecutor
from vcf_ops_telegraf_helper.logger import get_logger

logger = get_logger("workflow.windows")


class WindowsTelegrafDetection:
    """Discovered Telegraf details on a Windows endpoint."""

    def __init__(
        self,
        installed: bool = False,
        binary_path: Optional[str] = None,
        service_name: Optional[str] = None,
        version: Optional[str] = None,
        service_state: Optional[str] = None,
        running: bool = False,
    ):
        self.installed = installed
        self.binary_path = binary_path
        self.service_name = service_name
        self.version = version
        self.service_state = service_state
        self.running = running

    def __iter__(self):
        """Enable unpacking as (binary_path, service_name, version)."""
        return iter((self.binary_path, self.service_name, self.version))

    def __repr__(self) -> str:
        return (
            f"WindowsTelegrafDetection(installed={self.installed}, "
            f"binary_path={self.binary_path!r}, "
            f"service_name={self.service_name!r}, "
            f"version={self.version!r}, "
            f"service_state={self.service_state!r}, "
            f"running={self.running})"
        )


def _extract_binary_path(raw_path: str) -> str:
    """Extract executable path from a Windows service command line or PathName."""
    raw = raw_path.strip()
    if not raw:
        return ""
    if raw.startswith('"'):
        end_q = raw.find('"', 1)
        if end_q != -1:
            return raw[1:end_q].strip()
    if raw.startswith("'"):
        end_q = raw.find("'", 1)
        if end_q != -1:
            return raw[1:end_q].strip()
    match = re.search(r"^(.*?\.exe)\b", raw, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return raw.split()[0]


def _is_valid_stdout(res) -> bool:
    """Verify execution returned valid stdout string and exit code 0."""
    return (
        getattr(res, "success", False) is True
        and isinstance(getattr(res, "stdout", None), str)
        and bool(res.stdout.strip())
    )


def detect_windows_telegraf(executor: EndpointExecutor) -> WindowsTelegrafDetection:
    """Detect Telegraf installation on a Windows endpoint.

    Searches in order, stopping at the first hit:
    1. Services whose binary path mentions telegraf (Get-CimInstance Win32_Service)
    2. A running process named telegraf (Get-Process telegraf)
    3. PATH command lookup (Get-Command telegraf.exe)
    4. Registry ImagePath for service 'telegraf'
    5. Known folders (C:\\Program Files\\Telegraf\\telegraf.exe, then C:\\telegraf\\telegraf.exe)
    """
    logger.debug("Starting Windows Telegraf detection")

    def _query_version(bin_path: str) -> Optional[str]:
        v_res = executor.execute(f"& '{bin_path}' version", timeout=10)
        if _is_valid_stdout(v_res):
            return v_res.stdout.strip().splitlines()[0].strip()
        v_res2 = executor.execute(f"\"{bin_path}\" version", timeout=10)
        if _is_valid_stdout(v_res2):
            return v_res2.stdout.strip().splitlines()[0].strip()
        return None

    def _query_service_status(svc_name: str) -> tuple[Optional[str], bool]:
        safe_name = svc_name.replace("'", "''")
        res = executor.execute(f"(Get-Service '{safe_name}' -ErrorAction SilentlyContinue).Status", timeout=5)
        if _is_valid_stdout(res):
            st = res.stdout.strip().splitlines()[-1].strip()
            return st, "running" in st.lower()
        sc_res = executor.execute(f"sc.exe query '{safe_name}'", timeout=5)
        if _is_valid_stdout(sc_res):
            running = "RUNNING" in sc_res.stdout.upper()
            return "Running" if running else "Stopped", running
        return "Stopped", False

    # 1. Services whose binary path mentions telegraf
    svc_cmd = (
        "Get-CimInstance Win32_Service | Where-Object { $_.PathName -match 'telegraf' } "
        "| Select-Object -First 1 | ForEach-Object { '{0}|{1}|{2}' -f $_.Name, $_.State, $_.PathName }"
    )
    res = executor.execute(svc_cmd, timeout=10)
    if _is_valid_stdout(res):
        line = res.stdout.strip().splitlines()[-1].strip()
        parts = line.split("|", 2)
        if len(parts) == 3:
            s_name, s_state, raw_p = parts[0].strip(), parts[1].strip(), parts[2].strip()
            b_path = _extract_binary_path(raw_p)
            if b_path:
                is_running = "running" in s_state.lower()
                ver = _query_version(b_path)
                logger.info("Found Windows Telegraf via Win32_Service: %s (path: %s)", s_name, b_path)
                return WindowsTelegrafDetection(
                    installed=True,
                    binary_path=b_path,
                    service_name=s_name,
                    version=ver,
                    service_state=s_state,
                    running=is_running,
                )

    # 2. Running process named telegraf
    proc_cmd = "Get-Process telegraf -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Path"
    res = executor.execute(proc_cmd, timeout=10)
    if _is_valid_stdout(res):
        b_path = res.stdout.strip().splitlines()[-1].strip()
        if b_path:
            ver = _query_version(b_path)
            s_state, is_running = _query_service_status("telegraf")
            logger.info("Found Windows Telegraf via running process: %s", b_path)
            return WindowsTelegrafDetection(
                installed=True,
                binary_path=b_path,
                service_name="telegraf",
                version=ver,
                service_state="Running",
                running=True,
            )

    # 3. PATH
    path_cmd = "(Get-Command telegraf.exe -ErrorAction SilentlyContinue).Source"
    res = executor.execute(path_cmd, timeout=10)
    if _is_valid_stdout(res):
        b_path = res.stdout.strip().splitlines()[-1].strip()
        if b_path:
            ver = _query_version(b_path)
            s_state, is_running = _query_service_status("telegraf")
            logger.info("Found Windows Telegraf in PATH: %s", b_path)
            return WindowsTelegrafDetection(
                installed=True,
                binary_path=b_path,
                service_name="telegraf",
                version=ver,
                service_state=s_state or "Stopped",
                running=is_running,
            )

    # 4. Registry ImagePath for service 'telegraf'
    reg_cmd = "(Get-ItemProperty -Path 'HKLM:\\SYSTEM\\CurrentControlSet\\Services\\telegraf' -ErrorAction SilentlyContinue).ImagePath"
    res = executor.execute(reg_cmd, timeout=10)
    if _is_valid_stdout(res):
        raw_p = res.stdout.strip().splitlines()[-1].strip()
        b_path = _extract_binary_path(raw_p)
        if b_path:
            ver = _query_version(b_path)
            s_state, is_running = _query_service_status("telegraf")
            logger.info("Found Windows Telegraf via registry ImagePath: %s", b_path)
            return WindowsTelegrafDetection(
                installed=True,
                binary_path=b_path,
                service_name="telegraf",
                version=ver,
                service_state=s_state or "Stopped",
                running=is_running,
            )

    # 5. Known folders, C:\telegraf last
    known_candidates = [
        "C:\\Program Files\\Telegraf\\telegraf.exe",
        "C:\\telegraf\\telegraf.exe",
    ]
    for p in known_candidates:
        exists = False
        try:
            fe_fn = getattr(executor, "file_exists", None)
            if callable(fe_fn):
                fe_res = fe_fn(p)
                if isinstance(fe_res, bool):
                    exists = fe_res
        except Exception:
            pass
        if not exists:
            safe_p = p.replace("'", "''")
            t_res = executor.execute(f"Test-Path -Path '{safe_p}'", timeout=5)
            if _is_valid_stdout(t_res) and "True" in t_res.stdout:
                exists = True

        if exists:
            ver = _query_version(p)
            s_state, is_running = _query_service_status("telegraf")
            logger.info("Found Windows Telegraf at known folder: %s", p)
            return WindowsTelegrafDetection(
                installed=True,
                binary_path=p,
                service_name="telegraf",
                version=ver,
                service_state=s_state or "Stopped",
                running=is_running,
            )

    logger.info("No Windows Telegraf installation detected")
    return WindowsTelegrafDetection(
        installed=False,
        binary_path=None,
        service_name=None,
        version=None,
        service_state="Stopped",
        running=False,
    )
