"""Tests for task service enhancements: QA linking."""

import tempfile
from pathlib import Path

import pytest

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.services.task_service import TaskService
from pyrite.storage.database import PyriteDB


@pytest.fixture
def task_env():
    """Fresh task environment for each test."""
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
        db.register_kb("test-tasks", "task", str(tasks_path), "Test task KB")

        svc = TaskService(config, db)
        yield {"svc": svc, "config": config, "db": db}
        db.close()


class TestLinkQAAssessment:
    def test_links_assessment_as_evidence(self, task_env):
        svc = task_env["svc"]

        task = svc.create_task(kb_name="test-tasks", title="QA validation task")
        result = svc.link_qa_assessment(task["entry_id"], "qa-assessment-1", "test-tasks")

        assert result["linked"] is True
        assert result["total_evidence"] == 1

    def test_deduplicates_evidence(self, task_env):
        svc = task_env["svc"]

        task = svc.create_task(kb_name="test-tasks", title="QA task")
        svc.link_qa_assessment(task["entry_id"], "qa-1", "test-tasks")
        result = svc.link_qa_assessment(task["entry_id"], "qa-1", "test-tasks")

        assert result["total_evidence"] == 1  # Not 2

    def test_returns_error_for_missing_task(self, task_env):
        svc = task_env["svc"]
        result = svc.link_qa_assessment("nonexistent", "qa-1", "test-tasks")
        assert result["linked"] is False
