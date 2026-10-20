"""One link row per link the file writes, keyed by one text form of the id
(#1009, #1022).

The property: every link a file writes is a row, so ``backlinks`` lists it and
``index health`` checks it; two links are one row only when they are the same
link: the same target as the index stores an id (``id_text``), the same
resolved KB, the same relation and the same prefix as written. The write
path's duplicate check (``KBService.add_link`` / ``add_links``) compares
targets by that same text, so a link it reports as already there is the row
the index holds.

Every expected value below is written by hand from the file's text.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager

runner = CliRunner()


def _write(path: Path, entry_id: str, body: str = "", frontmatter: str = "") -> Path:
    path.write_text(
        f"---\nid: {entry_id}\ntitle: {entry_id}\ntype: note\n{frontmatter}---\n{body}\n",
        encoding="utf-8",
    )
    return path


def _rows(db: PyriteDB, source_id: str) -> list[tuple]:
    """The source's link rows, sorted with no prefix (None) first."""
    return sorted(
        (
            (r["target_id"], r["target_kb"], r["relation"], r["target_prefix"])
            for r in db.execute_sql(
                "SELECT target_id, target_kb, relation, target_prefix FROM link "
                "WHERE source_id = :s",
                {"s": source_id},
            )
        ),
        key=lambda row: tuple("" if v is None else v for v in row),
    )


def _row_ids(db: PyriteDB, source_id: str) -> list[int]:
    return sorted(
        r["id"]
        for r in db.execute_sql("SELECT id FROM link WHERE source_id = :s", {"s": source_id})
    )


@pytest.fixture
def two_kbs(tmp_path):
    """KBs `ka` and `kb`, each with an entry `x`; `s` lives in `ka`."""
    for name in ("ka", "kb"):
        (tmp_path / name).mkdir()
        _write(tmp_path / name / "x.md", "x", body=f"x in {name}")
    config = PyriteConfig(
        knowledge_bases=[
            KBConfig(name="ka", path=tmp_path / "ka"),
            KBConfig(name="kb", path=tmp_path / "kb"),
        ],
        settings=Settings(index_path=tmp_path / "index.db"),
    )
    db = PyriteDB(config.settings.index_path)
    try:
        yield config, db, tmp_path
    finally:
        db.close()


def _sync(config, db):
    for kb in config.knowledge_bases:
        IndexManager(db, config).sync_kb(kb)


class TestEveryWrittenLinkIsARow:
    def test_two_kbs_one_target_id_both_are_backlinked(self, two_kbs):
        """The groom's probe: `[[ka:x]] and [[kb:x]]` link two different
        entries. Today the second is dropped because its bare target `x` was
        already seen, so `backlinks x -k kb` is empty."""
        config, db, root = two_kbs
        _write(root / "ka" / "s.md", "s", body="[[ka:x]] and [[kb:x]]")
        _sync(config, db)

        for kb in ("ka", "kb"):
            backlinks = db.get_backlinks("x", kb, readable_kbs=None)
            assert [(b["id"], b["kb_name"]) for b in backlinks] == [("s", "ka")], kb
        assert _rows(db, "s") == [
            ("x", "ka", "wikilink", "ka"),
            ("x", "kb", "wikilink", "kb"),
        ]

    def test_two_prefixes_of_one_target_are_two_rows_that_health_checks(self, tmp_path):
        """#1009's case: `[[ops-kb:runbook]] [[ops:runbook]]` name one entry by
        its full name and by its shortname. Both are rows, so when the
        shortname changes, health counts the one whose prefix no longer names
        the KB its row points at."""
        for name in ("dev", "ops"):
            (tmp_path / name).mkdir()
        _write(tmp_path / "ops" / "runbook.md", "runbook", body="ops body")
        _write(tmp_path / "dev" / "note.md", "note", body="[[ops-kb:runbook]] [[ops:runbook]]")
        index_path = tmp_path / "index.db"
        config = PyriteConfig(
            knowledge_bases=[
                KBConfig(name="dev-kb", path=tmp_path / "dev", shortname="dev"),
                KBConfig(name="ops-kb", path=tmp_path / "ops", shortname="ops"),
            ],
            settings=Settings(index_path=index_path),
        )
        db = PyriteDB(index_path)
        try:
            _sync(config, db)
            assert _rows(db, "note") == [
                ("runbook", "ops-kb", "wikilink", "ops"),
                ("runbook", "ops-kb", "wikilink", "ops-kb"),
            ]
            assert IndexManager(db, config).check_health()["links_stale_kb"] == 0

            renamed = PyriteConfig(
                knowledge_bases=[
                    KBConfig(name="dev-kb", path=tmp_path / "dev", shortname="dev"),
                    KBConfig(name="ops-kb", path=tmp_path / "ops", shortname="o"),
                ],
                settings=Settings(index_path=index_path),
            )
            health = IndexManager(db, renamed).check_health()
            assert health["links_stale_kb"] == 1

            # A rebuild re-reads the file: `[[ops:runbook]]` now names no KB,
            # so it is one broken row and nothing is stale.
            IndexManager(db, renamed).reconcile_kb(renamed.knowledge_bases[0], force=True)
            health = IndexManager(db, renamed).check_health()
            assert health["links_stale_kb"] == 0
            assert health["broken_links"] == 1
        finally:
            db.close()

    def test_a_typed_link_and_a_body_wikilink_are_two_rows(self, two_kbs):
        config, db, root = two_kbs
        _write(
            root / "ka" / "s.md",
            "s",
            body="See [[x]].",
            frontmatter="links:\n  - target: x\n    relation: related_to\n",
        )
        _sync(config, db)
        assert _rows(db, "s") == [
            ("x", "ka", "related_to", None),
            ("x", "ka", "wikilink", None),
        ]

    @pytest.mark.control(
        reason="already two rows before #1009: the frontmatter KB was in the sync key"
    )
    def test_two_frontmatter_kbs_for_one_target_are_two_rows(self, two_kbs):
        config, db, root = two_kbs
        _write(
            root / "ka" / "s.md",
            "s",
            frontmatter=(
                "links:\n  - target: x\n    relation: related_to\n    kb: ka\n"
                "  - target: x\n    relation: related_to\n    kb: kb\n"
            ),
        )
        _sync(config, db)
        assert _rows(db, "s") == [
            ("x", "ka", "related_to", "ka"),
            ("x", "kb", "related_to", "kb"),
        ]

    def test_a_written_prefix_and_no_prefix_are_two_rows(self, two_kbs):
        """`[[ka:x]]` and `[[x]]` in `ka` resolve alike but are spelled
        differently; each is checked on its own."""
        config, db, root = two_kbs
        _write(root / "ka" / "s.md", "s", body="[[ka:x]] [[x]]")
        _sync(config, db)
        assert _rows(db, "s") == [
            ("x", "ka", "wikilink", None),
            ("x", "ka", "wikilink", "ka"),
        ]

    def test_an_unknown_prefix_is_its_own_row(self, two_kbs):
        config, db, root = two_kbs
        _write(root / "ka" / "s.md", "s", body="[[nowhere:x]] [[x]]")
        _sync(config, db)
        assert _rows(db, "s") == [
            ("x", "ka", "wikilink", None),
            ("x", "nowhere", "wikilink", "nowhere"),
        ]

    def test_a_reference_and_a_wikilink_are_two_rows(self, two_kbs):
        config, db, root = two_kbs
        _write(root / "ka" / "s.md", "s", body="[[kb:x]]", frontmatter="references:\n  - kb:x\n")
        _sync(config, db)
        assert _rows(db, "s") == [
            ("x", "kb", "references", "kb"),
            ("x", "kb", "wikilink", "kb"),
        ]

    def test_a_transclusion_is_one_row_with_its_own_relation(self, two_kbs):
        """`![[x]]` is one written link, a transclusion. The wikilink pattern
        also matches the `[[x]]` inside it; that match is the same link, not a
        second one."""
        config, db, root = two_kbs
        _write(root / "ka" / "s.md", "s", body="![[x]]")
        _sync(config, db)
        assert _rows(db, "s") == [("x", "ka", "transclusion", None)]


class TestOneLinkIsOneRow:
    def test_identical_spellings_are_one_row(self, two_kbs):
        """The same link written twice is one link. The heading or block id
        (`note`) is not part of the key."""
        config, db, root = two_kbs
        _write(root / "ka" / "s.md", "s", body="[[x]] then [[x]] and [[x#Part]] [[kb:x]] [[kb:x]]")
        _sync(config, db)
        assert _rows(db, "s") == [
            ("x", "ka", "wikilink", None),
            ("x", "kb", "wikilink", "kb"),
        ]

    @pytest.mark.control(
        reason="dev kept the first link at extraction; guards `_link_rows` keeping it now"
    )
    def test_the_first_spelling_of_one_link_gives_the_row_its_note(self, two_kbs):
        """`[[kb:x#Part]]` then `[[kb:x]]` is one link; the row's note is the
        first one written, as extraction kept it before the key moved to the
        backend."""
        config, db, root = two_kbs
        _write(root / "ka" / "s.md", "s", body="[[kb:x#Part]] then [[kb:x]]")
        _sync(config, db)
        notes = db.execute_sql("SELECT note FROM link WHERE source_id = 's'", {})
        assert [r["note"] for r in notes] == ["#Part"]

    @pytest.mark.control(reason="code regions were already stripped; guards the extraction rewrite")
    def test_a_link_in_a_code_fence_is_not_a_link(self, two_kbs):
        config, db, root = two_kbs
        _write(root / "ka" / "s.md", "s", body="[[x]]\n\n```\n[[kb:x]]\n```\n")
        _sync(config, db)
        assert _rows(db, "s") == [("x", "ka", "wikilink", None)]

    def test_a_rebuild_leaves_every_row_in_place(self, two_kbs):
        """No churn: re-reading an unchanged file keeps the same rows, the same
        row ids, for every spelling the theme now keeps apart."""
        config, db, root = two_kbs
        _write(
            root / "ka" / "s.md",
            "s",
            body="[[x]] [[ka:x]] [[kb:x]] [[nowhere:x]] ![[kb:x]] [[x]]",
            frontmatter=(
                "links:\n  - target: x\n    relation: related_to\n"
                "  - target: x\n    relation: related_to\n    kb: kb\n"
                "references:\n  - kb:x\n"
            ),
        )
        _sync(config, db)
        rows, ids = _rows(db, "s"), _row_ids(db, "s")
        assert len(rows) == 8
        for _ in range(2):
            IndexManager(db, config).reconcile_kb(config.knowledge_bases[0], force=True)
            assert _rows(db, "s") == rows
            assert _row_ids(db, "s") == ids

    def test_many_links_in_one_file(self, two_kbs):
        config, db, root = two_kbs
        body = " ".join(f"[[t{i}]] [[kb:t{i}]]" for i in range(2000))
        _write(root / "ka" / "s.md", "s", body=body)
        _sync(config, db)
        assert len(_rows(db, "s")) == 4000


# -- #1022: the write path's duplicate check uses the index's text ------------


@pytest.fixture
def hand_file_kb(tmp_path):
    kb_path = tmp_path / "t"
    kb_path.mkdir()
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="t", path=kb_path)],
        settings=Settings(index_path=tmp_path / "index.db"),
    )
    return config, kb_path


def _patch_config(config):
    import contextlib
    from unittest.mock import patch

    @contextlib.contextmanager
    def _multi_patch():
        with patch("pyrite.cli.load_config", return_value=config):
            with patch("pyrite.cli.context.load_config", return_value=config):
                yield

    return _multi_patch()


#: (the target as the hand file writes it, the id as the CLI is given it)
_HAND_TARGETS = [
    ("true", "1"),
    ("false", "0"),
    ("2024", "2024"),
    ("1.5", "1.5"),
    ("1.2345678901234567", "1.2345678901234567"),
    ("2024-01-01", "2024-01-01"),
    ("2024-01-01 10:00:00", "2024-01-01 10:00:00"),
    # ruamel keeps the `T` in str(); the index spells a timestamp id with a
    # space (`id_text`), and so does the link key.
    ("2024-01-01T10:00:00", "2024-01-01 10:00:00"),
]


def _cases(red: set[str], skip: frozenset[str] = frozenset()) -> list:
    """`_HAND_TARGETS` with every case not in ``red`` marked a control: a
    scalar `str()` already spelled as the index does, kept beside the red
    cases so the one conversion is seen to agree with the old one there."""
    return [
        pytest.param(
            w,
            g,
            marks=()
            if w in red
            else pytest.mark.control(reason="str() already gave this scalar the index's text"),
        )
        for w, g in _HAND_TARGETS
        if w not in skip
    ]


_DUPLICATE_RED = {"true", "false", "2024-01-01T10:00:00"}


@pytest.mark.cli
@pytest.mark.parametrize(("written", "given"), _cases(_DUPLICATE_RED))
def test_link_against_a_hand_file_scalar_target_is_a_no_op(hand_file_kb, written, given):
    """`pyrite link src <id>` against a hand file whose `target:` YAML read as a
    bool, number or date answers `created: false` and leaves the file's bytes
    as they were (#1022, #927). Today `target: true` and `pyrite link src 1`
    write a second item."""
    from pyrite.cli import app

    config, kb_path = hand_file_kb
    src = _write(kb_path / "src.md", "src", frontmatter=f"links:\n  - target: {written}\n")
    before = src.read_bytes()
    with _patch_config(config):
        result = runner.invoke(app, ["link", "src", given, "-k", "t", "--format", "json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert [link["created"] for link in payload["links"]] == [False]
    assert src.read_bytes() == before


@pytest.mark.parametrize(("written", "given"), _cases(_DUPLICATE_RED))
def test_add_links_skips_a_hand_file_scalar_target(hand_file_kb, written, given):
    from pyrite.services.kb_service import KBService

    config, kb_path = hand_file_kb
    src = _write(kb_path / "src.md", "src", frontmatter=f"links:\n  - target: {written}\n")
    before = src.read_bytes()
    db = PyriteDB(config.settings.index_path)
    try:
        results = KBService(config, db).add_links("t", [{"source": "src", "target": given}])
    finally:
        db.close()
    assert results == [{"status": "skipped"}]
    assert src.read_bytes() == before


# `id: false` is no id (a falsy `id:` derives one from the title), so it has
# no entry to resolve to.
# A timestamp target was bound as `isoformat()` (`crud.bindable`, with a `T`)
# while its entry's id is `id_text` (a space): those two are red.
@pytest.mark.parametrize(
    ("written", "given"),
    _cases({"2024-01-01 10:00:00", "2024-01-01T10:00:00"}, skip=frozenset({"false"})),
)
def test_a_hand_file_scalar_target_resolves_to_the_entry_it_names(hand_file_kb, written, given):
    """The row's target is the text the index stores the entry's id as, so a
    link to `id: <scalar>` is not broken."""
    config, kb_path = hand_file_kb
    _write(kb_path / "dest.md", written)
    _write(kb_path / "src.md", "src", frontmatter=f"links:\n  - target: {written}\n")
    db = PyriteDB(config.settings.index_path)
    try:
        IndexManager(db, config).sync_kb(config.knowledge_bases[0])
        assert [r[0] for r in _rows(db, "src")] == [given]
        assert db.get_entry(given, "t") is not None
        assert IndexManager(db, config).check_health()["broken_links"] == 0
    finally:
        db.close()


def test_the_link_code_has_one_conversion_of_a_target_to_text():
    """Every place that compares or keys a link target spells it with
    `as_stored_text`; a `str(...target...)` there is a fourth conversion that
    can disagree with the index (#1022)."""
    import inspect

    from pyrite.services import kb_service
    from pyrite.storage import index
    from pyrite.storage.backends import base_backend, postgres_backend, sqlite_backend

    # The write path's comparison first: it is the conversion #1022 found.
    same_target = inspect.getsource(kb_service._same_target)
    assert not re.search(r"str\([^)]*target", same_target)
    assert "as_stored_text" in same_target
    sources = {
        "base_backend._link_rows": inspect.getsource(base_backend._link_rows),
        "sqlite_backend._sync_links": inspect.getsource(sqlite_backend.SQLiteBackend._sync_links),
        "postgres_backend._sync_links": inspect.getsource(
            postgres_backend.PostgresBackend._sync_links
        ),
        "IndexManager._entry_to_dict": inspect.getsource(index.IndexManager._entry_to_dict),
    }
    for name, source in sources.items():
        assert not re.search(r"str\([^)]*target", source), name
    assert "as_stored_text" in sources["base_backend._link_rows"]
