"""Which destinations Pyrite may contact on a user's behalf.

One rule for every fetch of a URL a user or an entry supplied (the web
clipper, the QA URL checker): http(s) only, and every address the host
resolves to must be globally routable.
"""

import ipaddress
import socket
from urllib.parse import urlparse

from ..exceptions import ClipperBlockedHostError, outside_text


def check_url_safe(url: str) -> None:
    """Reject one URL that would let the clipper act as an SSRF gadget.

    This validates the URL it is handed and nothing else: it does not see
    redirects. The fetch path applies it to every hop through
    ``_reject_blocked_request`` (#219).

    Raises ClipperBlockedHostError if:
      - the scheme is not http(s);
      - the URL has no host;
      - the host resolves to a loopback, link-local, private, reserved,
        unspecified, or multicast IPv4/IPv6 address.

    All resolved IPs for the host are checked. If ANY resolved IP is
    blocked, the URL is rejected — that prevents the trivial DNS-mixed
    case where a host returns both a public and a private A record.
    (Full DNS-rebinding defense — pinning the connection to the resolved
    IP — is a follow-up; see follow-up ticket
    clipper-ssrf-defense-followups.)
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ClipperBlockedHostError(
            f"Refusing scheme {parsed.scheme!r}; only http(s) is permitted"
        )
    host = parsed.hostname
    if not host:
        raise ClipperBlockedHostError(f"URL {url!r} has no host")

    # Try parsing the host directly as an IP literal first; that catches
    # http://127.0.0.1/ and http://[::1]/ without a DNS lookup.
    try:
        ip = ipaddress.ip_address(host)
        _reject_if_blocked(ip, host)
        return
    except ValueError:
        pass  # not an IP literal — resolve via DNS

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise ClipperBlockedHostError(
            f"Could not resolve host {host!r}: {outside_text(exc)}"
        ) from exc

    seen: set[str] = set()
    for info in infos:
        addr = info[4][0]
        if addr in seen:
            continue
        seen.add(addr)
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError as exc:
            # An address this check cannot read (a scoped IPv6 address, say)
            # is one it cannot vouch for: refused.
            raise ClipperBlockedHostError(
                f"Refusing to fetch {host!r}: a resolved address cannot be checked"
            ) from exc
        _reject_if_blocked(ip, host)
    if not seen:
        raise ClipperBlockedHostError(f"Refusing to fetch {host!r}: it resolved to no address")


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
