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


@pytest.mark.control(reason="the update half was fixed in #955; this guards it from regressing")
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
        assert service.delete_entry("omega", "test", readable_kbs=None)
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


# -- A rename frees the old id (ADR-0038 I6 with create's exists check) ------
#
# Under sticky paths a file's name no longer says which id it holds, so an id's
# default path (`<id>.md`) can be held by another id. Ways that happens:
#   1. rename a -> b: `a.md` holds `b`;
#   2. a rename chain, a -> b then c -> a: `a.md` holds `b`, `c.md` holds `a`,
#      and creating `c` meets the second;
#   3. a hand edit of `id:` in `alpha.md`, then `index sync`;
#   4. a hand-written or externally written file named for one id, holding
#      another;
#   5. a `file_pattern` filename built from fields, where a fresh name would
#      leave the pattern (the existing #391 refusal stands, naming the holder).
# One rule covers 1-4: a create whose default path is held by a different id
# gets a fresh filename (`<id>-2.md`, ...); the path is a location, never
# evidence (ADR-0038 decision 1). Case 5 refuses, naming the holder's id.


def _paths(kb):
    return sorted(str(p.relative_to(kb.path)) for p in kb.path.rglob("*.md"))


def test_a_rename_frees_the_old_id_for_a_create(env):
    repo, service, kb = env
    old = repo.save(build_entry("note", entry_id="alpha", title="Alpha", body="Body"))
    _index(service)
    service.rename_entry("alpha", "omega", "test")

    created = service.create_entry("test", "alpha", "Alpha again", "note", body="new")

    assert created.id == "alpha"
    assert repo.find_file("omega") == old and repo.find_file("alpha") != old
    assert repo.load("alpha").body.strip() == "new" and repo.load("omega").body.strip() == "Body"
    assert service.update("alpha", "test", {"title": "Alpha 2"}).entry.title == "Alpha 2"
    assert len(_paths(kb)) == 2


def test_a_rename_chain_frees_both_ids(env):
    repo, service, kb = env
    repo.save(build_entry("note", entry_id="a", title="A", body="1"))
    repo.save(build_entry("note", entry_id="c", title="C", body="2"))
    _index(service)
    service.rename_entry("a", "b", "test")  # a.md holds b
    service.rename_entry("c", "a", "test")  # c.md holds a

    service.create_entry("test", "c", "C again", "note", body="3")

    assert {i: repo.load(i).body.strip() for i in ("a", "b", "c")} == {"a": "2", "b": "1", "c": "3"}
    assert len(_paths(kb)) == 3


def test_a_hand_edited_id_frees_the_old_id_for_a_create(env):
    repo, service, kb = env
    path = repo.save(build_entry("note", entry_id="alpha", title="Alpha", body="Body"))
    _index(service)
    path.write_text(path.read_text(encoding="utf-8").replace("id: alpha", "id: zeta"), "utf-8")
    IndexManager(service.db, service.config).sync_incremental("test")

    service.create_entry("test", "alpha", "Alpha again", "note", body="new")

    assert repo.find_file("zeta") == path
    assert repo.load("alpha").body.strip() == "new"


def test_a_file_named_for_one_id_holding_another_does_not_block_a_create(env):
    repo, service, kb = env
    (kb.path / "notes").mkdir()
    (kb.path / "notes" / "x.md").write_text(
        "---\nid: y\ntitle: Y\ntype: note\n---\n\nbody\n", "utf-8"
    )
    _index(service)

    service.create_entry("test", "x", "X", "note", body="new")

    assert repo.load("y").body.strip() == "body" and repo.load("x").body.strip() == "new"


def test_a_taken_file_pattern_name_is_refused_naming_the_holder(env):
    from pyrite.exceptions import EntryExistsError
    from pyrite.schema.field_schema import TypeSchema
    from pyrite.schema.kb_schema import KBSchema

    repo, service, kb = env
    kb._schema_cache = KBSchema(
        types={"note": TypeSchema(name="note", subdirectory="notes", file_pattern="fixed-{id}.md")}
    )
    repo.save(build_entry("note", entry_id="alpha", title="Alpha", body="Body"))
    _index(service)
    service.rename_entry("alpha", "omega", "test")

    with pytest.raises(EntryExistsError, match="holds 'omega'"):
        service.create_entry("test", "alpha", "Alpha again", "note", body="new")
