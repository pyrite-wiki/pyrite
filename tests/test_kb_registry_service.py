"""Tests for KBRegistryService — DB-first unified KB lifecycle management."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.exceptions import KBNotFoundError, KBProtectedError
from pyrite.services.kb_registry_service import KBRegistryService
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager


@pytest.fixture
def tmp_kb_path(tmp_path):
    """Create a temporary KB directory with some markdown files."""
    kb_dir = tmp_path / "test-kb"
    kb_dir.mkdir()
    (kb_dir / "entry1.md").write_text("---\ntitle: Entry 1\n---\nBody 1")
    (kb_dir / "entry2.md").write_text("---\ntitle: Entry 2\n---\nBody 2")
    return kb_dir


@pytest.fixture
def config_with_kb(tmp_path, tmp_kb_path):
    """Config with one configured KB."""
    config = MagicMock(spec=PyriteConfig)
    # Instance fields are not on a class spec; loading the registry reads
    # settings.workspace_path (the ephemeral-KB policy repair).
    config.settings = Settings(index_path=tmp_path / "test.db", workspace_path=tmp_path / "ws")
    kb = KBConfig(name="test-kb", path=tmp_kb_path, kb_type="research", description="Test KB")
    config.knowledge_bases = [kb]
    config.get_kb.side_effect = lambda name: kb if name == "test-kb" else None
    # DB-only KBs must resolve to real scalar fields, not a MagicMock that
    # can accidentally reach SQL bindings when health queries their types.
    config.kb_config_from_registry_row.side_effect = PyriteConfig(
        settings=config.settings
    ).kb_config_from_registry_row
    return config


@pytest.fixture
def db(tmp_path):
    """Create a PyriteDB instance."""
    db_path = tmp_path / "test.db"
    return PyriteDB(str(db_path))


@pytest.fixture
def index_mgr(db, config_with_kb):
    """Create an IndexManager instance."""
    return IndexManager(db, config_with_kb)


@pytest.fixture
def registry(config_with_kb, db, index_mgr):
    """Create a KBRegistryService instance."""
    return KBRegistryService(config_with_kb, db, index_mgr)


class TestSeedFromConfig:
    def test_seed_creates_db_rows_with_source_config(self, registry, db):
        """seed_from_config creates DB rows with source='config'."""
        count = registry.seed_from_config()
        assert count == 1

        kb = registry.get_kb("test-kb")
        assert kb is not None
        assert kb["source"] == "config"
        assert kb["name"] == "test-kb"
        assert kb["type"] == "research"

    def test_seed_is_idempotent(self, registry):
        """Calling seed_from_config twice doesn't create duplicates."""
        registry.seed_from_config()
        registry.seed_from_config()

        kbs = registry.list_kbs()
        assert len(kbs) == 1


class TestListKBs:
    def test_list_merges_config_and_user_kbs(self, registry, tmp_path):
        """list_kbs shows both config and user KBs with correct sources."""
        registry.seed_from_config()

        user_kb_path = tmp_path / "user-kb"
        user_kb_path.mkdir()
        registry.add_kb(name="user-kb", path=str(user_kb_path))

        kbs = registry.list_kbs()
        assert len(kbs) == 2

        sources = {kb["name"]: kb["source"] for kb in kbs}
        assert sources["test-kb"] == "config"
        assert sources["user-kb"] == "user"


class TestAddKB:
    def test_add_creates_db_row_not_config(self, registry, tmp_path):
        """add_kb creates a DB row, not in config.yaml."""
        registry.seed_from_config()
        user_path = tmp_path / "new-kb"
        user_path.mkdir()

        result = registry.add_kb(name="new-kb", path=str(user_path), description="New KB")
        assert result["name"] == "new-kb"
        assert result["source"] == "user"
        assert result["description"] == "New KB"

    def test_add_duplicate_raises_config_error(self, registry):
        """add_kb with a duplicate name raises ConfigError."""
        from pyrite.exceptions import ConfigError

        registry.seed_from_config()

        with pytest.raises(ConfigError, match="already exists"):
            registry.add_kb(name="test-kb", path="/tmp/whatever")


def _core_kb_presets():
    """Every KB preset a bare core registry offers, so the tests below hold
    for whatever is installed rather than for a hand-picked type."""
    from pyrite.plugins.registry import PluginRegistry

    return sorted(PluginRegistry().get_all_kb_presets())


def _tree(root: Path) -> dict[str, bytes | None]:
    """Every path under root, with file bytes (None for a directory).

    Registering a KB that is already set up must leave this identical, which a
    list of names alone would not prove: a rewritten file with the same path
    would slip through.
    """
    snapshot = {}
    for path in sorted(root.rglob("*")):
        snapshot[path.relative_to(root).as_posix()] = path.read_bytes() if path.is_file() else None
    return snapshot


@pytest.mark.parametrize("kb_type", _core_kb_presets())
class TestAddExistingKBIsNotScaffolded:
    """`kb add` on a directory that already has a kb.yaml registers it and
    writes nothing inside it (#945). Before the fix every preset materialized
    its directories and _templates/ into a KB the user had built by hand."""

    def _existing_kb(self, tmp_path: Path, name: str) -> Path:
        kb_dir = tmp_path / name
        (kb_dir / "notes").mkdir(parents=True)
        (kb_dir / "kb.yaml").write_text(f"name: {name}\nkb_type: research\n", encoding="utf-8")
        (kb_dir / "notes" / "a.md").write_text("---\ntitle: A\n---\nBody\n", encoding="utf-8")
        return kb_dir

    def test_writes_nothing_inside_the_kb(self, registry, tmp_path, kb_type):
        kb_dir = self._existing_kb(tmp_path, f"have-{kb_type}")
        before = _tree(kb_dir)

        result = registry.add_kb(name=kb_dir.name, path=str(kb_dir), kb_type=kb_type)

        assert _tree(kb_dir) == before
        assert result["name"] == kb_dir.name
        assert result["source"] == "user"

    def test_keeps_files_it_would_have_written(self, registry, tmp_path, kb_type):
        """The preset's own artifacts, already present and hand-edited, keep
        their bytes: no _templates/ rewrite, no directory churn."""
        from pyrite.plugins.registry import PluginRegistry

        preset = PluginRegistry().get_all_kb_presets()[kb_type]
        kb_dir = self._existing_kb(tmp_path, f"have-files-{kb_type}")
        for dirname in preset.get("directories", []):
            (kb_dir / dirname).mkdir(exist_ok=True)
        templates = kb_dir / "_templates"
        templates.mkdir(exist_ok=True)
        for type_name in preset.get("types", {}):
            (templates / f"{type_name}.md").write_text("hand written\n", encoding="utf-8")
        before = _tree(kb_dir)

        registry.add_kb(name=kb_dir.name, path=str(kb_dir), kb_type=kb_type)

        assert _tree(kb_dir) == before

    def test_a_kb_without_kb_yaml_still_scaffolds(self, registry, tmp_path, kb_type):
        """Unchanged: a directory that is not a KB yet is still created from
        the preset, because that is what `kb add` is for."""
        from pyrite.plugins.registry import PluginRegistry

        preset = PluginRegistry().get_all_kb_presets()[kb_type]
        kb_dir = tmp_path / f"fresh-{kb_type}"
        kb_dir.mkdir()

        registry.add_kb(name=kb_dir.name, path=str(kb_dir), kb_type=kb_type)

        assert (kb_dir / "kb.yaml").exists()
        for dirname in preset.get("directories", []):
            assert (kb_dir / dirname).is_dir()
        assert (kb_dir / "_templates").is_dir()
        for type_name in preset.get("types", {}):
            assert (kb_dir / "_templates" / f"{type_name}.md").is_file()


class TestRemoveKB:
    def test_remove_user_kb(self, registry, tmp_path):
        """remove_kb deletes a user KB."""
        user_path = tmp_path / "to-remove"
        user_path.mkdir()
        registry.add_kb(name="to-remove", path=str(user_path))
        assert registry.get_kb("to-remove") is not None

        result = registry.remove_kb("to-remove")
        assert result is True
        assert registry.get_kb("to-remove") is None

    def test_remove_config_kb_raises_protected_error(self, registry):
        """remove_kb on a config KB raises KBProtectedError."""
        registry.seed_from_config()

        with pytest.raises(KBProtectedError, match="config.yaml"):
            registry.remove_kb("test-kb")

    def test_remove_missing_raises_not_found(self, registry):
        """remove_kb on a missing KB raises KBNotFoundError."""
        with pytest.raises(KBNotFoundError):
            registry.remove_kb("nonexistent")


class TestUpdateKB:
    def test_update_changes_description(self, registry):
        """update_kb changes the description."""
        registry.seed_from_config()

        result = registry.update_kb("test-kb", description="Updated description")
        assert result["description"] == "Updated description"

    def test_update_missing_raises_not_found(self, registry):
        """update_kb on a missing KB raises KBNotFoundError."""
        with pytest.raises(KBNotFoundError):
            registry.update_kb("nonexistent", description="x")


class TestGetKB:
    def test_get_returns_none_for_missing(self, registry):
        """get_kb returns None for a non-existent KB."""
        assert registry.get_kb("nonexistent") is None

    def test_get_returns_kb_after_seed(self, registry):
        """get_kb returns a KB after seeding."""
        registry.seed_from_config()

        kb = registry.get_kb("test-kb")
        assert kb is not None
        assert kb["name"] == "test-kb"


class TestGetKBConfig:
    def test_builds_valid_kb_config_from_db(self, registry, tmp_path):
        """get_kb_config builds a valid KBConfig from a DB-only KB."""
        user_path = tmp_path / "cfg-test"
        user_path.mkdir()
        registry.add_kb(name="cfg-test", path=str(user_path), kb_type="events")

        # Override config to return None for this KB
        registry.config.get_kb.side_effect = lambda name: None
        # The mock config builds registry rows the way a real, trusted one does.
        registry.config.kb_config_from_registry_row.side_effect = (
            PyriteConfig().kb_config_from_registry_row
        )

        cfg = registry.get_kb_config("cfg-test")
        assert cfg is not None
        assert isinstance(cfg, KBConfig)
        assert cfg.name == "cfg-test"
        assert cfg.kb_type == "events"
        assert cfg.path == user_path

    def test_returns_config_kb_first(self, registry):
        """get_kb_config returns config KB if it exists there."""
        cfg = registry.get_kb_config("test-kb")
        assert cfg is not None
        assert cfg.name == "test-kb"


class TestReindexKB:
    def test_reindex_syncs_files(self, registry, tmp_kb_path):
        """reindex_kb syncs files to index."""
        registry.seed_from_config()

        result = registry.reindex_kb("test-kb")
        assert "added" in result
        assert "updated" in result
        assert "removed" in result
        assert result["added"] >= 0

    def test_reindex_missing_raises_not_found(self, registry):
        """reindex_kb on a missing KB raises KBNotFoundError."""
        with pytest.raises(KBNotFoundError):
            registry.reindex_kb("nonexistent")

    def test_reindex_reports_malformed_files(self, registry, tmp_kb_path):
        """A file that fails to parse during reindex must be visible in the
        result, not silently dropped from index coverage."""
        (tmp_kb_path / "broken.md").write_text(
            "---\ntitle: Broken\n*undefined-alias\n---\n\nBody\n"
        )
        registry.seed_from_config()

        result = registry.reindex_kb("test-kb")
        assert "malformed" in result, f"expected 'malformed' key in result; got {result.keys()}"
        paths = [m.get("path") if isinstance(m, dict) else str(m) for m in result["malformed"]]
        assert any("broken.md" in p for p in paths)

    def test_reindex_response_schema_carries_malformed(self, registry, tmp_kb_path):
        """The REST endpoint's KBReindexResponse must not silently drop the
        malformed-files list -- a Pydantic model missing the field would
        ignore it during **result unpacking, re-introducing exactly the
        silent-index-coverage-loss failure this fix targets."""
        from pyrite.server.schemas import KBReindexResponse

        (tmp_kb_path / "broken.md").write_text(
            "---\ntitle: Broken\n*undefined-alias\n---\n\nBody\n"
        )
        registry.seed_from_config()
        result = registry.reindex_kb("test-kb")

        response = KBReindexResponse(name="test-kb", **result)
        assert response.malformed, "malformed list must survive into the API response schema"
        assert any("broken.md" in m["path"] for m in response.malformed)


class TestHealthKB:
    def test_health_reports_healthy(self, registry, tmp_kb_path):
        """health_kb reports healthy for a good KB."""
        registry.seed_from_config()

        result = registry.health_kb("test-kb")
        assert result["name"] == "test-kb"
        assert result["path_exists"] is True
        assert result["file_count"] >= 2
        assert result["source"] == "config"

    def test_health_reports_missing_path(self, registry, db, tmp_path):
        """health_kb reports unhealthy for missing path."""
        missing_path = tmp_path / "does-not-exist"
        db.register_kb(name="missing-kb", kb_type="generic", path=str(missing_path))

        result = registry.health_kb("missing-kb")
        assert result["path_exists"] is False
        assert result["healthy"] is False
        assert result["type_name_drift"] == []

    def test_health_missing_kb_raises(self, registry):
        """health_kb on a missing KB raises KBNotFoundError."""
        with pytest.raises(KBNotFoundError):
            registry.health_kb("nonexistent")
