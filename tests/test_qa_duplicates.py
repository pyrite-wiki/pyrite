"""`qa duplicates` reports candidate near-duplicate entries, never merges (#934)."""

from __future__ import annotations

import json
import time

import pytest

from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.models.task import TaskEntry
from pyrite.services.access_policy import UNSCOPED
from pyrite.services.qa_service import QAService
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager
from pyrite.storage.repository import KBRepository


@pytest.fixture
def dup_setup(tmp_path):
    kb_path = tmp_path / "kb"
    kb_path.mkdir()
    kb = KBConfig(name="dups", path=kb_path, kb_type=KBType.GENERIC)
    config = PyriteConfig(knowledge_bases=[kb], settings=Settings(index_path=tmp_path / "index.db"))
    db = PyriteDB(tmp_path / "index.db")
    index = IndexManager(db, config)
    yield {
        "config": config,
        "db": db,
        "index": index,
        "qa": QAService(config, db),
        "repo": KBRepository(kb),
    }
    db.close()


def _task(repo, entry_id: str, title: str, *, status: str = "open") -> None:
    repo.save(TaskEntry(id=entry_id, title=title, body="Body.", status=status))


def _pairs(setup, **kwargs):
    setup["index"].index_all()
    return setup["qa"].find_duplicates("task", readable_kbs=UNSCOPED, **kwargs)


def _world_with_pair(cli_world):
    """A world whose generic KB holds two task files that share a title."""
    world = cli_world()
    folder = world.kb_path("test-kb")
    for entry_id in ("election-day", "2026-11-03"):
        (folder / f"{entry_id}.md").write_text(
            f"---\nid: {entry_id}\ntype: task\ntitle: Election Day\nstatus: open\n---\nBody.\n",
            encoding="utf-8",
        )
    world.index()
    return world


class TestDuplicates:
    def test_title_signal_pairs_an_event_name_with_an_iso_date_id(self, dup_setup):
        _task(dup_setup["repo"], "election-day", "Election Day")
        _task(dup_setup["repo"], "2026-11-03", "Election Day")

        result = _pairs(dup_setup)

        assert result["count"] == 1
        pair = result["pairs"][0]
        assert (pair["a"]["id"], pair["b"]["id"]) == ("2026-11-03", "election-day")
        assert pair["score"] == 1.0
        assert pair["signal"] == "title"

    def test_id_prefix_is_configurable(self, dup_setup):
        _task(dup_setup["repo"], "research-alpha", "Prefixed One")
        _task(dup_setup["repo"], "alpha", "Other Words")

        assert _pairs(dup_setup)["count"] == 0

        result = _pairs(dup_setup, strip_prefixes=("research-",))

        assert result["count"] == 1
        assert result["pairs"][0]["score"] == 1.0
        assert result["pairs"][0]["signal"] == "id"

    def test_threshold_includes_just_above_and_excludes_just_below(self, dup_setup):
        _task(dup_setup["repo"], "red-green-blue", "Zeta One")
        _task(dup_setup["repo"], "red-green-blue-cyan", "Zeta Two")
        _task(dup_setup["repo"], "sun-moon-star", "Kappa One")
        _task(dup_setup["repo"], "sun-moon-star-comet-nova", "Kappa Two")

        result = _pairs(dup_setup)

        assert result["count"] == 1
        pair = result["pairs"][0]
        assert {pair["a"]["id"], pair["b"]["id"]} == {
            "red-green-blue",
            "red-green-blue-cyan",
        }
        assert pair["score"] == 0.75

    def test_a_non_live_entry_is_excluded(self, dup_setup):
        _task(dup_setup["repo"], "done-pair", "Same Title", status="done")
        _task(dup_setup["repo"], "done-pair-extra", "Same Title", status="done")

        assert _pairs(dup_setup)["count"] == 0

    def test_cli_json_shape_and_exit_0(self, cli_world):
        world = _world_with_pair(cli_world)

        result = world.cli("qa", "duplicates", "-t", "task", "--format", "json")

        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert payload["entry_type"] == "task"
        assert payload["threshold"] == 0.62
        assert payload["count"] == 1
        assert payload["pairs"][0]["signal"] == "title"
        assert set(payload["pairs"][0]["a"]) == {"id", "kb_name", "title"}

    def test_cli_rich_format_prints_one_line_per_pair(self, cli_world):
        world = _world_with_pair(cli_world)

        result = world.cli("qa", "duplicates", "-t", "task", "--format", "rich")

        assert result.exit_code == 0, result.output
        assert "~" in result.stdout
        assert "[title]" in result.stdout

    def test_a_bad_threshold_is_a_usage_error(self, cli_world):
        world = cli_world()

        result = world.cli("qa", "duplicates", "-t", "task", "--threshold", "2")

        assert result.exit_code == 2
        assert "threshold" in (result.output + getattr(result, "stderr", "")).lower()

    def test_two_thousand_entries_stay_inside_the_bound(self, dup_setup):
        rows = [
            (f"ticket-{index:04d}", "dups", "task", f"Ticket {index:04d}", "open", "active")
            for index in range(2000)
        ]
        dup_setup["db"]._raw_conn.execute("PRAGMA foreign_keys=OFF")
        dup_setup["db"]._raw_conn.executemany(
            "INSERT INTO entry (id, kb_name, entry_type, title, status, lifecycle) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        dup_setup["db"]._raw_conn.commit()
        dup_setup["db"]._raw_conn.execute("PRAGMA foreign_keys=ON")

        started = time.perf_counter()
        result = dup_setup["qa"].find_duplicates("task", readable_kbs=UNSCOPED)
        elapsed = time.perf_counter() - started

        assert result["pairs"] == []
        # Measured locally at ~0.2 s for 2,000 rows; the bound is deliberately
        # loose so a slow CI runner does not turn the guard into a flake.
        assert elapsed < 10.0, f"2,000 entries took {elapsed:.2f}s"


class TestCommonTokens:
    def _seed(self, setup, rows):
        db = setup["db"]
        kb = setup["config"].knowledge_bases[0]
        db.register_kb(kb.name, "generic", str(kb.path))
        db._raw_conn.executemany(
            "INSERT INTO entry (id, kb_name, entry_type, title, status, lifecycle) "
            "VALUES (?, 'dups', 'task', ?, 'open', 'active')",
            rows,
        )
        db._raw_conn.commit()

    def test_one_hundred_identical_titles_keep_every_pair(self, dup_setup):
        self._seed(dup_setup, [(f"item{i}", "Weekly report") for i in range(100)])

        result = dup_setup["qa"].find_duplicates("task", readable_kbs=UNSCOPED)

        assert result["count"] == 100 * 99 // 2
        assert all(p["score"] == 1.0 and p["signal"] == "title" for p in result["pairs"])
        assert result["skipped_tokens"] == 2

    def test_exact_id_tokens_survive_two_thousand_common_word_rows(self, dup_setup):
        rows = [(f"fix-bug-{i}", f"Noise {i}") for i in range(1998)]
        rows += [("fix-bug", "Different alpha"), ("bug-fix", "Other beta")]
        self._seed(dup_setup, rows)

        started = time.perf_counter()
        result = dup_setup["qa"].find_duplicates("task", readable_kbs=UNSCOPED)
        elapsed = time.perf_counter() - started

        assert result["count"] == 1
        pair = result["pairs"][0]
        assert (pair["a"]["id"], pair["b"]["id"]) == ("bug-fix", "fix-bug")
        assert pair["signal"] == "id"
        assert result["skipped_tokens"] == 3
        assert elapsed < 10.0, f"2,000 entries took {elapsed:.2f}s"

    def test_a_skipped_near_match_path_is_disclosed(self, dup_setup):
        self._seed(dup_setup, [(f"item{i}", f"Weekly report {i}") for i in range(100)])

        result = dup_setup["qa"].find_duplicates("task", readable_kbs=UNSCOPED, threshold=0.5)

        # These non-identical sets would meet the threshold; their common-token
        # expansion is bounded, so the report must disclose that incomplete path.
        assert result["pairs"] == []
        assert result["skipped_tokens"] == 2


@pytest.mark.parametrize(
    ("option", "value"), [("--type", " "), ("--status", " "), ("--status", ",, ")]
)
def test_an_empty_filter_is_a_structured_refusal(cli_world, option, value):
    world = cli_world()
    result = world.cli("qa", "duplicates", "-t", "task", option, value)
    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["error_code"] == "VALIDATION_FAILED"
    assert "NO_OUTCOME" not in result.stdout


def test_rich_output_discloses_the_common_token_cutoff(cli_world):
    world = cli_world()
    folder = world.kb_path("test-kb")
    for i in range(100):
        (folder / f"item{i}.md").write_text(
            f"---\nid: item{i}\ntype: task\ntitle: Weekly report {i}\nstatus: open\n---\nBody.\n",
            encoding="utf-8",
        )
    world.index()

    result = world.cli("qa", "duplicates", "-t", "task", "--format", "rich")

    assert result.exit_code == 0, result.output
    assert "Common-token expansion skipped for 2 tokens" in result.stdout
    assert "near-match coverage is incomplete" in result.stdout
