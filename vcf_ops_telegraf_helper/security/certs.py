"""The certificate VCF Operations presents, and trusting it by its fingerprint.

A company that issues its own certificates makes verification against the operating system's
store fail. Instead of offering "do not verify", the app shows who the certificate was issued
to and by, and once the person trusts it, connects only to a server that presents that exact
certificate (its SHA-256 fingerprint is pinned). A different certificate later is refused and
shown again, so a changed certificate is never accepted silently.
"""

from __future__ import annotations

import hashlib
import socket
import ssl
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

from pydantic import BaseModel
from requests.adapters import HTTPAdapter


class PresentedCertificate(BaseModel):
    """What the person needs to see to decide whether to trust a certificate."""

    host: str
    issued_to: str
    issued_by: str
    valid_until: str
    sha256: str
    self_signed: bool = False


def normalize_fingerprint(value: Optional[str]) -> str:
    """Lower-case hex without separators, so 'AB:CD', 'ab cd' and 'abcd' compare equal."""
    return "".join(ch for ch in (value or "").lower() if ch in "0123456789abcdef")


def host_and_port(url: str) -> tuple[str, int]:
    parsed = urlparse(url if "://" in url else f"https://{url}")
    return parsed.hostname or url, parsed.port or 443


def _name(parts) -> str:
    from cryptography.x509.oid import NameOID

    for oid in (NameOID.COMMON_NAME, NameOID.ORGANIZATION_NAME, NameOID.ORGANIZATIONAL_UNIT_NAME):
        found = parts.get_attributes_for_oid(oid)
        if found:
            return str(found[0].value)
    return parts.rfc4514_string() or "(not stated)"


def describe_certificate(der: bytes, host: str) -> PresentedCertificate:
    from cryptography import x509

    cert = x509.load_der_x509_certificate(der)
    issued_to = _name(cert.subject)
    try:
        names = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        alternatives = [str(n.value) for n in names]
        if alternatives and issued_to not in alternatives:
            # The address a certificate is good for is listed in its alternative names
            issued_to = alternatives[0] if host not in alternatives else host
    except Exception:
        pass
    until: datetime = getattr(cert, "not_valid_after_utc", None) or cert.not_valid_after
    return PresentedCertificate(
        host=host,
        issued_to=issued_to,
        issued_by=_name(cert.issuer),
        valid_until=f"{until.strftime('%B')} {until.day}, {until.year}",
        sha256=hashlib.sha256(der).hexdigest(),
        self_signed=cert.issuer == cert.subject,
    )


def fetch_presented_certificate(url: str, timeout: float = 10.0) -> PresentedCertificate:
    """Read the certificate the server shows, without deciding whether to trust it."""
    host, port = host_and_port(url)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=timeout) as raw:
        with context.wrap_socket(raw, server_hostname=host) as tls:
            der = tls.getpeercert(binary_form=True)
    if not der:
        raise RuntimeError(f"{host} did not present a certificate")
    return describe_certificate(der, host)


class PinnedCertificateAdapter(HTTPAdapter):
    """Accept exactly the certificate with this SHA-256 fingerprint, and nothing else.

    The chain is not checked against a certificate authority: the fingerprint match is the
    trust decision the person made. A server presenting any other certificate fails.
    """

    def __init__(self, sha256: str, **kwargs):
        self.fingerprint = normalize_fingerprint(sha256)
        if len(self.fingerprint) != 64:
            raise ValueError("A SHA-256 certificate fingerprint is 64 hexadecimal characters")
        super().__init__(**kwargs)

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        pool_kwargs["assert_fingerprint"] = self.fingerprint
        return super().init_poolmanager(connections, maxsize, block, **pool_kwargs)

    def proxy_manager_for(self, proxy, **proxy_kwargs):
        # A connection made through a proxy is built by a different manager; it needs the pin as well
        proxy_kwargs["assert_fingerprint"] = self.fingerprint
        return super().proxy_manager_for(proxy, **proxy_kwargs)

    def get_connection_with_tls_context(self, request, verify, proxies=None, cert=None):
        conn = super().get_connection_with_tls_context(request, verify, proxies=proxies, cert=cert)
        if normalize_fingerprint(getattr(conn, "assert_fingerprint", None)) != self.fingerprint:
            # Never fall back to an unchecked connection: without the pin there is no check at all
            import requests

            raise requests.exceptions.SSLError("The trusted certificate could not be enforced on this connection")
        return conn

    def cert_verify(self, conn, url, verify, cert):
        # The pool asserts the fingerprint on every connection; no authority is consulted
        super().cert_verify(conn, url, False, cert)

    def build_connection_pool_key_attributes(self, request, verify, cert=None):
        return super().build_connection_pool_key_attributes(request, False, cert)
