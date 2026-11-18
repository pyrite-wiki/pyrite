"""A Markdown link to an entry file is a link row, like a wikilink (#1723).

The property (the groom of #1723, option C, with the maintainer's decisions):

* every in-KB Markdown link from an entry body to an entry file is exactly one
  ``link`` row, relation ``markdown_link`` (D2), whose ``target_path`` is the
  KB-relative path it names (symbolic links followed, #1262) and whose
  ``target_id`` is the id of the entry indexed at that path *now*, whatever
  order the writes came in;
* an in-KB ``.md`` link with nothing at its path is a row whose ``target_id``
  is the path text, so every broken-link reader reports it (D1);
* a link that leaves the KB, a URL, a fragment-only link, an image, a link in
  code, a self-link and a link to a file the walk does not list give no row;
* an index from before this change gets the rows from one plain ``index sync``
  that reports nothing changed (D3 a).

Every expected value is written by hand from the files' text. The entries'
ids differ from their file names on purpose: a row that guessed the id from
the name would be wrong.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.services.access_policy import UNSCOPED
from pyrite.services.qa_service import QAService
from pyrite.services.wikilink_service import WikilinkService
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager

KB = "demo"
needs_symlinks = pytest.mark.skipif(os.name == "nt", reason="symlinks are not reliable on Windows")


def _note(entry_id: str, body: str = "") -> str:
    return f"---\nid: {entry_id}\ntitle: {entry_id}\ntype: note\n---\n{body}\n"


class World:
    """A KB on disk, its index, and the indexer, all under ``tmp_path``."""

    def __init__(self, tmp_path: Path, name: str = "index.db"):
        self.tmp = tmp_path
        self.root = tmp_path / "kb"
        self.root.mkdir(exist_ok=True)
        self.path = tmp_path / name
        self.config = PyriteConfig(
            knowledge_bases=[KBConfig(name=KB, path=self.root, kb_type="generic")],
            settings=Settings(index_path=self.path, auto_embed=False),
        )
        self.db = PyriteDB(self.path)
        self.im = IndexManager(self.db, self.config)

    def write(self, rel: str, text: str) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def md_rows(self, source_id: str | None = None) -> list[tuple]:
        """The Markdown link rows: (source, target_id, target_path, note)."""
        sql = (
            "SELECT source_id, target_id, target_path, note FROM link "
            "WHERE relation = 'markdown_link'"
        )
        params = {}
        if source_id is not None:
            sql += " AND source_id = :s"
            params["s"] = source_id
        return sorted(
            (r["source_id"], r["target_id"], r["target_path"], r["note"])
            for r in self.db.execute_sql(sql, params)
        )

    def md_count(self, source_id: str) -> int:
        """How many Markdown link rows the source has (columns every build has)."""
        return self.db.execute_sql(
            "SELECT COUNT(*) AS n FROM link WHERE relation = 'markdown_link' AND source_id = :s",
            {"s": source_id},
        )[0]["n"]

    def close(self) -> None:
        self.db.close()


@pytest.fixture
def world(tmp_path):
    w = World(tmp_path)
    yield w
    w.close()


def _targets(world: World) -> None:
    """The entry files the shapes link to; no id equals its file name."""
    world.write("a.md", _note("alpha", "A."))
    world.write("notes/b.md", _note("beta", "B."))
    world.write("notes/sp ace.md", _note("spaced", "S."))
    world.write("README.md", "# Not an entry\n")
    world.write("_templates/t.md", _note("templ"))
    world.write(".hidden/h.md", _note("hid"))
    (world.root / "pic.png").write_bytes(b"\x89PNG")
    (world.tmp / "outside.md").write_text(_note("outsider"), encoding="utf-8")


# (source file, body, expected rows of the source as (target_id, target_path, note))
ROW_SHAPES = [
    pytest.param("src.md", "[t](a.md)", [("alpha", "a.md", "")], id="same-folder"),
    pytest.param("src.md", "[t](./a.md)", [("alpha", "a.md", "")], id="dot-slash"),
    pytest.param("src.md", "[t](notes/b.md)", [("beta", "notes/b.md", "")], id="nested"),
    pytest.param("sub/src.md", "[t](../a.md)", [("alpha", "a.md", "")], id="parent"),
    pytest.param(
        "sub/src.md", "[t](../notes/../notes/b.md)", [("beta", "notes/b.md", "")], id="dotdot-path"
    ),
    pytest.param("src.md", "[t](a.md#Part)", [("alpha", "a.md", "#Part")], id="fragment"),
    pytest.param("src.md", "[t](a)", [("alpha", "a.md", "")], id="bare-resolves"),
    pytest.param("src.md", "[t](notes/b#H)", [("beta", "notes/b.md", "#H")], id="bare-fragment"),
    pytest.param(
        "src.md", "[t](<notes/sp ace.md>)", [("spaced", "notes/sp ace.md", "")], id="angle"
    ),
    pytest.param(
        "src.md", "[t](notes/sp%20ace.md)", [("spaced", "notes/sp ace.md", "")], id="percent"
    ),
    pytest.param("src.md", '[t](a.md "A title")', [("alpha", "a.md", "")], id="title"),
    pytest.param("src.md", "[t](a.md 'A title')", [("alpha", "a.md", "")], id="title-single"),
    pytest.param("src.md", "[![i](pic.png)](a.md)", [("alpha", "a.md", "")], id="linked-image"),
    pytest.param("src.md", "[t][r]\n\n[r]: a.md\n", [("alpha", "a.md", "")], id="ref-full"),
    pytest.param("src.md", "[R][]\n\n[r]: a.md\n", [("alpha", "a.md", "")], id="ref-collapsed"),
    pytest.param("src.md", "See [r].\n\n[r]: <a.md>\n", [("alpha", "a.md", "")], id="ref-shortcut"),
    pytest.param(
        "src.md", "[t](missing.md)", [("missing.md", "missing.md", "")], id="broken-same-folder"
    ),
    pytest.param(
        "sub/src.md",
        "[t](gone/x.md#h)",
        [("sub/gone/x.md", "sub/gone/x.md", "#h")],
        id="broken-nested",
    ),
    pytest.param(
        "src.md",
        "[t](a.md) and [u](missing.md)",
        [("alpha", "a.md", ""), ("missing.md", "missing.md", "")],
        id="two-links",
    ),
]

# (source file, body): no Markdown link row.
NO_ROW_SHAPES = [
    pytest.param("src.md", "[t](https://example.com/a.md)", id="url"),
    pytest.param("src.md", "[t](mailto:x@example.com)", id="mailto"),
    pytest.param("src.md", "[t](//example.com/a.md)", id="scheme-relative"),
    pytest.param("src.md", "[t](#Part)", id="fragment-only"),
    pytest.param("src.md", "[t](/a.md)", id="root-relative"),
    pytest.param("src.md", "[t](../outside.md)", id="leaves-kb"),
    pytest.param("sub/src.md", "[t](../../outside.md)", id="leaves-kb-nested"),
    pytest.param("src.md", "![i](a.md)", id="image"),
    pytest.param("src.md", "```\n[t](a.md)\n```", id="fenced-code"),
    pytest.param("src.md", "Write `[t](a.md)` to link.", id="inline-code"),
    pytest.param("src.md", "[top](src.md#x)", id="self"),
    pytest.param("src.md", "[top](src)", id="self-bare"),
    pytest.param("src.md", "[t](README.md)", id="readme"),
    pytest.param("src.md", "[t](_templates/t.md)", id="templates"),
    pytest.param("src.md", "[t](.hidden/h.md)", id="hidden-folder"),
    pytest.param("src.md", "[t](pic.png)", id="other-suffix"),
    pytest.param("src.md", "[t](nofile)", id="bare-no-file"),
    pytest.param("src.md", "[t](notes/)", id="folder"),
    pytest.param("src.md", "[t][nope]\n\n[r]: a.md\n", id="ref-undefined"),
    pytest.param("src.md", "[r]: a.md\n", id="definition-unused"),
    pytest.param("src.md", "[[alpha]]", id="wikilink-only"),
    pytest.param("src.md", "", id="empty-body"),
]


@pytest.mark.parametrize(("source", "body", "expected"), ROW_SHAPES)
def test_a_markdown_link_to_an_entry_file_is_one_row(world, source, body, expected):
    _targets(world)
    world.write(source, _note("src", body))
    world.im.index_all()
    assert world.md_rows("src") == [("src", *row) for row in expected]


_NO_ROW_CONTROL = (
    "guards the parser against matching too much: no build before #1723 makes a "
    "Markdown link row, so these pass there and must keep passing"
)


@pytest.mark.control(reason=_NO_ROW_CONTROL)
@pytest.mark.parametrize(("source", "body"), NO_ROW_SHAPES)
def test_a_link_that_is_not_to_an_in_kb_entry_file_is_no_row(world, source, body):
    _targets(world)
    world.write(source, _note("src", body))
    world.im.index_all()
    assert world.db.get_entry("src", KB) is not None  # indexed, with no such row
    assert world.md_count("src") == 0


def test_a_wikilink_and_a_markdown_link_to_one_entry_are_two_rows(world):
    _targets(world)
    world.write("src.md", _note("src", "[[alpha]] and [t](a.md)"))
    world.im.index_all()
    rows = sorted(
        (r["relation"], r["target_id"], r["inverse_relation"])
        for r in world.db.execute_sql("SELECT * FROM link WHERE source_id = 'src'")
    )
    assert rows == [
        ("markdown_link", "alpha", "markdown_linked_by"),
        ("wikilink", "alpha", "wikilinked_by"),
    ]


def test_rereading_an_unchanged_link_rewrites_no_row(world):
    """The row's identity is its path, not the id it resolved to: a re-read of
    the source keeps the same rows (SQLite's diff by key, #927), it does not
    delete and re-insert them because `target_id` was resolved."""
    _targets(world)
    world.write("src.md", _note("src", "[t](a.md) [u](missing.md)"))
    world.im.index_all()
    ids = sorted(r["id"] for r in world.db.execute_sql("SELECT id FROM link"))
    world.im.index_all()  # a rebuild re-reads and rewrites every file
    assert sorted(r["id"] for r in world.db.execute_sql("SELECT id FROM link")) == ids
    assert world.md_rows("src") == [
        ("src", "alpha", "a.md", ""),
        ("src", "missing.md", "missing.md", ""),
    ]


def test_two_spellings_of_one_path_are_one_row(world):
    _targets(world)
    world.write("src.md", _note("src", "[t](a.md) [u](./a.md#X) [v](a)"))
    world.im.index_all()
    # The first link with the key gives the row its note, as for [[x]] (#1009).
    assert world.md_rows("src") == [("src", "alpha", "a.md", "")]


@needs_symlinks
def test_a_link_through_an_in_kb_symlink_names_the_target_file(world):
    _targets(world)
    os.symlink("a.md", world.root / "alias.md")
    world.write("src.md", _note("src", "[t](alias.md)"))
    world.im.index_all()
    assert world.md_rows("src") == [("src", "alpha", "a.md", "")]


@needs_symlinks
def test_a_link_through_a_dangling_symlink_is_broken_at_the_missing_name(world):
    _targets(world)
    os.symlink("nothing.md", world.root / "dang.md")
    world.write("src.md", _note("src", "[t](dang.md)"))
    world.im.index_all()
    assert world.md_rows("src") == [("src", "nothing.md", "nothing.md", "")]


@needs_symlinks
@pytest.mark.control(reason=_NO_ROW_CONTROL)
def test_a_link_through_a_symlinked_folder_that_leaves_the_kb_is_no_row(world):
    _targets(world)
    (world.tmp / "elsewhere").mkdir()
    (world.tmp / "elsewhere" / "x.md").write_text(_note("x"), encoding="utf-8")
    os.symlink(world.tmp / "elsewhere", world.root / "linkout")
    world.write("src.md", _note("src", "[t](linkout/x.md)"))
    world.im.index_all()
    assert world.db.get_entry("src", KB) is not None
    assert world.md_count("src") == 0


@pytest.mark.control(
    reason="the invariant: wikilink, transclusion and frontmatter rows are the rows dev makes"
)
def test_wikilink_transclusion_and_frontmatter_rows_are_unchanged(world):
    """The rows the other link kinds make, written by hand: a body that also
    carries Markdown links adds rows and changes none of these."""
    _targets(world)
    world.write(
        "src.md",
        "---\nid: src\ntitle: src\ntype: note\nlinks:\n  - target: beta\n    relation: related_to\n"
        "references: [spaced]\n---\n[[alpha#H]] ![[beta]] [t](a.md) [u](missing.md)\n",
    )
    world.im.index_all()
    rows = sorted(
        (
            r["target_id"],
            r["target_kb"],
            r["relation"],
            r["inverse_relation"],
            r["target_prefix"],
            r.get("target_path"),
            r["note"],
        )
        for r in world.db.execute_sql(
            "SELECT * FROM link WHERE source_id = 'src' AND relation != 'markdown_link'"
        )
    )
    assert rows == [
        ("alpha", KB, "wikilink", "wikilinked_by", None, None, "#H"),
        ("beta", KB, "related_to", "related_to", None, None, ""),
        ("beta", KB, "transclusion", "transcluded_by", None, None, ""),
        ("spaced", KB, "references", "referenced_by", None, None, ""),
    ]


# ---------------------------------------------------------------------------
# Every reader sees the rows through target_id, unchanged
# ---------------------------------------------------------------------------


def test_backlinks_links_check_health_and_qa_agree(world):
    _targets(world)
    world.write("src.md", _note("src", "[t](a.md) [u](notes/missing.md)"))
    world.im.index_all()

    backlinks = world.db.get_backlinks("alpha", KB, readable_kbs=UNSCOPED)
    assert [(b["id"], b["forward_relation"], b["relation"]) for b in backlinks] == [
        ("src", "markdown_link", "markdown_linked_by")
    ]

    missing = WikilinkService(world.config, world.db).check_links(kb_name=KB)
    assert [(m["target_id"], m["ref_count"]) for m in missing] == [("notes/missing.md", 1)]

    assert world.im.check_health(KB)["broken_links"] == 1

    issues = QAService(world.config, world.db).validate_kb(KB, readable_kbs=UNSCOPED)["issues"]
    broken = [(i["entry_id"], i["rule"]) for i in issues if i["rule"] == "broken_link"]
    assert broken == [("src", "broken_link")]


# ---------------------------------------------------------------------------
# Order of writes: the row names the entry at the path now
# ---------------------------------------------------------------------------


def _snapshot_links(path: Path) -> list[tuple]:
    with sqlite3.connect(path) as conn:
        return sorted(
            conn.execute(
                "SELECT source_id, source_kb, target_id, target_kb, target_prefix, target_path,"
                " relation, inverse_relation, note FROM link"
            ).fetchall(),
            key=repr,
        )


def _rebuild_links(world: World, name: str) -> list[tuple]:
    fresh = World(world.tmp, name)
    try:
        fresh.im.index_all()
        return _snapshot_links(fresh.path)
    finally:
        fresh.close()


def test_the_row_follows_the_entry_at_the_path_through_file_edits(world):
    """Each step is a hand edit and a plain ``index sync``; after each the row
    names what is at ``a.md`` now, and a rebuild from the files agrees."""
    world.write("sub/b.md", _note("b", "See [a](../a.md#Part)."))
    world.im.index_all()
    steps = [
        ("source before target", lambda: None, "a.md"),
        ("target created", lambda: world.write("a.md", _note("a")), "a"),
        ("target's id renamed", lambda: world.write("a.md", _note("a2")), "a2"),
        ("target moved", lambda: (world.root / "a.md").rename(world.root / "moved.md"), "a.md"),
        ("a new entry at the path", lambda: world.write("a.md", _note("fresh")), "fresh"),
        ("target deleted", lambda: (world.root / "a.md").unlink(), "a.md"),
    ]
    for n, (name, act, target_id) in enumerate(steps):
        act()
        world.im.sync_incremental(KB)
        assert world.md_rows() == [("b", target_id, "a.md", "#Part")], name
        assert _snapshot_links(world.path) == _rebuild_links(world, f"fresh{n}.db"), name


def test_the_row_follows_the_entry_through_service_writes(world):
    """The same through Pyrite's own writes: create, rename (the id changes,
    the file stays, #1355) and delete."""
    from pyrite.services.kb_service import KBService

    world.im.index_all()
    service = KBService(world.config, world.db)
    # A note is written under `notes/`; the link is relative to the linker's
    # own folder, so it names `notes/target.md`.
    service.create_entry(KB, "linker", "Linker", "note", body="See [t](target.md).")
    assert (world.root / "notes" / "linker.md").exists()
    assert world.md_rows() == [("linker", "notes/target.md", "notes/target.md", "")]
    service.create_entry(KB, "target", "Target", "note", body="T.")
    assert (world.root / "notes" / "target.md").exists()
    assert world.md_rows() == [("linker", "target", "notes/target.md", "")]
    service.rename_entry("target", "renamed", KB)
    assert (world.root / "notes" / "target.md").exists()
    assert world.md_rows() == [("linker", "renamed", "notes/target.md", "")]
    assert _snapshot_links(world.path) == _rebuild_links(world, "fresh-a.db")
    service.delete_entry("renamed", KB, readable_kbs=None)
    assert world.md_rows() == [("linker", "notes/target.md", "notes/target.md", "")]
    assert _snapshot_links(world.path) == _rebuild_links(world, "fresh-b.db")


def test_a_link_to_the_losing_file_of_a_duplicate_id_is_broken(world):
    """Two files claim one id; the row is the winner's file. A link to the
    other file names no indexed entry, so it is broken, not the winner."""
    world.write("a.md", _note("dup", "first"))
    world.write("b.md", _note("dup", "second"))
    world.write("src.md", _note("src", "[a](a.md) [b](b.md)"))
    world.im.index_all()
    winner = world.db.execute_sql("SELECT file_path FROM entry WHERE id = 'dup'")[0]["file_path"]
    won, lost = ("a.md", "b.md") if winner.endswith("a.md") else ("b.md", "a.md")
    assert world.md_rows("src") == sorted([("src", "dup", won, ""), ("src", lost, lost, "")])


def test_an_upsert_from_a_read_leaves_the_markdown_rows_as_they_are(world):
    """A row written back from a read (``dedup``: ``upsert_entry(get_entry())``)
    carries no root: the source's Markdown rows are neither dropped nor turned
    into id rows; only a write from the file re-derives them."""
    _targets(world)
    world.write("src.md", _note("src", "[t](a.md) [u](missing.md)"))
    world.im.index_all()
    before = world.md_rows()
    world.db.upsert_entry(world.db.get_entry("src", KB))
    assert (
        world.md_rows()
        == before
        == [
            ("src", "alpha", "a.md", ""),
            ("src", "missing.md", "missing.md", ""),
        ]
    )


# ---------------------------------------------------------------------------
# Upgrade: an index from before this change gets the rows from a plain sync
# ---------------------------------------------------------------------------


def test_an_index_from_v0_26_0_gains_the_rows_from_one_plain_sync(tmp_path):
    from pyrite.storage.index import content_hash

    shapes = Path(__file__).parent / "fixtures" / "index-shapes"
    root = tmp_path / "kb"
    root.mkdir()
    a = root / "a.md"
    a.write_text(_note("alpha", "A."), encoding="utf-8")
    b = root / "b.md"
    b.write_text(_note("beta", "See [a](a.md)."), encoding="utf-8")
    path = tmp_path / "index.db"
    # The rows a v0.26.0 build (schema 29) wrote for these files: their stat
    # and hash recorded, and no Markdown link row, since it made none.
    conn = sqlite3.connect(path)
    conn.executescript((shapes / "v0.26.0.sql").read_text())
    conn.execute("INSERT INTO kb (name, kb_type, path) VALUES (?, 'generic', ?)", (KB, str(root)))
    for entry_id, f in (("alpha", a), ("beta", b)):
        st = f.stat()
        conn.execute(
            "INSERT INTO entry (id, kb_name, entry_type, title, body, file_path, content_hash,"
            " file_mtime_ns, file_size) VALUES (?, ?, 'note', ?, '', ?, ?, ?, ?)",
            (
                entry_id,
                KB,
                entry_id,
                str(f),
                content_hash(f.read_bytes()),
                st.st_mtime_ns,
                st.st_size,
            ),
        )
    conn.commit()
    conn.close()

    config = PyriteConfig(
        knowledge_bases=[KBConfig(name=KB, path=root, kb_type="generic")],
        settings=Settings(index_path=path, auto_embed=False),
    )
    db = PyriteDB(path)
    try:
        im = IndexManager(db, config)
        # The open marked every entry for one re-read, and health says so.
        stale = sorted(e["id"] for e in im.check_health(KB)["stale_entries"])
        result = im.sync_incremental(KB)
        stale_after = im.check_health(KB)["stale_entries"]
        rows = [
            (r["source_id"], r["target_id"], r["target_path"])
            for r in db.execute_sql("SELECT * FROM link WHERE relation = 'markdown_link'")
        ]
    finally:
        db.close()
    assert rows == [("beta", "alpha", "a.md")]
    assert stale == ["alpha", "beta"] and stale_after == []
    # Nothing changed in the files, and the sync says so.
    assert (result["added"], result["updated"], result["removed"]) == (0, 0, 0)


# ---------------------------------------------------------------------------
# The surfaces users read: the CLI, as docs/links.md shows it
# ---------------------------------------------------------------------------


def test_the_cli_shows_a_markdown_link_in_backlinks_and_links_check(cli_world):
    """`pyrite create` with a Markdown link, then `backlinks` and `links check`:
    the docs/links.md walkthrough, through the real CLI."""
    import json

    world = cli_world()
    for title, body in (
        ("Beta", "A claim"),
        ("Gamma", "See [Beta](beta.md#Claim) and [x](nope.md)"),
    ):
        result = world.cli(
            "create", "-k", "test-kb", "-t", "note", "--title", title, "--body", body
        )
        assert result.exit_code == 0, result.output

    backlinks = world.cli("backlinks", "beta", "-k", "test-kb")
    assert backlinks.exit_code == 0, backlinks.output
    rows = [(r["id"], r["forward_relation"]) for r in json.loads(backlinks.stdout)["entries"]]
    assert rows == [("gamma", "markdown_link")]

    check = world.cli("links", "check", "-k", "test-kb")
    assert check.exit_code == 1, check.output  # a broken link is a finding (D1)
    found = json.loads(check.stdout)
    # The note is written under notes/, so the broken path is notes/nope.md.
    assert [t["target_id"] for t in found["targets"]] == ["notes/nope.md"]


# ---------------------------------------------------------------------------
# Bare links: KBFiles.resolved decides before KBFiles.present does
# ---------------------------------------------------------------------------


@needs_symlinks
@pytest.mark.control(
    reason="no build before #1723 makes a Markdown link row; guards that present(), "
    "which answers True for a link out, never decides a bare link before resolved()"
)
def test_a_bare_link_whose_md_file_links_out_of_the_kb_is_no_row(world):
    _targets(world)
    os.symlink(world.tmp / "outside.md", world.root / "out.md")
    world.write("src.md", _note("src", "[t](out) and [u](a)"))
    world.im.index_all()
    assert world.db.get_entry("src", KB) is not None
    # Only the in-KB bare link is a row; `out.md` leads outside.
    assert [
        r["target_id"]
        for r in world.db.execute_sql(
            "SELECT target_id FROM link WHERE relation = 'markdown_link' AND source_id = 'src'"
        )
    ] == ["alpha"]


def test_a_bare_link_that_cannot_be_checked_drops_only_that_link(world, monkeypatch):
    """Where the OS will not say whether `<path>.md` is there
    (`KBPresenceUnknownError`), that one link gives no row: the sync goes on,
    and the entry's other rows are written as usual."""
    from pyrite.utils.kb_files import KBFiles, KBPresenceUnknownError

    real_present = KBFiles.present

    def present(self, path):
        if Path(path).name == "a.md":
            raise KBPresenceUnknownError()
        return real_present(self, path)

    monkeypatch.setattr(KBFiles, "present", present)
    _targets(world)
    world.write("src.md", _note("src", "[t](a) [u](notes/b.md) [v](missing.md) [[alpha]]"))
    world.write("other.md", _note("other", "[w](notes/b.md)"))
    world.im.index_all()
    assert world.md_rows() == [
        ("other", "beta", "notes/b.md", ""),
        ("src", "beta", "notes/b.md", ""),
        ("src", "missing.md", "missing.md", ""),
    ]
    wikilinks = world.db.execute_sql(
        "SELECT target_id FROM link WHERE source_id = 'src' AND relation = 'wikilink'"
    )
    assert [r["target_id"] for r in wikilinks] == ["alpha"]
