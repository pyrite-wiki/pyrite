"""Unloadable entries are never reported as not found by write paths (#1319).

A file that exists but fails to load as its declared type (here: a task
whose ``title`` is a mapping, which the loader refuses with a TypeError --
the same load-failure mechanism as the issue's ``rank: .inf`` example on
a backlog item) must be reported as unloadable, with its path and the
loader's message, by all three write paths the issue names -- task
updates, qa-fix field writes, and bulk-link writes -- and the file's
bytes must be unchanged. A genuinely missing id keeps the not-found
answer, so the two cases stay distinguishable.
"""

import re
import tempfile
from pathlib import Path

import pytest

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.exceptions import EntryNotFoundError, ValidationError
from pyrite.services.kb_service import KBService
from pyrite.services.qa_fix_service import QAFixService
from pyrite.services.task_service import TaskService
from pyrite.storage.database import PyriteDB
from pyrite.storage.repository import KBRepository


@pytest.fixture
def write_env():
    """A temp task KB with task, KB and qa-fix services sharing one KBService."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        tasks_path = tmpdir / "tasks-kb"
        tasks_path.mkdir()
        (tasks_path / "tasks").mkdir()

        kb_config = KBConfig(
            name="test-tasks",
            path=tasks_path,
            kb_type="task",
            description="Test task KB",
        )
        config = PyriteConfig(
            knowledge_bases=[kb_config],
            settings=Settings(index_path=tmpdir / "index.db"),
        )
        db = PyriteDB(config.settings.index_path)
        db.register_kb(
            name="test-tasks",
            kb_type="task",
            path=str(tasks_path),
            description="Test task KB",
        )
        kb_svc = KBService(config, db)
        task_svc = TaskService(config, db, kb_svc)
        qa_svc = QAFixService(config, db, kb_svc)
        yield {
            "kb_svc": kb_svc,
            "task_svc": task_svc,
            "qa_svc": qa_svc,
            "kb_config": kb_config,
            "tasks_path": tasks_path,
        }
        db.close()


def _break_entry(write_env, entry_id):
    """Corrupt one task file's title so it no longer loads; return its path/bytes."""
    repo = KBRepository(write_env["kb_config"])
    path = repo.find_file(entry_id)
    assert path is not None, f"no file found for {entry_id}"
    before = path.read_bytes()
    text = before.decode("utf-8")
    broken, n = re.subn(r"^title:.*$", "title: {a: b}", text, count=1, flags=re.M)
    assert n == 1, f"no title line to corrupt in {path}:\n{text}"
    path.write_text(broken, encoding="utf-8")
    assert repo.find_file(entry_id) is not None, "file lost its id; test setup is wrong"
    assert repo.load(entry_id) is None, "corrupted entry still loads; test setup is wrong"
    # Snapshot AFTER breaking: the write attempts below must leave these bytes alone.
    return path, path.read_bytes()


def _make_task(write_env, title="Unloadable target"):
    created = write_env["task_svc"].create_task(kb_name="test-tasks", title=title)
    return created["entry_id"]


class TestUnloadableWritePaths:
    def test_task_update_refuses_unloadable(self, write_env):
        entry_id = _make_task(write_env)
        path, before = _break_entry(write_env, entry_id)
        with pytest.raises(ValidationError) as excinfo:
            write_env["task_svc"].update_task(entry_id, "test-tasks", status="claimed")
        assert not isinstance(excinfo.value, EntryNotFoundError)
        assert str(path) in str(excinfo.value)
        assert path.read_bytes() == before

    def test_task_update_with_audit_comment_refuses_unloadable(self, write_env):
        # The audit route goes through apply_operations, not update_entry.
        entry_id = _make_task(write_env)
        path, before = _break_entry(write_env, entry_id)
        with pytest.raises(ValidationError) as excinfo:
            write_env["task_svc"].update_task(
                entry_id, "test-tasks", status="claimed", comment="why"
            )
        assert not isinstance(excinfo.value, EntryNotFoundError)
        assert str(path) in str(excinfo.value)
        assert path.read_bytes() == before

    def test_qa_fix_write_refuses_unloadable(self, write_env):
        entry_id = _make_task(write_env)
        path, before = _break_entry(write_env, entry_id)
        with pytest.raises(ValidationError) as excinfo:
            write_env["qa_svc"]._apply_field_updates(
                entry_id, "test-tasks", {"title": "fixed"}, write_env["kb_svc"]
            )
        assert not isinstance(excinfo.value, EntryNotFoundError)
        assert str(path) in str(excinfo.value)
        assert path.read_bytes() == before

    def test_bulk_link_refuses_unloadable_source(self, write_env):
        entry_id = _make_task(write_env)
        target_id = _make_task(write_env, title="Link target")
        path, before = _break_entry(write_env, entry_id)
        results = write_env["kb_svc"].add_links(
            "test-tasks",
            [{"source": entry_id, "target": target_id, "relation": "related_to"}],
        )
        assert results[0]["status"] == "failed"
        assert "not found" not in results[0]["error"]
        assert str(path) in results[0]["error"]
        assert results[0].get("error_code") != "ENTRY_NOT_FOUND"
        assert path.read_bytes() == before

    def test_missing_id_still_reports_not_found(self, write_env):
        with pytest.raises(EntryNotFoundError):
            write_env["task_svc"].update_task("no-such-task", "test-tasks", status="claimed")
        results = write_env["kb_svc"].add_links(
            "test-tasks",
            [{"source": "no-such-task", "target": "also-missing", "relation": "related_to"}],
        )
        assert results[0]["status"] == "failed"
        assert "not found" in results[0]["error"]
