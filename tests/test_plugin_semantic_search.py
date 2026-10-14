"""`PluginContext.search_semantic`: the vector index first, keyword as fallback.

A real index and a fake model, not mocks. The earlier tests mocked
`EmbeddingService` whole, and a `MagicMock` has every method: the code called
`svc.search`, which the service never had, so in real use the AttributeError
was caught and every plugin search was a keyword search (#668). They also
swapped `sys.modules` entries, which un-imports torch mid-run and crashes the
interpreter when this file runs first.
"""

from __future__ import annotations

import pytest

import pyrite.services.embedding_service as es
from pyrite.config import KBType, PyriteConfig, Settings
from pyrite.plugins.context import PluginContext
from tests.search_verdict_seed import FailingModel, add_entry, install_fake_model, make_db

OTHER_MODEL = "BAAI/bge-base-en-v1.5"


@pytest.fixture
def loaded_models(monkeypatch):
    return install_fake_model(monkeypatch)


@pytest.fixture
def db(tmp_path):
    db = make_db(tmp_path)
    for kb in ("kb", "other-kb"):
        db.register_kb(kb, KBType.GENERIC, str(tmp_path / kb))
    yield db
    db.close()


def make_ctx(db, kb_name="kb", **settings):
    return PluginContext(config=PyriteConfig(settings=Settings(**settings)), db=db, kb_name=kb_name)


class TestTheVectorIndexIsUsed:
    def test_rows_come_from_the_vector_index_with_similarity(self, db, loaded_models):
        add_entry(db, "near", "kb", cosine=0.62, axis=1, body="unrelated words")
        add_entry(db, "far", "kb", cosine=0.2, axis=2, body="deep meaning")

        rows = make_ctx(db).search_semantic("deep meaning")

        assert [(r["id"], r["similarity"]) for r in rows] == [("near", 0.62), ("far", 0.2)]
        assert all("distance" in r and r["snippet"] for r in rows)

    def test_the_query_is_embedded_by_the_configured_model(self, db, loaded_models):
        add_entry(db, "near", "kb", cosine=0.62, axis=1)

        make_ctx(db, embedding_model=OTHER_MODEL).search_semantic("anything")

        assert loaded_models == [OTHER_MODEL]

    def test_kb_name_overrides_the_context_kb(self, db, loaded_models):
        add_entry(db, "here", "kb", cosine=0.62, axis=1)
        add_entry(db, "there", "other-kb", cosine=0.5, axis=2)
        ctx = make_ctx(db)

        assert [r["id"] for r in ctx.search_semantic("q")] == ["here"]
        assert [r["id"] for r in ctx.search_semantic("q", kb_name="other-kb")] == ["there"]

    def test_limit(self, db, loaded_models):
        for i in range(4):
            add_entry(db, f"e{i}", "kb", cosine=0.6 - i / 10, axis=i + 1)

        assert [r["id"] for r in make_ctx(db).search_semantic("q", limit=2)] == ["e0", "e1"]


@pytest.mark.control(reason="the keyword fallback is the behaviour kept; it always ran")
class TestTheKeywordFallback:
    def test_without_the_semantic_extra(self, db, monkeypatch):
        add_entry(db, "note", "kb", cosine=0.62, axis=1, body="deep meaning")
        monkeypatch.setattr(es, "is_available", lambda: False)

        rows = make_ctx(db).search_semantic("meaning")

        assert [r["id"] for r in rows] == ["note"]
        assert "similarity" not in rows[0], "a keyword row has no similarity"

    def test_when_the_vector_index_returns_nothing(self, db, loaded_models):
        add_entry(db, "note", "kb", body="deep meaning")

        assert [r["id"] for r in make_ctx(db).search_semantic("meaning")] == ["note"]

    def test_when_the_vector_search_raises(self, db, monkeypatch):
        add_entry(db, "note", "kb", cosine=0.62, axis=1, body="deep meaning")
        install_fake_model(monkeypatch, model=FailingModel())  # the real service meets it

        assert [r["id"] for r in make_ctx(db).search_semantic("meaning")] == ["note"]

    def test_it_stays_in_the_context_kb_and_respects_limit(self, db, monkeypatch):
        monkeypatch.setattr(es, "is_available", lambda: False)
        for i in range(3):
            add_entry(db, f"k{i}", "kb", body="deep meaning")
        add_entry(db, "elsewhere", "other-kb", body="deep meaning")

        rows = make_ctx(db).search_semantic("meaning", limit=2)

        assert len(rows) == 2
        assert {r["kb_name"] for r in rows} == {"kb"}

    def test_everything_failing_is_an_empty_list(self, db, monkeypatch):
        monkeypatch.setattr(es, "is_available", lambda: False)
        add_entry(db, "note", "kb", body="deep meaning")
        assert [r["id"] for r in make_ctx(db).search_semantic("meaning")] == ["note"]  # it works
        db.close()  # a real database error on both legs, not a patched service

        assert make_ctx(db).search_semantic("meaning") == []
