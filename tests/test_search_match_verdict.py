"""A search response says what its page is worth (#668).

`match` is the verdict for the query, `match_basis` the reason in words, and
`keyword_match` the keyword leg's own signal. They are computed by one function
(`match_verdict`) from each leg's signal, and attached by one assembler
(`SearchService.search_response`). Nothing here loads an embedding model: the
vectors are built to have a known cosine with the query, and stored in a real
sqlite-vec index, so the numbers the verdict reads are the backend's own.

The tests that walk the CLI, MCP and REST are in
`test_search_verdict_on_every_surface.py`; the ones with the real model and the
fixture KB in `test_search_verdict_fixture_kb.py`.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import pyrite.services.embedding_service as es
from pyrite.config import KBType, Settings
from pyrite.services.search_service import SearchService
from tests.search_verdict_seed import add_entry, embed_crowd, install_fake_model, make_db


@pytest.fixture
def loaded_models(monkeypatch):
    return install_fake_model(monkeypatch)


@pytest.fixture
def db(tmp_path):
    db = make_db(tmp_path)
    db.register_kb("t", KBType.GENERIC, str(tmp_path / "kb"))
    yield db
    db.close()


# =========================================================================
# The model: the query is embedded by the model the config names
# =========================================================================


class TestTheConfiguredModelEmbedsTheQuery:
    """Entries are embedded by `settings.embedding_model` (`pyrite index embed`).

    The search path built its embedder with no model name, so with any other
    setting the query and the entries came from different models, and the
    per-model thresholds would have been read for a model that never ran.
    """

    def test_a_semantic_search_loads_the_configured_model(self, db, loaded_models):
        add_entry(db, "a", cosine=0.6, axis=1)
        svc = SearchService(db, settings=Settings(embedding_model="BAAI/bge-base-en-v1.5"))

        svc.search("anything", mode="semantic")

        assert loaded_models == ["BAAI/bge-base-en-v1.5"]

    def test_without_settings_it_is_the_default_model(self, db, loaded_models):
        add_entry(db, "a", cosine=0.6, axis=1)

        SearchService(db).search("anything", mode="semantic")

        assert loaded_models == [es.DEFAULT_EMBEDDING_MODEL]

    def test_the_queue_embeds_entries_with_the_model_it_is_given(self, db, loaded_models):
        from pyrite.services.embedding_worker import EmbeddingWorker

        add_entry(db, "a", body="some text")
        worker = EmbeddingWorker(db, model_name="BAAI/bge-base-en-v1.5")
        worker.enqueue("a", "t")

        assert worker.drain() == 1
        assert loaded_models == ["BAAI/bge-base-en-v1.5"]

    def test_every_embedder_built_in_pyrite_names_its_model(self):
        """No call site may fall back to the constructor's default (design.md, 8).

        The search path, the embed queue and the plugin context each built
        `EmbeddingService(db)` and so used the default model whatever the
        config said. A new construction site has to say which model it means.
        """
        root = Path(es.__file__).resolve().parents[1]
        unnamed: list[str] = []
        for path in sorted(root.rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
                if name not in ("EmbeddingService", "EmbeddingWorker"):
                    continue
                if not any(kw.arg == "model_name" for kw in node.keywords):
                    unnamed.append(f"{path.relative_to(root.parent)}:{node.lineno} {name}(...)")
        assert not unnamed, "built without model_name=:\n" + "\n".join(unnamed)


# =========================================================================
# The rule: one function, every branch, no model
# =========================================================================

MINILM = "all-MiniLM-L6-v2"
OTHER = "BAAI/bge-base-en-v1.5"


def verdict(mode, **signal):
    from pyrite.services.search_service import match_verdict

    return match_verdict(mode, **signal)


class TestTheThresholdTable:
    def test_it_has_one_row_and_sits_in_the_embedding_service(self):
        """Thresholds are measured per model; a row is added with its evidence."""
        assert set(es._MODEL_MATCH_THRESHOLDS) == {MINILM}
        row = es.match_thresholds_for_model(MINILM)
        assert (row.floor, row.strong, row.margin) == (0.35, 0.50, 0.10)

    def test_a_model_with_no_row_gets_none_not_a_borrowed_row(self):
        assert es.match_thresholds_for_model(OTHER) is None


class TestKeywordVerdict:
    @pytest.mark.parametrize(
        ("keyword_match", "expected"),
        [("all_terms", "strong"), ("some_terms", "weak"), ("none", "none")],
    )
    def test_each_keyword_signal(self, keyword_match, expected):
        match, basis = verdict("keyword", keyword_match=keyword_match, similarities=None)
        assert match == expected
        assert basis.startswith("keyword: ")


class TestSemanticVerdict:
    def test_at_or_above_the_strong_line_is_strong(self):
        match, basis = verdict(
            "semantic", keyword_match=None, similarities=[0.5, 0.49], model=MINILM
        )
        assert match == "strong"
        assert "0.50" in basis

    def test_below_the_floor_is_none_whatever_the_margin(self):
        match, basis = verdict(
            "semantic", keyword_match=None, similarities=[0.26, 0.01], model=MINILM
        )
        assert match == "none"
        assert basis == "semantic: best similarity 0.26, below 0.35"

    def test_between_the_lines_with_a_clear_margin_is_weak(self):
        match, basis = verdict(
            "semantic", keyword_match=None, similarities=[0.42, 0.2, 0.2], model=MINILM
        )
        assert match == "weak"
        assert "0.42" in basis and "0.22 above" in basis

    def test_between_the_lines_with_no_margin_is_none(self):
        """The near-domain absence: adjacent entries, none of them an answer."""
        match, basis = verdict(
            "semantic", keyword_match=None, similarities=[0.42, 0.38, 0.36], model=MINILM
        )
        assert match == "none"
        assert "0.42" in basis and "0.05 above" in basis and "0.10" in basis

    def test_the_margin_is_over_the_next_nine_only(self):
        # Nine neighbours at 0.30 give a margin of 0.12; an eleventh row at 0.0
        # would raise it, and must not be read.
        near = [0.42] + [0.30] * 9
        assert verdict("semantic", keyword_match=None, similarities=near, model=MINILM)[0] == "weak"
        assert (
            verdict("semantic", keyword_match=None, similarities=near + [0.0], model=MINILM)[0]
            == "weak"
        )
        crowd = [0.42] + [0.35] * 9 + [-1.0] * 5
        assert (
            verdict("semantic", keyword_match=None, similarities=crowd, model=MINILM)[0] == "none"
        )

    def test_one_row_between_the_lines_is_weak_and_says_it_had_no_neighbours(self):
        match, basis = verdict("semantic", keyword_match=None, similarities=[0.42], model=MINILM)
        assert match == "weak"
        assert "no other result" in basis

    def test_a_leg_that_ran_and_returned_nothing_is_none(self):
        match, basis = verdict("semantic", keyword_match=None, similarities=[], model=MINILM)
        assert match == "none"
        assert "semantic: " in basis

    def test_a_leg_that_did_not_run_is_weak_and_says_so(self):
        """It said `none`: "the KB does not contain an answer", of a KB nobody searched."""
        match, basis = verdict("semantic", keyword_match=None, similarities=None, model=MINILM)
        assert match == "weak"
        assert "did not run" in basis

    @pytest.mark.parametrize(
        ("code", "words"),
        [
            ("extra_missing", "sentence-transformers is not installed"),
            ("sqlite_vec_missing", "the vector index is not available"),
            ("sqlite_vec_not_loaded", "the vector index is not available"),
            ("no_embeddings", "nothing in the KBs searched is embedded yet"),
            ("filter_unsupported", "this backend cannot filter it"),
        ],
    )
    def test_the_basis_says_why_it_did_not_run(self, code, words):
        """In its own words: the basis must not send the reader to a warning
        that may not be there."""
        match, basis = verdict(
            "semantic", keyword_match=None, similarities=None, model=MINILM, semantic_skip=code
        )
        assert match == "weak"
        assert basis == f"semantic: the leg did not run: {words}"

    def test_nothing_indexed_is_an_absence(self):
        match, basis = verdict(
            "semantic", keyword_match=None, similarities=None, semantic_skip="nothing_indexed"
        )
        assert (match, basis) == ("none", "semantic: nothing is indexed in the KBs searched")

    @pytest.mark.parametrize("similarities", [[], [0.2], [0.42, 0.4, 0.39]])
    def test_a_capped_result_is_never_none(self, similarities):
        """sqlite-vec stopped at its ceiling with filtered rows unseen."""
        plain = verdict("semantic", keyword_match=None, similarities=similarities, model=MINILM)
        match, basis = verdict(
            "semantic", keyword_match=None, similarities=similarities, model=MINILM, capped=True
        )
        assert plain[0] == "none"
        assert match == "weak"
        assert "could not reach every entry" in basis

    def test_a_capped_result_with_a_strong_row_is_still_strong(self):
        match, _ = verdict(
            "semantic", keyword_match=None, similarities=[0.7], model=MINILM, capped=True
        )
        assert match == "strong"


class TestHybridVerdict:
    @pytest.mark.parametrize(
        ("keyword_match", "similarities", "expected"),
        [
            ("all_terms", [0.2], "strong"),  # keyword strong carries it
            ("none", [0.6, 0.1], "strong"),  # semantic strong carries it
            ("none", [0.2], "none"),  # semantic none, keyword not strong
            ("some_terms", [0.2], "none"),
            ("none", [0.42, 0.1], "weak"),  # semantic weak
            ("some_terms", [0.42, 0.1], "weak"),
            ("none", [], "none"),
            ("all_terms", None, "strong"),  # vector leg skipped: keyword alone
            ("some_terms", None, "weak"),
            # ... but the keyword leg alone cannot say none: hybrid promised a
            # vector search too, and it did not happen.
            ("none", None, "weak"),
        ],
    )
    def test_combinations(self, keyword_match, similarities, expected):
        match, basis = verdict(
            "hybrid", keyword_match=keyword_match, similarities=similarities, model=MINILM
        )
        assert match == expected
        assert "keyword: " in basis and "semantic: " in basis


class TestAnUncalibratedModel:
    """`bge-base` scores unanswerable queries 0.44 to 0.69: MiniLM's lines are
    wrong for it 25 times in 38 (the spike). No row, no semantic verdict."""

    def test_semantic_alone_is_weak_at_best_and_names_the_model(self):
        match, basis = verdict(
            "semantic", keyword_match=None, similarities=[0.91, 0.2], model=OTHER
        )
        assert match == "weak"
        assert f"uncalibrated model {OTHER}" in basis
        assert "0.91" in basis, "the similarity is still reported"

    def test_no_rows_is_not_none_either(self):
        """ "Nothing reached the floor" is a threshold too, and this model has none."""
        for mode, keyword_match in (
            ("semantic", None),
            ("hybrid", "none"),
            ("hybrid", "some_terms"),
        ):
            match, basis = verdict(mode, keyword_match=keyword_match, similarities=[], model=OTHER)
            assert match == "weak", mode
            assert f"uncalibrated model {OTHER}" in basis

    def test_hybrid_takes_strong_from_the_keyword_leg_only(self):
        match, basis = verdict("hybrid", keyword_match="all_terms", similarities=[0.1], model=OTHER)
        assert match == "strong"
        assert f"uncalibrated model {OTHER}" in basis

    def test_hybrid_with_rows_it_cannot_judge_does_not_claim_none(self):
        """Hybrid's keyword leg is AND-only: "not every term" there is no
        evidence of absence, and the vector rows may hold a paraphrase."""
        match, basis = verdict("hybrid", keyword_match="none", similarities=[0.1], model=OTHER)
        assert match == "weak"
        assert f"uncalibrated model {OTHER}" in basis


class TestEveryPathToNone:
    """`none` tells an agent "the KB does not contain an answer; say so".

    Every combination of signals `match_verdict` can be given, with the ones
    that may say `none` listed. Anything else that returns `none` fails here:
    a new path to it has to be added to the list, with its evidence.
    """

    #: (mode, keyword_match, the vector leg) -> why `none` is true of the KB
    SUPPORTED = {
        ("keyword", "none", "-"): "no entry matches the query, nor any one of its bare terms",
        ("semantic", None, "none"): "every embedded entry was compared; none is near (measured)",
        ("semantic", None, "empty"): "every embedded entry was compared; none passed the 0.155 cut",
        ("semantic", None, "nothing_indexed"): "there is nothing to search",
        ("hybrid", "none", "none"): "the measured hybrid rule: vector none, keyword not strong",
        ("hybrid", "some_terms", "none"): "the same rule",
        ("hybrid", "none", "empty"): "the same rule",
        ("hybrid", "some_terms", "empty"): "the same rule",
        ("hybrid", "none", "nothing_indexed"): "there is nothing to search",
        ("hybrid", "some_terms", "nothing_indexed"): "unreachable; nothing indexed has no hits",
    }

    VECTOR = {
        # name -> (similarities, model, semantic_skip, capped)
        "strong": ([0.7, 0.1], MINILM, None, False),
        "weak": ([0.42, 0.1], MINILM, None, False),
        "none": ([0.2], MINILM, None, False),
        "empty": ([], MINILM, None, False),
        "capped_none": ([0.2], MINILM, None, True),
        "capped_empty": ([], MINILM, None, True),
        "uncalibrated": ([0.2], OTHER, None, False),
        "uncalibrated_empty": ([], OTHER, None, False),
        "extra_missing": (None, None, "extra_missing", False),
        "sqlite_vec_missing": (None, None, "sqlite_vec_missing", False),
        "sqlite_vec_not_loaded": (None, MINILM, "sqlite_vec_not_loaded", False),
        "no_embeddings": (None, MINILM, "no_embeddings", False),
        "filter_unsupported": (None, MINILM, "filter_unsupported", False),
        "did_not_run": (None, MINILM, None, False),
        "nothing_indexed": (None, MINILM, "nothing_indexed", False),
    }

    def _cases(self):
        for keyword_match in ("all_terms", "some_terms", "none"):
            yield "keyword", keyword_match, "-"
        for name in self.VECTOR:
            yield "semantic", None, name
            for keyword_match in ("all_terms", "some_terms", "none"):
                yield "hybrid", keyword_match, name

    def test_none_is_said_only_on_the_listed_paths(self):
        said_none = set()
        for mode, keyword_match, name in self._cases():
            similarities, model, skip, capped = self.VECTOR.get(name, (None, None, None, False))
            match, basis = verdict(
                mode,
                keyword_match=keyword_match,
                similarities=similarities,
                model=model,
                semantic_skip=skip,
                capped=capped,
            )
            assert match in ("strong", "weak", "none") and basis
            if match == "none":
                said_none.add((mode, keyword_match, name))
        assert said_none == set(self.SUPPORTED)


# =========================================================================
# The assembler: the verdict belongs to the query, not to the page
# =========================================================================


@pytest.fixture
def svc(db, loaded_models):
    return SearchService(db, settings=Settings())


def response(svc, query="kestrel", **kwargs):
    return svc.search_response(query, **kwargs)


class TestKeywordRegimes:
    def test_every_term_matched(self, db, svc):
        add_entry(db, "a", body="kestrel hovering falcon")
        resp = response(svc, "kestrel falcon", mode="keyword")
        assert (resp["match"], resp["keyword_match"]) == ("strong", "all_terms")
        assert [r["id"] for r in resp["results"]] == ["a"]
        assert resp["legs_run"] == ["keyword"]

    def test_or_relaxed(self, db, svc):
        add_entry(db, "a", body="kestrel hovering")
        resp = response(svc, "kestrel submarine", mode="keyword")
        assert (resp["match"], resp["keyword_match"]) == ("weak", "some_terms")
        assert [r["id"] for r in resp["results"]] == ["a"], "weak still returns its rows"

    def test_no_hits(self, db, svc):
        add_entry(db, "a", body="kestrel hovering")
        resp = response(svc, "submarine periscope", mode="keyword")
        assert (resp["match"], resp["keyword_match"]) == ("none", "none")
        assert resp["results"] == []

    def test_a_caller_written_or_is_not_every_term(self, db, svc):
        add_entry(db, "a", body="kestrel hovering")
        resp = response(svc, "kestrel OR submarine", mode="keyword")
        assert (resp["match"], resp["keyword_match"]) == ("weak", "some_terms")
        assert "OR" in resp["match_basis"]

    def test_a_caller_written_and_or_phrase_is_every_term(self, db, svc):
        add_entry(db, "a", body="kestrel hovering falcon")
        for query in ("kestrel AND falcon", '"kestrel hovering"'):
            resp = response(svc, query, mode="keyword")
            assert (resp["match"], resp["keyword_match"]) == ("strong", "all_terms"), query

    def test_the_verdict_is_the_same_on_every_page(self, db, svc):
        for i in range(5):
            add_entry(db, f"k{i}", body="kestrel falcon")
        first = response(svc, "kestrel falcon", mode="keyword", limit=2)
        later = response(svc, "kestrel falcon", mode="keyword", limit=2, offset=4)
        past = response(svc, "kestrel falcon", mode="keyword", limit=2, offset=10)
        assert len(later["results"]) == 1
        assert past["results"] == [], "a page past the end is empty, not OR-relaxed rows"
        for resp in (later, past):
            for key in ("match", "match_basis", "keyword_match"):
                assert resp[key] == first[key]


class _Expansion:
    def __init__(self, terms):
        self.terms = terms

    def expand(self, query):
        return self.terms


class TestExpandRegime:
    """`expand` ORs model-written terms onto the query; a hit on one of those
    alone is not the caller's terms matching."""

    def test_a_hit_on_the_callers_own_terms_is_all_terms(self, db, svc):
        add_entry(db, "a", body="kestrel hovering falcon")
        svc._expansion_service = _Expansion(["raptor"])
        resp = response(svc, "kestrel falcon", mode="keyword", expand=True)
        assert (resp["match"], resp["keyword_match"]) == ("strong", "all_terms")

    def test_a_hit_only_on_an_expansion_term_is_some_terms(self, db, svc):
        add_entry(db, "a", body="raptor notes")
        svc._expansion_service = _Expansion(["raptor"])
        for mode in ("keyword", "hybrid"):
            resp = response(svc, "kestrel falcon", mode=mode, expand=True)
            assert resp["keyword_match"] == "some_terms", mode
            assert resp["match"] == "weak", mode
            assert [r["id"] for r in resp["results"]] == ["a"]


class TestSemanticRegimes:
    def test_strong(self, db, svc):
        embed_crowd(db, 0.62, [0.2] * 11)
        resp = response(svc, mode="semantic")
        assert resp["match"] == "strong"
        assert "keyword_match" not in resp, "no keyword leg ran"
        assert resp["legs_run"] == ["semantic"]
        assert resp["results"][0]["similarity"] == 0.62

    def test_weak(self, db, svc):
        embed_crowd(db, 0.42, [0.2] * 11)
        assert response(svc, mode="semantic")["match"] == "weak"

    def test_none_below_the_floor_still_returns_the_rows(self, db, svc):
        embed_crowd(db, 0.3, [0.2] * 11)
        resp = response(svc, mode="semantic")
        assert resp["match"] == "none"
        assert resp["match_basis"] == "semantic: best similarity 0.30, below 0.35"
        assert len(resp["results"]) == 12, "none tells the reader what the page is worth"

    def test_none_by_margin(self, db, svc):
        embed_crowd(db, 0.42, [0.38] * 11)
        assert response(svc, mode="semantic")["match"] == "none"

    def test_every_row_carries_similarity_and_still_carries_distance(self, db, svc):
        embed_crowd(db, 0.62, [0.4, 0.2])
        rows = response(svc, mode="semantic")["results"]
        assert [r["similarity"] for r in rows] == [0.62, 0.4, 0.2]
        assert all("distance" in r for r in rows), "`distance` stays for one release"

    def test_the_cutoff_is_a_similarity_floor(self, db, svc):
        embed_crowd(db, 0.62, [0.16, 0.1])
        rows = response(svc, mode="semantic")["results"]
        assert [r["similarity"] for r in rows] == [0.62, 0.16], "cos 0.1 is cut (floor 0.155)"

    def test_fewer_than_ten_embedded_entries(self, db, svc):
        embed_crowd(db, 0.42, [0.38, 0.36])
        resp = response(svc, mode="semantic")
        assert resp["match"] == "none"
        assert "next 2 results" in resp["match_basis"]

    @pytest.mark.parametrize("top", [0.62, 0.42, 0.3])
    @pytest.mark.parametrize("page", [{"limit": 1}, {"limit": 3}, {"limit": 3, "offset": 6}])
    def test_limit_and_offset_do_not_change_the_verdict(self, db, svc, top, page):
        """The margin is read over the next nine results whatever page was asked for."""
        # Nine near neighbours then a far tail: a margin taken over fewer than
        # nine (limit 1, limit 3) or over a later page reads differently.
        embed_crowd(db, top, [0.36] * 3 + [0.2] * 6 + [0.16] * 6)
        whole = response(svc, mode="semantic", limit=20)
        resp = response(svc, mode="semantic", **page)
        assert len(resp["results"]) == page["limit"]
        assert (resp["match"], resp["match_basis"]) == (whole["match"], whole["match_basis"])

    def test_no_embeddings_at_all(self, db, svc):
        add_entry(db, "a", body="kestrel")
        resp = response(svc, mode="semantic")
        assert resp["results"] == []
        assert resp["match"] == "weak", "nobody searched: that is not an absence"
        assert resp["match_basis"] == (
            "semantic: the leg did not run: nothing in the KBs searched is embedded yet"
        )
        assert resp["legs_run"] == []
        assert any("pyrite index embed" in w for w in resp["warnings"])

    def test_an_empty_kb_is_an_absence(self, db, svc):
        resp = response(svc, mode="semantic")
        assert (resp["results"], resp["match"]) == ([], "none")
        assert resp["match_basis"] == "semantic: nothing is indexed in the KBs searched"
        assert "warnings" not in resp

    def test_an_uncalibrated_model_gets_similarity_and_says_so(self, db, loaded_models):
        embed_crowd(db, 0.9, [0.2] * 3)
        svc = SearchService(db, settings=Settings(embedding_model=OTHER))
        resp = response(svc, mode="semantic")
        assert resp["match"] == "weak"
        assert f"uncalibrated model {OTHER}" in resp["match_basis"]
        assert resp["results"][0]["similarity"] == 0.9


class TestHybridRegimes:
    def test_a_row_both_legs_returned_keeps_similarity_and_names_its_legs(self, db, svc):
        add_entry(db, "both", cosine=0.62, axis=1, body="kestrel falcon")
        add_entry(db, "kw", body="kestrel falcon too")
        add_entry(db, "vec", cosine=0.4, axis=2, body="unrelated words")
        rows = {r["id"]: r for r in response(svc, "kestrel falcon", mode="hybrid")["results"]}
        assert rows["both"]["similarity"] == 0.62
        assert rows["both"]["legs"] == ["keyword", "semantic"]
        assert rows["kw"]["legs"] == ["keyword"] and "similarity" not in rows["kw"]
        assert rows["vec"]["legs"] == ["semantic"] and rows["vec"]["similarity"] == 0.4

    def test_strong_from_either_leg(self, db, svc):
        add_entry(db, "kw", body="kestrel falcon")
        embed_crowd(db, 0.2, [0.18] * 3, body="unrelated")
        resp = response(svc, "kestrel falcon", mode="hybrid")
        assert (resp["match"], resp["keyword_match"]) == ("strong", "all_terms")

    def test_none_when_neither_leg_has_it(self, db, svc):
        embed_crowd(db, 0.3, [0.2] * 3, body="unrelated")
        resp = response(svc, "kestrel falcon", mode="hybrid")
        assert (resp["match"], resp["keyword_match"]) == ("none", "none")
        assert len(resp["results"]) == 4
        assert "keyword: " in resp["match_basis"] and "semantic: " in resp["match_basis"]

    def test_the_vector_leg_skipped_leaves_the_keyword_signal_and_the_warning(self, db, svc):
        add_entry(db, "kw", body="kestrel falcon")
        resp = response(svc, "kestrel falcon", mode="hybrid")
        assert (resp["match"], resp["keyword_match"]) == ("strong", "all_terms")
        assert "did not run" in resp["match_basis"]
        assert any("pyrite index embed" in w for w in resp["warnings"])
        assert resp["results"][0]["legs"] == ["keyword"]
        assert resp["legs_run"] == ["keyword"], "which legs ran, without parsing the basis"

    def test_the_vector_leg_skipped_for_a_missing_extra(self, db, svc, monkeypatch):
        add_entry(db, "kw", body="kestrel falcon")
        monkeypatch.setattr(es, "is_available", lambda: False)
        resp = response(svc, "submarine periscope", mode="hybrid")
        assert (resp["match"], resp["keyword_match"]) == ("weak", "none")
        assert resp["legs_run"] == ["keyword"]
        assert "sentence-transformers is not installed" in resp["match_basis"]
        assert any("pyrite[semantic]" in w for w in resp["warnings"])

    @pytest.mark.parametrize("why", ["no embeddings", "extra missing"])
    def test_a_hybrid_search_whose_vector_leg_did_not_run_is_a_keyword_search(
        self, db, svc, monkeypatch, why
    ):
        """The default `kb_search` on an install with no embeddings.

        No entry has every term. Keyword mode retries with the terms OR-ed and
        returns the near miss as `weak`; hybrid's keyword leg did not relax, so
        hybrid returned nothing and called it `none`.
        """
        add_entry(db, "kw", body="kestrel hovering")
        if why == "extra missing":
            monkeypatch.setattr(es, "is_available", lambda: False)
        hybrid = response(svc, "kestrel submarine", mode="hybrid")
        keyword = response(svc, "kestrel submarine", mode="keyword")

        assert [r["id"] for r in hybrid["results"]] == ["kw"]
        assert [r["id"] for r in keyword["results"]] == ["kw"]
        assert (hybrid["match"], hybrid["keyword_match"]) == ("weak", "some_terms")
        assert (keyword["match"], keyword["keyword_match"]) == ("weak", "some_terms")
        assert hybrid["legs_run"] == ["keyword"]

    def test_beside_a_vector_leg_that_ran_the_keyword_leg_does_not_relax(self, db, svc):
        """Its near neighbours are the near misses; the measured rule is AND."""
        add_entry(db, "kw", cosine=0.2, axis=1, body="kestrel hovering")
        resp = response(svc, "kestrel submarine", mode="hybrid")
        assert resp["keyword_match"] == "none"
        assert resp["legs_run"] == ["keyword", "semantic"]
        assert resp["results"][0]["legs"] == ["semantic"]

    def test_the_verdict_is_the_same_on_every_page(self, db, svc):
        embed_crowd(db, 0.42, [0.36] * 3 + [0.2] * 6 + [0.16] * 6, body="unrelated")
        whole = response(svc, "kestrel falcon", mode="hybrid", limit=20)
        for page in ({"limit": 1}, {"limit": 3}, {"limit": 3, "offset": 6}):
            resp = response(svc, "kestrel falcon", mode="hybrid", **page)
            assert len(resp["results"]) == page["limit"]
            assert (resp["match"], resp["match_basis"]) == (whole["match"], whole["match_basis"])

    def test_a_caller_written_or_is_not_every_term(self, db, svc):
        add_entry(db, "kw", cosine=0.3, axis=1, body="kestrel hovering")
        resp = response(svc, "kestrel OR submarine", mode="hybrid")
        assert resp["keyword_match"] == "some_terms"
        assert resp["match"] == "none", "semantic says none and keyword is not strong"


# =========================================================================
# Read scoping: the verdict is that of a search over the readable KBs only
# =========================================================================


@pytest.fixture
def two_kbs(tmp_path, loaded_models):
    """`open` is readable; `secret` holds the best hit on both legs."""
    db = make_db(tmp_path)
    db.register_kb("open", KBType.GENERIC, str(tmp_path / "open"))
    db.register_kb("secret", KBType.GENERIC, str(tmp_path / "secret"))
    embed_crowd(db, 0.3, [0.2] * 3, kb="open", prefix="o", body="hovering notes")
    embed_crowd(
        db, 0.87, [0.86] * 30, kb="secret", prefix="s", start_axis=50, body="kestrel falcon"
    )
    yield db
    db.close()


class TestReadScoping:
    @pytest.mark.parametrize("mode", ["keyword", "semantic", "hybrid"])
    @pytest.mark.parametrize("page", [{}, {"limit": 2}, {"limit": 2, "offset": 2}])
    def test_a_scoped_search_answers_as_a_search_of_the_readable_kbs(self, two_kbs, mode, page):
        svc = SearchService(two_kbs, settings=Settings())
        scoped = response(svc, "kestrel falcon", mode=mode, kb_names={"open"}, **page)
        alone = response(svc, "kestrel falcon", mode=mode, kb_name="open", **page)

        assert scoped == alone
        assert {r["kb_name"] for r in scoped["results"]} <= {"open"}
        assert scoped["match"] == "none"
        assert "0.87" not in scoped["match_basis"] and "0.86" not in scoped["match_basis"]
        assert "secret" not in str(scoped)

    @pytest.mark.parametrize("mode", ["semantic", "hybrid"])
    def test_a_crowd_of_unreadable_neighbours_does_not_starve_the_page(self, two_kbs, mode):
        """31 nearer rows in `secret`: an over-fetch-then-filter loses `open`'s rows."""
        svc = SearchService(two_kbs, settings=Settings())
        resp = response(svc, "kestrel falcon", mode=mode, kb_names={"open"}, limit=2)
        assert resp["results"][0]["id"] == "o0"
        assert [r["kb_name"] for r in resp["results"]] == ["open", "open"]

    @pytest.mark.parametrize("mode", ["semantic", "hybrid"])
    @pytest.mark.parametrize(
        ("best_readable", "expected"),
        [(0.62, "strong"), (0.3, "none")],
    )
    def test_more_unreadable_neighbours_than_the_knn_can_hold(
        self, tmp_path, loaded_models, monkeypatch, mode, best_readable, expected
    ):
        """sqlite-vec considers at most k nearest vectors (4096; 8 here). With
        more nearer vectors than that in a KB the caller cannot read, a
        readable set applied after the KNN sees none of the caller's rows: the
        page is empty and the verdict is about the wrong candidates. The
        readable set rides inside the KNN with the other filters (#716's
        prefilter), so the candidates, the rows and the verdict are the
        scoped ones: `strong` when a readable entry answers, and a real `none`
        (with its own best similarity) when none does."""
        from pyrite.storage.backends import sqlite_backend

        monkeypatch.setattr(sqlite_backend, "_SQLITE_VEC_MAX_K", 8)
        db = make_db(tmp_path)
        db.register_kb("open", KBType.GENERIC, str(tmp_path / "open"))
        db.register_kb("secret", KBType.GENERIC, str(tmp_path / "secret"))
        embed_crowd(db, best_readable, [0.2] * 3, kb="open", prefix="o", body="hovering notes")
        embed_crowd(db, 0.87, [0.86] * 39, kb="secret", prefix="s", start_axis=50, body="x")
        svc = SearchService(db, settings=Settings())

        scoped = response(svc, "kestrel falcon", mode=mode, kb_names={"open"}, limit=3)
        alone = response(svc, "kestrel falcon", mode=mode, kb_name="open", limit=3)

        assert scoped == alone
        assert scoped["results"][0]["id"] == "o0" and len(scoped["results"]) == 3
        assert scoped["match"] == expected
        assert f"best similarity {best_readable:.2f}" in scoped["match_basis"]
        assert "could not reach" not in scoped["match_basis"], "not a capped result"
        assert "semantic" in scoped["legs_run"]
        assert {r["kb_name"] for r in scoped["results"]} == {"open"}
        assert "0.87" not in scoped["match_basis"] and "0.86" not in scoped["match_basis"]
        db.close()

    @pytest.mark.parametrize("mode", ["keyword", "semantic", "hybrid"])
    def test_all_kbs(self, two_kbs, mode):
        svc = SearchService(two_kbs, settings=Settings())
        resp = response(svc, "kestrel falcon", mode=mode)
        assert resp["match"] == "strong"
        assert resp["results"][0]["kb_name"] == "secret"

    @pytest.mark.parametrize("mode", ["keyword", "semantic", "hybrid"])
    def test_an_empty_readable_set_reads_nothing(self, two_kbs, mode):
        svc = SearchService(two_kbs, settings=Settings())
        resp = response(svc, "kestrel falcon", mode=mode, kb_names=set())
        assert resp["results"] == []
        assert resp["match"] == "none"

    @pytest.mark.parametrize("mode", ["semantic", "hybrid"])
    def test_embeddings_in_a_kb_the_caller_cannot_read_change_nothing(self, tmp_path, mode):
        """`has_embeddings()` asked the whole index.

        `open` has entries and no vectors. Whether `secret` has vectors decided
        what a caller who reads only `open` was told: with them the vector leg
        "ran" and found nothing (`none`); without them it was skipped, with a
        warning. Both must be the second: nothing the caller can read was
        searched by that leg.
        """
        answers = []
        for secret_is_embedded in (False, True):
            db = make_db(tmp_path, f"{secret_is_embedded}.db")
            db.register_kb("open", KBType.GENERIC, str(tmp_path / "open"))
            db.register_kb("secret", KBType.GENERIC, str(tmp_path / "secret"))
            add_entry(db, "o", "open", body="hovering notes")
            add_entry(
                db, "s", "secret", cosine=0.9 if secret_is_embedded else None, axis=1, body="x"
            )
            svc = SearchService(db, settings=Settings())
            scoped = response(svc, "kestrel falcon", mode=mode, kb_names={"open"})
            named = response(svc, "kestrel falcon", mode=mode, kb_name="open")
            assert scoped == named
            answers.append(scoped)
            db.close()

        assert answers[0] == answers[1]
        assert answers[0]["match"] == "weak"
        assert "semantic" not in answers[0]["legs_run"]
        assert any("pyrite index embed" in w for w in answers[0]["warnings"])

    def test_no_warning_about_entries_the_caller_cannot_read(self, tmp_path, loaded_models):
        """The embed warning was decided by `count_entries()` over every KB."""
        db = make_db(tmp_path)
        db.register_kb("open", KBType.GENERIC, str(tmp_path / "open"))
        db.register_kb("secret", KBType.GENERIC, str(tmp_path / "secret"))
        add_entry(db, "s", "secret", body="unembedded")
        resp = response(
            SearchService(db, settings=Settings()), "q", mode="semantic", kb_names={"open"}
        )
        assert "warnings" not in resp
        assert (resp["match"], resp["legs_run"]) == ("none", [])
        db.close()

    @pytest.mark.parametrize("mode", ["semantic", "hybrid"])
    def test_a_result_the_index_cut_short_is_weak_not_none(self, two_kbs, monkeypatch, mode):
        """sqlite-vec looks at the 4096 nearest vectors. With more than that
        many nearer ones in KBs the caller cannot read, their own rows are not
        reached: the page is short or empty, and that is not an absence. The
        backend marks it (`SemanticRows.capped`; the conformance suite builds
        the 4097 rows) and the verdict does not read it as `none`."""
        from pyrite.storage.backends.protocol import SemanticRows

        def _capped(self, *args, **kwargs):
            rows = SemanticRows()
            rows.capped = True
            return rows

        monkeypatch.setattr(type(two_kbs.backend), "search_semantic", _capped)
        resp = response(
            SearchService(two_kbs, settings=Settings()), "q", mode=mode, kb_names={"open"}
        )
        assert resp["match"] == "weak"
        assert "could not reach every entry" in resp["match_basis"]
        assert "semantic" in resp["legs_run"]


class TestTheAssemblerIsTheSearch:
    def test_its_rows_are_search_s_rows(self, db, svc):
        add_entry(db, "both", cosine=0.62, axis=1, body="kestrel falcon")
        add_entry(db, "vec", cosine=0.4, axis=2, body="unrelated words")
        for mode in ("keyword", "semantic", "hybrid"):
            assert response(svc, "kestrel falcon", mode=mode)["results"] == svc.search(
                "kestrel falcon", mode=mode
            )

    def test_warnings_are_absent_when_there_are_none(self, db, svc):
        add_entry(db, "a", body="kestrel")
        assert "warnings" not in response(svc, "kestrel", mode="keyword")

    def test_a_callers_trace_is_still_filled(self, db, svc):
        add_entry(db, "a", body="kestrel")
        trace: dict = {}
        response(svc, "kestrel submarine", mode="keyword", trace=trace)
        assert trace["relaxed"] is True and trace["actual_mode"] == "keyword"
