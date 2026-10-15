"""A `date:` YAML reads as a timestamp is indexed, in one canonical text (#951).

The indexer bound ruamel's ``TimeStamp`` (a ``datetime`` subclass sqlite3 has
no adapter for) to the ``date`` column as it was, so a typed entry whose
``date:`` holds a full timestamp (``2025-06-01T00:00:00+00:00``) was never
indexed: ``index_all`` counted 0 for a KB holding just that file and the only
signal was a log warning. Required property: every file the loader reads is
indexed or reported, and a scalar YAML 1.2 loads as a date or timestamp is
stored in a temporal column as its ISO text, whatever the type: the text
``str()`` gives a date or datetime (a space before the time), which is what
sqlite3's own adapter writes for a plain ``datetime`` and what a read of the
file returns for the same value (``tests/test_get_reads_the_file.py``).

The oracle is the row ``PyriteDB.get_entry`` returns, compared with a value
written by hand, never with the indexer's own projection.
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


@pytest.fixture
def software_kb(tmp_path: Path):
    """A software KB (the ADR type is software-kb's) and an index database."""
    pytest.importorskip("pyrite_software_kb.entry_types")
    from pyrite.plugins.registry import PluginRegistry
    from pyrite_software_kb.plugin import SoftwareKBPlugin

    PluginRegistry().register(SoftwareKBPlugin())
    kb_path = tmp_path / "kb"
    (kb_path / "adrs").mkdir(parents=True)
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="t", path=kb_path, kb_type="software")],
        settings=Settings(index_path=tmp_path / "index.db"),
    )
    db = PyriteDB(config.settings.index_path)
    yield config, db, kb_path / "adrs" / "a.md"
    db.close()


@pytest.mark.parametrize(
    ("spelled", "stored"),
    [
        ("2025-06-01T00:00:00+00:00", "2025-06-01 00:00:00+00:00"),
        ("2025-06-01 09:30:00+00:00", "2025-06-01 09:30:00+00:00"),
        ("2025-06-01", "2025-06-01"),
        ('"2025-06-01"', "2025-06-01"),
    ],
)
def test_an_adr_whose_date_is_a_yaml_timestamp_is_indexed(software_kb, spelled, stored):
    config, db, path = software_kb
    path.write_text(ADR.format(date=spelled), encoding="utf-8")

    counts = IndexManager(db, config).index_all()

    assert counts == {"t": 1}, f"the file was not indexed: {counts}"
    row = db.get_entry("a", "t")
    assert row is not None, "index_all counted the file but no row holds it"
    assert row["date"] == stored


def test_a_date_edit_that_writes_a_timestamp_is_indexed_from_the_file(software_kb):
    """The case #730 P3 meets: an update writes a timestamp into `date:` and
    the row is built from the bytes written."""
    config, db, path = software_kb
    path.write_text(ADR.format(date="2025-06-01"), encoding="utf-8")
    IndexManager(db, config).index_all()

    KBService(config, db).update_entry("a", "t", date=datetime(2025, 6, 2, 8, 0, tzinfo=UTC))

    assert "date: 2025-06-02 08:00:00+00:00" in path.read_text(encoding="utf-8")
    db2 = PyriteDB(config.settings.index_path)
    try:
        assert IndexManager(db2, config).index_all() == {"t": 1}
        assert db2.get_entry("a", "t")["date"] == "2025-06-02 08:00:00+00:00"
    finally:
        db2.close()
