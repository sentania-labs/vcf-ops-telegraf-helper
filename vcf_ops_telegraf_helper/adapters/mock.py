"""Mock VCF Operations adapter for testing and offline simulations."""

from __future__ import annotations

from typing import Optional

from vcf_ops_telegraf_helper.adapters.base import IntegrationArtifacts, VCFOpsIntegration
from vcf_ops_telegraf_helper.models.vcf import AuthToken, CollectorInfo, VCFEnvironment


class MockVCFOpsIntegration(VCFOpsIntegration):
    """Simulated VCF Operations 9.1 adapter for offline testing and development."""

    def __init__(
        self,
        env: VCFEnvironment,
        connected: bool = True,
        version: str = "9.1.0",
        ingestion_status: str = "PASS",
    ):
        self.env = env
        self.connected = connected
        self.version = version
        self.ingestion_status = ingestion_status

    def validate_connection(self) -> bool:
        return self.connected

    def detect_version(self) -> str:
        return self.version

    def acquire_token(self, username: str, password: str) -> AuthToken:
        return AuthToken(token="simulated-vcf-token-abc123xyz")

    def get_collector_information(self) -> CollectorInfo:
        return self.env.collector

    def prepare_telegraf_integration(
        self,
        os_family: str = "linux",
        target_ip: Optional[str] = None,
        target_hostname: Optional[str] = None,
        target_uuid: Optional[str] = None,
    ) -> IntegrationArtifacts:
        collector_addr = self.env.collector.address
        script_name = "telegraf-utils.ps1" if os_family.lower() == "windows" else "telegraf-utils.sh"
        return IntegrationArtifacts(
            token="simulated-vcf-token-abc123xyz",
            collector_address=collector_addr,
            script_url=f"https://{collector_addr}/downloads/salt/{script_name}",
            output_url=f"https://{collector_addr}/opensource/default/metric",
            skip_certificate=not self.env.verify_ssl,
            ca_cert_content="-----BEGIN CERTIFICATE-----\nSIMULATED CA CERT\n-----END CERTIFICATE-----\n",
            client_cert_content="-----BEGIN CERTIFICATE-----\nSIMULATED CLIENT CERT\n-----END CERTIFICATE-----\n",
            client_key_content="-----BEGIN RSA PRIVATE KEY-----\nSIMULATED KEY\n-----END RSA PRIVATE KEY-----\n",
            mandatory_tags_content="#!/bin/bash\necho 'mandatory.tag,value=1i'\n",
            is_managed_vm=False,
            client_id="simulated-client-id",
            mutual_auth=True,
            master_pub_content="ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQC simulated",
            vip_content=collector_addr,
            collector_group="default-collector-group",
        )

    def verify_ingestion(self, target_hostname: str) -> str:
        return self.ingestion_status
