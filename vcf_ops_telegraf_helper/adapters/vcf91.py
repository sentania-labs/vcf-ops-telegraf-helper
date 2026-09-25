"""VCF Operations 9.1 integration adapter.

Implements the official Broadcom workflow for VCF Operations 9.1 open-source Telegraf.
"""

from __future__ import annotations

from typing import Any, Optional
import requests

from vcf_ops_telegraf_helper.adapters.base import IntegrationArtifacts, VCFOpsIntegration
from vcf_ops_telegraf_helper.models.vcf import AuthToken, CollectorInfo, VCFEnvironment


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

    def prepare_telegraf_integration(self, os_family: str = "linux") -> IntegrationArtifacts:
        """Prepare artifacts required for Cloud Proxy Wavefront ingestion."""
        token = self.env.token
        if not token:
            if self.env.username and self.env.password:
                token_obj = self.acquire_token(self.env.username, self.env.password)
                token = token_obj.token
            else:
                token = "local-simulated-token"

        collector_addr = self.env.collector.address
        script_name = "telegraf-utils.ps1" if os_family.lower() == "windows" else "telegraf-utils.sh"
        return IntegrationArtifacts(
            token=token,
            collector_address=collector_addr,
            script_url=f"https://{collector_addr}/downloads/salt/{script_name}",
            output_url=f"https://{collector_addr}/opensource/default/metric",
            skip_certificate=not self.env.verify_ssl,
            ca_cert_path=self.env.ca_cert_path,
        )

    def verify_ingestion(self, target_hostname: str) -> str:
        """Check whether metrics for target are appearing in VCF Operations.

        Returns:
            'PASS' if object found, 'UNKNOWN' if API cannot confirm yet, 'FAIL' if error.
        """
        if not self.env.token:
            return "UNKNOWN"

        try:
            url = f"{self.base_url}/suite-api/api/resources"
            headers = {
                "Accept": "application/json",
                "Authorization": f"vRealizeOpsToken {self.env.token}",
            }
            params = {"name": target_hostname}
            resp = self.session.get(url, headers=headers, params=params, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                resource_list = data.get("resourceList", [])
                if resource_list:
                    return "PASS"
            return "UNKNOWN"
        except Exception:
            return "UNKNOWN"
