"""Adapters package."""

from vcf_ops_telegraf_helper.adapters.base import IntegrationArtifacts, VCFOpsIntegration
from vcf_ops_telegraf_helper.adapters.factory import get_adapter
from vcf_ops_telegraf_helper.adapters.mock import MockVCFOpsIntegration
from vcf_ops_telegraf_helper.adapters.vcf91 import VCF91OpenTelegrafIntegration

__all__ = [
    "IntegrationArtifacts",
    "MockVCFOpsIntegration",
    "VCFOpsIntegration",
    "VCF91OpenTelegrafIntegration",
    "get_adapter",
]
