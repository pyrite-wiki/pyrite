"""Health uses the KB's declared types, including unrestricted schemas."""

import pytest
from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager


@pytest.mark.parametrize("entry_type", ["note", "collection", "relationship"])
def test_core_override_is_reported_in_typed_kb(tmp_path, entry_type):
    assert _health(tmp_path, "types:\n  event:\n    description: Event\n", entry_type) == [
        {"kb": "test", "type": entry_type, "count": 1}
    ]


def test_empty_type_declarations_do_not_restrict_plugin_types(tmp_path):
    assert _health(tmp_path, "types: {}\n", "custom_plugin") == []


@pytest.mark.control(reason="No schema already permits unlisted types")
def test_absent_schema_is_unrestricted(tmp_path):
    assert _health(tmp_path, None, "custom_plugin") == []


@pytest.mark.control(reason="An explicitly declared type is already accepted")
def test_declared_type_is_accepted(tmp_path):
    assert _health(tmp_path, "types:\n  event:\n    description: Event\n", "event") == []


def _health(tmp_path, schema, entry_type):
    root = tmp_path / "kb"
    root.mkdir()
    if schema is not None:
        (root / "kb.yaml").write_text("name: test\n" + schema, encoding="utf-8")
    path = root / "entry.md"
    path.write_text(
        f"---\nid: entry\ntitle: Entry\ntype: {entry_type}\n---\nBody\n", encoding="utf-8"
    )
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="test", path=root)],
        settings=Settings(index_path=tmp_path / "index.db"),
    )
    db = PyriteDB(config.settings.index_path)
    try:
        manager = IndexManager(db, config)
        db.register_kb("test", "generic", str(root))
        db.upsert_entry(
            {
                "id": "entry",
                "kb_name": "test",
                "entry_type": entry_type,
                "title": "Entry",
                "file_path": str(path),
            }
        )
        return manager.check_health()["undeclared_types"]
    finally:
        db.close()
