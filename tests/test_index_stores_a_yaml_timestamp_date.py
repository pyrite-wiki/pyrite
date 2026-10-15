"""A value YAML reads as a timestamp is indexable wherever it reaches a column (#951).

The loader gives a bare YAML timestamp (``2025-06-01T00:00:00+00:00``) as
ruamel's ``TimeStamp``, a ``datetime`` subclass sqlite3's adapters do not
match (they match the exact type). So an entry carrying one in its ``date``,
in ``start_date``/``end_date``/``due_date``/``assigned_at``, in a source's
``date`` or in a tag could not be bound: ``index_all`` counted 0 for a KB
holding just that file and the only signal was a log warning.

Required property: every value the loader reads is indexable wherever a YAML
timestamp appears, and the row stores a date or timestamp as one text form
(``2025-06-01``, ``2025-06-01 00:00:00+00:00``). A read reports what the file
holds: the loader's spelling (``2025-06-01T00:00:00+00:00`` for a file spelled
with ``T``), never the row's one form, so a read-modify-write echoes the file's
own spelling back.

The oracle is the row ``PyriteDB.get_entry`` returns, or the read
``KBService.get_entry`` returns, compared with values written by hand.
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
---

Body.
"""

TAGS = """---
id: t
title: T
type: task
tags: [alpha, 2026-01-01T10:00:00]
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
        ("2025-06-01T00:00:00+00:00", "2025-06-01 00:00:00+00:00"),
        ("2025-06-01 09:30:00+00:00", "2025-06-01 09:30:00+00:00"),
        ("2025-06-01T09:30:00Z", "2025-06-01 09:30:00+00:00"),
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

    assert row[key] == "2026-01-02 10:00:00"


def test_a_sources_date_that_is_a_yaml_timestamp_binds(tmp_path: Path):
    config = _config(tmp_path, "task")
    (config.knowledge_bases[0].path / "tasks").mkdir()
    (config.knowledge_bases[0].path / "tasks" / "t.md").write_text(SOURCES, encoding="utf-8")

    db = _indexed(config)
    try:
        row = db.get_entry("t", "t")
    finally:
        db.close()

    assert [s["date"] for s in row["sources"]] == ["2026-01-01 10:00:00"]


def test_a_tag_that_is_a_yaml_timestamp_binds(tmp_path: Path):
    config = _config(tmp_path, "task")
    (config.knowledge_bases[0].path / "tasks").mkdir()
    (config.knowledge_bases[0].path / "tasks" / "t.md").write_text(TAGS, encoding="utf-8")

    db = _indexed(config)
    try:
        row = db.get_entry("t", "t")
    finally:
        db.close()

    assert row["tags"] == ["alpha", "2026-01-01 10:00:00"]


@pytest.mark.parametrize(
    ("spelled", "stored", "read"),
    [
        ("2026-01-02T10:00:00", "2026-01-02 10:00:00", "2026-01-02T10:00:00"),
        ("2026-01-02 10:00:00", "2026-01-02 10:00:00", "2026-01-02 10:00:00"),
        ("2026-01-02T10:00:00Z", "2026-01-02 10:00:00+00:00", "2026-01-02T10:00:00+00:00"),
    ],
)
def test_a_read_reports_the_files_own_spelling_not_the_rows(tmp_path: Path, spelled, stored, read):
    """The row holds one form; a read of the file holds the file's, as far as
    the loader keeps it (``Z`` reads back ``+00:00``), so an echo of a read
    carries the file's spelling, not the index's."""
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
        assert db2.get_entry("a", "t")["date"] == "2025-06-02 08:00:00+00:00"
    finally:
        db2.close()
