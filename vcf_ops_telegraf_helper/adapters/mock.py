"""Mock VCF Operations adapter for testing and offline simulations."""

from __future__ import annotations

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

    def prepare_telegraf_integration(self) -> IntegrationArtifacts:
        collector_addr = self.env.collector.address
        return IntegrationArtifacts(
            token="simulated-vcf-token-abc123xyz",
            collector_address=collector_addr,
            script_url=f"https://{collector_addr}/downloads/salt/telegraf-utils.sh",
            output_url=f"https://{collector_addr}/opensource/default/metric",
            skip_certificate=not self.env.verify_ssl,
        )

    def verify_ingestion(self, target_hostname: str) -> str:
        return self.ingestion_status
