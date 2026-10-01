"""Mock VCF Operations adapter for testing and offline simulations."""

from __future__ import annotations

from typing import Any, Optional

from vcf_ops_telegraf_helper.adapters.base import IntegrationArtifacts, VCFOpsIntegration
from vcf_ops_telegraf_helper.models.vcf import AuthToken, CollectorInfo, VCFEnvironment, VirtualMachineResource


MOCK_CERT_PEM = (
    "-----BEGIN CERTIFICATE-----\n"
    "MIICvjCCAaagAwIBAgIBATANBgkqhkiG9w0BAQsFADAYMRYwFAYDVQQDDA1tb2Nr\n"
    "LWNhLmxvY2FsMB4XDTIwMDEwMTAwMDAwMFoXDTM1MDEwMTAwMDAwMFowGDEWMBQG\n"
    "A1UEAwwNbW9jay1jYS5sb2NhbDCCASIwDQYJKoZIhvcNAQEBBQADggEPADCCAQoC\n"
    "ggEBAIX/jotZJgjhcP+1mQCBK3YzOeKjly8lNnwxIMiUMWpP58LSqux31QWF88gP\n"
    "ya69L9TFqChkkonsMkuuxhBAKhlTI8P9bXbNLRCvps0eN3s6CxbVdKZVRB+gfKRG\n"
    "SElvbdaC6Q6yJsrYU8BYslo4tByhIebP13o9hyqwdrw2hKo9Gr5Rh/1YrBpGFh2W\n"
    "Qw1sviAQSJ0SSdaAFzufl8zg8r3LVyWIWcYEQ0iftjeIEdtou/YMOXcNqoMgIiBb\n"
    "8TrmMkcy/ThAJqikAh2X29uX0LwKIuc6TbsNKSxd40OBg7M4FzabkDWKiCEFOY0d\n"
    "0vo7TRyD1T9yfFct16LO9bAJBesCAwEAAaMTMBEwDwYDVR0TAQH/BAUwAwEB/zAN\n"
    "BgkqhkiG9w0BAQsFAAOCAQEANzC7OdrCeP7T+njPfnUgBzimaYUNJGrjRXtXaXzk\n"
    "Hluy2LdzJysXGdidD0MuMCY4yImwjlNAZMdmcbuR1dumScC9q3r6Nba+haYvzUvd\n"
    "+Jx+/L5REZBk1nRt9RLRX9jfcoCHhU/vsWAjhBWjrJusuHjatvuFv+o+7tqoXR1W\n"
    "WjPCtrPGUGWboY4gpTz8+rRpQ3FfEErUiBuKRCRdm9OPx61qtGS/u0AWdLP7ns6s\n"
    "N+moUQEmpZc2ZyQUUIqBr+Sq8DEWfPqspTNY2msEi2d5izcJ4GZbDLvuToNB4d40\n"
    "4vh7MjWjMeFMHIE098sxmTQMO9dohvbDiHDoJ8wZlkYRDA==\n"
    "-----END CERTIFICATE-----\n"
)

MOCK_KEY_PEM = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIEowIBAAKCAQEAhf+Oi1kmCOFw/7WZAIErdjM54qOXLyU2fDEgyJQxak/nwtKq\n"
    "7HfVBYXzyA/Jrr0v1MWoKGSSiewyS67GEEAqGVMjw/1tds0tEK+mzR43ezoLFtV0\n"
    "plVEH6B8pEZISW9t1oLpDrImythTwFiyWji0HKEh5s/Xej2HKrB2vDaEqj0avlGH\n"
    "/VisGkYWHZZDDWy+IBBInRJJ1oAXO5+XzODyvctXJYhZxgRDSJ+2N4gR22i79gw5\n"
    "dw2qgyAiIFvxOuYyRzL9OEAmqKQCHZfb25fQvAoi5zpNuw0pLF3jQ4GDszgXNpuQ\n"
    "NYqIIQU5jR3S+jtNHIPVP3J8Vy3Xos71sAkF6wIDAQABAoIBABIuRmzpv5tc2zQW\n"
    "s5e57ueus5/oik6/QdE/6S7NzJacGNn6M266I5EIR7dRTRAEY0T/PH2eh7Nm9LwI\n"
    "Dp+N1Shye1vQOtXvqLmm237hJq31hiOm+pjG4ONZpw+y6YPtNn3wbSatTU4gY9yp\n"
    "LCnJn8ZypmLmuFnBl2FXaATJcN6YEZCiXKKEKBYJ5muIwKog/RdR3tJlsarqaGN4\n"
    "Hu98YIqGFrRIKvpX77kLCFMqwGturLAit4VCSfORprbLxS886iGUtw7EJ1nr4aoX\n"
    "xRbyhtzxXCLRp9zDS8+gKbEQ+w83DR3PIo/+sZ/RJqvc74l8D/8INdc+TALP2I0E\n"
    "mohvByECgYEAvEu0UYTElBZw4PYzl2DXmW9bwd7t1bdlh3orHlZPqL6jL04w9F2X\n"
    "jSS2OObYl0U8is0BM/y+MLXBfAajsp83PfLxq1OUGJMabHPZ0Vy8Tbq8iN55WrM1\n"
    "pm6inSKFEiz4RpG0ROJ7KvXvUD28mwnmKtpZIXJL7fGDwqGjoadijS8CgYEAti3d\n"
    "qmlayNZJnvG+rW8KKwPhYICEYTe+6hoc/8Aso4HnDheiCnl/4jfe2v3ph/9W6a4v\n"
    "Tn1eH7Ggr0zk7VV9oGvH7FZbf1duTb8e6fE42qgpwYdgtruD42QjVyMY4mASFkp+\n"
    "8ugn2KmQ8ghz5ENfzRD81/oskOYSUeYwSwV+/AUCgYB6GtG1J1be/Wp3x9CO8wL9\n"
    "AgTLxQgQVlylrSi3BJulvvJNo/QFE4hKxCrS3YhJGGH5VJXaI6UmK0dsaVXQaIVH\n"
    "S/tB8fIQuZwiBkKTDQMjmNvYGgUyNxKsegRDx/XpYnYiNSxkm0XqBxAIxfA/zfyP\n"
    "f4bbNKZeiAa8uVtGYih7iwKBgBsDlhkM4k9hpy0Qf8vL6WAThToAFKEt2Ptxv9cU\n"
    "sgnU22Q1kOuotJPg4QTsHdLyw/qGv7EN2gUtG7yi1Fd1E9nT4aNj8tFhL5QLwRPD\n"
    "l0ClKvvtjSPLjnULhkoHhEsdH9F6XnS6hB4Wls2s/zJb4zrPSA7mo/EgjJrkXUji\n"
    "mb/ZAoGBAIvur8a6e7sXtKjebX2dRfJnAj0oGwr5Hvzi1vxdCO64N+OidV9nVnGi\n"
    "J3ppFhKa2EyM18uoDZ8Cf4F83c3DNkiGEAvTLHXYDjR+9nnR7Pn2g7dNv7qK+ioj\n"
    "Sk5aWe8OikFFfA20MxEhKbX1YXvrca0i5vV6i8dLfgZuwc0cgSlV\n"
    "-----END RSA PRIVATE KEY-----\n"
)


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
        existing_cert_bundle: Optional[dict[str, Any]] = None,
    ) -> IntegrationArtifacts:
        collector_addr = self.env.collector.address
        script_name = "telegraf-utils.ps1" if os_family.lower() == "windows" else "telegraf-utils.sh"
        win_tags = (
            "@echo off\r\n"
            "echo mandatory.tag,OS_NAME=Windows,OS_VERSION=unknown,TELEGRAF_VERSION=1.40.1,HOSTNAME=%COMPUTERNAME%,IP=127.0.0.1 value=1i\r\n"
        )
        linux_tags = (
            "#!/usr/bin/env bash\n"
            "echo 'mandatory.tag,OS_NAME=Linux,OS_VERSION=unknown,TELEGRAF_VERSION=1.40.1,HOSTNAME=localhost,IP=127.0.0.1 value=1i'\n"
        )
        return IntegrationArtifacts(
            token="simulated-vcf-token-abc123xyz",
            collector_address=collector_addr,
            script_url=f"https://{collector_addr}/downloads/salt/{script_name}",
            output_url=f"https://{collector_addr}/opensource/default/metric",
            skip_certificate=not self.env.verify_ssl,
            ca_cert_content=MOCK_CERT_PEM,
            client_cert_content=MOCK_CERT_PEM,
            client_key_content=MOCK_KEY_PEM,
            mandatory_tags_content=win_tags if os_family.lower() == "windows" else linux_tags,
            is_managed_vm=False,
            client_id="simulated-client-id",
            mutual_auth=True,
            master_pub_content="ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQC simulated",
            vip_content=collector_addr,
            collector_group="default-collector-group",
        )

    def verify_ingestion(self, target_hostname: str) -> str:
        return self.ingestion_status

    def list_virtual_machines(self) -> list[VirtualMachineResource]:
        """Return simulated virtual machine inventory for tests and offline usage."""
        if hasattr(self, "_vms") and self._vms is not None:
            return self._vms

        return [
            VirtualMachineResource(
                resource_id="res-vm-001",
                name="dbdemo01",
                ip_address="172.17.0.2",
                vm_mor="vm-1001",
                vc_id="423b-81f0-91a2-0001",
                os_name="Windows Server 2022 Datacenter",
                os_family="WINDOWS",
                collector_group="Default Collector Group",
                telegraf_status="MISSING",
            ),
            VirtualMachineResource(
                resource_id="res-vm-002",
                name="mssqldemo2",
                ip_address="172.16.3.80",
                vm_mor="vm-1042",
                vc_id="423b-81f0-91a2-0002",
                os_name="Windows Server 2025 Standard",
                os_family="WINDOWS",
                collector_group="Default Collector Group",
                telegraf_status="MISSING",
            ),
            VirtualMachineResource(
                resource_id="res-vm-003",
                name="oraclesrv01",
                ip_address="172.18.2.14",
                vm_mor="vm-1004",
                vc_id="423b-81f0-91a2-0003",
                os_name="Red Hat Enterprise Linux 9.4",
                os_family="LINUX",
                collector_group="DMZ Collector Group",
                telegraf_status="MISSING",
            ),
            VirtualMachineResource(
                resource_id="res-vm-004",
                name="webapp01",
                ip_address="172.16.10.5",
                vm_mor="vm-1020",
                vc_id="423b-81f0-91a2-0004",
                os_name="Ubuntu 24.04 LTS",
                os_family="LINUX",
                collector_group="Default Collector Group",
                telegraf_status="ACTIVE",
                telegraf_version="1.40.1",
            ),
            VirtualMachineResource(
                resource_id="res-vm-005",
                name="k8s-node01",
                ip_address="172.19.1.50",
                vm_mor="vm-2005",
                vc_id="423b-81f0-91a2-0005",
                os_name="Ubuntu 22.04 LTS",
                os_family="LINUX",
                collector_group="PCI Cluster Group",
                telegraf_status="STOPPED",
            ),
        ]

