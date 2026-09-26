"""
MCP SSE transport routes for FastAPI.

Mounts the MCP SDK's SSE transport on the FastAPI app, enabling
Claude Desktop and Claude Code to connect over HTTP with Bearer token auth.

Endpoints:
    GET  /mcp/sse       — SSE connection (long-lived stream)
    POST /mcp/messages/ — client posts JSON-RPC messages
    GET  /mcp/info      — connection metadata for frontends
"""

import hashlib
import logging
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route

from ..config import PyriteConfig
from ..services import credential_events
from ..services.access_policy import ROLES, AccessPolicy, Principal, resolve_api_key_role
from ..storage.database import PyriteDB

logger = logging.getLogger(__name__)


def _resolve_bearer_auth(
    request: Request,
    config: PyriteConfig,
    db: PyriteDB,
) -> dict[str, Any]:
    """Validate Bearer token, X-API-Key header, or session cookie.

    Returns a dict with keys: role, username, user_id (optional),
    `readable_kbs` -- the KBs this caller may read -- and `writable_kbs` --
    the KBs a write-tier tool may target; each None when the caller is not
    scoped.

    The readable and writable sets come from the access policy
    (`AccessPolicy.read_scope` / `write_scope`), the rule the REST routes
    resolve through too, so a grant honoured over REST is honoured over MCP
    and vice versa. There is deliberately no second implementation of the
    rule (#201).

    `user_id` is the discriminator, matching REST: non-None for a session
    user (scoped), None for an API key -- the operator's credential, not a
    peer's -- or for auth being disabled entirely, both unscoped.

    Raises HTTPException(401) on failure. Note there is no anonymous branch:
    `/mcp` 401s a caller with no credential where REST would admit them at
    `anonymous_tier`.
    """
    policy = AccessPolicy(config, db)
    ctx = _resolve_credential(request, config, db, policy)
    principal = (
        Principal.user(ctx["user_id"], ctx["role"])
        if ctx.get("user_id") is not None
        else Principal.from_api_key(ctx["role"])
    )
    ctx["readable_kbs"] = policy.read_scope(principal).as_set()
    # The KBs a write-tier tool may target: the same per-KB rule REST's
    # `requires_kb_tier("write")` applies, resolved once per connection.
    ctx["writable_kbs"] = policy.write_scope(principal).as_set()
    return ctx


def _session_ctx(user: dict, session: dict) -> dict[str, Any]:
    """A session credential's ctx. ``session_hash`` and ``session_expires_at``
    let a live-update socket be closed when that session ends (ADR-0036)."""
    return {
        "role": user["role"],
        "username": user["username"],
        "user_id": user["id"],
        "session_hash": session["token_hash"],
        "session_expires_at": session["expires_at"],
        "principal_kind": "user",
        "principal_id": str(user["id"]),
    }


def _key_ctx(key: str, role: str) -> dict[str, Any]:
    """An operator key's ctx. ``principal_id`` is the key's whole hash:
    ``username`` shows only a prefix, and is a name a user could register."""
    digest = hashlib.sha256(key.encode()).hexdigest()
    return {
        "role": role,
        "username": f"apikey-{digest[:8]}",
        "user_id": None,
        "principal_kind": "operator_key",
        "principal_id": digest,
    }


def _resolve_credential(
    request: HTTPConnection,
    config: PyriteConfig,
    db: PyriteDB,
    policy: AccessPolicy | None = None,
) -> dict[str, Any]:
    """The credential half of `_resolve_bearer_auth`: role, username, user_id
    (and, for a session, ``session_hash`` and ``session_expires_at``).

    Reads only headers and cookies, so it takes any `HTTPConnection` -- a
    `Request` here, a `WebSocket` handshake in `websocket.resolve_socket_scope`
    (#218). One credential resolver for both transports, not two.

    Synchronous and may query the DB (session lookup): callers on the event
    loop run it in a threadpool. Sessions are verified through the access
    policy's identity store (`policy`, built on `db` when not given).
    """
    if policy is None:
        policy = AccessPolicy(config, db)
    # 1. Bearer token in Authorization header
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()
        if token:
            role = _resolve_api_key_role(token, config)
            if role is not None:
                return _key_ctx(token, role)

            # Check against session tokens (for web-authenticated users)
            if config.settings.auth.enabled:
                found = policy.auth_service.verify_session_detail(token)
                if found:
                    return _session_ctx(*found)

    # 2. X-API-Key header (fallback for clients that use it)
    api_key = request.headers.get("x-api-key")
    if api_key:
        role = _resolve_api_key_role(api_key, config)
        if role is not None:
            return _key_ctx(api_key, role)

    # 3. Session cookie
    if config.settings.auth.enabled:
        session_token = request.cookies.get("pyrite_session")
        if session_token:
            found = policy.auth_service.verify_session_detail(session_token)
            if found:
                return _session_ctx(*found)

    # 4. No auth configured — open access
    if (
        not config.settings.api_key
        and not config.settings.api_keys
        and not config.settings.auth.enabled
    ):
        return {
            "role": "admin",
            "username": "anonymous",
            "user_id": None,
            "principal_kind": "operator_key",
            "principal_id": "open-access",
        }

    raise HTTPException(
        status_code=401,
        detail="Invalid or missing authentication. Provide Authorization: Bearer <token> header.",
    )


def _resolve_api_key_role(key: str, config: PyriteConfig) -> str | None:
    """Resolve an API key to its role -- the access policy's rule, not a copy.

    There were two copies of this rule, and a bug in the "no keys configured"
    branch (any key answered "admin" with auth enabled) had to be fixed in
    both. Delegating keeps one implementation.
    """
    return resolve_api_key_role(key, config)


async def _authenticate(request: Request, config: PyriteConfig, shared_db: PyriteDB) -> dict:
    """Resolve the caller on a worker thread, with a per-request DB handle.

    The session lookup is synchronous DB work, so it runs off the event loop
    (as REST's ``verify_api_key`` does, #131) on a handle whose Session closes
    as soon as auth is resolved -- an SSE connection may stay open for hours
    and must not pin a pooled connection for its lifetime.
    """
    from starlette.concurrency import run_in_threadpool

    def _run() -> dict:
        with shared_db.request_handle() as db:
            return _resolve_bearer_auth(request, config, db)

    return await run_in_threadpool(_run)


async def _identify(request: Request, config: PyriteConfig, shared_db: PyriteDB) -> dict | None:
    """The credential half only (no KB scope), off the event loop; None when
    the request carries no credential this server accepts."""
    from starlette.concurrency import run_in_threadpool

    def _run() -> dict | None:
        with shared_db.request_handle() as db:
            try:
                return _resolve_credential(request, config, db)
            except HTTPException:
                return None

    return await run_in_threadpool(_run)


def _session_owner(ctx: dict[str, Any]):
    """The principal a ctx resolves to, as the MCP SDK's transport sees an
    owner: an ``AuthenticatedUser`` in the ASGI scope's ``user``.

    ``SseServerTransport`` records the ``user`` of the ``/mcp/sse`` request
    as the session's owner and answers a message POST whose ``user`` differs
    exactly as an unknown session (P-M3; mcp >= 1.27.2). Identity is the
    principal's kind plus id -- a user's id, an operator key's whole hash --
    never a name a user can register.
    """
    from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
    from mcp.server.auth.provider import AccessToken

    identity = f"{ctx['principal_kind']}:{ctx['principal_id']}"
    return AuthenticatedUser(AccessToken(token="", client_id=identity, scopes=[], subject=identity))


class _TrackedSend:
    """An ASGI ``send`` that remembers whether the response started and ended,
    so a session cancelled mid-stream can still end its response cleanly."""

    def __init__(self, send):
        self._send = send
        self.started = False
        self.finished = False

    async def __call__(self, message) -> None:
        if message["type"] == "http.response.start":
            self.started = True
        elif message["type"] == "http.response.body" and not message.get("more_body", False):
            self.finished = True
        await self._send(message)

    async def finish(self) -> None:
        if self.started and not self.finished:
            try:
                await self({"type": "http.response.body", "body": b"", "more_body": False})
            except Exception:
                logger.debug("Could not end an MCP SSE response", exc_info=True)


async def _already_sent(scope, receive, send) -> None:
    """The response the SSE stream already was: nothing more to send."""


# The first MCP SDK whose SSE transport refuses a message from a principal
# other than the session's (the check `_session_owner` feeds).
REQUIRED_MCP_FOR_OWNER_CHECK = "1.27.2"


def _sdk_checks_session_owner(transport: Any) -> bool:
    """Does the installed SDK's SSE transport enforce the same-owner check?

    Detects the feature, not a version string: the transport keeps a
    per-session owner table, the SDK can derive an owner from a user, and
    two different principals yield two different owners. Without all three,
    a session id alone would authorize its messages (P-M3).
    """
    try:
        from mcp.server.auth.middleware.bearer_auth import authorization_context
    except ImportError:
        return False
    if not isinstance(getattr(transport, "_session_owners", None), dict):
        return False
    try:
        one = authorization_context(
            _session_owner({"principal_kind": "probe", "principal_id": "1"})
        )
        two = authorization_context(
            _session_owner({"principal_kind": "probe", "principal_id": "2"})
        )
    except Exception:
        return False
    return one != two


def mount_mcp_routes(
    app: FastAPI,
    app_get_config: Callable[[], PyriteConfig],
    app_get_db: Callable[[], PyriteDB],
) -> None:
    """Mount MCP SSE transport endpoints on the FastAPI application.

    Uses Starlette-level routes for the SSE and message endpoints (which
    manage their own ASGI responses) and a standard FastAPI route for
    the /mcp/info metadata endpoint.
    """
    from mcp.server.sse import SseServerTransport

    from .mcp_server import PyriteMCPServer
    from .mcp_sessions import sessions as sse_sessions
    from .websocket import SocketScope

    # SseServerTransport prepends scope["root_path"] (which is "/mcp" here,
    # since this whole app is nested under Mount("/mcp", ...) below) to this
    # endpoint to build the client-facing POST path. Passing "/mcp/messages/"
    # here double-prefixes it to "/mcp/mcp/messages/", which 404s — the
    # endpoint must be relative to the mount point, i.e. just "/messages/".
    sse_transport = SseServerTransport("/messages/")
    if not _sdk_checks_session_owner(sse_transport):
        # Fail closed: serving /mcp without the check would let anyone who
        # learns a session id act as that session's principal.
        logger.error(
            "MCP over HTTP (/mcp) is disabled: the installed mcp SDK does not "
            "check that a message comes from its session's owner. "
            "Install mcp>=%s to serve /mcp.",
            REQUIRED_MCP_FOR_OWNER_CHECK,
        )
        return
    # The characterization harness reaches the transport's session table here.
    app.state.pyrite_mcp_sse_transport = sse_transport
    # Idempotent: the listener is module-level, like the registry.
    credential_events.subscribe(sse_sessions.on_credential_change)

    # Cache MCP server instances per tier to avoid repeated heavy init.
    _mcp_servers: dict[str, PyriteMCPServer] = {}

    def _get_mcp_server(tier: str) -> PyriteMCPServer:
        """Get or create MCP server for the given tier."""
        if tier not in _mcp_servers:
            config = app_get_config()
            _mcp_servers[tier] = PyriteMCPServer(config=config, tier=tier)
        return _mcp_servers[tier]

    # -----------------------------------------------------------------
    # GET /mcp/sse — long-lived SSE connection
    # -----------------------------------------------------------------

    async def handle_sse(request: Request) -> Response:
        """SSE endpoint for MCP client connections.

        Authenticates via Bearer token, resolves the user's tier, then hands
        off to the MCP SSE transport for the session lifetime -- which is no
        longer than the credential's (``mcp_sessions``, P-M4). The resolved
        principal is the session's owner (``_session_owner``, P-M3).
        """
        config = app_get_config()
        # Read before the credential is resolved; `sessions.hold` compares it
        # after registering, so a change landing in between is not missed.
        epoch = sse_sessions.epoch
        try:
            user_ctx = await _authenticate(request, config, app_get_db())
        except HTTPException as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.detail},
            )

        role = user_ctx["role"]
        tier = role if role in ROLES else "read"
        readable = user_ctx["readable_kbs"]
        writable = user_ctx["writable_kbs"]

        logger.info(
            "MCP SSE connection: user=%s tier=%s scoped=%s",
            user_ctx["username"],
            tier,
            readable is not None,
        )

        # The per-tier cache is untouched and its key stays `tier`: the
        # cached PyriteMCPServer is identity-free, and the readable set
        # rides the per-connection closures build_sdk_server already makes
        # for client_id. Two callers at one tier share this instance and
        # still get correctly different answers (#201).
        mcp_server = _get_mcp_server(tier)
        sdk = mcp_server.build_sdk_server(
            client_id=user_ctx["principal_id"],
            client_kind=user_ctx["principal_kind"],
            readable_kbs=readable,
            writable_kbs=writable,
        )

        request.scope["user"] = _session_owner(user_ctx)
        record = SocketScope(
            readable=readable,
            user_id=user_ctx.get("user_id"),
            session_hash=user_ctx.get("session_hash"),
            expires_at=user_ctx.get("session_expires_at"),
        )
        send = _TrackedSend(request._send)
        with sse_sessions.hold(record, epoch) as cancel_scope:
            async with sse_transport.connect_sse(request.scope, request.receive, send) as (
                read_stream,
                write_stream,
            ):
                await sdk.run(read_stream, write_stream, sdk.create_initialization_options())
        if cancel_scope.cancel_called:
            logger.info("MCP SSE session ended with its credential: user=%s", user_ctx["username"])
            await send.finish()

        # The stream was the response. Starting a second one after it (the
        # SDK example's `return Response()`) is a protocol error.
        if send.started:
            return _already_sent
        return Response()

    # -----------------------------------------------------------------
    # POST /mcp/messages/ — JSON-RPC message relay
    # -----------------------------------------------------------------
    # The SDK's handle_post_message is a raw ASGI app. Every POST carries its
    # own credential; `_session_owner` puts its principal where the SDK's
    # same-owner check compares it with the session's (P-M3). A POST with no
    # accepted credential has no owner, and is refused as an unknown session.

    async def handle_messages(scope, receive, send) -> None:
        if scope["type"] == "http":
            ctx = await _identify(Request(scope), app_get_config(), app_get_db())
            if ctx is not None:
                scope["user"] = _session_owner(ctx)
        await sse_transport.handle_post_message(scope, receive, send)

    # -----------------------------------------------------------------
    # GET /mcp/info — connection metadata (normal JSON endpoint)
    # -----------------------------------------------------------------

    async def handle_info(request: Request) -> Response:
        """Return MCP connection info for frontends and documentation."""
        config = app_get_config()
        base_url = str(request.base_url).rstrip("/")
        endpoint_url = f"{base_url}/mcp/sse"

        info: dict[str, Any] = {
            "endpoint": endpoint_url,
            "transport": "sse",
            "auth": "bearer",
        }

        # Try to resolve user context for tier-specific info
        try:
            user_ctx = await _authenticate(request, config, app_get_db())
            tier = user_ctx["role"] if user_ctx["role"] in ROLES else "read"
            mcp_server = _get_mcp_server(tier)
            info["tools_count"] = len(mcp_server.tools)
            info["tier"] = tier
        except HTTPException:
            # Unauthenticated — show basic info
            read_server = _get_mcp_server("read")
            info["tools_count"] = len(read_server.tools)
            info["tier"] = "unauthenticated"

        return JSONResponse(content=info)

    # -----------------------------------------------------------------
    # Mount all routes under /mcp
    # -----------------------------------------------------------------
    app.routes.insert(
        0,
        Mount(
            "/mcp",
            routes=[
                Route("/sse", endpoint=handle_sse, methods=["GET"]),
                Route("/info", endpoint=handle_info, methods=["GET"]),
                Mount("/messages/", app=handle_messages),
            ],
        ),
    )
