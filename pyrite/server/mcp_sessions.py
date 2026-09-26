"""MCP SSE sessions live no longer than the credential that opened them.

P-M4 and P-A2 in kb/designs/multi-user-threat-model.md; the same rule
ADR-0036 set for ``/ws`` (#411). An SSE session's tier and KB scope are
resolved once, at ``/mcp/sse`` connect, and closed over by that session's
``sdk.run`` loop. Rather than re-resolve them per message, the session ends
when that credential does:

- the opening session is logged out, evicted or expires (``session_hash``);
- the user's role or KB grants change, or ``logout_all`` (``user_id``);
- the session's own ``expires_at`` passes (a cancel-scope deadline: expiry
  publishes no event unless something touches the session row);
- a KB in its readable set changes policy -- its ``default_role``, or it is
  removed (``kb_name``; P-W3, the rule ``/ws`` follows too).

``AuthService`` announces the first two through
``pyrite.services.credential_events``; ``SSESessions.on_credential_change``
is the listener ``mount_mcp_routes`` subscribes. The client reconnects and is
resolved afresh -- a demoted admin's new session is served at its new tier.

Each live session is a ``SocketScope`` (the ``/ws`` record: user, session
hash, expiry) plus the anyio cancel scope its handler runs in and the loop it
runs on. The listener may be called on any thread; it cancels on the
session's own loop.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime

import anyio

from ..services.credential_events import CredentialChange
from .websocket import ChangeEpochs, ScopeEpoch, SocketScope

logger = logging.getLogger(__name__)


@dataclass(eq=False)
class _Live:
    scope: SocketScope
    cancel_scope: anyio.CancelScope
    loop: asyncio.AbstractEventLoop


class SSESessions:
    """The open SSE sessions of this process, by the credential that opened each."""

    def __init__(self) -> None:
        self._live: set[_Live] = set()
        self._lock = threading.Lock()
        # Advanced by every change; `hold` checks a handshake against it
        # (the /ws rule, `websocket.ChangeEpochs`).
        self._epochs = ChangeEpochs()

    @property
    def epoch(self) -> ScopeEpoch:
        with self._lock:
            return self._epochs.current

    @contextmanager
    def hold(self, scope: SocketScope, epoch: ScopeEpoch) -> Iterator[anyio.CancelScope]:
        """Run the body as a live session that ``scope``'s credential bounds.

        The body is cancelled when a matching change arrives (its credential,
        or the policy of a KB it reads), when ``scope.expires_at`` passes, or
        at once if a change that could alter ``scope`` was processed since
        ``epoch`` was read (`websocket.ChangeEpochs.stale`).
        """
        cancel_scope = anyio.CancelScope()
        if scope.expires_at is not None:
            remaining = (scope.expires_at - datetime.now(UTC)).total_seconds()
            cancel_scope.deadline = anyio.current_time() + remaining
        live = _Live(scope, cancel_scope, asyncio.get_running_loop())
        with self._lock:
            self._live.add(live)
            stale = self._epochs.stale(scope, epoch)
        if stale:
            logger.info("Ended an MCP SSE session: its scope changed during the handshake")
            cancel_scope.cancel()
        try:
            with cancel_scope:
                yield cancel_scope
        finally:
            with self._lock:
                self._live.discard(live)

    def on_credential_change(self, change: CredentialChange) -> None:
        """The ``credential_events`` listener: end the sessions ``change``
        names -- by credential, or by a KB in their readable set
        (`SocketScope.matches`, the same rule as ``/ws``)."""
        with self._lock:
            self._epochs.record(change)
            victims = [live for live in self._live if live.scope.matches(change)]
        for live in victims:
            self._cancel(live)
        if victims:
            logger.info("Ended %d MCP SSE session(s) after a credential change", len(victims))

    @staticmethod
    def _cancel(live: _Live) -> None:
        # A cancel scope is cancelled on its own loop; this may be any thread.
        try:
            live.loop.call_soon_threadsafe(live.cancel_scope.cancel)
        except RuntimeError:
            # The loop has closed: the session went with it.
            logger.debug("Loop closed; an MCP SSE session was already gone")


sessions = SSESessions()
