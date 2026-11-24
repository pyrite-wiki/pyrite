"""Which destinations Pyrite may contact on a user's behalf, and how it connects.

One rule for every fetch of a URL a user or an entry supplied (the web
clipper, the QA URL checker): http(s) only, and every address the host
resolves to must be globally routable.

One mechanism makes the connection go where the rule looked. For each hop,
``check_url_safe`` resolves the host once, judges every address, and returns
the addresses it judged; the caller records them in a ``PinnedAddresses`` for
that fetch; and the connection step (``pinned_async_transport`` for httpx,
``pinned_url_handlers`` for urllib) dials only one of those recorded
addresses, never resolving the name again. The URL is unchanged, so the HTTP
Host header, TLS SNI and certificate verification still use the URL's name.
A host with no recorded addresses is refused, not resolved.
"""

import functools
import http.client
import ipaddress
import socket
import ssl
import urllib.request
from urllib.parse import urlparse

import httpcore
import httpx

from ..exceptions import ClipperBlockedHostError, outside_text


def check_url_shape(url: str) -> str:
    """The URL's host, if the URL is one the rule could allow; no name lookup.

    Raises ClipperBlockedHostError if the scheme is not http(s) or the URL has
    no host. ``check_url_safe`` applies this first.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ClipperBlockedHostError(
            f"Refusing scheme {parsed.scheme!r}; only http(s) is permitted"
        )
    host = parsed.hostname
    if not host:
        raise ClipperBlockedHostError(f"URL {url!r} has no host")
    return host


def check_url_safe(url: str) -> tuple[str, ...]:
    """Judge one URL's destination and return the addresses it judged.

    This validates the URL it is handed and nothing else: it does not see
    redirects. The fetch paths apply it to every hop (the clipper's request
    hook, #219; the URL checker's redirect handler) and record the result in
    the fetch's ``PinnedAddresses``, so the connection for that hop goes to
    one of the returned addresses and the name is not resolved a second time
    between the check and the connect (DNS rebinding).

    Raises ClipperBlockedHostError if:
      - the scheme is not http(s);
      - the URL has no host;
      - the host resolves to a loopback, link-local, private, reserved,
        unspecified, or multicast IPv4/IPv6 address.

    All resolved IPs for the host are checked. If ANY resolved IP is
    blocked, the URL is rejected — that prevents the trivial DNS-mixed
    case where a host returns both a public and a private A record.
    """
    host = check_url_shape(url)

    # Try parsing the host directly as an IP literal first; that catches
    # http://127.0.0.1/ and http://[::1]/ without a DNS lookup.
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        pass  # not an IP literal — resolve via DNS
    else:
        _reject_if_blocked(ip, host)
        return (str(ip),)

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise ClipperBlockedHostError(
            f"Could not resolve host {host!r}: {outside_text(exc)}"
        ) from exc

    judged: list[str] = []
    for info in infos:
        addr = str(info[4][0])
        if addr in judged:
            continue
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError as exc:
            # An address this check cannot read (a scoped IPv6 address, say)
            # is one it cannot vouch for: refused.
            raise ClipperBlockedHostError(
                f"Refusing to fetch {host!r}: a resolved address cannot be checked"
            ) from exc
        _reject_if_blocked(ip, host)
        judged.append(addr)
    if not judged:
        raise ClipperBlockedHostError(f"Refusing to fetch {host!r}: it resolved to no address")
    return tuple(judged)


class PinnedAddresses:
    """The addresses ``check_url_safe`` judged, per host, for one fetch.

    The fetch records each hop's result before that hop connects; the
    connection step asks for a host's addresses here and dials one of them.
    One instance per fetch: it is not shared between callers.
    """

    def __init__(self) -> None:
        self._by_host: dict[str, tuple[str, ...]] = {}

    def record(self, url: str, addresses: tuple[str, ...] | None) -> None:
        host = urlparse(url).hostname
        if host:
            self._by_host[host.lower()] = tuple(addresses or ())

    def addresses(self, host: str) -> tuple[str, ...]:
        """The judged addresses for ``host``; refused if none were recorded."""
        judged = self._by_host.get(host.lower().strip("[]"), ())
        if not judged:
            raise ClipperBlockedHostError(
                f"Refusing to connect to {host!r}: its address was not checked"
            )
        return judged

    def connect(self, host: str, port: int, timeout: object = None) -> socket.socket:
        """A TCP connection to one of ``host``'s judged addresses.

        The socket is made for the address itself (no ``getaddrinfo``), so no
        lookup happens here at all.
        """
        last: OSError | None = None
        for addr in self.addresses(host):
            family = socket.AF_INET6 if ipaddress.ip_address(addr).version == 6 else socket.AF_INET
            sock = socket.socket(family, socket.SOCK_STREAM)
            try:
                if isinstance(timeout, int | float):
                    sock.settimeout(timeout)
                sock.connect((addr, port))
            except OSError as exc:
                sock.close()
                last = exc
                continue
            return sock
        assert last is not None  # addresses() never returns an empty tuple
        raise last


# --- httpx: the connection pool dials a pinned address --------------------------


class _PinnedAsyncBackend(httpcore.AnyIOBackend):
    """httpcore's own TCP dial, handed an address from the pins instead of the
    name. anyio does not resolve an IP literal. TLS is started afterwards by
    httpcore with ``server_hostname`` set to the URL's host, so SNI and
    certificate verification are against the name, not the address."""

    def __init__(self, pins: PinnedAddresses) -> None:
        self._pins = pins

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):  # noqa: ANN001, ANN201
        last: httpcore.ConnectError | None = None
        for addr in self._pins.addresses(host):
            try:
                return await super().connect_tcp(addr, port, timeout, local_address, socket_options)
            except httpcore.ConnectError as exc:
                last = exc
        assert last is not None
        raise last


def pinned_async_transport(pins: PinnedAddresses) -> httpx.AsyncHTTPTransport:
    """An httpx transport whose connections go only to addresses in ``pins``.

    No proxy, no environment (ADR-0052 N2), default certificate verification.
    """
    transport = httpx.AsyncHTTPTransport(trust_env=False)
    # httpx takes no network backend; the pool it built is replaced by one with
    # the same settings and the pinned dial (encode/httpx discussion 2470).
    # ``_pool`` is httpx's own attribute: if a release renames it, assigning it
    # would leave the unpinned pool in use, so that is refused, not tolerated.
    if not isinstance(getattr(transport, "_pool", None), httpcore.AsyncConnectionPool):
        raise RuntimeError("httpx.AsyncHTTPTransport no longer keeps its pool in _pool")
    transport._pool = httpcore.AsyncConnectionPool(
        ssl_context=httpx.create_ssl_context(trust_env=False),
        max_connections=100,
        max_keepalive_connections=20,
        keepalive_expiry=5.0,
        http1=True,
        http2=False,
        network_backend=_PinnedAsyncBackend(pins),
    )
    return transport


# --- urllib: the HTTP(S) connection dials a pinned address ----------------------


class _PinnedDial(http.client.HTTPConnection):
    """``connect()`` (public ``http.client`` API) opens the socket to a pinned
    address instead of resolving ``self.host``. ``HTTPSConnection.connect``
    calls this through ``super()`` and then wraps the socket with
    ``server_hostname=self.host``, the URL's name: SNI and certificate
    verification stay on the name."""

    _pins: PinnedAddresses

    def connect(self) -> None:
        sock = self._pins.connect(self.host, self.port, self.timeout)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)  # as http.client does
        self.sock = sock


class _PinnedHTTPConnection(_PinnedDial):
    def __init__(self, host: str, *, pins: PinnedAddresses, timeout: float | None = None) -> None:
        super().__init__(host, timeout=timeout)
        self._pins = pins


class _PinnedHTTPSConnection(http.client.HTTPSConnection, _PinnedDial):
    def __init__(
        self,
        host: str,
        *,
        pins: PinnedAddresses,
        context: ssl.SSLContext,
        timeout: float | None = None,
    ) -> None:
        super().__init__(host, timeout=timeout, context=context)
        self._pins = pins


class _PinnedHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, pins: PinnedAddresses) -> None:
        super().__init__()
        self._pins = pins

    def http_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        return self.do_open(functools.partial(_PinnedHTTPConnection, pins=self._pins), req)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, pins: PinnedAddresses) -> None:
        super().__init__()
        self._pins = pins
        # Its own default context (verification on, hostname checked), not
        # urllib's process-wide default hook, which a PEP 476 opt-out can
        # replace with one that does not verify.
        self._tls = ssl.create_default_context()

    def https_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        return self.do_open(
            functools.partial(_PinnedHTTPSConnection, pins=self._pins, context=self._tls), req
        )


def pinned_url_handlers(pins: PinnedAddresses) -> tuple[urllib.request.BaseHandler, ...]:
    """urllib handlers (for ``build_opener``) that dial only addresses in ``pins``."""
    return (_PinnedHTTPHandler(pins), _PinnedHTTPSHandler(pins))


#: Prefixes that carry an IPv4 address inside an IPv6 one. The stdlib does not
#: judge the embedded address, so these are refused outright (NAT64 and 6to4
#: translate to whatever IPv4 host they embed).
_EMBEDDING_NETWORKS = (
    ipaddress.ip_network("64:ff9b::/96"),
    ipaddress.ip_network("64:ff9b:1::/48"),
    ipaddress.ip_network("2002::/16"),
)


def _reject_if_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address, host: str) -> None:
    """Raise ClipperBlockedHostError unless `ip` is a globally routable address.

    An allowlist, not a blocklist: ``is_global`` is false for loopback,
    link-local, private, unspecified, reserved and the special-purpose ranges
    a blocklist forgets (carrier-grade NAT 100.64.0.0/10, 192.0.0.0/24, the
    benchmarking and documentation ranges). Multicast is refused too. An IPv4
    address mapped into IPv6 is judged as the IPv4 address it is.
    """
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    embedded = isinstance(ip, ipaddress.IPv6Address) and any(
        ip in net for net in _EMBEDDING_NETWORKS
    )
    if embedded or not ip.is_global or ip.is_multicast:
        raise ClipperBlockedHostError(
            f"Refusing to fetch {host!r}: resolved IP {ip} is not a public address"
        )
