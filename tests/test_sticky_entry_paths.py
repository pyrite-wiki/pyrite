"""Updates and identity changes preserve deliberate on-disk placement."""

import pytest
from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.models.factory import build_entry
from pyrite.storage.database import PyriteDB
from pyrite.storage.document_manager import DocumentManager
from pyrite.storage.index import IndexManager
from pyrite.storage.repository import KBRepository


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "kb"
    root.mkdir()
    kb = KBConfig(name="test", path=root)
    config = PyriteConfig(knowledge_bases=[kb], settings=Settings(index_path=tmp_path / "index.db"))
    db = PyriteDB(config.settings.index_path)
    yield KBRepository(kb), DocumentManager(db, IndexManager(db, config)), kb
    db.close()


def test_update_keeps_root_file(env):
    repo, manager, kb = env
    entry = build_entry("note", entry_id="alpha", title="Alpha", body="Body")
    path = repo.save(entry, subdir="")
    entry = repo.load("alpha")
    entry.title = "Changed"
    assert manager.save_entry(entry, "test", kb) == path
    assert path.exists()
    assert list(kb.path.rglob("*.md")) == [path]


@pytest.mark.parametrize("subdir", ["", "notes", "hand/made"])
@pytest.mark.parametrize("after", ["update", "delete"])
def test_rename_keeps_filename_and_new_id_is_addressable(env, subdir, after):
    repo, manager, kb = env
    entry = build_entry("note", entry_id="alpha", title="Alpha", body="Body")
    path = repo.save(entry, subdir=subdir)
    repo.rename("alpha", "omega")
    assert path.exists()
    assert repo.find_file("omega") == path
    assert repo.load("omega").id == "omega"
    assert repo.find_file("alpha") is None
    if after == "update":
        changed = repo.load("omega")
        changed.title = "Changed"
        assert manager.save_entry(changed, "test", kb) == path
        assert list(kb.path.rglob("*.md")) == [path]
    else:
        assert repo.delete("omega")
        assert not path.exists()


@pytest.mark.control(reason="Templated folders already move when their field changes")
def test_template_folder_move_remains_supported(env):
    from pyrite.schema.field_schema import TypeSchema
    from pyrite.schema.kb_schema import KBSchema

    repo, manager, kb = env
    kb._schema_cache = KBSchema(
        types={"note": TypeSchema(name="note", subdirectory="notes/{status}")}
    )
    entry = build_entry("note", entry_id="alpha", title="Alpha", body="Body")
    entry.metadata["status"] = "active"
    first = manager.save_entry(entry, "test", kb)
    assert first.parent == kb.path / "notes" / "active"
    entry.metadata["status"] = "done"
    second = manager.save_entry(entry, "test", kb)
    assert second.parent == kb.path / "notes" / "done"
    assert second.exists() and not first.exists()


@pytest.mark.control(reason="File-pattern types already keep their filename on rename")
def test_file_pattern_filename_stays_fixed(env):
    from pyrite.schema.field_schema import TypeSchema
    from pyrite.schema.kb_schema import KBSchema

    repo, _, kb = env
    kb._schema_cache = KBSchema(
        types={"note": TypeSchema(name="note", subdirectory="notes", file_pattern="fixed-{id}.md")}
    )
    entry = build_entry("note", entry_id="alpha", title="Alpha", body="Body")
    path = repo.save(entry)
    repo.rename("alpha", "omega")
    assert repo.find_file("omega") == path
