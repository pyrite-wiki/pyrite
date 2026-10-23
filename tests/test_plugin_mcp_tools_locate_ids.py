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
#: An entry id by name (``entry_id``, ``item_ids``, ``entry``) or by its
#: description ("Place item after this item ID"): the CLI walk's rule
#: (``tests/test_ambiguous_id_is_refused.py``, ``_is_entry_id_param``), so an id
#: argument named ``after`` is seen here as it is there.
_ID_NAME = re.compile(r"(^|_)(ids?|entry|entries|item|items)$")
_ID_HELP = re.compile(r"\b(entry|item|article|writeup|claim|epic|task)\s+ids?\b", re.I)

#: Tools the walk finds that take an id and act on no entry it names; each
#: names its reason and, for a bug, its issue.
EXCLUDED = {
    "sw_backlog": "epic is a list filter, not a pick or a write; blank kb_name filters to "
    "nothing (#1314)",
}

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
    return {
        p
        for p, prop in props.items()
        if p not in PRINCIPAL_PARAMS
        and (_ID_NAME.search(p) or _ID_HELP.search(str(prop.get("description") or "")))
    }


def _plugin_id_tools(srv) -> dict[str, dict]:
    """Every registered tool that takes an entry id and a KB, from the tool
    registry the server built (core tools resolve ids in KBService itself).
    A KB argument is any name the dispatcher checks (``KB_ARGUMENT_NAMES``),
    not only ``kb_name``."""
    from pyrite.server import tool_schemas
    from pyrite.server.mcp_server import KB_ARGUMENT_NAMES

    core = (
        set(tool_schemas.READ_TOOLS) | set(tool_schemas.WRITE_TOOLS) | set(tool_schemas.ADMIN_TOOLS)
    )
    return {
        name: schema
        for name, schema in srv.tools.items()
        if name not in core
        and _id_params(schema)
        and set(KB_ARGUMENT_NAMES) & set(schema["inputSchema"].get("properties", {}))
    }


@pytest.mark.control(reason="inventory: fails on a tool added later with no case, not on this fix")
def test_every_plugin_id_tool_has_a_case(server):
    found = set(_plugin_id_tools(server["server"]))
    known = set(CASES) | set(EXCLUDED)
    assert found == known, (
        f"plugin tools taking an entry id with no case: {sorted(found - known)}; "
        f"cases for tools that no longer exist: {sorted(known - found)}"
    )


@pytest.mark.control(reason="pins the widened walk: it must see ids named for neither *_id")
def test_the_walk_sees_ids_by_description_and_kbs_by_any_name():
    """The walk itself: ``after``/``before`` are ids by their description, and
    a tool whose KB argument is ``kb`` is seen as taking a KB."""
    schema = {
        "inputSchema": {
            "properties": {
                "kb": {"type": "string"},
                "after": {"description": "Place item after this item ID"},
                "before": {"description": "Place item before this item ID"},
                "voter_id": {"description": "Entry ID of nobody"},
                "limit": {"description": "How many"},
            }
        }
    }

    class Srv:
        tools = {"probe_tool": schema}

    assert _id_params(schema) == {"after", "before"}
    assert set(_plugin_id_tools(Srv)) == {"probe_tool"}


def test_every_plugin_id_tool_declares_locates_entry(server):
    for name, schema in _plugin_id_tools(server["server"]).items():
        if name in EXCLUDED:
            continue
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


# -- sw_prioritize's anchors are ids too -------------------------------------


def _prioritize(kb, items, anchor_arg, anchor):
    """``items``: ("item_ids", id) or ("item_id", id); ``kb`` None leaves it out."""
    arg, item = items
    args = {arg: [item] if arg == "item_ids" else item, anchor_arg: anchor}
    return _with_kb(args, kb)


@pytest.mark.parametrize("items", ["item_ids", "item_id"])
@pytest.mark.parametrize("anchor_arg", ["after", "before"])
def test_sw_prioritize_locates_its_anchor_with_its_items(server, anchor_arg, items):
    """``after`` and ``before`` name a backlog item. With no KB named, the
    anchor must be in the KB the items resolve to: one held elsewhere, held
    nowhere or held in two KBs is refused by the dispatcher (typed, before
    the handler), and nothing is written; an anchor in the items' KB
    resolves there."""
    ids = _ids("sw_prioritize")
    a_only = ids["twin"]  # a backlog item in A only (B's twin is a note)

    def run(kb, item, anchor):
        before = _files(server)
        result = _call(server, "sw_prioritize", _prioritize(kb, (items, item), anchor_arg, anchor))
        after = _files(server)
        return result, {p.parent.name for p in after if after[p] != before.get(p)}

    for kb in BLANKS:
        result, changed = run(kb, ids["one"], a_only)
        assert result.get("error_code") == "ENTRY_NOT_FOUND", (kb, result)
        assert changed == set(), (kb, result)

        result, changed = run(kb, ids["one"], ids["none"])
        assert result.get("error_code") == "ENTRY_NOT_FOUND", (kb, result)
        assert changed == set(), (kb, result)

        result, changed = run(kb, ids["dup"], ids["dup"])
        assert result.get("error_code") == "AMBIGUOUS", (kb, result)
        assert result["candidate_kbs"] == [KB_A, KB_B], (kb, result)
        assert changed == set(), (kb, result)

    result, changed = run(KB_A, ids["dup"], ids["one"])
    assert result.get("error_code") == "ENTRY_NOT_FOUND", result
    assert changed == set(), result

    # The items resolve to B, so the anchor held in A and B is B's. (A list
    # placed --before an anchor at rank 0 is the handler's own "no room"
    # answer, which it gives only after finding the anchor in B.)
    result, changed = run(None, ids["one"], ids["dup"])
    assert result.get("error_code") is None and not _missing(result), result
    assert changed <= {KB_B}, result
    if "error" not in result:
        assert changed == {KB_B}, result


# -- the handler's KB is always a checked KB ---------------------------------


def _other_kb_args() -> list[str]:
    """Every KB argument name the dispatcher checks except ``kb_name``, the
    one the lookup fills in. A call may name a KB under any of them and leave
    ``kb_name`` blank."""
    from pyrite.server.mcp_server import KB_ARGUMENT_NAMES

    return [n for n in KB_ARGUMENT_NAMES if n != "kb_name"]


def _named(arg: str, kb: str):
    return [kb] if arg == "kb_names" else kb


@pytest.mark.parametrize(
    "named",
    [
        pytest.param(KB_A, id="writable-named"),
        pytest.param(
            KB_B,
            id="read-only-named",
            marks=pytest.mark.control(
                reason="negative side: a write tool naming the read-only KB is refused by "
                "the write check with or without the fix; writable-named is the evidence"
            ),
        ),
    ],
)
@pytest.mark.parametrize("arg", _other_kb_args())
@pytest.mark.parametrize("tool", sorted(CASES))
def test_the_handler_acts_only_in_a_kb_the_checks_ran_on(server, monkeypatch, tool, arg, named):
    """Readable {A, B}, writable {A}; the id lives in B only; ``kb_name`` is
    blank and a KB is named under another KB argument. The KB the handler
    works in is one the dispatcher's KB checks ran on, and a write tool never
    changes B (the caller may only read it)."""
    from pyrite.server import mcp_server as mod

    srv = server["server"]
    case, ids = CASES[tool], _ids(tool)
    checked: list[object] = []
    real_refusal = mod._kb_refusal

    def recording_refusal(svc, kb_name, readable_kbs):
        checked.append(kb_name)
        return real_refusal(svc, kb_name, readable_kbs)

    monkeypatch.setattr(mod, "_kb_refusal", recording_refusal)
    seen: list[object] = []
    real_handler = srv.tools[tool]["handler"]

    def spy(args, **kw):
        seen.append(args.get("kb_name"))
        return real_handler(args, **kw)

    monkeypatch.setitem(srv.tools[tool], "handler", spy)

    files, rows = _files(server), _rows(server, case.table)
    args = {**case.args(ids["one"]), arg: _named(arg, named)}
    result = _call(server, tool, args, readable={KB_A, KB_B}, writable={KB_A})

    for kb in seen:
        assert kb in checked, f"{tool} worked in {kb!r}; the checks ran on {checked}: {result}"
    if srv._tool_tiers.get(tool, "read") != "read":
        assert "error" in result, result
        assert _files(server) == files, result
        assert _rows(server, case.table) == rows, result


#: Every way an id argument names nothing: missing, empty, whitespace; for a
#: list argument, empty or holding only blanks.
_BLANK_IDS = {
    "missing": lambda arg, is_list: None,
    "empty": lambda arg, is_list: [] if is_list else "",
    "spaces": lambda arg, is_list: ["", "  "] if is_list else "  ",
}


def _locating_tools(srv) -> dict[str, dict]:
    """Every registered tool that declares ``locates_entry``, from the registry."""
    return {name: tool for name, tool in srv.tools.items() if tool.get("locates_entry")}


def test_a_call_with_every_id_blank_is_refused_before_anything_runs(server, monkeypatch):
    """No handler is called with an id the dispatcher has not resolved: a
    ``locates_entry`` call whose id arguments are all blank is VALIDATION_FAILED
    (not retryable) before any KB check or handler, whether it names no KB, a
    KB it may write, a KB it may only read, or no KB that exists, and for a
    scoped caller and an unscoped one alike. Nothing is written in any KB.
    Every such tool in the registry, not a list."""
    srv = server["server"]
    tools = _locating_tools(srv)
    assert set(tools) == set(CASES), sorted(set(tools) ^ set(CASES))
    reached: list[str] = []
    for name, tool in tools.items():
        monkeypatch.setitem(
            tool, "handler", lambda args, _n=name, **kw: reached.append(_n) or {"ok": True}
        )
    files = _files(server)
    rows = {t: _rows(server, c.table) for t, c in CASES.items() if c.table}

    for name, tool in sorted(tools.items()):
        props = tool["inputSchema"]["properties"]
        others = CASES[name].args("x")
        for shape, blank in _BLANK_IDS.items():
            args = dict(others)
            for arg in tool["locates_entry"]["id_args"]:
                value = blank(arg, props.get(arg, {}).get("type") == "array")
                if value is None:
                    args.pop(arg, None)
                else:
                    args[arg] = value
            for kb in (None, KB_A, KB_B, "no-such-kb-1310"):
                for scope in ({}, {"readable_kbs": {KB_A, KB_B}, "writable_kbs": {KB_A}}):
                    # A client per call: the rate limit runs first, by design,
                    # and hundreds of calls from one client would hit it.
                    result = srv._dispatch_tool(
                        name,
                        _with_kb(args, kb),
                        client_id=f"blank-{name}-{shape}-{kb}-{bool(scope)}",
                        **scope,
                    )
                    where = (name, shape, kb, bool(scope), result)
                    assert result.get("error_code") == "VALIDATION_FAILED", where
                    assert result.get("retryable") is False, where
    assert reached == []
    assert _files(server) == files
    assert {t: _rows(server, c.table) for t, c in CASES.items() if c.table} == rows


# -- over the wire: the SDK validates arguments against the schema first -----


def test_every_locating_tool_leaves_kb_name_optional(server):
    """The MCP SDK checks a call's arguments against the tool's inputSchema
    before ``_dispatch_tool`` sees it, so a schema that requires ``kb_name``
    refuses the very call the lookup exists for. Every ``locates_entry`` tool,
    from the registry and from what the SDK lists."""
    import asyncio

    from mcp.shared.memory import create_connected_server_and_client_session

    srv = server["server"]
    # The case tools as well as the declared ones, so the check never runs
    # over an empty set (a tool that stopped declaring is still checked).
    tools = {**_locating_tools(srv), **{n: srv.tools[n] for n in CASES}}
    for name, tool in tools.items():
        assert "kb_name" not in tool["inputSchema"].get("required", []), name

    async def listed():
        async with create_connected_server_and_client_session(srv.build_sdk_server()) as session:
            return {t.name: t.inputSchema for t in (await session.list_tools()).tools}

    schemas = asyncio.run(listed())
    for name in tools:
        assert "kb_name" not in schemas[name].get("required", []), name


def _sdk_call(srv, tool: str, arguments: dict) -> dict:
    """One ``tools/call`` through the SDK server ``handle_sse`` builds, over
    the SDK's in-memory client session: schema validation included."""
    import asyncio
    import json

    from mcp.shared.memory import create_connected_server_and_client_session

    async def run():
        async with create_connected_server_and_client_session(srv.build_sdk_server()) as session:
            result = await session.call_tool(tool, arguments)
            text = result.content[0].text
            try:
                return json.loads(text)
            except ValueError:  # the SDK's own refusal is plain text
                return {"error": text, "sdk": True}

    return asyncio.run(run())


@pytest.mark.parametrize("tool", sorted(CASES))
def test_a_call_over_the_sdk_with_no_kb_name_is_located(server, tool):
    """Through the SDK server path, a call with no ``kb_name`` reaches the
    lookup: an id in one KB resolves there (and a write lands there), an id in
    two is AMBIGUOUS -- the dispatcher's answers, not the SDK's schema error."""
    case, ids = CASES[tool], _ids(tool)
    srv = server["server"]
    before = _files(server)
    result = _sdk_call(srv, tool, case.args(ids["one"]))
    assert not result.get("sdk"), result
    assert result.get("error_code") is None and not _missing(result), result
    if case.table:
        assert _rows(server, case.table) == [(ids["one"], KB_B)]
    if case.writes_file:
        after = _files(server)
        assert {p.parent.name for p in after if after[p] != before.get(p)} == {KB_B}, result

    ambiguous = _sdk_call(srv, tool, case.args(ids["dup"]))
    assert ambiguous.get("error_code") == "AMBIGUOUS", ambiguous


def _write_case_tools() -> list[str]:
    """The cases whose tool writes a file or a row: one family each of sw
    files, social and wiki rows."""
    return sorted(t for t, c in CASES.items() if c.writes_file or c.table)


@pytest.mark.parametrize("arg", ["kb", None], ids=["other-arg-named", "nothing-named"])
@pytest.mark.parametrize("tool", _write_case_tools())
def test_a_write_that_succeeds_lands_in_the_checked_kb(server, monkeypatch, tool, arg):
    """The success side of the property: a scoped caller who may read A and B
    and write B, the id in B only, ``kb_name`` blank. The call succeeds, the
    checks ran on B, the handler worked in B, and the file or row lands in B
    and nowhere else."""
    from pyrite.server import mcp_server as mod

    srv = server["server"]
    case, ids = CASES[tool], _ids(tool)
    checked: list[object] = []
    real_refusal = mod._kb_refusal

    def recording_refusal(svc, kb_name, readable_kbs):
        checked.append(kb_name)
        return real_refusal(svc, kb_name, readable_kbs)

    monkeypatch.setattr(mod, "_kb_refusal", recording_refusal)
    before = _files(server)
    args = case.args(ids["one"]) if arg is None else {**case.args(ids["one"]), arg: KB_B}
    result = _call(server, tool, args, readable={KB_A, KB_B}, writable={KB_B})

    assert _resolved(result), result
    assert KB_B in checked, checked
    if case.table:
        assert _rows(server, case.table) == [(ids["one"], KB_B)], result
    if case.writes_file:
        after = _files(server)
        assert {p.parent.name for p in after if after[p] != before.get(p)} == {KB_B}, result
