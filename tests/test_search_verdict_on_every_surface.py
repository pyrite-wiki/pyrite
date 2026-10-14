"""The CLI, MCP and REST return one search response (#668).

`SearchService.search_response` assembles it; each surface adds its envelope
(`query`, `count`, `has_more`) and shapes the rows. These tests walk the three
with the same query and hold them to the same verdict, in the same words, so a
surface that computes, rewords or drops a key fails here.

A real index, real files, a fake model (see `tests/search_verdict_seed.py`):
`open` is readable by everyone and has nothing above similarity 0.30; `secret`
is private and holds the best hit on both legs.
"""

from __future__ import annotations

import inspect
import json
import re
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from pyrite.config import AuthConfig, KBConfig, PyriteConfig, Settings
from pyrite.server.api import create_app, get_config, get_db
from pyrite.server.mcp_server import PyriteMCPServer
from pyrite.server.tool_schemas import READ_TOOLS
from pyrite.services.kb_service import KBService
from pyrite.services.search_service import SearchService
from pyrite.storage.database import PyriteDB
from tests.auth_seed import seed_and_sign_in
from tests.cli_help import plain
from tests.search_verdict_seed import install_fake_model, vector_at

OPEN, SECRET = "open", "secret"
SECRET_SCORES = (0.87, 0.86)  # the similarities seeded into `secret` below
SCORE_KEYS = {"similarity", "distance", "score", "rank", "rrf_score"}
KB_KEYS = {"kb_name", "kb", "kb_id"}
SECRET_TOKEN = re.compile(r"(?<![\d.])0\.8[67]\d*(?![\d])")  # a whole number, never inside 10.878
ABSENT = "Andon cord"
MODES = ["keyword", "semantic", "hybrid"]
VERDICT_KEYS = ("match", "match_basis", "keyword_match", "legs_run")


@pytest.fixture
def env(tmp_path, monkeypatch):
    install_fake_model(monkeypatch)
    for kb in (OPEN, SECRET):
        (tmp_path / kb).mkdir()
    config = PyriteConfig(
        knowledge_bases=[
            KBConfig(name=OPEN, path=tmp_path / OPEN, kb_type="generic", default_role="read"),
            KBConfig(name=SECRET, path=tmp_path / SECRET, kb_type="generic", default_role="none"),
        ],
        settings=Settings(
            index_path=tmp_path / "index.db",
            auto_embed=False,
            auth=AuthConfig(enabled=True, allow_registration=True, anonymous_tier="read"),
        ),
    )
    app = create_app(config=config)
    db = PyriteDB(config.settings.index_path)
    if not db.vec_available:
        db.close()
        pytest.skip("sqlite-vec unavailable; the vector leg cannot run")
    app.dependency_overrides[get_config] = lambda: config
    app.dependency_overrides[get_db] = lambda: db
    svc = KBService(config, db)
    seeds = [
        (OPEN, "kestrel", "Kestrel notes", "a small falcon that hovers", 0.30),
        (OPEN, "heron", "Heron notes", "a wading bird", 0.20),
        (OPEN, "wren", "Wren notes", "a very small bird", 0.20),
        (SECRET, "andon", "Andon cord", "the Andon cord stops the line", 0.87),
        (SECRET, "jidoka", "Jidoka", "Andon cord and autonomation", 0.86),
    ]
    for axis, (kb, entry_id, title, body, cosine) in enumerate(seeds, start=1):
        svc.create_entry(kb, entry_id, title, "note", body)
        assert db.backend.upsert_embedding(entry_id, kb, vector_at(cosine, axis))

    admin = TestClient(app)
    seed_and_sign_in(admin, "admin-user", "password123", role="admin")
    peer = TestClient(app)
    seed_and_sign_in(peer, "peer", "password123", role="read")
    mcp = PyriteMCPServer(config, tier="read")
    try:
        yield {"config": config, "db": db, "admin": admin, "peer": peer, "mcp": mcp}
    finally:
        mcp.close()
        db.close()


def cli(env, *args):
    from pyrite.cli import app

    config = env["config"]
    with (
        patch("pyrite.cli.context.load_config", return_value=config),
        patch("pyrite.cli.search_commands.load_config", return_value=config),
    ):
        result = CliRunner().invoke(app, ["search", *args, "--format", "json"])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def rest(client, **params):
    resp = client.get("/api/search", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def mcp(env, **args):
    result = env["mcp"]._dispatch_tool("kb_search", args)
    assert "error" not in result, result
    return result


def walk(env, query, mode, kb=OPEN):
    """The same search through each surface: ``{surface: payload}``."""
    return {
        "cli": cli(env, query, "-k", kb, "--mode", mode),
        "mcp": mcp(env, query=query, kb_name=kb, mode=mode),
        "rest": rest(env["admin"], q=query, kb=kb, mode=mode),
    }


def leaks(payload):
    """What in `payload` reveals the unreadable KB, by structure.

    Not a substring search over the JSON: wall-clock fields (`created_at`,
    `indexed_at`) contain digit runs such as `10.878126` (#881)."""
    found = []

    def numbers_equal_secret(value):
        return isinstance(value, (int, float)) and any(
            abs(value - s) < 0.005 for s in SECRET_SCORES
        )

    def walk_node(node, path, in_legs=False):
        if isinstance(node, dict):
            for key, value in node.items():
                here = f"{path}.{key}"
                if key in KB_KEYS and value == SECRET:
                    found.append(f"{here} is the unreadable KB")
                if key in SCORE_KEYS and numbers_equal_secret(value):
                    found.append(f"{here}={value} is a seeded secret score")
                if key in (
                    "match_basis",
                    "legs_run",
                    "legs",
                    "hint",
                    "hints",
                    "warnings",
                    "kbs",
                    "kb_names",
                ):
                    text = json.dumps(value)
                    if SECRET in text:
                        found.append(f"{here} names the unreadable KB")
                    if key == "match_basis" and SECRET_TOKEN.search(text):
                        found.append(f"{here} quotes a secret score: {value}")
                walk_node(value, here, in_legs or key == "legs")
        elif isinstance(node, list):
            for i, value in enumerate(node):
                if in_legs and numbers_equal_secret(value):
                    found.append(f"{path}[{i}] is a seeded secret score")
                walk_node(value, f"{path}[{i}]", in_legs)

    walk_node(payload, "$")
    return found


def verdict(payload):
    return {key: payload.get(key) for key in VERDICT_KEYS}


class TestOneVerdictOnEverySurface:
    @pytest.mark.parametrize("mode", MODES)
    def test_an_absent_term_is_none_in_the_same_words(self, env, mode):
        payloads = walk(env, ABSENT, mode)
        service = SearchService(env["db"], settings=env["config"].settings).search_response(
            ABSENT, kb_name=OPEN, mode=mode
        )

        for surface, payload in payloads.items():
            assert payload["match"] == "none", (surface, payload)
            assert verdict(payload) == {k: service.get(k) for k in VERDICT_KEYS}, surface
            assert ("keyword_match" in payload) == (mode != "semantic"), surface
            assert (
                payload["legs_run"]
                == {
                    "keyword": ["keyword"],
                    "semantic": ["semantic"],
                    "hybrid": ["keyword", "semantic"],
                }[mode]
            ), surface

    def test_the_issue_s_reproduction(self, env):
        """`pyrite search "Andon cord" -k <kb without it> --mode semantic` (#668)."""
        payload = cli(env, ABSENT, "-k", OPEN, "--mode", "semantic")
        assert payload["match"] == "none"
        assert payload["match_basis"] == "semantic: best similarity 0.30, below 0.35"
        ids = [r["id"] for r in payload["results"]]
        assert ids[0] == "kestrel" and set(ids) == {"kestrel", "heron", "wren"}, (
            "none still returns the nearest entries"
        )

    @pytest.mark.parametrize("mode", ["semantic", "hybrid"])
    def test_every_vector_row_carries_the_same_similarity(self, env, mode):
        expected = {"kestrel": 0.3, "heron": 0.2, "wren": 0.2}
        for surface, payload in walk(env, ABSENT, mode).items():
            got = {r["id"]: r.get("similarity") for r in payload["results"]}
            assert got == expected, surface

    def test_a_strong_match_is_strong_on_each(self, env):
        for mode in MODES:
            for surface, payload in walk(env, ABSENT, mode, kb=SECRET).items():
                assert payload["match"] == "strong", (surface, mode, payload)

    def test_legs_run_and_a_row_s_legs_are_two_different_facts(self, env):
        """`legs_run`, top level: the legs that ran. `legs`, per result: the legs
        that returned that result. Here both legs ran, and the two rows came
        from one leg each, so neither key can stand in for the other."""
        for surface, payload in walk(env, "falcon", "hybrid").items():
            assert payload["legs_run"] == ["keyword", "semantic"], surface
            assert "legs" not in payload, surface
            rows = {r["id"]: r for r in payload["results"]}
            assert rows["kestrel"]["legs"] == ["keyword", "semantic"], surface
            assert rows["heron"]["legs"] == ["semantic"], surface
            assert all("legs_run" not in r for r in payload["results"]), surface

    def test_hybrid_rows_name_their_legs(self, env):
        for surface, payload in walk(env, ABSENT, "hybrid", kb=SECRET).items():
            legs = {r["id"]: r.get("legs") for r in payload["results"]}
            assert legs == {
                "andon": ["keyword", "semantic"],
                "jidoka": ["keyword", "semantic"],
            }, surface

    def test_the_envelope_keys_are_still_first_and_unchanged(self, env):
        payloads = walk(env, "falcon", "keyword")
        for surface, payload in payloads.items():
            assert payload["query"] == "falcon" and payload["count"] == 1, surface
            assert payload["match"] == "strong" and payload["keyword_match"] == "all_terms"
            assert "warnings" not in payload, surface
        assert list(payloads["cli"])[:3] == ["query", "count", "results"]
        assert "has_more" in payloads["mcp"] and "has_more" not in payloads["cli"]


class TestTheVerdictSurvivesRowShaping:
    @pytest.mark.parametrize("mode", MODES)
    def test_a_fields_projection_keeps_the_top_level_keys(self, env, mode):
        whole = walk(env, ABSENT, mode)
        projected = {
            "cli": cli(env, ABSENT, "-k", OPEN, "--mode", mode, "--fields", "id"),
            "mcp": mcp(env, query=ABSENT, kb_name=OPEN, mode=mode, fields=["id"]),
            "rest": rest(env["admin"], q=ABSENT, kb=OPEN, mode=mode, fields="id"),
        }
        for surface, payload in projected.items():
            assert payload["match"] == "none" and payload["match_basis"], surface
            assert verdict(payload) == verdict(whole[surface]), surface
            assert all(set(r) == {"id", "kb_name"} for r in payload["results"]), surface

    def test_the_cli_s_empty_result_carries_it_too(self, env):
        payload = cli(env, "zzzabsent qqqabsent", "-k", OPEN, "--mode", "keyword")
        assert payload["results"] == [] and payload["count"] == 0
        assert (payload["match"], payload["keyword_match"]) == ("none", "none")
        assert payload["match_basis"] == "keyword: nothing matched"

    def test_the_cli_json_carries_warnings_like_the_others(self, env):
        """The CLI printed warnings on stderr only; its JSON had no such key."""
        with env["db"].backend._raw_cursor() as cur:
            cur.execute("DELETE FROM vec_entry")
        env["db"].backend._raw_conn.commit()
        for surface, payload in walk(env, "falcon", "hybrid").items():
            assert any("pyrite index embed" in w for w in payload["warnings"]), surface
            assert payload["match"] == "strong", surface
            assert "did not run" in payload["match_basis"], surface
            assert payload["legs_run"] == ["keyword"], surface

    def test_a_hybrid_search_with_no_embeddings_never_says_none(self, env):
        """`kb_search` defaults to hybrid, and a stock install has no vectors.

        No entry has both words. Each surface returns the near miss, as keyword
        mode would, calls it `weak`, and says in `legs` that only the keyword
        leg ran. It returned nothing and said `none`.
        """
        with env["db"].backend._raw_cursor() as cur:
            cur.execute("DELETE FROM vec_entry")
        env["db"].backend._raw_conn.commit()
        for surface, payload in walk(env, "falcon zzzabsent", "hybrid").items():
            assert [r["id"] for r in payload["results"]] == ["kestrel"], surface
            assert (payload["match"], payload["keyword_match"]) == ("weak", "some_terms"), surface
            assert payload["legs_run"] == ["keyword"], surface
        for surface, payload in walk(env, "qqqabsent zzzabsent", "hybrid").items():
            assert payload["results"] == [] and payload["match"] == "weak", surface
        for surface, payload in walk(env, "falcon", "semantic").items():
            assert (payload["match"], payload["legs_run"]) == ("weak", []), surface

    def test_rest_group_by_kb_does_not_change_the_verdict(self, env):
        plain_resp = rest(env["admin"], q=ABSENT, mode="hybrid")
        grouped = rest(env["admin"], q=ABSENT, mode="hybrid", group_by_kb="true")
        assert verdict(grouped) == verdict(plain_resp)
        assert grouped["match"] == "strong"


class TestAScopedCallerOnTheSurfaces:
    """The peer reads `open` only. Asked across every KB, each surface answers
    as it does for `open` alone: the best hit, in `secret`, changes nothing."""

    @pytest.mark.parametrize("mode", MODES)
    def test_rest(self, env, mode):
        scoped = rest(env["peer"], q=ABSENT, mode=mode)
        alone = rest(env["peer"], q=ABSENT, kb=OPEN, mode=mode)
        assert scoped == alone
        assert scoped["match"] == "none"
        assert SECRET not in json.dumps(scoped)
        assert leaks(scoped) == []
        assert leaks(rest(env["admin"], q=ABSENT, mode=mode)) != [], (
            "the check must see a real leak"
        )
        assert rest(env["admin"], q=ABSENT, mode=mode)["match"] == "strong"

    @pytest.mark.parametrize("mode", MODES)
    def test_mcp(self, env, mode):
        server = env["mcp"]
        scoped = server._kb_search({"query": ABSENT, "mode": mode}, readable_kbs={OPEN})
        alone = server._kb_search({"query": ABSENT, "mode": mode, "kb_name": OPEN})
        assert scoped == alone
        assert scoped["match"] == "none"
        assert SECRET not in json.dumps(scoped)
        assert leaks(scoped) == []
        assert leaks(mcp(env, query=ABSENT, mode=mode)) != [], "the check must see a real leak"
        assert mcp(env, query=ABSENT, mode=mode)["match"] == "strong"


class TestTheLeakCheckItself:
    """The check is structural: it catches a leak and ignores a timestamp (#881)."""

    CLEAN = {
        "results": [{"id": "kestrel", "kb_name": OPEN, "similarity": 0.3, "legs": ["semantic"]}],
        "match_basis": "semantic: best similarity 0.30, below 0.35",
        "legs_run": ["semantic"],
    }

    def test_a_timestamp_holding_the_old_score_is_not_a_leak(self):
        payload = json.loads(json.dumps(self.CLEAN))
        payload["results"][0].update(
            created_at="2026-10-14T07:00:10.878126+00:00", indexed_at="2026-10-14T10:87:00"
        )
        assert "0.87" in json.dumps(payload) or "10.87" in json.dumps(payload)
        assert leaks(payload) == []

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda p: p["results"].append({"id": "andon", "kb_name": SECRET}),
            lambda p: p["results"][0].update(similarity=0.87),
            lambda p: p["results"][0].update(rrf_score=0.86),
            lambda p: p["results"][0].update(legs=[0.87]),
            lambda p: p.update(match_basis="semantic: best similarity 0.87, at or above 0.50"),
            lambda p: p.update(legs_run=["semantic", SECRET]),
        ],
        ids=["row", "similarity", "rrf", "legs", "basis", "legs_run"],
    )
    def test_an_injected_secret_is_caught(self, mutate):
        payload = json.loads(json.dumps(self.CLEAN))
        assert leaks(payload) == []
        mutate(payload)
        assert leaks(payload) != []


class TestHowToReadIt:
    """Stated once, in `SearchService.search`'s docstring; repeated word for
    word where an agent meets the tool."""

    @staticmethod
    def _flat(text):
        return " ".join(text.split())

    @pytest.fixture
    def how_to_read(self):
        # Imported here, not at the top: the rest of this file must collect,
        # and fail on behaviour, on a tree without the change.
        from pyrite.services.search_service import HOW_TO_READ_MATCH

        return HOW_TO_READ_MATCH

    def test_the_docstring_is_the_statement(self, how_to_read):
        doc = self._flat(inspect.getdoc(SearchService.search))
        assert how_to_read in doc
        assert "What a search response owes its caller" in doc

    def test_the_kb_search_description_repeats_it_and_points_there(self, how_to_read):
        description = READ_TOOLS["kb_search"]["description"]
        assert how_to_read in description
        assert "docstring of SearchService.search" in description

    @pytest.mark.cli
    def test_search_help_repeats_it_and_points_there(self, how_to_read):
        from pyrite.cli import app

        result = CliRunner().invoke(app, ["search", "--help"], terminal_width=200)
        text = self._flat(plain(result.output).replace("│", " "))
        assert how_to_read in text, text
        assert "docstring of SearchService.search" in text
