"""IndexManager.is_empty: the check `pyrite search` and `pyrite index embed`
make before working on the index (#380 moved it off raw SQL / db calls)."""

import pytest

from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.models.core_types import NoteEntry
from pyrite.storage.entry_writer import create_entry_text
from pyrite.storage.repository import new_entry_text
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager


@pytest.mark.control(
    reason="the deleted Entry.save/to_markdown is replaced by the create's text (new_entry_text), which the base already has; the property is unchanged (#955 P4)"
)
def test_is_empty_until_something_is_indexed(tmp_path):
    kb = tmp_path / "kb"
    kb.mkdir()
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="kb", path=kb, kb_type=KBType.GENERIC)],
        settings=Settings(index_path=tmp_path / "index.db"),
    )
    db = PyriteDB(config.settings.index_path)
    try:
        mgr = IndexManager(db, config)
        assert mgr.is_empty() is True
        create_entry_text(kb / "n.md", new_entry_text(NoteEntry(id="n", title="N", body="b")))
        mgr.index_all()
        assert mgr.is_empty() is False
    finally:
        db.close()
