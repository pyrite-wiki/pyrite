"""The verdict on real text, with the real model (#668, criterion 8).

The labelled queries marked ★ in the spike on #668, against the fixture KBs in
`tests/fixtures/search-verdict/`: an unanswerable query reads `none`, an
answerable one `strong` or `weak`, in semantic and in hybrid mode. Everything
else about the verdict is tested without a model in
`test_search_match_verdict.py`; this file is what ties the thresholds in
`_MODEL_MATCH_THRESHOLDS` to text.

One module-scoped index: the model is loaded once and the 22 entries embedded
once. The queries go through MCP `kb_search`, the surface an agent uses, so
on a tree without the change these tests fail on the missing `match`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("sentence_transformers")

from pyrite.config import KBConfig, PyriteConfig, Settings  # noqa: E402
from pyrite.server.mcp_server import PyriteMCPServer  # noqa: E402
from pyrite.services.embedding_service import EmbeddingService  # noqa: E402
from pyrite.storage import IndexManager  # noqa: E402
from pyrite.storage.database import PyriteDB  # noqa: E402

pytestmark = pytest.mark.embeddings

FIXTURE = Path(__file__).parent / "fixtures" / "search-verdict"

# (kb, query, an entry that answers it)
ANSWERABLE = [
    ("pyrite", "does a write wait for the embedding to finish", "adr-0035"),
    ("pyrite", "kanban pull workflow for agent teams", "adr-0019"),
    ("tps", "Andon cord", "andon"),
    ("tps", "stopping the line when a worker sees a defect", "andon"),
    ("tps", "asking why repeatedly to find the root cause", "five-whys"),
]
# (kb, query): the KB never mentions the topic
UNANSWERABLE = [
    ("pyrite", "Andon cord"),
    ("pyrite", "Flurbex sync daemon"),
    ("tps", "Pyrite MCP server"),
]
MODES = ["semantic", "hybrid"]


@pytest.fixture(scope="module")
def search(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("search-verdict")
    config = PyriteConfig(
        knowledge_bases=[
            KBConfig(name=name, path=FIXTURE / name, kb_type="generic")
            for name in ("tps", "pyrite")
        ],
        settings=Settings(index_path=tmp / "index.db", auto_embed=False),
    )
    db = PyriteDB(config.settings.index_path)
    if not db.vec_available:
        db.close()
        pytest.skip("sqlite-vec unavailable; the vector leg cannot run")
    IndexManager(db, config).index_all()
    stats = EmbeddingService(db, model_name=config.settings.embedding_model).embed_all()
    assert stats["embedded"] == 22 and stats["errors"] == 0, stats
    server = PyriteMCPServer(config, tier="read")

    def _search(query, kb, mode):
        result = server._dispatch_tool("kb_search", {"query": query, "kb_name": kb, "mode": mode})
        assert "error" not in result, result
        return result

    yield _search
    server.close()
    db.close()


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(("kb", "query"), UNANSWERABLE)
def test_an_unanswerable_query_reads_none(search, kb, query, mode):
    resp = search(query, kb, mode)
    assert resp["match"] == "none", resp["match_basis"]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(("kb", "query", "answer"), ANSWERABLE)
def test_an_answerable_query_reads_strong_or_weak_and_finds_its_entry(
    search, kb, query, answer, mode
):
    resp = search(query, kb, mode)
    assert resp["match"] in ("strong", "weak"), resp["match_basis"]
    assert answer in [r["id"] for r in resp["results"][:5]]


@pytest.mark.parametrize("mode", MODES)
def test_a_near_domain_absence_in_a_small_kb_is_never_strong(search, mode):
    """The fourth ★ unanswerable query, and the limit of this fixture.

    "Elasticsearch backend" has an adjacent entry (`postgres-backend`, 0.48).
    What tells it from an answer is the margin over the next nine results, and
    a twelve-entry KB has no crowd of backend entries to stand among: here the
    margin is 0.23 and the verdict `weak`. On the 936-entry `kb/` the same
    query has a margin of 0.09 and reads `none`, as the spike measured. The
    signal is a hint for the reader to verify; what must hold at any size is
    that an absent topic is not called `strong`.
    """
    resp = search("Elasticsearch backend", "pyrite", mode)
    assert resp["match"] != "strong", resp["match_basis"]
    assert resp["results"][0]["id"] == "postgres-backend"


def test_the_issue_s_report(search):
    """`pyrite search "Andon cord" -k pyrite --mode semantic` returned twenty
    unrelated entries and nothing that said so."""
    resp = search("Andon cord", "pyrite", "semantic")
    assert resp["match"] == "none"
    assert resp["match_basis"].startswith("semantic: ")


def test_similarity_is_a_cosine_of_unit_vectors(search):
    rows = search("Andon cord", "tps", "semantic")["results"]
    assert rows[0]["id"] == "andon"
    assert all(0.155 <= r["similarity"] <= 1.0 for r in rows)
    # sqlite-vec's `distance` is L2; for unit vectors cos = 1 - d**2 / 2. The
    # two are computed independently, so this also checks the model normalises.
    for r in rows:
        assert abs(r["similarity"] - (1 - r["distance"] ** 2 / 2)) < 0.002
