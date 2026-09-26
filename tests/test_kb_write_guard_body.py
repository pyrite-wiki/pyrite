"""The per-KB write guard checks exactly the KBs the handler acts on.

`requires_kb_tier("write")` reads the KB names a request carries -- path,
query and body -- and checks each. The handler binds its body through
FastAPI. If the two disagree about whether a body was read, a write can be
authorised against no KB (and so against the caller's global role) and then
act on a KB the caller cannot even see.

Two independent defences, each pinned here on its own:

- **The guard.** It reads every body a handler could bind as JSON --
  whatever the Content-Type says -- and refuses a body it could not parse.
  A KB-scoped write that resolves no KB is refused; it never falls back to
  the caller's global role.
- **The FastAPI pin.** From 0.132.0 FastAPI's `strict_content_type` (on by
  default) does not parse a body without a JSON Content-Type as JSON, so a
  handler never binds a body the browser could send without a preflight.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi", reason="fastapi not installed")

from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from pyrite.server.api import get_config, get_db, requires_kb_tier
from tests.auth_seed import SESSION_COOKIE, seed_user, sign_in
from tests.characterization.world import (
    PRIVATE,
    PRIVATE_ENTRY,
    READABLE,
    READABLE_ENTRY,
    WRITE_KEY,
    build_world,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
STRICT_CONTENT_TYPE_SINCE = (0, 132, 0)
_JSON_CONTROL = (
    "a JSON Content-Type always reached the guard's body read; the bug is the other case"
)


@pytest.fixture(scope="module")
def w(tmp_path_factory):
    world = build_world(tmp_path_factory, label="kb-write-guard-body")
    world.index_worker.wait_for_idle(timeout=10)
    yield world
    world.close()


def _writer_cookie(w) -> dict:
    """A signed-in user with the global write role and no grant on PRIVATE."""
    try:
        seed_user(w.db, "guard-body-writer", role="write")
    except Exception:
        pass  # module-scoped world: seeded by an earlier test
    return {SESSION_COOKIE: sign_in(w.app, "guard-body-writer")}


def _file(w, kb: str, entry: str) -> Path:
    matches = sorted((w.tmpdir / kb).rglob(f"{entry}.md"))
    assert matches, f"{entry} not on disk under {kb}"
    return matches[0]


def _headers(with_header: bool) -> dict:
    return {"content-type": "application/json"} if with_header else {}


# ---------------------------------------------------------------------------
# S1: the guard sees the body's KB whatever the Content-Type
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "with_header",
    [
        pytest.param(True, id="json-header", marks=pytest.mark.control(reason=_JSON_CONTROL)),
        pytest.param(False, id="no-content-type"),
    ],
)
def test_put_private_entry_refused_regardless_of_content_type(w, with_header):
    path = _file(w, PRIVATE, PRIVATE_ENTRY)
    before = path.read_bytes()
    body = b'{"kb": "%s", "body": "overwritten"}' % PRIVATE.encode()
    w.release_idle_connections()
    r = w.client.put(
        f"/api/entries/{PRIVATE_ENTRY}",
        content=body,
        headers=_headers(with_header),
        cookies=_writer_cookie(w),
    )
    assert r.status_code == 404, r.text
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "with_header",
    [
        pytest.param(True, id="json-header", marks=pytest.mark.control(reason=_JSON_CONTROL)),
        pytest.param(False, id="no-content-type"),
    ],
)
def test_anonymous_write_tier_put_private_entry_refused(w, with_header):
    path = _file(w, PRIVATE, PRIVATE_ENTRY)
    before = path.read_bytes()
    auth = w.config.settings.auth
    old = auth.anonymous_tier
    auth.anonymous_tier = "write"
    try:
        body = b'{"kb": "%s", "body": "overwritten anonymously"}' % PRIVATE.encode()
        w.release_idle_connections()
        r = w.client.put(
            f"/api/entries/{PRIVATE_ENTRY}", content=body, headers=_headers(with_header)
        )
    finally:
        auth.anonymous_tier = old
    assert r.status_code == 404, r.text
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "content_type",
    [
        "text/plain",
        "application/x-unknown",
        pytest.param(
            "application/json; charset=utf-8", marks=pytest.mark.control(reason=_JSON_CONTROL)
        ),
    ],
)
def test_body_kb_is_checked_under_any_non_form_content_type(w, content_type):
    """A handler that some FastAPI version would bind as JSON -- or not --
    makes no difference: the guard reads the body and checks its KB."""
    path = _file(w, PRIVATE, PRIVATE_ENTRY)
    before = path.read_bytes()
    body = b'{"kb": "%s", "body": "overwritten"}' % PRIVATE.encode()
    w.release_idle_connections()
    r = w.client.put(
        f"/api/entries/{PRIVATE_ENTRY}",
        content=body,
        headers={"content-type": content_type},
        cookies=_writer_cookie(w),
    )
    assert r.status_code == 404, r.text
    assert path.read_bytes() == before


def test_unparseable_body_without_content_type_is_refused(w):
    """A body with no Content-Type is one an older FastAPI parses as JSON: a
    guard that could not parse it does not know which KB it names."""
    w.release_idle_connections()
    r = w.client.put(
        f"/api/entries/{READABLE_ENTRY}",
        content=b'{"kb": "%s", ' % PRIVATE.encode(),
        cookies=_writer_cookie(w),
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "INVALID_BODY"


def test_every_value_of_a_repeated_kb_query_parameter_is_checked(w):
    """FastAPI binds one value of a repeated parameter; the guard checks them
    all, so which one the handler takes cannot matter."""
    w.release_idle_connections()
    r = w.client.delete(
        "/api/entries/no-such-entry",
        params=[("kb", PRIVATE), ("kb", READABLE)],
        cookies=_writer_cookie(w),
    )
    assert r.status_code == 404, r.text
    assert r.json()["detail"]["code"] == "KB_NOT_FOUND", r.text


# ---------------------------------------------------------------------------
# The FastAPI pin: strict_content_type
# ---------------------------------------------------------------------------


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", text)[:3])


def test_pyproject_requires_a_fastapi_with_strict_content_type():
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    specs = [
        s for s in data["project"]["optional-dependencies"]["server"] if s.startswith("fastapi")
    ]
    assert len(specs) == 1, specs
    match = re.search(r">=\s*([\d.]+)", specs[0])
    assert match, specs[0]
    assert _version(match.group(1)) >= STRICT_CONTENT_TYPE_SINCE, specs[0]


@pytest.mark.control(reason="checks the environment CI installs, not the code under test")
def test_installed_fastapi_has_strict_content_type():
    assert _version(fastapi.__version__) >= STRICT_CONTENT_TYPE_SINCE, fastapi.__version__


@pytest.mark.control(
    reason="pins FastAPI's strict_content_type on the installed version; holds without the guard fix"
)
def test_handler_does_not_bind_a_body_without_a_json_content_type(w):
    """A caller the guard admits (an operator key, unscoped) sends a JSON
    body with no Content-Type: FastAPI refuses to bind it, nothing is written.
    This is the pin's own behaviour, independent of the guard."""
    path = _file(w, READABLE, READABLE_ENTRY)
    before = path.read_bytes()
    body = b'{"kb": "%s", "body": "written without a content type"}' % READABLE.encode()
    w.release_idle_connections()
    r = w.client.put(
        f"/api/entries/{READABLE_ENTRY}", content=body, headers={"X-API-Key": WRITE_KEY}
    )
    assert r.status_code == 422, r.text
    assert path.read_bytes() == before


# ---------------------------------------------------------------------------
# No KB resolved: refused, never the global role
# ---------------------------------------------------------------------------


def _bare_app(role: str) -> TestClient:
    """A route guarded by requires_kb_tier("write") that names no KB, and a
    signed-in user whose *global* role is `role`."""
    app = FastAPI()

    async def _as_user(request: Request) -> None:
        request.state.api_role = role
        request.state.auth_user = {"id": 1, "role": role}

    app.dependency_overrides[get_config] = lambda: None
    app.dependency_overrides[get_db] = lambda: None

    @app.post("/write", dependencies=[Depends(_as_user), Depends(requires_kb_tier("write"))])
    def write() -> dict:
        return {"wrote": True}

    return TestClient(app)


@pytest.mark.parametrize("role", ["write", "admin"])
def test_kb_scoped_write_naming_no_kb_is_refused(role):
    """Even a global admin: a KB-scoped write with no KB has nothing to be
    authorised against, and the handler would act on whatever it defaults to."""
    client = _bare_app(role)
    r = client.post("/write", json={"body": "no kb here"})
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "KB_REQUIRED"
    assert "wrote" not in r.text
