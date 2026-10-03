"""Tests for Typer CLI commands (pyrite CLI).

Tests create/update/delete/list/timeline/tags/backlinks via the Typer app.
Uses shared fixtures from conftest.py.
"""

import json
import tempfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tests.cli_help import plain

from pyrite.cli import app
from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.models import EventEntry
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager
from pyrite.storage.repository import KBRepository

runner = CliRunner()


@pytest.fixture
def cli_env():
    """Environment for Typer CLI tests using monkeypatch-style config override."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        db_path = tmpdir / "index.db"

        events_path = tmpdir / "events"
        events_path.mkdir()

        research_path = tmpdir / "research"
        research_path.mkdir()
        (research_path / "actors").mkdir()

        events_kb = KBConfig(
            name="test-events",
            path=events_path,
            kb_type=KBType.EVENTS,
        )
        research_kb = KBConfig(
            name="test-research",
            path=research_path,
            kb_type=KBType.RESEARCH,
        )

        config = PyriteConfig(
            knowledge_bases=[events_kb, research_kb],
            settings=Settings(index_path=db_path),
        )

        # Create sample data
        events_repo = KBRepository(events_kb)
        for i in range(3):
            event = EventEntry.create(
                date=f"2025-01-{10 + i:02d}",
                title=f"Test Event {i}",
                body=f"Body for event {i} about immigration.",
                importance=5 + i,
            )
            event.tags = ["test", "immigration"]
            events_repo.save(event)

        db = PyriteDB(db_path)
        IndexManager(db, config).index_all()
        db.close()

        yield {"config": config, "tmpdir": tmpdir, "events_kb": events_kb}


def _patch_config(cli_env):
    """Patch load_config to return test config in all CLI modules."""
    import contextlib
    from unittest.mock import patch

    @contextlib.contextmanager
    def _multi_patch():
        with patch("pyrite.cli.load_config", return_value=cli_env["config"]):
            with patch("pyrite.cli.context.load_config", return_value=cli_env["config"]):
                yield

    return _multi_patch()


@pytest.mark.cli
@pytest.mark.core
class TestTyperListCommand:
    def test_list_shows_kbs(self, cli_env):
        with _patch_config(cli_env):
            result = runner.invoke(app, ["list"])
            assert result.exit_code == 0
            assert "test-events" in result.output

    def test_list_shows_research_kb(self, cli_env):
        with _patch_config(cli_env):
            result = runner.invoke(app, ["list"])
            assert "test-research" in result.output


@pytest.mark.cli
@pytest.mark.core
class TestTyperGetCommand:
    def test_get_entry_found(self, cli_env):
        with _patch_config(cli_env):
            result = runner.invoke(app, ["get", "2025-01-10--test-event-0", "--kb", "test-events"])
            assert result.exit_code == 0
            assert "Test Event 0" in result.output

    def test_get_entry_not_found(self, cli_env):
        with _patch_config(cli_env):
            result = runner.invoke(app, ["get", "nonexistent"])
            assert result.exit_code == 1
            assert "not found" in result.output.lower()


@pytest.mark.cli
class TestTyperTimelineCommand:
    def test_timeline_shows_events(self, cli_env):
        with _patch_config(cli_env):
            result = runner.invoke(app, ["timeline", "--format", "rich"])
            assert result.exit_code == 0
            assert "Timeline" in result.output

    def test_timeline_with_date_filter(self, cli_env):
        with _patch_config(cli_env):
            result = runner.invoke(app, ["timeline", "--from", "2025-01-11"])
            assert result.exit_code == 0


@pytest.mark.cli
class TestTyperTagsCommand:
    def test_tags_shows_results(self, cli_env):
        with _patch_config(cli_env):
            result = runner.invoke(app, ["tags", "--format", "rich"])
            assert result.exit_code == 0
            assert "Tags" in result.output

    def test_tags_with_kb_filter(self, cli_env):
        with _patch_config(cli_env):
            result = runner.invoke(app, ["tags", "--kb", "test-events"])
            assert result.exit_code == 0


@pytest.mark.cli
class TestTyperBacklinksCommand:
    def test_backlinks_no_results(self, cli_env):
        with _patch_config(cli_env):
            result = runner.invoke(
                app, ["backlinks", "2025-01-10--test-event-0", "--kb", "test-events"]
            )
            assert result.exit_code == 0
            assert "No backlinks" in result.output


@pytest.mark.cli
@pytest.mark.core
class TestTyperCreateCommand:
    def test_create_note(self, cli_env):
        with _patch_config(cli_env):
            result = runner.invoke(
                app,
                [
                    "create",
                    "--kb",
                    "test-events",
                    "--type",
                    "note",
                    "--title",
                    "My New Note",
                    "--body",
                    "Some body text",
                ],
            )
            assert result.exit_code == 0
            assert "Created" in result.output

    def test_create_with_tags(self, cli_env):
        with _patch_config(cli_env):
            result = runner.invoke(
                app,
                [
                    "create",
                    "--kb",
                    "test-events",
                    "--type",
                    "note",
                    "--title",
                    "Tagged Entry",
                    "--tags",
                    "tag1,tag2",
                ],
            )
            assert result.exit_code == 0

    def test_create_nonexistent_kb(self, cli_env):
        with _patch_config(cli_env):
            result = runner.invoke(
                app,
                [
                    "create",
                    "--kb",
                    "nonexistent",
                    "--type",
                    "note",
                    "--title",
                    "Test",
                ],
            )
            assert result.exit_code == 1
            assert "not found" in result.output.lower() or "Error" in result.output
            assert "KB_NOT_FOUND" in result.output


@pytest.mark.cli
class TestTyperUpdateCommand:
    def test_update_title(self, cli_env):
        with _patch_config(cli_env):
            result = runner.invoke(
                app,
                [
                    "update",
                    "2025-01-10--test-event-0",
                    "--kb",
                    "test-events",
                    "--title",
                    "Updated Title",
                ],
            )
            assert result.exit_code == 0
            assert "updated" in result.output.lower()

    def test_update_nonexistent(self, cli_env):
        with _patch_config(cli_env):
            result = runner.invoke(
                app,
                [
                    "update",
                    "nonexistent",
                    "--kb",
                    "test-events",
                    "--title",
                    "New",
                    "--format",
                    "json",
                ],
            )
            assert result.exit_code == 1
            assert json.loads(result.output)["error_code"] == "NOT_FOUND"


@pytest.mark.cli
class TestTyperDeleteCommand:
    def test_delete_entry(self, cli_env):
        with _patch_config(cli_env):
            result = runner.invoke(
                app,
                ["delete", "2025-01-10--test-event-0", "--kb", "test-events", "--force"],
            )
            assert result.exit_code == 0
            assert "Deleted" in result.output

    def test_delete_nonexistent(self, cli_env):
        with _patch_config(cli_env):
            result = runner.invoke(
                app,
                ["delete", "nonexistent", "--kb", "test-events", "--force"],
            )
            assert result.exit_code == 1
            assert "NOT_FOUND" in result.output


@pytest.mark.cli
@pytest.mark.parametrize(
    ("args", "expected_code"),
    [
        (
            [
                "update",
                "2025-01-10--test-event-0",
                "--kb",
                "nonexistent",
                "--title",
                "New",
                "--format",
                "json",
            ],
            "KB_NOT_FOUND",
        ),
        (
            ["delete", "2025-01-10--test-event-0", "--kb", "nonexistent", "--force"],
            "KB_NOT_FOUND",
        ),
        (
            ["link", "2025-01-10--test-event-0", "2025-01-11--test-event-1", "--kb", "nonexistent"],
            "KB_NOT_FOUND",
        ),
        (
            ["link", "missing-source", "2025-01-11--test-event-1", "--kb", "test-events"],
            "NOT_FOUND",
        ),
    ],
)
def test_entry_write_commands_emit_specific_not_found_codes(cli_env, args, expected_code):
    with _patch_config(cli_env):
        result = runner.invoke(app, args)

    assert result.exit_code == 1
    if "--format" in args:
        assert json.loads(result.output)["error_code"] == expected_code
    else:
        assert f"[{expected_code}]" in plain(result.output)


@pytest.mark.cli
class TestAddCommand:
    def _write_md(self, path, frontmatter, body="Some content."):
        """Helper to write a markdown file with frontmatter."""
        path.write_text(f"---\n{frontmatter}\n---\n\n{body}", encoding="utf-8")

    def test_add_valid_file(self, cli_env):
        md = cli_env["tmpdir"] / "test-add.md"
        self._write_md(md, "type: note\ntitle: Added Note\ntags:\n  - test")
        with _patch_config(cli_env):
            result = runner.invoke(app, ["add", str(md), "--kb", "test-events"])
            assert result.exit_code == 0
            assert "Added" in result.output
            assert "added-note" in result.output

    def test_add_validate_only(self, cli_env):
        md = cli_env["tmpdir"] / "test-validate.md"
        self._write_md(md, "type: note\ntitle: Validate Only Note")
        with _patch_config(cli_env):
            result = runner.invoke(app, ["add", str(md), "--kb", "test-events", "--validate-only"])
            assert result.exit_code == 0
            assert "Valid" in result.output
            # Should NOT be saved to KB directory
            events_path = cli_env["events_kb"].path
            assert not (events_path / "notes" / "validate-only-note.md").exists()
            assert not (events_path / "validate-only-note.md").exists()

    def test_add_missing_type(self, cli_env):
        md = cli_env["tmpdir"] / "no-type.md"
        self._write_md(md, "title: No Type Here")
        with _patch_config(cli_env):
            result = runner.invoke(app, ["add", str(md), "--kb", "test-events"])
            assert result.exit_code == 1
            assert "type" in result.output.lower()

    def test_add_missing_title(self, cli_env):
        md = cli_env["tmpdir"] / "no-title.md"
        self._write_md(md, "type: note")
        with _patch_config(cli_env):
            result = runner.invoke(app, ["add", str(md), "--kb", "test-events"])
            assert result.exit_code == 1
            assert "title" in result.output.lower()

    def test_add_generates_id(self, cli_env):
        md = cli_env["tmpdir"] / "auto-id.md"
        self._write_md(md, "type: note\ntitle: Auto Generated ID")
        with _patch_config(cli_env):
            result = runner.invoke(app, ["add", str(md), "--kb", "test-events"])
            assert result.exit_code == 0
            assert "auto-generated-id" in result.output

    def test_add_nonexistent_file(self, cli_env):
        with _patch_config(cli_env):
            result = runner.invoke(
                app, ["add", "/tmp/does-not-exist-12345.md", "--kb", "test-events"]
            )
            assert result.exit_code == 1
            assert "not found" in result.output.lower() or "Error" in result.output

    def test_add_nonexistent_kb(self, cli_env):
        md = cli_env["tmpdir"] / "good-file.md"
        self._write_md(md, "type: note\ntitle: Good File")
        with _patch_config(cli_env):
            result = runner.invoke(app, ["add", str(md), "--kb", "nonexistent"])
            assert result.exit_code == 1
            assert "not found" in result.output.lower() or "Error" in result.output

    def test_add_duplicate_id(self, cli_env):
        md = cli_env["tmpdir"] / "dup.md"
        self._write_md(md, "type: note\ntitle: Duplicate Entry")
        with _patch_config(cli_env):
            result = runner.invoke(app, ["add", str(md), "--kb", "test-events"])
            assert result.exit_code == 0
            # Add same file again
            result = runner.invoke(app, ["add", str(md), "--kb", "test-events"])
            assert result.exit_code == 1
            assert "already exists" in result.output.lower() or "Error" in result.output


@pytest.mark.cli
class TestCreateImprovements:
    def test_create_body_file(self, cli_env):
        body_path = cli_env["tmpdir"] / "body.txt"
        body_path.write_text("Body from file content.", encoding="utf-8")
        with _patch_config(cli_env):
            result = runner.invoke(
                app,
                [
                    "create",
                    "--kb",
                    "test-events",
                    "--type",
                    "note",
                    "--title",
                    "Body File Test",
                    "--body-file",
                    str(body_path),
                ],
            )
            assert result.exit_code == 0
            assert "Created" in result.output

    def test_create_stdin(self, cli_env):
        with _patch_config(cli_env):
            result = runner.invoke(
                app,
                [
                    "create",
                    "--kb",
                    "test-events",
                    "--type",
                    "note",
                    "--title",
                    "Stdin Test",
                    "--stdin",
                ],
                input="Body from stdin.",
            )
            assert result.exit_code == 0
            assert "Created" in result.output

    def test_create_template_note(self, cli_env):
        with _patch_config(cli_env):
            result = runner.invoke(app, ["create", "--template", "--type", "note"])
            assert result.exit_code == 0
            assert "---" in plain(result.output)
            assert "type: note" in result.output
            assert "Your Title Here" in result.output

    def test_create_template_event(self, cli_env):
        with _patch_config(cli_env):
            result = runner.invoke(app, ["create", "--template", "--type", "event"])
            assert result.exit_code == 0
            assert "type: event" in result.output
            assert "date:" in result.output or "location:" in result.output

    def test_create_template_no_title(self, cli_env):
        with _patch_config(cli_env):
            result = runner.invoke(app, ["create", "--template"])
            assert result.exit_code == 0
            assert "Your Title Here" in result.output

    def test_create_body_file_merges_frontmatter(self, cli_env):
        """Fields from file frontmatter appear in created entry metadata."""
        body_path = cli_env["tmpdir"] / "with_fm.md"
        body_path.write_text(
            "---\nkind: component\npath: src/foo.py\nowner: alice\n---\nActual body content.",
            encoding="utf-8",
        )
        with _patch_config(cli_env):
            result = runner.invoke(
                app,
                [
                    "create",
                    "--kb",
                    "test-events",
                    "--type",
                    "note",
                    "--title",
                    "FM Merge Test",
                    "--body-file",
                    str(body_path),
                ],
            )
            assert result.exit_code == 0
            assert "Created" in result.output

            # Read back the saved file and verify frontmatter was merged
            saved = list(cli_env["events_kb"].path.glob("**/*fm-merge-test*"))
            assert len(saved) == 1
            content = saved[0].read_text(encoding="utf-8")
            assert "kind: component" in content
            assert "owner: alice" in content
            assert "path: src/foo.py" in content
            # Body should be just the content after closing ---
            assert "Actual body content." in content
            # Frontmatter block should NOT appear in body
            assert content.count("---") == 2  # only the entry's own frontmatter fences

    def test_create_body_file_cli_flags_override(self, cli_env):
        """Explicit CLI --field takes precedence over file frontmatter."""
        body_path = cli_env["tmpdir"] / "override.md"
        body_path.write_text(
            "---\nkind: from-file\npriority: low\n---\nOverride body.",
            encoding="utf-8",
        )
        with _patch_config(cli_env):
            result = runner.invoke(
                app,
                [
                    "create",
                    "--kb",
                    "test-events",
                    "--type",
                    "note",
                    "--title",
                    "Override Test",
                    "--body-file",
                    str(body_path),
                    "--field",
                    "kind=from-cli",
                ],
            )
            assert result.exit_code == 0

            saved = list(cli_env["events_kb"].path.glob("**/*override-test*"))
            assert len(saved) == 1
            content = saved[0].read_text(encoding="utf-8")
            assert "kind: from-cli" in content
            # priority from file should still be present
            assert "priority: low" in content

    def test_create_body_file_plain_content(self, cli_env):
        """File without frontmatter works as before (no regression)."""
        body_path = cli_env["tmpdir"] / "plain.txt"
        body_path.write_text("Just plain text, no frontmatter.", encoding="utf-8")
        with _patch_config(cli_env):
            result = runner.invoke(
                app,
                [
                    "create",
                    "--kb",
                    "test-events",
                    "--type",
                    "note",
                    "--title",
                    "Plain Body Test",
                    "--body-file",
                    str(body_path),
                ],
            )
            assert result.exit_code == 0
            assert "Created" in result.output

            saved = list(cli_env["events_kb"].path.glob("**/*plain-body-test*"))
            assert len(saved) == 1
            content = saved[0].read_text(encoding="utf-8")
            assert "Just plain text, no frontmatter." in content


@pytest.mark.cli
class TestTyperConfigCommand:
    def test_config_shows_info(self, cli_env):
        with _patch_config(cli_env):
            result = runner.invoke(app, ["config"])
            assert result.exit_code == 0
            assert "Config file" in result.output


@pytest.mark.cli
@pytest.mark.core
class TestTopLevelHelpAdvertisesOrient:
    """docs-operational-contracts-travel-with-tool item 1: nothing at the
    top-level `pyrite --help` currently tells a cold agent that `orient`
    exists -- it's buried as one command among ~20 in the command list,
    with no hint that it's the recommended starting point (unlike the MCP
    `kb_orient` tool description, which already says "Use this first")."""

    def test_top_level_help_mentions_orient_as_starting_point(self):
        """`orient` merely appearing in the auto-generated command list
        doesn't count -- every one of ~20 commands appears there, and
        `serve`/`mcp`'s own descriptions happen to contain "Start" too,
        which would make a loose substring check pass for the wrong
        reason. The bar is an explicit "run orient first" pointer, the
        way the MCP kb_orient tool description already says "Use this
        first". Look for the phrase in the epilog specifically, not
        anywhere in the full help text."""
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert (
            "run" in result.output.lower()
            and "orient" in result.output.lower()
            and ("first" in result.output.lower())
        )


@pytest.mark.cli
class TestMcpCommandTier:
    """docs-onboarding-fiction-sweep item 2: `pyrite mcp --tier read` is
    documented in README/getting-started/both MCP integration docs (the
    headline three-tier security story) but the CLI never exposed a
    --tier flag -- it hardcoded tier="write" unconditionally. The
    PyriteMCPServer backend already supports read/write/admin tiers;
    only the CLI flag was missing."""

    def test_default_tier_is_write(self, cli_env):
        """No --tier flag: preserves existing default behavior (write)."""
        from unittest.mock import MagicMock, patch

        with _patch_config(cli_env):
            with patch("pyrite.server.mcp_server.PyriteMCPServer") as mock_server_cls:
                mock_server_cls.VALID_TIERS = ("read", "write", "admin")
                mock_server_cls.return_value = MagicMock()
                runner.invoke(app, ["mcp"])

        mock_server_cls.assert_called_once()
        assert mock_server_cls.call_args.kwargs.get("tier") == "write"

    def test_tier_read_flag_constructs_read_tier_server(self, cli_env):
        from unittest.mock import MagicMock, patch

        with _patch_config(cli_env):
            with patch("pyrite.server.mcp_server.PyriteMCPServer") as mock_server_cls:
                mock_server_cls.VALID_TIERS = ("read", "write", "admin")
                mock_server_cls.return_value = MagicMock()
                result = runner.invoke(app, ["mcp", "--tier", "read"])

        assert result.exit_code == 0, result.output
        mock_server_cls.assert_called_once()
        assert mock_server_cls.call_args.kwargs.get("tier") == "read"

    def test_tier_admin_flag_constructs_admin_tier_server(self, cli_env):
        from unittest.mock import MagicMock, patch

        with _patch_config(cli_env):
            with patch("pyrite.server.mcp_server.PyriteMCPServer") as mock_server_cls:
                mock_server_cls.VALID_TIERS = ("read", "write", "admin")
                mock_server_cls.return_value = MagicMock()
                result = runner.invoke(app, ["mcp", "--tier", "admin"])

        assert result.exit_code == 0, result.output
        mock_server_cls.assert_called_once()
        assert mock_server_cls.call_args.kwargs.get("tier") == "admin"

    def test_invalid_tier_rejected(self, cli_env):
        with _patch_config(cli_env):
            result = runner.invoke(app, ["mcp", "--tier", "bogus"])

        assert result.exit_code != 0
        assert "bogus" in result.output.lower() or "invalid" in result.output.lower()

    def test_help_tool_inventory_matches_actual_tool_counts(self):
        """docs-operational-contracts-travel-with-tool item 5: `pyrite mcp
        --help` claimed the read tier exposes 8 named tools and the write
        tier adds 3 -- both drastically stale.

        #229 found the next fix (core dict counts: 29/11/8) had drifted the
        same way: it omitted 41+ plugin tools (sw_*, investigation_*,
        cascade_*, ...) registered dynamically per tier
        (`PyriteMCPServer._register_plugin_tools`), understating the real
        read-tier total by 2.4x (29 claimed vs. 70 actual). The help text
        now counts core + plugin tools per tier, the same way
        `PyriteMCPServer.__init__` assembles `self.tools` -- assert against
        that live total, not the core-only dicts alone. See also the more
        detailed regex-based check in test_mcp_help_tool_counts.py."""
        from pyrite.plugins import get_registry
        from pyrite.server.tool_schemas import ADMIN_TOOLS, READ_TOOLS, WRITE_TOOLS

        result = runner.invoke(app, ["mcp", "--help"])
        assert result.exit_code == 0

        registry = get_registry()
        read_total = len(READ_TOOLS) + len(registry.get_all_mcp_tools("read"))
        write_additions = (
            len(WRITE_TOOLS)
            + len(registry.get_all_mcp_tools("write"))
            - len(registry.get_all_mcp_tools("read"))
        )
        admin_additions = (
            len(ADMIN_TOOLS)
            + len(registry.get_all_mcp_tools("admin"))
            - len(registry.get_all_mcp_tools("write"))
        )

        assert str(read_total) in result.output, (
            f"expected live read tool count ({read_total}) in --help output"
        )
        assert str(write_additions) in result.output, (
            f"expected live write-tier addition count ({write_additions}) in --help output"
        )
        assert str(admin_additions) in result.output, (
            f"expected live admin-tier addition count ({admin_additions}) in --help output"
        )


@pytest.mark.cli
@pytest.mark.core
def test_index_sync_verify_forwards_option(monkeypatch):
    from pyrite.cli import index_commands

    calls = {}

    class FakeIndexManager:
        def __init__(self, db, config):
            pass

        def sync_incremental(self, kb_name=None, *, verify=False):
            calls["kb_name"] = kb_name
            calls["verify"] = verify
            return {
                "added": 0,
                "updated": 0,
                "removed": 0,
                "malformed": [],
                "duplicates": [],
            }

    monkeypatch.setattr(index_commands, "get_config_and_db", lambda: (object(), object()))
    monkeypatch.setattr("pyrite.storage.IndexManager", FakeIndexManager)
    result = runner.invoke(app, ["index", "sync", "--verify", "--no-embed"])

    assert result.exit_code == 0, result.output
    assert calls == {"kb_name": None, "verify": True}


@pytest.mark.cli
@pytest.mark.core
def test_index_sync_verify_rejects_background_mode():
    result = runner.invoke(app, ["index", "sync", "--verify", "--background"])

    assert result.exit_code == 2
    assert "cannot be combined with --background" in result.output
