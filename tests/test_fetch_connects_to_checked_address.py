"""A fetch connects only to an address the destination check judged.

Property: for every fetch of a URL a user or an entry supplied (the web clipper
and the QA URL checker), the host is resolved once per hop, every resolved
address is judged by the public-destination rule, and the connection goes to an
address that was judged, on the first hop and on every redirect hop. The Host
header and TLS SNI / certificate verification keep using the URL's name.

The world here: a resolver that answers the check (lookups made by
``pyrite.utils.public_destination``) with a public address and answers every
other lookup with loopback, which is a DNS-rebinding host. The tests watch the
real ``socket.connect`` calls: a dial of a public address is carried to a local
server standing in for the public site (and recorded); a dial of loopback
reaches the "internal" server, which must never be hit.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import socket
import ssl
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import anyio
import httpx
import pytest

from pyrite.services.clipper import ClipperService
from pyrite.services.url_checker import URLChecker

PUBLIC = "93.184.216.34"
PUBLIC_2 = "93.184.216.35"
CHECKER = "pyrite.utils.public_destination"


# --- servers -----------------------------------------------------------------


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, title: str, redirect_to: str | None = None, tls=None):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.title = title
        self.redirect_to = redirect_to
        self.hits = 0
        self.hosts: list[str] = []
        self.sni: list[str | None] = []
        if tls is not None:
            tls.sni_callback = lambda _sock, name, _ctx: self.sni.append(name)
            self.socket = tls.wrap_socket(self.socket, server_side=True)
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()

    @property
    def port(self) -> int:
        return self.server_address[1]

    def stop(self) -> None:
        self.shutdown()
        self.server_close()
        self.thread.join(timeout=5)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _answer(self):
        self.server.hits += 1
        self.server.hosts.append(self.headers.get("Host", ""))
        if self.server.redirect_to:
            self.send_response(302)
            self.send_header("Location", self.server.redirect_to)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = f"<title>{self.server.title}</title><p>{self.server.title}</p>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    do_GET = do_HEAD = _answer  # noqa: N815


@pytest.fixture
def servers():
    made: list[_Server] = []

    def make(title, redirect_to=None, tls=None):
        made.append(_Server(title, redirect_to, tls))
        return made[-1]

    yield make
    for s in made:
        s.stop()


# --- the rebinding resolver and the dial recorder ------------------------------


@pytest.fixture
def world(monkeypatch):
    """`answers[name] = (to_the_check, to_anyone_else)`; `stand_ins[ip] = port`.

    Returns the list of (address, port) pairs dialled, recorded before a public
    address is carried to its stand-in.
    """
    answers: dict[str, tuple[str, str]] = {}
    stand_ins: dict[str, int] = {}
    dialled: list[tuple[str, int]] = []
    real_gai = socket.getaddrinfo
    real_connect = socket.socket.connect

    def getaddrinfo(host, port, *args, **kwargs):
        if isinstance(host, bytes):  # anyio passes the IDNA-encoded name
            host = host.decode("ascii")
        if host in answers:
            caller = sys._getframe(1).f_globals.get("__name__")
            addr = answers[host][0] if caller == CHECKER else answers[host][1]
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (addr, int(port or 0)))]
        return real_gai(host, port, *args, **kwargs)

    def connect(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6) and isinstance(address, tuple):
            dialled.append((address[0], address[1]))
            if address[0] in stand_ins:
                address = ("127.0.0.1", stand_ins[address[0]])
        return real_connect(self, address)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", connect)

    class World:
        pass

    w = World()
    w.answers, w.stand_ins, w.dialled = answers, stand_ins, dialled
    return w


def _clip(url):
    async def run():
        return await ClipperService(timeout=5).clip_url(url)

    return anyio.run(run)


# --- plain http ----------------------------------------------------------------


def test_the_clipper_connects_to_the_checked_address_not_a_later_answer(world, servers):
    public = servers("public")
    internal = servers("internal")
    world.answers["rebind.test"] = (PUBLIC, "127.0.0.1")
    world.stand_ins[PUBLIC] = public.port
    url = f"http://rebind.test:{internal.port}/page"

    result = _clip(url)

    assert ("127.0.0.1", internal.port) not in world.dialled
    assert internal.hits == 0
    assert (PUBLIC, internal.port) in world.dialled
    assert result.title == "public"
    # The Host header is the URL's name, not the pinned address.
    assert public.hosts == [f"rebind.test:{internal.port}"]


def test_the_clipper_connects_to_the_checked_address_on_a_redirect_hop(world, servers):
    internal = servers("internal")
    landed = servers("landed")
    first = servers("first", redirect_to=f"http://rebind.test:{internal.port}/next")
    world.answers["start.test"] = (PUBLIC, PUBLIC)  # an honest host
    world.answers["rebind.test"] = (PUBLIC_2, "127.0.0.1")
    world.stand_ins[PUBLIC] = first.port
    world.stand_ins[PUBLIC_2] = landed.port

    result = _clip(f"http://start.test:{internal.port}/page")

    assert first.hits == 1
    assert ("127.0.0.1", internal.port) not in world.dialled
    assert internal.hits == 0
    assert result.title == "landed"
    assert landed.hosts == [f"rebind.test:{internal.port}"]


def test_the_url_checker_connects_to_the_checked_address(world, servers):
    public = servers("public")
    internal = servers("internal")
    world.answers["rebind.test"] = (PUBLIC, "127.0.0.1")
    world.stand_ins[PUBLIC] = public.port

    result = URLChecker(db=None, timeout=5).check_url(f"http://rebind.test:{internal.port}/x")

    assert ("127.0.0.1", internal.port) not in world.dialled
    assert internal.hits == 0
    assert result.ok is True, result
    assert public.hosts == [f"rebind.test:{internal.port}"]


def test_the_url_checker_connects_to_the_checked_address_on_a_redirect_hop(world, servers):
    internal = servers("internal")
    landed = servers("landed")
    first = servers("first", redirect_to=f"http://rebind.test:{internal.port}/next")
    world.answers["start.test"] = (PUBLIC, PUBLIC)
    world.answers["rebind.test"] = (PUBLIC_2, "127.0.0.1")
    world.stand_ins[PUBLIC] = first.port
    world.stand_ins[PUBLIC_2] = landed.port

    result = URLChecker(db=None, timeout=5).check_url(f"http://start.test:{internal.port}/x")

    assert first.hits == 1
    assert ("127.0.0.1", internal.port) not in world.dialled
    assert internal.hits == 0
    assert result.ok is True, result
    assert landed.hosts == [f"rebind.test:{internal.port}"]


# --- https: verification stays on the URL's name ---------------------------------


@pytest.fixture
def pki(tmp_path, monkeypatch):
    """A throwaway CA the clients trust, and a factory of server contexts it signs."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    now = dt.datetime.now(dt.UTC)

    def name(cn):
        return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])

    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca = (
        x509.CertificateBuilder()
        .subject_name(name("pyrite test CA"))
        .issuer_name(name("pyrite test CA"))
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), False)
        .sign(ca_key, hashes.SHA256())
    )
    ca_path = tmp_path / "ca.pem"
    ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    # httpx builds its default context from certifi's bundle.
    import certifi

    monkeypatch.setattr(certifi, "where", lambda: str(ca_path))

    def server_context(*sans: x509.GeneralName) -> ssl.SSLContext:
        key = ec.generate_private_key(ec.SECP256R1())
        cert = (
            x509.CertificateBuilder()
            .subject_name(name("leaf"))
            .issuer_name(ca.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName(list(sans)), critical=False)
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), False
            )
            .sign(ca_key, hashes.SHA256())
        )
        n = len(list(tmp_path.glob("leaf*.pem")))
        cert_path, key_path = tmp_path / f"leaf{n}.pem", tmp_path / f"key{n}.pem"
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert_path, key_path)
        return ctx

    server_context.dns = lambda n: x509.DNSName(n)
    server_context.ip = lambda a: x509.IPAddress(ipaddress.ip_address(a))
    return server_context


def test_https_to_a_pinned_address_still_sends_and_verifies_the_urls_name(world, servers, pki):
    public = servers("public", tls=pki(pki.dns("rebind.test")))
    internal = servers("internal", tls=pki(pki.dns("rebind.test")))
    world.answers["rebind.test"] = (PUBLIC, "127.0.0.1")
    world.stand_ins[PUBLIC] = public.port

    result = _clip(f"https://rebind.test:{internal.port}/page")

    assert internal.hits == 0
    assert result.title == "public"
    assert public.sni == ["rebind.test"]


def test_https_to_a_pinned_address_does_not_verify_against_the_ip(world, servers, pki):
    # A certificate naming only the pinned IP: valid for the address dialled,
    # not for the name in the URL. Verification must refuse it.
    public = servers("public", tls=pki(pki.ip(PUBLIC)))
    internal = servers("internal", tls=pki(pki.dns("rebind.test")))
    world.answers["rebind.test"] = (PUBLIC, "127.0.0.1")
    world.stand_ins[PUBLIC] = public.port

    with pytest.raises(httpx.ConnectError, match="CERTIFICATE_VERIFY_FAILED"):
        _clip(f"https://rebind.test:{internal.port}/page")
    assert internal.hits == 0
    assert public.hits == 0
