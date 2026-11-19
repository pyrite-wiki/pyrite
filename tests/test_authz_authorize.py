"""`authz.authorize(...)`: REST's one dependency for asking the policy (ADR-0037 §2).

Theme 3a lands the read half: `authorize(Action.KB_READ, KB)` checks every KB
the request names and hands the handler the caller's `ReadScope`;
`authorize(Action.KB_READ, AnyKB)` hands over the scope alone, for routes that
span KBs. These tests drive a small FastAPI app over a real config and index,
with the principal set on the request the way `verify_api_key` sets it, so
each branch of the dependency is entered on the surface where it runs.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from pyrite.config import AuthConfig, KBConfig, PyriteConfig, Settings
from pyrite.server import authz
from pyrite.server.api import get_config, get_db
from pyrite.server.errors import register_pyrite_exception_handler
from pyrite.services.access_policy import KB, Action, AnyKB, ReadScope, Row
from pyrite.storage.database import PyriteDB

PUBLIC, PRIVATE = "public-kb", "private-kb"


def _set_principal(request: Request) -> None:
    """What `verify_api_key` records, chosen by a test header."""
    who = request.headers.get("X-Who", "")
    if who == "anonymous":
        request.state.api_role = "read"
        request.state.anonymous = True
    elif who == "operator":
        request.state.api_role = "read"
    # "" -- no principal at all


@pytest.fixture
def app():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        (tmp / PUBLIC).mkdir()
        (tmp / PRIVATE).mkdir()
        config = PyriteConfig(
            knowledge_bases=[
                KBConfig(name=PUBLIC, path=tmp / PUBLIC, kb_type="generic", default_role="read"),
                KBConfig(name=PRIVATE, path=tmp / PRIVATE, kb_type="generic", default_role="none"),
            ],
            settings=Settings(
                index_path=tmp / "index.db",
                auth=AuthConfig(enabled=True, anonymous_tier="read"),
            ),
        )
        db = PyriteDB(config.settings.index_path)
        application = FastAPI(dependencies=[Depends(_set_principal)])
        register_pyrite_exception_handler(application)
        application.dependency_overrides[get_config] = lambda: config
        application.dependency_overrides[get_db] = lambda: db

        named = authz.authorize(Action.KB_READ, KB)
        spanning = authz.authorize(Action.KB_READ, AnyKB)

        def _scope_json(scope: ReadScope) -> dict:
            return {"kbs": None if scope.unscoped else sorted(scope.kbs)}

        @application.get("/named")
        def named_route(kb: str | None = None, scope: ReadScope = Depends(named)):
            return _scope_json(scope)

        @application.get("/named/{kb_name}")
        def named_path_route(kb_name: str, scope: ReadScope = Depends(named)):
            return _scope_json(scope)

        @application.post("/named-body")
        async def named_body_route(request: Request, scope: ReadScope = Depends(named)):
            return _scope_json(scope)

        @application.get("/spanning")
        def spanning_route(kb: str | None = None, scope: ReadScope = Depends(spanning)):
            return _scope_json(scope)

        @application.get("/twice", dependencies=[Depends(named)])
        def twice_route(kb: str | None = None, scope: ReadScope = Depends(named)):
            return _scope_json(scope)

        try:
            yield application
        finally:
            db.close()


@pytest.fixture
def client(app):
    return TestClient(app)


def _as(who: str) -> dict:
    return {"X-Who": who}


# -- the declaration ------------------------------------------------------------


def test_one_declaration_is_one_dependency():
    """The same (action, resource) is the same callable, so a route that
    declares it on its router and again as a parameter runs it once and the
    §5 guard counts it once."""
    assert authz.authorize(Action.KB_READ, KB) is authz.authorize(Action.KB_READ, KB)
    assert authz.authorize(Action.KB_READ, KB) is not authz.authorize(Action.KB_READ, AnyKB)


@pytest.mark.parametrize(
    ("action", "resource"),
    [(Action.KB_WRITE, KB), (Action.INSTANCE_ADMIN, AnyKB), (Action.KB_READ, Row)],
)
def test_a_declaration_this_theme_does_not_decide_fails_when_declared(action, resource):
    """Writes (3b) and instance routes (3c) are not decided here yet: asking
    for one fails when the route is declared, never on a request."""
    with pytest.raises(NotImplementedError):
        authz.authorize(action, resource)


# -- KB: every KB the request names ------------------------------------------------


def test_a_readable_named_kb_passes_with_the_callers_scope(client):
    resp = client.get("/named", params={"kb": PUBLIC}, headers=_as("anonymous"))
    assert resp.status_code == 200
    assert resp.json() == {"kbs": [PUBLIC]}


def test_an_unreadable_named_kb_answers_like_a_missing_one(client):
    private = client.get("/named", params={"kb": PRIVATE}, headers=_as("anonymous"))
    missing = client.get("/named", params={"kb": "no-such-kb"}, headers=_as("anonymous"))
    assert private.status_code == missing.status_code == 404
    assert private.json()["detail"]["code"] == "KB_NOT_FOUND"
    assert private.json()["detail"]["message"] == f"KB '{PRIVATE}' not found"
    # The one resolver's refusal (#952), byte for byte with the name swapped.
    assert private.text.replace(PRIVATE, "<kb>") == missing.text.replace("no-such-kb", "<kb>")


def test_every_named_kb_is_checked_not_only_the_first(client):
    """A readable KB in the query does not buy a private one in the path."""
    resp = client.get(f"/named/{PRIVATE}", params={"kb": PUBLIC}, headers=_as("anonymous"))
    assert resp.status_code == 404


def test_a_kb_named_in_the_json_body_is_checked(client):
    resp = client.post("/named-body", json={"kb_name": PRIVATE}, headers=_as("anonymous"))
    assert resp.status_code == 404


def test_an_unparseable_json_body_is_refused_not_read_as_naming_no_kb(client):
    resp = client.post(
        "/named-body",
        content=b"{not json",
        headers={**_as("anonymous"), "content-type": "application/json"},
    )
    assert resp.status_code == 400
    assert resp.json() == {
        "detail": {"code": "INVALID_BODY", "message": "Request body could not be parsed"}
    }


def test_naming_no_kb_passes_with_the_scope(client):
    resp = client.get("/named", headers=_as("anonymous"))
    assert resp.status_code == 200
    assert resp.json() == {"kbs": [PUBLIC]}


def test_an_operator_key_is_unscoped_but_an_unknown_kb_is_still_refused(client):
    """A caller with no identity to scope by passes for any KB the instance
    has (`{"kbs": None}`), but a name that is no KB is refused for it too
    (#952): a scope parameter never fails open, and the handler never sees
    an unknown name."""
    resp = client.get("/named", params={"kb": PRIVATE}, headers=_as("operator"))
    assert resp.status_code == 200
    assert resp.json() == {"kbs": None}
    resp = client.get("/named", params={"kb": "no-such-kb"}, headers=_as("operator"))
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "KB_NOT_FOUND"


def test_no_principal_is_refused_not_treated_as_unscoped(client):
    """Fail closed. `verify_api_key` always records a principal on a mounted
    `/api` router, so this is the route mounted without it (the #330 class):
    `readable_kbs` used to read that as "not scoped" and let it through."""
    for path in ("/named", "/spanning"):
        resp = client.get(path, params={"kb": PUBLIC})
        assert resp.status_code == 401, path
        assert resp.json() == {"detail": "Invalid or missing API key"}


def test_no_principal_is_refused_before_the_body_is_read(client):
    """Who is asking comes before what they named: no principal and a bad
    body is 401, not 400."""
    resp = client.post(
        "/named-body", content=b"{not json", headers={"content-type": "application/json"}
    )
    assert resp.status_code == 401


def test_the_scope_is_computed_once_per_request(client, monkeypatch):
    """Declared on the route and again as a parameter: one walk of the KBs."""
    from pyrite.services.access_policy import AccessPolicy

    calls = []
    real = AccessPolicy.read_scope

    def counting(self, principal):
        calls.append(principal)
        return real(self, principal)

    monkeypatch.setattr(AccessPolicy, "read_scope", counting)
    resp = client.get("/twice", params={"kb": PUBLIC}, headers=_as("anonymous"))
    assert resp.status_code == 200
    assert len(calls) == 1


# -- AnyKB: the scope alone ------------------------------------------------------------


def test_any_kb_hands_over_the_scope_without_checking_named_kbs(client):
    """The spanning form filters; it does not 404 (routes that also name a KB
    declare the KB form)."""
    resp = client.get("/spanning", params={"kb": PRIVATE}, headers=_as("anonymous"))
    assert resp.status_code == 200
    assert resp.json() == {"kbs": [PUBLIC]}


def test_any_kb_is_unscoped_for_an_operator_key(client):
    resp = client.get("/spanning", headers=_as("operator"))
    assert resp.json() == {"kbs": None}


# -- api.readable_kbs: the plain-set helper, resolved through get_principal ------------


def _request_as(who: str) -> Request:
    request = Request({"type": "http", "headers": [(b"x-who", who.encode())]})
    _set_principal(request)
    return request


def test_readable_kbs_reads_the_caller_through_get_principal(app):
    import asyncio

    from pyrite.server.api import readable_kbs

    config = app.dependency_overrides[get_config]()
    db = app.dependency_overrides[get_db]()
    assert asyncio.run(readable_kbs(_request_as("anonymous"), config, db)) == {PUBLIC}
    assert asyncio.run(readable_kbs(_request_as("operator"), config, db)) is None


def test_readable_kbs_refuses_no_principal_rather_than_unscoping_it(app):
    import asyncio

    from fastapi import HTTPException

    from pyrite.server.api import readable_kbs

    config = app.dependency_overrides[get_config]()
    db = app.dependency_overrides[get_db]()
    with pytest.raises(HTTPException) as err:
        asyncio.run(readable_kbs(_request_as(""), config, db))
    assert err.value.status_code == 401
