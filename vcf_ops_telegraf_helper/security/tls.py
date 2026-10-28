"""Requests transport using the desktop's native certificate store."""
import ssl

from requests.adapters import HTTPAdapter
import truststore


class NativeTrustAdapter(HTTPAdapter):
    """Use native trust for default verification; respect explicit bundle/verify overrides."""

    def build_connection_pool_key_attributes(self, request, verify, cert=None):
        host, tls = super().build_connection_pool_key_attributes(request, verify, cert)
        if verify is True:
            tls['ssl_context'] = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        return host, tls


_unverified_hosts: set = set()


def note_unverified_host(url: str) -> None:
    """Record once per host that certificate verification is deliberately off.

    Replaces urllib3's InsecureRequestWarning, which otherwise repeats for every request.
    """
    import urllib3
    from urllib.parse import urlparse
    from vcf_ops_telegraf_helper.logger import get_logger

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    host = urlparse(url).hostname or url
    if host not in _unverified_hosts:
        _unverified_hosts.add(host)
        get_logger("tls").warning("Certificate verification is disabled for %s", host)
