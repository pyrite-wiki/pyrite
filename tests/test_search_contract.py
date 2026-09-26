"""Search text a caller didn't write as a query always parses, and every search
failure keeps the error contract (#431).

Two halves over the same modules:

- **Query building.** FTS5 operators are uppercase only, so a lowercase
  ``and``/``or``/``not`` is a plain word and must not switch off auto-quoting.
  Text a feature *derives* a search from -- an entry title, a chat message --
  is built into quoted OR terms, so it cannot fail to parse, and the semantic
  leg is given that text, not the OR string.
- **Error contract.** Every sqlite failure from either leg is classified: a
  parse error the caller caused is ``QuerySyntaxError`` (400 / QUERY_SYNTAX);
  anything else is ``StorageError`` (a logged 500 with a ``{code, message}``
  body on REST, the structured envelope on MCP). Only a transient fault
  (locked / busy) is retryable.

Parsing and classification claims run against real SQLite FTS5 and real
sqlite3 exception instances (with their ``sqlite_errorcode``), not strings in
a mock.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from pyrite.exceptions import QuerySyntaxError, StorageError
from pyrite.services.link_discovery_service import LinkDiscoveryService
from pyrite.services.search_service import MAX_SEARCH_QUERY_LENGTH, SearchService
from pyrite.services.access_policy import UNSCOPED

ENGLISH = "how do pre-push hooks and CI interact?"


# ---------------------------------------------------------------------------
# Real sqlite3 errors, raised by SQLite itself
# ---------------------------------------------------------------------------


def _real_locked_error(tmp_path) -> sqlite3.OperationalError:
    """SQLITE_BUSY: a read while another connection holds an exclusive lock."""
    path = tmp_path / "locked.db"
    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute("CREATE TABLE t (x)")
    holder.execute("BEGIN EXCLUSIVE")
    reader = sqlite3.connect(path, timeout=0)
    try:
        reader.execute("SELECT * FROM t").fetchall()
    except sqlite3.OperationalError as exc:
        return exc
    finally:
        reader.close()
        holder.execute("ROLLBACK")
        holder.close()
    raise AssertionError("the read was not blocked")


def _real_not_a_database_error(tmp_path) -> sqlite3.DatabaseError:
    """SQLITE_NOTADB: what a corrupt index file raises."""
    path = tmp_path / "corrupt.db"
    path.write_bytes(b"this is not a sqlite database" * 64)
    con = sqlite3.connect(path)
    try:
        with pytest.raises(sqlite3.DatabaseError) as excinfo:
            con.execute("SELECT 1 FROM sqlite_master").fetchall()
    finally:
        con.close()
    # The base type, not OperationalError: the case _db_search used to miss.
    assert type(excinfo.value) is sqlite3.DatabaseError
    return excinfo.value


def _real_missing_table_error() -> sqlite3.OperationalError:
    con = sqlite3.connect(":memory:")
    try:
        con.execute("SELECT * FROM vec_entries").fetchall()
    except sqlite3.OperationalError as exc:
        return exc
    finally:
        con.close()
    raise AssertionError("the table exists")


def _fts_accepts(query: str) -> None:
    """Run ``query`` through a real FTS5 MATCH; raise if it does not parse."""
    con = sqlite3.connect(":memory:")
    try:
        con.execute("CREATE VIRTUAL TABLE t USING fts5(title, body)")
        con.execute("SELECT * FROM t WHERE t MATCH ?", (query,)).fetchall()
    finally:
        con.close()


def _raise(exc):
    def _raiser(*args, **kwargs):
        raise exc

    return _raiser


@pytest.fixture
def fake_embeddings(monkeypatch):
    """Let the semantic leg run without a model: record what it is asked for."""
    from pyrite.services.embedding_service import EmbeddingService

    calls: list[str] = []

    def _search_similar(self, query, **kwargs):
        calls.append(query)
        return []

    monkeypatch.setattr(EmbeddingService, "has_embeddings", lambda self: True)
    monkeypatch.setattr(EmbeddingService, "search_similar", _search_similar)
    return calls


@pytest.fixture
def mcp_server(indexed_test_env):
    from pyrite.server.mcp_server import PyriteMCPServer

    server = PyriteMCPServer(indexed_test_env["config"], tier="read")
    yield server
    server.close()


# ---------------------------------------------------------------------------
# 1. Lowercase and/or/not are plain words
# ---------------------------------------------------------------------------


class TestLowercaseOperatorsArePlainWords:
    def test_sanitized_english_parses_in_fts5(self):
        _fts_accepts(SearchService.sanitize_fts_query(ENGLISH))

    @pytest.mark.parametrize("word", ["and", "or", "not"])
    def test_a_lowercase_operator_word_does_not_switch_off_quoting(self, word):
        sanitized = SearchService.sanitize_fts_query(f"pre-push {word} ci")
        assert '"pre-push"' in sanitized
        _fts_accepts(sanitized)

    @pytest.mark.control(
        reason="uppercase operators pass through on dev too; pins that the case-sensitive check keeps them"
    )
    @pytest.mark.parametrize("op", ["AND", "OR", "NOT"])
    def test_an_uppercase_operator_still_passes_through(self, op):
        query = f'alex {op} "not-here"'
        assert SearchService.sanitize_fts_query(query) == query

    @pytest.mark.parametrize("word", ["and", "or", "not"])
    def test_relax_to_or_quotes_terms_next_to_a_lowercase_operator_word(self, word):
        relaxed = SearchService._relax_to_or(f"pre-push {word} ci")
        assert relaxed is not None
        assert '"pre-push"' in relaxed
        _fts_accepts(relaxed)

    def test_the_service_runs_it_against_the_real_index(self, indexed_test_env):
        assert isinstance(SearchService(indexed_test_env["db"]).search(ENGLISH), list)

    def test_rest(self, rest_api_env):
        resp = rest_api_env["client"].get("/api/search", params={"q": ENGLISH})
        assert resp.status_code == 200, resp.json()

    def test_mcp(self, mcp_server):
        result = mcp_server._dispatch_tool("kb_search", {"query": ENGLISH, "mode": "keyword"})
        assert "error" not in result, result

    @pytest.mark.cli
    def test_cli(self, indexed_test_env):
        from pyrite.cli import app

        config = indexed_test_env["config"]
        with (
            patch("pyrite.cli.context.load_config", return_value=config),
            patch("pyrite.cli.search_commands.load_config", return_value=config),
        ):
            result = CliRunner().invoke(app, ["search", ENGLISH, "--format", "json"])
        assert result.exit_code == 0, result.output
        assert "error_code" not in json.loads(result.output)


# ---------------------------------------------------------------------------
# 2. Derived queries: short words, stop words
# ---------------------------------------------------------------------------


class TestBuildSuggestQuery:
    def test_two_letter_words_are_kept(self):
        query = LinkDiscoveryService.build_suggest_query({"title": "AI UX notes"})
        assert '"AI"' in query
        assert '"UX"' in query

    def test_lowercase_stop_words_are_dropped(self):
        query = LinkDiscoveryService.build_suggest_query({"title": "the state of it and AI"})
        assert query == '"state" OR "AI"'

    def test_an_all_caps_stop_word_is_an_acronym_and_kept(self):
        query = LinkDiscoveryService.build_suggest_query({"title": "US IT policy"})
        assert query == '"US" OR "IT" OR "policy"'

    @pytest.mark.control(
        reason="unchanged behaviour: single characters were already dropped (by the old <=2 rule)"
    )
    def test_a_nul_in_a_tag_cannot_break_the_query(self):
        """FTS5 ends a quoted string at a NUL, so a NUL inside a quoted tag is
        "unterminated string" (#437 cold read)."""
        query = LinkDiscoveryService.build_suggest_query({"title": "", "tags": ["a\x00b"]})
        assert "\x00" not in query
        _fts_accepts(query)

    def test_a_tag_that_is_only_nul_adds_nothing(self):
        assert LinkDiscoveryService.build_suggest_query({"title": "", "tags": ["\x00"]}) == ""

    def test_single_letters_are_still_dropped(self):
        assert LinkDiscoveryService.build_suggest_query({"title": "Q&A"}) == ""


# ---------------------------------------------------------------------------
# 3. Chat retrieval is built from the message's words and cannot fail to parse
# ---------------------------------------------------------------------------


def _chat(client, content):
    resp = client.post(
        "/api/ai/chat",
        json={"messages": [{"role": "user", "content": content}], "kb": "test-events"},
    )
    assert resp.status_code == 200
    events = [json.loads(line[6:]) for line in resp.text.splitlines() if line.startswith("data: ")]
    return next((e["entries"] for e in events if e["type"] == "sources"), [])


@pytest.fixture
def chat_env(rest_api_env):
    from pyrite.server.api import get_llm_service

    llm = MagicMock()
    llm.status.return_value = {"configured": True, "provider": "mock", "model": "m"}

    async def _stream(prompt, system=None):
        yield "ok"

    llm.stream = _stream
    rest_api_env["client"].app.dependency_overrides[get_llm_service] = lambda: llm
    return rest_api_env


NUMBERED = "1) first thing 2) second thing ".ljust(MAX_SEARCH_QUERY_LENGTH + 50, "z")


class TestChatRetrieval:
    @pytest.mark.parametrize(
        ("message", "word"), [(NUMBERED, "second"), ("thanks :)", "thanks")], ids=["clip", "smile"]
    )
    def test_the_search_is_over_the_messages_words(self, chat_env, message, word):
        from pyrite.server.api import get_search_service

        mock_svc = MagicMock()
        mock_svc.search.return_value = []
        app = chat_env["client"].app
        app.dependency_overrides[get_search_service] = lambda: mock_svc
        _chat(chat_env["client"], message)
        assert mock_svc.search.call_args.kwargs["query"].count(f'"{word}"') == 1

    @pytest.mark.parametrize(
        ("message", "word"),
        [
            pytest.param(NUMBERED, "second", id="clip"),
            pytest.param(
                "thanks :)",
                "thanks",
                id="smile",
                marks=pytest.mark.control(
                    reason="dev's sanitizer quoted ':)' and FTS5 dropped it, so this "
                    "retrieved on dev too; the search-query test above is its red case"
                ),
            ),
        ],
    )
    def test_sources_come_back_from_a_kb_holding_the_words(self, chat_env, message, word):
        from pyrite.services.kb_service import KBService

        KBService(chat_env["config"], chat_env["db"]).create_entry(
            "test-events", f"note-{word}", f"A {word} note", "note", body=f"{word} here"
        )
        sources = _chat(chat_env["client"], message)
        assert f"note-{word}" in [s["id"] for s in sources]

    def test_a_storage_fault_before_the_keyword_retry_is_logged(self, chat_env, caplog):
        """The keyword retry recovers, but the fault it recovered from is the
        server's problem and must not vanish (#437 cold read)."""
        from pyrite.server.api import get_search_service

        mock_svc = MagicMock()
        mock_svc.search.side_effect = [StorageError("Search failed: database is locked"), []]
        chat_env["client"].app.dependency_overrides[get_search_service] = lambda: mock_svc
        with caplog.at_level(logging.WARNING):
            _chat(chat_env["client"], "border policy")
        assert mock_svc.search.call_count == 2
        warned = [r for r in caplog.records if r.levelno == logging.WARNING and r.exc_info]
        assert len(warned) == 1, [r.getMessage() for r in caplog.records]
        assert isinstance(warned[0].exc_info[1], StorageError)

    def test_a_message_that_is_invalid_fts5_still_retrieves(self, chat_env, caplog):
        from pyrite.services.kb_service import KBService

        KBService(chat_env["config"], chat_env["db"]).create_entry(
            "test-events", "big-lie", "The Big Lie", "note", body="lie"
        )
        with caplog.at_level(logging.ERROR):
            sources = _chat(chat_env["client"], 'The "Big Lie and e-mail AND x:y (')
        assert "big-lie" in [s["id"] for s in sources]
        assert "RAG search failed" not in caplog.text


# ---------------------------------------------------------------------------
# 4. The semantic leg is given the text, not the OR query
# ---------------------------------------------------------------------------


class TestSemanticLegText:
    def test_search_passes_semantic_query_to_the_vector_leg(
        self, indexed_test_env, fake_embeddings
    ):
        svc = SearchService(indexed_test_env["db"])
        svc.search('"border" OR "policy"', semantic_query="border policy", mode="hybrid")
        assert fake_embeddings == ["border policy"]

    def test_semantic_mode_uses_it_too(self, indexed_test_env, fake_embeddings):
        svc = SearchService(indexed_test_env["db"])
        svc.search('"border" OR "policy"', semantic_query="border policy", mode="semantic")
        assert fake_embeddings == ["border policy"]

    @pytest.mark.control(
        reason="the default is unchanged: without semantic_query both legs get the query, as on dev"
    )
    def test_without_it_both_legs_get_the_query(self, indexed_test_env, fake_embeddings):
        SearchService(indexed_test_env["db"]).search("border policy", mode="hybrid")
        assert fake_embeddings == ["border policy"]

    def test_an_over_long_semantic_query_is_refused(self, indexed_test_env):
        from pyrite.exceptions import QueryTooLongError

        with pytest.raises(QueryTooLongError):
            SearchService(indexed_test_env["db"]).search(
                "x", semantic_query="y" * (MAX_SEARCH_QUERY_LENGTH + 1), mode="hybrid"
            )

    def test_suggest_links_logs_a_fault_before_the_keyword_retry(self, ai_env_real, caplog):
        from pyrite.server.api import get_search_service

        mock_svc = MagicMock()
        mock_svc.search.side_effect = [StorageError("Search failed: database is locked"), []]
        ai_env_real["client"].app.dependency_overrides[get_search_service] = lambda: mock_svc
        with caplog.at_level(logging.WARNING):
            resp = ai_env_real["client"].post(
                "/api/ai/suggest-links",
                json={"entry_id": "2025-01-10--test-event-0", "kb_name": "test-events"},
            )
        assert resp.status_code == 200, resp.json()
        assert mock_svc.search.call_count == 2
        warned = [r for r in caplog.records if r.levelno == logging.WARNING and r.exc_info]
        assert len(warned) == 1, [r.getMessage() for r in caplog.records]
        assert isinstance(warned[0].exc_info[1], StorageError)

    def test_suggest_links_embeds_the_title(self, ai_env_real, fake_embeddings):
        resp = ai_env_real["client"].post(
            "/api/ai/suggest-links",
            json={"entry_id": "2025-01-10--test-event-0", "kb_name": "test-events"},
        )
        assert resp.status_code == 200, resp.json()
        title = ai_env_real["db"].get_entry("2025-01-10--test-event-0", "test-events")["title"]
        assert fake_embeddings == [title]

    def test_chat_embeds_the_message(self, chat_env, fake_embeddings):
        _chat(chat_env["client"], "what about border AND e-mail policy?")
        assert fake_embeddings == ["what about border AND e-mail policy?"]

    def test_chat_embeds_a_bounded_prefix_of_a_long_message(self, chat_env, fake_embeddings):
        _chat(chat_env["client"], NUMBERED)
        assert len(fake_embeddings) == 1
        assert len(fake_embeddings[0]) <= MAX_SEARCH_QUERY_LENGTH
        assert fake_embeddings[0].startswith("1) first thing 2) second thing")

    @pytest.mark.control(
        reason="semantic mode already embedded title+summary on dev; pins that the keyword/semantic split keeps it (mutation G11b)"
    )
    def test_discover_neighbors_semantic_embeds_title_and_summary(
        self, indexed_test_env, fake_embeddings
    ):
        from pyrite.services.kb_service import KBService

        config, db = indexed_test_env["config"], indexed_test_env["db"]
        KBService(config, db).create_entry(
            "test-events", "sem", "Border (policy", "note", body="x", summary="A summary"
        )
        LinkDiscoveryService(config, db).discover_neighbors(
            "sem", "test-events", mode="semantic", readable_kbs=UNSCOPED
        )
        assert fake_embeddings == ["Border (policy A summary"]

    def test_discover_neighbors_embeds_tags_as_text_when_there_is_no_title(
        self, indexed_test_env, fake_embeddings
    ):
        """An indexed entry with no title (create_entry refuses one, a file on
        disk need not) still has tags: the semantic leg embeds them as words,
        not as the OR-joined keyword query."""
        from pyrite.services.kb_service import KBService

        config, db = indexed_test_env["config"], indexed_test_env["db"]
        entry = {"id": "tagged", "title": "", "summary": "", "tags": ["border", "asylum-policy"]}
        with patch.object(KBService, "get_entry", return_value=entry):
            LinkDiscoveryService(config, db).discover_neighbors(
                "tagged", "test-events", mode="hybrid", readable_kbs=UNSCOPED
            )
        assert fake_embeddings == ["border asylum-policy"]

    def test_discover_neighbors_hybrid_parses_and_embeds_the_title(
        self, indexed_test_env, fake_embeddings
    ):
        from pyrite.services.kb_service import KBService

        config, db = indexed_test_env["config"], indexed_test_env["db"]
        title = 'The "Big Lie and e-mail'
        KBService(config, db).create_entry("test-events", "big-lie", title, "note", body="x")
        LinkDiscoveryService(config, db).discover_neighbors(
            "big-lie", "test-events", mode="hybrid", readable_kbs=UNSCOPED
        )
        assert fake_embeddings == [title]


@pytest.fixture
def ai_env_real(rest_api_env):
    from pyrite.server.api import get_llm_service

    llm = MagicMock()
    llm.status.return_value = {"configured": True, "provider": "mock", "model": "m"}

    async def _complete(*args, **kwargs):
        return "[]"

    llm.complete = _complete
    rest_api_env["client"].app.dependency_overrides[get_llm_service] = lambda: llm
    return rest_api_env


# ---------------------------------------------------------------------------
# 5. Classification: whose fault, and is it retryable
# ---------------------------------------------------------------------------


class TestClassification:
    def test_a_quoted_dotted_column_filter_is_the_callers_fault(self, indexed_test_env):
        with pytest.raises(QuerySyntaxError):
            SearchService(indexed_test_env["db"]).search('x AND "a.b":y')

    def test_rest_answers_it_400(self, rest_api_env):
        resp = rest_api_env["client"].get("/api/search", params={"q": 'x AND "a.b":y'})
        assert resp.status_code == 400, resp.json()
        assert resp.json()["detail"]["code"] == "QUERY_SYNTAX"

    def test_a_missing_column_the_query_does_not_name_is_storage(self):
        from pyrite.services.search_service import _looks_like_query_syntax_error

        assert not _looks_like_query_syntax_error("no such column: e.fips", "fips AND x")
        assert not _looks_like_query_syntax_error("no such column: fips", "hello AND x")
        assert _looks_like_query_syntax_error("no such column: party", "a AND third-party")
        # A whole token or a separator-delimited piece, not any substring.
        assert not _looks_like_query_syntax_error("no such column: fips", "fipsy AND x")
        assert not _looks_like_query_syntax_error("no such column: fips", "e.fips AND x")
        # An empty name would match between any two separators.
        assert not _looks_like_query_syntax_error("no such column: ", "a  b")

    def test_corruption_is_a_storage_error(self, indexed_test_env, tmp_path, monkeypatch):
        db = indexed_test_env["db"]
        monkeypatch.setattr(db, "search", _raise(_real_not_a_database_error(tmp_path)))
        with pytest.raises(StorageError) as excinfo:
            SearchService(db).search("hello")
        assert excinfo.value.retryable is False

    # Retryability is judged on MCP's envelope, the surface that reports it.

    def test_a_real_lock_is_retryable(self, mcp_server, tmp_path, monkeypatch):
        assert _mcp_retryable(mcp_server, monkeypatch, _real_locked_error(tmp_path)) is True

    @pytest.mark.parametrize(
        "text", ["database is locked", "database table is locked", "database is busy"]
    )
    def test_a_lock_message_without_an_error_code_is_retryable(self, mcp_server, monkeypatch, text):
        """A driver or wrapper that loses ``sqlite_errorcode`` still keeps the text."""
        error = sqlite3.OperationalError(text)
        assert _mcp_retryable(mcp_server, monkeypatch, error) is True

    def test_an_extended_busy_code_is_retryable(self, mcp_server, monkeypatch):
        error = sqlite3.OperationalError("cannot start a transaction")
        error.sqlite_errorcode = sqlite3.SQLITE_BUSY_SNAPSHOT
        assert _mcp_retryable(mcp_server, monkeypatch, error) is True

    @pytest.mark.control(
        reason="nothing is retryable on dev; pins that sqlite_errorcode, when "
        "present, decides over lock-like text (mutation G4c)"
    )
    def test_the_error_code_wins_over_the_text(self, mcp_server, monkeypatch):
        error = sqlite3.OperationalError("no such table: database is locked")
        error.sqlite_errorcode = sqlite3.SQLITE_ERROR
        assert _mcp_retryable(mcp_server, monkeypatch, error) is False

    @pytest.mark.control(
        reason="nothing is retryable on dev; pins that only a lock becomes "
        "retryable -- the earlier all-StorageError-retryable attempt was reverted"
    )
    def test_a_missing_table_is_not_retryable(self, mcp_server, monkeypatch):
        assert _mcp_retryable(mcp_server, monkeypatch, _real_missing_table_error()) is False

    def test_a_semantic_leg_failure_is_a_storage_error(self, indexed_test_env, monkeypatch):
        from pyrite.services.embedding_service import EmbeddingService

        monkeypatch.setattr(EmbeddingService, "has_embeddings", lambda self: True)
        monkeypatch.setattr(EmbeddingService, "search_similar", _raise(_real_missing_table_error()))
        with pytest.raises(StorageError):
            SearchService(indexed_test_env["db"]).search("hello", mode="semantic")

    def test_a_semantic_leg_lock_is_retryable(self, indexed_test_env, tmp_path, monkeypatch):
        from pyrite.services.embedding_service import EmbeddingService

        monkeypatch.setattr(
            EmbeddingService, "has_embeddings", _raise(_real_locked_error(tmp_path))
        )
        with pytest.raises(StorageError) as excinfo:
            SearchService(indexed_test_env["db"]).search("hello", mode="hybrid")
        assert "database is locked" in str(excinfo.value)
        assert excinfo.value.retryable is True

    def test_a_semantic_leg_lock_is_retryable_on_mcp(self, mcp_server, tmp_path, monkeypatch):
        """On dev this escaped as a non-PyriteError: INTERNAL, retryable by accident."""
        from pyrite.services.embedding_service import EmbeddingService

        monkeypatch.setattr(
            EmbeddingService, "has_embeddings", _raise(_real_locked_error(tmp_path))
        )
        result = mcp_server._dispatch_tool("kb_search", {"query": "hello", "mode": "hybrid"})
        assert result["error_code"] == "STORAGE_ERROR", result
        assert result["legacy_error_code"] == "REQUEST_REFUSED", result
        assert result["retryable"] is True


# ---------------------------------------------------------------------------
# 6. Surfaces: REST body and log, MCP envelope and log
# ---------------------------------------------------------------------------


def _mcp_retryable(server, monkeypatch, error) -> bool:
    monkeypatch.setattr(server.db, "search", _raise(error))
    result = server._dispatch_tool("kb_search", {"query": "hello", "mode": "keyword"})
    assert result["error_code"] == "STORAGE_ERROR", result
    assert result["legacy_error_code"] == "REQUEST_REFUSED", result
    return result["retryable"]


def _error_records(caplog):
    return [r for r in caplog.records if r.levelno >= logging.ERROR]


class TestRestContract:
    def _get(self, rest_api_env, caplog, **params):
        with caplog.at_level(logging.ERROR):
            return rest_api_env["client"].get("/api/search", params={"q": "hello", **params})

    def test_corruption_is_a_json_500_logged_with_a_traceback(
        self, rest_api_env, tmp_path, monkeypatch, caplog
    ):
        monkeypatch.setattr(
            rest_api_env["db"], "search", _raise(_real_not_a_database_error(tmp_path))
        )
        resp = self._get(rest_api_env, caplog)
        assert resp.status_code == 500
        assert resp.json()["detail"]["code"] == "STORAGE_ERROR"
        # ADR-0037 theme 2 fix round 1: StorageError has a fixed, safe
        # public_message now (ADR §3) -- the real detail ("file is not a
        # database", which could be considered server-internal) no longer
        # reaches the response body; it still reaches the server log below,
        # with a traceback.
        assert resp.json()["detail"]["message"] == (
            "A storage operation failed. An administrator needs to check the "
            "server log for the underlying error."
        )
        records = _error_records(caplog)
        assert len(records) == 1, [r.getMessage() for r in records]
        assert records[0].exc_info and records[0].exc_info[0] is not None
        assert "file is not a database" in records[0].getMessage()

    def test_a_semantic_leg_failure_is_a_json_500_logged_with_a_traceback(
        self, rest_api_env, monkeypatch, caplog
    ):
        from pyrite.services.embedding_service import EmbeddingService

        monkeypatch.setattr(EmbeddingService, "has_embeddings", lambda self: True)
        monkeypatch.setattr(EmbeddingService, "search_similar", _raise(_real_missing_table_error()))
        resp = self._get(rest_api_env, caplog, mode="semantic")
        assert resp.status_code == 500
        assert set(resp.json()) == {"detail"}
        assert set(resp.json()["detail"]) >= {"code", "message", "retryable"}
        assert resp.json()["detail"]["code"] == "STORAGE_ERROR"
        records = _error_records(caplog)
        assert len(records) == 1, [r.getMessage() for r in records]
        assert records[0].exc_info and records[0].exc_info[0] is not None


class TestMcpContract:
    def _search(self, server, caplog, mode="keyword"):
        with caplog.at_level(logging.ERROR):
            return server._dispatch_tool("kb_search", {"query": "hello", "mode": mode})

    def test_a_lock_is_retryable_and_logged(self, mcp_server, tmp_path, monkeypatch, caplog):
        monkeypatch.setattr(mcp_server.db, "search", _raise(_real_locked_error(tmp_path)))
        result = self._search(mcp_server, caplog)
        assert result["retryable"] is True, result
        assert result["error_code"] == "STORAGE_ERROR"
        assert result["legacy_error_code"] == "REQUEST_REFUSED"
        records = _error_records(caplog)
        assert len(records) == 1, [r.getMessage() for r in records]
        assert records[0].exc_info and records[0].exc_info[0] is not None

    def test_corruption_is_not_retryable_and_logged(
        self, mcp_server, tmp_path, monkeypatch, caplog
    ):
        monkeypatch.setattr(mcp_server.db, "search", _raise(_real_not_a_database_error(tmp_path)))
        result = self._search(mcp_server, caplog)
        assert result["retryable"] is False, result
        assert result["error_code"] == "STORAGE_ERROR"
        assert result["legacy_error_code"] == "REQUEST_REFUSED"
        records = _error_records(caplog)
        assert len(records) == 1, [r.getMessage() for r in records]
        assert records[0].exc_info and records[0].exc_info[0] is not None

    def test_a_semantic_leg_failure_is_the_structured_envelope(
        self, mcp_server, monkeypatch, caplog
    ):
        from pyrite.services.embedding_service import EmbeddingService

        monkeypatch.setattr(EmbeddingService, "has_embeddings", lambda self: True)
        monkeypatch.setattr(EmbeddingService, "search_similar", _raise(_real_missing_table_error()))
        result = self._search(mcp_server, caplog, mode="semantic")
        assert result["error_code"] == "STORAGE_ERROR", result
        assert result["legacy_error_code"] == "REQUEST_REFUSED", result
        assert result["retryable"] is False
        # ADR-0037 theme 2 fix round 1: StorageError has a fixed, safe
        # public_message now (ADR §3) -- the real detail ("no such table")
        # no longer reaches the MCP envelope; it still reaches the server
        # log (checked below), with a traceback.
        assert result["error"] == (
            "A storage operation failed. An administrator needs to check the "
            "server log for the underlying error."
        )
        records = _error_records(caplog)
        assert any("no such table" in r.getMessage() for r in records), [
            r.getMessage() for r in records
        ]

    @pytest.mark.parametrize(
        ("query", "code"),
        [
            ('x AND "a.b":y', "QUERY_SYNTAX"),
            pytest.param(
                "x" * (MAX_SEARCH_QUERY_LENGTH + 1),
                "QUERY_TOO_LONG",
                marks=pytest.mark.control(
                    reason="dev logged no refusal at all; pins that only a StorageError "
                    "is logged by the dispatcher (mutation G9c)"
                ),
            ),
        ],
        ids=["syntax", "too-long"],
    )
    def test_a_refusal_that_is_not_a_storage_fault_is_not_logged(
        self, mcp_server, caplog, query, code
    ):
        """QUERY_TOO_LONG reaches the dispatcher's PyriteError branch, where
        only a StorageError is logged; QUERY_SYNTAX is answered by kb_search."""
        with caplog.at_level(logging.ERROR):
            result = mcp_server._dispatch_tool("kb_search", {"query": query})
        assert result["error_code"] == code
        assert result["retryable"] is False
        assert _error_records(caplog) == []
