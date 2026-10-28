"""Shared managed-agent layout, capture and in-memory decryption."""
from __future__ import annotations

import base64
import shlex
from dataclasses import dataclass
from typing import Optional

from vcf_ops_telegraf_helper.logger import get_logger
from vcf_ops_telegraf_helper.workflow.managed_encryption import decrypt_managed_config

logger = get_logger("workflow.managed")


@dataclass(frozen=True)
class ManagedPaths:
    root: str
    data_root: str
    conf: str
    fragments: str
    tags: str
    grains: str
    is_windows: bool = False

    def join(self, root, suffix):
        return root + ("\\" if self.is_windows else "/") + suffix.replace("/", "\\" if self.is_windows else "/")

    @property
    def cert_dir(self):
        return self.join(self.data_root, "certkeys")

    @property
    def key(self):
        return self.join(self.cert_dir, "key.pem")

    @property
    def encrypted_conf(self):
        return self.join(self.root, "plugins/telegraf.conf-encrypted")

    @property
    def encrypted_plugins(self):
        return self.join(self.root, "plugins/telegraf-encrypted")


WINDOWS_PATHS = ManagedPaths(
    r"C:\VMware\UCP", r"C:\ProgramData\VMware\UCP", r"C:\VMware\UCP\ucp-telegraf\telegraf.conf",
    r"C:\VMware\UCP\ucp-telegraf\telegraf.d", r"C:\VMware\UCP\ucp-telegraf\mandatory_tags.bat",
    r"C:\VMware\UCP\salt\conf\grains", True,
)
LINUX_PATHS = ManagedPaths(
    "/opt/vmware/ucp", "/opt/vmware/ucp", "/opt/vmware/ucp/ucp-telegraf/etc/telegraf/telegraf.conf",
    "/opt/vmware/ucp/ucp-telegraf/etc/telegraf/telegraf.d", "/opt/vmware/ucp/ucp-telegraf/mandatory_tags.sh",
    "/opt/vmware/ucp/salt-minion/etc/salt/grains",
)


class ManagedInstallation:
    """What an Ops product-managed agent looks like on one endpoint.

    `present` is decided by the control services (salt-minion, ucp-salt-minion, ucp-minion, ucp-telegraf) or a
    service running from the managed root. Directories alone are only cleanup scope:
    the Ops uninstall can leave an empty root behind.
    """

    def __init__(self, paths=None) -> None:
        self.paths = paths or WINDOWS_PATHS
        self.extra_files: dict[str, str] = {}
        self.services: dict[str, tuple[str, str]] = {}  # name -> (state, path)
        self.root_present: bool = False
        self.cert_dir_present: bool = False
        self.data_root_present: bool = False
        self.plugins_config: Optional[str] = None
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
            paths.append(self.paths.root)
        if self.data_root_present and self.paths.data_root != self.paths.root:
            paths.append(self.paths.data_root)
        elif self.cert_dir_present and not (
            self.root_present and self.paths.cert_dir.startswith(self.paths.join(self.paths.root, ""))
        ):
            paths.append(self.paths.cert_dir)
        return paths

    def summary(self) -> str:
        if not self.present:
            return "No Ops-managed agent"
        svc = ", ".join(f"{n} ({st})" for n, (st, _) in sorted(self.services.items()))
        ver = f", {self.telegraf_version}" if self.telegraf_version else ""
        return f"Ops-managed agent: services {svc}{ver}"

    def __repr__(self) -> str:
        return f"ManagedInstallation(services={sorted(self.services)}, root={self.root_present}, certs={self.cert_dir_present})"



def read_managed_config(executor, found):
    paths = found.paths

    def _read(path: str, required: bool = False) -> Optional[str]:
        try:
            if not executor.file_exists(path):
                if required:
                    found.read_errors.append(f"The file {path} is missing. Restore it and try again.")
                return None
            return executor.download(path)
        except Exception as exc:
            found.read_errors.append(f"{path}: {exc}")
            return None

    def _binary(path: str) -> bytes:
        if not paths.is_windows:
            result = executor.execute("bash -c " + shlex.quote("base64 -- " + shlex.quote(path)), timeout=30)
            if not result.success:
                raise ValueError(f"Could not read {path}")
            return base64.b64decode("".join(result.stdout.split()), validate=True)
        safe_path = path.replace("'", "''")
        result = executor.execute(f"[Convert]::ToBase64String([IO.File]::ReadAllBytes('{safe_path}'))", timeout=30)
        if not result.success:
            raise ValueError(f"Could not read {path}: {result.stderr or result.stdout}")
        return base64.b64decode("".join(result.stdout.split()), validate=True)

    def _decrypt(path: str) -> Optional[str]:
        try:
            if not executor.file_exists(paths.key):
                raise ValueError(f"The key file {paths.key} is missing")
            return decrypt_managed_config(_binary(paths.key), _binary(path)).decode("utf-8")
        except Exception as exc:
            found.read_errors.append(f"Could not read {path}: {exc}. Check the file and key, then try again.")
            return None

    source = "plain file"
    if executor.file_exists(paths.conf):
        found.telegraf_conf = _read(paths.conf, required=True)
    elif executor.file_exists(paths.encrypted_conf):
        source = "encrypted file"
        found.telegraf_conf = _decrypt(paths.encrypted_conf)
    else:
        found.read_errors.append(f"The managed configuration is missing: neither {paths.conf} nor {paths.encrypted_conf} exists. Restore the configuration and try again.")
    if found.telegraf_conf is None:
        source = "unavailable: " + "; ".join(found.read_errors)
    if executor.file_exists(paths.encrypted_plugins):
        found.plugins_config = _decrypt(paths.encrypted_plugins)
    found.mandatory_tags = _read(paths.tags)
    found.grains = _read(paths.grains)
    if paths.is_windows:
        command = f"Get-ChildItem -Path '{paths.fragments}' -File -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name"
    else:
        command = "bash -c " + shlex.quote(
            f"if [ -d {shlex.quote(paths.fragments)} ]; then find {shlex.quote(paths.fragments)} -maxdepth 1 -type f -printf '%f\\n'; fi")
    listing = executor.execute(command, timeout=10)
    if not listing.success:
        found.read_errors.append(f"Could not list {paths.fragments}")
    elif listing.stdout.strip():
        for name in listing.stdout.splitlines():
            if name in (".", "..") or "/" in name or "\\" in name:
                found.read_errors.append("Invalid managed fragment name")
                continue
            content = _read(paths.fragments + ("\\" if paths.is_windows else "/") + name, required=True)
            if content is not None:
                found.telegraf_d[name] = content
    logger.info("Managed agent inspection: %r, configuration: %s, %d telegraf.d fragments", found, source, len(found.telegraf_d))
