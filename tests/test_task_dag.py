"""Tests for task DAG traversal: subtree, ancestors, blocked-by, critical path."""

import tempfile
from pathlib import Path

import pytest

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.services.task_service import TaskService
from pyrite.storage.database import PyriteDB


@pytest.fixture
def dag_env():
    """Task environment with a dependency DAG:

    root
    ├── child-1 (depends on dep-a)
    │   └── grandchild-1
    └── child-2 (depends on dep-a, dep-b)

    dep-a (standalone)
    dep-b (depends on dep-c)
    dep-c (standalone)
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        tasks_path = tmpdir / "tasks-kb"
        tasks_path.mkdir()
        (tasks_path / "tasks").mkdir()

        kb_config = KBConfig(
            name="test",
            path=tasks_path,
            kb_type="task",
            description="Test",
        )
        config = PyriteConfig(
            knowledge_bases=[kb_config],
            settings=Settings(index_path=tmpdir / "index.db"),
        )
        db = PyriteDB(config.settings.index_path)
        db.register_kb("test", "task", str(tasks_path), "Test")
        svc = TaskService(config, db)

        # Build the DAG
        svc.create_task(kb_name="test", title="Root Task")
        svc.create_task(kb_name="test", title="Child 1", parent="root-task")
        svc.create_task(kb_name="test", title="Child 2", parent="root-task")
        svc.create_task(kb_name="test", title="Grandchild 1", parent="child-1")

        svc.create_task(kb_name="test", title="Dep A")
        svc.create_task(kb_name="test", title="Dep B", dependencies=["dep-c"])
        svc.create_task(kb_name="test", title="Dep C")

        # Add dependencies
        svc.update_task("child-1", "test", dependencies=["dep-a"])
        svc.update_task("child-2", "test", dependencies=["dep-a", "dep-b"])

        yield {"svc": svc, "config": config, "db": db}
        db.close()


class TestGetSubtree:
    def test_returns_all_descendants(self, dag_env):
        svc = dag_env["svc"]
        result = svc.get_subtree("root-task", "test")
        ids = {r["id"] for r in result}
        assert "child-1" in ids
        assert "child-2" in ids
        assert "grandchild-1" in ids
        assert "root-task" not in ids  # Root itself excluded

    def test_leaf_has_empty_subtree(self, dag_env):
        svc = dag_env["svc"]
        result = svc.get_subtree("grandchild-1", "test")
        assert result == []

    def test_single_level(self, dag_env):
        svc = dag_env["svc"]
        result = svc.get_subtree("child-1", "test")
        ids = {r["id"] for r in result}
        assert ids == {"grandchild-1"}


class TestGetAncestors:
    def test_returns_parent_chain(self, dag_env):
        svc = dag_env["svc"]
        result = svc.get_ancestors("grandchild-1", "test")
        ids = [r["id"] for r in result]
        assert "child-1" in ids
        assert "root-task" in ids

    def test_root_has_no_ancestors(self, dag_env):
        svc = dag_env["svc"]
        result = svc.get_ancestors("root-task", "test")
        assert result == []

    def test_order_is_parent_first(self, dag_env):
        svc = dag_env["svc"]
        result = svc.get_ancestors("grandchild-1", "test")
        ids = [r["id"] for r in result]
        # Immediate parent before grandparent
        assert ids.index("child-1") < ids.index("root-task")


class TestGetBlockedBy:
    def test_returns_transitive_dependencies(self, dag_env):
        svc = dag_env["svc"]
        result = svc.get_blocked_by("child-2", "test")
        ids = {r["id"] for r in result}
        # child-2 depends on dep-a and dep-b, dep-b depends on dep-c
        assert "dep-a" in ids
        assert "dep-b" in ids
        assert "dep-c" in ids  # Transitive!

    def test_no_dependencies(self, dag_env):
        svc = dag_env["svc"]
        result = svc.get_blocked_by("dep-c", "test")
        assert result == []

    def test_single_dependency(self, dag_env):
        svc = dag_env["svc"]
        result = svc.get_blocked_by("child-1", "test")
        ids = {r["id"] for r in result}
        assert ids == {"dep-a"}


class TestCriticalPath:
    def test_finds_longest_blocking_chain(self, dag_env):
        svc = dag_env["svc"]
        result = svc.critical_path("child-2", "test")
        ids = [r["id"] for r in result]
        # The longest chain is: child-2 → dep-b → dep-c (length 2)
        # (dep-a has no dependencies, so that chain is length 1)
        assert "dep-b" in ids
        assert "dep-c" in ids

    def test_no_path_for_leaf(self, dag_env):
        svc = dag_env["svc"]
        result = svc.critical_path("dep-c", "test")
        assert result == []

    # Without the cycle guard (`dep_id in active` in critical_path) the walk
    # never ends; the per-test deadline turns that hang into a failure in 20 s.
    @pytest.mark.timeout(20)
    def test_handles_cycle_gracefully(self, dag_env):
        """If a cycle exists in dependencies, should not infinite loop."""
        svc = dag_env["svc"]
        # Create a cycle: dep-c depends on dep-b (which depends on dep-c)
        svc.update_task("dep-c", "test", dependencies=["dep-b"])
        # Should return without hanging
        result = svc.critical_path("child-2", "test")
        assert isinstance(result, list)
