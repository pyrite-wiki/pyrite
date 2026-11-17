"""`qa duplicates` reports candidate near-duplicate entries, never merges (#934)."""

from __future__ import annotations

import json
import time

import pytest
import typer

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

    @pytest.mark.parametrize("value", ["2", "abc"])
    def test_a_bad_threshold_is_a_usage_error(self, cli_world, value):
        world = cli_world()

        result = world.cli("qa", "duplicates", "-t", "task", "--threshold", value)

        assert result.exit_code == 2
        assert "threshold" in (result.output + getattr(result, "stderr", "")).lower()

    @pytest.mark.parametrize("value", ["0", "1"])
    def test_cli_accepts_a_threshold_on_the_boundary(self, cli_world, value):
        world = _world_with_pair(cli_world)

        result = world.cli(
            "qa", "duplicates", "-t", "task", "--threshold", value, "--format", "json"
        )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert payload["threshold"] == float(value)
        assert payload["count"] == 1

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


def _write(folder, entry_id: str, entry_type: str, title: str, status: str | None) -> None:
    status_line = f"status: {status}\n" if status is not None else ""
    (folder / f"{entry_id}.md").write_text(
        f"---\nid: {entry_id}\ntype: {entry_type}\ntitle: {title}\n{status_line}---\nBody.\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    "settled",
    [
        pytest.param(
            "done",
            marks=pytest.mark.control(reason="done was already settled in the old scan"),
        ),
        "retired",
        "superseded",
        "wont_do",
    ],
)
def test_a_settled_backlog_item_is_never_in_the_default_scan(dup_setup, tmp_path, settled):
    """#934 groom: the default scan lists live items only. `settled` comes from
    the backlog_item type's declared statuses, not a list in the scan."""
    folder = tmp_path / "kb"
    _write(folder, "weekly-report-a", "backlog_item", "Weekly Report Sync", settled)
    _write(folder, "weekly-report-b", "backlog_item", "Weekly Report Sync", "proposed")
    _write(folder, "weekly-report-c", "backlog_item", "Weekly Report Sync", "in_progress")
    dup_setup["index"].index_all()

    result = dup_setup["qa"].find_duplicates("backlog_item", readable_kbs=UNSCOPED)

    ids = {side["id"] for pair in result["pairs"] for side in (pair["a"], pair["b"])}
    assert ids == {"weekly-report-b", "weekly-report-c"}
    assert result["count"] == 1


@pytest.mark.control(
    reason="passes on the old scan too: an explicit --status is not narrowed by the settled set"
)
def test_an_explicit_status_still_reaches_a_settled_backlog_item(dup_setup, tmp_path):
    folder = tmp_path / "kb"
    _write(folder, "old-a", "backlog_item", "Retired Thing", "retired")
    _write(folder, "old-b", "backlog_item", "Retired Thing", "retired")
    dup_setup["index"].index_all()

    result = dup_setup["qa"].find_duplicates(
        "backlog_item", readable_kbs=UNSCOPED, statuses={"retired"}
    )

    assert result["count"] == 1


@pytest.mark.control(
    reason="passes on the old scan too: guards that the declared-status rule drops nothing from a type with no status"
)
def test_a_type_with_no_status_field_is_scanned_and_every_entry_is_live(dup_setup, tmp_path):
    """A note has no status: nothing is dropped and nothing raises."""
    folder = tmp_path / "kb"
    for entry_id in ("election-day", "2026-11-03"):
        _write(folder, entry_id, "note", "Election Day", None)
    dup_setup["index"].index_all()

    result = dup_setup["qa"].find_duplicates("note", readable_kbs=UNSCOPED)

    assert result["count"] == 1
    assert {result["pairs"][0]["a"]["id"], result["pairs"][0]["b"]["id"]} == {
        "election-day",
        "2026-11-03",
    }


@pytest.mark.control(
    reason="passes on the old scan too: guards that the declared-status rule drops nothing from a type with no status"
)
def test_cli_scans_a_statusless_type(cli_world):
    world = cli_world()
    folder = world.kb_path("test-kb")
    for entry_id in ("election-day", "2026-11-03"):
        _write(folder, entry_id, "note", "Election Day", None)
    world.index()

    result = world.cli("qa", "duplicates", "-t", "note", "--format", "json")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["count"] == 1


# --- fix round 1 (#934): every documented rule has a test red without it -----


def _titles(setup, tmp_path, entry_type, pairs, status=None):
    for entry_id, title in pairs:
        _write(tmp_path / "kb", entry_id, entry_type, title, status)
    setup["index"].index_all()
    return setup["qa"].find_duplicates(entry_type, readable_kbs=UNSCOPED)


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("选举日", "选举日"),
        ("День выборов", "день ВЫБОРОВ"),
        ("Élection présidentielle", "Election presidentielle"),
    ],
    ids=["cjk", "cyrillic", "accented-vs-plain"],
)
def test_titles_in_any_script_are_compared(dup_setup, tmp_path, left, right):
    result = _titles(dup_setup, tmp_path, "note", [("entry-one", left), ("entry-two", right)])

    assert result["count"] == 1, result
    assert result["pairs"][0]["signal"] == "title"
    assert result["pairs"][0]["score"] == 1.0


def test_distinct_non_ascii_titles_are_not_paired(dup_setup, tmp_path):
    result = _titles(
        dup_setup, tmp_path, "note", [("entry-one", "选举日"), ("entry-two", "День выборов")]
    )
    assert result["count"] == 0


@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
def test_cli_refuses_a_non_finite_threshold_as_a_usage_error(cli_world, value):
    world = cli_world()

    result = world.cli("qa", "duplicates", "-t", "task", "--threshold", value)

    assert result.exit_code == 2, result.output
    assert "NaN" not in result.stdout
    assert "NO_OUTCOME" not in result.stdout + result.output


@pytest.mark.parametrize("value", ["2", "nan", "inf", "-inf", "abc", "", "1.1", "-0.1"])
def test_parse_unit_interval_refuses_bad_values(value):
    from pyrite.cli.qa_commands import _parse_unit_interval

    with pytest.raises(typer.BadParameter, match="not a valid number|not in the range"):
        _parse_unit_interval(value)


@pytest.mark.parametrize(("value", "expected"), [("0.5", 0.5), ("0", 0.0), ("1", 1.0)])
def test_parse_unit_interval_accepts_values_in_range(value, expected):
    from pyrite.cli.qa_commands import _parse_unit_interval

    assert _parse_unit_interval(value) == expected


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.1, 1.1])
def test_service_refuses_a_threshold_outside_zero_to_one(dup_setup, value):
    from pyrite.exceptions import ValidationError

    with pytest.raises(ValidationError):
        dup_setup["qa"].find_duplicates("task", readable_kbs=UNSCOPED, threshold=value)


def test_an_archived_entry_is_excluded_by_default(dup_setup, tmp_path):
    folder = tmp_path / "kb"
    for entry_id in ("keep-a", "keep-b"):
        _write(folder, entry_id, "note", "Same Title", None)
    (folder / "gone.md").write_text(
        "---\nid: gone\ntype: note\ntitle: Same Title\nlifecycle: archived\n---\nBody.\n",
        encoding="utf-8",
    )
    dup_setup["index"].index_all()

    result = dup_setup["qa"].find_duplicates("note", readable_kbs=UNSCOPED)

    assert {s["id"] for p in result["pairs"] for s in (p["a"], p["b"])} == {"keep-a", "keep-b"}


@pytest.mark.parametrize("settled", ["rejected", "deprecated", "superseded"])
def test_a_settled_adr_is_excluded_and_a_live_adr_is_not(dup_setup, tmp_path, settled):
    folder = tmp_path / "kb"
    _write(folder, "adr-a", "adr", "Use SQLite", settled)
    _write(folder, "adr-b", "adr", "Use SQLite", "accepted")
    _write(folder, "adr-c", "adr", "Use SQLite", "proposed")
    dup_setup["index"].index_all()

    result = dup_setup["qa"].find_duplicates("adr", readable_kbs=UNSCOPED)

    assert {s["id"] for p in result["pairs"] for s in (p["a"], p["b"])} == {"adr-b", "adr-c"}


@pytest.mark.parametrize("live", ["deferred", "proposed", "accepted"])
def test_a_deferred_backlog_item_stays_live(dup_setup, tmp_path, live):
    folder = tmp_path / "kb"
    _write(folder, "weekly-a", "backlog_item", "Weekly Report", live)
    _write(folder, "weekly-b", "backlog_item", "Weekly Report", "proposed")
    dup_setup["index"].index_all()

    assert dup_setup["qa"].find_duplicates("backlog_item", readable_kbs=UNSCOPED)["count"] == 1


def test_a_failed_task_beside_its_open_retry_is_a_candidate(dup_setup, tmp_path):
    """A failed task can be reopened (models/task.py), so it is not settled."""
    folder = tmp_path / "kb"
    _write(folder, "deploy-a", "task", "Deploy The Thing", "failed")
    _write(folder, "deploy-b", "task", "Deploy The Thing", "open")
    dup_setup["index"].index_all()

    assert dup_setup["qa"].find_duplicates("task", readable_kbs=UNSCOPED)["count"] == 1


@pytest.mark.parametrize("settled", ["done", "cancelled"])
def test_a_resolved_task_is_excluded(dup_setup, tmp_path, settled):
    folder = tmp_path / "kb"
    _write(folder, "ship-a", "task", "Ship The Thing", settled)
    _write(folder, "ship-b", "task", "Ship The Thing", "open")
    dup_setup["index"].index_all()

    assert dup_setup["qa"].find_duplicates("task", readable_kbs=UNSCOPED)["count"] == 0


@pytest.mark.parametrize(
    ("entry_type", "settled"),
    [("milestone", "closed"), ("design_doc", "obsolete"), ("design_doc", "implemented")],
)
def test_other_software_kb_types_declare_what_ends_their_live_work(
    dup_setup, tmp_path, entry_type, settled
):
    live = {"milestone": "open", "design_doc": "draft"}[entry_type]
    folder = tmp_path / "kb"
    _write(folder, "one-a", entry_type, "Same Name", settled)
    _write(folder, "one-b", entry_type, "Same Name", settled)
    _write(folder, "one-c", entry_type, "Same Name", live)
    _write(folder, "one-d", entry_type, "Same Name", live)
    dup_setup["index"].index_all()

    result = dup_setup["qa"].find_duplicates(entry_type, readable_kbs=UNSCOPED)

    assert {s["id"] for p in result["pairs"] for s in (p["a"], p["b"])} == {"one-c", "one-d"}


def test_a_type_declared_only_in_kb_yaml_declares_settled_statuses(dup_setup, tmp_path):
    from pyrite.schema.core_types import resolve_type_metadata

    (tmp_path / "kb" / "kb.yaml").write_text(
        "name: dups\ntypes:\n  ticket:\n    settled_statuses: [shipped]\n", encoding="utf-8"
    )
    schema = dup_setup["config"].get_kb("dups").kb_schema
    assert resolve_type_metadata("ticket", schema)["settled_statuses"] == ["shipped"]

    folder = tmp_path / "kb"
    for entry_id, status in (("t-a", "shipped"), ("t-b", "open"), ("t-c", "open")):
        _write(folder, entry_id, "ticket", "Same Ticket", status)
    dup_setup["index"].index_all()

    result = dup_setup["qa"].find_duplicates("ticket", readable_kbs=UNSCOPED)

    assert {s["id"] for p in result["pairs"] for s in (p["a"], p["b"])} == {"t-b", "t-c"}


@pytest.mark.parametrize("value", ["0.5", None, True, [0.5]])
def test_service_refuses_a_threshold_that_is_not_a_number(dup_setup, value):
    from pyrite.exceptions import ValidationError

    with pytest.raises(ValidationError):
        dup_setup["qa"].find_duplicates("task", readable_kbs=UNSCOPED, threshold=value)
