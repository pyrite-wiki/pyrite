"""The investigation CLI commands write through the KBService pipeline.

`investigation start`, `bulk-edges` and `ftm-import` create every entry the
way the MCP tools do: a file on disk as well as an index row, and a read-only
KB refuses the command with nothing written. Before, the CLI wrote index rows
alone: no file (a rebuilt index lost them) and none of the pipeline's checks,
the read-only flag among them.

Driven through the Typer app, as a user runs it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pyrite_journalism_investigation.cli import investigation_app

from pyrite.config import KBConfig
from pyrite.storage.database import PyriteDB
from tests._world import build_world

INV, FROZEN = "inv", "frozen"
EDGES = [
    {"type": "ownership", "fields": {"owner": "[[alice]]", "asset": "[[corp-a]]"}},
    {"type": "membership", "fields": {"person": "[[bob]]", "organization": "[[org-x]]"}},
]
ENTITIES = [
    {"id": "p-1", "schema": "Person", "properties": {"name": ["Alice Example"]}},
    {"id": "o-1", "schema": "Organization", "properties": {"name": ["Example Org"]}},
]


@pytest.fixture
def env(tmp_path: Path):
    """A real world (`tests/_world.py`): config.yaml on disk, read by the CLI."""
    world = build_world(
        tmp_path / "world",
        kbs=[
            KBConfig(name=INV, path=tmp_path / INV, kb_type="journalism-investigation"),
            KBConfig(
                name=FROZEN,
                path=tmp_path / FROZEN,
                kb_type="journalism-investigation",
                read_only=True,
            ),
        ],
        rest=False,
    )
    (tmp_path / "edges.json").write_text(json.dumps(EDGES))
    (tmp_path / "ftm.json").write_text(json.dumps(ENTITIES))
    try:
        yield world, tmp_path
    finally:
        world.close()


def _files(world, kb) -> list[str]:
    return sorted(p.stem for p in world.config.get_kb(kb).path.rglob("*.md"))


def _indexed(world, kb) -> list[str]:
    db = PyriteDB(world.config.settings.index_path)
    try:
        rows = db._raw_conn.execute("SELECT id FROM entry WHERE kb_name = ?", (kb,))
        return sorted(r["id"] for r in rows.fetchall())
    finally:
        db.close()


def _run(world, *args: str):
    return world.cli(*args, app=investigation_app)


class TestEachCreatedEntryIsAFile:
    def test_start(self, env):
        world, _ = env
        result = _run(world, "start", "--title", "New Investigation", "-k", INV, "--json")
        assert result.exit_code == 0, result.output
        assert _files(world, INV) == ["new-investigation"]
        assert _indexed(world, INV) == ["new-investigation"]

    def test_bulk_edges(self, env):
        world, tmp = env
        result = _run(world, "bulk-edges", "-f", str(tmp / "edges.json"), "-k", INV, "--json")
        assert result.exit_code == 0, result.output
        ids = sorted(e["id"] for e in json.loads(result.output)["entries"])
        assert _files(world, INV) == ids
        assert _indexed(world, INV) == ids

    def test_ftm_import(self, env):
        world, tmp = env
        result = _run(world, "ftm-import", "-f", str(tmp / "ftm.json"), "-k", INV, "--json")
        assert result.exit_code == 0, result.output
        assert _files(world, INV) == ["ftm-o-1", "ftm-p-1"]
        assert _indexed(world, INV) == ["ftm-o-1", "ftm-p-1"]


class TestAReadOnlyKBIsRefusedAndNothingIsWritten:
    @pytest.mark.parametrize(
        "args",
        [
            ("start", "--title", "New Investigation"),
            ("bulk-edges", "-f", "{tmp}/edges.json"),
            ("ftm-import", "-f", "{tmp}/ftm.json"),
        ],
        ids=["start", "bulk-edges", "ftm-import"],
    )
    def test_refused(self, env, args):
        world, tmp = env
        argv = [a.format(tmp=tmp) for a in args]
        result = _run(world, *argv, "-k", FROZEN, "--json")
        assert result.exit_code == 1, result.output
        assert json.loads(result.output.strip().splitlines()[-1])["error_code"] == "KB_READ_ONLY"
        assert _files(world, FROZEN) == []
        assert _indexed(world, FROZEN) == []
