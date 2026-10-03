"""Entries are found by the id a file holds, not by its filename (ADR-0038 step 1).

One regression test per issue, on a real KB on disk with a real ``PyriteDB``
and ``KBService``:

- #483: a filename hit is returned without reading the file's id, so
  ``delete('alpha')`` removed the file holding ``beta``.
- #484: the lookup scan compares only an explicit ``id:`` (with a naive
  ``---`` split), so an id the loader derives from the title is unfindable and
  a create writes a second file for it.
- #494: delete of an id held by two files removed one of them and the row,
  leaving the other file holding the id with no row.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.exceptions import EntryExistsError, ValidationError
from pyrite.models.core_types import entry_id_from_markdown
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager
from pyrite.storage.repository import KBRepository

KB = "idkb"


@pytest.fixture
def kb(tmp_path):
    kb_path = tmp_path / KB
    kb_path.mkdir()
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name=KB, path=kb_path, kb_type="generic")],
        settings=Settings(index_path=tmp_path / "index.db", auto_embed=False),
    )
    db = PyriteDB(tmp_path / "index.db")
    svc = KBService(config, db)
    repo = KBRepository(config.get_kb(KB))
    yield kb_path, svc, repo, db
    db.close()


def _write(path: Path, *, id: str | None, title: str, extra: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    id_line = f"id: {id}\n" if id else ""
    path.write_text(f"---\n{extra}{id_line}type: note\ntitle: {title}\n---\n\nby hand\n")
    return path


# -- #483: a filename is a hint, never evidence ------------------------------


def test_483_find_file_does_not_return_a_filename_hit_holding_another_id(kb):
    kb_path, _svc, repo, _db = kb
    alpha = _write(kb_path / "alpha.md", id="beta", title="Alpha")

    assert repo.find_file("alpha") is None
    assert repo.find_file("beta") == alpha


def test_483_filename_hit_is_skipped_for_the_file_that_holds_the_id(kb):
    kb_path, _svc, repo, _db = kb
    _write(kb_path / "alpha.md", id="beta", title="Alpha")
    real = _write(kb_path / "notes" / "elsewhere.md", id="alpha", title="Whatever")

    assert repo.find_file("alpha") == real


def test_483_delete_by_a_filename_does_not_remove_another_entry(kb):
    kb_path, svc, _repo, _db = kb
    alpha = _write(kb_path / "alpha.md", id="beta", title="Alpha")

    svc.delete_entry("alpha", KB)

    assert alpha.exists(), "delete('alpha') removed the file holding 'beta'"


# -- #484: the loader's id, including a derived one, is the lookup's id -------


def test_484_a_derived_id_is_findable(kb):
    kb_path, _svc, repo, _db = kb
    beta = _write(kb_path / "beta.md", id=None, title="Alpha")

    assert repo.find_file("alpha") == beta
    assert repo.find_file("beta") is None


def test_484_create_refuses_an_id_a_file_holds_by_derivation(kb):
    kb_path, svc, _repo, _db = kb
    _write(kb_path / "alpha.md", id=None, title="Beta")

    with pytest.raises(EntryExistsError):
        svc.create(KB, {"entry_type": "note", "title": "Beta", "body": "b"})
    assert not (kb_path / "notes" / "beta.md").exists()


def test_484_scan_parses_frontmatter_not_the_first_triple_dash(kb):
    """A ``---`` inside a quoted value ended the naive split before ``id:``."""
    kb_path, _svc, repo, _db = kb
    f = _write(kb_path / "x.md", id="gamma", title="G", extra='summary: "a --- b"\n')

    assert repo.find_file("gamma") == f


def test_484_scan_skips_what_list_files_skips_and_nothing_else(kb):
    kb_path, _svc, repo, _db = kb
    drafts = _write(kb_path / "_drafts" / "x.md", id="gamma", title="G")
    # Named like their ids, so the filename fast path meets them first.
    _write(kb_path / "_templates" / "delta.md", id="delta", title="D")
    _write(kb_path / ".hidden" / "omega.md", id="omega", title="O")
    _write(kb_path / "README.md", id="README", title="Read me")

    listed = set(repo.list_files())
    assert drafts in listed
    assert repo.find_file("gamma") == drafts
    assert repo.find_file("delta") is None
    assert repo.find_file("omega") is None
    assert repo.find_file("README") is None


def test_484_the_id_is_read_after_the_kbs_schema_migrations(kb, monkeypatch):
    """The loader migrates frontmatter before deriving the id; so does lookup."""
    import pyrite.storage.repository as repo_mod
    from pyrite.migrations import MigrationRegistry

    kb_path, _svc, repo, _db = kb
    (kb_path / "kb.yaml").write_text(f"name: {KB}\ntypes:\n  note:\n    version: 2\n")
    reg = MigrationRegistry()

    @reg.register("note", 1, 2)
    def _name_to_title(data):
        data["title"] = data.pop("name")
        return data

    monkeypatch.setattr(repo_mod, "get_migration_registry", lambda: reg)
    f = kb_path / "old.md"
    f.write_text("---\ntype: note\n_schema_version: 1\nname: Old Style\n---\n\nb\n")

    assert repo.load_entry_from_file(f).id == "old-style"
    assert repo.find_file("old-style") == f


@pytest.mark.control(
    reason="entry_id_from_markdown already derived ids; this pins it to the loader"
)
def test_484_entry_id_from_markdown_is_the_loaders_id(kb):
    kb_path, _svc, repo, _db = kb
    cases = [
        _write(kb_path / "a.md", id=None, title="Some Title"),
        _write(kb_path / "b.md", id="explicit", title="Other"),
        _write(kb_path / "c.md", id="q", title="T", extra='summary: "x --- y"\n'),
    ]
    for f in cases:
        assert entry_id_from_markdown(f.read_text()) == repo.load_entry_from_file(f).id


@pytest.mark.parametrize(
    ("yaml_title", "expected_id"),
    [("2024", "2024"), ("1e3", "1000-0"), ("true", "true")],
)
@pytest.mark.control(
    reason="YAML scalar titles must remain readable and indexable when their id is derived"
)
def test_yaml_scalar_title_is_indexable_as_derived_id(kb, yaml_title, expected_id):
    kb_path, svc, repo, db = kb
    entry_file = kb_path / "numeric-title.md"
    entry_file.write_text(
        f"---\ntype: note\ntitle: {yaml_title}\n---\n\nBody\n",
        encoding="utf-8",
    )

    counts = IndexManager(db, svc.config).index_all()
    assert counts == {KB: 1}

    entry = repo.load_entry_from_file(entry_file)
    assert entry.id == expected_id
    assert repo.find_file(expected_id) == entry_file


# -- #494: delete removes every file holding the id, and only those ----------


def test_494_delete_removes_explicit_duplicates_it_meets(kb):
    """The index row's file and the filename hit both hold the id explicitly:
    both go. (A copy elsewhere in the KB is step 2's reconcile, #494.)"""
    kb_path, svc, _repo, db = kb
    svc.create(KB, {"entry_type": "note", "title": "Gamma", "body": "b"})
    created = kb_path / "notes" / "gamma.md"
    dup = _write(kb_path / "gamma.md", id="gamma", title="Alpha")
    other = _write(kb_path / "gamma-notes.md", id="delta", title="Delta")

    assert svc.delete_entry("gamma", KB)

    assert not created.exists()
    assert not dup.exists(), "a second file holding 'gamma' survived its delete"
    assert other.exists()
    assert db.get_entry("gamma", KB) is None


# -- delete is certain or refuses (cold read of #497) --------------------------


def _vault(kb_path: Path, files: dict[str, str]) -> list[Path]:
    """Obsidian-style notes: frontmatter without an ``id:`` line."""
    out = []
    for name, fm in files.items():
        f = kb_path / name
        f.write_text(f"---\n{fm}---\n\nbody of {name}\n")
        out.append(f)
    return out


def _index(svc, db) -> None:
    from pyrite.storage.index import IndexManager

    IndexManager(db, svc.config).index_kb(KB)


def test_delete_refuses_a_derived_id_two_same_title_notes_share(kb):
    kb_path, svc, _repo, db = kb
    note = "type: note\ntitle: Draft\n"
    files = _vault(kb_path, {"a.md": note, "b.md": note})
    _index(svc, db)

    with pytest.raises(ValidationError) as refused:
        svc.delete_entry("draft", KB)

    assert all(f.exists() for f in files), "delete removed a note it could not be sure of"
    assert "a.md" in str(refused.value) and "b.md" in str(refused.value)


def test_delete_refuses_the_id_untitled_notes_all_derive(kb):
    kb_path, svc, _repo, db = kb
    files = _vault(kb_path, {f"n{i}.md": "tags: [x]\n" for i in range(3)})
    _index(svc, db)
    shared = entry_id_from_markdown(files[0].read_text())
    assert shared and all(entry_id_from_markdown(f.read_text()) == shared for f in files)

    with pytest.raises(ValidationError):
        svc.delete_entry(shared, KB)

    assert all(f.exists() for f in files)


def test_delete_removes_the_one_note_that_derives_the_id(kb):
    kb_path, svc, _repo, db = kb
    (only,) = _vault(kb_path, {"beta.md": "type: note\ntitle: Alpha\n"})
    _index(svc, db)

    assert svc.delete_entry("alpha", KB)
    assert not only.exists()
    assert db.get_entry("alpha", KB) is None


# -- the id reader stringifies a scalar id exactly as the index stores it ------


@pytest.mark.parametrize(
    ("raw", "spelled"),
    # The spelling SQLite's TEXT column gave these before the shared helper,
    # so an existing index keeps its rows.
    [("true", "1"), ("123", "123"), ("1.50", "1.5"), ("2026-01-01", "2026-01-01")],
)
def test_a_scalar_id_reads_as_the_index_stores_it(kb, raw, spelled):
    kb_path, svc, repo, db = kb
    f = kb_path / "scalar.md"
    f.write_text(f"---\nid: {raw}\ntype: note\ntitle: Scalar\n---\n\nb\n")
    _index(svc, db)
    (row_id,) = [r["id"] for r in db.list_entries(kb_name=KB)]

    assert row_id == spelled
    assert repo.id_of_file(f) == row_id
    assert repo.find_file(row_id) == f


# -- a rejected filename hit says which id the file holds ----------------------


def test_not_found_names_the_file_a_filename_hit_holds(kb):
    from pyrite.exceptions import EntryNotFoundError

    kb_path, svc, _repo, _db = kb
    _vault(kb_path, {"alpha.md": "type: note\ntitle: Beta\n"})

    with pytest.raises(EntryNotFoundError) as missing:
        svc.update("alpha", KB, {"body": "x"})

    assert "alpha.md" in str(missing.value) and "'beta'" in str(missing.value)


@pytest.mark.control(
    reason="a collection was findable and deletable before; delete's new walk keeps it"
)
def test_494_delete_of_a_collection_still_removes_its_yaml(kb):
    kb_path, _svc, repo, _db = kb
    yaml_file = kb_path / "notes" / "__collection.yaml"
    yaml_file.parent.mkdir()
    yaml_file.write_text("title: Notes Collection\n")

    assert repo.find_file("collection-notes") == yaml_file
    assert repo.delete("collection-notes")
    assert not yaml_file.exists()
