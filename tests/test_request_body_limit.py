"""A request body is bounded by ``settings.max_request_body_bytes``, and an
import that does not parse is answered in Pyrite's own words.

The properties:

1. Every request body the app reads -- the import upload, a JSON body, the
   ``/mcp`` mount -- is counted as it arrives against
   ``settings.max_request_body_bytes``. A body one byte over answers ``413``
   with ``code: PAYLOAD_TOO_LARGE``, the app stops reading there (it never
   holds the whole body), and nothing is written. A body at the limit is
   served as before.
2. ``POST /api/entries/import`` answers a file that does not parse with
   ``400`` ``PARSE_ERROR`` and a message of Pyrite's own; the exception's text
   is never in the response and goes to the server log instead.

All through the real app (``create_app``) with TestClient, except the
"stops reading" test, which drives the same app as an ASGI callable so it can
count what was pulled from the client.
"""

from __future__ import annotations

import asyncio
import json
import logging

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")

from pyrite.config import PyriteConfig, Settings, _apply_env_overrides  # noqa: E402

KB = "test-kb"
BOUNDARY = "pyriteboundary0123456789"


def _multipart(filename: str, payload: bytes) -> bytes:
    """A one-file multipart body with a fixed boundary, so its length is known."""
    return (
        (
            f"--{BOUNDARY}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode()
        + payload
        + f"\r\n--{BOUNDARY}--\r\n".encode()
    )


MULTIPART = {"content-type": f"multipart/form-data; boundary={BOUNDARY}"}


def _entry_json(title: str, pad: int = 0) -> bytes:
    return json.dumps([{"title": title, "body": "x" * pad, "entry_type": "note"}]).encode()


def _written(config: PyriteConfig) -> list:
    return sorted(p.name for p in config.knowledge_bases[0].path.rglob("*.md"))


def _post_import(client, body: bytes, fmt: str = "json"):
    return client.post(f"/api/entries/import?kb={KB}&format={fmt}", content=body, headers=MULTIPART)


# --------------------------------------------------------------------------
# Property 1: the import upload
# --------------------------------------------------------------------------


@pytest.mark.parametrize("delta", [0, 1], ids=["at-the-limit", "one-under"])
def test_import_at_or_under_the_limit_is_served(make_client, delta):
    body = _multipart("i.json", _entry_json("Sized Note", pad=2000))
    client, config, _ = make_client(
        kb_name=KB, extra_settings={"max_request_body_bytes": len(body) + delta}
    )
    resp = _post_import(client, body)
    assert resp.status_code == 200, resp.text
    assert resp.json()["imported"] == 1, resp.json()
    assert _written(config) == ["sized-note.md"]


def test_import_one_byte_over_the_limit_is_413_and_writes_nothing(make_client):
    body = _multipart("i.json", _entry_json("Oversized Note", pad=2000))
    client, config, db = make_client(
        kb_name=KB, extra_settings={"max_request_body_bytes": len(body) - 1}
    )
    resp = _post_import(client, body)
    assert resp.status_code == 413, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "PAYLOAD_TOO_LARGE", detail
    assert detail["retryable"] is False
    assert str(len(body) - 1) in detail["message"]
    assert _written(config) == []
    assert db.get_entry("oversized-note", KB) is None


def test_import_over_the_limit_without_content_length_is_413(make_client):
    """A chunked body declares no length; it is counted as it arrives."""
    body = _multipart("i.json", _entry_json("Chunked Note", pad=20_000))
    client, config, _ = make_client(kb_name=KB, extra_settings={"max_request_body_bytes": 4096})

    def chunks():
        for i in range(0, len(body), 1024):
            yield body[i : i + 1024]

    resp = client.post(
        f"/api/entries/import?kb={KB}&format=json", content=chunks(), headers=MULTIPART
    )
    assert resp.status_code == 413, resp.text
    assert resp.json()["detail"]["code"] == "PAYLOAD_TOO_LARGE"
    assert _written(config) == []


def _drive(
    app,
    headers: list[tuple[bytes, bytes]],
    chunks: list[bytes],
    method: str = "POST",
    path: str = "/api/entries/import",
    query: str = f"kb={KB}&format=json",
):
    """Run one HTTP request through the ASGI app; return (status, bytes pulled)."""
    pulled = {"bytes": 0, "messages": 0}
    sent: list[dict] = []
    queue = list(chunks)

    async def receive():
        if not queue:
            return {"type": "http.disconnect"}
        chunk = queue.pop(0)
        pulled["bytes"] += len(chunk)
        pulled["messages"] += 1
        return {"type": "http.request", "body": chunk, "more_body": bool(queue)}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": query.encode(),
        "headers": [(b"host", b"testserver"), *headers],
        "client": ("127.0.0.1", 5000),
        "server": ("testserver", 80),
    }
    asyncio.run(app(scope, receive, send))
    start = next(m for m in sent if m["type"] == "http.response.start")
    return start["status"], pulled


def _app(make_client, limit: int):
    client, config, _ = make_client(kb_name=KB, extra_settings={"max_request_body_bytes": limit})
    return client.app, config


def test_the_app_stops_reading_once_the_body_passes_the_limit(make_client):
    """Without a Content-Length the app pulls at most one chunk past the limit,
    never the rest of a body ten times its size."""
    limit, chunk = 8192, 1024
    payload = _multipart("i.json", _entry_json("Streamed Note", pad=limit * 10))
    chunks = [payload[i : i + chunk] for i in range(0, len(payload), chunk)]
    app, config = _app(make_client, limit)
    status, pulled = _drive(app, [(b"content-type", MULTIPART["content-type"].encode())], chunks)
    assert status == 413
    assert pulled["bytes"] <= limit + chunk, pulled
    assert _written(config) == []


def test_a_declared_length_over_the_limit_is_refused_before_any_read(make_client):
    limit = 8192
    payload = _multipart("i.json", _entry_json("Declared Note", pad=limit * 10))
    app, config = _app(make_client, limit)
    headers = [
        (b"content-type", MULTIPART["content-type"].encode()),
        (b"content-length", str(len(payload)).encode()),
    ]
    status, pulled = _drive(app, headers, [payload])
    assert status == 413
    assert pulled["messages"] == 0, pulled
    assert _written(config) == []


# --------------------------------------------------------------------------
# Property 3: the same bound on every other body the app reads
# --------------------------------------------------------------------------


def test_a_json_write_over_the_limit_is_413_and_writes_nothing(make_client):
    client, config, _ = make_client(kb_name=KB, extra_settings={"max_request_body_bytes": 1024})
    body = {"kb": KB, "title": "Big Json Note", "body": "y" * 4096, "entry_type": "note"}
    resp = client.post("/api/entries", json=body)
    assert resp.status_code == 413, resp.text
    assert resp.json()["detail"]["code"] == "PAYLOAD_TOO_LARGE"
    assert _written(config) == []


@pytest.mark.control(reason="the bound must not refuse an ordinary JSON write under it")
def test_a_json_write_under_the_limit_is_served(make_client):
    client, config, _ = make_client(kb_name=KB, extra_settings={"max_request_body_bytes": 65536})
    body = {"kb": KB, "title": "Small Json Note", "body": "y" * 100, "entry_type": "note"}
    resp = client.post("/api/entries", json=body)
    assert resp.status_code in (200, 201), resp.text
    assert _written(config) == ["small-json-note.md"]


def test_the_mcp_mount_is_bounded(make_client):
    client, _, _ = make_client(kb_name=KB, extra_settings={"max_request_body_bytes": 1024})
    rpc = {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {"pad": "z" * 4096}}
    resp = client.post(
        "/mcp/messages/?session_id=00000000000000000000000000000000",
        json=rpc,
        follow_redirects=False,
    )
    assert resp.status_code == 413, resp.text
    assert resp.json()["detail"]["code"] == "PAYLOAD_TOO_LARGE"


def test_the_auth_routes_are_bounded(make_client):
    client, _, _ = make_client(kb_name=KB, extra_settings={"max_request_body_bytes": 1024})
    resp = client.post("/auth/login", json={"username": "u" * 4096, "password": "p"})
    assert resp.status_code == 413, resp.text
    assert resp.json()["detail"]["code"] == "PAYLOAD_TOO_LARGE"


def _body_routes():
    """Every state-changing route in the app's OpenAPI schema, with whether it
    declares a request body. Found from the app, not from a list in this test,
    so a route added later is covered without editing it."""
    import tempfile
    from pathlib import Path

    from pyrite.config import KBConfig
    from pyrite.server.api import create_app

    tmp = Path(tempfile.mkdtemp())
    (tmp / "kb").mkdir()
    app = create_app(
        PyriteConfig(
            knowledge_bases=[KBConfig(name=KB, path=tmp / "kb")],
            settings=Settings(index_path=tmp / "index.db"),
        )
    )
    routes = []
    for path, ops in sorted(app.openapi()["paths"].items()):
        for method, op in ops.items():
            if method in ("get", "head", "options"):
                continue
            routes.append((method.upper(), path, "requestBody" in op))
    return routes


BODY_ROUTES = _body_routes()


def _concrete(path: str) -> str:
    import re

    return re.sub(r"\{date_str\}", "2026-01-01", re.sub(r"\{(?!date_str)\w+\}", "x", path))


def _inner_statuses(caplog) -> list[int]:
    """The statuses the app itself sent after the overflow, which the
    middleware replaced with its 413 (it logs each one at DEBUG)."""
    import re

    found = []
    for record in caplog.records:
        m = re.search(r"the app answered (\d+) after the body limit", record.getMessage())
        if m:
            found.append(int(m.group(1)))
    return found


@pytest.mark.control(reason="guards the census the per-route test iterates, not the limit")
def test_the_route_census_found_the_app():
    """A guard on the census itself: an empty or tiny list would make the
    per-route test below pass vacuously."""
    with_body = [r for r in BODY_ROUTES if r[2]]
    assert len(with_body) >= 40, BODY_ROUTES
    assert ("POST", "/api/entries/import", True) in BODY_ROUTES


@pytest.mark.parametrize(
    ("method", "path", "declares_body"), BODY_ROUTES, ids=[f"{m} {p}" for m, p, _ in BODY_ROUTES]
)
def test_every_route_reads_at_most_the_limit(make_client, method, path, declares_body, caplog):
    """An oversized chunked body sent to any state-changing route is never read
    past the limit plus one chunk; a route that declares a body answers 413."""
    limit, chunk = 2048, 512
    payload = json.dumps({"kb": KB, "pad": "q" * (limit * 8)}).encode()
    chunks = [payload[i : i + chunk] for i in range(0, len(payload), chunk)]
    client, config, db = make_client(kb_name=KB, extra_settings={"max_request_body_bytes": limit})
    caplog.set_level(logging.DEBUG, logger="pyrite.server.body_limit")
    status, pulled = _drive(
        client.app,
        [(b"content-type", b"application/json")],
        chunks,
        method=method,
        path=_concrete(path),
        query=f"kb={KB}",
    )
    assert pulled["bytes"] <= limit + chunk, (status, pulled)
    if declares_body:
        assert status == 413, (status, pulled)
    if status == 413:
        # No handler acted on a refused request: whatever the app itself
        # answered behind the 413 was a refusal, never a success...
        inner = _inner_statuses(caplog)
        if declares_body:
            # FastAPI read the body, met the limit, and answered its own
            # refusal; an empty list would make the next line vacuous.
            assert inner, (method, path, "the app's own answer was not logged")
        assert all(code >= 400 for code in inner), (method, path, inner)
        # ...and nothing reached the KB or its index.
        assert _written(config) == [], (method, path)
        assert db.count_entries(KB) == 0, (method, path)


def test_the_mcp_messages_route_reads_at_most_the_limit(make_client):
    """The MCP SDK reads a POST /mcp/messages/ body whole (``request.body()``)
    once its session is found; under the middleware no read can pass the
    limit. With no live session the SDK answers before reading, so this pins
    only the bound, not the 413 (the declared-length test above pins that)."""
    limit, chunk = 2048, 512
    rpc = {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {"pad": "z" * (limit * 8)}}
    payload = json.dumps(rpc).encode()
    chunks = [payload[i : i + chunk] for i in range(0, len(payload), chunk)]
    app, _ = _app(make_client, limit)
    status, pulled = _drive(
        app,
        [(b"content-type", b"application/json")],
        chunks,
        path="/mcp/messages/",
        query="session_id=00000000000000000000000000000000",
    )
    assert pulled["bytes"] <= limit + chunk, (status, pulled)


# --------------------------------------------------------------------------
# The setting
# --------------------------------------------------------------------------


def test_the_default_limit_is_32_mib():
    assert Settings().max_request_body_bytes == 32 * 1024 * 1024


def test_the_limit_is_read_from_config_and_written_back():
    config = PyriteConfig.from_dict({"settings": {"max_request_body_bytes": 12345}})
    assert config.settings.max_request_body_bytes == 12345
    assert config.to_dict()["settings"]["max_request_body_bytes"] == 12345


def test_the_limit_is_read_from_the_environment(monkeypatch):
    config = PyriteConfig()
    monkeypatch.setenv("PYRITE_MAX_REQUEST_BODY_BYTES", "4096")
    _apply_env_overrides(config)
    assert config.settings.max_request_body_bytes == 4096


@pytest.mark.parametrize("bad", [0, -1, "lots"])
def test_a_limit_that_is_not_a_positive_integer_is_refused(bad):
    with pytest.raises(ValueError, match="max_request_body_bytes"):
        Settings(max_request_body_bytes=bad)


# --------------------------------------------------------------------------
# Property 2: a file that does not parse
# --------------------------------------------------------------------------

# One malformed file per importer format. The expected exception text comes
# from calling the importer itself, not from the route.
MALFORMED = {
    "json": b'[{"title": "unterminated',
    "yaml": b"- title: [unclosed\n  body: : :\n",
    "markdown": b"---\ntitle: [unclosed\n---\n\nbody\n",
    "csv": b"title,body\n\xff\xfe not utf-8\n",
}


def _importer_error(fmt: str, data: bytes) -> str:
    from pyrite.formats.importers import get_importer_registry

    with pytest.raises(Exception) as info:  # noqa: PT011 - any importer failure
        get_importer_registry().get(fmt)(data)
    return str(info.value)


@pytest.mark.parametrize("fmt", sorted(MALFORMED))
def test_a_malformed_file_answers_parse_error_in_pyrites_words(make_client, fmt, caplog):
    data = MALFORMED[fmt]
    raw = _importer_error(fmt, data)
    assert raw, "the importer must raise with some text for this test to mean anything"
    client, config, _ = make_client(kb_name=KB)
    ext = {"json": "json", "yaml": "yaml", "markdown": "md", "csv": "csv"}[fmt]
    with caplog.at_level(logging.WARNING):
        resp = _post_import(client, _multipart(f"bad.{ext}", data), fmt=fmt)
    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "PARSE_ERROR", detail
    assert raw not in resp.text, resp.text
    assert fmt in detail["message"]
    assert _written(config) == []
    # The detail is the operator's: it is in the server log.
    assert any(raw in (r.getMessage() + str(r.exc_info and r.exc_info[1])) for r in caplog.records)


def test_a_json_file_that_is_not_a_list_of_entries_answers_parse_error(make_client):
    """A top-level number makes the importer raise a TypeError; its text stays out."""
    raw = _importer_error("json", b"5")
    client, _, _ = make_client(kb_name=KB)
    resp = _post_import(client, _multipart("n.json", b"5"))
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["code"] == "PARSE_ERROR"
    assert raw not in resp.text


# --------------------------------------------------------------------------
# The guards behind the limit do not hide it
# --------------------------------------------------------------------------


def test_the_truncated_body_guard_does_not_swallow_the_body_limit():
    """``refuses_truncated_body`` reads the raw JSON body. A read that the body
    limit stopped must propagate, never be taken as "nothing to inspect" and
    let the handler run."""
    from starlette.requests import Request

    from pyrite.server.body_limit import RequestBodyTooLargeError
    from pyrite.server.endpoints.write_refusal import refuses_truncated_body

    async def receive():
        raise RequestBodyTooLargeError()

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/entries",
        "headers": [(b"content-type", b"application/json")],
        "query_string": b"",
    }
    with pytest.raises(RequestBodyTooLargeError):
        asyncio.run(refuses_truncated_body(Request(scope, receive)))


@pytest.mark.control(reason="a body that is not JSON is still left to the handler, as before")
def test_the_truncated_body_guard_still_ignores_a_body_that_is_not_json():
    from starlette.requests import Request

    from pyrite.server.endpoints.write_refusal import refuses_truncated_body

    async def receive():
        return {"type": "http.request", "body": b"{not json", "more_body": False}

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/entries",
        "headers": [(b"content-type", b"application/json")],
        "query_string": b"",
    }
    assert asyncio.run(refuses_truncated_body(Request(scope, receive))) is None


@pytest.mark.parametrize("path", ["/api/entries/import?kb=" + KB + "&format=json", "/api/entries"])
def test_a_413_carries_the_cors_headers(make_client, path):
    """The limit sits inside CORS (api.py), so a browser on an allowed origin
    can read the refusal."""
    origin = "http://localhost:5173"
    client, _, _ = make_client(
        kb_name=KB,
        cors_origins=[origin],
        extra_settings={"max_request_body_bytes": 1024},
    )
    resp = client.post(path, content=b"x" * 4096, headers={"origin": origin, **MULTIPART})
    assert resp.status_code == 413, resp.text
    assert resp.headers.get("access-control-allow-origin") == origin, dict(resp.headers)
