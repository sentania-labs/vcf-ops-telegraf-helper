"""Base interface for VCF Operations integration adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional
from pydantic import BaseModel, Field

from vcf_ops_telegraf_helper.models.vcf import AuthToken, CollectorInfo


class IntegrationArtifacts(BaseModel):
    """Artifacts prepared for endpoint integration."""

    token: str = Field(description="Authentication token for collector bootstrap")
    collector_address: str = Field(description="Cloud Proxy or Collector destination")
    script_url: Optional[str] = Field(default=None, description="Download URL for helper script")
    output_url: str = Field(description="Metrics ingestion endpoint URL")
    skip_certificate: bool = Field(default=False)
    ca_cert_path: Optional[str] = Field(default=None)


class VCFOpsIntegration(ABC):
    """Abstract boundary for VCF Operations release-specific integration logic."""

    @abstractmethod
    def validate_connection(self) -> bool:
        """Validate API reachability and TLS connectivity to VCF Operations."""
        pass

    @abstractmethod
    def detect_version(self) -> str:
        """Detect the remote VCF Operations software release version."""
        pass

    @abstractmethod
    def acquire_token(self, username: str, password: str) -> AuthToken:
        """Obtain an authentication token from VCF Operations."""
        pass

    @abstractmethod
    def get_collector_information(self) -> CollectorInfo:
        """Retrieve details on the active Cloud Proxy or Collector Group."""
        pass

    @abstractmethod
    def prepare_telegraf_integration(self, os_family: str = "linux") -> IntegrationArtifacts:
        """Prepare tokens, URLs, and artifacts for the open-source Telegraf workflow."""
        pass

    @abstractmethod
    def verify_ingestion(self, target_hostname: str) -> str:
        """Check whether telemetry from the target is visible in VCF Operations.

        Returns:
            One of: 'PASS', 'UNKNOWN', 'FAIL'.
        """
        pass
