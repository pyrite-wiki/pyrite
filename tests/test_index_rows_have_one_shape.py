"""A field has one JSON type whichever reader returns it (#1503).

The property, from the user's side: "a field has one JSON type whichever
tool I read it with, and what I read I can write back unchanged". An index
row used to have no single projection: the raw-SQL readers handed back the
``metadata`` column as JSON text and ``aliases`` as text, the ORM readers
decoded them, and every reader returned a TEXT column's value (a task's or a
declared number's ``priority: 7``) as ``"7"``, which ``kb_update`` then wrote
back into the file as ``priority: "7"``.

The rule now lives in one place (principle 8): every reader of entry rows
returns them through ``shape_entry_row`` (``pyrite/storage/backends/
base_backend.py``), which decodes ``metadata`` to an object and ``aliases`` to
a list, lifts declared fields, and casts a TEXT column's value by the field's
declared type (``KBSchema.type_fields``; option B (i) of the maintainer's
decision of 2026-11-16). The single-entry read casts with the same function.

Where the code already has the list of readers, the cells here are
parametrised from it, so a new reader cannot be added without one:

- the MCP tool table (``PyriteMCPServer.tools``, iterated as
  ``tests/test_mcp_tool_registry_is_scoped.py`` iterates it): every tool is
  classified, and the row readers are compared with the file;
- the storage backends (``SearchBackend``'s methods), on SQLite and, in the
  ``pg`` job, Postgres;
- a structural scan of ``pyrite/storage`` for SQL that selects entry rows in
  a function that does not return through the shaper.

Expected values are written by hand from the files below, never computed by
the shaper (``tdd.md``: an oracle that restates the code shares its blind
spots).
"""

from __future__ import annotations

import ast
import json
import re
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from pyrite.config import KBConfig
from tests._world import build_world

REPO = Path(__file__).resolve().parent.parent

KB = "emp"
SW = "sw"

#: kb.yaml for the generic KB: `chore` declares `priority` a number and
#: `urgent` a checkbox. The index keeps `priority` in a TEXT column.
KB_YAML = """\
name: emp
types:
  chore:
    fields:
      priority: {type: number}
      urgent: {type: checkbox}
"""

FILES = {
    # A declared number in a TEXT column, a nested undeclared object in the
    # bag, an empty aliases list.
    "chores/c-sweep.md": (
        "---\nid: c-sweep\ntype: chore\ntitle: Sweep the zebra yard\nstatus: open\n"
        "priority: 7\nimportance: 4\nassignee: alice\ndue_date: 2020-01-01\n"
        "location: Paris\ntags:\n- errand\naliases: []\nroom:\n  floor: 2\n"
        "---\nzebra stripes\n"
    ),
    # The issue's case: a task's priority, an undeclared key, an alias.
    "tasks/register-domain.md": (
        "---\nid: register-domain\ntype: task\ntitle: Register the zebra domain\n"
        "status: open\npriority: 7\nimportance: 4\nassignee: alice\n"
        "due_date: 2020-01-01\nlocation: Paris\ntags:\n- errand\naliases:\n- Reg\n"
        "colour: blue\n---\nzebra domain\n"
    ),
    # Nothing but the basics: `metadata` and `aliases` empty (the row's
    # `metadata` is set to NULL below, an old row's shape).
    "notes/n-plain.md": (
        "---\nid: n-plain\ntype: note\ntitle: Plain zebra note\nstatus: open\n"
        "importance: 4\nassignee: alice\ndue_date: 2020-01-01\nlocation: Paris\n"
        "tags:\n- errand\n---\nzebra plain\n"
    ),
}

SW_FILES = {
    # A declared field held in the bag (`rank`), lifted on every reader.
    "backlog/b-one.md": (
        "---\nid: b-one\ntype: backlog_item\ntitle: One zebra item\nkind: bug\n"
        "status: open\npriority: high\nimportance: 4\nassignee: alice\nrank: 3\n"
        "due_date: 2020-01-01\nlocation: Paris\ntags:\n- errand\n---\nzebra item\n"
    ),
}

#: What each entry's row says, by hand from the files above. `metadata`
#: lists the undeclared keys every reader's bag must hold with these values
#: (the ORM row keeps the whole bag; the single read keeps only undeclared
#: keys, #1025 option B), so the check is "holds these", by key.
EXPECTED: dict[str, dict[str, Any]] = {
    "c-sweep": {
        "priority": 7,
        "importance": 4,
        "aliases": [],
        "metadata": {"room": {"floor": 2}},
    },
    "register-domain": {
        "priority": 7,
        "importance": 4,
        "aliases": ["Reg"],
        "metadata": {"colour": "blue"},
    },
    "n-plain": {"importance": 4, "aliases": [], "metadata": {}},
    "b-one": {"priority": "high", "importance": 4, "aliases": [], "rank": 3, "metadata": {}},
}


def _rows_check(rows: list[dict[str, Any]], *, reader: str, skip: set[str] = frozenset()) -> int:
    """Compare every row of a known entry with EXPECTED; return how many."""
    seen = 0
    for row in rows:
        want = EXPECTED.get(row.get("id"))
        if want is None:
            continue
        seen += 1
        # Every row reader here carries `metadata`: a cell whose rows lost it
        # would pass without checking anything.
        assert "metadata" in row, (reader, row["id"], sorted(row))
        for key, value in want.items():
            if key not in row or (row["id"], key) in skip or key in skip:
                continue
            got = row[key]
            if key == "metadata":
                assert isinstance(got, dict), (reader, row["id"], key, got)
                for k, v in value.items():
                    assert got.get(k) == v, (reader, row["id"], k, got)
            else:
                assert got == value and type(got) is type(value), (
                    reader,
                    row["id"],
                    key,
                    got,
                    value,
                )
    return seen


# ---------------------------------------------------------------------------
# The world: real files, the real indexer, the real surfaces
# ---------------------------------------------------------------------------


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


@pytest.fixture(scope="module")
def world() -> Iterator[Any]:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        emp, sw = root / KB, root / SW
        _write(emp, FILES)
        (emp / "kb.yaml").write_text(KB_YAML)
        _write(sw, SW_FILES)
        w = build_world(
            root / "world",
            kbs=[
                KBConfig(name=KB, path=emp, kb_type="generic"),
                KBConfig(name=SW, path=sw, kb_type="software"),
            ],
        )
        # An old row's shape: the `metadata` column NULL.
        w.db.execute_write_sql("UPDATE entry SET metadata = NULL WHERE id = 'n-plain'")
        try:
            yield w
        finally:
            w.close()


def _mcp(world, name: str, args: dict[str, Any]) -> dict[str, Any]:
    # A server per call: a plugin's tool context is process-wide and names
    # the database of the last server built, so a server kept across another
    # world's (closed with it) would answer from a closed database.
    result = world.mcp("admin")._dispatch_tool(name, args)
    assert "error" not in result, (name, result)
    return result


def _entries(key: str) -> Callable[[dict[str, Any]], list[dict[str, Any]]]:
    return lambda result: list(result.get(key) or [])


#: The MCP tools that return index entry rows, with the call that returns
#: the seeded entries and where the rows are in its result.
MCP_ROW_READERS: dict[str, list[tuple[dict[str, Any], Callable]]] = {
    "kb_get": [
        ({"kb_name": KB, "entry_id": eid}, lambda r: [r["entry"]])
        for eid in ("c-sweep", "register-domain", "n-plain")
    ]
    + [({"kb_name": SW, "entry_id": "b-one"}, lambda r: [r["entry"]])],
    "kb_list_entries": [({"kb_name": k}, _entries("entries")) for k in (KB, SW)],
    "kb_recent": [({"kb_name": k}, _entries("entries")) for k in (KB, SW)],
    "kb_batch_read": [
        (
            {
                "entries": [
                    {"entry_id": "c-sweep", "kb_name": KB},
                    {"entry_id": "register-domain", "kb_name": KB},
                    {"entry_id": "n-plain", "kb_name": KB},
                    {"entry_id": "b-one", "kb_name": SW},
                ]
            },
            _entries("entries"),
        )
    ],
    "kb_find_by_status": [
        ({"kb_name": k, "status": "open"}, _entries("entries")) for k in (KB, SW)
    ],
    "kb_find_by_assignee": [
        ({"kb_name": k, "assignee": "alice"}, _entries("entries")) for k in (KB, SW)
    ],
    "kb_find_overdue": [
        ({"kb_name": k, "as_of": "2030-01-01"}, _entries("entries")) for k in (KB, SW)
    ],
    "kb_find_by_location": [
        ({"kb_name": k, "location": "Paris"}, _entries("entries")) for k in (KB, SW)
    ],
    "kb_search": [
        ({"kb_name": k, "query": "zebra", **extra}, _entries("results"))
        for k in (KB, SW)
        for extra in ({}, {"fields": ["metadata", "aliases", "priority", "importance", "rank"]})
    ],
}

#: Tools that build their own dicts from rows (not the index row shape).
#: Each is called where cheap and every `metadata`/`aliases` it returns,
#: anywhere in the result, must be an object/list; the args are None where
#: the tool needs state these files do not have.
MCP_OWN_DICTS: dict[str, dict[str, Any] | None] = {
    "kb_read_body": {"kb_name": KB, "entry_id": "c-sweep"},
    "kb_orient": {"kb_name": KB},
    "kb_timeline": {},
    "kb_backlinks": {"kb_name": KB, "entry_id": "c-sweep"},
    "kb_discover_neighbors": None,
    "kb_batch_suggest": None,
    "kb_tags": {"kb_name": KB},
    "kb_stats": {},
    "task_list": {"kb_name": KB},
    "task_status": {"kb_name": KB, "task_id": "register-domain"},
    "task_subtree": {"kb_name": KB, "task_id": "register-domain"},
    "task_ancestors": {"kb_name": KB, "task_id": "register-domain"},
    "task_blocked_by": {"kb_name": KB, "task_id": "register-domain"},
    "task_critical_path": {"kb_name": KB, "task_id": "register-domain"},
    "sw_adrs": {"kb_name": SW},
    "sw_backlog": {"kb_name": SW},
    "sw_board": {"kb_name": SW},
    "sw_check_ready": None,
    "sw_component": None,
    "sw_context_for_item": None,
    "sw_conventions": {"kb_name": SW},
    "sw_epic_detail": None,
    "sw_epics": {"kb_name": SW},
    "sw_milestones": {"kb_name": SW},
    "sw_pull_next": {"kb_name": SW},
    "sw_review_queue": {"kb_name": SW},
    "sw_standards": {"kb_name": SW},
    "sw_validations": {"kb_name": SW},
    "wiki_quality_stats": None,
    "wiki_review_queue": None,
    "wiki_stubs": None,
    "social_newest": None,
    "social_top": None,
    "social_reputation": None,
    "zettel_graph": None,
    "zettel_inbox": None,
    "list_edge_types": {},
    "kb_list": {},
    "kb_schema": {"kb_name": KB},
}

#: Tools whose result carries no entry rows: writes, registry, QA, admin.
MCP_NO_ENTRY_ROWS = {
    "kb_create": "write: returns the created entry's id and outcome",
    "kb_update": "write: returns the outcome",
    "kb_delete": "write",
    "kb_bulk_create": "write",
    "kb_link": "write",
    "kb_commit": "git: returns the commit",
    "kb_push": "git",
    "kb_manage": "admin",
    "kb_index_sync": "admin: index counts",
    "kb_index_job_status": "admin: job status",
    "kb_registry_add": "registry",
    "kb_registry_health": "registry: health counts",
    "kb_registry_reindex": "registry",
    "kb_registry_remove": "registry",
    "kb_qa_assess": "QA: findings",
    "kb_qa_status": "QA: counts",
    "kb_qa_validate": "QA: findings",
    "task_create": "write",
    "task_update": "write",
    "task_claim": "write",
    "task_checkpoint": "write",
    "task_decompose": "write",
    "sw_create_adr": "write",
    "sw_create_backlog_item": "write",
    "sw_claim": "write",
    "sw_submit": "write",
    "sw_review": "write",
    "sw_transition": "write",
    "sw_prioritize": "write",
    "sw_refine": "write",
    "sw_log": "write",
    "social_post": "write",
    "social_vote": "write",
    "wiki_assess_quality": "write: a QA assessment",
    "wiki_protect": "write",
    "wiki_submit_review": "write",
}

#: Experimental until 0.28 (#657): out of this theme's scope.
OUT_OF_SCOPE_PREFIXES = ("investigation_",)


def _all_tools() -> set[str]:
    from pyrite.config import PyriteConfig, Settings
    from pyrite.server.mcp_server import PyriteMCPServer

    names: set[str] = set()
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "k").mkdir()
        config = PyriteConfig(
            knowledge_bases=[KBConfig(name="k", path=root / "k", kb_type="generic")],
            settings=Settings(index_path=root / "i.db"),
        )
        for tier in ("read", "write", "admin"):
            server = PyriteMCPServer(config=config, tier=tier)
            try:
                names |= set(server.tools)
            finally:
                server.close()
    return names


@pytest.mark.control(
    reason="a ratchet over the tool table: it holds before the fix too, and fails when a tool is added without a cell"
)
def test_every_mcp_tool_is_classified():
    """A new tool cannot be added without a cell: it is a row reader
    (compared with the file below), an own-dict tool (walked), a tool with no
    entry rows (with a reason), or out of scope."""
    tools = {t for t in _all_tools() if not t.startswith(OUT_OF_SCOPE_PREFIXES)}
    classified = set(MCP_ROW_READERS) | set(MCP_OWN_DICTS) | set(MCP_NO_ENTRY_ROWS)
    assert tools - classified == set(), "unclassified MCP tools"
    assert classified - tools == set(), "classified tools that no longer exist"
    overlap = (
        (set(MCP_ROW_READERS) & set(MCP_OWN_DICTS))
        | (set(MCP_ROW_READERS) & set(MCP_NO_ENTRY_ROWS))
        | (set(MCP_OWN_DICTS) & set(MCP_NO_ENTRY_ROWS))
    )
    assert overlap == set()


def _skips_for(tool: str) -> set:
    skip: set = set()
    if tool == "kb_search":
        # The keyword row's `rank` is bm25 (a projection the search owns);
        # a declared `rank` is never lifted over a key the row already has.
        skip.add(("b-one", "rank"))
    return skip


@pytest.mark.parametrize("tool", sorted(MCP_ROW_READERS))
def test_an_mcp_row_reader_returns_each_field_with_its_files_type(world, tool):
    seen = 0
    for args, rows_of in MCP_ROW_READERS[tool]:
        rows = rows_of(_mcp(world, tool, args))
        seen += _rows_check(rows, reader=tool, skip=_skips_for(tool))
    assert seen, f"{tool} returned none of the seeded entries"


def _task_priorities(world, tool: str) -> list[Any]:
    return [
        row["priority"]
        for args, rows_of in MCP_ROW_READERS[tool]
        for row in rows_of(_mcp(world, tool, args))
        if row.get("id") == "register-domain" and "priority" in row
    ]


#: The row readers whose rows select the `priority` column (the keyword
#: search row does not: its projection is the search's own).
_PRIORITY_READERS = sorted(t for t in MCP_ROW_READERS if t != "kb_search")


@pytest.mark.parametrize("tool", _PRIORITY_READERS)
def test_a_task_priority_reads_as_the_integer_in_the_file_on_every_mcp_row_reader(world, tool):
    """The issue's case: `priority: 7` in a task file reads `7` on every
    index reader, as `task_list` reads it. `task` declares `priority` a
    number (`TASK_TYPE_METADATA["fields"]`, decision (a) of 2026-11-17)."""
    values = _task_priorities(world, tool)
    assert values, f"{tool} returned no task priority to compare"
    assert all(v == 7 and type(v) is int for v in values), (tool, values)


def test_a_task_declares_its_priority_a_number_where_the_schema_reads_declarations():
    from pyrite.schema import KBSchema

    assert KBSchema().type_fields("task")["priority"].field_type == "number"


@pytest.mark.parametrize("surface", ["mcp", "cli", "rest"])
def test_a_task_priority_read_and_written_back_leaves_the_file_byte_identical(rt_world, surface):
    """The issue's round trip on a task: `kb_get` used to read `"7"`, and an
    update with it rewrote `priority: 7` as `priority: "7"`."""
    w, emp = rt_world
    path = emp / "tasks/register-domain.md"
    before = path.read_bytes()
    value = _read_priority(w, surface, "register-domain")
    assert value == 7 and type(value) is int, value
    _write_priority(w, surface, "register-domain", value)
    assert path.read_bytes() == before


@pytest.mark.control(
    reason="a create never wrote more than the type's own defaults; pinned so declaring "
    "task.priority (no default) never adds a key to a created task's file"
)
def test_declaring_a_task_priority_adds_nothing_to_a_created_tasks_file(tmp_path):
    emp = tmp_path / KB
    emp.mkdir()
    w = build_world(
        tmp_path / "world", kbs=[KBConfig(name=KB, path=emp, kb_type="generic")], rest=False
    )
    try:
        _mcp(w, "kb_create", {"kb_name": KB, "entry_type": "task", "title": "Plain task"})
    finally:
        w.close()
    text = (emp / "tasks/plain-task.md").read_text()
    front = text.split("---")[1]
    keys = [
        line.split(":")[0]
        for line in front.strip().splitlines()
        if not line.startswith(" ") and not line.startswith(("created_at", "updated_at"))
    ]
    assert keys == ["id", "title", "type", "importance", "status", "priority"], text
    assert "\npriority: 5\n" in text


def _walk_shapes(value: Any, where: str) -> None:
    if isinstance(value, dict):
        if "metadata" in value and value["metadata"] is not None:
            assert isinstance(value["metadata"], dict), (where, value["metadata"])
        if "aliases" in value and value["aliases"] is not None:
            assert isinstance(value["aliases"], list), (where, value["aliases"])
        for k, v in value.items():
            _walk_shapes(v, f"{where}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            _walk_shapes(v, f"{where}[{i}]")


@pytest.mark.parametrize("tool", sorted(t for t, a in MCP_OWN_DICTS.items() if a is not None))
@pytest.mark.control(
    reason="own-dict tools build their own dicts and were never string-shaped; pinned so one that starts returning an index row must return it shaped"
)
def test_an_own_dict_tool_returns_no_metadata_or_aliases_as_text(world, tool):
    _walk_shapes(_mcp(world, tool, MCP_OWN_DICTS[tool]), tool)


# ---------------------------------------------------------------------------
# CLI --format json and REST
# ---------------------------------------------------------------------------


def _cli_json(world, *args: str) -> Any:
    result = world.cli(*args, "--format", "json")
    assert result.exit_code == 0, (args, result.output)
    return json.loads(result.output)


CLI_ROW_READERS: dict[str, Callable[[Any], list[dict[str, Any]]]] = {
    "get": lambda w: (
        [_cli_json(w, "get", "c-sweep", "-k", KB)] + [_cli_json(w, "get", "b-one", "-k", SW)]
    ),
    "list-entries": lambda w: (
        _cli_json(w, "list-entries", "-k", KB)["entries"]
        + _cli_json(w, "list-entries", "-k", SW)["entries"]
    ),
    "recent": lambda w: _cli_json(w, "recent", "-k", KB)["entries"],
    "search": lambda w: (
        _cli_json(w, "search", "zebra", "-k", KB, "--fields", "*")["results"]
        + _cli_json(w, "search", "zebra", "-k", SW, "--fields", "*")["results"]
    ),
    "batch-read": lambda w: _cli_json(w, "batch-read", f"{KB}:c-sweep", f"{SW}:b-one")["entries"],
}


@pytest.mark.parametrize("command", sorted(CLI_ROW_READERS))
def test_a_cli_json_row_reader_returns_each_field_with_its_files_type(world, command):
    rows = CLI_ROW_READERS[command](world)
    skip = _skips_for("kb_search" if command == "search" else command)
    assert _rows_check(rows, reader=f"cli {command}", skip=skip)


def _rest(world, method: str, url: str, **kw) -> Any:
    resp = getattr(world.client, method)(url, **kw)
    assert resp.status_code == 200, (url, resp.status_code, resp.text)
    return resp.json()


REST_ROW_READERS: dict[str, Callable[[Any], list[dict[str, Any]]]] = {
    "GET /api/entries": lambda w: (
        _rest(w, "get", "/api/entries", params={"kb": KB})["entries"]
        + _rest(w, "get", "/api/entries", params={"kb": SW})["entries"]
    ),
    "GET /api/entries/{id}": lambda w: [
        _rest(w, "get", "/api/entries/c-sweep", params={"kb": KB}),
        _rest(w, "get", "/api/entries/b-one", params={"kb": SW}),
    ],
    "POST /api/entries/batch": lambda w: _rest(
        w,
        "post",
        "/api/entries/batch",
        json={
            "entries": [
                {"entry_id": "c-sweep", "kb_name": KB},
                {"entry_id": "b-one", "kb_name": SW},
            ]
        },
    )["entries"],
    "GET /api/search?fields=metadata,importance": lambda w: _rest(
        w, "get", "/api/search", params={"q": "zebra", "kb": KB, "fields": "metadata,importance"}
    )["results"],
}


@pytest.mark.parametrize("route", sorted(REST_ROW_READERS))
def test_a_rest_row_reader_returns_each_field_with_its_files_type(world, route):
    rows = REST_ROW_READERS[route](world)
    skip = _skips_for("kb_search" if "search" in route else route)
    assert _rows_check(rows, reader=route, skip=skip)


# ---------------------------------------------------------------------------
# The storage backends, SQLite and Postgres
# ---------------------------------------------------------------------------

#: Every `SearchBackend` method that returns entry rows, called so it returns
#: the seeded entries.
BACKEND_ROW_READERS: dict[str, Callable[[Any], list[dict[str, Any]]]] = {
    "get_entry": lambda be: [be.get_entry("c-sweep", KB), be.get_entry("n-plain", KB)],
    "get_entries": lambda be: be.get_entries([("c-sweep", KB), ("n-plain", KB)]),
    "list_entries": lambda be: be.list_entries(kb_name=KB),
    "search": lambda be: be.search("zebra", kb_name=KB),
    "search_by_tag": lambda be: be.search_by_tag("errand", kb_name=KB),
    "search_by_date_range": lambda be: be.search_by_date_range(
        "2019-01-01", "2021-01-01", kb_name=KB
    ),
    "search_by_tag_prefix": lambda be: be.search_by_tag_prefix("errand", kb_name=KB),
    "list_entries_in_folder": lambda be: (
        be.list_entries_in_folder(KB, "chores") + be.list_entries_in_folder(KB, "notes")
    ),
    "search_semantic": lambda be: be.search_semantic([0.05] * 384, kb_name=KB, limit=10),
}

#: `SearchBackend` methods that return no entry rows, each with why.
BACKEND_NOT_ROWS = {
    "close": "lifecycle",
    "upsert_entry": "write",
    "delete_entry": "write",
    "count_entries": "a count",
    "get_distinct_types": "type names",
    "get_entries_for_indexing": "the indexer's id/path/hash projection, no metadata",
    "upsert_embedding": "write",
    "has_embeddings": "a bool",
    "embedding_stats": "counts",
    "get_embedded_rowids": "row ids",
    "get_entries_for_embedding": "the embedder's id/title/body projection, no metadata",
    "delete_embedding": "write",
    "get_edge_endpoints": "edge rows",
    "get_edges_by_endpoint": "edge rows",
    "get_edges_between": "edge rows",
    "get_backlinks": "link rows (id, title, relation), no metadata",
    "get_outlinks": "link rows, no metadata",
    "get_kbs_holding": "KB names",
    "get_all_backlinks_for_kb": "link rows",
    "get_all_outlinks_for_kb": "link rows",
    "get_all_sources_for_kb": "source rows",
    "get_graph_data": "graph nodes and edges",
    "get_most_linked": "id/title/count rows",
    "get_orphans": "id/title rows",
    "get_all_tags": "tag counts",
    "get_tags_as_dicts": "tag counts",
    "get_timeline": "a fixed column subset with no metadata, aliases or entry_type",
    "get_refs_from": "ref rows",
    "get_refs_to": "ref rows",
    "count_entries_in_folder": "a count",
    "get_global_counts": "counts",
}


def _protocol_methods() -> set[str]:
    from pyrite.storage.backends.protocol import SearchBackend

    return {n for n, v in vars(SearchBackend).items() if callable(v) and not n.startswith("_")}


@pytest.mark.control(
    reason="a ratchet over SearchBackend's methods: it holds before the fix too, and fails when a method is added without a cell"
)
def test_every_backend_method_is_classified():
    methods = _protocol_methods()
    classified = set(BACKEND_ROW_READERS) | set(BACKEND_NOT_ROWS)
    assert methods - classified == set(), "unclassified SearchBackend methods"
    assert classified - methods == set(), "classified methods that no longer exist"


def _seed(be) -> None:
    common = {
        "kb_name": KB,
        "summary": "",
        "status": "open",
        "importance": 4,
        "assignee": "alice",
        "due_date": "2020-01-01",
        "location": "Paris",
        "date": "2020-06-01",
        "tags": ["errand"],
        "sources": [],
        "links": [],
    }
    be.upsert_entry(
        {
            **common,
            "id": "c-sweep",
            "entry_type": "chore",
            "title": "Sweep the zebra yard",
            "body": "zebra stripes",
            "file_path": "chores/c-sweep.md",
            # What the indexer stores: the TEXT column's text, the bag's JSON.
            "priority": "7",
            "metadata": {"priority": 7, "room": {"floor": 2}},
            "aliases": [],
        }
    )
    be.upsert_entry(
        {
            **common,
            "id": "n-plain",
            "entry_type": "note",
            "title": "Plain zebra note",
            "body": "zebra plain",
            "file_path": "notes/n-plain.md",
            "metadata": {},
        }
    )
    from sqlalchemy import text

    session = be._session
    session.execute(text("UPDATE entry SET metadata = NULL WHERE id = 'n-plain'"))
    session.commit()
    if getattr(be, "vec_available", False):
        for eid in ("c-sweep", "n-plain"):
            be.upsert_embedding(eid, KB, [0.05] * 384)


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=pytest.mark.pg)])
def seeded_backend(request, tmp_path):
    kb_path = tmp_path / KB
    kb_path.mkdir()
    (kb_path / "kb.yaml").write_text(KB_YAML)
    if request.param == "sqlite":
        from pyrite.storage.database import PyriteDB

        db = PyriteDB(tmp_path / "index.db")
        db.register_kb(KB, "generic", str(kb_path), "")
        try:
            _seed(db.backend)
            yield db.backend
        finally:
            db.close()
    else:
        from tests.backends.conftest import _require_pg, _setup_pg_backend, _teardown_postgres

        _require_pg()
        be, engine, session = _setup_pg_backend((KB, "generic", str(kb_path), ""))
        try:
            _seed(be)
            yield be
        finally:
            _teardown_postgres(engine, session)


@pytest.mark.parametrize("method", sorted(BACKEND_ROW_READERS))
def test_a_backend_row_reader_returns_each_field_with_its_files_type(seeded_backend, method):
    if method == "search_semantic" and not getattr(seeded_backend, "vec_available", False):
        pytest.skip("vector search not available on this backend")
    rows = [r for r in BACKEND_ROW_READERS[method](seeded_backend) if r]
    assert _rows_check(rows, reader=method), f"{method} returned none of the seeded entries"


def test_the_find_readers_return_each_field_with_its_files_type(world):
    """`PyriteDB`'s protocol-aware finds (SQLite: `queries.py`)."""
    db = world.db
    readers = {
        "find_by_status": db.find_by_status("open", kb_name=KB),
        "find_by_assignee": db.find_by_assignee("alice", kb_name=KB),
        "find_overdue": db.find_overdue(as_of="2030-01-01", kb_name=KB),
        "find_by_location": db.find_by_location("Paris", kb_name=KB),
    }
    for name, rows in readers.items():
        # A note's `status` is its bag's, not the column's: the finds return
        # the two entries whose type has the column.
        assert {"c-sweep", "register-domain"} <= {r["id"] for r in rows}, name
        _rows_check(rows, reader=name)


# ---------------------------------------------------------------------------
# What a caller reads it can write back without changing the file
# ---------------------------------------------------------------------------


@pytest.fixture
def rt_world(tmp_path):
    emp = tmp_path / KB
    _write(emp, FILES)
    (emp / "kb.yaml").write_text(KB_YAML)
    w = build_world(tmp_path / "world", kbs=[KBConfig(name=KB, path=emp, kb_type="generic")])
    try:
        yield w, emp
    finally:
        w.close()


def _read_priority(w, surface: str, entry_id: str) -> Any:
    if surface == "mcp":
        return _mcp(w, "kb_get", {"kb_name": KB, "entry_id": entry_id})["entry"]["priority"]
    if surface == "cli":
        return _cli_json(w, "get", entry_id, "-k", KB)["priority"]
    return _rest(w, "get", f"/api/entries/{entry_id}", params={"kb": KB})["priority"]


def _write_priority(w, surface: str, entry_id: str, value: Any) -> None:
    if surface == "mcp":
        _mcp(w, "kb_update", {"kb_name": KB, "entry_id": entry_id, "priority": value})
    elif surface == "cli":
        # The CLI's `-f` takes text; the value read is sent as JSON prints it.
        result = w.cli("update", entry_id, "-k", KB, "-f", f"priority={json.dumps(value)}")
        assert result.exit_code == 0, result.output
    else:
        _rest(
            w,
            "patch",
            f"/api/entries/{entry_id}",
            json={"kb": KB, "field": "priority", "value": value},
        )


SURFACES = ("mcp", "cli", "rest")


@pytest.mark.parametrize("surface", SURFACES)
def test_a_declared_number_read_and_written_back_leaves_the_file_byte_identical(rt_world, surface):
    w, emp = rt_world
    path = emp / "chores/c-sweep.md"
    before = path.read_bytes()
    value = _read_priority(w, surface, "c-sweep")
    assert value == 7 and type(value) is int, value
    _write_priority(w, surface, "c-sweep", value)
    assert path.read_bytes() == before


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.control(
    reason="an update of another field never touched `priority`'s bytes; pinned so the cast on read never leaks into a write"
)
def test_an_update_of_another_field_leaves_a_typed_values_bytes_alone(rt_world, surface):
    w, emp = rt_world
    path = emp / "chores/c-sweep.md"
    before = path.read_text()
    if surface == "mcp":
        _mcp(w, "kb_update", {"kb_name": KB, "entry_id": "c-sweep", "title": "Sweep it"})
    elif surface == "cli":
        assert w.cli("update", "c-sweep", "-k", KB, "--title", "Sweep it").exit_code == 0
    else:
        _rest(
            w,
            "patch",
            "/api/entries/c-sweep",
            json={"kb": KB, "field": "title", "value": "Sweep it"},
        )
    after = path.read_text()
    assert after == before.replace("title: Sweep the zebra yard", "title: Sweep it"), after
    assert "\npriority: 7\n" in after


def test_a_single_read_and_the_list_rows_agree_on_a_declared_number(rt_world):
    w, _emp = rt_world
    read = _mcp(w, "kb_get", {"kb_name": KB, "entry_id": "c-sweep"})["entry"]["priority"]
    listed = {
        r["id"]: r["priority"]
        for r in _mcp(w, "kb_find_by_status", {"kb_name": KB, "status": "open"})["entries"]
    }
    assert read == listed["c-sweep"] == 7
    assert type(read) is type(listed["c-sweep"]) is int


#: A declared number's file spelling -> what a read returns, by hand from
#: the maintainer's rule (#1628, 2026-11-17): only plain ASCII whole numbers
#: and decimals are numbers; every other text stays the file's text.
SPELLINGS = {
    "7": 7,
    "2.5": 2.5,
    '"7"': 7,
    '"007"': "007",
    '"+7"': "+7",
    '" 7 "': " 7 ",
    '"7."': "7.",
    '"1e3"': "1e3",
    '"٧"': "٧",
    '"７"': "７",
    '"7.50"': "7.50",
    '"-7"': "-7",
}


def _spelling_params() -> list[Any]:
    """A spelling that stays text read as text on `dev` too: a control, red
    against a cast looser than the rule (`"007"` read as 7 in a draft of this
    change), never against `dev`."""
    reason = (
        "text the strict rule keeps was text before the change too; pinned so a looser "
        "cast (which read it as a number and let a write-back respell the file) fails"
    )
    return [
        pytest.param(sp, marks=[pytest.mark.control(reason=reason)])
        if isinstance(want, str)
        else sp
        for sp, want in SPELLINGS.items()
    ]


def _chore(spelling: str) -> str:
    return (
        f"---\nid: c-spelt\ntype: chore\ntitle: Spelt\nstatus: open\npriority: {spelling}\n---\nq\n"
    )


@pytest.mark.parametrize("spelling", _spelling_params())
def test_a_declared_numbers_file_spelling_reads_by_the_strict_rule(rt_world, spelling):
    """A read never rewrites the file, whatever it returns."""
    w, emp = rt_world
    path = emp / "chores/c-spelt.md"
    path.write_text(_chore(spelling))
    w.index(KB)
    want = SPELLINGS[spelling]
    for got in (
        _mcp(w, "kb_get", {"kb_name": KB, "entry_id": "c-spelt"})["entry"]["priority"],
        {r["id"]: r["priority"] for r in _mcp(w, "kb_list_entries", {"kb_name": KB})["entries"]}[
            "c-spelt"
        ],
    ):
        # The single read returns the file's own string (a `str` subclass).
        same_type = isinstance(got, str) if isinstance(want, str) else type(got) is type(want)
        assert got == want and same_type, (spelling, got)
    assert path.read_text() == _chore(spelling)


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("spelling", _spelling_params())
def test_a_declared_number_read_and_written_back_leaves_what_the_rule_says(
    rt_world, surface, spelling
):
    """What a caller reads, sent back as one field, leaves the file as it was,
    with one stated exception (the decision on #1628): a quoted `"7"` reads
    as 7, and the write of that 7 stores it unquoted."""
    w, emp = rt_world
    path = emp / "chores/c-spelt.md"
    path.write_text(_chore(spelling))
    w.index(KB)
    value = _read_priority(w, surface, "c-spelt")
    _write_priority(w, surface, "c-spelt", value)
    want = _chore("7") if spelling == '"7"' else _chore(spelling)
    assert path.read_text() == want, (spelling, value)


@pytest.mark.control(
    reason="text that is no number was text before the fix; pinned so the cast never loses a value"
)
def test_a_value_a_declared_number_cannot_read_stays_text(rt_world):
    """No lossy cast: text that is no number stays the file's text."""
    w, emp = rt_world
    path = emp / "chores/c-word.md"
    path.write_text(
        "---\nid: c-word\ntype: chore\ntitle: Word\nstatus: open\npriority: high\n---\nw\n"
    )
    w.index(KB)
    rows = _mcp(w, "kb_list_entries", {"kb_name": KB})["entries"]
    assert {r["id"]: r.get("priority") for r in rows}["c-word"] == "high"
    assert _mcp(w, "kb_get", {"kb_name": KB, "entry_id": "c-word"})["entry"]["priority"] == "high"


# ---------------------------------------------------------------------------
# The rule is structural: no storage reader builds entry rows past the shaper
# ---------------------------------------------------------------------------

#: SQL that selects whole entry rows (or their `metadata` column).
_ENTRY_ROW_SQL = re.compile(
    r"SELECT\s+(DISTINCT\s+)?(e\.\*|\*)[\s\S]*?FROM\s+entry\b|\be\.metadata\b",
    re.IGNORECASE,
)

#: Functions that select entry rows and never return them to a caller.
STRUCTURAL_ALLOWLIST = {
    ("pyrite/storage/index.py", "_check_off_list_values"): (
        "index health's enum scan reads the stored column itself; returns findings, not rows"
    ),
}


def _functions_with_entry_sql() -> Iterator[tuple[str, str, str]]:
    for path in sorted((REPO / "pyrite" / "storage").rglob("*.py")):
        rel = path.relative_to(REPO).as_posix()
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            strings = [
                c.value
                for c in ast.walk(node)
                if isinstance(c, ast.Constant) and isinstance(c.value, str)
            ]
            if any(_ENTRY_ROW_SQL.search(s) for s in strings):
                yield rel, node.name, ast.get_source_segment(path.read_text(), node) or ""


def test_every_storage_reader_of_entry_rows_returns_through_the_shaper():
    offenders = []
    found = set()
    for rel, name, source in _functions_with_entry_sql():
        found.add((rel, name))
        if (rel, name) in STRUCTURAL_ALLOWLIST:
            continue
        if "shape_entry_row" not in source:
            offenders.append(f"{rel}::{name}")
    assert offenders == [], "entry rows built without shape_entry_row(s)"
    assert set(STRUCTURAL_ALLOWLIST) <= found, "stale allowlist entries"


@pytest.mark.control(
    reason="raw rows never had lifted keys; pinned because lifting them in the shaper broke 66 search and reader cells during this change"
)
def test_a_raw_readers_row_gains_no_key_its_sql_did_not_select(world):
    """The shaper reads the keys a row has and adds none (#1503): a backlog
    item's `kind` lives in the bag (it has no column), so a keyword search
    hit and a find row, which select columns, carry no `kind` key; the ORM
    row, whose projection has always lifted declared fields, does."""
    db = world.db
    search = [r for r in db.backend.search("zebra", kb_name=SW) if r["id"] == "b-one"]
    found = [r for r in db.find_by_status("open", kb_name=SW) if r["id"] == "b-one"]
    assert search and found
    assert "kind" not in search[0], sorted(search[0])
    assert "kind" not in found[0], sorted(found[0])
    assert db.backend.get_entry("b-one", SW)["kind"] == "bug"
