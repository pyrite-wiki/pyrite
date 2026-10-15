"""Link discovery leaves out entries with nothing to show (#200).

"Empty" is a body and a summary that are both blank after trimming whitespace.
A summary without a body is not empty. The exclusion is a predicate in the
search query itself (so ``limit`` results come back when ``limit`` qualify),
on every leg: keyword, semantic and hybrid.

The tests enter through the real surfaces -- the CLI root app, an MCP
``tools/call`` round trip, a REST ``TestClient`` -- and the service, with the
real search underneath. Semantic legs use the deterministic stub embedder of
``test_search_filters_across_modes``: what is under test is where the predicate
is applied, not the model.
"""

import asyncio
import json
import tempfile
import zlib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.server.tool_schemas import READ_TOOLS
from pyrite.services.kb_service import KBService
from pyrite.services.link_discovery_service import LinkDiscoveryService
from pyrite.storage.database import PyriteDB
from tests.cli_help import plain

runner = CliRunner()

MODES = ["keyword", "semantic", "hybrid"]
EMPTIES = {"stub-1", "stub-2", "stub-3", "blank-body"}
FULL = {"note-1", "note-2", "note-3"}


class _Vec(list):
    def tolist(self):
        return list(self)


class _StubModel:
    """The model is the process edge: a deterministic 384-dim vector per text.

    The real `EmbeddingService` and `SearchService` run on top of it.
    """

    def encode(self, text, **kwargs):
        if isinstance(text, list):  # the service's warm-up call
            return [self.encode(t) for t in text]
        vec = [0.05] * 384
        vec[zlib.crc32(text.encode()) % 384] += 0.001
        return _Vec(vec)


@pytest.fixture
def env(monkeypatch):
    import pyrite.services.embedding_service as es

    monkeypatch.setattr(es, "is_available", lambda: True)
    monkeypatch.setattr(es, "_load_model", lambda name: _StubModel())
    monkeypatch.setattr(es, "_MODEL_CACHE", {})

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        kbs = []
        for name in ("kb-a", "kb-b", "kb-c"):
            (tmp / name).mkdir()
            kbs.append(KBConfig(name=name, path=tmp / name, kb_type=KBType.GENERIC))
        config = PyriteConfig(knowledge_bases=kbs, settings=Settings(index_path=tmp / "index.db"))
        db = PyriteDB(tmp / "index.db")
        if not db.vec_available:
            db.close()
            pytest.skip("sqlite-vec not available")
        svc = KBService(config, db)

        # Sources. The second has no body and no summary: it still gets
        # suggestions (the exclusion is of candidates, never of the source).
        svc.create_entry(
            "kb-a",
            "palantir-source",
            "Palantir contract analysis",
            "note",
            body="An analysis of the Palantir contract and its terms.",
            tags=["palantir"],
        )
        svc.create_entry(
            "kb-a", "empty-source", "Palantir contract analysis", "note", tags=["palantir"]
        )

        # kb-b: three title-only stubs that outrank three notes with bodies
        # (short documents win on BM25), and one blank-body entry.
        for i, title in enumerate(
            ["Palantir contract", "Palantir contract analysis", "Contract Palantir"], 1
        ):
            svc.create_entry("kb-b", f"stub-{i}", title, "note")
        for i in (1, 2, 3):
            svc.create_entry(
                "kb-b",
                f"note-{i}",
                f"Reading notes {i}",
                "note",
                body=(
                    "Notes on the Palantir contract: the contract covers analysis of "
                    "data held by agencies, with renewal terms and audit clauses. " * 3
                ),
            )
        # Whitespace only, written to the index as is (a file edit by hand).
        db.upsert_entry(
            {
                "id": "blank-body",
                "kb_name": "kb-b",
                "entry_type": "note",
                "title": "Palantir contract analysis",
                "body": " \n\t  \n",
                "summary": "  ",
                "tags": ["palantir"],
                "sources": [],
                "links": [],
            }
        )

        # kb-c: a summary and no body (kept), and a stub (left out).
        svc.create_entry(
            "kb-c",
            "summary-only",
            "Palantir contract",
            "note",
            summary="What the Palantir contract covers",
        )
        svc.create_entry("kb-c", "stub-c", "Palantir contract analysis", "note")

        from pyrite.services.embedding_service import EmbeddingService

        embedder = EmbeddingService(db)
        for kb in ("kb-a", "kb-b", "kb-c"):
            for row in db.list_entries(kb_name=kb, limit=100):
                db.backend.upsert_embedding(row["id"], kb, embedder.embed_text(row["title"]))

        # The CLI opens its own db from the config; hand it this one.
        monkeypatch.setattr("pyrite.cli.link_commands.get_config_and_db", lambda: (config, db))
        db.close = lambda: None
        yield {"config": config, "db": db}


def _ids(rows, key="id"):
    return {r[key] for r in rows}


def _discover(env, entry="palantir-source", kb="kb-a", target="kb-b", **kw):
    kw.setdefault("limit", 3)
    kw.setdefault("mode", "keyword")
    return LinkDiscoveryService(env["config"], env["db"]).discover_neighbors(
        entry_id=entry, kb_name=kb, target_kb=target, readable_kbs=None, **kw
    )


class TestService:
    @pytest.mark.parametrize("mode", MODES)
    def test_default_returns_the_notes_when_stubs_outrank_them(self, env, mode):
        got = _discover(env, mode=mode)
        assert _ids(got) == FULL, got

    def test_the_stubs_do_outrank_the_notes_once_allowed(self, env):
        """Why the default test means something: with the stubs allowed in,
        they fill the page, so a post-cut filter would return fewer."""
        got = _discover(env, include_empty=True)
        assert _ids(got) & EMPTIES
        assert _ids(got) != FULL

    @pytest.mark.parametrize("mode", MODES)
    def test_include_empty_returns_them(self, env, mode):
        got = _discover(env, mode=mode, limit=10, include_empty=True)
        assert EMPTIES <= _ids(got), got

    @pytest.mark.parametrize("mode", MODES)
    def test_fewer_than_limit_qualify(self, env, mode):
        got = _discover(env, mode=mode, limit=10)
        assert _ids(got) == FULL

    @pytest.mark.parametrize("mode", MODES)
    def test_summary_without_body_is_kept(self, env, mode):
        got = _discover(env, target="kb-c", mode=mode, limit=10)
        assert _ids(got) == {"summary-only"}, got

    @pytest.mark.parametrize("mode", MODES)
    def test_an_empty_source_still_gets_suggestions(self, env, mode):
        got = _discover(env, entry="empty-source", mode=mode)
        assert _ids(got) == FULL, got

    def test_across_kbs_with_no_target(self, env):
        got = _discover(env, target=None, limit=20)
        assert not (_ids(got) & (EMPTIES | {"stub-c"}))
        assert {"note-1", "summary-only"} <= _ids(got)

    def test_batch_suggest_limit_per_entry(self, env):
        pairs = LinkDiscoveryService(env["config"], env["db"]).batch_suggest(
            source_kb="kb-a", target_kb="kb-b", limit_per_entry=3, readable_kbs=None
        )
        assert _ids(pairs, "target_id") == FULL
        assert sum(p["source_id"] == "palantir-source" for p in pairs) == 3

    def test_batch_suggest_include_empty(self, env):
        pairs = LinkDiscoveryService(env["config"], env["db"]).batch_suggest(
            source_kb="kb-a",
            target_kb="kb-b",
            limit_per_entry=10,
            readable_kbs=None,
            include_empty=True,
        )
        assert EMPTIES & _ids(pairs, "target_id")


class TestCLI:
    def _run(self, *args):
        result = runner.invoke(app, [*args, "--format", "json"])
        assert result.exit_code == 0, result.output
        return json.loads(result.output)

    def test_discover_default_and_flag(self, env):
        base = ["links", "discover", "palantir-source", "-k", "kb-a", "--target-kb", "kb-b"]
        data = self._run(*base, "--limit", "3", "--mode", "keyword")
        assert _ids(data["discoveries"]) == FULL
        data = self._run(*base, "--limit", "10", "--mode", "keyword", "--include-empty")
        assert EMPTIES <= _ids(data["discoveries"])

    def test_batch_suggest_default_and_flag(self, env):
        base = ["links", "batch-suggest", "--source-kb", "kb-a", "--target-kb", "kb-b", "-n", "10"]
        data = self._run(*base)
        assert _ids(data["pairs"], "target_id") == FULL
        data = self._run(*base, "--include-empty")
        assert EMPTIES <= _ids(data["pairs"], "target_id")

    @pytest.mark.parametrize("cmd", ["discover", "batch-suggest"])
    def test_help_says_what_is_left_out(self, cmd):
        out = plain(runner.invoke(app, ["links", cmd, "--help"]).output)
        flat = " ".join(out.split())
        assert "--include-empty" in flat
        assert "no body and no summary" in flat


class TestMCP:
    def _call(self, env, tool, arguments):
        from mcp.shared.memory import create_connected_server_and_client_session

        from pyrite.server.mcp_server import PyriteMCPServer

        server = PyriteMCPServer(config=env["config"], tier="read")
        sdk = server.build_sdk_server(client_id="t", readable_kbs=None)

        async def run():
            async with create_connected_server_and_client_session(sdk) as session:
                result = await session.call_tool(tool, arguments)
                return json.loads(result.content[0].text)

        try:
            return asyncio.run(run())
        finally:
            server.close()

    def test_discover_neighbors(self, env):
        args = {
            "entry_id": "palantir-source",
            "kb_name": "kb-a",
            "target_kb": "kb-b",
            "mode": "keyword",
            "limit": 3,
        }
        assert _ids(self._call(env, "kb_discover_neighbors", args)["discoveries"]) == FULL
        out = self._call(env, "kb_discover_neighbors", {**args, "limit": 10, "include_empty": True})
        assert EMPTIES <= _ids(out["discoveries"])

    def test_batch_suggest(self, env):
        args = {"source_kb": "kb-a", "target_kb": "kb-b", "limit_per_entry": 10}
        assert _ids(self._call(env, "kb_batch_suggest", args)["pairs"], "target_id") == FULL
        out = self._call(env, "kb_batch_suggest", {**args, "include_empty": True})
        assert EMPTIES <= _ids(out["pairs"], "target_id")

    @pytest.mark.parametrize("tool", ["kb_discover_neighbors", "kb_batch_suggest"])
    def test_a_string_false_is_not_read_as_true(self, env, tool):
        """`bool("false")` is True. The handler reads the argument as a boolean,
        whatever client or route (the SDK's schema check included) reached it."""
        from pyrite.server.mcp_server import PyriteMCPServer

        server = PyriteMCPServer(config=env["config"], tier="read")
        try:
            if tool == "kb_discover_neighbors":
                call = server._kb_discover_neighbors
                args = {"entry_id": "palantir-source", "kb_name": "kb-a", "target_kb": "kb-b"}
                rows_key, id_key = "discoveries", "id"
            else:
                call = server._kb_batch_suggest
                args = {"source_kb": "kb-a", "target_kb": "kb-b", "limit_per_entry": 10}
                rows_key, id_key = "pairs", "target_id"
            args["mode"] = "keyword"
            out = call(args | {"include_empty": "false"})
            assert not (_ids(out[rows_key], id_key) & EMPTIES), out
            out = call(args | {"include_empty": "true"})
            assert EMPTIES <= _ids(out[rows_key], id_key)
            out = call(args | {"include_empty": "maybe"})
            assert out["error_code"] == "INVALID_PARAMETER"
        finally:
            server.close()

    @pytest.mark.parametrize("tool", ["kb_discover_neighbors", "kb_batch_suggest"])
    def test_description_and_schema_name_the_switch(self, tool):
        spec = READ_TOOLS[tool]
        assert "no body and no summary" in spec["description"]
        assert "include_empty" in spec["description"]
        prop = spec["inputSchema"]["properties"]["include_empty"]
        assert prop["type"] == "boolean"


class TestREST:
    @pytest.fixture
    def client(self, env):
        from starlette.testclient import TestClient

        from pyrite.server.api import create_app, get_config, get_db

        api = create_app(env["config"])
        api.dependency_overrides[get_config] = lambda: env["config"]
        api.dependency_overrides[get_db] = lambda: env["db"]
        return TestClient(api)

    def test_discover_neighbors(self, client):
        params = {
            "entry_id": "palantir-source",
            "kb": "kb-a",
            "target_kb": "kb-b",
            "mode": "keyword",
            "limit": 3,
        }
        r = client.get("/api/links/discover-neighbors", params=params)
        assert r.status_code == 200, r.text
        assert _ids(r.json()["discoveries"]) == FULL
        r = client.get(
            "/api/links/discover-neighbors", params={**params, "limit": 10, "include_empty": True}
        )
        assert EMPTIES <= _ids(r.json()["discoveries"])

    def test_batch_suggest(self, client):
        params = {"source_kb": "kb-a", "target_kb": "kb-b", "limit_per_entry": 10}
        r = client.get("/api/links/batch-suggest", params=params)
        assert r.status_code == 200, r.text
        assert _ids(r.json()["pairs"], "target_id") == FULL
        r = client.get("/api/links/batch-suggest", params={**params, "include_empty": True})
        assert EMPTIES <= _ids(r.json()["pairs"], "target_id")


class TestBlankKbNeighbours:
    """A blank or whitespace KB places the source by lookup (#528); discovery
    then answers what a named KB answers: never the source as its own
    neighbour, and never its backlinks (#912). Three surfaces, one answer."""

    SOURCE = "palantir-source"

    @staticmethod
    def _named(rows):
        return {(r["kb_name"], r["id"]) for r in rows}

    def _expected(self, env):
        named = LinkDiscoveryService(env["config"], env["db"]).discover_neighbors(
            self.SOURCE, "kb-a", mode="keyword", readable_kbs=None
        )
        assert named, "the fixture must have neighbours"
        assert ("kb-a", self.SOURCE) not in self._named(named)
        return self._named(named)

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_service(self, env, blank):
        svc = LinkDiscoveryService(env["config"], env["db"])
        got = svc.discover_neighbors(self.SOURCE, blank, mode="keyword", readable_kbs=None)
        assert self._named(got) == self._expected(env)
        sug = svc.suggest_links(self.SOURCE, blank)
        assert ("kb-a", self.SOURCE) not in self._named(sug) and sug

    def test_a_backlink_is_excluded_for_a_blank_kb(self, env):
        svc = KBService(env["config"], env["db"])
        svc.create_entry(
            "kb-a",
            "back-linker",
            "Palantir contract analysis notes",
            "note",
            body="x",
            links=[{"target": self.SOURCE, "relation": "related_to"}],
        )
        lds = LinkDiscoveryService(env["config"], env["db"])
        named = lds.discover_neighbors(self.SOURCE, "kb-a", mode="keyword", readable_kbs=None)
        blank = lds.discover_neighbors(self.SOURCE, "  ", mode="keyword", readable_kbs=None)
        assert ("kb-a", "back-linker") not in self._named(named)
        assert ("kb-a", "back-linker") not in self._named(blank)

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_cli(self, env, blank):
        result = runner.invoke(
            app,
            [
                "links",
                "discover",
                self.SOURCE,
                "-k",
                blank,
                "--mode",
                "keyword",
                "--format",
                "json",
            ],
        )
        assert result.exit_code == 0, result.output
        got = self._named(json.loads(result.output)["discoveries"])
        assert got == self._expected(env)

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_rest(self, env, blank):
        from starlette.testclient import TestClient

        from pyrite.server.api import create_app, get_config, get_db

        api = create_app(env["config"])
        api.dependency_overrides[get_config] = lambda: env["config"]
        api.dependency_overrides[get_db] = lambda: env["db"]
        r = TestClient(api).get(
            "/api/links/discover-neighbors",
            params={"entry_id": self.SOURCE, "kb": blank, "mode": "keyword"},
        )
        assert r.status_code == 200, r.text
        assert self._named(r.json()["discoveries"]) == self._expected(env)

    def test_mcp(self, env):
        # An empty kb_name is a missing argument there; whitespace reaches discovery.
        call = lambda blank: TestMCP()._call(  # noqa: E731
            env,
            "kb_discover_neighbors",
            {"entry_id": self.SOURCE, "kb_name": blank, "mode": "keyword"},
        )
        assert "discoveries" not in call("")
        assert self._named(call("   ")["discoveries"]) == self._expected(env)
