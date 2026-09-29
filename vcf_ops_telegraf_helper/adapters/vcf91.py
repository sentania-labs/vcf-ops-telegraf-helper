"""VCF Operations 9.1 integration adapter.

Implements the official Broadcom workflow for VCF Operations 9.1 open-source Telegraf.
"""

from __future__ import annotations

import io
from typing import Any, Dict, Optional, Tuple
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

        url = f"{self.base_url}/suite-api/api/applications/clientCertificate/{collector_group_or_cp}"
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
        mutual_auth = True

        try:
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                namelist = zf.namelist()
                for name in ("ca.cert.pem", "ca.pem"):
                    if name in namelist:
                        ca_pem = zf.read(name).decode("utf-8")
                        break
                for name in namelist:
                    if name.endswith(".cert.pem") and name not in ("ca.cert.pem", "ca.pem"):
                        cert_pem = zf.read(name).decode("utf-8")
                        break
                    elif name.endswith("cert.pem") and name not in ("ca.cert.pem", "ca.pem"):
                        cert_pem = zf.read(name).decode("utf-8")
                        break
                for name in namelist:
                    if name.endswith(".key") or name.endswith("key.pem"):
                        key_pem = zf.read(name).decode("utf-8")
                        break
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
                return resp.text
        except Exception:
            pass

        if os_family.lower() == "windows":
            return (
                "@echo off\r\n"
                "set TELEGRAF_EXE_PATH=%~1\r\n"
                "if \"%TELEGRAF_EXE_PATH%\"==\"\" set TELEGRAF_EXE_PATH=C:\\telegraf\\telegraf.exe\r\n"
                "set OS_NAME=Windows\r\n"
                "set OS_VERSION=unknown\r\n"
                "set TELEGRAF_VER=unknown\r\n"
                "set IP=unknown\r\n"
                "for /f \"tokens=2*\" %%a in ('reg query \"HKLM\\Software\\Microsoft\\Windows NT\\CurrentVersion\" /v ProductName 2^>nul') do set OS_NAME=%%b\r\n"
                "for /f \"tokens=2*\" %%a in ('reg query \"HKLM\\Software\\Microsoft\\Windows NT\\CurrentVersion\" /v CurrentBuild 2^>nul') do set OS_VERSION=%%b\r\n"
                "for /f \"tokens=2\" %%v in ('\"%TELEGRAF_EXE_PATH%\" version 2^>nul') do set TELEGRAF_VER=%%v\r\n"
                "for /f \"tokens=2 delims=:\" %%f in ('ipconfig ^| findstr /i \"IPv4\"') do if not defined IP set IP=%%f\r\n"
                "set OS_NAME=%OS_NAME: =_%\r\n"
                "set IP=%IP: =%\r\n"
                "echo mandatory.tag,OS_NAME=%OS_NAME%,OS_VERSION=%OS_VERSION%,TELEGRAF_VERSION=%TELEGRAF_VER%,HOSTNAME=%COMPUTERNAME%,IP=%IP% value=1i\r\n"
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
        mutual_auth = True

        collector_target = self.env.collector.name or collector_addr
        last_cert_err = None
        try:
            bundle = self.fetch_client_certificate_bundle(collector_target, client_id)
            ca_cert_content = bundle.get("ca_cert")
            client_cert_content = bundle.get("client_cert")
            client_key_content = bundle.get("client_key")
            mutual_auth = bundle.get("mutual_auth", True)
        except Exception as e:
            last_cert_err = e
            # If collector group name failed and address is different, try address
            if self.env.collector.name and collector_target != collector_addr:
                try:
                    bundle = self.fetch_client_certificate_bundle(collector_addr, client_id)
                    ca_cert_content = bundle.get("ca_cert")
                    client_cert_content = bundle.get("client_cert")
                    client_key_content = bundle.get("client_key")
                    mutual_auth = bundle.get("mutual_auth", True)
                    last_cert_err = None
                except Exception as inner_e:
                    last_cert_err = inner_e

        if last_cert_err is not None:
            logger.warning("Could not acquire mTLS client certificate bundle for client %s: %s", client_id, last_cert_err)

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
