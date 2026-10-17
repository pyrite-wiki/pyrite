"""BlockService: block lookups behind a service (#380).

The blocks endpoint and `/entries/resolve` each ran their own
``svc.db.session.query(Block)``; both now ask this service.
"""

import pytest

from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.models.core_types import NoteEntry
from pyrite.storage.entry_writer import create_entry_text
from pyrite.storage.repository import new_entry_text
from pyrite.services.block_service import BlockService
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager


@pytest.fixture
def blocks(tmp_path):
    kb_path = tmp_path / "kb"
    kb_path.mkdir()
    note = NoteEntry(
        id="n",
        title="N",
        body="# Intro\nHello world\n\n## Details\nSome details here",
    )
    create_entry_text(kb_path / "n.md", new_entry_text(note))
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="kb", path=kb_path, kb_type=KBType.GENERIC)],
        settings=Settings(index_path=tmp_path / "index.db"),
    )
    db = PyriteDB(tmp_path / "index.db")
    IndexManager(db, config).index_all()
    yield BlockService(db)
    db.close()


def test_list_blocks_in_position_order(blocks):
    rows = blocks.list_blocks("n", "kb")
    assert [b.position for b in rows] == sorted(b.position for b in rows)
    assert len(rows) == 4


def test_list_blocks_filters(blocks):
    assert {b.heading for b in blocks.list_blocks("n", "kb", heading="Details")} == {"Details"}
    assert {b.block_type for b in blocks.list_blocks("n", "kb", block_type="paragraph")} == {
        "paragraph"
    }
    one = blocks.list_blocks("n", "kb")[1]
    assert [b.block_id for b in blocks.list_blocks("n", "kb", block_id=one.block_id)] == [
        one.block_id
    ]


def test_list_blocks_other_kb_is_empty(blocks):
    assert blocks.list_blocks("n", "other") == []


def test_find_block(blocks):
    assert blocks.find_block("n", "kb", heading="Details").content == "## Details"
    assert blocks.find_block("n", "kb", heading="Nowhere") is None
