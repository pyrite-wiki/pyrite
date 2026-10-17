"""Tests for schema versioning and migration (#93)."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pyrite.migrations import Migration, MigrationRegistry, get_migration_registry
from pyrite.models.base import Entry
from pyrite.models.core_types import NoteEntry, entry_from_frontmatter
from pyrite.models.generic import GenericEntry
from pyrite.schema import FieldSchema, KBSchema, TypeSchema
from pyrite.storage.repository import new_entry_text


# =========================================================================
# Step 1: Schema dataclass extensions
# =========================================================================


class TestFieldSchemaSinceVersion:
    def test_roundtrip(self):
        data = {"type": "text", "required": True, "since_version": 3}
        fs = FieldSchema.from_dict("category", data)
        assert fs.since_version == 3
        d = fs.to_dict()
        assert d["since_version"] == 3

    def test_none_suppressed(self):
        data = {"type": "text"}
        fs = FieldSchema.from_dict("name", data)
        assert fs.since_version is None
        d = fs.to_dict()
        assert "since_version" not in d


class TestTypeSchemaVersion:
    def test_version_parsing(self):
        schema_data = {
            "types": {
                "article": {
                    "description": "A news article",
                    "version": 3,
                    "fields": {
                        "category": {"type": "select", "required": True, "since_version": 2},
                    },
                }
            }
        }
        kb = KBSchema.from_dict(schema_data)
        assert kb.types["article"].version == 3
        assert kb.types["article"].fields["category"].since_version == 2

    def test_version_default_zero(self):
        schema_data = {"types": {"note": {"description": "A note"}}}
        kb = KBSchema.from_dict(schema_data)
        assert kb.types["note"].version == 0

    def test_version_serialized(self):
        ts = TypeSchema(name="test", version=2)
        d = ts.to_dict()
        assert d["version"] == 2

    def test_version_zero_suppressed(self):
        ts = TypeSchema(name="test", version=0)
        d = ts.to_dict()
        assert "version" not in d


class TestKBSchemaVersion:
    def test_schema_version_parsing(self):
        data = {"schema_version": 5, "types": {}}
        kb = KBSchema.from_dict(data)
        assert kb.schema_version == 5

    def test_schema_version_default(self):
        kb = KBSchema.from_dict({})
        assert kb.schema_version == 0


# =========================================================================
# Step 1: Validation with since_version
# =========================================================================


class TestValidateSinceVersion:
    def _make_schema(self):
        return KBSchema(
            types={
                "article": TypeSchema(
                    name="article",
                    version=3,
                    fields={
                        "category": FieldSchema(
                            name="category",
                            field_type="text",
                            required=True,
                            since_version=3,
                        ),
                    },
                )
            }
        )

    def test_old_entry_gets_warning(self):
        schema = self._make_schema()
        result = schema.validate_entry("article", {"title": "Test"}, context={"_schema_version": 2})
        assert result["valid"] is True  # no errors
        assert any(w["field"] == "category" for w in result["warnings"])

    def test_current_entry_gets_error(self):
        schema = self._make_schema()
        result = schema.validate_entry("article", {"title": "Test"}, context={"_schema_version": 3})
        assert result["valid"] is False
        assert any(e["field"] == "category" for e in result["errors"])

    def test_no_version_context_strict(self):
        schema = self._make_schema()
        result = schema.validate_entry("article", {"title": "Test"})
        assert result["valid"] is False
        assert any(e["field"] == "category" for e in result["errors"])

    def test_field_present_no_error(self):
        schema = self._make_schema()
        result = schema.validate_entry(
            "article",
            {"title": "Test", "category": "news"},
            context={"_schema_version": 1},
        )
        assert result["valid"] is True
        assert len(result["errors"]) == 0


# =========================================================================
# Step 2: Entry model _schema_version
# =========================================================================


class TestEntrySchemaVersion:
    @pytest.mark.control(
        reason="a rewritten test of a property that already held; its old form used the model's emitter (Entry.save/to_markdown), gone since #955 P4"
    )
    def test_roundtrip(self):
        entry = NoteEntry(id="test-1", title="Test", _schema_version=3)
        md = new_entry_text(entry)
        parsed = NoteEntry.from_markdown(md)
        assert parsed._schema_version == 3

    def test_zero_suppressed(self):
        entry = NoteEntry(id="test-2", title="Test", _schema_version=0)
        fm = entry.to_frontmatter()
        assert "_schema_version" not in fm

    def test_nonzero_emitted(self):
        entry = NoteEntry(id="test-3", title="Test", _schema_version=5)
        fm = entry.to_frontmatter()
        assert fm["_schema_version"] == 5

    def test_generic_entry_not_in_metadata(self):
        meta = {
            "id": "gen-1",
            "title": "Generic",
            "type": "custom_type",
            "_schema_version": 2,
            "custom_field": "value",
        }
        entry = GenericEntry.from_frontmatter(meta, "body")
        assert entry._schema_version == 2
        assert "_schema_version" not in entry.metadata


# =========================================================================
# Step 3: MigrationRegistry
# =========================================================================


class TestMigrationRegistry:
    def test_register_and_apply(self):
        reg = MigrationRegistry()

        @reg.register("article", 1, 2, description="Add category field")
        def migrate_v1_v2(data):
            data["category"] = data.get("category", "general")
            return data

        result = reg.apply("article", {"title": "Test"}, 1, 2)
        assert result["category"] == "general"
        assert result["_schema_version"] == 2

    def test_chain_resolution(self):
        reg = MigrationRegistry()

        @reg.register("article", 1, 2)
        def m1(data):
            data["step1"] = True
            return data

        @reg.register("article", 2, 3)
        def m2(data):
            data["step2"] = True
            return data

        chain = reg.get_chain("article", 1, 3)
        assert len(chain) == 2
        assert chain[0].from_version == 1
        assert chain[1].from_version == 2

    def test_chain_gap_error(self):
        reg = MigrationRegistry()

        @reg.register("article", 1, 2)
        def m1(data):
            return data

        @reg.register("article", 3, 4)
        def m2(data):
            return data

        with pytest.raises(ValueError, match="Migration gap"):
            reg.get_chain("article", 1, 4)

    def test_no_migration_needed(self):
        reg = MigrationRegistry()
        chain = reg.get_chain("article", 3, 3)
        assert chain == []

    def test_has_migrations(self):
        reg = MigrationRegistry()
        assert not reg.has_migrations("article")

        @reg.register("article", 1, 2)
        def m(data):
            return data

        assert reg.has_migrations("article")


# =========================================================================
# Step 4: Repository integration
# =========================================================================


class TestRepositoryMigration:
    def test_load_migrates(self, tmp_path):
        """Load entry with old version, verify migration applied."""
        from pyrite.config import KBConfig
        from pyrite.storage.repository import KBRepository

        # Set up KB directory with entry file
        kb_dir = tmp_path / "test-kb"
        kb_dir.mkdir()
        notes_dir = kb_dir / "notes"
        notes_dir.mkdir()

        entry_file = notes_dir / "test-note.md"
        entry_file.write_text(
            "---\nid: test-note\ntitle: Test Note\ntype: note\n_schema_version: 1\n---\n\nContent\n"
        )

        # Create kb.yaml with versioned type
        kb_yaml = kb_dir / "kb.yaml"
        kb_yaml.write_text("name: test\ntypes:\n  note:\n    description: A note\n    version: 2\n")

        config = KBConfig(name="test", path=kb_dir)
        repo = KBRepository(config)

        # Register a migration
        from pyrite.migrations import MigrationRegistry

        reg = MigrationRegistry()

        @reg.register("note", 1, 2, description="Add summary default")
        def m(data):
            if not data.get("summary"):
                data["summary"] = "auto-summary"
            return data

        # Temporarily swap global registry
        import pyrite.migrations as mig_mod
        import pyrite.storage.repository as repo_mod

        old_get = mig_mod.get_migration_registry
        mig_mod.get_migration_registry = lambda: reg
        repo_mod.get_migration_registry = lambda: reg

        try:
            entry = repo.load("test-note")
            assert entry is not None
            assert entry._schema_version == 2
        finally:
            mig_mod.get_migration_registry = old_get
            repo_mod.get_migration_registry = old_get

    def test_save_stamps_version(self, tmp_path):
        """Save entry, verify _schema_version in file."""
        from pyrite.config import KBConfig
        from pyrite.storage.repository import KBRepository

        kb_dir = tmp_path / "test-kb"
        kb_dir.mkdir()

        kb_yaml = kb_dir / "kb.yaml"
        kb_yaml.write_text("name: test\ntypes:\n  note:\n    description: A note\n    version: 3\n")

        config = KBConfig(name="test", path=kb_dir)
        repo = KBRepository(config)

        entry = NoteEntry(id="stamp-test", title="Stamp Test")
        assert entry._schema_version == 0

        path = repo.save(entry, subdir="notes")
        content = path.read_text()
        assert "_schema_version: 3" in content

    def test_migrate_idempotent(self, tmp_path):
        """Load+save twice, same result."""
        from pyrite.config import KBConfig
        from pyrite.storage.repository import KBRepository

        kb_dir = tmp_path / "test-kb"
        kb_dir.mkdir()
        notes_dir = kb_dir / "notes"
        notes_dir.mkdir()

        entry_file = notes_dir / "idem-test.md"
        entry_file.write_text(
            "---\nid: idem-test\ntitle: Idempotent\ntype: note\n_schema_version: 2\n---\n\nBody\n"
        )

        kb_yaml = kb_dir / "kb.yaml"
        kb_yaml.write_text("name: test\ntypes:\n  note:\n    description: A note\n    version: 2\n")

        config = KBConfig(name="test", path=kb_dir)
        repo = KBRepository(config)

        entry1 = repo.load("idem-test")
        repo.save(entry1, subdir="notes")
        content1 = entry_file.read_text()

        entry2 = repo.load("idem-test")
        repo.save(entry2, subdir="notes")
        content2 = entry_file.read_text()

        # Core frontmatter should be the same (timestamps may differ)
        assert "_schema_version: 2" in content1
        assert "_schema_version: 2" in content2
