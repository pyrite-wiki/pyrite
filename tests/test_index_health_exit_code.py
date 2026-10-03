"""Tests for #18: `index health` reporting unhealthy while exiting 0, and having
no way to scope the report to one KB.

Measured 2026-09-17: the command printed `"status": "unhealthy"` (22 missing
files, 33 stale, 52 malformed frontmatter) and exited 0. Every script, CI step
and agent that gates on the exit code read that as healthy -- a failure
converted to success at a boundary. The pyrite-dev skill uses this command as a
per-project gate, which also needs `-k` so another KB's problems do not decide
this project's verdict.
"""

import json

import pytest
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.models import EventEntry
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager
from pyrite.storage.repository import KBRepository

runner = CliRunner()


def _patch_config(config):
    """Patch load_config where the health command reads it."""
    from unittest.mock import patch

    return patch("pyrite.cli.context.load_config", return_value=config)


def _make_kb(tmp_path, name, n_events=1):
    path = tmp_path / name
    path.mkdir()
    kb = KBConfig(name=name, path=path, kb_type=KBType.EVENTS)
    repo = KBRepository(kb)
    for i in range(n_events):
        repo.save(
            EventEntry.create(date=f"2026-01-0{i + 1}", title=f"{name} event {i}", body="Body.")
        )
    return kb


@pytest.fixture
def two_kb_env(tmp_path):
    """Two indexed KBs: 'clean' stays healthy, 'dirty' is broken per-test."""
    clean = _make_kb(tmp_path, "clean")
    dirty = _make_kb(tmp_path, "dirty")
    config = PyriteConfig(
        knowledge_bases=[clean, dirty],
        settings=Settings(index_path=tmp_path / "index.db"),
    )
    db = PyriteDB(config.settings.index_path)
    IndexManager(db, config).index_all()
    db.close()
    return {"config": config, "tmpdir": tmp_path, "clean": clean, "dirty": dirty}


def _break_kb(env, kb_name):
    """Delete a KB's file behind the index's back -> a missing_files entry."""
    target = next((env["tmpdir"] / kb_name).rglob("*.md"))
    target.unlink()


def _run(config, *args):
    with _patch_config(config):
        return runner.invoke(app, ["index", "health", *args])


class TestExitCodeReflectsStatus:
    def test_healthy_index_exits_zero(self, two_kb_env):
        result = _run(two_kb_env["config"], "--format", "json")
        assert json.loads(result.output)["status"] == "healthy"
        assert result.exit_code == 0

    def test_unhealthy_index_exits_one(self, two_kb_env):
        """The bug: this printed 'unhealthy' and exited 0."""
        _break_kb(two_kb_env, "dirty")

        result = _run(two_kb_env["config"], "--format", "json")
        assert json.loads(result.output)["status"] == "unhealthy"
        assert result.exit_code == 1

    def test_no_fail_keeps_the_old_behaviour(self, two_kb_env):
        """An explicit opt-out for callers that just want the report."""
        _break_kb(two_kb_env, "dirty")

        result = _run(two_kb_env["config"], "--format", "json", "--no-fail")
        assert json.loads(result.output)["status"] == "unhealthy"
        assert result.exit_code == 0

    def test_unhealthy_rich_output_also_exits_one(self, two_kb_env):
        """The gate must not depend on which formatter was asked for."""
        _break_kb(two_kb_env, "dirty")

        result = _run(two_kb_env["config"], "--format", "rich")
        assert result.exit_code == 1


class TestKbScoping:
    def test_k_flag_excludes_another_kbs_problems(self, two_kb_env):
        """Scoped to the healthy KB, the broken one must not decide the verdict."""
        _break_kb(two_kb_env, "dirty")

        result = _run(two_kb_env["config"], "--format", "json", "-k", "clean")
        data = json.loads(result.output)
        assert data["status"] == "healthy", data
        assert data["missing_files"] == 0
        assert result.exit_code == 0

    def test_k_flag_reports_the_named_kbs_problems(self, two_kb_env):
        _break_kb(two_kb_env, "dirty")

        result = _run(two_kb_env["config"], "--format", "json", "-k", "dirty")
        data = json.loads(result.output)
        assert data["status"] == "unhealthy", data
        assert data["missing_files"] == 1
        assert result.exit_code == 1

    def test_k_flag_findings_name_only_that_kb(self, two_kb_env):
        _break_kb(two_kb_env, "dirty")
        _break_kb(two_kb_env, "clean")

        result = _run(two_kb_env["config"], "--format", "json", "-k", "dirty")
        rows = json.loads(result.output)["checks"]["missing_files"]
        assert rows and all(r["kb"] == "dirty" for r in rows), rows

    def test_unknown_kb_is_an_error_not_an_empty_clean_bill(self, two_kb_env):
        """Silently reporting 'healthy' for a typo'd KB name would be the same
        class of bug as the exit code: a non-answer that reads as success."""
        result = _run(two_kb_env["config"], "--format", "json", "-k", "no-such-kb")
        assert result.exit_code != 0


class TestOutputChannelsAreNotRegressed:
    def test_stdout_stays_parseable_json_when_unhealthy(self, two_kb_env):
        """A gate reads the exit code; a script still parses stdout. Warnings
        and progress must not end up in the JSON stream."""
        _break_kb(two_kb_env, "dirty")

        with _patch_config(two_kb_env["config"]):
            result = runner.invoke(
                app, ["index", "health", "--format", "json"], catch_exceptions=False
            )
        json.loads(result.stdout)  # raises if anything else was printed to stdout


class TestUnreadableAndOrphanedEntries:
    def test_unreadable_file_is_reported_with_path_and_error(self, two_kb_env):
        target = next((two_kb_env["tmpdir"] / "dirty").rglob("*.md"))
        target.write_bytes(b"\xff")

        result = _run(two_kb_env["config"], "--format", "json")
        data = json.loads(result.stdout)
        rows = data["checks"]["unreadable_files"]

        row = next(item for item in rows if item["path"] == str(target))
        assert data["unreadable_files"] == rows
        assert row["kb"] == "dirty"
        assert "UnicodeDecodeError" in row["error"]
        assert data["status"] == "unhealthy"
        assert result.exit_code == 1

    def test_replaced_id_names_old_index_row(self, two_kb_env):
        target = next((two_kb_env["tmpdir"] / "dirty").rglob("*.md"))
        content = target.read_text(encoding="utf-8")
        id_line = next(line for line in content.splitlines() if line.startswith("id:"))
        old_id = id_line.split(":", 1)[1].strip()
        target.write_text(content.replace(id_line, "id: replacement-id", 1), encoding="utf-8")

        result = _run(two_kb_env["config"], "--format", "json")
        data = json.loads(result.stdout)
        rows = data["checks"]["orphaned_entries"]

        old_row = next(item for item in rows if item["id"] == old_id)
        assert old_row["kb"] == "dirty"
        assert old_row["path"] == str(target)
        assert old_row["replacement_id"] == "replacement-id"
        assert any(item["id"] == "replacement-id" for item in data["checks"]["unindexed_files"])
        assert data["status"] == "unhealthy"
        assert result.exit_code == 1

    def test_unreadable_file_is_explained_in_rich_output(self, two_kb_env):
        target = next((two_kb_env["tmpdir"] / "dirty").rglob("*.md"))
        target.write_bytes(b"\xff")

        result = _run(two_kb_env["config"], "--format", "rich")
        assert "Unreadable files (1)" in result.stdout
        assert target.name in result.stdout
        assert "UnicodeDecodeError" in result.stdout
        assert result.exit_code == 1

    def test_moved_file_remains_missing_until_index_sync(self, two_kb_env):
        target = next((two_kb_env["tmpdir"] / "dirty").rglob("*.md"))
        moved = target.with_name("moved-" + target.name)
        target.rename(moved)

        result = _run(two_kb_env["config"], "--format", "json")
        data = json.loads(result.stdout)
        missing = data["checks"]["missing_files"]
        assert any(item["path"] == str(target) for item in missing)
        assert data["checks"]["orphaned_entries"] == []
        assert data["status"] == "unhealthy"
        assert result.exit_code == 1
