"""A value YAML reads as a timestamp is indexable wherever it reaches a column (#951).

The loader gives a bare YAML timestamp (``2025-06-01T00:00:00+00:00``) as
ruamel's ``TimeStamp``, a ``datetime`` subclass sqlite3's adapters do not
match (they match the exact type). So an entry carrying one in its ``date``,
in ``start_date``/``end_date``/``due_date``/``assigned_at``, in a source's
``date`` or in a tag could not be bound: ``index_all`` counted 0 for a KB
holding just that file and the only signal was a log warning.

Required property: every value the loader reads is indexable wherever a YAML
timestamp appears, and the row stores a date or timestamp as the one text the
index has always stored, ``isoformat()`` (``2025-06-01``,
``2025-06-01T00:00:00+00:00``: what ``pyrite/storage/connection.py``'s own
sqlite3 adapter writes for a plain ``datetime``), so rows written before and
after this fix sort together. A read reports what the file holds for the
entry's own columns, as the loader keeps it (``2025-06-01T00:00:00+00:00``
for a file spelled with ``T``; a ``Z`` reads back ``+00:00``), so a
read-modify-write echoes the file's own value back. A tag and a source's
date are stored and read as the index's one text (#987: a tag is a name
the index matches by text, so one spelling whatever release built the
row), and a tag a read returned finds the entry again. A value under
``metadata:`` is the index's JSON text (``SafeEncoder``), which is
``isoformat()`` too.

The oracle is the row ``PyriteDB.get_entry`` returns, or the read
``KBService.get_entry`` returns, compared with values written by hand, and
for the stored form ``datetime.isoformat()`` of the same instant.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager

ADR = """---
id: a
title: A
type: adr
adr_number: 1
status: proposed
date: {date}
---

Decision.
"""

#: A kb.yaml type declaring the temporal and assignable protocols reaches
#: every temporal column of the entry row through the metadata promotion.
THING_KB_YAML = (
    "name: t\nkb_type: generic\ntypes:\n  thing:\n    protocols: [temporal, assignable]\n"
)

THING = """---
id: t
title: T
type: thing
{key}: {value}
---

Body.
"""

SOURCES = """---
id: t
title: T
type: task
sources:
- title: A source
  date: 2026-01-01T10:00:00
- title: Spelled with a space
  date: 2026-01-02 10:00:00
---

Body.
"""

#: One tag spelled with a `T`, one with a space: the same kind of value in
#: the two spellings a file may use (#987).
TAGS = """---
id: t
title: T
type: task
tags: [alpha, 2026-01-01T10:00:00, 2026-01-02 10:00:00]
---

Body.
"""


def _config(tmp_path: Path, kb_type: str, kb_yaml: str | None = None) -> PyriteConfig:
    kb_path = tmp_path / "kb"
    kb_path.mkdir(exist_ok=True)
    if kb_yaml:
        (kb_path / "kb.yaml").write_text(kb_yaml, encoding="utf-8")
    return PyriteConfig(
        knowledge_bases=[KBConfig(name="t", path=kb_path, kb_type=kb_type)],
        settings=Settings(index_path=tmp_path / "index.db"),
    )


def _indexed(config: PyriteConfig) -> PyriteDB:
    db = PyriteDB(config.settings.index_path)
    counts = IndexManager(db, config).index_all()
    assert counts == {"t": 1}, f"the file was not indexed: {counts}"
    return db


@pytest.fixture
def software_kb(tmp_path: Path):
    """A software KB (the ADR type is software-kb's) and its one ADR path."""
    pytest.importorskip("pyrite_software_kb.entry_types")
    from pyrite.plugins.registry import PluginRegistry
    from pyrite_software_kb.plugin import SoftwareKBPlugin

    PluginRegistry().register(SoftwareKBPlugin())
    config = _config(tmp_path, "software")
    (config.knowledge_bases[0].path / "adrs").mkdir()
    return config, config.knowledge_bases[0].path / "adrs" / "a.md"


@pytest.mark.parametrize(
    ("spelled", "stored"),
    [
        ("2025-06-01T00:00:00+00:00", "2025-06-01T00:00:00+00:00"),
        ("2025-06-01 09:30:00+00:00", "2025-06-01T09:30:00+00:00"),
        ("2025-06-01T09:30:00Z", "2025-06-01T09:30:00+00:00"),
        ("2025-06-01", "2025-06-01"),
        ('"2025-06-01"', "2025-06-01"),
    ],
)
def test_an_adr_whose_date_is_a_yaml_timestamp_is_indexed(software_kb, spelled, stored):
    config, path = software_kb
    path.write_text(ADR.format(date=spelled), encoding="utf-8")

    db = _indexed(config)
    try:
        row = db.get_entry("a", "t")
    finally:
        db.close()

    assert row is not None, "index_all counted the file but no row holds it"
    assert row["date"] == stored


@pytest.mark.parametrize("key", ["date", "start_date", "end_date", "due_date", "assigned_at"])
def test_every_temporal_column_of_the_row_binds_a_yaml_timestamp(tmp_path: Path, key: str):
    config = _config(tmp_path, "generic", THING_KB_YAML)
    (config.knowledge_bases[0].path / "things").mkdir()
    (config.knowledge_bases[0].path / "things" / "t.md").write_text(
        THING.format(key=key, value="2026-01-02T10:00:00"), encoding="utf-8"
    )

    db = _indexed(config)
    try:
        row = db.get_entry("t", "t")
    finally:
        db.close()

    assert row[key] == datetime(2026, 1, 2, 10).isoformat()


def test_a_timestamp_row_keeps_the_form_the_index_always_stored(tmp_path: Path):
    """A row written for a loaded timestamp sorts with the rows the index
    already holds: a quoted ``'2026-01-02T10:00:00'`` is stored verbatim, as
    every row of a plain ``datetime`` was (``connection.py``'s adapter writes
    ``isoformat()``), and a bare timestamp an hour later must sort after it.
    A space-separated form would sort before every ``T`` row of the same day."""
    config = _config(tmp_path, "generic", THING_KB_YAML)
    things = config.knowledge_bases[0].path / "things"
    things.mkdir()
    (things / "later.md").write_text(
        THING.format(key="date", value="2026-01-02T11:00:00").replace("id: t", "id: later"),
        encoding="utf-8",
    )
    (things / "earlier.md").write_text(
        THING.format(key="date", value="'2026-01-02T10:00:00'").replace("id: t", "id: earlier"),
        encoding="utf-8",
    )

    db = PyriteDB(config.settings.index_path)
    try:
        assert IndexManager(db, config).index_all() == {"t": 2}
        assert db.get_entry("later", "t")["date"] == datetime(2026, 1, 2, 11).isoformat()
        ordered = [row["id"] for row in db.get_timeline(kb_name="t", min_importance=0)]
    finally:
        db.close()

    assert ordered == ["earlier", "later"]


def test_a_sources_date_that_is_a_yaml_timestamp_binds_and_reads_as_the_rows_text(
    tmp_path: Path,
):
    """The row binds a source's date as the index's one text (``isoformat()``,
    #987), whatever the file's spelling; a read returns that same text for
    it, so the read's source equals the row's and keeps the row's ``id``
    for the space-spelled source too (it read back ``id: None`` before)."""
    config = _config(tmp_path, "task")
    (config.knowledge_bases[0].path / "tasks").mkdir()
    (config.knowledge_bases[0].path / "tasks" / "t.md").write_text(SOURCES, encoding="utf-8")

    db = _indexed(config)
    try:
        row = db.get_entry("t", "t")
        got = KBService(config, db).get_entry("t", "t", readable_kbs=None)
    finally:
        db.close()

    stored = ["2026-01-01T10:00:00", "2026-01-02T10:00:00"]
    assert [s["date"] for s in row["sources"]] == stored
    assert [s["date"] for s in got["sources"]] == stored
    for mine, theirs in zip(got["sources"], row["sources"], strict=True):
        assert mine["id"] == theirs["id"] is not None


def test_a_tag_that_is_a_yaml_timestamp_is_stored_in_one_spelling(tmp_path: Path):
    """A tag spelled like a timestamp is stored as the index's one text
    (``isoformat()``, the form every row ever held, #987), whether the file
    writes it with a ``T`` or a space: an index built before the upgrade and
    one after hold one tag, not two. A read returns that text, so
    ``search_by_tag`` with the tag ``get`` returned finds the entry."""
    config = _config(tmp_path, "task")
    (config.knowledge_bases[0].path / "tasks").mkdir()
    (config.knowledge_bases[0].path / "tasks" / "t.md").write_text(TAGS, encoding="utf-8")

    db = _indexed(config)
    try:
        row = db.get_entry("t", "t")
        got = KBService(config, db).get_entry("t", "t", readable_kbs=None)
        found = {tag: [r["id"] for r in db.search_by_tag(tag, kb_name="t")] for tag in got["tags"]}
        by_t = [r["id"] for r in db.search_by_tag("2026-01-02T10:00:00", kb_name="t")]
    finally:
        db.close()

    assert row["tags"] == ["alpha", "2026-01-01T10:00:00", "2026-01-02T10:00:00"]
    assert got["tags"] == row["tags"]
    assert found == {tag: ["t"] for tag in row["tags"]}
    assert by_t == ["t"], "the space-spelled tag is found by the one stored spelling"


@pytest.mark.control(
    reason="the JSON column spelled a date this way before #951; the control pins the narrowed claim: a metadata value reads as the index's JSON text, not the file's spelling"
)
def test_a_metadata_timestamp_reads_as_the_indexs_json_text(tmp_path: Path):
    config = _config(tmp_path, "generic")
    (config.knowledge_bases[0].path / "notes").mkdir()
    (config.knowledge_bases[0].path / "notes" / "t.md").write_text(
        THING.format(key="seen", value="2026-01-02 10:00:00").replace("type: thing", "type: note"),
        encoding="utf-8",
    )

    db = _indexed(config)
    try:
        got = KBService(config, db).get_entry("t", "t", readable_kbs=None)
    finally:
        db.close()

    assert got["metadata"]["seen"] == "2026-01-02T10:00:00"


@pytest.mark.parametrize(
    ("spelled", "stored", "read"),
    [
        ("2026-01-02T10:00:00", "2026-01-02T10:00:00", "2026-01-02T10:00:00"),
        ("2026-01-02 10:00:00", "2026-01-02T10:00:00", "2026-01-02 10:00:00"),
        ("2026-01-02T10:00:00Z", "2026-01-02T10:00:00+00:00", "2026-01-02T10:00:00+00:00"),
    ],
)
def test_a_read_reports_the_files_own_spelling_not_the_rows(tmp_path: Path, spelled, stored, read):
    """The row holds one form; a read of the file holds the file's, as far as
    the loader keeps it (``Z`` reads back ``+00:00``), so an echo of a read
    carries the file's spelling, not the index's. For a ``T``-spelled file the
    two are equal, so a search by the value ``get`` returned finds the row."""
    config = _config(tmp_path, "generic", THING_KB_YAML)
    (config.knowledge_bases[0].path / "things").mkdir()
    (config.knowledge_bases[0].path / "things" / "t.md").write_text(
        THING.format(key="start_date", value=spelled), encoding="utf-8"
    )

    db = _indexed(config)
    try:
        assert db.get_entry("t", "t")["start_date"] == stored
        got = KBService(config, db).get_entry("t", "t", readable_kbs=None)
    finally:
        db.close()

    assert got["start_date"] == read


def test_a_date_edit_that_writes_a_timestamp_is_indexed_from_the_file(software_kb):
    """The case #730 P3 meets: an update writes a timestamp into `date:` and
    the row is built from the bytes written."""
    config, path = software_kb
    path.write_text(ADR.format(date="2025-06-01"), encoding="utf-8")
    db = _indexed(config)
    try:
        KBService(config, db).update_entry("a", "t", date=datetime(2025, 6, 2, 8, 0, tzinfo=UTC))
    finally:
        db.close()

    assert "date: 2025-06-02 08:00:00+00:00" in path.read_text(encoding="utf-8")
    db2 = _indexed(config)
    try:
        assert db2.get_entry("a", "t")["date"] == "2025-06-02T08:00:00+00:00"
    finally:
        db2.close()
