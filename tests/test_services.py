"""Tests for service layer."""

import tempfile
from pathlib import Path

import pytest

from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.exceptions import KBReadOnlyError, QuerySyntaxError
from pyrite.services import KBService, QueryExpansionService, SearchMode, SearchService
from pyrite.services.query_expansion_service import is_available
from pyrite.storage.database import PyriteDB
from pyrite.services.access_policy import UNSCOPED


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test data."""
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def test_config(temp_dir):
    """Create test configuration."""
    kb_path = temp_dir / "research"
    kb_path.mkdir()
    (kb_path / "actors").mkdir()

    timeline_path = temp_dir / "timeline"
    timeline_path.mkdir()

    return PyriteConfig(
        knowledge_bases=[
            KBConfig(
                name="test-research",
                path=kb_path,
                kb_type=KBType.RESEARCH,
            ),
            KBConfig(
                name="test-timeline",
                path=timeline_path,
                kb_type=KBType.EVENTS,
            ),
        ],
        settings=Settings(index_path=temp_dir / "index.db"),
    )


@pytest.fixture
def test_db(test_config):
    """Create test database."""
    db = PyriteDB(test_config.settings.index_path)
    yield db
    db.close()


class TestSearchService:
    """Tests for SearchService."""

    @pytest.mark.parametrize(
        ("input_query", "expected"),
        [
            ("hello world", "hello world"),
            ("alex-jones", '"alex-jones"'),
            ("alex-jones 2024-01-15", '"alex-jones" "2024-01-15"'),
            ('trump AND "border wall"', 'trump AND "border wall"'),
            ('"alex-jones"', '"alex-jones"'),
            ("trump OR biden", "trump OR biden"),
            ("trump NOT fake", "trump NOT fake"),
            ("--leading-hyphen", '"--leading-hyphen"'),
            ("trailing-", '"trailing-"'),
            ("a-b-c-d", '"a-b-c-d"'),
            ("café résumé", "café résumé"),
            ("hello  world", "hello  world"),
            ("0.6 milestone", '"0.6" milestone'),
            ("test.py", '"test.py"'),
            ("user@example.com", '"user@example.com"'),
            ("path/to/file", '"path/to/file"'),
            ("tag#name", '"tag#name"'),
            ("a:b", '"a:b"'),
            ("hello! world", '"hello!" world'),
        ],
        ids=[
            "simple-passthrough",
            "hyphenated-quoted",
            "multiple-hyphens-quoted",
            "AND-operator-preserved",
            "quoted-phrase-preserved",
            "OR-operator-preserved",
            "NOT-operator-preserved",
            "leading-double-hyphen",
            "trailing-hyphen",
            "multi-hyphen-chain",
            "unicode-passthrough",
            "double-space-passthrough",
            "dot-in-number",
            "dot-in-filename",
            "at-sign-email",
            "slash-in-path",
            "hash-sign",
            "colon-separator",
            "exclamation-mark",
        ],
    )
    def test_sanitize_fts_query(self, input_query, expected):
        """FTS5 query sanitization handles special characters and operators."""
        assert SearchService.sanitize_fts_query(input_query) == expected

    @pytest.mark.parametrize(
        ("input_query", "expected"),
        [
            ("orange county florida", "orange OR county OR florida"),
            ("single", None),  # one term: nothing to relax
            ("", None),  # empty: nothing to relax
            ("trump AND biden", None),  # explicit operator: leave it alone
            ("trump OR biden", None),  # already OR: leave it alone
            ('"family separation"', None),  # quoted phrase: leave it alone
            ("a b c d", "a OR b OR c OR d"),
        ],
        ids=[
            "three-terms-to-or",
            "single-term-noop",
            "empty-noop",
            "explicit-and-noop",
            "explicit-or-noop",
            "quoted-phrase-noop",
            "four-terms-to-or",
        ],
    )
    def test_relax_to_or(self, input_query, expected):
        """_relax_to_or OR-combines bare multi-term queries, leaving
        operator/quoted/single-term queries untouched (returns None to signal
        'no relaxation applicable')."""
        assert SearchService._relax_to_or(input_query) == expected

    @pytest.mark.parametrize(
        ("input_query", "expected"),
        [
            ("alex-jones zzz", '"alex-jones" OR zzz'),
            ("pre-push selection", '"pre-push" OR selection'),
            ("section 230(c) reform", 'section OR "230(c)" OR reform'),
        ],
        ids=["hyphenated-term", "hyphenated-term-pre-push", "parenthesized-term"],
    )
    def test_relax_to_or_quotes_special_char_terms(self, input_query, expected):
        """A term with an FTS5-special character (hyphen, parens, ...) must
        be quoted before joining with " OR " -- sanitize_fts_query can't do
        this after the fact, since it skips quoting once it sees the joined
        query already contains an operator. Without per-term quoting, an
        entirely ordinary plain-word query like "pre-push selection" relaxes
        to unquoted `pre-push OR selection`, and the bare hyphenated term
        then reaches FTS5 raw and raises QUERY_SYNTAX -- blaming the user
        for a query they never wrote wrong (#361).
        """
        assert SearchService._relax_to_or(input_query) == expected

    def test_search_normalizes_all_kbs(self, test_db, test_config):
        """'All KBs' is normalized to None."""
        service = SearchService(test_db)
        # This should not raise - it normalizes the kb_name
        results = service.search("test", kb_name="All KBs")
        assert isinstance(results, list)

    @pytest.mark.parametrize(
        "query",
        ["alex-jones zzz", "section 230(c) reform", "pre-push selection"],
    )
    def test_keyword_search_zero_hits_relaxes_without_raising(self, test_db, test_config, query):
        """End-to-end, real SQLite FTS5 (no mock): a plain multi-word query
        with a hyphenated or parenthesized term finds nothing on the first
        (implicit-AND) pass, triggers the zero-hit OR-relaxation retry, and
        that retry must not itself raise QUERY_SYNTAX. Before the per-term
        quoting fix, `_relax_to_or` built an unquoted OR query
        (`alex-jones OR zzz`) that sanitize_fts_query would not touch
        (already has an operator), and the bare hyphenated/parenthesized
        term then hit SQLite raw (#361).
        """
        service = SearchService(test_db)
        # keyword mode is the only mode that calls _relax_to_or.
        results = service.search(query, mode="keyword")
        assert results == []

    def test_search_populates_trace(self, test_db, test_config):
        """A caller-supplied trace dict is filled with observability fields
        (search-observability)."""
        test_db.register_kb("research", "generic", "/tmp/research", "")
        test_db.upsert_entry(
            {
                "id": "trace-doc",
                "kb_name": "research",
                "entry_type": "note",
                "title": "Traceable widget",
                "body": "content",
                "tags": [],
            }
        )
        service = SearchService(test_db)
        trace: dict = {}
        results = service.search("widget", mode="keyword", trace=trace)

        assert trace["requested_mode"] == "keyword"
        assert trace["actual_mode"] == "keyword"
        assert trace["result_count"] == len(results)
        assert trace["query_len"] == len("widget")
        assert isinstance(trace["latency_ms"], (int, float))
        assert trace["latency_ms"] >= 0

    def test_trace_records_hybrid_keyword_fallback(self, test_db, test_config):
        """Hybrid degrades to keyword when no embeddings exist, and the trace
        records the actual mode and the reason."""
        test_db.register_kb("research", "generic", "/tmp/research", "")
        test_db.upsert_entry(
            {
                "id": "fallback-doc",
                "kb_name": "research",
                "entry_type": "note",
                "title": "Fallback widget",
                "body": "content",
                "tags": [],
            }
        )
        service = SearchService(test_db)
        trace: dict = {}
        # No embeddings in this test DB → hybrid must fall back to keyword.
        service.search("widget", mode="hybrid", trace=trace)

        assert trace["requested_mode"] == "hybrid"
        assert trace["actual_mode"] == "keyword"
        assert trace["reason"]  # a non-empty reason for the degrade

    def test_trace_records_or_relaxation(self, test_db, test_config):
        """When a 0-hit keyword query is OR-relaxed, the trace flags it."""
        test_db.register_kb("research", "generic", "/tmp/research", "")
        test_db.upsert_entry(
            {
                "id": "relax-doc",
                "kb_name": "research",
                "entry_type": "note",
                "title": "Orange county widget",
                "body": "content",
                "tags": [],
            }
        )
        service = SearchService(test_db)
        trace: dict = {}
        service.search("orange county absentterm", mode="keyword", trace=trace)
        assert trace.get("relaxed") is True

    @pytest.mark.parametrize(
        "query",
        [
            '"family separation" cross-link',  # hyphen term + phrase quote
            "cross-link NOT foo",  # hyphen term + NOT operator
            "a:b AND foo",  # colon term + AND operator
        ],
        ids=[
            "hyphen-term-plus-phrase-quote",
            "hyphen-term-plus-not-operator",
            "colon-term-plus-and-operator",
        ],
    )
    def test_search_raises_query_syntax_error_not_raw_db_error(self, test_db, test_config, query):
        """The sanitizer bypasses special-char quoting when a query contains
        an FTS5 operator or an existing quote (sanitize_fts_query's "user
        knows what they're doing" guard). A bare special-char token like
        `cross-link` then reaches FTS5 unquoted and SQLite raises a raw
        OperationalError ("no such column: ..."), because the hyphen/colon
        is parsed as column-filter syntax.

        Mixed literal+operator queries are exactly what agents write. The
        service must classify this as QuerySyntaxError (retryable=False),
        not let the raw sqlite3 exception escape — search-query-syntax-
        error-contract, same family as the fixed `links orphans` crash
        (40e7a39)."""
        test_db.register_kb("research", "generic", "/tmp/research", "")
        service = SearchService(test_db)
        with pytest.raises(QuerySyntaxError):
            service.search(query, kb_name="research", mode="keyword")

    def test_search_mode_enum(self):
        """SearchMode enum has expected values."""
        assert SearchMode.KEYWORD.value == "keyword"
        assert SearchMode.SEMANTIC.value == "semantic"
        assert SearchMode.HYBRID.value == "hybrid"

    def test_search_mode_from_string(self):
        """SearchMode can be created from string."""
        assert SearchMode("keyword") == SearchMode.KEYWORD
        assert SearchMode("semantic") == SearchMode.SEMANTIC
        assert SearchMode("hybrid") == SearchMode.HYBRID

    def test_search_with_mode_keyword(self, test_db, test_config):
        """Search with keyword mode works (default path)."""
        service = SearchService(test_db)
        results = service.search("test", mode=SearchMode.KEYWORD)
        assert isinstance(results, list)

    def test_search_with_mode_string(self, test_db, test_config):
        """Search accepts mode as string."""
        service = SearchService(test_db)
        results = service.search("test", mode="keyword")
        assert isinstance(results, list)

    def test_search_status_filter_keyword(self, test_db, test_config):
        """SearchService.search(status=...) scopes keyword results by status.

        Covers the CLI/MCP --status path end-to-end at the service tier
        (search-status-filter-cli).
        """
        test_db.register_kb("research", "generic", "/tmp/research", "")
        test_db.upsert_entry(
            {
                "id": "queue-open",
                "kb_name": "research",
                "entry_type": "note",
                "title": "Queue widget alpha",
                "body": "An open queue item.",
                "status": "unprocessed",
                "tags": [],
            }
        )
        test_db.upsert_entry(
            {
                "id": "queue-closed",
                "kb_name": "research",
                "entry_type": "note",
                "title": "Queue widget beta",
                "body": "A processed queue item.",
                "status": "processed",
                "tags": [],
            }
        )
        service = SearchService(test_db)

        both = service.search("queue", mode="keyword")
        assert {r["id"] for r in both} == {"queue-open", "queue-closed"}

        only_open = service.search("queue", mode="keyword", status="unprocessed")
        assert [r["id"] for r in only_open] == ["queue-open"]

    def test_keyword_or_relaxation_on_zero_hits(self, test_db, test_config):
        """A multi-term keyword query that returns 0 hits under implicit-AND
        retries OR-relaxed so a near-miss still surfaces
        (search-keyword-and-no-fallback)."""
        test_db.register_kb("research", "generic", "/tmp/research", "")
        test_db.upsert_entry(
            {
                "id": "orange-county-igsa",
                "kb_name": "research",
                "entry_type": "note",
                "title": "Orange County Florida IGSA terminated",
                "body": "The county ended its ICE detention agreement.",
                "tags": [],
            }
        )
        service = SearchService(test_db)

        # Implicit-AND: this exact phrase with an absent term ("quarterly")
        # would normally zero out. With OR-relaxation it should still find the
        # entry on a 0-hit retry.
        results = service.search("orange county florida quarterly absent", mode="keyword")
        assert any(r["id"] == "orange-county-igsa" for r in results), (
            "OR-relaxation should surface the near-miss entry on 0 AND-hits"
        )

    def test_keyword_no_relaxation_when_results_exist(self, test_db, test_config):
        """Relaxation only fires on 0 hits — a query that already matches under
        AND is returned as-is, not widened."""
        test_db.register_kb("research", "generic", "/tmp/research", "")
        test_db.upsert_entry(
            {
                "id": "alpha-doc",
                "kb_name": "research",
                "entry_type": "note",
                "title": "Alpha specific document",
                "body": "alpha specific content",
                "tags": [],
            }
        )
        test_db.upsert_entry(
            {
                "id": "beta-doc",
                "kb_name": "research",
                "entry_type": "note",
                "title": "Beta unrelated",
                "body": "beta only",
                "tags": [],
            }
        )
        service = SearchService(test_db)

        # "alpha specific" matches alpha-doc under AND; must NOT widen to also
        # pull beta-doc via OR.
        results = service.search("alpha specific", mode="keyword")
        assert [r["id"] for r in results] == ["alpha-doc"]

    def test_search_hybrid_fallback_no_embeddings(self, test_db, test_config):
        """Hybrid search falls back to keyword when no embeddings exist."""
        service = SearchService(test_db)
        # Hybrid should not raise, just fall back to keyword
        results = service.search("test", mode=SearchMode.HYBRID)
        assert isinstance(results, list)

    def test_search_semantic_no_deps(self, test_db, test_config):
        """Semantic search returns empty when embeddings unavailable."""
        service = SearchService(test_db)
        # Without embeddings, semantic returns empty
        results = service.search("test", mode=SearchMode.SEMANTIC)
        assert isinstance(results, list)

    def test_search_invalid_mode_falls_back(self, test_db, test_config):
        """Invalid mode string falls back to keyword."""
        service = SearchService(test_db)
        results = service.search("test", mode="invalid_mode")
        assert isinstance(results, list)

    def test_hybrid_search_offset_returns_correct_page(self, test_db, test_config):
        """Hybrid search with offset returns the correct page of fused results."""
        from unittest.mock import patch

        service = SearchService(test_db)

        # Create 20 fake keyword results
        keyword_results = [
            {"id": f"kw-{i}", "kb_name": "test", "title": f"Keyword {i}"} for i in range(20)
        ]
        # Create 20 fake semantic results (different IDs = 40 unique after fusion)
        semantic_results = [
            {"id": f"sem-{i}", "kb_name": "test", "title": f"Semantic {i}"} for i in range(20)
        ]

        with (
            patch.object(test_db, "search", return_value=keyword_results),
            patch.object(service, "_semantic_search", return_value=semantic_results),
        ):
            # Get page 1 (offset=0, limit=5)
            page1 = service.search("test", mode=SearchMode.HYBRID, limit=5, offset=0)
            # Get page 2 (offset=5, limit=5)
            page2 = service.search("test", mode=SearchMode.HYBRID, limit=5, offset=5)
            # Get page 3 (offset=10, limit=5)
            page3 = service.search("test", mode=SearchMode.HYBRID, limit=5, offset=10)

        # Pages should not overlap
        page1_ids = {r["id"] for r in page1}
        page2_ids = {r["id"] for r in page2}
        page3_ids = {r["id"] for r in page3}
        assert page1_ids.isdisjoint(page2_ids), "Page 1 and 2 should not overlap"
        assert page2_ids.isdisjoint(page3_ids), "Page 2 and 3 should not overlap"
        assert page1_ids.isdisjoint(page3_ids), "Page 1 and 3 should not overlap"

        # Each page should have 5 results (40 unique entries in pool)
        assert len(page1) == 5
        assert len(page2) == 5
        assert len(page3) == 5

    def test_hybrid_search_large_offset_fetches_enough(self, test_db, test_config):
        """Hybrid search fetches enough candidates from each leg for large offsets."""
        from unittest.mock import patch

        service = SearchService(test_db)
        offset = 20
        limit = 5

        db_call_args = {}

        def capture_db_search(**kwargs):
            db_call_args.update(kwargs)
            return [
                {"id": f"kw-{i}", "kb_name": "test", "title": f"Keyword {i}"}
                for i in range(kwargs.get("limit", 50))
            ]

        semantic_results = [
            {"id": f"sem-{i}", "kb_name": "test", "title": f"Semantic {i}"} for i in range(50)
        ]

        with (
            patch.object(test_db, "search", side_effect=capture_db_search),
            patch.object(service, "_semantic_search", return_value=semantic_results),
        ):
            results = service.search("test", mode=SearchMode.HYBRID, limit=limit, offset=offset)

        # Keyword leg should fetch enough to cover offset + limit
        keyword_limit = db_call_args["limit"]
        assert keyword_limit >= offset + limit, (
            f"Keyword leg fetched {keyword_limit} but needs >= {offset + limit} "
            f"to cover offset={offset} + limit={limit}"
        )


@pytest.mark.core
class TestKBService:
    """Tests for KBService."""

    def test_list_kbs(self, test_db, test_config):
        """list_kbs returns all configured KBs."""
        service = KBService(test_config, test_db)

        kbs = service.list_kbs()

        assert len(kbs) == 2
        names = {kb["name"] for kb in kbs}
        assert "test-research" in names
        assert "test-timeline" in names

    def test_list_kbs_includes_stats(self, test_db, test_config):
        """list_kbs includes entry counts."""
        service = KBService(test_config, test_db)

        kbs = service.list_kbs()

        for kb in kbs:
            assert "entries" in kb
            assert "indexed" in kb
            assert "type" in kb

    def test_list_kbs_includes_db_only_kb_when_no_registry(self, test_db, test_config, temp_dir):
        """A KB registered only via `pyrite kb add` (DB-only, not in
        config.yaml's knowledge_bases) must appear in list_kbs() even when
        KBService has no registry -- collapse-kb-registry-to-one-source-
        of-truth's all_kbs() sweep. list_kbs()'s non-registry fallback
        path previously iterated config.knowledge_bases directly."""
        kb_path = temp_dir / "db-only-kb"
        kb_path.mkdir()
        test_db.register_kb("db-only-kb", "generic", str(kb_path), "", source="user")
        test_db.merge_registered_kbs(test_config)

        service = KBService(test_config, test_db)  # no registry passed
        kbs = service.list_kbs()

        names = {kb["name"] for kb in kbs}
        assert "db-only-kb" in names, f"expected DB-only KB in list_kbs(); got {names}"

    def test_get_kb_found(self, test_db, test_config):
        """get_kb returns config for existing KB."""
        service = KBService(test_config, test_db)

        kb = service.get_kb("test-research")

        assert kb is not None
        assert kb.name == "test-research"

    def test_get_kb_not_found(self, test_db, test_config):
        """get_kb returns None for missing KB."""
        service = KBService(test_config, test_db)

        kb = service.get_kb("nonexistent")

        assert kb is None

    def test_create_entry_research(self, test_db, test_config):
        """create_entry creates research entry."""
        service = KBService(test_config, test_db)

        entry = service.create_entry(
            kb_name="test-research",
            entry_id="test-actor",
            title="Test Actor",
            entry_type="actor",
            body="Test body",
            tags=["test"],
        )

        assert entry.id == "test-actor"
        assert entry.title == "Test Actor"

    def test_create_entry_event(self, test_db, test_config):
        """create_entry creates event entry."""
        service = KBService(test_config, test_db)

        entry = service.create_entry(
            kb_name="test-timeline",
            entry_id="test-event",
            title="Test Event",
            entry_type="event",
            date="2024-01-15",
            importance=4,
        )

        assert entry.id == "test-event"
        assert entry.date == "2024-01-15"

    def test_create_entry_read_only_fails(self, test_db, temp_dir):
        """create_entry fails on read-only KB."""
        config = PyriteConfig(
            knowledge_bases=[
                KBConfig(
                    name="readonly-kb",
                    path=temp_dir / "readonly",
                    kb_type=KBType.RESEARCH,
                    read_only=True,
                ),
            ],
            settings=Settings(index_path=temp_dir / "index.db"),
        )
        (temp_dir / "readonly").mkdir()

        service = KBService(config, test_db)

        with pytest.raises(KBReadOnlyError, match="read-only"):
            service.create_entry(
                kb_name="readonly-kb",
                entry_id="test",
                title="Test",
                entry_type="actor",
            )

    def test_get_entry(self, test_db, test_config):
        """get_entry retrieves created entry."""
        service = KBService(test_config, test_db)

        # Create an entry
        service.create_entry(
            kb_name="test-research",
            entry_id="get-test",
            title="Get Test",
            entry_type="actor",
        )

        # Retrieve it
        entry = service.get_entry("get-test", "test-research", readable_kbs=UNSCOPED)

        assert entry is not None
        assert entry["title"] == "Get Test"

    def test_get_entry_projects_well_known_metadata_fields_to_top_level(self, test_db, test_config):
        """The read projection lifts well-known software-kb metadata fields
        (rank, effort, kind) to the top level. Regression for Tier A 1200:
        `pyrite get` silently omitted `rank` from JSON even though it was in
        the index's metadata column. The conductor's rank-aware grooming
        scripts and the cascade-cluster sequencing depend on this.
        """
        service = KBService(test_config, test_db)

        service.create_entry(
            kb_name="test-research",
            entry_id="rank-projection-test",
            title="Rank Projection Test",
            entry_type="actor",
            # These three are software-kb fields stored in metadata; they
            # used to silently disappear from the read projection.
            rank=1200,
            effort="S",
            kind="bug",
        )

        entry = service.get_entry("rank-projection-test", "test-research", readable_kbs=UNSCOPED)

        assert entry is not None
        # Lifted fields — primary regression assertions.
        assert entry.get("rank") == 1200, (
            f"rank missing from get_entry projection (Tier A 1200); got keys {sorted(entry.keys())}"
        )
        assert entry.get("effort") == "S", "effort missing from projection"
        assert entry.get("kind") == "bug", "kind missing from projection"
        # The full metadata bag is still nested for callers that need it.
        assert isinstance(entry.get("metadata"), dict)

    def test_get_entry_searches_all_kbs(self, test_db, test_config):
        """get_entry without kb_name searches all KBs."""
        service = KBService(test_config, test_db)

        service.create_entry(
            kb_name="test-research",
            entry_id="search-all-test",
            title="Search All Test",
            entry_type="actor",
        )

        # Search without specifying KB
        entry = service.get_entry("search-all-test", readable_kbs=UNSCOPED)

        assert entry is not None
        assert entry["title"] == "Search All Test"

    def test_get_entry_searches_db_only_kb(self, test_db, test_config, temp_dir):
        """get_entry() without kb_name must also search KBs registered only
        via `pyrite kb add` (DB-only, not in config.yaml's
        knowledge_bases) -- collapse-kb-registry-to-one-source-of-truth's
        all_kbs() sweep. The "search all KBs" fallback previously iterated
        config.knowledge_bases directly, silently skipping DB-only KBs."""
        kb_path = temp_dir / "db-only-kb"
        kb_path.mkdir()
        test_db.register_kb("db-only-kb", "generic", str(kb_path), "", source="user")
        test_db.merge_registered_kbs(test_config)

        service = KBService(test_config, test_db)
        service.create_entry(
            kb_name="db-only-kb",
            entry_id="db-only-search-test",
            title="DB-only Search Test",
            entry_type="note",
        )

        entry = service.get_entry("db-only-search-test", readable_kbs=UNSCOPED)

        assert entry is not None, "expected the entry to be found via the all-KBs fallback"
        assert entry["title"] == "DB-only Search Test"

    def test_delete_entry(self, test_db, test_config):
        """delete_entry removes entry from file and index."""
        service = KBService(test_config, test_db)

        service.create_entry(
            kb_name="test-research",
            entry_id="delete-test",
            title="Delete Test",
            entry_type="actor",
        )

        result = service.delete_entry("delete-test", "test-research")

        assert result is True
        assert service.get_entry("delete-test", "test-research", readable_kbs=UNSCOPED) is None


class TestQueryExpansionService:
    """Tests for QueryExpansionService."""

    def test_stub_provider_returns_empty(self):
        """Stub provider returns empty list."""
        svc = QueryExpansionService(provider="stub")
        assert svc.expand("immigration policy") == []

    def test_none_provider_returns_empty(self):
        """None provider returns empty list."""
        svc = QueryExpansionService(provider="none")
        assert svc.expand("immigration policy") == []

    def test_empty_query_returns_empty(self):
        """Empty query returns empty list."""
        svc = QueryExpansionService(provider="anthropic")
        assert svc.expand("") == []
        assert svc.expand("   ") == []

    def test_unavailable_provider_returns_empty(self):
        """Unavailable/unknown provider returns empty list."""
        svc = QueryExpansionService(provider="nonexistent_provider_xyz")
        assert svc.expand("immigration policy") == []

    def test_is_available_stub(self):
        """is_available returns True for stub/none."""
        assert is_available("stub") is True
        assert is_available("none") is True
        assert is_available("") is True

    def test_is_available_unknown(self):
        """is_available returns False for unknown provider."""
        assert is_available("nonexistent_provider_xyz") is False

    def test_parse_terms_basic(self):
        """_parse_terms handles basic multi-line output."""
        terms = QueryExpansionService._parse_terms("term one\nterm two\nterm three")
        assert terms == ["term one", "term two", "term three"]

    def test_parse_terms_strips_bullets(self):
        """_parse_terms strips bullet/numbering prefixes."""
        terms = QueryExpansionService._parse_terms("- term one\n1. term two\n* term three")
        assert terms == ["term one", "term two", "term three"]

    def test_parse_terms_respects_max(self):
        """_parse_terms limits to MAX_TERMS."""
        lines = "\n".join(f"term {i}" for i in range(20))
        terms = QueryExpansionService._parse_terms(lines)
        assert len(terms) <= 10

    def test_parse_terms_filters_long(self):
        """_parse_terms skips terms longer than MAX_TERM_LENGTH."""
        terms = QueryExpansionService._parse_terms("short\n" + "x" * 100)
        assert terms == ["short"]

    def test_search_with_expand_stub_works(self, test_db, test_config):
        """SearchService with expand=True + stub provider works (no-op expansion)."""
        service = SearchService(test_db, settings=test_config.settings)
        results = service.search("test", expand=True)
        assert isinstance(results, list)

    def test_search_expand_without_settings(self, test_db):
        """SearchService with expand=True but no settings returns normal results."""
        service = SearchService(test_db)
        results = service.search("test", expand=True)
        assert isinstance(results, list)
