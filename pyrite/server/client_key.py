"""The one key for "this client": the hashed network of its remote address.

The REST rate limiter and the anonymous WebSocket share both key a client this
way, so the same visitor is the same client to each. An IPv4 address is its
own key (/32); an IPv6 address is keyed by its /64, the smallest allocation an
end site receives, so rotating addresses inside one allocation stays one
client. An IPv4-mapped IPv6 address is keyed as the IPv4 address.

The address is the connection's peer. Behind a reverse proxy that is the
proxy's, so every visitor shares one key unless the deployment trusts the
proxy's forwarding headers (see docs/configuration.md).
"""

import hashlib
import ipaddress

from slowapi.util import get_remote_address


def network_of(address: str) -> str:
    """``address`` as the string that identifies its client: the address for
    IPv4, the /64 network for IPv6, the text itself if it is neither."""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return address
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return str(ip.ipv4_mapped)
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(ip)


def client_key(conn) -> str:
    """SHA-256 of the client's network, truncated to 16 hex characters: enough
    to tell clients apart, not reversible to the address. ``conn`` is a
    request or any connection with ``client.host``."""
    return hashlib.sha256(network_of(get_remote_address(conn)).encode()).hexdigest()[:16]
