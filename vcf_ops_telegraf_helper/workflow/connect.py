"""Connecting to VCF Operations, with the outcome named.

"Could not reach it", "it refused the sign-in" and "its certificate is not one this computer
knows" are three different problems with three different fixes, so they are three different
results rather than one False or one exception string.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Callable, Optional

from pydantic import BaseModel

from vcf_ops_telegraf_helper.adapters.base import CertificateNotTrusted, CredentialsRejected, VCFOpsIntegration
from vcf_ops_telegraf_helper.logger import get_logger
from vcf_ops_telegraf_helper.models.vcf import VCFEnvironment
from vcf_ops_telegraf_helper.security.certs import (
    PresentedCertificate,
    fetch_presented_certificate,
    normalize_fingerprint,
)

logger = get_logger("connect")


class ConnectOutcome(str, Enum):
    OK = "ok"
    UNREACHABLE = "unreachable"
    UNAUTHORIZED = "unauthorized"
    UNTRUSTED = "untrusted"


class ConnectResult(BaseModel):
    outcome: ConnectOutcome
    detail: str = ""
    certificate: Optional[PresentedCertificate] = None
    certificate_changed: bool = False  # the server no longer shows the certificate that was trusted

    @property
    def ok(self) -> bool:
        return self.outcome == ConnectOutcome.OK


def normalize_address(text: str) -> str:
    """Accept the address the way people type it: with or without https://, with a stray path or spaces."""
    value = (text or "").strip()
    value = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", "", value)
    value = re.split(r"[/?#]", value, maxsplit=1)[0].strip()
    return value


def address_to_url(text: str) -> str:
    host = normalize_address(text)
    return f"https://{host}" if host else ""


def connect(
    env: VCFEnvironment,
    adapter: VCFOpsIntegration,
    fetch_certificate: Callable[[str], PresentedCertificate] = fetch_presented_certificate,
) -> ConnectResult:
    """Reach VCF Operations and sign in, reporting which of the four outcomes happened."""
    try:
        reachable = adapter.validate_connection()
    except CertificateNotTrusted as exc:
        return _untrusted(env, fetch_certificate, str(exc))
    except Exception as exc:  # anything else at this point is the network
        logger.warning("Could not reach %s: %s", env.url, exc)
        return ConnectResult(outcome=ConnectOutcome.UNREACHABLE, detail=str(exc))
    if not reachable:
        return ConnectResult(outcome=ConnectOutcome.UNREACHABLE, detail="No answer from VCF Operations")
    try:
        adapter.verify_credentials()
    except CredentialsRejected as exc:
        return ConnectResult(outcome=ConnectOutcome.UNAUTHORIZED, detail=str(exc))
    except CertificateNotTrusted as exc:
        return _untrusted(env, fetch_certificate, str(exc))
    except Exception as exc:
        text = str(exc)
        if re.search(r"\b(401|403)\b|rejected the credentials|No API token", text):
            return ConnectResult(outcome=ConnectOutcome.UNAUTHORIZED, detail=text)
        logger.warning("Sign-in to %s did not complete: %s", env.url, exc)
        return ConnectResult(outcome=ConnectOutcome.UNREACHABLE, detail=text)
    return ConnectResult(outcome=ConnectOutcome.OK)


def _untrusted(env: VCFEnvironment, fetch_certificate, detail: str) -> ConnectResult:
    certificate = None
    try:
        certificate = fetch_certificate(env.url)
    except Exception as exc:
        logger.warning("Could not read the certificate of %s: %s", env.url, exc)
    pinned = normalize_fingerprint(env.pinned_cert_sha256)
    changed = bool(pinned and certificate is not None and normalize_fingerprint(certificate.sha256) != pinned)
    return ConnectResult(
        outcome=ConnectOutcome.UNTRUSTED, detail=detail, certificate=certificate, certificate_changed=changed
    )
