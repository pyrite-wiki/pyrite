"""Host and Origin checks for a server that grants access without a credential.

With auth disabled and no API keys, every request to the API is admin; with
``anonymous_tier: write``, every visitor can write. A browser is then the only
thing between a web page the user visits and their KBs, and two browser rules
do not hold on their own:

- **Host.** A page on a name its owner controls can re-point that name at
  127.0.0.1. The browser then treats this server as same-origin with the page,
  so CORS never applies. The only thing such a request cannot fake is the
  ``Host`` it is addressed to, so a request must name a host this server
  expects, or it gets 421 and reaches no handler.
- **Origin.** CORS stops a page reading a response, not sending a simple
  request (a form post, a multipart upload, a body-less POST). A
  state-changing request whose ``Origin`` -- or, with no ``Origin``, whose
  ``Referer`` -- is neither this host nor configured in ``cors_origins`` gets
  403. Requests with neither header are not browser-initiated cross-site
  requests (CLI, curl, agents) and are unaffected.

Both rules apply while a request can act without a credential
(``acts_without_credential``): auth disabled with no API keys, or auth enabled
with ``anonymous_tier: write``. Otherwise the Host rule is off -- such servers
usually sit behind a proxy with a public hostname -- but the **Origin rule
still applies to every request authenticated by the session cookie**, in
every mode. The cookie is ``SameSite=Lax``, which a *same-site* page (another
port on the same host, a sibling subdomain) still gets attached to its simple
POSTs, so the cookie alone is not proof the user meant the request. A request
authenticated by an API key is not cookie-bound: a page cannot set that
header on a cross-origin request without a CORS preflight.

``request_origin_admitted`` is the one Origin rule; ``/ws`` uses it too.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Receive, Scope, Send

from ..config import PyriteConfig, Settings

logger = logging.getLogger(__name__)

# Loopback names a local install always answers. Tests add TestClient's
# "testserver" through the repo-wide conftest; it is deliberately not here.
LOCAL_HOSTS: frozenset[str] = frozenset({"localhost", "127.0.0.1", "::1"})

_WILDCARD_BIND_HOSTS = frozenset({"", "0.0.0.0", "::"})
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _hostname(value: str) -> str:
    """``host[:port]`` (or ``[v6]:port``) -> lowercase host, no brackets or port."""
    value = value.strip().lower()
    if value.startswith("["):
        return value[1:].split("]", 1)[0]
    if value.count(":") == 1:
        return value.split(":", 1)[0]
    return value


def allowed_hosts(settings: Settings) -> set[str]:
    """Every hostname this server answers while it grants credential-free access."""
    hosts = set(LOCAL_HOSTS)
    bind = _hostname(settings.host or "")
    if bind not in _WILDCARD_BIND_HOSTS:
        hosts.add(bind)
    hosts.update(_hostname(h) for h in settings.allowed_hosts)
    return hosts


def acts_without_credential(settings: Settings) -> bool:
    """A request can change something here without presenting a credential.

    (a) Auth disabled and no API keys: ``verify_api_key`` makes every request
    admin. (b) Auth enabled with ``anonymous_tier: write``: anonymous visitors
    can write. Everywhere else a request that acts needs a credential a
    foreign page cannot supply, so the guard stays off -- a keyed or proxied
    deployment answers whatever hostname its proxy sends.
    """
    if settings.auth.enabled:
        return settings.auth.anonymous_tier == "write"
    return not settings.api_key and not settings.api_keys


def origin_permitted(origin: str, host: str, cors_origins: list[str]) -> bool:
    """An ``Origin`` value is this server's own host or configured in ``cors_origins``.

    ``"*"`` in ``cors_origins`` is not a wildcard here: REST drops credentials
    for a wildcard origin, and this rule exists for requests whose authority is
    ambient (a cookie, or no credential at all).
    """
    if origin in cors_origins:
        return True
    netloc = urlsplit(origin).netloc
    return bool(host) and netloc.lower() == host.lower()


def request_origin(headers: Headers) -> str | None:
    """The request's ``Origin``, or the origin part of its ``Referer``."""
    origin = headers.get("origin")
    if origin is not None:
        return origin
    referer = headers.get("referer")
    if referer is None:
        return None
    parts = urlsplit(referer)
    return f"{parts.scheme}://{parts.netloc}"


def request_origin_admitted(headers: Headers, cors_origins: list[str]) -> bool:
    """The one Origin rule, for REST, ``/mcp`` and ``/ws``.

    Admitted when the request's ``Origin`` (or, with none, its ``Referer``)
    is this server's own host or configured in ``cors_origins``. A request
    with neither header is not a browser's cross-site request (a CLI, curl,
    an agent) and is admitted: browsers send ``Origin`` on every
    state-changing request and on every WebSocket handshake.
    """
    origin = request_origin(headers)
    if origin is None:
        return True
    return origin_permitted(origin, headers.get("host", ""), cors_origins)


SESSION_COOKIE = "pyrite_session"


def cookie_authenticated(conn: HTTPConnection, config: PyriteConfig) -> bool:
    """Could this request be authenticated by the session cookie?

    True when it carries the session cookie, unless an ``X-API-Key`` header
    authenticates it first -- REST and ``/mcp`` both try that header before
    the cookie, and a page cannot set it on a cross-origin request without a
    CORS preflight. A key that does not resolve falls through to the cookie,
    so it exempts nothing. Other key locations do not exempt: the
    ``api_key`` query parameter is one a foreign page can put in a URL, and
    ``/mcp`` would still authenticate such a request by its cookie. The
    cookie itself is not verified here: a request that carries one is
    refused cross-origin whether or not it is still valid.
    """
    if not conn.cookies.get(SESSION_COOKIE):
        return False
    from ..services.access_policy import resolve_api_key_role

    key = conn.headers.get("x-api-key")
    return not (key and resolve_api_key_role(key, config) is not None)


class RequestGuardMiddleware:
    """ASGI middleware applying the Host and Origin rules above.

    Covers every HTTP route and WebSocket handshake of the app it wraps,
    including the ``/mcp`` mount and ``/ws``. Reads config per request so a
    test or an admin reload that swaps ``app.state.pyrite_config`` is honoured.
    """

    def __init__(self, app: ASGIApp, get_config: Callable[[], PyriteConfig]) -> None:
        self.app = app
        self.get_config = get_config

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        config = self.get_config()
        settings = config.settings
        conn = HTTPConnection(scope)
        without_credential = acts_without_credential(settings)

        if without_credential:
            host = conn.headers.get("host", "")
            if _hostname(host) not in allowed_hosts(settings):
                logger.warning(
                    "Refused request to %s: Host %r is not an allowed host "
                    "(add it to settings.allowed_hosts to serve on that name)",
                    scope.get("path"),
                    host,
                )
                await _refuse(scope, send, 421, "Misdirected request: host not allowed")
                return

        if (
            scope["type"] == "http"
            and scope["method"] not in _SAFE_METHODS
            and (without_credential or cookie_authenticated(conn, config))
            and not request_origin_admitted(conn.headers, settings.cors_origins)
        ):
            logger.warning(
                "Refused cross-origin %s %s from %r",
                scope["method"],
                scope.get("path"),
                request_origin(conn.headers),
            )
            await _refuse(scope, send, 403, "Cross-origin request refused")
            return

        await self.app(scope, receive, send)


async def _refuse(scope: Scope, send: Send, status: int, detail: str) -> None:
    if scope["type"] == "websocket":
        # Closing before accept makes the server answer the handshake with 403.
        await send({"type": "websocket.close", "code": 1008})
        return
    body = json.dumps({"detail": detail}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
