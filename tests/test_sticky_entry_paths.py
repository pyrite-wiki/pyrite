"""The public write service preserves deliberate on-disk placement."""

import pytest

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.exceptions import KBReadOnlyError
from pyrite.models.factory import build_entry
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager
from pyrite.storage.repository import KBRepository


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "kb"
    root.mkdir()
    kb = KBConfig(name="test", path=root)
    config = PyriteConfig(
        knowledge_bases=[kb],
        settings=Settings(index_path=tmp_path / "index.db", auto_embed=False),
    )
    db = PyriteDB(config.settings.index_path)
    yield KBRepository(kb), KBService(config, db), kb
    db.close()


def _index(service):
    IndexManager(service.db, service.config).index_all()


def test_update_keeps_root_file(env):
    repo, service, kb = env
    path = repo.save(build_entry("note", entry_id="alpha", title="Alpha", body="Body"), subdir="")
    _index(service)
    result = service.update("alpha", "test", {"title": "Changed"})
    assert result.entry.file_path == path
    assert path.exists()
    assert list(kb.path.rglob("*.md")) == [path]


@pytest.mark.parametrize("subdir", ["", "notes", "hand/made"])
@pytest.mark.parametrize("after", ["update", "delete"])
def test_rename_keeps_filename_and_new_id_is_addressable(env, subdir, after):
    repo, service, kb = env
    path = repo.save(
        build_entry("note", entry_id="alpha", title="Alpha", body="Body"), subdir=subdir
    )
    _index(service)
    result = service.rename_entry("alpha", "omega", "test")
    assert result["index_verified"] is True
    assert path.exists()
    assert repo.find_file("omega") == path
    assert repo.load("omega").body == "Body"
    assert repo.find_file("alpha") is None
    assert service.db.get_entry("omega", "test") is not None
    assert service.db.get_entry("alpha", "test") is None
    if after == "update":
        assert service.update("omega", "test", {"title": "Changed"}).entry.file_path == path
        assert list(kb.path.rglob("*.md")) == [path]
    else:
        assert service.delete_entry("omega", "test")
        assert not path.exists()


@pytest.mark.control(reason="Templated folders still move when their folder fields change")
def test_template_folder_move_remains_supported(env):
    from pyrite.schema.field_schema import TypeSchema
    from pyrite.schema.kb_schema import KBSchema

    repo, service, kb = env
    kb._schema_cache = KBSchema(
        types={"note": TypeSchema(name="note", subdirectory="notes/{status}")}
    )
    entry = build_entry("note", entry_id="alpha", title="Alpha", body="Body")
    entry.metadata["status"] = "active"
    path = repo.save(entry)
    _index(service)
    assert path.parent == kb.path / "notes" / "active"
    moved = service.update("alpha", "test", {"status": "done"}).entry.file_path
    assert moved.parent == kb.path / "notes" / "done"
    assert moved.exists() and not path.exists()


@pytest.mark.control(reason="File-pattern types already keep their filename on rename")
def test_file_pattern_filename_stays_fixed(env):
    from pyrite.schema.field_schema import TypeSchema
    from pyrite.schema.kb_schema import KBSchema

    repo, service, kb = env
    kb._schema_cache = KBSchema(
        types={"note": TypeSchema(name="note", subdirectory="notes", file_pattern="fixed-{id}.md")}
    )
    path = repo.save(build_entry("note", entry_id="alpha", title="Alpha", body="Body"))
    _index(service)
    service.rename_entry("alpha", "omega", "test")
    assert repo.find_file("omega") == path


@pytest.mark.control(reason="A read-only KB refuses the rename without changing the file")
def test_read_only_rename_changes_nothing(env):
    repo, service, kb = env
    path = repo.save(build_entry("note", entry_id="alpha", title="Alpha", body="Body"), subdir="")
    _index(service)
    before = path.read_bytes()
    kb.read_only = True
    with pytest.raises(KBReadOnlyError):
        service.rename_entry("alpha", "omega", "test")
    assert path.read_bytes() == before
    assert service.db.get_entry("alpha", "test") is not None
    assert service.db.get_entry("omega", "test") is None
