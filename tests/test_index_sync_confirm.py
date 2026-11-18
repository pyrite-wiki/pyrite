"""`index sync --confirm`: sync, then say whether each named entry is indexed
and searchable (#1691, Option A of its groom).

The user's model: "I wrote these entries. Tell me they are in the index and
findable, or tell me why not." After ``pyrite index sync -k KB --confirm NAME
...`` each named id or path is reported as exactly one of ``indexed`` (the
row's hash equals the file's, and a keyword lookup returns it), ``not_found``,
``malformed``, ``duplicate``, ``skipped`` -- or ``not_indexed`` when a file
holds the id but the row does not match it or the keyword lookup misses it --
with ``findings`` (schema findings from the validator ``qa validate`` runs)
and ``embedding`` (``queued``, ``embedded`` or ``unavailable``, never waited
on: ADR-0035). The exit says whether every named id was confirmed: ``done``,
exit 0, when all are; ``partial``, exit 3, when any is not, and a schema
finding of severity error on a NAMED id counts as not confirmed; a refusal
when nothing synced. ``--confirm`` alone confirms every file this sync added
or changed, with findings reported only.

The maintainer's decision on #1691 (2026-11-17): plain ``index sync`` is on the
outcome contract and honours ``PYRITE_FORMAT`` / ``--format`` like every other
command; without ``--confirm`` its counts and its text are unchanged.

Every expected value below is written by hand from that property. Medium
tests: real files in a KB on disk, the real config loader, the CLI in-process
(``World.cli``), ``PyriteMCPServer._dispatch_tool``, and the app as ``pyrite
serve`` builds it (``tests/_world.py``).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from pyrite.config import KBConfig
from tests._world import build_world
from tests.cli_help import plain

KB = "notes"

#: A note type with one required field: a note without ``severity`` breaks
#: the schema, and the sync indexes it anyway (#1664).
KB_YAML = (
    "name: notes\n"
    "types:\n"
    "  note:\n"
    "    fields:\n"
    "      severity:\n"
    "        type: select\n"
    "        options: [low, medium, high]\n"
    "        required: true\n"
)


def note(entry_id: str | None, title: str, *, severity: str | None = "low", body: str = "") -> str:
    lines = ["---"]
    if entry_id is not None:
        lines.append(f"id: {entry_id}")
    lines += [f"title: {title}", "type: note"]
    if severity is not None:
        lines.append(f"severity: {severity}")
    lines += ["---", body or f"The body of {title}.", ""]
    return "\n".join(lines)


@pytest.fixture
def no_embedder(monkeypatch):
    """No embedding provider: sentence-transformers reads as not installed."""
    import pyrite.services.embedding_service as es

    monkeypatch.setattr(es, "is_available", lambda: False)


@pytest.fixture
def world(tmp_path, no_embedder):
    """One KB with a schema and three entries indexed by the first build:
    ``unchanged``, ``changed`` (edited below, after the build) and
    ``old-dup`` (unused)."""
    kb = tmp_path / KB
    kb.mkdir()
    (kb / "kb.yaml").write_text(KB_YAML, encoding="utf-8")
    (kb / "unchanged.md").write_text(note("unchanged", "Unchanged Alpha"), encoding="utf-8")
    (kb / "changed.md").write_text(note("changed", "Changed Beta"), encoding="utf-8")
    w = build_world(tmp_path / "w", kbs=[KBConfig(name=KB, path=kb, kb_type="generic")], rest=False)
    try:
        yield w
    finally:
        w.close()


def _write(world, rel: str, text: str) -> Path:
    path = world.kb_path(KB) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _json(result) -> dict:
    return json.loads(result.stdout)


def _sync(world, *args: str):
    return world.cli("index", "sync", "-k", KB, "--no-embed", "--format", "json", *args)


def _rows(payload: dict) -> dict[str, dict]:
    return {row["name"]: row for row in payload["confirm"]["entries"]}


# --------------------------------------------------------------------------
# The eight id shapes, parametrised
# --------------------------------------------------------------------------


def _new(world):
    _write(world, "fresh.md", note("fresh", "Fresh Gamma"))
    return "fresh"


def _changed(world):
    _write(world, "changed.md", note("changed", "Changed Beta", body="Edited after the build."))
    return "changed"


def _unchanged(world):
    return "unchanged"


def _malformed(world):
    _write(world, "broken.md", "---\nid: broken\ntitle: [unclosed\n---\nBody\n")
    return "broken"


def _duplicate(world):
    _write(world, "dup-a.md", note("dup", "Dup One"))
    _write(world, "dup-b.md", note("dup", "Dup Two"))
    return "dup"


def _absent(world):
    return "nowhere"


def _dot_folder(world):
    _write(world, ".hidden/secret.md", note("secret", "Secret Delta"))
    return "secret"


def _non_regular_by_path(world):
    (world.kb_path(KB) / "folder.md").mkdir()
    return "folder.md"


def _id_less_by_path(world):
    _write(world, "sub/no-id-here.md", note(None, "No Id Epsilon"))
    return "sub/no-id-here.md"


#: shape -> (seed, status, confirmed). By hand, from the property.
SHAPES = {
    "new file": (_new, "indexed", True),
    "changed file": (_changed, "indexed", True),
    "unchanged since the last sync": (_unchanged, "indexed", True),
    "malformed frontmatter": (_malformed, "malformed", False),
    "claimed by two files": (_duplicate, "duplicate", False),
    "absent": (_absent, "not_found", False),
    "in a dot-folder the walk skips": (_dot_folder, "skipped", False),
    "a non-regular path the walk skips": (_non_regular_by_path, "skipped", False),
    "an id-less file, by path": (_id_less_by_path, "indexed", True),
}


@pytest.mark.parametrize("shape", list(SHAPES))
def test_each_id_shape_gets_its_one_status_and_the_exit_says_so(world, shape):
    seed, status, confirmed = SHAPES[shape]
    name = seed(world)

    result = _sync(world, "--confirm", name)

    payload = _json(result)
    row = _rows(payload)[name]
    assert row["status"] == status, row
    assert row["confirmed"] is confirmed, row
    assert payload["outcome"] == ("done" if confirmed else "partial"), payload
    assert result.exit_code == (0 if confirmed else 3), result.output


def test_an_indexed_row_names_its_id_and_kb_relative_path(world):
    _write(world, "sub/no-id-here.md", note(None, "No Id Epsilon"))
    _write(world, "fresh.md", note("fresh", "Fresh Gamma"))

    rows = _rows(_json(_sync(world, "--confirm", "sub/no-id-here.md", "fresh")))

    assert rows["fresh"]["id"] == "fresh"
    assert rows["fresh"]["path"] == "fresh.md"
    assert rows["fresh"]["searchable"] is True
    # The reader derives an id-less file's id from its title.
    assert rows["sub/no-id-here.md"]["id"] == "no-id-epsilon"
    assert rows["sub/no-id-here.md"]["path"] == "sub/no-id-here.md"


def test_a_duplicate_row_names_every_file_that_claims_the_id(world):
    _duplicate(world)

    row = _rows(_json(_sync(world, "--confirm", "dup")))["dup"]

    assert row["paths"] == ["dup-a.md", "dup-b.md"]


def test_a_path_naming_the_losing_copy_of_a_duplicate_is_a_duplicate(world):
    _duplicate(world)

    row = _rows(_json(_sync(world, "--confirm", "dup-b.md")))["dup-b.md"]

    assert row["status"] == "duplicate"
    assert row["id"] == "dup"


def test_a_path_that_is_not_there_is_not_found(world):
    row = _rows(_json(_sync(world, "--confirm", "ghost.md")))["ghost.md"]
    assert row["status"] == "not_found"


def test_a_malformed_file_named_by_path_is_malformed(world):
    _malformed(world)
    row = _rows(_json(_sync(world, "--confirm", "broken.md")))["broken.md"]
    assert row["status"] == "malformed"


# --------------------------------------------------------------------------
# The groom's acceptance
# --------------------------------------------------------------------------


def test_acceptance_one_valid_one_breaking_the_schema_and_one_missing(world):
    """Two entry files written by hand, one valid, one breaking the schema:
    a is indexed and found by a keyword search, b is indexed with its schema
    finding, the exit is non-zero, a missing id is not_found."""
    _write(world, "a.md", note("a", "Valid Zeta Entry"))
    _write(world, "b.md", note("b", "Broken Eta Entry", severity=None))

    result = _sync(world, "--confirm", "a", "b", "missing")

    assert result.exit_code == 3, result.output
    payload = _json(result)
    assert payload["outcome"] == "partial"
    assert (payload["added"], payload["updated"], payload["removed"]) == (2, 0, 0)
    rows = _rows(payload)
    assert (rows["a"]["status"], rows["a"]["confirmed"], rows["a"]["findings"]) == (
        "indexed",
        True,
        [],
    )
    assert rows["b"]["status"] == "indexed"
    assert rows["b"]["confirmed"] is False
    assert [(f["rule"], f["severity"], f["field"]) for f in rows["b"]["findings"]] == [
        ("schema_violation", "error", "severity")
    ]
    assert rows["missing"]["status"] == "not_found"
    assert payload["confirm"]["confirmed"] == 1
    assert payload["confirm"]["not_confirmed"] == 2

    found = world.cli("search", "Zeta", "-k", KB, "--mode", "keyword", "--format", "json")
    assert "a" in [r["id"] for r in _json(found)["results"]], found.output


@pytest.mark.control(
    reason="plain index sync keeps its counts and its text: green before and after"
)
def test_without_confirm_the_counts_and_the_text_are_unchanged(world):
    _write(world, "a.md", note("a", "Valid Zeta Entry"))
    _write(world, "b.md", note("b", "Broken Eta Entry", severity=None))

    result = world.cli("index", "sync", "-k", KB, "--no-embed")

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        "Sync complete:",
        "  Added: 2",
        "  Updated: 0",
        "  Removed: 0",
    ]


def test_plain_sync_honours_pyrite_format_json(world, monkeypatch):
    _write(world, "a.md", note("a", "Valid Zeta Entry"))
    monkeypatch.setenv("PYRITE_FORMAT", "json")

    result = world.cli("index", "sync", "-k", KB, "--no-embed")

    assert result.exit_code == 0, result.output
    payload = _json(result)
    assert (payload["added"], payload["updated"], payload["removed"]) == (1, 0, 0)
    assert payload["outcome"] == "done"
    assert "confirm" not in payload


def test_format_json_overrides_pyrite_format_rich(world, monkeypatch):
    monkeypatch.setenv("PYRITE_FORMAT", "rich")
    result = world.cli("index", "sync", "-k", KB, "--no-embed", "--format", "json")
    assert _json(result)["outcome"] == "done"


def test_the_sync_result_lists_the_ids_and_paths_it_wrote(world):
    _write(world, "fresh.md", note("fresh", "Fresh Gamma"))
    _changed(world)
    (world.kb_path(KB) / "unchanged.md").unlink()

    payload = _json(_sync(world))

    assert sorted(payload["changed"], key=lambda r: r["id"]) == [
        {"kb": KB, "id": "changed", "path": "changed.md", "change": "updated"},
        {"kb": KB, "id": "fresh", "path": "fresh.md", "change": "added"},
        {"kb": KB, "id": "unchanged", "path": "unchanged.md", "change": "removed"},
    ]


# --------------------------------------------------------------------------
# --confirm with no ids: every file this sync added or changed
# --------------------------------------------------------------------------


def test_confirm_with_no_ids_confirms_what_this_sync_changed(world):
    _write(world, "fresh.md", note("fresh", "Fresh Gamma"))
    _changed(world)

    result = _sync(world, "--confirm")

    assert result.exit_code == 0, result.output
    rows = _rows(_json(result))
    assert sorted(rows) == ["changed", "fresh"]
    assert {r["status"] for r in rows.values()} == {"indexed"}


def test_confirm_with_no_ids_reports_schema_findings_without_failing(world):
    _write(world, "b.md", note("b", "Broken Eta Entry", severity=None))

    result = _sync(world, "--confirm")

    assert result.exit_code == 0, result.output
    row = _rows(_json(result))["b"]
    assert row["confirmed"] is True
    assert [f["rule"] for f in row["findings"]] == ["schema_violation"]


def test_confirm_with_no_ids_and_nothing_changed_is_done_and_empty(world):
    result = _sync(world, "--confirm")
    assert result.exit_code == 0, result.output
    assert _json(result)["confirm"]["entries"] == []


# --------------------------------------------------------------------------
# What "indexed" requires: the hash and the keyword lookup, each checked
# --------------------------------------------------------------------------


def test_a_row_whose_hash_differs_from_its_file_is_not_indexed(world):
    """The stat matches, so the sync does not re-read the file; the confirm
    still compares the row's hash with the file's."""
    con = world.db._raw_conn
    con.execute("UPDATE entry SET content_hash = 'not-the-files-hash' WHERE id = 'unchanged'")
    con.commit()

    row = _rows(_json(_sync(world, "--confirm", "unchanged")))["unchanged"]

    assert row["status"] == "not_indexed", row
    assert row["confirmed"] is False


def test_a_row_the_keyword_index_does_not_hold_is_not_searchable(world):
    """The groom assumed the keyword row is written with the entry row; the
    confirm does not rely on it, it runs the lookup."""
    db = world.db
    rows = db.execute_sql(
        "SELECT rowid, id, kb_name, entry_type, title, body, summary, location FROM entry "
        "WHERE id = 'unchanged'",
        {},
    )
    r = rows[0]
    db._raw_conn.execute(
        "INSERT INTO entry_fts(entry_fts, rowid, id, kb_name, entry_type, title, body, summary, "
        "location) VALUES('delete', :rowid, :id, :kb_name, :entry_type, :title, "
        "COALESCE(:body, ''), COALESCE(:summary, ''), COALESCE(:location, ''))",
        dict(r),
    )
    db._raw_conn.commit()

    row = _rows(_json(_sync(world, "--confirm", "unchanged")))["unchanged"]

    assert row["searchable"] is False, row
    assert row["status"] == "not_indexed"
    assert row["confirmed"] is False


# --------------------------------------------------------------------------
# The three embedding states, parametrised
# --------------------------------------------------------------------------


def _with_a_model(world, monkeypatch):
    """An embedding provider whose model is a stand-in (the process edge):
    the real EmbeddingService and embed queue run."""
    monkeypatch.undo()  # the world's no_embedder
    from tests.search_verdict_seed import install_fake_model

    install_fake_model(monkeypatch)
    if not world.db.vec_available:
        pytest.skip("sqlite-vec is not loaded here: no entry can be embedded")


def _embedded(world, monkeypatch):
    _with_a_model(world, monkeypatch)
    return ()  # the sync embeds


def _queued(world, monkeypatch):
    _with_a_model(world, monkeypatch)
    return ("--no-embed",)  # owed, not embedded


def _not_installed(world, monkeypatch):
    return ()  # the world's no_embedder


EMBEDDING = {
    "available": (_embedded, "embedded"),
    "queued": (_queued, "queued"),
    "not installed": (_not_installed, "unavailable"),
}


@pytest.mark.parametrize("state", list(EMBEDDING))
def test_each_embedding_state_is_reported_and_never_blocks_confirmation(world, monkeypatch, state):
    setup, expected = EMBEDDING[state]
    extra = setup(world, monkeypatch)
    _write(world, "fresh.md", note("fresh", "Fresh Gamma"))

    result = world.cli("index", "sync", "-k", KB, "--format", "json", "--confirm", "fresh", *extra)

    assert result.exit_code == 0, result.output
    row = _rows(_json(result))["fresh"]
    assert row["embedding"] == expected, row
    assert row["confirmed"] is True


def test_a_queued_row_is_queued_even_when_an_old_vector_exists(world, monkeypatch):
    """A queue row means the entry changed after it was embedded (ADR-0035)."""
    _embedded(world, monkeypatch)
    _write(world, "fresh.md", note("fresh", "Fresh Gamma"))
    assert world.cli("index", "sync", "-k", KB, "--format", "json").exit_code == 0
    from pyrite.services.embedding_worker import EmbeddingWorker

    EmbeddingWorker(world.db).enqueue("fresh", KB)

    row = _rows(_json(_sync(world, "--confirm", "fresh")))["fresh"]

    assert row["embedding"] == "queued"


# --------------------------------------------------------------------------
# Regimes: refusals and usage
# --------------------------------------------------------------------------


def test_confirm_with_background_is_refused(world):
    result = world.cli("index", "sync", "-k", KB, "--background", "--confirm", "unchanged")
    assert result.exit_code == 2, result.output
    assert "--confirm cannot be combined with --background" in plain(result.output)


def test_names_without_confirm_are_refused(world):
    result = world.cli("index", "sync", "-k", KB, "unchanged")
    assert result.exit_code == 2, result.output
    assert "--confirm" in plain(result.output)


def test_confirm_without_a_kb_is_refused(world):
    result = world.cli("index", "sync", "--no-embed", "--format", "json", "--confirm", "x")
    assert result.exit_code == 2, result.output
    assert "--confirm needs the KB" in plain(result.output)


def test_a_kb_whose_kb_yaml_does_not_load_refuses_and_confirms_nothing(world):
    (world.kb_path(KB) / "kb.yaml").write_bytes(b"name: notes\ntypes: {note: [unclosed\n")

    result = _sync(world, "--confirm", "unchanged")

    assert result.exit_code == 1, result.output
    payload = _json(result)
    assert payload["outcome"] == "error"
    assert payload["error_code"] == "KB_CONFIG_INVALID"
    assert "confirm" not in payload


def test_a_kb_whose_files_cannot_be_read_refuses_and_confirms_nothing(world):
    """#1542: a sync of one named KB whose files cannot be read is refused."""
    root = world.kb_path(KB)
    root.chmod(0o000)
    try:
        if os.access(root, os.R_OK):
            pytest.skip(f"running as a user (uid {os.getuid()}) whom mode 000 does not stop")
        result = _sync(world, "--confirm", "unchanged")
    finally:
        root.chmod(0o755)

    assert result.exit_code == 1, result.output
    payload = _json(result)
    assert payload["error_code"] == "KB_UNREADABLE"
    assert "confirm" not in payload


def test_no_embedding_provider_still_confirms(world):
    _write(world, "fresh.md", note("fresh", "Fresh Gamma"))
    result = world.cli("index", "sync", "-k", KB, "--format", "json", "--confirm", "fresh")
    assert result.exit_code == 0, result.output
    assert _rows(_json(result))["fresh"]["embedding"] == "unavailable"


# --------------------------------------------------------------------------
# CLI, MCP and REST agree; a network answer follows index health's shape (#1552)
# --------------------------------------------------------------------------

NAMES = [
    "a",
    "b",
    "missing",
    "dup",
    "broken",
    "sub/no-id-here.md",
    "secret",
    ".hidden/secret.md",
]


def _seed_mixed(world):
    _write(world, "a.md", note("a", "Valid Zeta Entry"))
    _write(world, "b.md", note("b", "Broken Eta Entry", severity=None))
    _duplicate(world)
    _malformed(world)
    _id_less_by_path(world)
    _dot_folder(world)


_SCHEMA_ERROR = [("schema_violation", "error", "severity")]

#: Every field of every row the local CLI reports for NAMES after the seed,
#: by hand: name -> (id, path, status, reason, extra, searchable, findings,
#: embedding, confirmed). ``extra`` is the duplicate's ``paths``.
CLI_ROWS = {
    "a": ("a", "a.md", "indexed", None, None, True, [], "unavailable", True),
    "b": ("b", "b.md", "indexed", None, None, True, _SCHEMA_ERROR, "unavailable", False),
    "missing": ("missing", None, "not_found", None, None, None, [], None, False),
    "dup": (
        "dup", "dup-a.md", "duplicate", None, ["dup-a.md", "dup-b.md"], None, [], None, False,
    ),
    "broken": (
        "broken", "broken.md", "malformed", "its frontmatter does not parse",
        None, None, [], None, False,
    ),
    "sub/no-id-here.md": (
        "no-id-epsilon", "sub/no-id-here.md", "indexed", None, None, True, [],
        "unavailable", True,
    ),
    "secret": ("secret", ".hidden/secret.md", "skipped", "hidden", None, None, [], None, False),
    ".hidden/secret.md": (
        None, ".hidden/secret.md", "skipped", "hidden", None, None, [], None, False,
    ),
}  # fmt: skip

#: The same for a network caller (MCP, REST), by hand. As index health answers
#: them (services/index_health.py ``network_shape``): a file the walk does not
#: index is not described (the name is ``not_found``), and the files that
#: claim a duplicate id are not listed.
NETWORK_ROWS = {
    **CLI_ROWS,
    "dup": ("dup", "dup-a.md", "duplicate", None, None, None, [], None, False),
    "secret": ("secret", None, "not_found", None, None, None, [], None, False),
    ".hidden/secret.md": (None, None, "not_found", None, None, None, [], None, False),
}


def _as_tuples(rows: list[dict]) -> dict[str, tuple]:
    return {
        r["name"]: (
            r["id"],
            r["path"],
            r["status"],
            r["reason"],
            r.get("paths"),
            r["searchable"],
            [(f["rule"], f["severity"], f["field"]) for f in r["findings"]],
            r["embedding"],
            r["confirmed"],
        )
        for r in rows
    }


def _cli_rows(world):
    payload = _json(_sync(world, "--confirm", *NAMES))
    assert payload["outcome"] == "partial"
    return payload["confirm"]["entries"]


def _mcp_answer(world, args: dict) -> dict:
    return world.mcp("admin")._dispatch_tool("kb_index_sync", {"kb_name": KB, **args})


def _rest_answer(world, params: dict):
    from starlette.testclient import TestClient

    from tests._world import _create_app_without_static

    app = _create_app_without_static(world.config, world.root)
    try:
        with TestClient(app) as client:
            return client.post("/api/index/sync", params={"wait": "true", "kb": KB, **params})
    finally:
        state_db = getattr(app.state, "pyrite_db", None)
        if state_db is not None:
            state_db.close()


def _mcp_rows(world):
    res = _mcp_answer(world, {"confirm": NAMES})
    assert res["confirm"]["outcome"] == "partial", res
    return res["confirm"]["entries"]


def _rest_rows(world):
    resp = _rest_answer(world, {"confirm": NAMES})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["confirm"]["outcome"] == "partial", body
    return body["confirm"]["entries"]


#: surface -> (rows, the rows it must report).
SURFACES = {
    "cli": (_cli_rows, CLI_ROWS),
    "mcp": (_mcp_rows, NETWORK_ROWS),
    "rest": (_rest_rows, NETWORK_ROWS),
}


@pytest.mark.parametrize("surface", list(SURFACES))
def test_each_surface_reports_its_rows_written_by_hand(world, surface):
    _seed_mixed(world)
    rows_of, expected = SURFACES[surface]

    rows = rows_of(world)

    assert [r["name"] for r in rows] == NAMES
    assert _as_tuples(rows) == expected


@pytest.mark.parametrize("surface", ["mcp", "rest"])
def test_a_network_row_never_names_a_file_the_walk_does_not_index(world, surface):
    _seed_mixed(world)
    rows = _mcp_rows(world) if surface == "mcp" else _rest_rows(world)

    # Everything but `name`, which is the caller's own words echoed back.
    text = json.dumps([{k: v for k, v in r.items() if k != "name"} for r in rows])

    assert ".hidden" not in text
    assert "dup-b.md" not in text


# --------------------------------------------------------------------------
# One service decides: blank names, and a KB this process has not loaded
# --------------------------------------------------------------------------


def _cli_blank(world, blank):
    result = _sync(world, "--confirm", blank)
    return result.exit_code, _json(result)["confirm"]


def _mcp_blank(world, blank):
    block = _mcp_answer(world, {"confirm": [blank]})["confirm"]
    return (0 if block["outcome"] == "done" else 3), block


def _rest_blank(world, blank):
    resp = _rest_answer(world, {"confirm": [blank]})
    assert resp.status_code == 200, resp.text
    block = resp.json()["confirm"]
    return (0 if block["outcome"] == "done" else 3), block


BLANK_SURFACES = {"cli": _cli_blank, "mcp": _mcp_blank, "rest": _rest_blank}


@pytest.mark.parametrize("blank", ["", "  "], ids=["empty", "spaces"])
@pytest.mark.parametrize("surface", list(BLANK_SURFACES))
def test_a_blank_name_is_no_name_on_every_surface(world, surface, blank):
    """A blank name names nothing: what this sync added or changed is
    confirmed, the same on all three surfaces."""
    _write(world, "fresh.md", note("fresh", "Fresh Gamma"))

    code, block = BLANK_SURFACES[surface](world, blank)

    assert code == 0
    assert block["named"] is False
    assert [(r["name"], r["status"], r["confirmed"]) for r in block["entries"]] == [
        ("fresh", "indexed", True)
    ]


def test_the_service_refuses_a_kb_this_process_has_not_loaded(world):
    """MCP and REST resolve a KB through the registry as well as the loaded
    config; the sync reads the config. The service takes the KB the entry
    point resolved, and a KB the sync did not reach is refused, never
    answered `done` with no rows."""
    from pyrite.config import PyriteConfig
    from pyrite.exceptions import KBNotFoundError
    from pyrite.services.index_confirm import confirm_entries
    from pyrite.storage.index import IndexManager

    resolved = world.config.knowledge_bases[0]  # what the entry point resolved
    unloaded = PyriteConfig(knowledge_bases=[], settings=world.config.settings)
    index_mgr = IndexManager(world.db, unloaded)

    for names in (["unchanged"], []):
        with pytest.raises(KBNotFoundError):
            confirm_entries(index_mgr, resolved, names, index_mgr.sync_incremental(KB))


# --------------------------------------------------------------------------
# A fault reading the embedding state is a refusal, never a state (#1531)
# --------------------------------------------------------------------------


def _break_the_vector_table(world, monkeypatch):
    """A real database fault, no mock: the vector table is gone, so reading
    which entries are embedded fails in the database itself."""
    _with_a_model(world, monkeypatch)
    world.db._raw_conn.execute("DROP TABLE vec_entry")
    world.db._raw_conn.commit()


def test_a_fault_reading_the_embedding_state_is_a_storage_refusal_on_the_cli(world, monkeypatch):
    _break_the_vector_table(world, monkeypatch)

    result = _sync(world, "--confirm", "unchanged")

    assert result.exit_code == 1, result.output
    payload = _json(result)
    assert (payload["outcome"], payload["error_code"]) == ("error", "STORAGE_ERROR")
    assert "confirm" not in payload


def test_a_fault_reading_the_embedding_state_is_a_storage_refusal_over_mcp(world, monkeypatch):
    _break_the_vector_table(world, monkeypatch)

    res = _mcp_answer(world, {"confirm": ["unchanged"]})

    assert res.get("error_code") == "STORAGE_ERROR", res
    assert "confirm" not in res


def test_mcp_confirm_with_background_is_refused(world):
    res = world.mcp("admin")._dispatch_tool(
        "kb_index_sync", {"kb_name": KB, "background": True, "confirm": ["a"]}
    )
    assert res["error_code"] == "VALIDATION_FAILED", res


def test_rest_confirm_without_wait_is_refused(world):
    from starlette.testclient import TestClient

    from tests._world import _create_app_without_static

    app = _create_app_without_static(world.config, world.root)
    with TestClient(app) as client:
        resp = client.post("/api/index/sync", params={"kb": KB, "confirm": ["a"]})
    state_db = getattr(app.state, "pyrite_db", None)
    if state_db is not None:
        state_db.close()
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "VALIDATION_FAILED"


# --------------------------------------------------------------------------
# A refusal keeps dev's shapes. With no format asked, dev printed a refusal of
# `index sync` as one rich `ERROR [CODE]` line, except a storage fault, which
# its root printed as the JSON error shape. Each expected answer below is
# dev's, copied by hand from a run of dev's CLI.
# --------------------------------------------------------------------------


def _unasked(world, *args: str, monkeypatch):
    monkeypatch.delenv("PYRITE_FORMAT", raising=False)
    return world.cli("index", "sync", "--no-embed", *args)


def test_an_unknown_kb_with_no_format_asked_is_dev_s_error_line(world, monkeypatch):
    # A name close to none, so no "Did you mean" is added (dev's run had none).
    result = _unasked(world, "-k", "zzzz", monkeypatch=monkeypatch)

    assert result.exit_code == 1, result.output
    assert plain(result.stdout).splitlines() == [
        "ERROR [KB_NOT_FOUND]: KB 'zzzz' not found",
        "    hint: To list the KBs you can read, call kb_orient (MCP) or `pyrite orient` (CLI) "
        "with no KB name.",
    ]


def test_a_kb_yaml_that_does_not_load_with_no_format_asked_is_dev_s_error_line(world, monkeypatch):
    (world.kb_path(KB) / "kb.yaml").write_bytes(b"name: notes\ntypes: {note: [unclosed\n")

    result = _unasked(world, "-k", KB, monkeypatch=monkeypatch)

    assert result.exit_code == 1, result.output
    assert plain(result.stdout).splitlines() == [
        "ERROR [KB_CONFIG_INVALID]: This knowledge base's kb.yaml could not be loaded (it is "
        "not valid UTF-8 YAML holding a mapping); the operator needs to correct the file."
    ]


def test_an_unreadable_kb_with_no_format_asked_is_dev_s_error_line(world, monkeypatch):
    root = world.kb_path(KB)
    root.chmod(0o000)
    try:
        if os.access(root, os.R_OK):
            pytest.skip(f"running as a user (uid {os.getuid()}) whom mode 000 does not stop")
        result = _unasked(world, "-k", KB, monkeypatch=monkeypatch)
    finally:
        root.chmod(0o755)

    assert result.exit_code == 1, result.output
    assert plain(result.stdout).splitlines() == [
        "ERROR [KB_UNREADABLE]: Pyrite could not read this knowledge base's files: the "
        "operating system refused (for example, a folder or file Pyrite may not open)."
    ]


@pytest.fixture
def corrupt_index(tmp_path, monkeypatch):
    """One KB whose index file is garbage: every command refuses STORAGE_ERROR."""
    import pyrite.config as config_module

    config_dir, data_dir, kb = tmp_path / "config", tmp_path / "data", tmp_path / "kb"
    for d in (config_dir, data_dir, kb):
        d.mkdir()
    config_file = config_dir / "config.yaml"
    config_file.write_text(
        json.dumps({"knowledge_bases": [{"name": "t", "path": str(kb), "kb_type": "generic"}]}),
        encoding="utf-8",
    )
    (data_dir / "index.db").write_bytes(b"garbage")
    monkeypatch.setenv("PYRITE_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("PYRITE_DATA_DIR", str(data_dir))
    monkeypatch.delenv("PYRITE_FORMAT", raising=False)
    monkeypatch.setattr(config_module, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_module, "CONFIG_FILE", config_file)


def _invoke(*args: str, env: dict[str, str] | None = None):
    from typer.testing import CliRunner

    from pyrite.cli import app

    return CliRunner().invoke(app, list(args), env=env)


def test_a_refusal_under_format_rich_is_one_error_line(corrupt_index):
    result = _invoke("index", "sync", "--format", "rich")

    assert result.exit_code == 1, result.output
    lines = plain(result.stdout).splitlines()
    assert lines[0].startswith("ERROR [STORAGE_ERROR]: "), result.stdout


def test_a_storage_fault_with_no_format_asked_is_the_json_error_shape(corrupt_index):
    result = _invoke("index", "sync", "--confirm", "-k", "t", "x")

    assert result.exit_code == 1, result.output
    payload = _json(result)
    assert (payload["error_code"], payload["outcome"]) == ("STORAGE_ERROR", "error")


@pytest.mark.control(reason="dev already refuses as JSON under PYRITE_FORMAT=json: kept, not added")
def test_a_refusal_under_pyrite_format_json_is_json(corrupt_index):
    result = _invoke("index", "sync", env={"PYRITE_FORMAT": "json"})
    assert _json(result)["error_code"] == "STORAGE_ERROR"
