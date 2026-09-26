"""WebSocket connection manager for multi-tab awareness.

Every socket is authenticated at the handshake and carries the set of KBs its
owner may read (#218). An event naming a KB goes only to sockets that may read
it; an event naming none (``kb_synced``) goes to every accepted socket.

The readable set is resolved **once, at connect**; events carry metadata
only (KB name, entry id), and re-resolving per event would put a DB walk per
socket per event on the loop. Instead a socket lives no longer than the
credential that opened it (#411, ADR-0036): each socket records the user and
session it was opened with, and the server closes it when that session ends
(logout, eviction, expiry) or that user's role or KB grants change. The
client then reconnects and is scoped afresh. ``AuthService`` announces each
change through ``pyrite.services.credential_events``; ``on_credential_change``
is the listener the app subscribes at startup. Expiry has no event: the
recorded expiry is checked before every send and by a periodic sweep.
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, WebSocket
from starlette.requests import HTTPConnection

from ..config import PyriteConfig
from ..services.access_policy import AccessPolicy, Principal, resolve_api_key_role
from ..services.credential_events import CredentialChange
from ..storage.database import PyriteDB
from .request_guard import request_origin_admitted

logger = logging.getLogger(__name__)


# Events that name no KB and carry nothing KB-specific: delivered to every
# accepted socket. Anything else without a kb_name goes to unscoped sockets only.
GLOBAL_EVENTS = frozenset({"kb_synced"})

# Events for operators only, whatever KB they name: delivered to unscoped
# sockets and never to a scoped one. `index_progress` carries index job ids
# and whole-index counts (the rule in kb/backlog/authenticate-and-scope-ws-218.md).
UNSCOPED_ONLY_EVENTS = frozenset({"index_progress"})


# Policy Violation: the credential no longer admits this socket. The code a
# refused handshake uses too; the web client reconnects after any close of a
# socket that had opened (#336), which scopes it afresh.
CREDENTIAL_CLOSE_CODE = 1008

# How often the expiry sweep closes sockets whose session has expired. An
# expired socket receives nothing in between: `broadcast` checks the expiry
# before every send; the sweep only makes the close itself prompt.
EXPIRY_SWEEP_SECONDS = 60.0


@dataclass
class SocketScope:
    """What a socket may read, and the credential it was opened with.

    ``readable`` is None for an unscoped socket (an operator key, or auth
    disabled). ``user_id``, ``session_hash`` and ``expires_at`` are set only
    for a socket opened with a session; an operator-key, auth-disabled or
    anonymous socket has no credential that can be revoked.
    """

    readable: set[str] | None
    user_id: int | None = None
    session_hash: str | None = None
    expires_at: datetime | None = None

    @property
    def revocable(self) -> bool:
        return self.user_id is not None or self.session_hash is not None

    def matches(self, change: CredentialChange) -> bool:
        if change.session_hash is not None and change.session_hash == self.session_hash:
            return True
        return change.user_id is not None and change.user_id == self.user_id

    def expired(self, now: datetime) -> bool:
        return self.expires_at is not None and self.expires_at <= now


class HandshakeRejectedError(Exception):
    """The socket's credential (or lack of one) does not admit it."""


def origin_allowed(conn: HTTPConnection, config: PyriteConfig) -> bool:
    """A handshake's ``Origin`` is absent, the server's own host, or configured.

    CORS does not apply to WebSockets: a page on any site can open a socket to
    this server and the browser attaches the ``pyrite_session`` cookie
    (SameSite=lax admits it on a same-site cross-origin handshake, and
    non-browser clients send whatever they like). Once the cookie
    authenticates the socket, this is the only cross-origin gate.

    The rule is REST's own (``request_guard.request_origin_admitted``), so
    the two surfaces cannot drift. Absent ``Origin`` (and ``Referer``) is
    allowed: browsers always send ``Origin`` on a WebSocket handshake, so its
    absence means a non-browser client, which the credential check alone
    governs. ``"*"`` in ``cors_origins`` is **not** a
    wildcard here: REST drops credentials for a wildcard origin, and this
    check exists precisely because a socket's credential is a cookie.
    """
    return request_origin_admitted(conn.headers, config.settings.cors_origins)


def resolve_socket_scope(conn: HTTPConnection, config: PyriteConfig, db: PyriteDB) -> SocketScope:
    """The KBs a handshake's caller may read (None when unscoped), and the
    session that admitted it, if any.

    Synchronous and may touch the DB (session lookup, grants): run it off the
    event loop. Outcomes match REST's ``verify_api_key``:

    - an operator API key (header, or the ``api_key`` query parameter, since
      a browser cannot set headers on a WebSocket) -- unscoped;
    - a valid session (cookie, or Bearer) -- scoped to that user;
    - no usable credential, auth enabled with an ``anonymous_tier`` -- scoped
      to what an anonymous visitor may read;
    - auth disabled and no keys configured -- unscoped (the default local
      install, unchanged);
    - otherwise ``HandshakeRejectedError``.

    Identity comes from ``mcp_routes._resolve_credential`` and the rule from
    the access policy (``AccessPolicy.read_scope``) -- no third
    implementation of either.
    """
    from .mcp_routes import _resolve_credential

    settings = config.settings
    policy = AccessPolicy(config, db)

    # An operator key, which `resolve_api_key_role` accepts only when keys are
    # configured or auth is disabled (#331).
    key = conn.query_params.get("api_key")
    if key and resolve_api_key_role(key, config) is not None:
        return SocketScope(readable=None)

    try:
        ctx = _resolve_credential(conn, config, db, policy)
    except HTTPException:
        ctx = None

    if ctx is not None and ctx.get("user_id") is not None:
        return SocketScope(
            readable=policy.read_scope(Principal.user(ctx["user_id"], ctx["role"])).as_set(),
            user_id=ctx["user_id"],
            session_hash=ctx.get("session_hash"),
            expires_at=ctx.get("session_expires_at"),
        )
    if ctx is not None:
        return SocketScope(readable=None)  # an operator key, or auth disabled with no keys

    if settings.auth.enabled and settings.auth.anonymous_tier:
        return SocketScope(
            readable=policy.read_scope(Principal.anonymous(settings.auth.anonymous_tier)).as_set()
        )
    raise HandshakeRejectedError


class ConnectionManager:
    """Manages active WebSocket connections and broadcasts events.

    Each connection maps to its ``SocketScope``, resolved once at connect;
    ``broadcast`` filters on it without touching the DB. Every method runs on
    the server's event loop; none of them is thread-safe.
    """

    def __init__(self):
        self._connections: dict[WebSocket, SocketScope] = {}
        # Bumped by every `revoke`. A handshake reads it before resolving its
        # credential and `connect` compares it after `accept`: a change that
        # landed in between may have revoked the credential just resolved.
        self._epoch = 0
        self._pending_closes: set[asyncio.Task] = set()

    @property
    def epoch(self) -> int:
        return self._epoch

    async def connect(self, ws: WebSocket, scope: SocketScope, epoch: int | None = None) -> bool:
        """Accept an *already authenticated* socket with its scope.

        ``epoch`` is ``self.epoch`` as read before the credential was
        resolved. If a credential change has been processed since, and this
        socket's credential is revocable, the socket is closed at once
        instead of registered (the client reconnects and is resolved afresh)
        and False is returned. The check and the registration have no
        ``await`` between them, so no change can slip past both.
        """
        await ws.accept()
        if epoch is not None and scope.revocable and epoch != self._epoch:
            logger.info("Closed /ws: its credential changed during the handshake")
            await self._close_quietly(ws)
            return False
        self._connections[ws] = scope
        logger.debug("WebSocket connected, total: %d", len(self._connections))
        return True

    def disconnect(self, ws: WebSocket):
        self._connections.pop(ws, None)
        logger.debug("WebSocket disconnected, total: %d", len(self._connections))

    def revoke(self, change: CredentialChange) -> int:
        """Close every socket opened with the credential ``change`` names.

        Each socket leaves the manager *now*, before its close frame is sent,
        so no event broadcast after this call can reach it. Returns the
        number closed.
        """
        self._epoch += 1
        victims = [ws for ws, scope in self._connections.items() if scope.matches(change)]
        for ws in victims:
            self._close(ws)
        if victims:
            logger.info("Closed %d socket(s) after a credential change", len(victims))
        return len(victims)

    def close_expired(self, now: datetime | None = None) -> int:
        """Close every socket whose session has expired; the count closed."""
        now = now or datetime.now(UTC)
        victims = [ws for ws, scope in self._connections.items() if scope.expired(now)]
        for ws in victims:
            self._close(ws)
        return len(victims)

    def _close(self, ws: WebSocket) -> None:
        """Unregister ``ws`` now; send its close frame in a task."""
        self._connections.pop(ws, None)
        task = asyncio.get_running_loop().create_task(self._close_quietly(ws))
        self._pending_closes.add(task)
        task.add_done_callback(self._pending_closes.discard)

    @staticmethod
    async def _close_quietly(ws: WebSocket) -> None:
        try:
            await ws.close(code=CREDENTIAL_CLOSE_CODE, reason="credential changed")
        except Exception:
            # Already gone: the client closed first, or the send failed.
            logger.debug("Close of a revoked socket failed", exc_info=True)

    async def broadcast(self, event: dict[str, Any]):
        """Send an event to every connected socket that may read its KB."""
        if not self._connections:
            return
        kb_name = event.get("kb_name")
        # An event that names no KB reaches a scoped socket only when it is on
        # the explicit global list. Failing closed keeps a future emitter
        # without a kb_name from reaching scoped or anonymous sockets.
        is_global = event.get("type") in GLOBAL_EVENTS
        unscoped_only = event.get("type") in UNSCOPED_ONLY_EVENTS
        message = json.dumps(event)
        now = datetime.now(UTC)
        dead: list[WebSocket] = []
        expired: list[WebSocket] = []
        for ws, scope in list(self._connections.items()):
            if ws not in self._connections:
                # Revoked while this fan-out awaited an earlier send.
                continue
            if scope.expired(now):
                expired.append(ws)
                continue
            readable = scope.readable
            if readable is not None:
                if unscoped_only:
                    continue
                if kb_name and kb_name not in readable:
                    continue
                if not kb_name and not is_global:
                    continue
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._connections.pop(ws, None)
        for ws in expired:
            if ws in self._connections:
                self._close(ws)

    @property
    def connection_count(self) -> int:
        return len(self._connections)


manager = ConnectionManager()


# The server's event loop, captured at startup (`bind_loop`). Sync code --
# a plain ``def`` route on a worker thread, an IndexWorker thread -- has no
# running loop of its own; it hands events to this one (#326, #322).
_loop: asyncio.AbstractEventLoop | None = None


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Record the loop that owns the sockets; called by the startup hook."""
    global _loop
    _loop = loop


def unbind_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Forget ``loop`` if it is still the bound one; called at shutdown.

    Leaves a loop bound by a later app alone (``manager`` is module-global,
    shared by every ``create_app()`` in a process).
    """
    global _loop
    if _loop is loop:
        _loop = None


# Fan-out tasks in flight. The loop holds only a weak reference to a task,
# so an otherwise unreferenced one can be garbage-collected before it has
# sent anything; each is held here until it completes.
_pending_broadcasts: set[asyncio.Task] = set()


def _schedule_broadcast(event: dict[str, Any]) -> None:
    """Runs *on* the loop: only here is the coroutine created."""
    task = asyncio.get_running_loop().create_task(manager.broadcast(event))
    _pending_broadcasts.add(task)
    task.add_done_callback(_pending_broadcasts.discard)


def broadcast_event(event_type: str, **data):
    """Broadcast a WebSocket event from any thread.

    On the server's loop (an ``async def`` route), the fan-out is scheduled
    as a task. Anywhere else -- a sync route on a worker thread, an
    IndexWorker thread, or code running under some other loop -- the event is
    handed to the loop captured at startup with ``call_soon_threadsafe``.
    The coroutine is created on that loop, never here, so a loop that closes
    before the hand-off runs leaves no coroutine unawaited.

    With no loop bound (a CLI process, or ``asyncio`` code with no server),
    an event raised under a running loop is scheduled there, as before; one
    raised with no running loop is dropped. A bound loop that has closed or
    stopped (a test after its ``TestClient`` context ended) counts as none.
    """
    event = {"type": event_type, **data}
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None

    loop = _loop
    if running is not None and (loop is None or running is loop):
        _schedule_broadcast(event)
        return

    if loop is None or loop.is_closed() or not loop.is_running():
        return
    try:
        loop.call_soon_threadsafe(_schedule_broadcast, event)
    except RuntimeError:
        # Closed between the check and the call.
        logger.debug("Event loop closed; dropped %s event", event_type)


def on_credential_change(change: CredentialChange) -> None:
    """The ``credential_events`` listener: close the sockets ``change`` names.

    Called on whatever thread made the change. On the server's loop (an
    ``async def`` route) the sockets are closed at once; from any other
    thread the change is handed to the loop captured at startup, in order
    with any event broadcast after it. With no live loop bound there are no
    sockets in this process to close.
    """
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None

    loop = _loop
    if running is not None and (loop is None or running is loop):
        manager.revoke(change)
        return

    if loop is None or loop.is_closed() or not loop.is_running():
        return
    try:
        loop.call_soon_threadsafe(manager.revoke, change)
    except RuntimeError:
        logger.debug("Event loop closed; dropped a credential change")


async def expiry_sweep(interval: float = EXPIRY_SWEEP_SECONDS) -> None:
    """Close expired-session sockets every ``interval`` seconds, until cancelled.

    Started by the app's startup hook and cancelled by its shutdown hook, so
    it lives exactly as long as the server's loop serves sockets.
    """
    while True:
        await asyncio.sleep(interval)
        try:
            manager.close_expired()
        except Exception:
            logger.exception("WebSocket expiry sweep failed")
