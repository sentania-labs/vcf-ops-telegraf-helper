"""Base interface for VCF Operations integration adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional
from pydantic import BaseModel, Field

from vcf_ops_telegraf_helper.models.vcf import (
    AgentObjectInfo,
    AuthToken,
    CollectorInfo,
    VirtualMachineResource,
)


class IntegrationArtifacts(BaseModel):
    """Artifacts prepared for endpoint integration."""

    token: str = Field(description="Authentication token for collector bootstrap")
    collector_address: str = Field(description="Cloud Proxy or Collector destination")
    script_url: Optional[str] = Field(default=None, description="Download URL for helper script")
    output_url: str = Field(description="Metrics ingestion endpoint URL")
    skip_certificate: bool = Field(default=False)
    ca_cert_path: Optional[str] = Field(default=None)
    ca_cert_content: Optional[str] = Field(default=None, description="PEM content of intermediate CA cert")
    client_cert_content: Optional[str] = Field(default=None, description="PEM content of client certificate")
    client_key_content: Optional[str] = Field(default=None, description="PEM content of client private key")
    mandatory_tags_content: Optional[str] = Field(default=None, description="Shell/batch script content for mandatory tags")
    is_managed_vm: bool = Field(default=False, description="True if host is a registered vSphere VM in VCF Ops")
    vm_name: Optional[str] = Field(default=None, description="Discovered vSphere VM name")
    vm_mor: Optional[str] = Field(default=None, description="vCenter VM MOR (VMEntityObjectID), e.g. vm-31164")
    vc_id: Optional[str] = Field(default=None, description="vCenter Instance UUID (VMEntityVCID)")
    client_id: Optional[str] = Field(default=None, description="Client ID used in certificate request")
    mutual_auth: bool = Field(default=True, description="Whether mutual TLS is enforced on Cloud Proxy")
    master_pub_content: Optional[str] = Field(default=None, description="Content of master.pub public key")
    vip_content: Optional[str] = Field(default=None, description="VIP IP address from certificate bundle")
    collector_group: Optional[str] = Field(default=None, description="Resolved Collector Group name")


class VCFOpsIntegration(ABC):
    """Abstract boundary for VCF Operations release-specific integration logic."""

    # Set by list_virtual_machines when part of the inventory (such as agent status) could not be read
    inventory_warning: Optional[str] = None

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
    def prepare_telegraf_integration(
        self,
        os_family: str = "linux",
        target_ip: Optional[str] = None,
        target_hostname: Optional[str] = None,
        target_uuid: Optional[str] = None,
        existing_cert_bundle: Optional[dict[str, Any]] = None,
        vm_mor: Optional[str] = None,
        vc_id: Optional[str] = None,
    ) -> IntegrationArtifacts:
        """Prepare tokens, URLs, and artifacts for the open-source Telegraf workflow.

        When vm_mor is supplied the endpoint is bound to that vCenter VM instead of
        being matched by IP or hostname. vc_id is resolved from inventory if omitted.
        """
        pass

    @abstractmethod
    def verify_ingestion(
        self,
        target_hostname: str,
        since: Optional[float] = None,
        vc_id: Optional[str] = None,
        vm_mor: Optional[str] = None,
    ) -> str:
        """Check whether telemetry from the target is visible in VCF Operations.

        When vc_id and vm_mor are given the agent object bound to that VM is checked first;
        the hostname lookup is only a fallback, since a name can match an unrelated registration.

        Returns:
            One of: 'PASS', 'PENDING', 'UNKNOWN', 'FAIL'.
        """
        pass

    def get_agent_object(
        self, vc_id: str, vm_mor: str, include_stat_keys: bool = False
    ) -> Optional[AgentObjectInfo]:
        """Return the agent OS object bound to the VM with this vCenter id and MOR, or None.

        include_stat_keys also counts the object's stat keys (an extra query). Adapters that
        cannot read agent objects return None; callers treat that as unknown.
        """
        return None

    @abstractmethod
    def list_virtual_machines(self, strict: bool = False) -> list[VirtualMachineResource]:
        """Discover candidate virtual machines (no templates or deleted VMs) from VCF Operations inventory."""
        pass

    def list_auth_sources(self) -> list[str]:
        """Names of the non-local login sources the instance offers (empty when unknown)."""
        return []

    def verify_credentials(self) -> None:
        """Confirm the configured credentials are accepted; raise RuntimeError if not.

        validate_connection() only proves the API is reachable. Adapters that talk to a real
        instance override this.
        """
        return None

    @abstractmethod
    def list_collector_targets(self) -> list[CollectorInfo]:
        """List collector groups with cloud proxies, and the individual cloud proxies, an agent can report to."""
        pass

