"""Tests for admin CLI commands: kb, index, qa, schema, extension.

Tests the CLI command groups not covered by test_cli_commands.py (which covers
get, create, update, delete, list, timeline, tags, backlinks, config).
Uses CliRunner with patched config.
"""

import contextlib
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.models import EventEntry
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager
from pyrite.storage.repository import KBRepository

runner = CliRunner()


@pytest.fixture
def admin_env():
    """Environment for admin CLI tests."""
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
            description="Test events KB",
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

        yield {
            "config": config,
            "tmpdir": tmpdir,
            "events_kb": events_kb,
            "research_kb": research_kb,
        }


@contextlib.contextmanager
def _patch_config(env):
    """Patch load_config at the source so all importers see it."""
    target = env["config"]
    with (
        patch("pyrite.config.load_config", return_value=target),
        patch("pyrite.cli.load_config", return_value=target),
        patch("pyrite.cli.context.load_config", return_value=target),
        patch("pyrite.cli.kb_commands.load_config", return_value=target),
        patch("pyrite.cli.search_commands.load_config", return_value=target),
        patch("pyrite.cli.repo_commands.load_config", return_value=target),
    ):
        yield


# =========================================================================
# KB commands
# =========================================================================


@pytest.mark.cli
class TestKBList:
    def test_kb_list(self, admin_env):
        with _patch_config(admin_env):
            result = runner.invoke(app, ["kb", "list"])
            assert result.exit_code == 0
            assert "test-events" in result.output

    def test_kb_list_json(self, admin_env):
        with _patch_config(admin_env):
            result = runner.invoke(app, ["kb", "list", "--format", "json"])
            assert result.exit_code == 0
            data = _parse_json(result.output)
            assert isinstance(data, (list, dict))


@pytest.mark.cli
class TestKBDiscover:
    def test_kb_discover(self, admin_env):
        with _patch_config(admin_env):
            result = runner.invoke(app, ["kb", "discover", str(admin_env["tmpdir"])])
            assert result.exit_code == 0

    def test_kb_discover_json(self, admin_env):
        with _patch_config(admin_env):
            result = runner.invoke(
                app, ["kb", "discover", str(admin_env["tmpdir"]), "--format", "json"]
            )
            assert result.exit_code == 0


@pytest.mark.cli
class TestKBValidate:
    def test_kb_validate(self, admin_env):
        with _patch_config(admin_env):
            result = runner.invoke(app, ["kb", "validate"])
            assert result.exit_code in (0, 1, 2)

    def test_kb_validate_specific_kb(self, admin_env):
        with _patch_config(admin_env):
            result = runner.invoke(app, ["kb", "validate", "test-events"])
            # Exit 0 = clean, 1 = errors, 2 = warnings-only
            assert result.exit_code in (0, 1, 2)

    def test_kb_validate_json(self, admin_env):
        with _patch_config(admin_env):
            result = runner.invoke(app, ["kb", "validate", "--format", "json"])
            assert result.exit_code in (0, 1, 2)


@pytest.fixture
def drift_env(tmp_path):
    """KB with intentional content drift — undeclared types, missing
    required fields, and subdirectory mismatch — for exercising the
    Phase 0 checks that `pyrite kb validate` consolidates."""
    db_path = tmp_path / "index.db"

    kb_path = tmp_path / "drift-kb"
    kb_path.mkdir()
    (kb_path / "kb.yaml").write_text(
        "name: drift-kb\n"
        "kb_type: events\n"
        "types:\n"
        "  event:\n"
        "    description: A thing that happened\n"
        "    required: [date, title]\n"
        "    subdirectory: events\n"
    )
    (kb_path / "events").mkdir()

    kb_config = KBConfig(
        name="drift-kb",
        path=kb_path,
        kb_type=KBType.EVENTS,
        description="KB with drift for validation tests",
    )
    config = PyriteConfig(
        knowledge_bases=[kb_config],
        settings=Settings(index_path=db_path),
    )

    db = PyriteDB(db_path)
    db.register_kb("drift-kb", "events", str(kb_path), "")
    # Undeclared type.
    db.upsert_entry(
        {
            "id": "tl-undeclared",
            "kb_name": "drift-kb",
            "entry_type": "timeline_event",
            "title": "Not in kb.yaml",
            "body": "body",
            "date": "2026-04-24",
            "file_path": str(kb_path / "events" / "tl-undeclared.md"),
        }
    )
    # Missing required field (date).
    db.upsert_entry(
        {
            "id": "ev-missing-date",
            "kb_name": "drift-kb",
            "entry_type": "event",
            "title": "No date here",
            "body": "body",
            "file_path": str(kb_path / "events" / "ev-missing-date.md"),
        }
    )
    # Subdirectory mismatch.
    db.upsert_entry(
        {
            "id": "ev-wrong-dir",
            "kb_name": "drift-kb",
            "entry_type": "event",
            "title": "Wrong place",
            "body": "body",
            "date": "2026-04-24",
            "file_path": str(kb_path / "ev-wrong-dir.md"),  # at root, not events/
        }
    )
    db.close()

    return {"config": config, "tmpdir": tmp_path, "kb_config": kb_config, "kb_path": kb_path}


@pytest.mark.cli
class TestKBValidateContentDrift:
    """`pyrite kb validate` consolidates Phase 0 content drift checks
    (undeclared types, missing required fields, subdirectory mismatch)
    with the existing kb.yaml structural validation.

    Exit code semantics: 0 clean, 1 errors, 2 warnings-only.
    Structural errors from kb.validate() are errors (exit 1); the
    content-drift warnings are warnings (exit 2) when that's the only
    issue.
    """

    def test_warnings_only_exit_code_is_2(self, drift_env):
        """A KB with only content-drift warnings and no kb.yaml structural
        errors should exit 2 (warnings-only)."""
        with _patch_config(drift_env):
            result = runner.invoke(app, ["kb", "validate", "drift-kb", "--format", "json"])
            assert result.exit_code == 2, result.output

    def test_json_reports_content_drift(self, drift_env):
        with _patch_config(drift_env):
            result = runner.invoke(app, ["kb", "validate", "drift-kb", "--format", "json"])
            import json as _json

            data = _json.loads(result.output)
            # Look for the merged content-check fields on the per-kb result.
            kbs = data.get("kbs", [])
            assert len(kbs) == 1
            kb_result = kbs[0]
            assert "undeclared_types" in kb_result
            assert any(r["type"] == "timeline_event" for r in kb_result["undeclared_types"])
            assert "missing_required_fields" in kb_result
            assert any(r["id"] == "ev-missing-date" for r in kb_result["missing_required_fields"])
            assert "subdirectory_mismatches" in kb_result
            assert any(r["id"] == "ev-wrong-dir" for r in kb_result["subdirectory_mismatches"])

    def test_clean_kb_exits_zero(self, admin_env):
        """A KB with no content drift and valid kb.yaml should exit 0."""
        # admin_env has no kb.yaml so content checks are no-op. The structural
        # validator should also be clean.
        with _patch_config(admin_env):
            result = runner.invoke(app, ["kb", "validate", "test-events", "--format", "json"])
            assert result.exit_code == 0, result.output


# =========================================================================
# Index commands
# =========================================================================


@pytest.mark.cli
class TestIndexBuild:
    def test_index_build(self, admin_env):
        with _patch_config(admin_env):
            result = runner.invoke(app, ["index", "build"])
            assert result.exit_code == 0
            assert "entries" in result.output.lower() or "build complete" in result.output.lower()


@pytest.mark.cli
class TestIndexSync:
    def test_index_sync(self, admin_env):
        with _patch_config(admin_env):
            result = runner.invoke(app, ["index", "sync"])
            assert result.exit_code == 0
            assert "Sync complete" in result.output

    def test_index_sync_specific_kb(self, admin_env):
        with _patch_config(admin_env):
            result = runner.invoke(app, ["index", "sync", "-k", "test-events"])
            assert result.exit_code == 0


@pytest.mark.cli
class TestIndexStats:
    def test_index_stats(self, admin_env):
        with _patch_config(admin_env):
            result = runner.invoke(app, ["index", "stats"])
            assert result.exit_code == 0

    def test_index_stats_json(self, admin_env):
        with _patch_config(admin_env):
            result = runner.invoke(app, ["index", "stats", "--format", "json"])
            assert result.exit_code == 0
            data = _parse_json(result.output)
            assert "total_entries" in data or "entries" in str(data).lower()


@pytest.mark.cli
class TestIndexHealth:
    def test_index_health(self, admin_env):
        with _patch_config(admin_env):
            result = runner.invoke(app, ["index", "health"])
            assert result.exit_code == 0

    def test_index_health_json(self, admin_env):
        with _patch_config(admin_env):
            result = runner.invoke(app, ["index", "health", "--format", "json"])
            assert result.exit_code == 0
            data = _parse_json(result.output)
            assert isinstance(data, dict)


# =========================================================================
# Index reconcile
# =========================================================================


@pytest.fixture
def reconcile_env():
    """Environment with templated subdirectory and a misplaced entry."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        db_path = tmpdir / "index.db"

        kb_path = tmpdir / "project-kb"
        kb_path.mkdir()

        # Write a kb.yaml with a templated subdirectory for a custom type
        (kb_path / "kb.yaml").write_text(
            "name: project\n"
            "types:\n"
            "  task:\n"
            '    subdirectory: "tasks/{status}"\n'
            "    fields:\n"
            "      status:\n"
            "        type: select\n"
            "        options: [active, done]\n"
            "  adr:\n"
            "    subdirectory: adrs/\n"
            "    file_pattern: '{adr_number:04d}-{title}.md'\n"
        )

        kb = KBConfig(
            name="project",
            path=kb_path,
            kb_type="generic",
        )

        config = PyriteConfig(
            knowledge_bases=[kb],
            settings=Settings(index_path=db_path),
        )

        # Create an entry at the "wrong" location (tasks/active/ instead of tasks/done/)
        wrong_dir = kb_path / "tasks" / "active"
        wrong_dir.mkdir(parents=True)
        (wrong_dir / "my-task.md").write_text(
            "---\ntype: task\ntitle: My Task\nstatus: done\n---\nBody\n"
        )

        from pyrite.models.generic import GenericEntry

        adr_repo = KBRepository(kb)
        adr = GenericEntry(
            _entry_type="adr",
            id="adr-9",
            title="Original Title",
            body="",
            metadata={"adr_number": 9},
        )
        adr_repo.save(adr)
        adr.title = "Keep Pattern"
        adr_repo.save(adr, keep_filename=True, touch_updated_at=False)

        # Static subdirectories preserve deliberate placement; reconcile must
        # not flatten an archived ADR back into adrs/.
        archived_adr = GenericEntry(
            _entry_type="adr",
            id="adr-10",
            title="Archived ADR",
            body="",
            metadata={"adr_number": 10},
        )
        adr_repo.save(archived_adr, subdir="adrs/archive")

        # Collections describe the directory that contains this YAML file.
        collection_path = kb_path / "mydocs" / "__collection.yaml"
        collection_path.parent.mkdir(parents=True)
        collection_path.write_text("title: My Docs\n", encoding="utf-8")

        # Build the index
        db = PyriteDB(db_path)
        index_mgr = IndexManager(db, config)
        repo = KBRepository(kb)
        for entry, file_path in repo.list_entries():
            db.register_kb(name="project", kb_type="generic", path=str(kb_path))
            index_mgr.index_entry(entry, "project", file_path)

        yield {"config": config, "db": db, "kb_path": kb_path}
        db.close()


@pytest.mark.cli
class TestIndexReconcile:
    def test_reconcile_dry_run(self, reconcile_env):
        with _patch_config(reconcile_env):
            result = runner.invoke(app, ["index", "reconcile", "project"])
            assert result.exit_code == 0
            assert "my-task" in result.output
            assert "Dry run" in result.output or "dry" in result.output.lower()
            # File should NOT have moved
            assert (reconcile_env["kb_path"] / "tasks" / "active" / "my-task.md").exists()

    def test_reconcile_apply(self, reconcile_env):
        with _patch_config(reconcile_env):
            result = runner.invoke(app, ["index", "reconcile", "project", "--apply"])
            assert result.exit_code == 0
            assert "Moved" in result.output or "moved" in result.output.lower()
            # File should be at new location
            assert (reconcile_env["kb_path"] / "tasks" / "done" / "my-task.md").exists()
            assert not (reconcile_env["kb_path"] / "tasks" / "active" / "my-task.md").exists()

    def test_reconcile_no_mismatches(self, admin_env):
        """When all files match, reports no moves needed."""
        with _patch_config(admin_env):
            result = runner.invoke(app, ["index", "reconcile", "test-events"])
            assert result.exit_code == 0
            assert "match" in result.output.lower() or "0" in result.output

    def test_reconcile_respects_file_pattern(self, reconcile_env):
        """Reconcile preserves creation-time filenames while fixing directories."""
        kb_path = reconcile_env["kb_path"]
        original_path = kb_path / "adrs" / "0009-original-title.md"
        recomputed_path = kb_path / "adrs" / "0009-keep-pattern.md"
        original = original_path.read_bytes()
        task_source = kb_path / "tasks" / "active" / "my-task.md"
        task_target = kb_path / "tasks" / "done" / "my-task.md"

        with (
            _patch_config(reconcile_env),
            patch(
                "pyrite.cli.index_commands.get_config_and_db",
                return_value=(reconcile_env["config"], reconcile_env["db"]),
            ),
            patch.object(
                reconcile_env["db"],
                "register_kb",
                wraps=reconcile_env["db"].register_kb,
            ) as register_kb,
        ):
            dry_run = runner.invoke(app, ["index", "reconcile", "project"])
            assert dry_run.exit_code == 0, dry_run.output
            assert "Total: 1 file(s) to move" in dry_run.output
            assert "my-task.md" in dry_run.output
            assert "original-title.md" not in dry_run.output

            apply = runner.invoke(app, ["index", "reconcile", "project", "--apply"])
            assert apply.exit_code == 0, apply.output
            assert "Moved 1 file(s)." in apply.output
            register_kb.assert_not_called()

        assert original_path.read_bytes() == original
        assert not recomputed_path.exists()
        assert task_target.exists()
        assert not task_source.exists()
        indexed = IndexManager(reconcile_env["db"], reconcile_env["config"])._load_indexed_state(
            "project"
        )
        assert Path(indexed["my-task"]["file_path"]).resolve() == task_target.resolve()

    def test_reconcile_skips_static_subdirs_and_collection_files(self, reconcile_env):
        kb_path = reconcile_env["kb_path"]
        archived_adr = next((kb_path / "adrs" / "archive").glob("*.md"))
        collection_path = kb_path / "mydocs" / "__collection.yaml"

        with (
            _patch_config(reconcile_env),
            patch(
                "pyrite.cli.index_commands.get_config_and_db",
                return_value=(reconcile_env["config"], reconcile_env["db"]),
            ),
        ):
            dry_run = runner.invoke(app, ["index", "reconcile", "project"])
            assert dry_run.exit_code == 0, dry_run.output
            assert "Total: 1 file(s) to move" in dry_run.output
            assert "archive" not in dry_run.output
            assert "__collection.yaml" not in dry_run.output

            apply = runner.invoke(app, ["index", "reconcile", "project", "--apply"])
            assert apply.exit_code == 0, apply.output
            assert "Moved 1 file(s)." in apply.output

        assert archived_adr.exists()
        assert collection_path.exists()
        assert (kb_path / "tasks" / "done" / "my-task.md").exists()

    def test_reconcile_does_not_overwrite_racing_destination(self, reconcile_env):
        kb_path = reconcile_env["kb_path"]
        source = kb_path / "tasks" / "active" / "my-task.md"
        destination = kb_path / "tasks" / "done" / "my-task.md"

        def create_destination_after_precheck(_source, target):
            Path(target).write_text("created by concurrent writer", encoding="utf-8")
            raise FileExistsError("destination appeared after the existence check")

        with (
            _patch_config(reconcile_env),
            patch(
                "pyrite.cli.index_commands.get_config_and_db",
                return_value=(reconcile_env["config"], reconcile_env["db"]),
            ),
            patch(
                "pyrite.cli.index_commands.os.link",
                side_effect=create_destination_after_precheck,
            ),
        ):
            result = runner.invoke(app, ["index", "reconcile", "project", "--apply"])

        assert result.exit_code == 0, result.output
        assert "Failed to move my-task" in result.output
        assert source.exists()
        assert destination.read_text(encoding="utf-8") == "created by concurrent writer"

    def test_reconcile_reports_bad_template_path_per_entry(self, reconcile_env):
        with (
            _patch_config(reconcile_env),
            patch(
                "pyrite.cli.index_commands.get_config_and_db",
                return_value=(reconcile_env["config"], reconcile_env["db"]),
            ),
            patch(
                "pyrite.storage.repository.KBRepository._get_file_path",
                side_effect=ValueError("target resolves outside KB"),
            ),
        ):
            result = runner.invoke(app, ["index", "reconcile", "project"])

        assert result.exit_code == 0, result.output
        assert "my-task" in result.output
        assert "outside KB" in result.output
        assert "Traceback" not in result.output


# =========================================================================
# QA commands
# =========================================================================


@pytest.mark.cli
class TestQAValidate:
    def test_qa_validate(self, admin_env):
        with _patch_config(admin_env):
            result = runner.invoke(app, ["qa", "validate"])
            assert result.exit_code == 0

    def test_qa_validate_specific_kb(self, admin_env):
        with _patch_config(admin_env):
            result = runner.invoke(app, ["qa", "validate", "test-events"])
            assert result.exit_code == 0

    def test_qa_validate_json(self, admin_env):
        with _patch_config(admin_env):
            result = runner.invoke(app, ["qa", "validate", "--format", "json"])
            assert result.exit_code == 0
            data = _parse_json(result.output)
            assert isinstance(data, (list, dict))


@pytest.mark.cli
class TestQAStatus:
    def test_qa_status(self, admin_env):
        with _patch_config(admin_env):
            result = runner.invoke(app, ["qa", "status"])
            assert result.exit_code == 0

    def test_qa_status_json(self, admin_env):
        with _patch_config(admin_env):
            result = runner.invoke(app, ["qa", "status", "--format", "json"])
            assert result.exit_code == 0


# =========================================================================
# Search command
# =========================================================================


@pytest.mark.cli
class TestSearchCommand:
    def test_search_finds_entries(self, admin_env):
        with _patch_config(admin_env):
            result = runner.invoke(app, ["search", "immigration"])
            assert result.exit_code == 0
            assert "Test Event" in result.output or "immigration" in result.output.lower()

    def test_search_no_results(self, admin_env):
        with _patch_config(admin_env):
            result = runner.invoke(app, ["search", "xyznonexistent123"])
            assert result.exit_code == 0

    def test_search_with_kb_filter(self, admin_env):
        with _patch_config(admin_env):
            result = runner.invoke(app, ["search", "immigration", "--kb", "test-events"])
            assert result.exit_code == 0


# =========================================================================
# Extension commands
# =========================================================================


@pytest.mark.cli
class TestExtensionList:
    def test_extension_list(self, admin_env):
        with _patch_config(admin_env):
            result = runner.invoke(app, ["extension", "list"])
            # May have plugins or not — should not crash
            assert result.exit_code == 0

    def test_extension_list_json(self, admin_env):
        with _patch_config(admin_env):
            result = runner.invoke(app, ["extension", "list", "--format", "json"])
            assert result.exit_code == 0


@pytest.mark.cli
class TestExtensionInit:
    def test_extension_init(self, admin_env):
        ext_path = admin_env["tmpdir"] / "test-ext"
        with _patch_config(admin_env):
            result = runner.invoke(app, ["extension", "init", "test-ext", "--path", str(ext_path)])
            assert result.exit_code == 0
            assert (ext_path / "pyproject.toml").exists()

    def test_extension_init_with_types(self, admin_env):
        ext_path = admin_env["tmpdir"] / "typed-ext"
        with _patch_config(admin_env):
            result = runner.invoke(
                app,
                [
                    "extension",
                    "init",
                    "typed-ext",
                    "--path",
                    str(ext_path),
                    "--types",
                    "widget,gadget",
                ],
            )
            assert result.exit_code == 0
            assert (ext_path / "pyproject.toml").exists()


# =========================================================================
# Init command (headless KB creation)
# =========================================================================


@pytest.mark.cli
class TestInitCommand:
    def test_init_creates_kb(self, admin_env):
        kb_path = admin_env["tmpdir"] / "new-kb"
        with _patch_config(admin_env):
            result = runner.invoke(app, ["init", "--path", str(kb_path), "--template", "empty"])
            assert result.exit_code == 0
            assert (kb_path / "kb.yaml").exists()

    def test_init_with_template(self, admin_env):
        kb_path = admin_env["tmpdir"] / "software-kb"
        with _patch_config(admin_env):
            result = runner.invoke(app, ["init", "--path", str(kb_path), "--template", "software"])
            assert result.exit_code == 0


# =========================================================================
# KB Add / Remove / Create / Commit / Push / GC
# =========================================================================


@pytest.mark.cli
class TestKBAdd:
    def test_kb_add_success(self, admin_env):
        new_kb_path = admin_env["tmpdir"] / "new-kb-add"
        new_kb_path.mkdir()
        with _patch_config(admin_env):
            result = runner.invoke(
                app,
                ["kb", "add", str(new_kb_path), "--name", "added-kb", "--type", "research"],
            )
            assert result.exit_code == 0
            assert "Added KB" in result.output

    def test_kb_add_nonexistent_path(self, admin_env):
        with _patch_config(admin_env):
            result = runner.invoke(
                app,
                ["kb", "add", "/tmp/nonexistent-path-xyz-12345", "--name", "ghost-kb"],
            )
            assert result.exit_code == 1
            assert "does not exist" in result.output

    def test_kb_add_duplicate_name(self, admin_env):
        with _patch_config(admin_env):
            result = runner.invoke(
                app,
                ["kb", "add", str(admin_env["tmpdir"]), "--name", "test-events"],
            )
            assert result.exit_code == 1
            assert "already exists" in result.output


@pytest.mark.cli
class TestKBRemove:
    def test_kb_remove_user_kb_success(self, admin_env):
        """Removing a user-added KB succeeds."""
        with _patch_config(admin_env):
            # First add a user KB via the registry
            user_kb_path = admin_env["tmpdir"] / "user-removable"
            user_kb_path.mkdir()
            result = runner.invoke(
                app,
                ["kb", "add", str(user_kb_path), "--name", "user-removable"],
            )
            assert result.exit_code == 0

            # Now remove it
            result = runner.invoke(
                app,
                ["kb", "remove", "user-removable", "--force"],
            )
            assert result.exit_code == 0
            assert "Removed" in result.output

    def test_kb_remove_config_kb_fails(self, admin_env):
        """Removing a config-defined KB fails with a protected error."""
        with _patch_config(admin_env):
            result = runner.invoke(
                app,
                ["kb", "remove", "test-events", "--force"],
            )
            assert result.exit_code == 1
            assert "config.yaml" in result.output

    def test_kb_remove_nonexistent(self, admin_env):
        with _patch_config(admin_env):
            result = runner.invoke(
                app,
                ["kb", "remove", "no-such-kb", "--force"],
            )
            assert result.exit_code == 1
            assert "not found" in result.output


@pytest.mark.cli
class TestKBCreate:
    def test_kb_create_success(self, admin_env):
        kb_path = admin_env["tmpdir"] / "created-kb"
        with _patch_config(admin_env):
            result = runner.invoke(
                app,
                ["kb", "create", "--name", "created-kb", "--path", str(kb_path)],
            )
            assert result.exit_code == 0
            assert "Created KB" in result.output

    def test_kb_create_with_type(self, admin_env):
        kb_path = admin_env["tmpdir"] / "typed-kb"
        with _patch_config(admin_env):
            result = runner.invoke(
                app,
                [
                    "kb",
                    "create",
                    "--name",
                    "typed-kb",
                    "--path",
                    str(kb_path),
                    "--type",
                    "events",
                ],
            )
            assert result.exit_code == 0

    def test_kb_create_no_path(self, admin_env):
        """Creating non-ephemeral KB without --path should fail."""
        with _patch_config(admin_env):
            result = runner.invoke(
                app,
                ["kb", "create", "--name", "no-path-kb"],
            )
            assert result.exit_code == 1
            assert "path" in result.output.lower()


@pytest.mark.cli
class TestKBCommit:
    def test_kb_commit_non_git(self, admin_env):
        """Commit on a non-git KB should error gracefully."""
        with _patch_config(admin_env):
            result = runner.invoke(
                app,
                ["kb", "commit", "--kb", "test-events", "--message", "test commit"],
            )
            # Should fail gracefully (not a git repo)
            assert result.exit_code in (0, 1)

    def test_kb_commit_nonexistent_kb(self, admin_env):
        with _patch_config(admin_env):
            result = runner.invoke(
                app,
                ["kb", "commit", "--kb", "no-such-kb", "--message", "test"],
            )
            assert result.exit_code in (1, 2)


@pytest.mark.cli
class TestKBPush:
    def test_kb_push_non_git(self, admin_env):
        """Push on a non-git KB should error gracefully."""
        with _patch_config(admin_env):
            result = runner.invoke(
                app,
                ["kb", "push", "--kb", "test-events"],
            )
            assert result.exit_code in (0, 1)

    def test_kb_push_nonexistent_kb(self, admin_env):
        with _patch_config(admin_env):
            result = runner.invoke(
                app,
                ["kb", "push", "--kb", "no-such-kb"],
            )
            assert result.exit_code in (1, 2)


@pytest.mark.cli
class TestKBGC:
    def test_kb_gc(self, admin_env):
        with _patch_config(admin_env):
            result = runner.invoke(app, ["kb", "gc"])
            assert result.exit_code == 0

    def test_kb_gc_no_expired(self, admin_env):
        """GC with no expired KBs should succeed with informational message."""
        with _patch_config(admin_env):
            result = runner.invoke(app, ["kb", "gc"])
            assert result.exit_code == 0
            assert "expired" in result.output.lower() or "garbage" in result.output.lower()


# =========================================================================
# KB Schema (sub-commands under kb schema)
# =========================================================================


@pytest.mark.cli
class TestKBSchemaAddType:
    def test_schema_add_type_success(self, admin_env):
        # Create a kb.yaml so schema operations work
        kb_yaml = admin_env["events_kb"].path / "kb.yaml"
        kb_yaml.write_text("name: test-events\ntypes: {}\n")
        with _patch_config(admin_env):
            result = runner.invoke(
                app,
                [
                    "kb",
                    "schema",
                    "add-type",
                    "test-events",
                    "--type",
                    "widget",
                    "--description",
                    "A widget",
                ],
            )
            assert result.exit_code in (0, 1)

    def test_schema_add_type_nonexistent_kb(self, admin_env):
        with _patch_config(admin_env):
            result = runner.invoke(
                app,
                ["kb", "schema", "add-type", "no-such-kb", "--type", "widget"],
            )
            assert result.exit_code == 1

    def test_schema_add_type_with_fields(self, admin_env):
        kb_yaml = admin_env["events_kb"].path / "kb.yaml"
        if not kb_yaml.exists():
            kb_yaml.write_text("name: test-events\ntypes: {}\n")
        with _patch_config(admin_env):
            result = runner.invoke(
                app,
                [
                    "kb",
                    "schema",
                    "add-type",
                    "test-events",
                    "--type",
                    "gadget",
                    "--required",
                    "title,body",
                    "--optional",
                    "tags",
                ],
            )
            assert result.exit_code in (0, 1)


@pytest.mark.cli
class TestKBSchemaRemoveType:
    def test_schema_remove_type_success(self, admin_env):
        # Create kb.yaml with a type to remove
        kb_yaml = admin_env["events_kb"].path / "kb.yaml"
        kb_yaml.write_text("name: test-events\ntypes:\n  widget:\n    description: A widget\n")
        with _patch_config(admin_env):
            result = runner.invoke(
                app,
                ["kb", "schema", "remove-type", "test-events", "--type", "widget"],
            )
            assert result.exit_code in (0, 1)

    def test_schema_remove_type_nonexistent_type(self, admin_env):
        kb_yaml = admin_env["events_kb"].path / "kb.yaml"
        kb_yaml.write_text("name: test-events\ntypes: {}\n")
        with _patch_config(admin_env):
            result = runner.invoke(
                app,
                ["kb", "schema", "remove-type", "test-events", "--type", "nonexistent"],
            )
            assert result.exit_code in (0, 1)


@pytest.mark.cli
class TestKBSchemaSet:
    def test_schema_set_from_file(self, admin_env):
        schema_file = admin_env["tmpdir"] / "schema.yaml"
        schema_file.write_text("types:\n  article:\n    description: An article\n")
        with _patch_config(admin_env):
            result = runner.invoke(
                app,
                ["kb", "schema", "set", "test-events", "--schema-file", str(schema_file)],
            )
            assert result.exit_code in (0, 1)

    def test_schema_set_nonexistent_file(self, admin_env):
        with _patch_config(admin_env):
            result = runner.invoke(
                app,
                [
                    "kb",
                    "schema",
                    "set",
                    "test-events",
                    "--schema-file",
                    "/tmp/nonexistent-schema.yaml",
                ],
            )
            assert result.exit_code == 1


# =========================================================================
# Index embed
# =========================================================================


@pytest.mark.cli
class TestIndexEmbed:
    def test_index_embed_no_embedder(self, admin_env):
        """Embedding without sentence-transformers should fail gracefully."""
        with _patch_config(admin_env):
            # Bound and closed via the context manager: constructed inline in
            # the patch() call it was unreachable, so its WAL connection stayed
            # open for the rest of the session.
            with PyriteDB(admin_env["config"].settings.index_path) as db:
                with patch(
                    "pyrite.cli.index_commands.get_config_and_db",
                    return_value=(admin_env["config"], db),
                ):
                    result = runner.invoke(app, ["index", "embed"])
                    # Exits 1 if sentence-transformers not available, 0 if it is
                    assert result.exit_code in (0, 1)

    def test_index_embed_specific_kb(self, admin_env):
        with _patch_config(admin_env):
            result = runner.invoke(app, ["index", "embed", "-k", "test-events"])
            assert result.exit_code in (0, 1)

    def test_index_embed_force(self, admin_env):
        with _patch_config(admin_env):
            result = runner.invoke(app, ["index", "embed", "--force"])
            assert result.exit_code in (0, 1)


# =========================================================================
# QA assess
# =========================================================================


@pytest.mark.cli
class TestQAAssess:
    def test_qa_assess_kb(self, admin_env):
        with _patch_config(admin_env):
            result = runner.invoke(app, ["qa", "assess", "test-events"])
            assert result.exit_code in (0, 1)

    def test_qa_assess_specific_entry(self, admin_env):
        with _patch_config(admin_env):
            result = runner.invoke(
                app,
                ["qa", "assess", "test-events", "--entry", "nonexistent-id"],
            )
            # May fail because entry doesn't exist; should not crash
            assert result.exit_code in (0, 1)

    def test_qa_assess_json(self, admin_env):
        with _patch_config(admin_env):
            result = runner.invoke(
                app,
                ["qa", "assess", "test-events", "--format", "json"],
            )
            assert result.exit_code in (0, 1)


# =========================================================================
# Repo commands
# =========================================================================


@pytest.mark.cli
class TestRepoSubscribe:
    def test_repo_subscribe_invalid_url(self, admin_env):
        with _patch_config(admin_env):
            result = runner.invoke(app, ["repo", "subscribe", "not-a-valid-url"])
            # Should fail because it's not a real git URL
            assert result.exit_code in (0, 1)

    def test_repo_subscribe_url(self, admin_env):
        """Subscribe with a fake URL — should fail gracefully (no network)."""
        with _patch_config(admin_env):
            result = runner.invoke(
                app,
                ["repo", "subscribe", "https://github.com/fake/nonexistent-repo-xyz"],
            )
            assert result.exit_code in (0, 1)


@pytest.mark.cli
class TestRepoFork:
    def test_repo_fork_url(self, admin_env):
        """Fork with a fake URL — should fail gracefully."""
        with _patch_config(admin_env):
            result = runner.invoke(
                app,
                ["repo", "fork", "https://github.com/fake/nonexistent-repo-xyz"],
            )
            assert result.exit_code in (0, 1)

    def test_repo_fork_nonexistent(self, admin_env):
        with _patch_config(admin_env):
            result = runner.invoke(app, ["repo", "fork", "not-a-url"])
            assert result.exit_code in (0, 1)


@pytest.mark.cli
class TestRepoSync:
    def test_repo_sync_all(self, admin_env):
        with _patch_config(admin_env):
            result = runner.invoke(app, ["repo", "sync"])
            assert result.exit_code in (0, 1)

    def test_repo_sync_nonexistent(self, admin_env):
        with _patch_config(admin_env):
            result = runner.invoke(app, ["repo", "sync", "nonexistent-repo"])
            assert result.exit_code in (0, 1)


@pytest.mark.cli
class TestRepoUnsubscribe:
    def test_repo_unsubscribe(self, admin_env):
        with _patch_config(admin_env):
            result = runner.invoke(
                app,
                ["repo", "unsubscribe", "nonexistent-repo", "--force"],
            )
            # Should fail because repo doesn't exist, but not crash
            assert result.exit_code in (0, 1)


@pytest.mark.cli
class TestRepoStatus:
    def test_repo_status_specific(self, admin_env):
        with _patch_config(admin_env):
            result = runner.invoke(app, ["repo", "status", "nonexistent-repo"])
            assert result.exit_code in (0, 1)

    def test_repo_status_json(self, admin_env):
        with _patch_config(admin_env):
            result = runner.invoke(
                app,
                ["repo", "status", "nonexistent-repo", "--format", "json"],
            )
            assert result.exit_code in (0, 1)


@pytest.mark.cli
class TestRepoList:
    def test_repo_list(self, admin_env):
        with _patch_config(admin_env):
            result = runner.invoke(app, ["repo", "list"])
            assert result.exit_code == 0

    def test_repo_list_json(self, admin_env):
        with _patch_config(admin_env):
            result = runner.invoke(app, ["repo", "list", "--format", "json"])
            assert result.exit_code == 0


# =========================================================================
# Schema commands (top-level)
# =========================================================================


@pytest.mark.cli
class TestSchemaDiff:
    def test_schema_diff(self, admin_env):
        with _patch_config(admin_env):
            result = runner.invoke(app, ["schema", "diff", "--kb", "test-events"])
            # May fail if kb.yaml doesn't exist — that's fine
            assert result.exit_code in (0, 1)

    def test_schema_diff_nonexistent_kb(self, admin_env):
        with _patch_config(admin_env):
            result = runner.invoke(app, ["schema", "diff", "--kb", "no-such-kb"])
            assert result.exit_code == 1


@pytest.mark.cli
class TestSchemaMigrate:
    def test_schema_migrate(self, admin_env):
        with _patch_config(admin_env):
            result = runner.invoke(app, ["schema", "migrate", "--kb", "test-events"])
            assert result.exit_code in (0, 1)

    def test_schema_migrate_dry_run(self, admin_env):
        with _patch_config(admin_env):
            result = runner.invoke(
                app,
                ["schema", "migrate", "--kb", "test-events", "--dry-run"],
            )
            assert result.exit_code in (0, 1)


# =========================================================================
# Extension install / uninstall
# =========================================================================


@pytest.mark.cli
class TestExtensionInstall:
    def test_extension_install_success(self, admin_env):
        """Install an extension from an init-scaffolded directory (mock pip)."""
        ext_path = admin_env["tmpdir"] / "installable-ext"
        ext_path.mkdir()
        (ext_path / "pyproject.toml").write_text(
            '[project]\nname = "pyrite-installable"\nversion = "0.1.0"\n'
        )
        with _patch_config(admin_env):
            with patch("pyrite.cli.extension_commands.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                result = runner.invoke(app, ["extension", "install", str(ext_path)])
                assert result.exit_code == 0
                assert "installed" in result.output

    def test_extension_install_nonexistent_path(self, admin_env):
        with _patch_config(admin_env):
            result = runner.invoke(
                app,
                ["extension", "install", "/tmp/nonexistent-ext-xyz-12345"],
            )
            assert result.exit_code == 1
            assert "pyproject.toml" in result.output


@pytest.mark.cli
class TestExtensionUninstall:
    def test_extension_uninstall_success(self, admin_env):
        """Uninstall an extension (mock pip)."""
        with _patch_config(admin_env):
            with patch("pyrite.cli.extension_commands.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                result = runner.invoke(
                    app,
                    ["extension", "uninstall", "test-plugin", "--force"],
                )
                assert result.exit_code == 0
                assert "uninstalled" in result.output

    def test_extension_uninstall_pip_failure(self, admin_env):
        """Uninstall when pip fails should exit 1."""
        with _patch_config(admin_env):
            with patch("pyrite.cli.extension_commands.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="No such package")
                result = runner.invoke(
                    app,
                    ["extension", "uninstall", "nonexistent-ext", "--force"],
                )
                assert result.exit_code == 1


# =========================================================================
# Helpers
# =========================================================================


import json


def _parse_json(output: str) -> dict | list:
    """Parse JSON from CLI output, handling potential non-JSON prefix lines."""
    # Try full output first
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        pass
    # Try last line (some commands print header + JSON)
    for line in reversed(output.strip().split("\n")):
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    # Try finding JSON object/array in output
    for start_char, end_char in [("{", "}"), ("[", "]")]:
        start = output.find(start_char)
        if start >= 0:
            end = output.rfind(end_char)
            if end > start:
                try:
                    return json.loads(output[start : end + 1])
                except json.JSONDecodeError:
                    continue
    pytest.fail(f"Could not parse JSON from output: {output[:200]}")
