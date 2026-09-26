"""A search query has a maximum length, refused explicitly on every surface.

Sanitizing a query for FTS5 costs time that grows with the query. The cap is
what bounds it: it is checked before the regex and before the operator/quote
short-circuit, and a query over it is refused -- never truncated -- with the
same code on every surface (422 on REST, a validation error on MCP, a clear
CLI error). A query exactly at the cap still searches.

Internal callers that *derive* a query from stored content (link discovery,
AI chat retrieval, AI link suggestions) are not the caller's query, so they
keep their derived queries within the cap instead of being refused.
"""

import json
import re
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from pyrite.exceptions import QueryTooLongError, ValidationError
from pyrite.services.search_service import MAX_SEARCH_QUERY_LENGTH, SearchService
from pyrite.services.access_policy import UNSCOPED

AT_CAP = "immigration".ljust(MAX_SEARCH_QUERY_LENGTH)
OVER_CAP = "immigration".ljust(MAX_SEARCH_QUERY_LENGTH + 1)


class _NoDB:
    """A db the guard must never reach."""

    def search(self, **kwargs):  # pragma: no cover - reaching it is the failure
        raise AssertionError("an over-long query reached the database")


# ---------------------------------------------------------------------------
# The service: the cap and where it is checked
# ---------------------------------------------------------------------------


class TestSanitizeCap:
    def test_cap_is_a_sane_fixed_constant(self):
        assert MAX_SEARCH_QUERY_LENGTH == 1000

    def test_the_error_is_a_validation_error_with_its_own_code(self):
        assert issubclass(QueryTooLongError, ValidationError)
        assert QueryTooLongError.error_code == "QUERY_TOO_LONG"

    def test_over_cap_is_refused_before_the_regex_runs(self):
        with patch.object(re, "sub", side_effect=AssertionError("regex ran")) as spy:
            with pytest.raises(QueryTooLongError) as excinfo:
                SearchService.sanitize_fts_query("a-" + "x" * MAX_SEARCH_QUERY_LENGTH)
        spy.assert_not_called()
        assert str(MAX_SEARCH_QUERY_LENGTH) in str(excinfo.value)

    @pytest.mark.parametrize("marker", [" AND ", " OR ", " NOT ", '"'])
    def test_over_cap_is_refused_before_the_operator_short_circuit(self, marker):
        query = ("alpha" + marker + "beta ").ljust(MAX_SEARCH_QUERY_LENGTH + 1, "z")
        with pytest.raises(QueryTooLongError):
            SearchService.sanitize_fts_query(query)

    def test_at_cap_is_sanitized(self):
        query = "alex-jones".ljust(MAX_SEARCH_QUERY_LENGTH)
        assert SearchService.sanitize_fts_query(query).startswith('"alex-jones"')

    def test_a_very_large_query_is_refused_without_the_regex(self):
        with patch.object(re, "sub", side_effect=AssertionError("regex ran")) as spy:
            with pytest.raises(QueryTooLongError):
                SearchService.sanitize_fts_query("x" * (100 * MAX_SEARCH_QUERY_LENGTH))
        spy.assert_not_called()

    def test_the_cap_counts_characters_not_bytes(self):
        """A multi-byte query at the cap is accepted whole; one more character
        is refused. Nothing is cut, so nothing is cut mid-codepoint."""
        at_cap = "漢" * MAX_SEARCH_QUERY_LENGTH  # 3 bytes each in UTF-8
        assert SearchService.sanitize_fts_query(at_cap) == at_cap
        with pytest.raises(QueryTooLongError):
            SearchService.sanitize_fts_query(at_cap + "漢")

    @pytest.mark.parametrize("query", ["", "   "])
    def test_empty_and_whitespace_are_unchanged(self, query):
        assert SearchService.sanitize_fts_query(query) == query


class TestSearchCap:
    def test_search_refuses_before_sanitizing_or_expanding(self):
        """The service entry point refuses even when sanitizing is off, so
        every mode -- semantic included -- gives the same answer."""
        svc = SearchService(_NoDB())
        with pytest.raises(QueryTooLongError):
            svc.search(OVER_CAP, sanitize=False)

    @pytest.mark.parametrize("mode", ["keyword", "semantic", "hybrid"])
    def test_every_mode_refuses(self, mode):
        svc = SearchService(_NoDB())
        with pytest.raises(QueryTooLongError):
            svc.search(OVER_CAP, mode=mode)

    def test_at_cap_searches(self, indexed_test_env):
        svc = SearchService(indexed_test_env["db"])
        assert svc.search(AT_CAP)  # the fixture holds immigration events


# ---------------------------------------------------------------------------
# REST
# ---------------------------------------------------------------------------


@pytest.mark.api
class TestRestSearch:
    @pytest.mark.parametrize("mode", ["keyword", "semantic", "hybrid"])
    def test_over_cap_is_422_query_too_long(self, rest_api_env, mode):
        resp = rest_api_env["client"].get("/api/search", params={"q": OVER_CAP, "mode": mode})
        assert resp.status_code == 422, resp.text
        body = resp.json()["detail"]
        assert body["code"] == "QUERY_TOO_LONG", body
        assert str(MAX_SEARCH_QUERY_LENGTH) in body["message"]

    def test_at_cap_searches(self, rest_api_env):
        resp = rest_api_env["client"].get("/api/search", params={"q": AT_CAP})
        assert resp.status_code == 200, resp.text
        assert resp.json()["count"] >= 1


# ---------------------------------------------------------------------------
# MCP
# ---------------------------------------------------------------------------


@pytest.fixture
def mcp_server(indexed_test_env):
    from pyrite.server.mcp_server import PyriteMCPServer

    server = PyriteMCPServer(indexed_test_env["config"], tier="read")
    yield server
    server.close()


class TestMcpSearch:
    @pytest.mark.parametrize("mode", ["keyword", "semantic", "hybrid"])
    def test_over_cap_is_a_validation_error(self, mcp_server, mode):
        result = mcp_server._dispatch_tool("kb_search", {"query": OVER_CAP, "mode": mode})
        assert result.get("error_code") == "QUERY_TOO_LONG", result
        assert result.get("retryable") is False, result
        assert str(MAX_SEARCH_QUERY_LENGTH) in result["error"]

    def test_at_cap_searches(self, mcp_server):
        result = mcp_server._dispatch_tool("kb_search", {"query": AT_CAP, "mode": "keyword"})
        assert "error" not in result, result
        assert result["count"] >= 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_config(indexed_test_env):
    config = indexed_test_env["config"]
    with (
        patch("pyrite.cli.context.load_config", return_value=config),
        patch("pyrite.cli.search_commands.load_config", return_value=config),
    ):
        yield config


@pytest.mark.cli
class TestCliSearch:
    def test_over_cap_json_is_a_query_too_long_error(self, cli_config):
        from pyrite.cli import app

        result = CliRunner().invoke(app, ["search", OVER_CAP, "--format", "json"])
        assert result.exit_code == 1, result.output
        data = json.loads(result.output)
        assert data["error_code"] == "QUERY_TOO_LONG", data
        assert data["retryable"] is False
        assert "results" not in data  # no fallback file search

    def test_over_cap_rich_is_a_clear_error_without_fallback(self, cli_config):
        from pyrite.cli import app

        result = CliRunner().invoke(app, ["search", OVER_CAP])
        assert result.exit_code == 1, result.output
        assert "QUERY_TOO_LONG" in result.output
        assert "Falling back" not in result.output

    def test_at_cap_searches(self, cli_config):
        from pyrite.cli import app

        result = CliRunner().invoke(app, ["search", AT_CAP, "--format", "json"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["count"] >= 1


# ---------------------------------------------------------------------------
# Internal callers that derive a query from content stay within the cap
# ---------------------------------------------------------------------------


def _many_tagged_long_titled_entry(config, db):
    from pyrite.services.kb_service import KBService

    svc = KBService(config, db)
    svc.create_entry(
        "test-events",
        "sprawling-entry",
        "Immigration " + " ".join(f"word{i:04d}" for i in range(200)),
        "note",
        body="immigration",
        tags=["immigration"] + [f"tag-{i:04d}" for i in range(200)],
    )
    return "sprawling-entry"


class TestDerivedQueriesStayWithinTheCap:
    def test_build_suggest_query_is_bounded(self):
        from pyrite.services.link_discovery_service import LinkDiscoveryService

        entry = {"title": "", "tags": [f"tag-{i:04d}" for i in range(500)]}
        query = LinkDiscoveryService.build_suggest_query(entry)
        assert 0 < len(query) <= MAX_SEARCH_QUERY_LENGTH
        # Whole quoted terms only: never a term cut mid-quote.
        assert query.count('"') % 2 == 0
        assert query.startswith('"tag-0000"')

    @pytest.mark.parametrize("mode", ["keyword", "semantic"])
    def test_discover_neighbors_on_a_sprawling_entry(self, indexed_test_env, mode):
        from pyrite.services.link_discovery_service import LinkDiscoveryService

        config, db = indexed_test_env["config"], indexed_test_env["db"]
        entry_id = _many_tagged_long_titled_entry(config, db)
        svc = LinkDiscoveryService(config, db)
        svc.discover_neighbors(
            entry_id, "test-events", mode=mode, readable_kbs=UNSCOPED
        )  # must not raise

    def test_suggest_links_on_a_sprawling_entry(self, indexed_test_env):
        from pyrite.services.link_discovery_service import LinkDiscoveryService

        config, db = indexed_test_env["config"], indexed_test_env["db"]
        entry_id = _many_tagged_long_titled_entry(config, db)
        LinkDiscoveryService(config, db).suggest_links(entry_id, "test-events")

    def test_ai_chat_retrieval_on_a_long_message_keeps_its_sources(self, rest_api_env):
        from tests.test_ai_endpoints import MockLLMService, _inject_llm

        client = rest_api_env["client"]
        _inject_llm(client.app, MockLLMService(stream_tokens=["Answer"]))
        resp = client.post(
            "/api/ai/chat",
            json={
                "messages": [{"role": "user", "content": OVER_CAP * 3}],
                "kb": "test-events",
            },
        )
        assert resp.status_code == 200, resp.text
        events = [
            json.loads(line[6:]) for line in resp.text.splitlines() if line.startswith("data: ")
        ]
        sources = [e for e in events if e["type"] == "sources"]
        assert sources and sources[0]["entries"], events

    def test_ai_suggest_links_on_a_long_title(self, rest_api_env):
        from tests.test_ai_endpoints import MockLLMService, _inject_llm

        client = rest_api_env["client"]
        entry_id = _many_tagged_long_titled_entry(rest_api_env["config"], rest_api_env["db"])
        _inject_llm(client.app, MockLLMService(complete_response="[]"))
        resp = client.post(
            "/api/ai/suggest-links", json={"entry_id": entry_id, "kb_name": "test-events"}
        )
        assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# Query expansion: derived terms fit the room the caller's query leaves
# ---------------------------------------------------------------------------


class _Expander:
    def __init__(self, terms):
        self.terms = terms

    def expand(self, query):
        return list(self.terms)


class TestExpansionStaysUnderTheCap:
    """Only the caller's own query is refused. Expansion terms are derived
    text: they are added while they fit and dropped whole when they do not."""

    @pytest.mark.parametrize("mode", ["keyword", "hybrid"])
    def test_a_query_under_the_cap_is_never_refused_for_its_expansion(self, indexed_test_env, mode):
        svc = SearchService(indexed_test_env["db"])
        svc._expansion_service = _Expander(["policy", "x" * 300, "border"])
        seen = []
        real = svc._db_search

        def spy(**kwargs):
            seen.append(kwargs["query"])
            return real(**kwargs)

        svc._db_search = spy
        query = "immigration".ljust(MAX_SEARCH_QUERY_LENGTH - 20)

        results = svc.search(query, mode=mode, expand=True)

        assert results
        assert seen and all(len(q) <= MAX_SEARCH_QUERY_LENGTH for q in seen)
        # Whole terms that fit are kept, one that does not is dropped whole,
        # and a shorter term after it still gets in.
        assert seen[0].endswith(" OR policy OR border"), seen[0][-40:]
        assert "xxx" not in seen[0]

    def test_no_room_means_the_query_alone(self, indexed_test_env):
        svc = SearchService(indexed_test_env["db"])
        svc._expansion_service = _Expander(["policy"])
        query = "immigration".ljust(MAX_SEARCH_QUERY_LENGTH)
        assert svc._expand_query(query) == query

    def test_the_callers_own_over_cap_query_is_still_refused(self, indexed_test_env):
        svc = SearchService(indexed_test_env["db"])
        svc._expansion_service = _Expander(["policy"])
        with pytest.raises(QueryTooLongError):
            svc.search(OVER_CAP, expand=True)


# ---------------------------------------------------------------------------
# clip_semantic_text: a bounded prefix for the semantic leg, never parsed
# ---------------------------------------------------------------------------


class TestClipSemanticText:
    def test_short_text_is_unchanged(self):
        from pyrite.services.search_service import clip_semantic_text

        assert clip_semantic_text('say "hello (there" OR') == 'say "hello (there" OR'

    def test_a_word_is_never_cut_mid_word(self):
        from pyrite.services.search_service import clip_semantic_text

        text = "a" * (MAX_SEARCH_QUERY_LENGTH - 3) + " immigration"
        assert clip_semantic_text(text) == "a" * (MAX_SEARCH_QUERY_LENGTH - 3)

    def test_a_single_over_long_word_is_cut_at_the_cap(self):
        from pyrite.services.search_service import clip_semantic_text

        assert clip_semantic_text("x" * (MAX_SEARCH_QUERY_LENGTH + 5)) == (
            "x" * MAX_SEARCH_QUERY_LENGTH
        )

    def test_fts5_punctuation_is_kept(self):
        """The semantic leg embeds text; a lone ")" or an open quote means
        nothing there, and cutting at it would lose the words (#431)."""
        from pyrite.services.search_service import clip_semantic_text

        text = '1) first "thing 2) second thing ' + "filler " * 200
        clipped = clip_semantic_text(text)
        assert clipped.startswith('1) first "thing 2) second thing')
        assert len(clipped) <= MAX_SEARCH_QUERY_LENGTH


def test_build_suggest_query_keeps_short_terms_after_an_over_long_one():
    from pyrite.services.link_discovery_service import LinkDiscoveryService

    entry = {"title": "", "tags": ["alpha", "x" * (MAX_SEARCH_QUERY_LENGTH + 5), "beta"]}
    assert LinkDiscoveryService.build_suggest_query(entry) == '"alpha" OR "beta"'


@pytest.mark.cli
class TestCliFileSearch:
    def test_files_over_cap_is_refused(self, cli_config):
        from pyrite.cli import app

        result = CliRunner().invoke(app, ["search", OVER_CAP, "--files", "--format", "json"])
        assert result.exit_code == 1, result.output
        assert json.loads(result.output)["error_code"] == "QUERY_TOO_LONG"

    def test_files_at_cap_runs(self, cli_config):
        from pyrite.cli import app

        result = CliRunner().invoke(app, ["search", AT_CAP, "--files"])
        assert result.exit_code == 0, result.output
