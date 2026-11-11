"""An unidentified caller is never the operator: the holder rule holds it.

A task is *held* when its assignee is an account (``user:<id>``). Only the
holder changes the assignee of a held entry, and no caller but that account
names it (tests/test_held_assignee_on_every_write_path.py, for signed-in
callers). The operator is exempt -- and the operator is only a caller bound
as one: the CLI (its root command), stdio MCP (``Principal.local``), an
operator API key or auth disabled (``Principal.from_api_key``).

A caller with no identity -- the anonymous visitor on an instance whose
``anonymous_tier`` grants a write, an MCP call with no principal, a service
call made where nothing bound a caller (a job, a hook) -- is not the operator.
Where the holder rule applies, its write is refused, through every writer.

The writers are the three places the write pipeline checks the rule
(``KBService._prepare``, ``KBService._update``, ``KBService.claim_entry``) and
every public method that reaches them; each is driven below with nothing
bound. ``WRITERS`` is that list; a writer added to ``KBService`` that can set
an assignee belongs in it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pyrite.config import AuthConfig, KBConfig, PyriteConfig, Settings
from pyrite.exceptions import Forbidden
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB

KB = "tasks"
HOLDER = "user:1"
OTHER = "user:2"


def _as_operator():
    """Bind the operator, explicitly. (Where ``OPERATOR`` does not exist, the
    code under test treated nothing bound as the operator; the fallback keeps
    these tests red there for the behaviour, not for an import.)"""
    from pyrite.services import acting

    return acting.acting_as(getattr(acting, "OPERATOR", None))


def _task(svc: KBService, entry_id: str = "held") -> dict:
    row = svc.db.get_entry(entry_id, KB)
    return row or {}


def _file_assignee(config: PyriteConfig, entry_id: str = "held") -> str:
    path = next(config.get_kb(KB).path.rglob(f"{entry_id}.md"))
    for line in path.read_text().splitlines():
        if line.startswith("assignee:"):
            return line.split(":", 1)[1].strip()
    return ""


@pytest.fixture
def svc(tmp_path):
    """A KB with one open task held by ``HOLDER``, made as the operator."""
    (tmp_path / KB).mkdir()
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name=KB, path=tmp_path / KB, kb_type="generic")],
        settings=Settings(index_path=tmp_path / "index.db", auto_embed=False),
    )
    db = PyriteDB(config.settings.index_path)
    service = KBService(config, db)
    with _as_operator():
        service.create_entry(KB, "held", "Held", "task", "body", status="open", assignee=HOLDER)
    try:
        yield service
    finally:
        db.close()


def _source_file(svc: KBService, tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("src")
    path = d / "pinned.md"
    path.write_text(f"---\ntitle: Pinned\ntype: task\nassignee: {OTHER}\n---\nbody\n")
    return path


#: Every KBService writer that can set an assignee, as a call with nothing
#: bound. Each either changes the held task's assignee or creates an entry
#: that names an account.
WRITERS = {
    "update": lambda svc, _p: svc.update("held", KB, {"assignee": OTHER}),
    "update-clear": lambda svc, _p: svc.update("held", KB, {"assignee": ""}),
    "update-unset": lambda svc, _p: svc.update("held", KB, {}, unset=["assignee"]),
    "update_entry": lambda svc, _p: svc.update_entry("held", KB, assignee="agent:w"),
    "apply_operations": lambda svc, _p: _apply_set(svc),
    "claim_entry": lambda svc, _p: _claim(svc),
    "create": lambda svc, _p: svc.create(
        KB, {"entry_type": "task", "title": "Pinned", "assignee": OTHER}
    ),
    "create_entry": lambda svc, _p: svc.create_entry(
        KB, "pinned", "Pinned", "task", "body", assignee=OTHER
    ),
    "bulk_create_entries": lambda svc, _p: _bulk(svc),
    "add_entry_from_file": lambda svc, p: svc.add_entry_from_file(KB, p),
}


def _apply_set(svc):
    from pyrite.storage.file_operations import Set

    return svc.apply_operations("held", KB, [Set("assignee", OTHER)])


def _claim(svc):
    result = svc.claim_entry("held", KB, OTHER)
    if isinstance(result, dict) and result.get("error_code") == "FORBIDDEN":
        raise Forbidden(result.get("error", "refused"))
    return result


def _bulk(svc):
    [result] = svc.bulk_create_entries(
        KB, [{"entry_type": "task", "title": "Pinned", "assignee": OTHER}]
    )
    if result.get("error_code") == "FORBIDDEN" or "FORBIDDEN" in str(result.get("error", "")):
        raise Forbidden(str(result))
    if not result.get("created") and result.get("status") not in ("created", None):
        raise Forbidden(str(result))
    return result


@pytest.mark.parametrize("writer", sorted(WRITERS))
def test_a_writer_with_nobody_bound_is_held_to_the_rule(svc, writer, tmp_path_factory):
    source = _source_file(svc, tmp_path_factory)
    with pytest.raises(Forbidden):
        WRITERS[writer](svc, source)
    assert _task(svc)["assignee"] == HOLDER
    assert _file_assignee(svc.config) == HOLDER
    assert svc.db.get_entry("pinned", KB) is None


@pytest.mark.control(reason="the operator was always exempt")
@pytest.mark.parametrize("writer", ["update", "update_entry", "claim_entry"])
def test_the_operator_bound_explicitly_is_exempt(svc, writer, tmp_path_factory):
    source = _source_file(svc, tmp_path_factory)
    with _as_operator():
        WRITERS[writer](svc, source)
    assert _task(svc)["assignee"] != HOLDER


@pytest.mark.control(reason="an edit that leaves the assignee alone was never refused")
def test_nobody_bound_may_still_edit_other_fields(svc):
    svc.update("held", KB, {"body": "edited"})
    assert _task(svc)["assignee"] == HOLDER


# -- the surfaces, through their own entry points --------------------------


def test_mcp_with_no_principal_is_held_to_the_rule(svc):
    """An in-process MCP call with no principal is not the operator; stdio
    passes ``Principal.local`` explicitly."""
    from pyrite.server.mcp_server import PyriteMCPServer

    server = PyriteMCPServer(config=svc.config, tier="admin")
    result = server._dispatch_tool(
        "kb_update", {"entry_id": "held", "kb_name": KB, "metadata": {"assignee": "agent:w"}}
    )
    assert result.get("error_code") == "FORBIDDEN", result
    assert _task(svc)["assignee"] == HOLDER


def test_mcp_with_no_principal_is_the_operator_only_when_bound(svc):
    """An embedded caller with no principal binds the operator itself
    (``acting_as(OPERATOR)``); the dispatch keeps that binding rather than
    replacing it with nobody."""
    from pyrite.server.mcp_server import PyriteMCPServer

    server = PyriteMCPServer(config=svc.config, tier="admin")
    with _as_operator():
        result = server._dispatch_tool(
            "kb_update", {"entry_id": "held", "kb_name": KB, "metadata": {"assignee": "agent:w"}}
        )
    assert "error" not in result, result
    assert _task(svc)["assignee"] == "agent:w"


@pytest.fixture
def review_kb(tmp_path):
    """A KB whose writes land for review: a user's go to a change set, and
    only the operator writes its files directly."""
    root = tmp_path / "review"
    root.mkdir()
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="review", path=root, landing="review")],
        settings=Settings(index_path=tmp_path / "rindex.db", auto_embed=False),
    )
    db = PyriteDB(config.settings.index_path)
    try:
        yield config, root
    finally:
        db.close()


def _create_in_review(config):
    from pyrite.server.mcp_server import PyriteMCPServer

    server = PyriteMCPServer(config=config, tier="admin")
    return server._dispatch_tool(
        "kb_create", {"kb_name": "review", "entry_type": "note", "title": "Landed", "body": "b"}
    )


def test_mcp_with_no_principal_lands_as_nobody(review_kb):
    """Where a write lands, no principal is nobody too, as for the holder
    rule: not the operator, and with no account to own a change set, so a
    write to a review KB has no place to land."""
    config, root = review_kb
    result = _create_in_review(config)
    assert result.get("error"), result
    assert not list(root.rglob("*.md"))


def test_mcp_with_no_principal_lands_as_the_operator_when_bound(review_kb):
    config, root = review_kb
    with _as_operator():
        result = _create_in_review(config)
    assert "error" not in result, result
    assert [p.stem for p in root.rglob("*.md")] == ["landed"]


@pytest.mark.control(reason="stdio MCP is the local operator, exempt before and after")
def test_stdio_mcp_is_the_operator(svc):
    from pyrite.server.mcp_server import PyriteMCPServer
    from pyrite.services.access_policy import Principal

    server = PyriteMCPServer(config=svc.config, tier="admin")
    result = server._dispatch_tool(
        "kb_update",
        {"entry_id": "held", "kb_name": KB, "metadata": {"assignee": "agent:w"}},
        client_id="stdio",
        client_kind="local",
        principal=Principal.local(),
    )
    assert "error" not in result, result
    assert _task(svc)["assignee"] == "agent:w"


@pytest.mark.control(reason="the CLI was the operator before, by binding nothing")
def test_cli_is_the_operator(svc, tmp_path):
    """The CLI's root command binds the operator for the command it runs."""
    from tests._world import build_world

    world = build_world(tmp_path / "w", config=svc.config, rest=False)
    try:
        result = world.cli("update", "held", "-k", KB, "-f", "assignee=agent:w")
    finally:
        world.close()
    assert result.exit_code == 0, result.output
    assert _file_assignee(svc.config) == "agent:w"


def test_cli_binding_ends_with_the_command(svc, tmp_path):
    """The operator is bound for the command only: after it returns, a write
    in the same thread with nothing bound is held to the rule again."""
    from tests._world import build_world

    world = build_world(tmp_path / "w", config=svc.config, rest=False)
    try:
        world.cli("kb", "list")
    finally:
        world.close()
    with pytest.raises(Forbidden):
        svc.update("held", KB, {"assignee": "agent:w"})


@pytest.fixture
def anonymous_app(tmp_path):
    """auth enabled, the anonymous visitor granted a write; the task is held."""
    (tmp_path / KB).mkdir()
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name=KB, path=tmp_path / KB, kb_type="generic")],
        settings=Settings(
            index_path=tmp_path / "index.db",
            auto_embed=False,
            auth=AuthConfig(enabled=True, allow_registration=True, anonymous_tier="write"),
        ),
    )
    with PyriteDB(config.settings.index_path) as db, _as_operator():
        KBService(config, db).create_entry(
            KB, "held", "Held", "task", "body", status="open", assignee=HOLDER
        )
    from pyrite.server.api import create_app

    with TestClient(create_app(config=config)) as client:
        yield client, config


@pytest.mark.parametrize(
    "request_",
    [
        ("patch", "/api/entries/held", {"kb": KB, "field": "assignee", "value": "agent:w"}),
        ("patch", "/api/entries/held", {"kb": KB, "field": "assignee", "op": "unset"}),
        ("put", "/api/entries/held", {"kb": KB, "metadata": {"assignee": OTHER}}),
        (
            "post",
            "/api/entries",
            {"kb": KB, "entry_type": "task", "title": "Pinned", "metadata": {"assignee": OTHER}},
        ),
    ],
    ids=["patch", "patch-unset", "put", "post"],
)
def test_the_anonymous_visitor_is_held_to_the_rule(anonymous_app, request_):
    client, config = anonymous_app
    method, url, body = request_
    r = getattr(client, method)(url, json=body)
    assert r.status_code == 403, r.text
    assert _file_assignee(config) == HOLDER
    assert not list(config.get_kb(KB).path.rglob("pinned.md"))


@pytest.mark.control(reason="an anonymous edit that leaves the assignee alone was never refused")
def test_the_anonymous_visitor_may_still_edit_other_fields(anonymous_app):
    client, config = anonymous_app
    r = client.put("/api/entries/held", json={"kb": KB, "body": "anonymous notes"})
    assert r.status_code == 200, r.text
    assert _file_assignee(config) == HOLDER


def test_every_request_starts_with_nobody_acting(tmp_path):
    """`pyrite serve` runs the server inside the CLI's command, which binds the
    operator; a request must not inherit it. A route that binds nobody (any
    route outside `/api`) sees nobody acting, however the serving process was
    bound."""
    import asyncio

    import httpx

    from pyrite.server.api import create_app
    from pyrite.services import acting

    config = PyriteConfig(
        knowledge_bases=[],
        settings=Settings(index_path=tmp_path / "index.db", auto_embed=False),
    )
    app = create_app(config=config)
    app.add_api_route("/probe-acting", lambda: {"acting": repr(acting.acting())})

    async def probe() -> str:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as c:
            r = await c.get("/probe-acting")
        assert r.status_code == 200, r.text
        return r.json()["acting"]

    async def as_the_serving_cli() -> str:
        with _as_operator():
            return await probe()

    assert asyncio.run(as_the_serving_cli()) == "None"
