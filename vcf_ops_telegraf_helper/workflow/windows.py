"""Windows Telegraf agent detection logic."""

from __future__ import annotations

import ntpath
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
        command_line: str = "",
    ):
        self.installed = installed
        self.binary_path = binary_path
        self.service_name = service_name
        self.version = version
        self.service_state = service_state
        self.running = running
        directory = ntpath.dirname(binary_path or r"C:\telegraf\telegraf.exe")
        self.main_config_path = self._config_argument(command_line, "config") or ntpath.join(directory, "telegraf.conf")
        self.config_dir = self._config_argument(command_line, "config-directory") or ntpath.join(directory, "telegraf.d")

    @staticmethod
    def _config_argument(command_line: str, name: str) -> Optional[str]:
        pattern = r"(?:^|\s)--" + re.escape(name) + r"(?:=|\s+)(?:\"([^\"]+)\"|'([^']+)'|([^\s]+))"
        matches = list(re.finditer(pattern, command_line, re.IGNORECASE))
        if not matches:
            return None
        value = next(group for group in matches[-1].groups() if group is not None)
        if not ntpath.isabs(value) or "%" in value:
            raise ValueError(f"Service --{name} must resolve to an absolute path before configuration: {value}")
        return value

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
        if "\n" in bin_path or "\r" in bin_path or "\0" in bin_path:
            return None
        safe_path = bin_path.replace("'", "''")
        v_res = executor.execute(f"& '{safe_path}' version", timeout=10)
        if _is_valid_stdout(v_res):
            return v_res.stdout.strip().splitlines()[0].strip()
        safe_quoted = bin_path.replace('"', '`"')
        v_res2 = executor.execute(f'& "{safe_quoted}" version', timeout=10)
        if _is_valid_stdout(v_res2):
            return v_res2.stdout.strip().splitlines()[0].strip()
        return None

    def _query_service_status(svc_name: str) -> tuple[Optional[str], bool]:
        if "\n" in svc_name or "\r" in svc_name:
            return None, False
        safe_name_ps = svc_name.replace("'", "''")
        res = executor.execute(f"(Get-Service '{safe_name_ps}' -ErrorAction SilentlyContinue).Status", timeout=5)
        if _is_valid_stdout(res):
            st = res.stdout.strip().splitlines()[-1].strip()
            return st, "running" in st.lower()
        safe_name_cmd = svc_name.replace('"', "")
        sc_res = executor.execute(f'sc.exe query "{safe_name_cmd}"', timeout=5)
        if _is_valid_stdout(sc_res):
            running = "RUNNING" in sc_res.stdout.upper()
            return "Running" if running else "Stopped", running
        return None, False

    def _check_file_exists(path: str) -> bool:
        try:
            fe_fn = getattr(executor, "file_exists", None)
            if callable(fe_fn):
                fe_res = fe_fn(path)
                if fe_res is True:
                    return True
        except Exception:
            pass
        safe_p = path.replace("'", "''")
        t_res = executor.execute(f"Test-Path -Path '{safe_p}'", timeout=5)
        return _is_valid_stdout(t_res) and "True" in t_res.stdout

    # 1. Services whose binary path mentions telegraf
    svc_cmd = (
        "Get-CimInstance Win32_Service | Where-Object { $_.PathName -match 'telegraf' } "
        "| Sort-Object -Property @{Expression={if ($_.State -eq 'Running') {0} else {1}}} "
        "| ForEach-Object { '{0}|{1}|{2}' -f $_.Name, $_.State, ([Environment]::ExpandEnvironmentVariables($_.PathName)) }"
    )
    res = executor.execute(svc_cmd, timeout=10)
    if _is_valid_stdout(res):
        for line in res.stdout.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("|", 2)
            if len(parts) == 3:
                s_name, s_state, raw_p = parts[0].strip(), parts[1].strip(), parts[2].strip()
                b_path = _extract_binary_path(raw_p)
                # Verify that the binary actually ends with telegraf.exe
                if b_path and b_path.lower().endswith("telegraf.exe"):
                    # Check file existence if service is stopped
                    is_running = "running" in s_state.lower()
                    if is_running or _check_file_exists(b_path):
                        ver = _query_version(b_path)
                        logger.info("Found Windows Telegraf via Win32_Service: %s (path: %s)", s_name, b_path)
                        return WindowsTelegrafDetection(
                            installed=True,
                            binary_path=b_path,
                            service_name=s_name,
                            version=ver,
                            service_state=s_state,
                            running=is_running,
                            command_line=raw_p,
                        )

    # 2. Running process named telegraf
    proc_cmd = "Get-Process telegraf -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Path"
    res = executor.execute(proc_cmd, timeout=10)
    if _is_valid_stdout(res):
        b_path = res.stdout.strip().splitlines()[-1].strip()
        if b_path:
            ver = _query_version(b_path)
            s_state, is_running = _query_service_status("telegraf")
            svc_name = "telegraf" if s_state else None
            logger.info("Found Windows Telegraf via running process: %s (service: %s)", b_path, svc_name)
            return WindowsTelegrafDetection(
                installed=True,
                binary_path=b_path,
                service_name=svc_name,
                version=ver,
                service_state=s_state or "Standalone",
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
            svc_name = "telegraf" if s_state else None
            logger.info("Found Windows Telegraf in PATH: %s (service: %s)", b_path, svc_name)
            return WindowsTelegrafDetection(
                installed=True,
                binary_path=b_path,
                service_name=svc_name,
                version=ver,
                service_state=s_state or ("Running" if is_running else "Stopped"),
                running=is_running,
            )

    # 4. Registry ImagePath for service 'telegraf'
    reg_cmd = (
        "[System.Environment]::ExpandEnvironmentVariables("
        "(Get-ItemProperty -Path 'HKLM:\\SYSTEM\\CurrentControlSet\\Services\\telegraf' -ErrorAction SilentlyContinue).ImagePath"
        ")"
    )
    res = executor.execute(reg_cmd, timeout=10)
    if _is_valid_stdout(res):
        raw_p = res.stdout.strip().splitlines()[-1].strip()
        b_path = _extract_binary_path(raw_p)
        if b_path and b_path.lower().endswith("telegraf.exe"):
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
                command_line=raw_p,
            )

    # 5. Known folders, C:\telegraf last
    known_candidates = [
        "C:\\Program Files\\Telegraf\\telegraf.exe",
        "C:\\telegraf\\telegraf.exe",
    ]
    for p in known_candidates:
        if _check_file_exists(p):
            ver = _query_version(p)
            s_state, is_running = _query_service_status("telegraf")
            svc_name = "telegraf" if s_state else None
            logger.info("Found Windows Telegraf at known folder: %s (service: %s)", p, svc_name)
            return WindowsTelegrafDetection(
                installed=True,
                binary_path=p,
                service_name=svc_name,
                version=ver,
                service_state=s_state or ("Running" if is_running else "Stopped"),
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


# ---------------------------------------------------------------------------
# Ops product-managed agent (VCF Operations 9.1 footprint, verified in the lab on 2026-10-09)
# ---------------------------------------------------------------------------

MANAGED_SERVICE_NAMES = ("salt-minion", "ucp-minion", "ucp-telegraf")
MANAGED_ROOT = r"C:\VMware\UCP"
MANAGED_CERT_DIR = r"C:\ProgramData\VMware\UCP\certkeys"
MANAGED_TELEGRAF_DIR = MANAGED_ROOT + r"\ucp-telegraf"
MANAGED_TELEGRAF_CONF = MANAGED_TELEGRAF_DIR + r"\telegraf.conf"
MANAGED_TELEGRAF_D = MANAGED_TELEGRAF_DIR + r"\telegraf.d"
MANAGED_TAGS_SCRIPT = MANAGED_TELEGRAF_DIR + r"\mandatory_tags.bat"
MANAGED_GRAINS = MANAGED_ROOT + r"\salt\conf\grains"


class ManagedInstallation:
    """What an Ops product-managed agent looks like on one Windows endpoint.

    `present` is decided by the control services (salt-minion, ucp-minion, ucp-telegraf) or a
    telegraf service running from under C:\\VMware\\UCP. Directories alone are only cleanup scope:
    the Ops uninstall leaves an empty C:\\VMware\\UCP behind.
    """

    def __init__(self) -> None:
        self.services: dict[str, tuple[str, str]] = {}  # name -> (state, path)
        self.root_present: bool = False
        self.cert_dir_present: bool = False
        self.telegraf_conf: Optional[str] = None
        self.telegraf_d: dict[str, str] = {}
        self.mandatory_tags: Optional[str] = None
        self.grains: Optional[str] = None
        self.telegraf_version: Optional[str] = None
        self.read_errors: list[str] = []
        self.query_ok: bool = False  # the service query itself succeeded (an empty result is only trusted then)
        self.query_error: str = ""

    @property
    def present(self) -> bool:
        return bool(self.services)

    @property
    def running_services(self) -> list[str]:
        return [n for n, (state, _) in self.services.items() if "running" in state.lower()]

    @property
    def grain_values(self) -> dict[str, str]:
        """Flat key: value pairs from the salt grains file (vm_id, vc_id, arc_virtual_ip, ...)."""
        values: dict[str, str] = {}
        for line in (self.grains or "").splitlines():
            if ":" not in line or line.lstrip().startswith("#"):
                continue
            key, _, val = line.partition(":")
            key, val = key.strip(), val.strip().strip("'\"")
            if key and val and not key.startswith("-"):
                values[key] = val
        return values

    @property
    def cleanup_paths(self) -> list[str]:
        paths = []
        if self.root_present:
            paths.append(MANAGED_ROOT)
        if self.cert_dir_present:
            paths.append(MANAGED_CERT_DIR)
        return paths

    def summary(self) -> str:
        if not self.present:
            return "No Ops-managed agent"
        svc = ", ".join(f"{n} ({st})" for n, (st, _) in sorted(self.services.items()))
        ver = f", {self.telegraf_version}" if self.telegraf_version else ""
        return f"Ops-managed agent: services {svc}{ver}"

    def __repr__(self) -> str:
        return f"ManagedInstallation(services={sorted(self.services)}, root={self.root_present}, certs={self.cert_dir_present})"


def is_managed_detection(found: WindowsTelegrafDetection) -> bool:
    """True when the telegraf found by detect_windows_telegraf is the Ops-managed ucp-telegraf."""
    if (found.service_name or "").lower() == "ucp-telegraf":
        return True
    return "\\vmware\\ucp\\" in (found.binary_path or "").lower().replace("/", "\\")


def detect_managed_installation(
    executor: EndpointExecutor,
    telegraf: Optional[WindowsTelegrafDetection] = None,
    read_config: bool = True,
) -> ManagedInstallation:
    """Inspect a Windows endpoint for the Ops product-managed agent.

    Services are read from the service control manager by name and by binary path. With
    read_config the managed telegraf.conf, telegraf.d fragments, mandatory_tags.bat and the salt
    grains are downloaded (text only, never the key files) so they can be ported and backed up.
    """
    found = ManagedInstallation()
    names = "', '".join(MANAGED_SERVICE_NAMES)
    svc_cmd = (
        f"Get-CimInstance Win32_Service | Where-Object {{ ($_.Name -in @('{names}')) -or ($_.PathName -like '*\\VMware\\UCP\\*') }} "
        "| ForEach-Object { '{0}|{1}|{2}' -f $_.Name, $_.State, $_.PathName }"
    )
    res = executor.execute(svc_cmd, timeout=15)
    found.query_ok = bool(getattr(res, "success", False)) and isinstance(getattr(res, "stdout", None), str)
    if not found.query_ok:
        found.query_error = (getattr(res, "stderr", "") or getattr(res, "stdout", "") or "no response").strip()
    if _is_valid_stdout(res):
        for line in res.stdout.strip().splitlines():
            parts = line.strip().split("|", 2)
            if len(parts) != 3 or not parts[0].strip():
                continue
            name, state, path = (part.strip() for part in parts)
            under_ucp = "\\vmware\\ucp\\" in path.lower().replace("/", "\\")
            # ucp-* names belong to Ops. salt-minion is also what a stock SaltStack install is called,
            # so it only counts when it runs from the managed root (C:\VMware\UCP\salt\nssm.exe).
            if under_ucp or name.lower().startswith("ucp-"):
                found.services[name] = (state, path)
    if telegraf is not None and is_managed_detection(telegraf):
        found.services.setdefault(telegraf.service_name or "ucp-telegraf", (telegraf.service_state or "Unknown", telegraf.binary_path or ""))
        found.telegraf_version = telegraf.version

    if not read_config:
        # Presence is all the caller wants; skip the extra round trips.
        return found

    # Remnant directories are cleanup scope: the Ops uninstall leaves an empty C:\VMware\UCP behind.
    found.root_present = _managed_path_exists(executor, MANAGED_ROOT)
    found.cert_dir_present = _managed_path_exists(executor, MANAGED_CERT_DIR)
    if not found.present:
        return found
    if found.telegraf_version is None:
        ver = executor.execute(f"& '{MANAGED_TELEGRAF_DIR}\\telegraf.exe' version", timeout=10)
        if _is_valid_stdout(ver):
            found.telegraf_version = ver.stdout.strip().splitlines()[0].strip()

    def _read(path: str) -> Optional[str]:
        try:
            if not executor.file_exists(path):
                return None
            return executor.download(path)
        except Exception as exc:
            found.read_errors.append(f"{path}: {exc}")
            return None

    found.telegraf_conf = _read(MANAGED_TELEGRAF_CONF)
    found.mandatory_tags = _read(MANAGED_TAGS_SCRIPT)
    found.grains = _read(MANAGED_GRAINS)
    listing = executor.execute(
        f"Get-ChildItem -Path '{MANAGED_TELEGRAF_D}' -File -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name",
        timeout=10,
    )
    if _is_valid_stdout(listing):
        for name in listing.stdout.strip().splitlines():
            name = name.strip()
            if name:
                content = _read(f"{MANAGED_TELEGRAF_D}\\{name}")
                if content is not None:
                    found.telegraf_d[name] = content
    logger.info("Managed agent inspection: %r, %d telegraf.d fragments", found, len(found.telegraf_d))
    return found


def _managed_path_exists(executor: EndpointExecutor, path: str) -> bool:
    # The managed directories are only ever consulted by exact path, so avoid the generic
    # file_exists shortcut and ask the endpoint directly.
    res = executor.execute(f"Test-Path -Path '{path}'", timeout=10)
    return _is_valid_stdout(res) and res.stdout.strip().splitlines()[-1].strip().lower() == "true"


def set_windows_tags_binary(script: str, telegraf_bin: str) -> str:
    """Point a mandatory_tags.bat at this endpoint's telegraf.exe through its default path line.

    Both the helper's script and the vendor's set TELEGRAF_BIN_PATH on one line and only override
    it when an argument is passed. The helper runs the script with no argument (issue #62), so the
    default line carries the quoted binary path.
    """
    quoted = '"' + telegraf_bin.replace("/", "\\").strip('"') + '"'
    new_line = f"set TELEGRAF_BIN_PATH={quoted}"
    pattern = re.compile(r"^([ \t]*)set[ \t]+TELEGRAF_BIN_PATH=[^\r\n]*", re.IGNORECASE | re.MULTILINE)
    replaced = False

    def _sub(match: "re.Match[str]") -> str:
        nonlocal replaced
        if replaced:
            return match.group(0)
        replaced = True
        return match.group(1) + new_line
    patched = pattern.sub(_sub, script, count=1)
    if not replaced:
        newline = "\r\n" if "\r\n" in script else "\n"
        lines = script.split(newline)
        insert_at = 1 if lines and lines[0].strip().lower().startswith("@echo") else 0
        lines.insert(insert_at, new_line)
        patched = newline.join(lines)
    return patched
