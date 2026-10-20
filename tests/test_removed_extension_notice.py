"""A KB that still declares a removed extension's KB type is told so (#953).

The cascade extension was deleted. A user's KB registered with
``kb_type: cascade-timeline`` (or ``cascade-research``,
``cascade-solidarity``) is still a KB: its files read and write as generic
entries. What changes is that nothing declares its types any more, and the
user deserves one sentence saying why, instead of a type nothing explains
(not "undeclared": a kb.yaml that declares no types is unrestricted, #809,
so health lists nothing for them).

The rule lives in one place, ``pyrite/plugins/removed.py``; two surfaces
show it: ``orient`` (the first call an agent makes in a KB, CLI and MCP
share the service) and ``index health`` (beside its type reports). A
generic KB sees nothing.
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.server.mcp_server import PyriteMCPServer
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager

runner = CliRunner()

EVENT = (
    "---\nid: e1\ntitle: The hearing\ntype: timeline_event\ndate: 2026-01-01\n"
    'actors: ["[[jane-doe]]"]\n---\n\nText.\n'
)
ACTOR = "---\nid: jane-doe\ntitle: Jane Doe\ntype: actor\n---\n\nShe testifies.\n"


NOTE = "---\nid: n1\ntitle: A note\ntype: note\n---\n\nText.\n"


def _env(tmp_path, kb_type: str, *, plain: bool = False):
    """A KB of ``kb_type`` holding cascade-typed files, or (``plain``) only a note."""
    kb_path = tmp_path / "kb"
    (kb_path / "events").mkdir(parents=True)
    (kb_path / "actors").mkdir()
    # A kb.yaml with no types, as the cascade KBs had: the plugin declared them.
    (kb_path / "kb.yaml").write_text(f"name: old-timeline\nkb_type: {kb_type}\n", encoding="utf-8")
    if plain:
        (kb_path / "n1.md").write_text(NOTE, encoding="utf-8")
    else:
        (kb_path / "events" / "e1.md").write_text(EVENT, encoding="utf-8")
        (kb_path / "actors" / "jane-doe.md").write_text(ACTOR, encoding="utf-8")
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="old-timeline", path=kb_path, kb_type=kb_type)],
        settings=Settings(index_path=tmp_path / "index.db"),
    )
    db = PyriteDB(config.settings.index_path)
    try:
        IndexManager(db, config).index_all()
    finally:
        db.close()
    return config


def _cli(config, *args):
    from unittest.mock import patch

    with patch("pyrite.cli.context.load_config", return_value=config):
        return runner.invoke(app, list(args))


def _mcp(config, tool, args):
    server = PyriteMCPServer(config, tier="read")
    try:
        return server._dispatch_tool(tool, args)
    finally:
        server.close()


def _is_the_notice(text: str) -> bool:
    return "cascade" in text and "removed" in text and "#953" in text and "old-timeline" in text


class TestOrientNamesTheRemovedExtension:
    def test_cli_orient_carries_the_notice(self, tmp_path):
        config = _env(tmp_path, "cascade-timeline")
        result = _cli(config, "orient", "-k", "old-timeline", "--format", "json")
        assert result.exit_code == 0, result.output
        body = json.loads(result.output)
        assert body["kb_type"] == "cascade-timeline"
        [notice] = body["notices"]
        assert _is_the_notice(notice), notice
        assert "journalism-investigation" in notice
        # The types the KB holds that the extension declared are named.
        assert "timeline_event" in notice and "actor" in notice

    def test_a_generic_kb_that_still_holds_cascade_types_is_told_too(self, tmp_path):
        config = _env(tmp_path, "generic")
        result = _mcp(config, "kb_orient", {"kb_name": "old-timeline"})
        [notice] = result["notices"]
        assert "holds entries of type actor, timeline_event" in notice, notice
        assert "cascade" in notice and "#953" in notice

    def test_mcp_orient_carries_the_same_notice(self, tmp_path):
        config = _env(tmp_path, "cascade-research")
        result = _mcp(config, "kb_orient", {"kb_name": "old-timeline"})
        assert "error" not in result, result
        [notice] = result["notices"]
        assert _is_the_notice(notice), notice
        assert "cascade-research" in notice

    def test_brief_orient_keeps_it(self, tmp_path):
        config = _env(tmp_path, "cascade-solidarity")
        result = _mcp(config, "kb_orient", {"kb_name": "old-timeline", "detail": "brief"})
        assert result["notices"] and _is_the_notice(result["notices"][0])

    def test_a_generic_kb_with_its_own_types_has_no_notice(self, tmp_path):
        config = _env(tmp_path, "generic", plain=True)
        result = _mcp(config, "kb_orient", {"kb_name": "old-timeline"})
        assert "notices" not in result, result.get("notices")


class TestIndexHealthNamesTheRemovedExtension:
    def test_json_report_lists_it_as_a_warning(self, tmp_path):
        config = _env(tmp_path, "cascade-timeline")
        result = _cli(config, "index", "health", "--format", "json")
        assert result.exit_code == 0, result.output
        body = json.loads(result.output)
        assert body["status"] == "warning"
        [row] = body["removed_extensions"]
        assert row["kb"] == "old-timeline"
        assert row["kb_type"] == "cascade-timeline"
        assert row["extension"] == "cascade"
        assert row["entry_types"] == ["actor", "timeline_event"]
        assert _is_the_notice(row["message"]), row
        # The notice must not call those entries undeclared: a kb.yaml that
        # declares no types is unrestricted (#809), so they are not, and
        # `undeclared_types` below says so. They are entries of a type no
        # extension declares any more, and read and write as generic ones.
        assert "undeclared" not in row["message"], row["message"]
        assert "timeline_event" in row["message"] and "generic" in row["message"]
        # The removed extension's types are named by this row, not by
        # `undeclared_types`: a kb.yaml that declares no types is unrestricted
        # (#809, "the schema owns the type vocabulary"), so nothing is
        # undeclared there, and nothing hides the types either.
        assert body["undeclared_types"] == []

    def test_rich_report_prints_it_without_a_traceback(self, tmp_path):
        config = _env(tmp_path, "cascade-timeline")
        result = _cli(config, "index", "health", "--format", "rich")
        assert result.exit_code == 0, result.output
        assert "Traceback" not in result.output
        flat = " ".join(result.output.split())
        assert "cascade" in flat and "#953" in flat and "old-timeline" in flat

    def test_a_generic_kb_holding_cascade_types_is_a_row_too(self, tmp_path):
        config = _env(tmp_path, "generic")
        result = _cli(config, "index", "health", "--format", "json")
        body = json.loads(result.output)
        [row] = body["removed_extensions"]
        assert (row["kb_type"], row["extension"]) == ("generic", "cascade")
        assert row["entry_types"] == ["actor", "timeline_event"]
        assert "holds entries of type actor, timeline_event" in row["message"]

    def test_a_generic_kb_with_its_own_types_reports_none(self, tmp_path):
        config = _env(tmp_path, "generic", plain=True)
        result = _cli(config, "index", "health", "--format", "json")
        body = json.loads(result.output)
        assert body["removed_extensions"] == []
        assert body["checks"]["removed_extensions"] == []


def test_the_table_is_the_one_place_and_names_every_cascade_kb_type():
    from pyrite.plugins.removed import REMOVED_EXTENSIONS, removed_extension_for_kb_type

    from pyrite.plugins.removed import removed_extension_for_entry_types

    cascade = REMOVED_EXTENSIONS["cascade"]
    assert set(cascade.kb_types) == {"cascade-research", "cascade-timeline", "cascade-solidarity"}
    for kb_type in cascade.kb_types:
        assert removed_extension_for_kb_type(kb_type) is cascade
    assert removed_extension_for_kb_type("generic") is None
    assert removed_extension_for_kb_type("") is None
    assert removed_extension_for_kb_type(None) is None
    assert removed_extension_for_entry_types(["note", "timeline_event"]) is cascade
    assert removed_extension_for_entry_types(["note", "person"]) is None
    assert removed_extension_for_entry_types([]) is None
