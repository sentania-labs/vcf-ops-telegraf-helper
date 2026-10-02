"""VCF Operations 9.1 integration adapter.

Implements the official Broadcom workflow for VCF Operations 9.1 open-source Telegraf.
"""

from __future__ import annotations

import io
import ipaddress
import os
import re
import socket
from typing import Any, Dict, List, Optional, Tuple
import urllib.parse
import zipfile
import requests

from vcf_ops_telegraf_helper.adapters.base import IntegrationArtifacts, VCFOpsIntegration
from vcf_ops_telegraf_helper.logger import get_logger
from vcf_ops_telegraf_helper.models.vcf import AuthToken, CollectorInfo, VCFEnvironment, VirtualMachineResource

logger = get_logger("vcf91")


def get_default_ca_bundle(custom_path: Optional[str] = None) -> Any:
    """Resolve TLS verification bundle checking custom path, env vars, and OS system store."""
    if custom_path:
        return custom_path
    for env_var in ("REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "SSL_CERT_FILE"):
        val = os.environ.get(env_var)
        if val and os.path.exists(val):
            return val
    system_paths = [
        "/etc/ssl/certs/ca-certificates.crt",
        "/etc/pki/tls/certs/ca-bundle.crt",
        "/etc/ssl/ca-bundle.pem",
        "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",
    ]
    for p in system_paths:
        if os.path.exists(p):
            return p
    return True


class VCF91OpenTelegrafIntegration(VCFOpsIntegration):
    """Adapter for VCF Operations 9.1 Suite API and Cloud Proxy integration."""

    def __init__(self, env: VCFEnvironment, session: Optional[requests.Session] = None):
        self.env = env
        self.base_url = env.url.rstrip("/")
        self.session = session or requests.Session()
        if not env.verify_ssl:
            self.session.verify = False
        else:
            self.session.verify = get_default_ca_bundle(env.ca_cert_path)

    def validate_connection(self) -> bool:
        """Verify reachability of VCF Operations Suite API."""
        try:
            url = f"{self.base_url}/suite-api/api/versions"
            resp = self.session.get(url, timeout=10, headers={"Accept": "application/json"})
            return resp.status_code in (200, 401)
        except Exception:
            # Fall back to root path check if versions path is blocked
            try:
                resp = self.session.get(f"{self.base_url}/ui/", timeout=10)
                return resp.status_code in (200, 301, 302, 401)
            except Exception:
                return False

    def verify_credentials(self) -> None:
        """Confirm the token, or the username and password, are accepted by the Suite API."""
        if not self.env.token:
            if not (self.env.username and self.env.password):
                raise RuntimeError("No API token or username and password supplied")
            self.acquire_token(self.env.username, self.env.password)
        resp = self.session.get(
            f"{self.base_url}/suite-api/api/versions/current", headers=self._api_headers(), timeout=10
        )
        if resp.status_code in (401, 403):
            raise RuntimeError(f"VCF Operations rejected the credentials (HTTP {resp.status_code})")
        if resp.status_code != 200:
            raise RuntimeError(f"Credential check failed with HTTP {resp.status_code}")

    def detect_version(self) -> str:
        """Detect remote release version using Suite API versions endpoint."""
        try:
            url = f"{self.base_url}/suite-api/api/versions"
            resp = self.session.get(url, timeout=10, headers={"Accept": "application/json"})
            if resp.status_code == 200:
                data = resp.json()
                # Parse version string from release info
                if isinstance(data, dict):
                    return str(data.get("releaseName", "9.1.0"))
            return "9.1.0"
        except Exception:
            return "9.1.0 (assumed)"

    def acquire_token(self, username: str, password: str) -> AuthToken:
        """Acquire auth token via Suite API POST /suite-api/api/auth/token/acquire."""
        url = f"{self.base_url}/suite-api/api/auth/token/acquire"
        payload = {
            "username": username,
            "password": password,
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        try:
            resp = self.session.post(url, json=payload, headers=headers, timeout=15)
            if resp.status_code in (200, 201):
                data: Any = resp.json()
                token_str = data.get("token") if isinstance(data, dict) else None
                if token_str:
                    self.env.token = token_str
                    return AuthToken(token=token_str)
            raise RuntimeError(f"Authentication failed with status code {resp.status_code}")
        except Exception as e:
            if isinstance(e, RuntimeError):
                raise
            raise RuntimeError(f"Failed to acquire token from {url}: {e}")

    def get_collector_information(self) -> CollectorInfo:
        """Return collector target details."""
        return self.env.collector

    def detect_managed_vm(
        self,
        target_ip: Optional[str] = None,
        target_hostname: Optional[str] = None,
    ) -> Tuple[bool, Optional[str], Optional[str], Optional[str]]:
        """Identify if target is a registered vSphere VM in VCF Operations.

        Returns:
            Tuple of (is_managed, vm_entity_name, vcid, vm_mor).
        """
        if not self.env.token:
            return False, None, None, None

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"vRealizeOpsToken {self.env.token}",
        }

        # 1. Attempt query by guest IP address
        if target_ip and target_ip not in ("127.0.0.1", "localhost"):
            query_url = f"{self.base_url}/suite-api/api/resources/query"
            payload = {
                "adapterKind": ["VMWARE"],
                "resourceKind": ["VirtualMachine"],
                "propertyConditions": {
                    "conjunctionOperator": "OR",
                    "conditions": [
                        {
                            "key": "summary|guest|ipAddress",
                            "operator": "EQ",
                            "stringValue": target_ip,
                        }
                    ],
                },
            }
            try:
                resp = self.session.post(query_url, json=payload, headers=headers, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    res_list = data.get("resourceList", [])
                    if res_list:
                        managed, name, vcid, mor = self._extract_vm_identifiers(res_list[0])
                        if managed:
                            return managed, name, vcid, mor
            except Exception:
                pass

        # 2. Fall back to query by hostname (short name or FQDN)
        if target_hostname:
            short_name = target_hostname.split(".")[0]
            candidates = [target_hostname]
            if short_name != target_hostname:
                candidates.append(short_name)
            for query_name in candidates:
                url = f"{self.base_url}/suite-api/api/resources"
                params = {
                    "adapterKind": "VMWARE",
                    "resourceKind": "VirtualMachine",
                    "name": query_name,
                }
                try:
                    resp = self.session.get(url, headers=headers, params=params, timeout=10)
                    if resp.status_code == 200:
                        data = resp.json()
                        res_list = data.get("resourceList", [])
                        if res_list:
                            managed, name, vcid, mor = self._extract_vm_identifiers(res_list[0])
                            if managed:
                                return managed, name, vcid, mor
                except Exception:
                    pass

        return False, None, None, None

    def resolve_bound_vm(
        self,
        vm_mor: str,
        vc_id: Optional[str] = None,
    ) -> Tuple[bool, Optional[str], str, str]:
        """Resolve an explicitly requested VM binding.

        An explicit (vm_mor, vc_id) pair is trusted as given. A MOR alone is looked up
        in inventory, since MORs are only unique within one vCenter.

        Returns:
            Tuple of (is_managed, vm_entity_name, vcid, vm_mor).
        """
        if vc_id:
            return True, None, vc_id, vm_mor
        candidates = []
        for res in self._fetch_paged_resources(strict=True, adapterKind="VMWARE", resourceKind="VirtualMachine"):
            if self._is_stale(res):
                continue
            idents = {
                i.get("identifierType", {}).get("name"): i.get("value")
                for i in res.get("resourceKey", {}).get("resourceIdentifiers", [])
            }
            mor, vcid = idents.get("VMEntityObjectID"), idents.get("VMEntityVCID")
            name = idents.get("VMEntityName") or res.get("resourceKey", {}).get("name")
            if mor == vm_mor:
                candidates.append(VirtualMachineResource(
                    resource_id=res.get("identifier") or "", name=name or "", vm_mor=mor, vc_id=vcid
                ))
        matches = [vm for vm in candidates if vm.vc_id]
        if not matches:
            if candidates:
                raise RuntimeError(
                    f"Requested VM binding '{vm_mor}' is in VCF Operations inventory but has no vCenter ID; "
                    "supply the vCenter ID as well."
                )
            raise RuntimeError(
                f"Requested VM binding '{vm_mor}' was not found in VCF Operations inventory. "
                "Check the MOR, or supply the vCenter ID as well."
            )
        if len({vm.vc_id for vm in matches}) > 1:
            raise RuntimeError(
                f"Requested VM binding '{vm_mor}' exists in more than one vCenter; supply the vCenter ID to choose one."
            )
        return True, matches[0].name, matches[0].vc_id, vm_mor

    def _extract_vm_identifiers(
        self,
        resource: dict,
    ) -> Tuple[bool, Optional[str], Optional[str], Optional[str]]:
        res_key = resource.get("resourceKey", {})
        identifiers = res_key.get("resourceIdentifiers", [])
        vm_name = None
        vcid = None
        vm_mor = None

        for ident in identifiers:
            itype = ident.get("identifierType", {}).get("name")
            val = ident.get("value")
            if itype == "VMEntityName":
                vm_name = val
            elif itype == "VMEntityVCID":
                vcid = val
            elif itype == "VMEntityObjectID":
                vm_mor = val

        if vcid and vm_mor:
            return True, vm_name or res_key.get("name"), vcid, vm_mor
        return False, None, None, None

    def get_collector_groups(self, strict: bool = False) -> List[Dict[str, Any]]:
        """Retrieve list of collector groups from VCF Operations Suite API.

        With strict=True a failed query raises instead of looking like an empty list.
        """
        if not self.env.token:
            if strict:
                raise RuntimeError("Cannot query VCF Operations collector groups: no API token")
            return []

        headers = {
            "Accept": "application/json",
            "Authorization": f"vRealizeOpsToken {self.env.token}",
        }
        for endpoint in ("/suite-api/api/collectorGroups", "/suite-api/api/collectorgroups"):
            try:
                resp = self.session.get(f"{self.base_url}{endpoint}", headers=headers, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    groups = data.get("collectorGroup", []) or data.get("collectorGroups", [])
                    if isinstance(groups, list):
                        return groups
                    elif isinstance(groups, dict):
                        return [groups]
                else:
                    logger.warning(
                        "Collector groups query to %s returned HTTP %d: %s",
                        endpoint,
                        resp.status_code,
                        resp.text,
                    )
            except Exception as e:
                logger.warning("Failed querying collector groups at %s: %s", endpoint, e)
        if strict:
            raise RuntimeError("VCF Operations collector group query failed (see log for details)")
        return []

    def get_collectors(self, strict: bool = False) -> List[Dict[str, Any]]:
        """Retrieve list of collectors from VCF Operations Suite API.

        With strict=True a failed query raises instead of looking like an empty list.
        """
        if not self.env.token:
            if strict:
                raise RuntimeError("Cannot query VCF Operations collectors: no API token")
            return []

        headers = {
            "Accept": "application/json",
            "Authorization": f"vRealizeOpsToken {self.env.token}",
        }
        try:
            resp = self.session.get(f"{self.base_url}/suite-api/api/collectors", headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                collectors = data.get("collector", []) or data.get("collectors", [])
                if isinstance(collectors, list):
                    return collectors
                elif isinstance(collectors, dict):
                    return [collectors]
            else:
                logger.warning("Collectors query returned HTTP %d: %s", resp.status_code, resp.text)
        except Exception as e:
            logger.warning("Failed querying collectors: %s", e)
        if strict:
            raise RuntimeError("VCF Operations collector query failed (see log for details)")
        return []

    def resolve_collector_group_name(self, collector_target: str) -> str:
        """Resolve a Cloud Proxy target to its Collector Group name.

        The VCF Operations client certificate API endpoint requires the Collector Group Name,
        not the individual proxy or VIP IP address.
        """
        if self.env.collector.name:
            return self.env.collector.name

        groups = self.get_collector_groups()
        target_clean = collector_target.strip().lower()

        # 1. Match collector target against collector group attributes (name, id, VIP, IP, FQDN)
        for g in groups:
            g_name = str(g.get("name", "")).strip()
            g_id = str(g.get("id", "")).strip()
            g_vip = str(g.get("vip", "")).strip()
            g_virtual_ip = str(g.get("virtualIP") or g.get("virtualIp") or "").strip()
            g_ip = str(g.get("ipAddress", "")).strip()
            g_configured_vip = str(g.get("configuredVip", "")).strip()
            g_fqdn = str(g.get("fqdn", "")).strip()
            g_host = str(g.get("hostName", "")).strip()

            candidates = {
                c.lower() for c in (g_name, g_id, g_vip, g_virtual_ip, g_ip, g_configured_vip, g_fqdn, g_host) if c
            }
            if target_clean in candidates:
                return g_name

        # 2. Match collector target against individual collectors to find group membership
        collectors = self.get_collectors()
        matching_collector = None
        for c in collectors:
            c_ip = str(c.get("ipAddress", "")).strip().lower()
            c_name = str(c.get("name", "")).strip().lower()
            c_host = str(c.get("hostName", "")).strip().lower()
            if target_clean in (c_ip, c_name, c_host):
                matching_collector = c
                break

        if matching_collector:
            c_group_name = matching_collector.get("collectorGroupName")
            if c_group_name:
                return c_group_name
            c_group_id = str(matching_collector.get("collectorGroupId", ""))
            if c_group_id:
                for g in groups:
                    if str(g.get("id", "")) == c_group_id:
                        return g.get("name", collector_target)

        # 3. Fallback: if exactly one group exists in the environment, use it
        if len(groups) == 1 and groups[0].get("name"):
            return groups[0]["name"]

        return collector_target

    def fetch_client_certificate_bundle(
        self,
        collector_group_or_cp: str,
        client_id: str,
    ) -> Dict[str, Any]:
        """Acquire signed client certificate bundle from VCF Operations Suite API."""
        if not self.env.token:
            if self.env.username and self.env.password:
                self.acquire_token(self.env.username, self.env.password)
            else:
                raise RuntimeError("VCF Operations authentication token required for certificate retrieval.")

        safe_group = urllib.parse.quote(collector_group_or_cp, safe="")
        url = f"{self.base_url}/suite-api/api/applications/clientCertificate/{safe_group}"
        headers = {
            "Authorization": f"vRealizeOpsToken {self.env.token}",
            "Accept": "application/octet-stream",
            "Content-Type": "application/octet-stream",
        }
        params = {"clientId": client_id}

        resp = self.session.get(url, headers=headers, params=params, timeout=25)
        if resp.status_code != 200:
            raise RuntimeError(
                f"Failed to acquire client certificate bundle from {url} (HTTP {resp.status_code}): {resp.text}"
            )

        ca_pem = ""
        cert_pem = ""
        key_pem = ""
        master_pub = None
        vip = None
        mutual_auth = True

        def is_ca_filename(filename: str) -> bool:
            base = os.path.basename(filename).lower()
            return bool(
                re.search(r"(^|[._-])ca([._-]|$)", base)
                or "cacert" in base
                or "root" in base
            )

        def is_key_filename(filename: str) -> bool:
            base = os.path.basename(filename).lower()
            return base.endswith((".key", ".key.pem", "-key.pem", "_key.pem")) or "key" in base

        try:
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                namelist = zf.namelist()

                # 1. Identify CA certificate
                ca_name = None
                for name in namelist:
                    if name.endswith((".pem", ".crt")) and is_ca_filename(name) and not is_key_filename(name):
                        ca_pem = zf.read(name).decode("utf-8")
                        ca_name = name
                        break

                # 2. Identify private key
                key_name = None
                for name in namelist:
                    if is_key_filename(name):
                        key_pem = zf.read(name).decode("utf-8")
                        key_name = name
                        break

                # 3. Identify client certificate (must not be CA cert or key)
                for name in namelist:
                    if name.endswith((".pem", ".crt")) and name != ca_name and not is_key_filename(name):
                        if not is_ca_filename(name):
                            cert_pem = zf.read(name).decode("utf-8")
                            break

                # Fallback for client cert if not matched above
                if not cert_pem:
                    for name in namelist:
                        if name.endswith((".pem", ".crt")) and name != ca_name and name != key_name:
                            cert_pem = zf.read(name).decode("utf-8")
                            break

                if "master.pub" in namelist:
                    master_pub = zf.read("master.pub").decode("utf-8")
                if "IP" in namelist:
                    vip = zf.read("IP").decode("utf-8").strip()
                if "MUTUAL_AUTHENTICATION" in namelist:
                    raw_ma = zf.read("MUTUAL_AUTHENTICATION").decode("utf-8").strip().lower()
                    mutual_auth = (raw_ma == "true")
        except Exception as e:
            raise RuntimeError(f"Corrupted or invalid certificate bundle returned from VCF Operations: {e}")

        if not ca_pem or not cert_pem or not key_pem:
            raise RuntimeError(
                "Certificate bundle from VCF Operations is incomplete (missing CA cert, client cert, or key)."
            )

        return {
            "ca_cert": ca_pem,
            "client_cert": cert_pem,
            "client_key": key_pem,
            "master_pub": master_pub,
            "vip": vip,
            "mutual_auth": mutual_auth,
        }

    def fetch_mandatory_tag_script(
        self,
        collector_address: str,
        os_family: str = "linux",
    ) -> str:
        """Fetch mandatory_tags script from Cloud Proxy or return embedded fallback."""
        script_name = "mandatory_tags.bat" if os_family.lower() == "windows" else "mandatory_tags.sh"
        url = f"https://{collector_address}/downloads/salt/{script_name}"
        try:
            resp = self.session.get(url, verify=False, timeout=10)
            if resp.status_code == 200 and resp.text.strip():
                content = resp.text
                if os_family.lower() == "windows":
                    # If Cloud Proxy script uses deprecated wmic without reg query fallback,
                    # use the modernized script compatible with Windows Server 2025.
                    if "reg query" in content:
                        return content
                    return self._get_windows_mandatory_tag_script()
                return content
        except Exception:
            pass

        if os_family.lower() == "windows":
            return self._get_windows_mandatory_tag_script()

        return (
            "#!/usr/bin/env bash\n"
            "TELEGRAF_BIN=\"${1:-/usr/bin/telegraf}\"\n"
            "TVER=$(\"$TELEGRAF_BIN\" version 2>/dev/null | awk '{print $2}')\n"
            "HNAME=$(hostname)\n"
            "OS_NAME=\"Linux\"\n"
            "OS_VER=\"unknown\"\n"
            "if [ -f /etc/os-release ]; then\n"
            "  . /etc/os-release\n"
            "  [ -n \"$NAME\" ] && OS_NAME=$(echo \"$NAME\" | tr ' ' '_')\n"
            "  [ -n \"$VERSION_ID\" ] && OS_VER=$(echo \"$VERSION_ID\" | tr ' ' '_')\n"
            "fi\n"
            "IP_VAL=$(hostname -I 2>/dev/null | awk '{print $1}')\n"
            "OS_NAME=\"${OS_NAME:-Linux}\"\n"
            "OS_VER=\"${OS_VER:-unknown}\"\n"
            "TVER=\"${TVER:-unknown}\"\n"
            "HNAME=\"${HNAME:-localhost}\"\n"
            "IP_VAL=\"${IP_VAL:-unknown}\"\n"
            "echo \"mandatory.tag,OS_NAME=${OS_NAME},OS_VERSION=${OS_VER},TELEGRAF_VERSION=${TVER},HOSTNAME=${HNAME},IP=${IP_VAL} value=1i\"\n"
        )

    @staticmethod
    def _get_windows_mandatory_tag_script() -> str:
        """Return robust mandatory_tags.bat compatible with Windows Server 2012 through 2025."""
        return (
            "@echo off\r\n"
            "set TELEGRAF_BIN_PATH=C:\\telegraf\\telegraf.exe\r\n"
            "set \"grains=C:\\VMware\\UCP\\salt\\conf\\grains\"\r\n"
            "if NOT \"%~1\"==\"\" set TELEGRAF_BIN_PATH=%~1\r\n"
            "set HNAME=%COMPUTERNAME%\r\n"
            "set ip_address_string=\"IPv4\"\r\n"
            "set ips=\r\n"
            "for /f \"usebackq tokens=2 delims=:\" %%f in (`ipconfig ^| findstr /c:%ip_address_string%`) do call set \"ips=%%ips%%-%%f\"\r\n"
            "set ips=%ips: =%\r\n"
            "set VM_IP=%ips:~1%\r\n"
            "if not defined VM_IP set VM_IP=127.0.0.1\r\n"
            "set OS_NAME=\r\n"
            "for /f \"tokens=2 delims==\" %%f in ('wmic os get Caption /value 2^>nul ^| find \"=\"') do set \"OS_NAME=%%f\"\r\n"
            "if not defined OS_NAME (\r\n"
            "    for /f \"tokens=2*\" %%a in ('reg query \"HKLM\\Software\\Microsoft\\Windows NT\\CurrentVersion\" /v ProductName 2^>nul') do set \"OS_NAME=%%b\"\r\n"
            ")\r\n"
            "if not defined OS_NAME set OS_NAME=Windows\r\n"
            "set OS_NAME=%OS_NAME: =_%\r\n"
            "set OS_VERSION=\r\n"
            "for /f \"tokens=2 delims==\" %%f in ('wmic os get Version /value 2^>nul ^| find \"=\"') do set \"OS_VERSION=%%f\"\r\n"
            "if not defined OS_VERSION (\r\n"
            "    for /f \"tokens=2*\" %%a in ('reg query \"HKLM\\Software\\Microsoft\\Windows NT\\CurrentVersion\" /v CurrentBuild 2^>nul') do set \"OS_VERSION=%%b\"\r\n"
            ")\r\n"
            "if not defined OS_VERSION set OS_VERSION=unknown\r\n"
            "set OS_VERSION=%OS_VERSION: =_%\r\n"
            "set TF_VERSION=unknown\r\n"
            "for /f \"tokens=2\" %%i in ('\"%TELEGRAF_BIN_PATH%\" --version 2^>nul') do set TF_VERSION=%%i\r\n"
            "if \"%TF_VERSION%\"==\"unknown\" (\r\n"
            "    for /f \"tokens=2\" %%i in ('\"%TELEGRAF_BIN_PATH%\" version 2^>nul') do set TF_VERSION=%%i\r\n"
            ")\r\n"
            "set BIOS_VERSION=\r\n"
            "for /f \"tokens=2 delims==\" %%f in ('wmic bios get smbiosbiosversion /value 2^>nul ^| find \"=\"') do set \"BIOS_VERSION=%%f\"\r\n"
            "if not defined BIOS_VERSION (\r\n"
            "    for /f \"tokens=2*\" %%a in ('reg query \"HKLM\\HARDWARE\\DESCRIPTION\\System\\BIOS\" /v BIOSVersion 2^>nul') do set \"BIOS_VERSION=%%b\"\r\n"
            ")\r\n"
            "if not defined BIOS_VERSION set BIOS_VERSION=unknown\r\n"
            "set BIOS_VERSION=%BIOS_VERSION: =_%\r\n"
            "set BOOTSTRAP_FQDN=None\r\n"
            "if exist \"%grains%\" (\r\n"
            "    for /f \"tokens=1,2 delims=: \" %%a in ('findstr /i /c:\"arc_fqdn:\" \"%grains%\" 2^>nul') do set \"BOOTSTRAP_FQDN=%%b\"\r\n"
            ")\r\n"
            "if not defined BOOTSTRAP_FQDN set BOOTSTRAP_FQDN=None\r\n"
            "set BOOTSTRAP_FQDN=%BOOTSTRAP_FQDN: =_%\r\n"
            "set METRIC_VALUE=1\r\n"
            "if [%HNAME%]==[] if [%VM_IP%]==[] if [%OS_NAME%]==[] if [%OS_VERSION%]==[] set METRIC_VALUE=0\r\n"
            "echo mandatory.tag,OS_NAME=%OS_NAME%,OS_VERSION=%OS_VERSION%,TELEGRAF_VERSION=%TF_VERSION%,IP=%VM_IP%,BIOS_VERSION=%BIOS_VERSION%,BOOTSTRAP_FQDN=%BOOTSTRAP_FQDN%,HOSTNAME=%HNAME% value=%METRIC_VALUE%i\r\n"
        )
        return (
            "#!/usr/bin/env bash\n"
            "TELEGRAF_BIN=\"${1:-/usr/bin/telegraf}\"\n"
            "TVER=$(\"$TELEGRAF_BIN\" version 2>/dev/null | awk '{print $2}')\n"
            "HNAME=$(hostname)\n"
            "OS_NAME=\"Linux\"\n"
            "OS_VER=\"unknown\"\n"
            "if [ -f /etc/os-release ]; then\n"
            "  . /etc/os-release\n"
            "  [ -n \"$NAME\" ] && OS_NAME=$(echo \"$NAME\" | tr ' ' '_')\n"
            "  [ -n \"$VERSION_ID\" ] && OS_VER=$(echo \"$VERSION_ID\" | tr ' ' '_')\n"
            "fi\n"
            "IP_VAL=$(hostname -I 2>/dev/null | awk '{print $1}')\n"
            "OS_NAME=\"${OS_NAME:-Linux}\"\n"
            "OS_VER=\"${OS_VER:-unknown}\"\n"
            "TVER=\"${TVER:-unknown}\"\n"
            "HNAME=\"${HNAME:-localhost}\"\n"
            "IP_VAL=\"${IP_VAL:-unknown}\"\n"
            "echo \"mandatory.tag,OS_NAME=${OS_NAME},OS_VERSION=${OS_VER},TELEGRAF_VERSION=${TVER},HOSTNAME=${HNAME},IP=${IP_VAL} value=1i\"\n"
        )

    def prepare_telegraf_integration(
        self,
        os_family: str = "linux",
        target_ip: Optional[str] = None,
        target_hostname: Optional[str] = None,
        target_uuid: Optional[str] = None,
        existing_cert_bundle: Optional[Dict[str, Any]] = None,
        vm_mor: Optional[str] = None,
        vc_id: Optional[str] = None,
    ) -> IntegrationArtifacts:
        """Prepare tokens, URLs, mTLS client certificates, and metadata artifacts."""
        token = self.env.token
        if not token:
            if self.env.username and self.env.password:
                token_obj = self.acquire_token(self.env.username, self.env.password)
                token = token_obj.token
            else:
                raise RuntimeError(
                    "VCF Operations credentials required: please specify a valid username and password, or an API token."
                )

        collector_addr = self.env.collector.address
        script_name = "telegraf-utils.ps1" if os_family.lower() == "windows" else "telegraf-utils.sh"

        # Validate collector target against known Ops collectors/groups
        all_collectors = self.get_collectors()
        all_groups = self.get_collector_groups()
        if all_collectors or all_groups:
            known_targets = set()
            has_ip_info = False
            for g in all_groups:
                for k in ("name", "id", "vip", "virtualIP", "virtualIp", "ipAddress", "configuredVip", "fqdn", "hostName"):
                    val = str(g.get(k, "")).strip().lower()
                    if val:
                        clean_v = val.split(":")[0]
                        known_targets.add(clean_v)
                        if "." in clean_v and not self._looks_like_ip(clean_v):
                            known_targets.add(clean_v.split(".")[0])
                if any(g.get(k) for k in ("vip", "virtualIP", "virtualIp", "ipAddress", "configuredVip")):
                    has_ip_info = True
            for c in all_collectors:
                for k in ("ipAddress", "name", "hostName"):
                    val = str(c.get(k, "")).strip().lower()
                    if val:
                        clean_v = val.split(":")[0]
                        known_targets.add(clean_v)
                        if "." in clean_v and not self._looks_like_ip(clean_v):
                            known_targets.add(clean_v.split(".")[0])
                if c.get("ipAddress"):
                    has_ip_info = True

            target_clean = collector_addr.strip().lower().split(":")[0]
            # Short names only apply to FQDNs; shortening an IP would match on its first octet
            target_short = (
                target_clean.split(".")[0] if "." in target_clean and not self._looks_like_ip(target_clean) else target_clean
            )

            is_target_ip = False
            try:
                socket.inet_aton(target_clean)
                is_target_ip = True
            except OSError:
                is_target_ip = False

            resolved_ips = set()
            try:
                resolved_ips.add(socket.gethostbyname(target_clean))
            except Exception:
                pass

            matched = (
                target_clean in known_targets
                or target_short in known_targets
                or bool(resolved_ips.intersection(known_targets))
                or (is_target_ip and not has_ip_info and len(all_groups) == 1)
            )
            if not matched:
                raise RuntimeError(
                    f"Collector '{collector_addr}' is not registered in VCF Operations. "
                    "Please verify the Cloud Proxy address or Collector Group."
                )

        # 1. Detect if target is a managed VM in VCF Operations
        is_managed = False
        vm_name = None

        explicit_binding = bool(vm_mor)
        if vm_mor:
            is_managed, vm_name, vc_id, vm_mor = self.resolve_bound_vm(vm_mor, vc_id)
        elif target_ip or target_hostname:
            is_managed, vm_name, vc_id, vm_mor = self.detect_managed_vm(target_ip, target_hostname)

        # 2. Determine clientId for certificate request
        if is_managed and vc_id and vm_mor:
            client_id = f"{vc_id}_{vm_mor}"
        else:
            clean_uuid = target_uuid.strip() if target_uuid else ""
            if clean_uuid and not any(ch in clean_uuid for ch in "[] \t\n\r"):
                client_id = clean_uuid
            else:
                clean_host = re.sub(r"[^a-zA-Z0-9_.-]", "_", (target_hostname or target_ip or "endpoint")).strip("_")
                client_id = f"endpoint_{clean_host or 'client'}"

        # 3. Retrieve client certificate bundle
        ca_cert_content = None
        client_cert_content = None
        client_key_content = None
        master_pub_content = None
        vip_content = None
        mutual_auth = True
        collector_group = self.resolve_collector_group_name(collector_addr)
        logger.info("Resolved collector group name for %s: %s", collector_addr, collector_group)

        last_cert_err = None
        # Reuse only a cert issued for this client identity. Endpoints enrolled before the identity
        # was recorded are reused as before, unless the caller explicitly binds a VM.
        existing_id = (existing_cert_bundle or {}).get("client_id")
        identity_ok = (existing_id == client_id) if existing_id else not explicit_binding
        if not identity_ok and existing_cert_bundle:
            logger.info("Existing certificate was issued for '%s', not '%s'; minting a new one", existing_id, client_id)
        if (
            identity_ok
            and existing_cert_bundle
            and existing_cert_bundle.get("client_cert")
            and existing_cert_bundle.get("client_key")
        ):
            logger.info("Reusing existing client certificate bundle from target (idempotent run)")
            ca_cert_content = existing_cert_bundle.get("ca_cert")
            client_cert_content = existing_cert_bundle.get("client_cert")
            client_key_content = existing_cert_bundle.get("client_key")
            master_pub_content = existing_cert_bundle.get("master_pub")
            vip_content = existing_cert_bundle.get("vip")
            mutual_auth = existing_cert_bundle.get("mutual_auth", True)
        else:
            try:
                bundle = self.fetch_client_certificate_bundle(collector_group, client_id)
                ca_cert_content = bundle.get("ca_cert")
                client_cert_content = bundle.get("client_cert")
                client_key_content = bundle.get("client_key")
                master_pub_content = bundle.get("master_pub")
                vip_content = bundle.get("vip")
                mutual_auth = bundle.get("mutual_auth", True)
            except Exception as e:
                last_cert_err = e
                # Collector group name failed; retry by collector_addr when it differs
                if collector_group != collector_addr:
                    try:
                        bundle = self.fetch_client_certificate_bundle(collector_addr, client_id)
                        ca_cert_content = bundle.get("ca_cert")
                        client_cert_content = bundle.get("client_cert")
                        client_key_content = bundle.get("client_key")
                        master_pub_content = bundle.get("master_pub")
                        vip_content = bundle.get("vip")
                        mutual_auth = bundle.get("mutual_auth", True)
                        last_cert_err = None
                    except Exception as inner_e:
                        last_cert_err = inner_e

        if last_cert_err is not None:
            raise RuntimeError(
                f"Failed to acquire mTLS client certificate bundle from VCF Operations for collector group '{collector_group}' and client '{client_id}': {last_cert_err}"
            )

        # 4. Retrieve mandatory_tags script content
        mandatory_tags_content = self.fetch_mandatory_tag_script(collector_addr, os_family)

        return IntegrationArtifacts(
            token=token,
            collector_address=collector_addr,
            script_url=f"https://{collector_addr}/downloads/salt/{script_name}",
            output_url=f"https://{collector_addr}/opensource/default/metric",
            skip_certificate=not self.env.verify_ssl,
            ca_cert_path=self.env.ca_cert_path,
            ca_cert_content=ca_cert_content,
            client_cert_content=client_cert_content,
            client_key_content=client_key_content,
            master_pub_content=master_pub_content,
            vip_content=vip_content,
            collector_group=collector_group,
            mandatory_tags_content=mandatory_tags_content,
            is_managed_vm=is_managed,
            vm_name=vm_name,
            vm_mor=vm_mor,
            vc_id=vc_id,
            client_id=client_id,
            mutual_auth=mutual_auth,
        )

    def verify_ingestion(self, target_hostname: str) -> str:
        """Check whether metrics for target are appearing in VCF Operations.

        Returns:
            'PASS' if object with stats found, 'PENDING' if enrolled but roll-up pending,
            'UNKNOWN' if API cannot confirm yet, 'FAIL' if error.
        """
        if not self.env.token:
            return "UNKNOWN"

        candidates = [target_hostname]
        try:
            import ipaddress
            ipaddress.ip_address(target_hostname)
        except ValueError:
            short_name = target_hostname.split(".")[0]
            if short_name != target_hostname:
                candidates.append(short_name)

        headers = {
            "Accept": "application/json",
            "Authorization": f"vRealizeOpsToken {self.env.token}",
        }

        try:
            url = f"{self.base_url}/suite-api/api/resources"
            for candidate in candidates:
                # Strictly query APPOSUCP: open-source Telegraf agents ingest exclusively under APPOSUCP.
                # Querying VMWARE produces false positives by matching the vCenter hypervisor VM stats.
                params = {"name": candidate, "adapterKind": "APPOSUCP"}
                resp = self.session.get(url, headers=headers, params=params, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    resource_list = data.get("resourceList", [])
                    if resource_list:
                        res_id = resource_list[0].get("identifier")
                        if res_id:
                            stats_url = f"{self.base_url}/suite-api/api/resources/{res_id}/stats/latest"
                            try:
                                s_resp = self.session.get(stats_url, headers=headers, timeout=10)
                                if s_resp.status_code == 200:
                                    s_data = s_resp.json()
                                    stat_values = s_data.get("values", [])
                                    if stat_values:
                                        return "PASS"
                            except Exception:
                                pass
                        # Resource enrolled in VCF Ops APPOSUCP, metrics roll-up pending (5-15 min)
                        return "PENDING"
            return "UNKNOWN"
        except Exception:
            return "UNKNOWN"

    def _api_headers(self) -> Dict[str, str]:
        return {
            "Accept": "application/json",
            "Authorization": f"vRealizeOpsToken {self.env.token}",
        }

    def _ensure_token(self) -> None:
        if not self.env.token and self.env.username and self.env.password:
            try:
                self.acquire_token(self.env.username, self.env.password)
            except Exception:
                pass

    def _fetch_paged_resources(self, strict: bool, **query: Any) -> List[Dict[str, Any]]:
        """Page through GET /resources. With strict=True a failure raises instead of truncating."""
        self._ensure_token()
        if not self.env.token:
            if strict:
                raise RuntimeError("Cannot query VCF Operations inventory: no API token (check credentials)")
            return []

        url = f"{self.base_url}/suite-api/api/resources"
        resources: List[Dict[str, Any]] = []
        page = 0
        page_size = 1000
        while True:
            params = dict(query, page=page, pageSize=page_size)
            try:
                resp = self.session.get(url, headers=self._api_headers(), params=params, timeout=30)
            except Exception as exc:
                if strict:
                    raise RuntimeError(f"VCF Operations inventory query failed on page {page}: {exc}") from exc
                logger.warning("Failed to list resources on page %d from VCF Operations: %s", page, exc)
                break
            if resp.status_code != 200:
                if strict:
                    raise RuntimeError(f"VCF Operations inventory query failed on page {page} with HTTP {resp.status_code}")
                logger.warning("Suite API resource query failed on page %d with status %d", page, resp.status_code)
                break
            data = resp.json()
            res_list = data.get("resourceList", [])
            resources.extend(res_list)
            total_count = (data.get("pageInfo") or {}).get("totalCount")
            if not res_list or len(res_list) < page_size:
                break
            if total_count is not None and len(resources) >= total_count:
                break
            page += 1
        return resources

    @staticmethod
    def _is_stale(resource: Dict[str, Any]) -> bool:
        """True when every adapter reports the object as no longer existing (deleted in vCenter)."""
        states = [s.get("resourceState") for s in resource.get("resourceStatusStates") or []]
        return bool(states) and all(st == "NOT_EXISTING" for st in states)

    def _fetch_properties(self, resource_ids: List[str]) -> Dict[str, Dict[str, str]]:
        """Bulk-read properties for many resources; returns {resource_id: {property: value}}."""
        url = f"{self.base_url}/suite-api/api/resources/properties"
        result: Dict[str, Dict[str, str]] = {}
        chunk = 100  # keeps the repeated resourceId query string well under URL length limits
        for i in range(0, len(resource_ids), chunk):
            ids = resource_ids[i:i + chunk]
            resp = self.session.get(
                url, headers=self._api_headers(), params=[("resourceId", rid) for rid in ids], timeout=60
            )
            if resp.status_code != 200:
                raise RuntimeError(f"VCF Operations property query failed with HTTP {resp.status_code}")
            for entry in resp.json().get("resourcePropertiesList", []):
                result[entry.get("resourceId")] = {
                    p.get("name"): p.get("value") for p in entry.get("property", []) or []
                }
        return result

    def _collector_maps(self) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, str], List[Dict[str, Any]]]:
        """Return ({collector_id: collector}, {collector_id: group_name}, groups); raises on query failure."""
        collectors = {str(c.get("id")): c for c in self.get_collectors(strict=True)}
        groups = self.get_collector_groups(strict=True)
        group_of: Dict[str, str] = {}
        for g in groups:
            for cid in g.get("collectorId") or []:
                group_of[str(cid)] = g.get("name")
        return collectors, group_of, groups

    @staticmethod
    def _looks_like_ip(value: str) -> bool:
        try:
            ipaddress.ip_address(value)
            return True
        except ValueError:
            return False

    @staticmethod
    def _group_vip(group: Dict[str, Any]) -> Optional[str]:
        for key in ("virtualIP", "virtualIp", "vip", "configuredVip"):
            if group.get(key):
                return str(group[key]).strip()
        return None

    def _fetch_agent_registrations(self) -> Dict[Tuple[str, str], List[Dict[str, Any]]]:
        """Map (vcid, vm_mor) to the agent OS objects (application monitoring adapter) bound to that VM."""
        resources = self._fetch_paged_resources(strict=True, adapterKind="APPOSUCP")
        resp = self.session.get(
            f"{self.base_url}/suite-api/api/adapters",
            headers=self._api_headers(),
            params={"adapterKindKey": "APPOSUCP"},
            timeout=30,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"VCF Operations adapter instance query failed with HTTP {resp.status_code}")
        instance_collector: Dict[str, str] = {
            inst.get("id"): str(inst.get("collectorId")) for inst in resp.json().get("adapterInstancesInfoDto", [])
        }
        collectors, group_of, _ = self._collector_maps()

        registrations: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        for res in resources:
            res_key = res.get("resourceKey", {})
            if res_key.get("resourceKindKey") not in ("linux", "win") or self._is_stale(res):
                continue
            ids = {i.get("identifierType", {}).get("name"): i.get("value") for i in res_key.get("resourceIdentifiers", [])}
            key = (ids.get("VCID"), ids.get("VMMOR"))
            if not key[0] or not key[1]:
                continue
            states = res.get("resourceStatusStates") or [{}]
            # Prefer the state reported by the application monitoring adapter instance itself
            status = next((st for st in states if st.get("adapterInstanceId") in instance_collector), states[0])
            collector_id = instance_collector.get(status.get("adapterInstanceId"))
            collector = collectors.get(collector_id or "", {})
            registrations.setdefault(key, []).append({
                "receiving": status.get("resourceStatus") == "DATA_RECEIVING",
                "collector_address": collector.get("hostName"),
                "collector_group": group_of.get(collector_id or ""),
            })
        return registrations

    def list_virtual_machines(self, strict: bool = False) -> List[VirtualMachineResource]:
        """Discover candidate virtual machines from VCF Operations inventory.

        Templates and objects for VMs already deleted from vCenter are excluded. Guest IP,
        hostname, OS, and power state come from VM properties; agent status comes from the
        agent OS objects that carry the VM's vCenter ID and MOR.
        """
        raw = [
            r for r in self._fetch_paged_resources(strict, adapterKind="VMWARE", resourceKind="VirtualMachine")
            if not self._is_stale(r)
        ]
        if not raw:
            return []
        props = self._fetch_properties([r.get("identifier") for r in raw if r.get("identifier")])
        self.inventory_warning = None
        registrations: Optional[Dict[Tuple[str, str], List[Dict[str, Any]]]]
        try:
            registrations = self._fetch_agent_registrations()
        except Exception as exc:
            # Agent status is supplementary; keep the VM list and say plainly what is missing
            logger.warning("Agent status lookup failed: %s", exc)
            self.inventory_warning = f"Agent status unavailable: {exc}"
            registrations = None

        vms: List[VirtualMachineResource] = []
        for res in raw:
            pr = props.get(res.get("identifier"), {})
            if str(pr.get("summary|config|isTemplate", "")).lower() == "true":
                continue
            _, vm_name, vcid, vm_mor = self._extract_vm_identifiers(res)
            ip_addr = (pr.get("summary|guest|ipAddress") or "").strip()
            if ip_addr.lower() in ("", "none", "unknown"):
                ip_addr = None
            hostname = (pr.get("summary|guest|hostName") or "").strip() or None
            os_name = pr.get("config|guestFullName") or pr.get("summary|guest|fullName")
            regs = registrations.get((vcid, vm_mor), []) if registrations is not None else []
            if registrations is None:
                status = "Unknown"
            elif not regs:
                status = "Not installed"
            else:
                status = "Reporting" if any(r["receiving"] for r in regs) else "No data"
            primary = next((r for r in regs if r["receiving"]), regs[0] if regs else {})
            vms.append(
                VirtualMachineResource(
                    resource_id=res.get("identifier") or "",
                    name=vm_name or res.get("resourceKey", {}).get("name") or "Unknown VM",
                    ip_address=ip_addr,
                    hostname=hostname,
                    vm_mor=vm_mor,
                    vc_id=vcid,
                    os_name=os_name,
                    os_family="WINDOWS" if os_name and "windows" in os_name.lower() else "LINUX",
                    power_state=pr.get("summary|runtime|powerState"),
                    collector_group=primary.get("collector_group"),
                    collector_address=primary.get("collector_address"),
                    telegraf_status=status,
                    agent_registrations=len(regs),
                )
            )
        return vms

    def list_collector_targets(self) -> List[CollectorInfo]:
        """List where an agent can send telemetry: each collector group containing cloud proxies
        (via its virtual IP when it has one) and each cloud proxy individually."""
        self._ensure_token()
        collectors, group_of, groups = self._collector_maps()
        proxies = {}
        for cid, c in collectors.items():
            if c.get("type") != "UNIFIED_CLOUD_PROXY":
                continue
            if not c.get("hostName"):
                logger.warning("Skipping cloud proxy %s (%s): no address reported", cid, c.get("name"))
                continue
            proxies[cid] = c
        targets: List[CollectorInfo] = []
        for g in groups:
            member_proxies = [proxies[str(cid)] for cid in g.get("collectorId") or [] if str(cid) in proxies]
            if not member_proxies:
                continue
            vip = self._group_vip(g)
            if vip:
                targets.append(CollectorInfo(address=vip, name=g.get("name"), is_collector_group=True))
            for p in member_proxies:
                targets.append(
                    CollectorInfo(address=p.get("hostName"), name=g.get("name"), display_name=p.get("name"))
                )
        grouped = {str(cid) for cid in group_of}
        for cid, p in proxies.items():
            if cid not in grouped:
                targets.append(CollectorInfo(address=p.get("hostName"), display_name=p.get("name")))
        return targets
