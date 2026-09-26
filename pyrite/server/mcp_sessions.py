"""MCP SSE sessions live no longer than the credential that opened them.

P-M4 and P-A2 in kb/designs/multi-user-threat-model.md; the same rule
ADR-0036 set for ``/ws`` (#411). An SSE session's tier and KB scope are
resolved once, at ``/mcp/sse`` connect, and closed over by that session's
``sdk.run`` loop. Rather than re-resolve them per message, the session ends
when that credential does:

- the opening session is logged out, evicted or expires (``session_hash``);
- the user's role or KB grants change, or ``logout_all`` (``user_id``);
- the session's own ``expires_at`` passes (a cancel-scope deadline: expiry
  publishes no event unless something touches the session row).

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
from .websocket import SocketScope

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
        # Bumped by every change. A handshake reads it before resolving its
        # credential and `hold` compares it after registering: a change that
        # landed in between may have revoked the credential just resolved.
        self._epoch = 0

    @property
    def epoch(self) -> int:
        with self._lock:
            return self._epoch

    @contextmanager
    def hold(self, scope: SocketScope, epoch: int) -> Iterator[anyio.CancelScope]:
        """Run the body as a live session that ``scope``'s credential bounds.

        The body is cancelled when a matching credential change arrives, when
        ``scope.expires_at`` passes, or at once if the credential is revocable
        and a change was processed since ``epoch`` was read.
        """
        cancel_scope = anyio.CancelScope()
        if scope.expires_at is not None:
            remaining = (scope.expires_at - datetime.now(UTC)).total_seconds()
            cancel_scope.deadline = anyio.current_time() + remaining
        live = _Live(scope, cancel_scope, asyncio.get_running_loop())
        with self._lock:
            self._live.add(live)
            stale = scope.revocable and epoch != self._epoch
        if stale:
            logger.info("Ended an MCP SSE session: its credential changed during the handshake")
            cancel_scope.cancel()
        try:
            with cancel_scope:
                yield cancel_scope
        finally:
            with self._lock:
                self._live.discard(live)

    def on_credential_change(self, change: CredentialChange) -> None:
        """The ``credential_events`` listener: end the sessions ``change`` names."""
        with self._lock:
            self._epoch += 1
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
