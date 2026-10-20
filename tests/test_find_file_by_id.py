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


# -- #704: a non-string title is indexed under its stringified slug -----------


@pytest.mark.parametrize(
    ("raw_title", "derived"),
    [("2024", "2024"), ("1e3", "1000-0"), ("true", "true")],
)
def test_704_numeric_title_is_indexed_and_findable(kb, raw_title, derived):
    kb_path, svc, repo, db = kb
    f = kb_path / f"num-{derived}.md"
    f.write_text(f"---\ntype: note\ntitle: {raw_title}\n---\n\nb\n")
    _index(svc, db)
    (row_id,) = [r["id"] for r in db.list_entries(kb_name=KB)]

    assert row_id == derived
    assert repo.id_of_file(f) == row_id
    assert repo.find_file(row_id) == f


# -- #704: the stored title says the same thing as the id ----------------------


@pytest.mark.parametrize(
    ("raw_title", "derived", "stored"),
    [
        ("true", "true", "true"),
        ("2024", "2024", "2024"),
        ("1e3", "1000-0", "1000.0"),
    ],
)
def test_704_stored_title_agrees_with_the_id(kb, raw_title, derived, stored):
    # #762 review item 1: only the slug used to be stringified, so `title: true`
    # got id `true` but the row held SQLite's reading of the raw bool ("1").
    # The load path now stringifies once, so the id, the stored title and
    # `get` all say the same thing.
    kb_path, svc, _repo, db = kb
    f = kb_path / f"num-{derived}.md"
    f.write_text(f"---\ntype: note\ntitle: {raw_title}\n---\n\nb\n")
    _index(svc, db)
    rows = db.list_entries(kb_name=KB)

    (row,) = rows
    assert row["id"] == derived
    assert row["title"] == stored


@pytest.mark.parametrize(
    ("raw_title", "stored"), [("0", "0"), ("false", "false"), ("0.0", "0.0"), ("-0", "0")]
)
def test_704_falsy_title_keeps_the_id_it_had_on_dev(kb, raw_title, stored):
    # A title YAML reads as a falsy scalar fell through the empty-title check
    # to the `entry-da39a3ee` hash on dev. Upgrading must move no id (links
    # stay valid), so the id stays; only the stored title is the stringified
    # value.
    kb_path, svc, repo, db = kb
    f = kb_path / "falsy.md"
    f.write_text(f"---\ntype: note\ntitle: {raw_title}\n---\n\nb\n")
    _index(svc, db)

    (row,) = db.list_entries(kb_name=KB)
    assert row["id"] == "entry-da39a3ee"
    assert row["title"] == stored
    assert repo.id_of_file(f) == row["id"]
    assert repo.find_file(row["id"]) == f


def test_704_unindexable_title_with_an_explicit_id_is_not_indexed(kb):
    # On dev an `id:` line skipped the title check, so a list or NaN title
    # reached the row write and crashed it (sqlite3.ProgrammingError).
    kb_path, svc, _repo, db = kb
    (kb_path / "fine.md").write_text("---\ntype: note\ntitle: Fine\n---\n\nb\n")
    for name, raw in (("lt.md", "[a, b]"), ("dt.md", "{a: 1}"), ("nan.md", ".nan")):
        (kb_path / name).write_text(f"---\ntype: note\nid: x-{name[:2]}\ntitle: {raw}\n---\n\nb\n")
    from pyrite.storage.index import IndexManager

    result = IndexManager(db, svc.config).reconcile_kb(svc.config.get_kb(KB), force=True)
    rows = {r["id"] for r in db.list_entries(kb_name=KB)}

    assert rows == {"fine"}
    # reported with the loader's reason, not a driver error naming SQL binding
    assert {m["path"].rsplit("/", 1)[1]: m["error"] for m in result["malformed"]} == {
        "lt.md": "title must be a string, got a list",
        "dt.md": "title must be a string, got a mapping",
        "nan.md": "title must be a string, got NaN",
    }


@pytest.mark.control(
    reason="dev already skips these files via the same TypeError; pins the refusal"
)
def test_704_a_title_the_index_cannot_store_is_not_indexed(kb):
    # #762 review item 2: a list, mapping or NaN title never reaches the row
    # write. The load refuses it with a named reason, so there is no row and
    # no id -- instead of a sqlite ProgrammingError traceback (#711).
    kb_path, svc, _repo, db = kb
    (kb_path / "fine.md").write_text("---\ntype: note\ntitle: Fine\n---\n\nb\n")
    for name, raw in (("lt.md", "[a, b]"), ("dt.md", "{a: 1}"), ("nan.md", ".nan")):
        (kb_path / name).write_text(f"---\ntype: note\ntitle: {raw}\n---\n\nb\n")
    _index(svc, db)
    rows = {r["id"]: r for r in db.list_entries(kb_name=KB)}

    assert set(rows) == {"fine"}
    assert all(isinstance(r["title"], str) for r in rows.values())


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


def test_704_skip_reason_is_one_line_and_kb_relative(kb, monkeypatch):
    # The reason shown by `ids missing` is the loader's message cut to its
    # first line, with this KB's absolute path made relative.
    import pyrite.storage.repository as repository_module

    kb_path, _svc, repo, _db = kb
    f = kb_path / "x.md"
    f.write_text("---\ntype: note\ntitle: X\n---\n\nb\n")

    def boom(*_a, **_k):
        raise ValueError(f"bad value in {f}\n  line 2 detail\n  line 3")

    monkeypatch.setattr(repository_module, "read_entry_id", boom)
    assert repo.read_id(f) == (None, "bad value in x.md")
