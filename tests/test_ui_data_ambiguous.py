"""The Streamlit data layer refuses an id held by two KBs (#912).

``pyrite.ui.data.get_entry`` walks config.yaml's KBs itself when no KB is
named. It returned the first match; two KBs holding the id is now
``AmbiguousEntryError``, as on every other surface (#528).
"""

import sys
import types
from unittest.mock import patch

import pytest

from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.exceptions import AmbiguousEntryError
from pyrite.models.core_types import NoteEntry
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager


@pytest.fixture
def data(tmp_path, monkeypatch):
    st = types.ModuleType("streamlit")
    st.cache_resource = lambda *a, **k: a[0] if a and callable(a[0]) else (lambda fn: fn)
    st.cache_data = st.cache_resource
    monkeypatch.setitem(sys.modules, "streamlit", st)
    monkeypatch.delitem(sys.modules, "pyrite.ui.data", raising=False)
    kbs = []
    for name in ("kb-a", "kb-b"):
        path = tmp_path / name
        path.mkdir()
        NoteEntry(id="dup", title=f"Dup in {name}", body="x").save(path / "dup.md")
        kbs.append(KBConfig(name=name, path=path, kb_type=KBType.GENERIC))
    NoteEntry(id="solo", title="Solo", body="x").save(tmp_path / "kb-a" / "solo.md")
    config = PyriteConfig(knowledge_bases=kbs, settings=Settings(index_path=tmp_path / "i.db"))
    db = PyriteDB(config.settings.index_path)
    IndexManager(db, config).index_all()
    db.close()
    import pyrite.ui.data as module

    with patch.object(module, "load_config", return_value=config):
        yield module


def test_an_id_in_two_kbs_is_refused_without_a_kb(data):
    for kb in (None, "All KBs"):
        with pytest.raises(AmbiguousEntryError) as raised:
            data.get_entry("dup", kb)
        assert raised.value.candidate_kbs == ["kb-a", "kb-b"]


def test_a_named_kb_resolves_and_a_unique_id_needs_none(data):
    assert data.get_entry("dup", "kb-b")["title"] == "Dup in kb-b"
    assert data.get_entry("solo")["kb_name"] == "kb-a"
    assert data.get_entry("missing") is None
