"""Connecting to VCF Operations: the named outcomes, and trusting a certificate by its fingerprint."""

from __future__ import annotations

import datetime
import http.server
import ssl
import threading

import pytest
import requests

from vcf_ops_telegraf_helper.adapters.base import CertificateNotTrusted, CredentialsRejected
from vcf_ops_telegraf_helper.adapters.mock import MockVCFOpsIntegration
from vcf_ops_telegraf_helper.adapters.vcf91 import VCF91OpenTelegrafIntegration
from vcf_ops_telegraf_helper.models.vcf import CollectorInfo, VCFEnvironment
from vcf_ops_telegraf_helper.security.certs import (
    PinnedCertificateAdapter,
    PresentedCertificate,
    fetch_presented_certificate,
    normalize_fingerprint,
)
from vcf_ops_telegraf_helper.storage.state import StateStore
from vcf_ops_telegraf_helper.workflow.connect import ConnectOutcome, address_to_url, connect, normalize_address


def _env(**more):
    values = dict(url="https://ops.example.com", username="admin", password="pw", collector=CollectorInfo(address=""))
    values.update(more)
    return VCFEnvironment(**values)


@pytest.mark.parametrize("typed,expected", [
    ("ops.example.com", "ops.example.com"),
    ("  https://ops.example.com/ui/login?next=1  ", "ops.example.com"),
    ("HTTP://ops.example.com:8443/", "ops.example.com:8443"),
    ("ops.example.com#frag", "ops.example.com"),
    ("", ""),
])
def test_address_is_accepted_the_way_people_type_it(typed, expected):
    assert normalize_address(typed) == expected
    assert address_to_url(typed) == (f"https://{expected}" if expected else "")


class _Ops(MockVCFOpsIntegration):
    def __init__(self, env, reach=True, signin=None):
        super().__init__(env)
        self._reach, self._signin = reach, signin

    def validate_connection(self):
        if isinstance(self._reach, Exception):
            raise self._reach
        return self._reach

    def verify_credentials(self):
        if self._signin is not None:
            raise self._signin


def _cert(url):
    return PresentedCertificate(host="ops.example.com", issued_to="ops.example.com", issued_by="Synthetic CA",
                                valid_until="March 4, 2028", sha256="ab" * 32)


def test_the_four_outcomes_are_told_apart():
    env = _env()
    assert connect(env, _Ops(env), _cert).outcome == ConnectOutcome.OK
    assert connect(env, _Ops(env, reach=False), _cert).outcome == ConnectOutcome.UNREACHABLE
    assert connect(env, _Ops(env, reach=OSError("no route to host")), _cert).outcome == ConnectOutcome.UNREACHABLE
    refused = connect(env, _Ops(env, signin=CredentialsRejected("HTTP 401")), _cert)
    assert refused.outcome == ConnectOutcome.UNAUTHORIZED and not refused.ok
    assert connect(env, _Ops(env, signin=RuntimeError("Authentication failed with status code 401")), _cert).outcome \
        == ConnectOutcome.UNAUTHORIZED
    assert connect(env, _Ops(env, signin=RuntimeError("Failed to acquire token: timed out")), _cert).outcome \
        == ConnectOutcome.UNREACHABLE
    untrusted = connect(env, _Ops(env, reach=CertificateNotTrusted("x")), _cert)
    assert untrusted.outcome == ConnectOutcome.UNTRUSTED and untrusted.certificate.issued_by == "Synthetic CA"
    assert not untrusted.certificate_changed


def test_a_certificate_that_is_no_longer_the_trusted_one_is_flagged():
    env = _env(pinned_cert_sha256="cd" * 32)
    changed = connect(env, _Ops(env, reach=CertificateNotTrusted("x")), _cert)
    assert changed.outcome == ConnectOutcome.UNTRUSTED and changed.certificate_changed

    def unreadable(url):
        raise OSError("connection reset")

    blind = connect(env, _Ops(env, reach=CertificateNotTrusted("x")), unreadable)
    assert blind.outcome == ConnectOutcome.UNTRUSTED and blind.certificate is None and not blind.certificate_changed


def test_fingerprints_compare_however_they_are_written():
    assert normalize_fingerprint("AB:CD ef") == "abcdef" and normalize_fingerprint(None) == ""
    with pytest.raises(ValueError):
        PinnedCertificateAdapter("abcd")


def test_the_pin_holds_through_a_proxy_and_is_never_silently_dropped():
    adapter = PinnedCertificateAdapter("ab" * 32)
    request = requests.Request("GET", "https://ops.example.com/suite-api/api/versions").prepare()
    direct = adapter.get_connection_with_tls_context(request, verify=True)
    proxied = adapter.get_connection_with_tls_context(request, verify=True, proxies={"https": "http://proxy.example.com:3128"})
    assert direct.assert_fingerprint == "ab" * 32
    assert proxied.proxy is not None and proxied.assert_fingerprint == "ab" * 32, "a proxy must not remove the check"

    class Forgetful(PinnedCertificateAdapter):
        def proxy_manager_for(self, proxy, **proxy_kwargs):
            return requests.adapters.HTTPAdapter.proxy_manager_for(self, proxy, **proxy_kwargs)

    with pytest.raises(requests.exceptions.SSLError, match="could not be enforced"):
        Forgetful("ab" * 32).get_connection_with_tls_context(request, verify=True, proxies={"https": "http://p.example.com:1"})


def test_trusted_certificates_are_remembered_per_host(tmp_path):
    store = StateStore(state_file=tmp_path / "state.json")
    assert store.trusted_certificate("ops.example.com") is None
    store.trust_certificate("Ops.Example.com", {"sha256": "ab" * 32, "issued_by": "Synthetic CA"})
    assert StateStore(state_file=tmp_path / "state.json").trusted_certificate("ops.example.com")["sha256"] == "ab" * 32
    store.forget_certificate("ops.example.com")
    assert store.trusted_certificate("ops.example.com") is None


# ---------------------------------------------------------------------------------------------
# Against a real TLS server on this computer, presenting a certificate nothing here trusts
# ---------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def tls_server(tmp_path_factory):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    folder = tmp_path_factory.mktemp("tls")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Synthetic Corp Internal CA")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(datetime.datetime(2028, 3, 4, tzinfo=datetime.timezone.utc))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
            .sign(key, hashes.SHA256()))
    (folder / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (folder / "key.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                                       serialization.NoEncryption()))

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = b'{"releaseName": "9.1.0"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(folder / "cert.pem"), str(folder / "key.pem"))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"https://localhost:{server.server_address[1]}"
    server.shutdown()


def test_presented_certificate_is_described_for_a_person(tls_server):
    shown = fetch_presented_certificate(tls_server)
    assert shown.issued_to == "localhost" and shown.issued_by == "Synthetic Corp Internal CA"
    assert shown.valid_until == "March 4, 2028" and len(shown.sha256) == 64 and not shown.self_signed


def test_only_the_pinned_certificate_is_accepted(tls_server):
    shown = fetch_presented_certificate(tls_server)
    with pytest.raises(requests.exceptions.SSLError):
        requests.get(tls_server, timeout=5)
    pinned = requests.Session()
    pinned.mount(tls_server + "/", PinnedCertificateAdapter(shown.sha256))
    assert pinned.get(tls_server + "/suite-api/api/versions", timeout=5).status_code == 200
    other = requests.Session()
    other.mount(tls_server + "/", PinnedCertificateAdapter("00" * 32))
    with pytest.raises(requests.exceptions.SSLError):
        other.get(tls_server, timeout=5)


def test_the_adapter_refuses_an_unknown_certificate_and_accepts_the_trusted_one(tls_server):
    shown = fetch_presented_certificate(tls_server)
    with pytest.raises(CertificateNotTrusted):
        VCF91OpenTelegrafIntegration(_env(url=tls_server)).validate_connection()
    assert VCF91OpenTelegrafIntegration(_env(url=tls_server, pinned_cert_sha256=shown.sha256)).validate_connection()
    with pytest.raises(CertificateNotTrusted):
        VCF91OpenTelegrafIntegration(_env(url=tls_server, pinned_cert_sha256="11" * 32)).validate_connection()
    result = connect(_env(url=tls_server), VCF91OpenTelegrafIntegration(_env(url=tls_server)))
    assert result.outcome == ConnectOutcome.UNTRUSTED and result.certificate.sha256 == shown.sha256
    # only VCF Operations is pinned: any other host keeps ordinary verification
    adapter = VCF91OpenTelegrafIntegration(_env(url=tls_server, pinned_cert_sha256=shown.sha256))
    assert type(adapter.session.get_adapter("https://dl.example.invalid/")).__name__ == "NativeTrustAdapter"


def test_saving_the_environment_keeps_the_fingerprint_and_never_the_password(tmp_path):
    store = StateStore(state_file=tmp_path / "state.json")
    store.save_environment(_env(password="ops-secret-value", pinned_cert_sha256="ab" * 32, token="session-secret-value"))
    saved = (tmp_path / "state.json").read_text()
    assert "ab" * 32 in saved and "ops-secret-value" not in saved and "session-secret-value" not in saved
