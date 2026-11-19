"""`searched` names only what the legs that ran read, on this backend (#1757).

Each case runs a real search over a real index and checks `searched` against
what the search did and did not find. Expected values are written by hand.
"""

from __future__ import annotations

import pytest

import pyrite.services.embedding_service as es
from pyrite.config import KBType
from pyrite.services.search_service import SearchService
from tests.search_verdict_seed import FakeModel, add_entry, install_fake_model, make_db

# What SQLite's FTS5 table reads on an unqualified MATCH, in table order.
SQLITE_ALL = ["id", "kb_name", "entry_type", "title", "body", "summary", "location"]


@pytest.fixture
def db(tmp_path):
    db = make_db(tmp_path)
    db.register_kb("t", KBType.GENERIC, str(tmp_path / "kb"))
    yield db
    db.close()


@pytest.fixture
def svc(db, monkeypatch):
    install_fake_model(monkeypatch)
    return SearchService(db)


def ids(resp):
    return [r["id"] for r in resp["results"]]


class TestKeywordSearchedIsWhatTheIndexReads:
    def test_a_summary_only_word_is_found_and_summary_is_named(self, db, svc):
        add_entry(db, "kestrel", summary="falconry notes")
        resp = svc.search_response("falconry", mode="keyword")
        assert ids(resp) == ["kestrel"]
        assert resp["searched"] == SQLITE_ALL

    def test_a_field_value_only_word_misses_and_is_not_claimed(self, db, svc):
        add_entry(db, "invoice", metadata={"vendor": "Acmecorp"})
        resp = svc.search_response("Acmecorp", mode="keyword")
        assert resp["results"] == [] and resp["match"] == "none"
        assert "metadata" not in resp["searched"] and "vendor" not in resp["searched"]
        assert "tags" not in resp["searched"]

    def test_a_tag_only_word_misses_and_is_not_claimed(self, db, svc):
        add_entry(db, "fish", tags=["zebrafish"])
        resp = svc.search_response("zebrafish", mode="keyword")
        assert resp["results"] == [] and resp["match"] == "none"
        assert "tags" not in resp["searched"]


class TestNoLegRanNothingWasSearched:
    def test_semantic_with_nothing_embedded(self, db, svc):
        add_entry(db, "a", body="kestrel")
        resp = svc.search_response("kestrel", mode="semantic")
        assert resp["legs_run"] == []
        assert resp["searched"] == []
        assert "body_prefix_chars" not in resp

    def test_semantic_refused_for_scope(self, db, svc):
        add_entry(db, "a", body="kestrel", cosine=0.9, axis=1)
        resp = svc.search_response("kestrel", mode="semantic", kb_names=set())
        assert resp["legs_run"] == []
        assert resp["searched"] == []


class TestSemanticOnlyReadsABodyPrefix:
    @pytest.mark.control(
        reason="pins what embedding already does (cuts the body); the `body_prefix` claim is true only while it holds"
    )
    def test_the_vector_leg_never_sees_a_word_past_the_prefix(self, db, monkeypatch):
        """The claim `body_prefix` makes, shown on the text the model is given."""
        seen: list[str] = []

        class Recording(FakeModel):
            def encode(self, text, **kwargs):
                seen.extend(text if isinstance(text, list) else [text])
                return super().encode(text, **kwargs)

        install_fake_model(monkeypatch, Recording())
        body = "x " * 280 + "needleword"  # the word starts at character 560
        add_entry(db, "long", body=body, summary="the summary")
        assert es.EmbeddingService(db).embed_entry("long", "t")
        assert seen and "needleword" not in seen[-1]
        assert "the summary" in seen[-1]

    def test_semantic_names_a_prefix_and_its_limit(self, db, svc):
        add_entry(db, "a", body="kestrel", cosine=0.9, axis=1)
        resp = svc.search_response("kestrel", mode="semantic")
        assert resp["legs_run"] == ["semantic"]
        assert resp["searched"] == ["title", "summary", "body_prefix"]
        assert resp["body_prefix_chars"] == 500

    def test_hybrid_reads_the_whole_body_through_its_keyword_leg(self, db, svc):
        add_entry(db, "a", body="kestrel", cosine=0.9, axis=1)
        resp = svc.search_response("kestrel", mode="hybrid")
        assert resp["legs_run"] == ["keyword", "semantic"]
        assert resp["searched"] == SQLITE_ALL
        assert "body_prefix_chars" not in resp

    def test_hybrid_with_only_the_keyword_leg(self, db, svc):
        add_entry(db, "a", body="kestrel")
        resp = svc.search_response("kestrel", mode="hybrid")
        assert resp["legs_run"] == ["keyword"]
        assert resp["searched"] == SQLITE_ALL


class TestAColumnFilterNarrowsIt:
    """FTS5 reads `title:hawk` as "hawk, in the title column only"."""

    def test_the_filter_limits_the_search_and_the_list(self, db, svc):
        add_entry(db, "in-body", title="Plain", body="hawk")
        add_entry(db, "in-title", title="hawk", body="plain")
        resp = svc.search_response("title:hawk OR title:falcon", mode="keyword")
        assert ids(resp) == ["in-title"]
        assert resp["searched"] == ["title"]

    def test_a_column_set(self, db, svc):
        add_entry(db, "in-summary", title="Plain", summary="hawk")
        resp = svc.search_response("{summary body}: (hawk OR falcon)", mode="keyword")
        assert ids(resp) == ["in-summary"]
        assert resp["searched"] == ["body", "summary"]

    def test_a_column_exclusion(self, db, svc):
        add_entry(db, "in-title", title="hawk", body="plain")
        resp = svc.search_response("- title: (hawk OR falcon)", mode="keyword")
        assert resp["results"] == []
        assert resp["searched"] == ["id", "kb_name", "entry_type", "body", "summary", "location"]

    def test_a_colon_inside_a_phrase_is_not_a_filter(self, db, svc):
        add_entry(db, "a", body="see title: hawk")
        resp = svc.search_response('"title: hawk" OR falcon', mode="keyword")
        assert ids(resp) == ["a"]
        assert resp["searched"] == SQLITE_ALL

    def test_an_unquoted_colon_word_is_quoted_by_sanitizing_not_a_filter(self, db, svc):
        add_entry(db, "a", title="Plain", body="title:hawk here")
        resp = svc.search_response("title:hawk", mode="keyword")
        assert ids(resp) == ["a"]
        assert resp["searched"] == SQLITE_ALL


# Where `hawk` is the only word, one entry per column it can be read in.
HAWK_IN = {
    "id": {"entry_id": "hawk", "title": "plain"},
    "kb_name": {"entry_id": "e-kb", "kb": "hawk-kb"},
    "entry_type": {"entry_id": "e-type", "entry_type": "hawk"},
    "title": {"entry_id": "e-title", "title": "hawk"},
    "body": {"entry_id": "e-body", "body": "hawk"},
    "summary": {"entry_id": "e-summary", "summary": "hawk"},
    "location": {"entry_id": "e-location", "location": "hawk"},
}


@pytest.fixture
def hawks(db, svc, tmp_path):
    db.register_kb("hawk-kb", KBType.GENERIC, str(tmp_path / "hawk-kb"))
    for spec in HAWK_IN.values():
        spec = dict(spec)
        add_entry(db, spec.pop("entry_id"), spec.pop("kb", "t"), **spec)
    return {spec["entry_id"]: column for column, spec in HAWK_IN.items()}


class TestSearchedNeverOverstatesAFilter:
    """Each query is run against real FTS5 on entries holding `hawk` in one
    column at a time: the columns that find it are the columns read, and
    `searched` must list exactly those (every term here is `hawk`, so the union
    over terms is what the index reads)."""

    ORDER = ["id", "kb_name", "entry_type", "title", "body", "summary", "location"]

    @pytest.mark.parametrize(
        ("query", "expected"),
        [
            ("hawk", ORDER),
            ("title: hawk", ["title"]),
            ('"title": hawk', ["title"]),
            ('"title" : hawk', ["title"]),
            ('{"title" summary}: hawk', ["title", "summary"]),
            ("{title body}: hawk", ["title", "body"]),
            ("- title: hawk", ["id", "kb_name", "entry_type", "body", "summary", "location"]),
            ("-{title body}: hawk", ["id", "kb_name", "entry_type", "summary", "location"]),
            ("title: hawk OR body: hawk", ["title", "body"]),
            ("title: (hawk OR hawk)", ["title"]),
            # the operand after OR is not filtered
            ("title: hawk OR hawk", ORDER),
            # nested filters intersect
            ("{title body}: ({body summary}: hawk)", ["body"]),
            ("title: (body: hawk)", []),
            ("{title body summary}: ({body summary}: (summary: hawk))", ["summary"]),
        ],
    )
    def test_searched_is_what_the_index_reads(self, svc, hawks, query, expected):
        # Unquoted, a filter reaches MATCH only beside an operator or a quote
        # (sanitizing quotes a stray `col:` otherwise): repeat it with OR.
        if not any(marker in query for marker in (" OR ", '"')):
            query = f"{query} OR {query}"
        resp = svc.search_response(query, mode="keyword")
        found = [
            column for column in self.ORDER if column in {hawks[r["id"]] for r in resp["results"]}
        ]
        assert found == expected, "the real index disagrees with the hand-written columns"
        assert resp["searched"] == expected

    def test_a_bare_colon_query_is_quoted_by_sanitizing_so_it_is_no_filter(self, svc, hawks):
        resp = svc.search_response("title: hawk", mode="keyword")
        assert resp["searched"] == self.ORDER

    def test_an_unfiltered_operand_after_a_filtered_one_reads_every_column(self, db, svc):
        add_entry(db, "a", title="hawk", body="falcon")
        resp = svc.search_response("title: hawk falcon OR hawk falcon", mode="keyword")
        assert ids(resp) == ["a"], "falcon was read in the body, not only in the title"
        assert resp["searched"] == SQLITE_ALL

    def test_a_filter_binds_to_a_near_group(self, db, svc):
        add_entry(db, "a", title="hawk hawk", body="x")
        near = "{title body}: NEAR(hawk hawk, 5)"
        resp = svc.search_response(f"{near} OR {near}", mode="keyword")
        assert ids(resp) == ["a"]
        assert resp["searched"] == ["title", "body"]

    def test_a_query_this_reader_cannot_follow_claims_nothing(self):
        from pyrite.storage.virtual_tables import fts_columns_read

        assert fts_columns_read("title: (hawk") == []
        assert fts_columns_read("title: body: hawk") == []
        assert fts_columns_read("nosuchcolumn: hawk") == []
        assert fts_columns_read("title:") == []


class TestALimitOfZeroEmbedsNoBody:
    def test_searched_agrees_with_what_embedding_does(self, db, svc, monkeypatch):
        seen: list[str] = []

        class Recording(FakeModel):
            def encode(self, text, **kwargs):
                seen.extend(text if isinstance(text, list) else [text])
                return super().encode(text, **kwargs)

        install_fake_model(monkeypatch, Recording())
        monkeypatch.setattr(es, "max_body_chars_for_model", lambda model_name: 0)
        add_entry(db, "a", title="the title", summary="the summary", body="needleword")
        assert es.EmbeddingService(db).embed_entry("a", "t")
        assert "needleword" not in seen[-1] and "the summary" in seen[-1]

        add_entry(db, "b", body="x", cosine=0.9, axis=2)
        resp = SearchService(db).search_response("x", mode="semantic")
        assert resp["legs_run"] == ["semantic"]
        assert resp["searched"] == ["title", "summary"]
        assert "body_prefix_chars" not in resp


class TestPostgresReportsItsOwnColumns:
    def test_the_postgres_vector_is_title_summary_body(self):
        from pyrite.storage.backends.postgres_backend import PostgresBackend

        # The method reads no state: it is the statement of the trigger's parts.
        assert PostgresBackend.keyword_searched_parts(None, "title:x OR y") == [
            "title",
            "summary",
            "body",
        ]
