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
