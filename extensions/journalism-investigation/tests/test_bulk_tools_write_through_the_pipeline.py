"""The investigation bulk tools write through the KBService pipeline.

`investigation_bulk_edges` and `investigation_ftm_import` create many entries
in one call. Every entry they create goes through `KBService`, as every other
tool write does: it is a file on disk as well as an index row, a read-only KB
refuses it, and its id is a plain filename. `dry_run` writes nothing.

Driven through `PyriteMCPServer._dispatch_tool`, whose plugin context is the
real one the server injects.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.server.mcp_server import PyriteMCPServer

INV, FROZEN = "inv", "frozen"
EDGES = [
    {"type": "ownership", "fields": {"owner": "[[alice]]", "asset": "[[corp-a]]"}},
    {"type": "membership", "fields": {"person": "[[bob]]", "organization": "[[org-x]]"}},
]
ENTITIES = [
    {"id": "p-1", "schema": "Person", "properties": {"name": ["Alice Example"]}},
    {"id": "o-1", "schema": "Organization", "properties": {"name": ["Example Org"]}},
]


@pytest.fixture
def server(tmp_path: Path):
    for name in (INV, FROZEN):
        (tmp_path / name).mkdir()
    config = PyriteConfig(
        knowledge_bases=[
            KBConfig(name=INV, path=tmp_path / INV, kb_type="journalism-investigation"),
            KBConfig(
                name=FROZEN,
                path=tmp_path / FROZEN,
                kb_type="journalism-investigation",
                read_only=True,
            ),
        ],
        settings=Settings(index_path=tmp_path / "index.db"),
    )
    s = PyriteMCPServer(config=config, tier="write")
    try:
        yield s
    finally:
        s.close()


def _call(server, tool, args):
    return server._dispatch_tool(tool, args, client_kind="local")


def _files(server, kb) -> list[Path]:
    return sorted(server.config.get_kb(kb).path.rglob("*.md"))


def _indexed(server, kb) -> list[str]:
    rows = server.db._raw_conn.execute("SELECT id FROM entry WHERE kb_name = ?", (kb,))
    return sorted(r["id"] for r in rows.fetchall())


class TestBulkEdges:
    @pytest.mark.control(
        reason="over MCP these tools have written through the context's KBService since #1249; pinned while the CLI path changes"
    )
    def test_each_created_edge_is_a_file_and_an_index_row(self, server):
        result = _call(server, "investigation_bulk_edges", {"edges": EDGES, "kb_name": INV})
        assert result.get("created") == 2, result
        ids = [e["id"] for e in result["entries"]]
        assert sorted(p.stem for p in _files(server, INV)) == sorted(ids)
        assert _indexed(server, INV) == sorted(ids)

    def test_a_read_only_kb_is_refused_and_nothing_is_written(self, server):
        result = _call(server, "investigation_bulk_edges", {"edges": EDGES, "kb_name": FROZEN})
        assert result.get("error_code") == "KB_READ_ONLY", result
        assert _files(server, FROZEN) == []
        assert _indexed(server, FROZEN) == []

    @pytest.mark.control(
        reason="over MCP these tools have written through the context's KBService since #1249; pinned while the CLI path changes"
    )
    def test_an_edge_that_exists_is_skipped_not_replaced(self, server):
        _call(server, "investigation_bulk_edges", {"edges": EDGES[:1], "kb_name": INV})
        [path] = _files(server, INV)
        before = path.read_text()
        again = _call(server, "investigation_bulk_edges", {"edges": EDGES[:1], "kb_name": INV})
        assert (again["created"], again["skipped"]) == (0, 1), again
        assert path.read_text() == before

    @pytest.mark.control(
        reason="dev's create_entry already refuses an entry's own field under metadata (#1010); pinned"
    )
    def test_an_edge_naming_a_reserved_field_is_reported_and_the_batch_goes_on(self, server):
        # `title`, `body`, `entry_type` and `kb_name` belong to the entry, not
        # to the edge's fields; one such edge is an error, not a crash.
        bad = {"type": "ownership", "fields": {"owner": "[[x]]", "asset": "[[y]]", "title": "t"}}
        result = _call(server, "investigation_bulk_edges", {"edges": [bad, *EDGES], "kb_name": INV})
        assert [e["status"] for e in result["entries"]] == ["error", "created", "created"], result
        assert len(_files(server, INV)) == 2

    def test_a_reserved_field_refusal_is_a_row_naming_the_field(self, server):
        # The tool's own check refuses before the write, in the row shape every
        # field refusal carries (docs/json-contracts.md), on MCP too.
        bad = {"type": "ownership", "fields": {"owner": "[[x]]", "asset": "[[y]]", "title": "t"}}
        result = _call(server, "investigation_bulk_edges", {"edges": [bad], "kb_name": INV})
        assert [(r["field"], r["rule"]) for r in result["entries"][0]["errors"]] == [
            ("title", "reserved")
        ], result
        assert _files(server, INV) == []

    @pytest.mark.control(reason="dry_run wrote nothing before the change either")
    def test_dry_run_writes_nothing(self, server):
        result = _call(
            server, "investigation_bulk_edges", {"edges": EDGES, "kb_name": INV, "dry_run": True}
        )
        assert [e["status"] for e in result["entries"]] == ["would_create", "would_create"]
        assert _files(server, INV) == [] and _indexed(server, INV) == []


class TestFtmImport:
    @pytest.mark.control(
        reason="over MCP these tools have written through the context's KBService since #1249; pinned while the CLI path changes"
    )
    def test_each_imported_entity_is_a_file_and_an_index_row(self, server):
        result = _call(server, "investigation_ftm_import", {"entities": ENTITIES, "kb_name": INV})
        assert result.get("imported") == 2, result
        assert sorted(p.stem for p in _files(server, INV)) == ["ftm-o-1", "ftm-p-1"]
        assert _indexed(server, INV) == ["ftm-o-1", "ftm-p-1"]

    def test_a_read_only_kb_is_refused_and_nothing_is_written(self, server):
        result = _call(
            server, "investigation_ftm_import", {"entities": ENTITIES, "kb_name": FROZEN}
        )
        assert result.get("error_code") == "KB_READ_ONLY", result
        assert _files(server, FROZEN) == []
        assert _indexed(server, FROZEN) == []

    @pytest.mark.control(
        reason="over MCP these tools have written through the context's KBService since #1249; pinned while the CLI path changes"
    )
    def test_an_imported_id_must_be_a_plain_filename(self, server):
        entity = {"id": "a/b", "schema": "Person", "properties": {"name": ["Nested Name"]}}
        result = _call(server, "investigation_ftm_import", {"entities": [entity], "kb_name": INV})
        assert result["imported"] == 0 and result["errors"] == 1, result
        assert _files(server, INV) == [] and _indexed(server, INV) == []

    @pytest.mark.control(reason="dry_run wrote nothing before the change either")
    def test_dry_run_writes_nothing(self, server):
        result = _call(
            server,
            "investigation_ftm_import",
            {"entities": ENTITIES, "kb_name": INV, "dry_run": True},
        )
        assert result["imported"] == 2
        assert _files(server, INV) == [] and _indexed(server, INV) == []


START = {
    "title": "New Investigation",
    "scope": "Scope",
    "initial_entities": [{"name": "Jane Doe", "type": "person"}],
}


class TestInvestigationStart:
    @pytest.mark.control(
        reason="over MCP these tools have written through the context's KBService since #1249; pinned while the CLI path changes"
    )
    def test_the_investigation_and_its_entities_are_files_and_index_rows(self, server):
        result = _call(server, "investigation_start", {**START, "kb_name": INV})
        assert result.get("created") == "new-investigation", result
        assert sorted(p.stem for p in _files(server, INV)) == ["jane-doe", "new-investigation"]
        assert _indexed(server, INV) == ["jane-doe", "new-investigation"]

    @pytest.mark.control(
        reason="investigation_start's handler refused a read-only KB before this change"
    )
    def test_a_read_only_kb_is_refused_and_nothing_is_written(self, server):
        result = _call(server, "investigation_start", {**START, "kb_name": FROZEN})
        assert result.get("error_code") == "KB_READ_ONLY", result
        assert _files(server, FROZEN) == [] and _indexed(server, FROZEN) == []

    @pytest.mark.control(
        reason="over MCP these tools have written through the context's KBService since #1249; pinned while the CLI path changes"
    )
    def test_an_existing_entity_is_kept_not_replaced(self, server):
        server.svc.create_entry(INV, "jane-doe", "Jane Doe", "person", "kept body")
        result = _call(server, "investigation_start", {**START, "kb_name": INV})
        assert result.get("created") == "new-investigation", result
        [jane] = [p for p in _files(server, INV) if p.stem == "jane-doe"]
        assert "kept body" in jane.read_text()
        assert (server.db.get_entry("jane-doe", INV) or {}).get("body") == "kept body"

    def test_an_entity_type_that_is_not_a_plain_name_is_not_written(self, server):
        args = {**START, "kb_name": INV, "initial_entities": [{"name": "Odd", "type": "a/b"}]}
        result = _call(server, "investigation_start", args)
        assert [p.stem for p in _files(server, INV)] == ["new-investigation"], result
        assert "odd" not in _indexed(server, INV)


class TestMalformedItemsAreReportedPerItem:
    """A batch item of the wrong shape is that item's error, never a crash of
    the whole call: the other items are still created."""

    @pytest.mark.parametrize(
        "bad",
        ["not an edge", ["a", "list"], {"type": "ownership", "fields": "owner=x"}],
        ids=["string-edge", "list-edge", "string-fields"],
    )
    def test_a_malformed_edge_is_an_error_for_that_edge(self, server, bad):
        result = _call(server, "investigation_bulk_edges", {"edges": [bad, *EDGES], "kb_name": INV})
        assert "error_code" not in result, result
        assert [e["status"] for e in result["entries"]] == ["error", "created", "created"]
        assert len(_files(server, INV)) == 2

    def test_a_malformed_entity_is_an_error_for_that_entity(self, server):
        result = _call(
            server,
            "investigation_ftm_import",
            {"entities": ["not an entity", *ENTITIES], "kb_name": INV},
        )
        assert "error_code" not in result, result
        assert (result["imported"], result["errors"]) == (2, 1), result
