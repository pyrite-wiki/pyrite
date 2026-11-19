#!/usr/bin/env python3
"""Prepare a single-file timing refresh from successful queue-run artifacts."""

from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import subprocess
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.check_shard_union import SPREAD_WARN, load
from scripts.merge_test_durations import merge, write


def dev_run(rows: list[dict]) -> int | None:
    """The API orders runs newest first; ignore other target branches."""
    for row in rows:
        branch = (row.get("head_branch") or "").removeprefix("refs/heads/")
        if branch == "dev" or branch.startswith("gh-readonly-queue/dev/"):
            return int(row["id"])
    return None


def latest_run(repo: str) -> int | None:
    page = 1
    while True:
        result = subprocess.run(
            [
                "gh",
                "api",
                f"repos/{repo}/actions/workflows/ci.yml/runs?event=merge_group&status=success&per_page=100&page={page}",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        rows = json.loads(result.stdout)["workflow_runs"]
        found = dev_run(rows)
        if found is not None:
            return found
        if len(rows) < 100:
            return None
        page += 1


def timings(path: Path) -> dict[str, float]:
    with gzip.open(path, "rt", encoding="utf-8") as source:
        values = json.load(source)
    if not isinstance(values, dict) or not values:
        raise ValueError("empty or non-object duration artifact")
    if any(
        not isinstance(key, str)
        or not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or value < 0
        for key, value in values.items()
    ):
        raise ValueError("durations must map test IDs to finite nonnegative seconds")
    return values


def estimated_loads(schedule: dict[str, float], measured: dict[str, float]) -> list[float]:
    """Greedy least-duration estimate over recorded IDs, not a collection replay."""
    average = sum(schedule.values()) / len(schedule) if schedule else 0
    weights = {node: schedule.get(node, average) for node in measured}
    allocated = [0.0, 0.0, 0.0]
    observed = [0.0, 0.0, 0.0]
    for node in sorted(weights, key=lambda key: (-weights[key], key)):
        group = min(range(3), key=lambda index: allocated[index])
        allocated[group] += weights[node]
        observed[group] += measured[node]
    return observed


def prepare(root: Path, fresh: Path, reports: Path, body: Path, run_url: str) -> bool:
    shards, _ = load(reports)
    if not shards or any(set(groups) != {1, 2, 3} for groups in shards.values()):
        raise ValueError("need all three junit shard reports for each interpreter")
    spreads = []
    for groups in shards.values():
        totals = [sum(case[2] for case in group) for group in groups.values()]
        if any(not math.isfinite(total) or total < 0 for total in totals):
            raise ValueError("invalid observed shard durations")
        average = sum(totals) / 3
        spreads.append((max(totals) - min(totals)) / average if average else 0)
    old_path = root / ".test_durations.gz"
    old = timings(old_path)
    candidate = merge(old, [timings(fresh)])
    if max(spreads) <= SPREAD_WARN or candidate == old:
        return False
    old_loads = estimated_loads(old, candidate)
    new_loads = estimated_loads(candidate, candidate)
    lines = [
        "Refs #1180",
        "",
        f"Source: {run_url} (latest successful dev merge-group CI).",
        f"Observed shard spread: {max(spreads):.1%} of the mean; refresh threshold: {SPREAD_WARN:.0%}.",
        "",
        "| shard | old schedule / latest seconds | refreshed estimate / seconds |",
        "|---:|---:|---:|",
    ]
    lines.extend(
        f"| {index + 1} | {old_loads[index]:.3f} | {new_loads[index]:.3f} |" for index in range(3)
    )
    lines.extend(
        [
            "",
            "Estimates use recorded test IDs and a deterministic greedy least-duration schedule; they are not a replay of current pytest collection or a wall-clock prediction.",
            "Only `.test_durations.gz` changes. Existing timing merge/write helpers are used; no test is skipped or selected by this refresh.",
            "",
            "Prepared by the scheduled duration refresh; CI and maintainer review remain required.",
        ]
    )
    body.write_text("\n".join(lines) + "\n", encoding="utf-8")
    write(old_path, candidate)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--latest", action="store_true")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--fresh", type=Path)
    parser.add_argument("--reports", type=Path)
    parser.add_argument("--body", type=Path)
    parser.add_argument("--run-url", default="")
    args = parser.parse_args()
    if args.latest:
        run = latest_run(os.environ["GITHUB_REPOSITORY"])
        print(f"run_id={run or ''}")
    else:
        if not all((args.fresh, args.reports, args.body, args.run_url)):
            parser.error("--fresh, --reports, --body and --run-url are required")
        changed = prepare(args.root, args.fresh, args.reports, args.body, args.run_url)
        print(f"changed={str(changed).lower()}")


if __name__ == "__main__":
    main()
