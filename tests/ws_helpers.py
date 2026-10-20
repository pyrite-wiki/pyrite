"""Bounded reads from a TestClient websocket (tests-t1).

A Starlette ``TestClient`` socket's ``receive_json()`` blocks forever when no
message is coming, so a regression that drops an event hangs the suite
instead of failing it. ``receive_json(ws)`` reads through the same portal
with a deadline and raises ``AssertionError`` naming the wait.
"""

import json
from typing import Any

import anyio

DEFAULT_DEADLINE = 10.0


def receive_json(ws, timeout: float = DEFAULT_DEADLINE) -> Any:
    """Next JSON message on ``ws``; fails after ``timeout`` seconds."""

    async def _next():
        with anyio.fail_after(timeout):
            # Starlette's TestClient session (private attrs `_send_rx` and
            # `_raise_on_close`, checked against starlette 1.8.0 in
            # this venv; `tests/test_websocket_delivery.py::TestReceiveHasADeadline`
            # fails loudly if a release renames them).
            return await ws._send_rx.receive()

    try:
        message = ws.portal.call(_next)
    except TimeoutError:
        raise AssertionError(f"no websocket message within {timeout}s") from None
    ws._raise_on_close(message)
    return json.loads(message["text"])
