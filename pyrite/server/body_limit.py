"""One bound on every request body the server reads.

``settings.max_request_body_bytes`` (default 32 MiB) is the most any request
may send. ``RequestBodyLimitMiddleware`` wraps the whole app -- every REST
route, the import upload, ``/auth``, the ``/mcp`` mount and plugin routers --
so no route can forget it:

- a ``Content-Length`` over the limit is answered ``413`` before the app runs
  and before a byte of the body is read;
- a body with no length (chunked) or a wrong one is counted as it arrives,
  and the first chunk that takes it past the limit ends the read: the app
  never holds more than the limit plus one chunk.

Why the overflow is answered here and not by the route: the read happens
inside FastAPI's body parsing or Starlette's multipart parser, and FastAPI
turns any exception raised while reading a body into its own ``400 "There was
an error parsing the body"``. So the wrapped ``receive`` records the overflow
and raises, and the wrapped ``send`` replaces whatever the app then answers
with the ``413``. The handler never ran past the read, so nothing is written.

WebSocket frames are not request bodies and are not counted here.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..config import PyriteConfig

logger = logging.getLogger(__name__)

PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"


class RequestBodyTooLargeError(Exception):
    """Raised from the wrapped ``receive`` once the body passes the limit."""


def _too_large_body(limit: int) -> bytes:
    return json.dumps(
        {
            "detail": {
                "code": PAYLOAD_TOO_LARGE,
                "message": (
                    f"Request body is larger than this server accepts ({limit} bytes; "
                    "settings.max_request_body_bytes)."
                ),
                "retryable": False,
                "hint": "Send a smaller body, or split the import into several files.",
            }
        }
    ).encode()


async def _send_413(send: Send, limit: int) -> None:
    body = _too_large_body(limit)
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"connection", b"close"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def _declared_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers", []):
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


class RequestBodyLimitMiddleware:
    """ASGI middleware bounding every HTTP request body; see the module doc.

    Reads the limit per request, so a test or an admin reload that swaps
    ``app.state.pyrite_config`` is honoured.
    """

    def __init__(self, app: ASGIApp, get_config: Callable[[], PyriteConfig]) -> None:
        self.app = app
        self.get_config = get_config

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self.get_config().settings.max_request_body_bytes

        declared = _declared_length(scope)
        if declared is not None and declared > limit:
            logger.warning(
                "Refused %s %s: Content-Length %d is over the %d-byte body limit",
                scope.get("method"),
                scope.get("path"),
                declared,
                limit,
            )
            await _send_413(send, limit)
            return

        state = {"received": 0, "over": False, "started": False, "answered": False}

        async def limited_receive() -> Message:
            if state["over"]:
                raise RequestBodyTooLargeError()
            message = await receive()
            if message["type"] == "http.request":
                state["received"] += len(message.get("body", b""))
                if state["received"] > limit:
                    state["over"] = True
                    logger.warning(
                        "Refused %s %s: body passed the %d-byte limit while it was read",
                        scope.get("method"),
                        scope.get("path"),
                        limit,
                    )
                    raise RequestBodyTooLargeError()
            return message

        async def limited_send(message: Message) -> None:
            if state["over"]:
                # Whatever the app answers after the overflow (FastAPI's 400,
                # a 500) is replaced by the one 413. The app's own status is
                # logged: tests read it to prove no handler acted on the body.
                if message["type"] == "http.response.start":
                    logger.debug(
                        "%s %s: the app answered %s after the body limit; sent 413 instead",
                        scope.get("method"),
                        scope.get("path"),
                        message.get("status"),
                    )
                if not state["answered"] and not state["started"]:
                    state["answered"] = True
                    await _send_413(send, limit)
                elif state["started"] and not state["answered"]:
                    state["answered"] = True
                    # Deliberate: a response the app had already started
                    # before the overflow cannot be replaced, so the rest of
                    # it is dropped and the client sees it cut off. No route
                    # starts a response before reading its body today.
                    logger.warning(
                        "%s %s: body passed the limit after the response started; "
                        "the response is cut off",
                        scope.get("method"),
                        scope.get("path"),
                    )
                return
            if message["type"] == "http.response.start":
                state["started"] = True
            await send(message)

        try:
            await self.app(scope, limited_receive, limited_send)
        except Exception:
            if not state["over"]:
                raise
            # The app's reaction to the overflow (our own error re-raised, or
            # whatever it raised instead) is answered by the 413 below.
            logger.debug(
                "%s %s: the app raised after the body limit; answered 413",
                scope.get("method"),
                scope.get("path"),
                exc_info=True,
            )
        if state["over"] and not state["answered"] and not state["started"]:
            state["answered"] = True
            await _send_413(send, limit)
