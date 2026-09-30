"""VCF Operations 9.1 integration adapter.

Implements the official Broadcom workflow for VCF Operations 9.1 open-source Telegraf.
"""

from __future__ import annotations

import io
import os
import re
from typing import Any, Dict, List, Optional, Tuple
import urllib.parse
import zipfile
import requests

from vcf_ops_telegraf_helper.adapters.base import IntegrationArtifacts, VCFOpsIntegration
from vcf_ops_telegraf_helper.logger import get_logger
from vcf_ops_telegraf_helper.models.vcf import AuthToken, CollectorInfo, VCFEnvironment

logger = get_logger("vcf91")


class VCF91OpenTelegrafIntegration(VCFOpsIntegration):
    """Adapter for VCF Operations 9.1 Suite API and Cloud Proxy integration."""

    def __init__(self, env: VCFEnvironment, session: Optional[requests.Session] = None):
        self.env = env
        self.base_url = env.url.rstrip("/")
        self.session = session or requests.Session()
        self.session.verify = env.verify_ssl if env.ca_cert_path is None else env.ca_cert_path

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

    def get_collector_groups(self) -> List[Dict[str, Any]]:
        """Retrieve list of collector groups from VCF Operations Suite API."""
        if not self.env.token:
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
        return []

    def get_collectors(self) -> List[Dict[str, Any]]:
        """Retrieve list of collectors from VCF Operations Suite API."""
        if not self.env.token:
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
            g_virtual_ip = str(g.get("virtualIp", "")).strip()
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
    ) -> IntegrationArtifacts:
        """Prepare tokens, URLs, mTLS client certificates, and metadata artifacts."""
        token = self.env.token
        if not token:
            if self.env.username and self.env.password:
                token_obj = self.acquire_token(self.env.username, self.env.password)
                token = token_obj.token
            else:
                token = "local-simulated-token"

        collector_addr = self.env.collector.address
        script_name = "telegraf-utils.ps1" if os_family.lower() == "windows" else "telegraf-utils.sh"

        # 1. Detect if target is a managed VM in VCF Operations
        is_managed = False
        vm_mor = None
        vc_id = None

        if target_ip or target_hostname:
            is_managed, _, vc_id, vm_mor = self.detect_managed_vm(target_ip, target_hostname)

        # 2. Determine clientId for certificate request
        if is_managed and vc_id and vm_mor:
            client_id = f"{vc_id}_{vm_mor}"
        else:
            client_id = target_uuid or "endpoint-client"

        # 3. Retrieve client certificate bundle
        ca_cert_content = None
        client_cert_content = None
        client_key_content = None
        master_pub_content = None
        vip_content = None
        mutual_auth = True
        collector_group = None

        if token == "local-simulated-token":
            ca_cert_content = "-----BEGIN CERTIFICATE-----\nSIMULATED CA CERT\n-----END CERTIFICATE-----\n"
            client_cert_content = "-----BEGIN CERTIFICATE-----\nSIMULATED CLIENT CERT\n-----END CERTIFICATE-----\n"
            client_key_content = "-----BEGIN RSA PRIVATE KEY-----\nSIMULATED KEY\n-----END RSA PRIVATE KEY-----\n"
            master_pub_content = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQC simulated"
            vip_content = collector_addr
            collector_group = "default-collector-group"
            mutual_auth = True
        else:
            collector_group = self.resolve_collector_group_name(collector_addr)
            logger.info("Resolved collector group name for %s: %s", collector_addr, collector_group)

            last_cert_err = None
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
                # If collector group name failed and differs from collector_addr, try collector_addr
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
            vm_mor=vm_mor,
            vc_id=vc_id,
            client_id=client_id,
            mutual_auth=mutual_auth,
        )

    def verify_ingestion(self, target_hostname: str) -> str:
        """Check whether metrics for target are appearing in VCF Operations.

        Returns:
            'PASS' if object found, 'UNKNOWN' if API cannot confirm yet, 'FAIL' if error.
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

        try:
            url = f"{self.base_url}/suite-api/api/resources"
            headers = {
                "Accept": "application/json",
                "Authorization": f"vRealizeOpsToken {self.env.token}",
            }
            for candidate in candidates:
                params = {"name": candidate}
                resp = self.session.get(url, headers=headers, params=params, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    resource_list = data.get("resourceList", [])
                    if resource_list:
                        return "PASS"
            return "UNKNOWN"
        except Exception:
            return "UNKNOWN"
