"""A plugin MCP tool that takes an entry id resolves it through ``KBService.locate`` (#1310).

The MCP half of #911. The property, for every plugin MCP tool that takes an
entry id (found in the tool registry, not listed by hand): with ``kb_name``
absent or blank, an id in exactly one readable KB is that KB, an id in several
is ``AMBIGUOUS`` with ``candidate_kbs``, an id in none is ``ENTRY_NOT_FOUND``
(``legacy_error_code`` ``NOT_FOUND`` until 0.27, as ``kb_get``); a ``kb_name``
naming a KB that lacks the id is ``ENTRY_NOT_FOUND``; one naming the KB that
holds it resolves. Nothing is written with an empty KB name, and a refused call
writes nothing at all.

``test_every_plugin_id_tool_has_a_case`` fails for a tool that takes an id and
is not in ``CASES``; ``test_every_plugin_id_tool_declares_locates_entry`` fails
for one that has a case but does not declare ``locates_entry``, the
declaration the dispatcher resolves by (``PyriteMCPServer._locate_entry_ids``).
The behaviour tests then enter each tool through ``_dispatch_tool``, over the
real index: red for a tool that reaches its handler with ``kb_name`` blank.
"""

from __future__ import annotations

import re
import sqlite3

import pytest

from tests._world import mcp_server

KB_A = "ids-a-1310"
KB_B = "ids-b-1310"

#: Ids of principals, not entries.
PRINCIPAL_PARAMS = frozenset({"user_id", "author_id", "reviewer_id", "assignee_id", "voter_id"})
_ID_PARAM = re.compile(r"(^|_)ids?$")

_BACKLOG = {"kind": "feature", "priority": "medium", "effort": "S"}


class Case:
    """How to drive one tool: what to seed, what else it needs, what it writes."""

    def __init__(self, entry_type, fields, args, *, table=None, writes_file=False, typed=True):
        self.entry_type = entry_type
        self.fields = fields
        self.args = args  # id -> the tool's arguments, minus kb_name
        self.table = table  # plugin table it inserts into (rows must name a real KB)
        self.writes_file = writes_file
        #: Acts on one entry type (counts only that type); False: takes any type.
        self.typed = typed


def _one(arg, **extra):
    return lambda i: {arg: i, **extra}


CASES: dict[str, Case] = {
    "sw_claim": Case(
        "backlog_item",
        {**_BACKLOG, "status": "accepted"},
        _one("item_id", assignee="me"),
        writes_file=True,
    ),
    "sw_submit": Case(
        "backlog_item",
        {**_BACKLOG, "status": "in_progress", "assignee": "me"},
        _one("item_id"),
        writes_file=True,
    ),
    "sw_transition": Case(
        "backlog_item",
        {**_BACKLOG, "status": "proposed"},
        _one("item_id", to_status="accepted"),
        writes_file=True,
    ),
    "sw_review": Case(
        "backlog_item",
        {**_BACKLOG, "status": "review", "assignee": "me"},
        _one("item_id", outcome="changes_requested", reviewer="r", feedback="f"),
        writes_file=True,
    ),
    "sw_log": Case(
        "backlog_item",
        {**_BACKLOG, "status": "in_progress"},
        _one("item_id", summary="did a thing"),
    ),
    "sw_check_ready": Case("backlog_item", {**_BACKLOG, "status": "accepted"}, _one("item_id")),
    "sw_context_for_item": Case(
        "backlog_item", {**_BACKLOG, "status": "accepted"}, _one("item_id")
    ),
    "sw_epic_detail": Case(
        "backlog_item", {**_BACKLOG, "kind": "epic", "status": "accepted"}, _one("epic_id")
    ),
    "sw_prioritize": Case(
        "backlog_item",
        {**_BACKLOG, "status": "accepted"},
        lambda i: {"item_ids": [i]},
        writes_file=True,
    ),
    "wiki_submit_review": Case(
        "article",
        {"quality": "stub", "review_status": "under_review"},
        _one("entry_id", reviewer_id="r", action="comment"),
        table="encyclopedia_review",
    ),
    "wiki_assess_quality": Case("article", {"quality": "stub"}, _one("entry_id", quality="start")),
    "wiki_protect": Case("article", {"quality": "stub"}, _one("entry_id", level="semi")),
    "social_vote": Case(
        "writeup",
        {"author_id": "someone", "writeup_type": "essay"},
        _one("entry_id", user_id="u", value=1),
        table="social_vote",
    ),
    "zettel_graph": Case("note", {}, _one("entry_id"), typed=False),
    "investigation_network": Case("organization", {}, _one("entry_id"), typed=False),
    "investigation_money_flow": Case("organization", {}, _one("entry_id"), typed=False),
    "investigation_ownership_chain": Case("organization", {}, _one("entry_id"), typed=False),
    "investigation_evidence_chain": Case(
        "claim", {"assertion": "a claim", "status": "unverified"}, _one("claim_id")
    ),
    "investigation_promote_claim": Case(
        "claim",
        {"assertion": "a claim", "status": "unverified"},
        _one("claim_id", edge_type="owns", dry_run=True),
    ),
}


def _slug(tool: str) -> str:
    return tool.replace("_", "-") + "-1310"


def _ids(tool: str) -> dict[str, str]:
    slug = _slug(tool)
    return {
        "dup": f"{slug}-dup",
        "one": f"{slug}-one",
        "none": f"{slug}-none",
        "twin": f"{slug}-twin",
        "wrongtype": f"{slug}-wrongtype",
    }


def _seed(config, kb_map):
    """``dup`` in both KBs, ``one`` in the second only, ``none`` nowhere;
    ``twin`` the tool's type in the first and a note in the second;
    ``wrongtype`` a note in the first only."""
    from pyrite.utils.yaml import dump_yaml

    for tool, case in CASES.items():
        ids = _ids(tool)
        for kb, entry_id in ((KB_A, ids["dup"]), (KB_B, ids["dup"]), (KB_B, ids["one"])):
            front = {"id": entry_id, "type": case.entry_type, "title": f"{kb} {entry_id}"}
            front.update(case.fields)
            (kb_map[kb].path / f"{entry_id}.md").write_text(
                f"---\n{dump_yaml(front).rstrip()}\n---\n\n{kb} body\n"
            )
        for kb, entry_id, entry_type in (
            (KB_A, ids["twin"], case.entry_type),
            (KB_B, ids["twin"], "note"),
            (KB_A, ids["wrongtype"], "note"),
        ):
            front = {"id": entry_id, "type": entry_type, "title": f"{kb} {entry_id}"}
            if entry_type == case.entry_type:
                front.update(case.fields)
            (kb_map[kb].path / f"{entry_id}.md").write_text(
                f"---\n{dump_yaml(front).rstrip()}\n---\n\n{kb} body\n"
            )


@pytest.fixture
def server():
    kbs = [{"name": KB_A, "kb_type": "generic"}, {"name": KB_B, "kb_type": "generic"}]
    with mcp_server(kbs, tier="admin", extra_setup=_seed) as ctx:
        yield ctx


def _id_params(schema: dict) -> set[str]:
    props = schema.get("inputSchema", {}).get("properties", {})
    return {p for p in props if _ID_PARAM.search(p) and p not in PRINCIPAL_PARAMS}


def _plugin_id_tools(srv) -> dict[str, dict]:
    """Every registered tool that takes an entry id and a KB, from the tool
    registry the server built (core tools resolve ids in KBService itself)."""
    from pyrite.server import tool_schemas

    core = (
        set(tool_schemas.READ_TOOLS) | set(tool_schemas.WRITE_TOOLS) | set(tool_schemas.ADMIN_TOOLS)
    )
    return {
        name: schema
        for name, schema in srv.tools.items()
        if name not in core
        and _id_params(schema)
        and "kb_name" in schema["inputSchema"].get("properties", {})
    }


@pytest.mark.control(reason="inventory: fails on a tool added later with no case, not on this fix")
def test_every_plugin_id_tool_has_a_case(server):
    found = set(_plugin_id_tools(server["server"]))
    assert found == set(CASES), (
        f"plugin tools taking an entry id with no case: {sorted(found - set(CASES))}; "
        f"cases for tools that no longer exist: {sorted(set(CASES) - found)}"
    )


def test_every_plugin_id_tool_declares_locates_entry(server):
    for name, schema in _plugin_id_tools(server["server"]).items():
        spec = schema.get("locates_entry")
        assert spec, f"{name} takes an entry id but declares no locates_entry"
        props = schema["inputSchema"]["properties"]
        assert set(spec["id_args"]) <= set(props), f"{name}: {spec}"
        assert _id_params(schema) <= set(spec["id_args"]), (
            f"{name} takes {sorted(_id_params(schema))} but locates only {spec['id_args']}"
        )


# -- behaviour -------------------------------------------------------------


def _call(ctx, tool, args, *, readable=None, writable=None):
    return ctx["server"]._dispatch_tool(
        tool, args, readable_kbs=readable, writable_kbs=writable or readable
    )


def _rows(ctx, table):
    if table is None:
        return []
    path = ctx["config"].settings.index_path
    conn = sqlite3.connect(path)
    try:
        return sorted(tuple(r) for r in conn.execute(f"SELECT entry_id, kb_name FROM {table}"))
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def _files(ctx):
    return {p: p.read_bytes() for kb in (KB_A, KB_B) for p in sorted(ctx[kb].path.rglob("*.md"))}


def _missing(result: dict) -> bool:
    return (
        result.get("error_code") in ("ENTRY_NOT_FOUND", "KB_NOT_FOUND")
        or result.get("legacy_error_code") == "NOT_FOUND"
        or "not found" in str(result.get("error", ""))
    )


def _resolved(result: dict) -> bool:
    """The tool reached its work in a KB: no refusal, no missing-entry answer,
    no crash (a handler that read ``args["kb_name"]`` unset answers INTERNAL)."""
    return "error" not in result or (result.get("error_code") is None and not _missing(result))


BLANKS = [None, "", "  "]


def _with_kb(args: dict, kb) -> dict:
    return args if kb is None else {**args, "kb_name": kb}


@pytest.mark.parametrize("kb", BLANKS, ids=["absent", "empty", "spaces"])
@pytest.mark.parametrize("tool", sorted(CASES))
def test_an_id_in_two_kbs_is_refused_without_a_kb(server, tool, kb):
    case, ids = CASES[tool], _ids(tool)
    files, rows = _files(server), _rows(server, case.table)
    result = _call(server, tool, _with_kb(case.args(ids["dup"]), kb))
    assert result.get("error_code") == "AMBIGUOUS", result
    assert result["candidate_kbs"] == [KB_A, KB_B], result
    assert _files(server) == files
    assert _rows(server, case.table) == rows


@pytest.mark.parametrize("kb", BLANKS, ids=["absent", "empty", "spaces"])
@pytest.mark.parametrize("tool", sorted(CASES))
def test_an_id_in_no_kb_is_not_found_and_writes_nothing(server, tool, kb):
    case, ids = CASES[tool], _ids(tool)
    files = _files(server)
    result = _call(server, tool, _with_kb(case.args(ids["none"]), kb))
    assert result.get("error_code") == "ENTRY_NOT_FOUND", result
    assert result.get("legacy_error_code") == "NOT_FOUND", result
    assert _files(server) == files
    if case.table:
        assert _rows(server, case.table) == []


@pytest.mark.parametrize("tool", sorted(CASES))
def test_a_kb_that_lacks_the_id_is_not_found_and_writes_nothing(server, tool):
    case, ids = CASES[tool], _ids(tool)
    files = _files(server)
    result = _call(server, tool, {**case.args(ids["one"]), "kb_name": KB_A})
    assert result.get("error_code") == "ENTRY_NOT_FOUND", result
    assert _files(server) == files
    if case.table:
        assert _rows(server, case.table) == []


#: Tools that already "resolved" a lone id with no KB before #1310, so their
#: case here is a control; the refusals above are their red tests.
_ANSWERED_BEFORE = {
    "wiki_assess_quality": "advisory: never reads the entry, answers for any id",
    "wiki_protect": "advisory: never reads the entry, answers for any id",
    "investigation_money_flow": "queries the default KB and answers empty",
    "investigation_ownership_chain": "queries the default KB and answers empty",
    "investigation_promote_claim": "dry run on the default KB answers without error",
}


def _one_kb_params():
    return [
        pytest.param(
            tool,
            kb,
            marks=[pytest.mark.control(reason=_ANSWERED_BEFORE[tool])]
            if tool in _ANSWERED_BEFORE
            else [],
            id=f"{tool}-{label}",
        )
        for tool in sorted(CASES)
        for kb, label in zip(BLANKS, ["absent", "empty", "spaces"], strict=True)
    ]


@pytest.mark.parametrize(("tool", "kb"), _one_kb_params())
def test_an_id_in_one_kb_resolves_to_it_without_a_kb(server, tool, kb):
    case, ids = CASES[tool], _ids(tool)
    before = _files(server)
    result = _call(server, tool, _with_kb(case.args(ids["one"]), kb))
    assert _resolved(result), result
    if case.table:
        assert _rows(server, case.table) == [(ids["one"], KB_B)]
    if case.writes_file:
        after = _files(server)
        changed = {p for p in after if after[p] != before.get(p)}
        assert {p.parent.name for p in changed} == {KB_B}, result


@pytest.mark.control(reason="a named KB that holds the id answered before, and still does")
@pytest.mark.parametrize("tool", sorted(CASES))
def test_the_kb_that_holds_the_id_resolves(server, tool):
    case, ids = CASES[tool], _ids(tool)
    for kb, entry_id in ((KB_B, ids["one"]), (KB_A, ids["dup"])):
        result = _call(server, tool, {**case.args(entry_id), "kb_name": kb})
        assert _resolved(result), (kb, result)
        if case.table:
            assert (entry_id, kb) in _rows(server, case.table)
    if case.table:
        assert all(kb for _, kb in _rows(server, case.table))


@pytest.mark.parametrize("tool", sorted(CASES))
def test_a_scoped_caller_never_sees_an_unreadable_kb_as_a_candidate(server, tool):
    """Readable {A}: the duplicate is A's alone (resolves); B's lone id is not found,
    and the refusal does not name B."""
    case, ids = CASES[tool], _ids(tool)
    result = _call(server, tool, case.args(ids["dup"]), readable={KB_A})
    assert _resolved(result), result
    refused = _call(server, tool, case.args(ids["one"]), readable={KB_A})
    assert refused.get("error_code") in ("ENTRY_NOT_FOUND", "NOT_FOUND"), refused
    assert KB_B not in str(refused)


@pytest.mark.parametrize("tool", sorted(CASES))
def test_a_tool_counts_only_entries_of_its_type(server, tool):
    """One rule for the type filter, as on the CLI (#1310): a tool that acts on
    one type counts only that type, so a note sharing the id in another KB
    neither makes it ambiguous nor answers it; a tool that takes any type
    refuses the pair. Declared once, in ``locates_entry``."""
    case, ids = CASES[tool], _ids(tool)
    twin = _call(server, tool, case.args(ids["twin"]))
    wrong = _call(server, tool, case.args(ids["wrongtype"]))
    if case.typed:
        assert _resolved(twin), twin
        if case.table:
            assert (ids["twin"], KB_A) in _rows(server, case.table)
        assert wrong.get("error_code") == "ENTRY_NOT_FOUND", wrong
        declared = server["server"].tools[tool]["locates_entry"]["entry_type"]
        assert declared == case.entry_type
    else:
        assert twin.get("error_code") == "AMBIGUOUS", twin
        assert "entry_type" not in server["server"].tools[tool]["locates_entry"]
        assert _resolved(wrong), wrong


@pytest.mark.parametrize("kb", [None, "", "  "], ids=["absent", "empty", "spaces"])
@pytest.mark.parametrize("tool", ["social_vote", "wiki_submit_review"])
def test_the_row_writers_refuse_a_blank_kb_even_called_directly(server, tool, kb):
    """Defence behind the dispatcher: a handler reached without it (the CLI's
    plugin calls, a future caller) still writes no row for KB ''."""
    from pyrite.exceptions import PyriteError

    handler = server["server"].tools[tool]["handler"]
    args = _with_kb(CASES[tool].args(_ids(tool)["one"]), kb)
    with pytest.raises(PyriteError):
        handler(args)
    assert _rows(server, CASES[tool].table) == []
