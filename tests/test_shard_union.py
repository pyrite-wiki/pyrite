"""scripts/check_shard_union.py and scripts/merge_test_durations.py (tests Q2)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


union = _load("check_shard_union")
durations = _load("merge_test_durations")


def _junit(path: Path, tests: list[tuple[str, str, float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cases = "".join(f'<testcase classname="{c}" name="{n}" time="{t}"/>' for c, n, t in tests)
    path.write_text(f"<testsuites><testsuite>{cases}</testsuite></testsuites>")


def _tree(tmp_path: Path, shards: dict[int, list], collected: int | None, py="3.12") -> Path:
    for k, tests in shards.items():
        _junit(tmp_path / f"junit-test-{py}-{k}" / f"junit-test-{py}-{k}.xml", tests)
    if collected is not None:
        d = tmp_path / f"collected-{py}"
        d.mkdir()
        (d / f"collected-{py}.txt").write_text(f"{collected}\n")
    return tmp_path


A, B, C, D = ("t", "a", 1.0), ("t", "b", 1.0), ("t", "c", 1.0), ("t", "d", 1.0)


class TestCount:
    @pytest.mark.parametrize(
        ("text", "n"),
        [
            ("tests/a.py: 3\n8143/8200 tests collected (57 deselected) in 20.1s\n", 8143),
            ("172 tests collected in 0.05s\n", 172),
            ("1 test collected in 0.01s", 1),
        ],
    )
    def test_reads_the_collected_count(self, text, n):
        assert union.count_collected(text) == n

    def test_refuses_output_without_a_count(self):
        with pytest.raises(ValueError):
            union.count_collected("ERROR collecting")


class TestUnion:
    def test_a_clean_partition_passes(self, tmp_path):
        shards, collected = union.load(_tree(tmp_path, {1: [A, B], 2: [C], 3: [D]}, 4))
        assert union.check(shards, collected, 3) == []

    def test_a_test_in_two_shards_fails(self, tmp_path):
        shards, collected = union.load(_tree(tmp_path, {1: [A, B], 2: [B], 3: [C]}, 4))
        problems = union.check(shards, collected, 3)
        assert any("ran in shard 1 and shard 2" in p for p in problems)

    def test_a_dropped_test_fails(self, tmp_path):
        shards, collected = union.load(_tree(tmp_path, {1: [A], 2: [B], 3: [C]}, 4))
        assert any("shards ran 3 tests" in p for p in union.check(shards, collected, 3))

    def test_a_missing_shard_fails(self, tmp_path):
        shards, collected = union.load(_tree(tmp_path, {1: [A, B], 3: [C, D]}, 4))
        assert any("shard(s) [2]" in p for p in union.check(shards, collected, 3))

    def test_no_collection_count_fails(self, tmp_path):
        shards, collected = union.load(_tree(tmp_path, {1: [A], 2: [B], 3: [C]}, None))
        assert any("no unsplit collection" in p for p in union.check(shards, collected, 3))

    def test_no_reports_at_all_fails(self):
        assert union.check({}, {}, 3)


class TestBalance:
    def test_a_lopsided_split_says_the_durations_are_stale(self, tmp_path):
        slow = [("t", str(i), 10.0) for i in range(5)]
        shards, _ = union.load(_tree(tmp_path, {1: slow, 2: [A], 3: [B]}, None))
        assert "stale" in union.balance(shards)

    def test_an_even_split_is_quiet(self, tmp_path):
        shards, _ = union.load(_tree(tmp_path, {1: [A], 2: [B], 3: [C]}, None))
        assert "stale" not in union.balance(shards)


class TestMergeDurations:
    def test_fresh_timings_from_each_shard_win_over_the_old_file(self):
        old = {"a": 1.0, "b": 1.0, "c": 1.0}
        s1 = {"a": 2.0, "b": 1.0, "c": 1.0}  # shard 1 ran `a`
        s2 = {"a": 1.0, "b": 3.0, "c": 1.0, "new": 0.5}  # shard 2 ran `b` and `new`
        assert durations.merge(old, [s1, s2]) == {"a": 2.0, "b": 3.0, "c": 1.0, "new": 0.5}

    def test_an_unreadable_old_file_is_an_empty_one(self, tmp_path):
        assert durations._load(tmp_path / "nope.json") == {}
