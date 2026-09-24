"""Factory to instantiate the appropriate VCF Operations adapter."""

from __future__ import annotations

from typing import Optional
import requests

from vcf_ops_telegraf_helper.adapters.base import VCFOpsIntegration
from vcf_ops_telegraf_helper.adapters.vcf91 import VCF91OpenTelegrafIntegration
from vcf_ops_telegraf_helper.models.vcf import VCFEnvironment


def get_adapter(
    env: VCFEnvironment,
    session: Optional[requests.Session] = None,
) -> VCFOpsIntegration:
    """Instantiate the VCF Operations adapter corresponding to the detected or specified release."""
    # VCF 9.1 is our primary supported version for this release
    return VCF91OpenTelegrafIntegration(env, session=session)
