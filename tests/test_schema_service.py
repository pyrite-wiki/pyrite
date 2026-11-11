"""Tests for SchemaService."""

from pathlib import Path

import pytest

from pyrite.config import KBConfig
from pyrite.exceptions import KBNotFoundError
from pyrite.services.schema_service import SchemaService
from pyrite.utils.yaml import dump_yaml_file, load_yaml_file

# =========================================================================
# Fixtures
# =========================================================================


class FakeKBConfig:
    """Minimal KB config for testing."""

    def __init__(self, name: str, path: Path):
        self.name = name
        self.path = path
        self.kb_type = "generic"
        self.description = ""
        self.schema = None
        self.types = None
        self.policies = None
        self.read_only = False

    @property
    def kb_yaml_path(self) -> Path:
        return self.path / "kb.yaml"

    # The read-only rule is the real KBConfig's, not a copy of it.
    refuse_writes = KBConfig.refuse_writes

    def load_kb_yaml(self) -> bool:
        if self.kb_yaml_path.exists():
            data = load_yaml_file(self.kb_yaml_path)
            self.schema = data.get("schema")
            self.types = data.get("types")
            self.policies = data.get("policies")
            return True
        return False


class FakeConfig:
    """Minimal PyriteConfig for testing."""

    def __init__(self):
        self._kbs: dict[str, FakeKBConfig] = {}

    def get_kb(self, name: str):
        return self._kbs.get(name)

    def add_kb(self, kb: FakeKBConfig):
        self._kbs[kb.name] = kb


@pytest.fixture
def tmp_kb(tmp_path):
    """Create a temporary KB with config."""
    kb_path = tmp_path / "test-kb"
    kb_path.mkdir()
    kb = FakeKBConfig("test", kb_path)
    config = FakeConfig()
    config.add_kb(kb)
    return config, kb


# =========================================================================
# Tests
# =========================================================================


class TestShowSchema:
    def test_show_empty(self, tmp_kb):
        config, kb = tmp_kb
        svc = SchemaService(config)
        result = svc.show_schema("test")
        assert result["kb_name"] == "test"
        assert result["types"] == {}
        assert result["policies"] == {}
        assert result["validation"] == {}

    def test_show_with_types(self, tmp_kb):
        config, kb = tmp_kb
        dump_yaml_file(
            {
                "name": "test",
                "types": {
                    "note": {"description": "A note", "required": ["title"]},
                },
                "policies": {"enforce": True},
            },
            kb.kb_yaml_path,
        )
        svc = SchemaService(config)
        result = svc.show_schema("test")
        assert "note" in result["types"]
        assert result["types"]["note"]["description"] == "A note"
        assert result["policies"]["enforce"] is True


class TestAddType:
    def test_add_type_creates(self, tmp_kb):
        config, kb = tmp_kb
        # Start with a basic kb.yaml
        dump_yaml_file({"name": "test"}, kb.kb_yaml_path)

        svc = SchemaService(config)
        result = svc.add_type(
            "test",
            "task",
            {"description": "A task", "required": ["title"], "optional": ["status"]},
        )
        assert result["added"] is True
        assert result["type_name"] == "task"
        assert result["kb_name"] == "test"

        # Verify persisted
        data = load_yaml_file(kb.kb_yaml_path)
        assert "task" in data["types"]
        assert data["types"]["task"]["description"] == "A task"

    def test_add_type_preserves_existing(self, tmp_kb):
        config, kb = tmp_kb
        dump_yaml_file(
            {
                "name": "test",
                "types": {"note": {"description": "A note", "required": ["title"]}},
            },
            kb.kb_yaml_path,
        )

        svc = SchemaService(config)
        svc.add_type("test", "task", {"description": "A task", "required": ["title"]})

        data = load_yaml_file(kb.kb_yaml_path)
        assert "note" in data["types"]
        assert "task" in data["types"]

    def test_add_type_duplicate_errors(self, tmp_kb):
        config, kb = tmp_kb
        dump_yaml_file(
            {
                "name": "test",
                "types": {"task": {"description": "existing"}},
            },
            kb.kb_yaml_path,
        )

        svc = SchemaService(config)
        result = svc.add_type("test", "task", {"description": "new"})
        assert "error" in result
        assert "already exists" in result["error"]


class TestRemoveType:
    def test_remove_type(self, tmp_kb):
        config, kb = tmp_kb
        dump_yaml_file(
            {
                "name": "test",
                "types": {
                    "note": {"description": "A note"},
                    "task": {"description": "A task"},
                },
            },
            kb.kb_yaml_path,
        )

        svc = SchemaService(config)
        result = svc.remove_type("test", "task")
        assert result["removed"] is True

        data = load_yaml_file(kb.kb_yaml_path)
        assert "task" not in data["types"]
        assert "note" in data["types"]

    def test_remove_missing_errors(self, tmp_kb):
        config, kb = tmp_kb
        dump_yaml_file({"name": "test", "types": {}}, kb.kb_yaml_path)

        svc = SchemaService(config)
        result = svc.remove_type("test", "nonexistent")
        assert "error" in result
        assert "not found" in result["error"]


class TestSetSchema:
    def test_set_replaces_types(self, tmp_kb):
        config, kb = tmp_kb
        dump_yaml_file(
            {
                "name": "test",
                "description": "Test KB",
                "types": {"old": {"description": "old type"}},
            },
            kb.kb_yaml_path,
        )

        svc = SchemaService(config)
        result = svc.set_schema(
            "test",
            {
                "types": {
                    "new_a": {"description": "type A"},
                    "new_b": {"description": "type B"},
                },
            },
        )
        assert result["set"] is True
        assert result["type_count"] == 2

        data = load_yaml_file(kb.kb_yaml_path)
        assert "old" not in data["types"]
        assert "new_a" in data["types"]
        assert "new_b" in data["types"]
        # Preserves name/description
        assert data["name"] == "test"
        assert data["description"] == "Test KB"

    def test_set_preserves_name_description(self, tmp_kb):
        config, kb = tmp_kb
        dump_yaml_file(
            {"name": "test", "description": "My KB", "kb_type": "software"},
            kb.kb_yaml_path,
        )

        svc = SchemaService(config)
        svc.set_schema("test", {"types": {"a": {"description": "type A"}}})

        data = load_yaml_file(kb.kb_yaml_path)
        assert data["name"] == "test"
        assert data["description"] == "My KB"
        assert data["kb_type"] == "software"


class TestNotFound:
    def test_show_unknown_kb(self):
        config = FakeConfig()
        svc = SchemaService(config)
        with pytest.raises(KBNotFoundError, match="not found"):
            svc.show_schema("nonexistent")

    def test_add_type_unknown_kb(self):
        config = FakeConfig()
        svc = SchemaService(config)
        with pytest.raises(KBNotFoundError, match="not found"):
            svc.add_type("nonexistent", "task", {})
