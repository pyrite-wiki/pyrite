#!/usr/bin/env python3
"""Prove the CI test shards ran every test exactly once, and show their balance.

CI splits each interpreter's pytest run into N shards (pytest-split). The
split is a partition by construction, but "by construction" is a claim about
a plugin; this checks the result.

    count FILE            print the number of tests in `pytest --collect-only -qq` output
    check DIR --shards N  per interpreter: no testcase appears twice across the
                          shards' junit reports, all N reports are present, and
                          their testcase total equals the unsplit collection
    balance DIR           shard times (summed testcase seconds) as a markdown table

DIR holds the downloaded artifacts: `junit-test-<py>-<k>*/junit-test-<py>-<k>.xml`
and `collected-<py>*/collected-<py>.txt`.
"""

from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

JUNIT = re.compile(r"junit-test-(?P<py>[\d.]+)-(?P<shard>\d+)\.xml$")
COLLECTED = re.compile(r"collected-(?P<py>[\d.]+)\.txt$")
SPREAD_WARN = 0.25


def count_collected(text: str) -> int:
    """`8143/8200 tests collected (57 deselected)` or `8143 tests collected`."""
    found = re.findall(r"(\d+)(?:/\d+)? tests? collected", text)
    if not found:
        raise ValueError("no `N tests collected` line in the collect-only output")
    return int(found[-1])


def _cases(path: Path) -> list[tuple[str, str, float]]:
    root = ET.parse(path).getroot()
    return [
        (c.get("classname", ""), c.get("name", ""), float(c.get("time", 0) or 0))
        for c in root.iter("testcase")
    ]


def load(directory: Path):
    """({py: {shard: [cases]}}, {py: collected count})."""
    shards: dict[str, dict[int, list]] = defaultdict(dict)
    collected: dict[str, int] = {}
    for path in sorted(directory.rglob("*")):
        if m := JUNIT.search(path.name):
            shards[m["py"]][int(m["shard"])] = _cases(path)
        elif m := COLLECTED.search(path.name):
            collected[m["py"]] = int(path.read_text().strip())
    return shards, collected


def check(shards, collected, expected_shards: int) -> list[str]:
    problems = []
    if not shards:
        problems.append("no shard junit reports found")
    for py, by_shard in sorted(shards.items()):
        missing = set(range(1, expected_shards + 1)) - set(by_shard)
        if missing:
            problems.append(f"{py}: shard(s) {sorted(missing)} produced no junit report")
        seen: dict[tuple[str, str], int] = {}
        for shard, cases in sorted(by_shard.items()):
            for cls, name, _ in cases:
                if (cls, name) in seen:
                    problems.append(
                        f"{py}: {cls}::{name} ran in shard {seen[(cls, name)]} and shard {shard}"
                    )
                seen[(cls, name)] = shard
        total = sum(len(c) for c in by_shard.values())
        if py not in collected:
            problems.append(f"{py}: no unsplit collection count to compare with")
        elif total != collected[py]:
            problems.append(
                f"{py}: the shards ran {total} tests, the unsplit collection holds {collected[py]}"
            )
    return problems


def balance(shards) -> str:
    out = []
    for py, by_shard in sorted(shards.items()):
        times = {k: sum(t for *_, t in v) for k, v in by_shard.items()}
        if not times:
            continue
        mean = sum(times.values()) / len(times)
        spread = (max(times.values()) - min(times.values())) / mean if mean else 0.0
        out.append(
            f"### Shard balance: Python {py}\n\n| shard | tests | seconds |\n|---:|---:|---:|"
        )
        for k in sorted(times):
            out.append(f"| {k} | {len(by_shard[k])} | {times[k]:.0f} |")
        note = f"spread {spread:.0%} of the mean"
        if spread > SPREAD_WARN:
            note += f" (over {SPREAD_WARN:.0%}: `.test_durations` is stale, refresh it from the `test-durations` artifact)"
        out.append(f"\n{note}\n")
    return "\n".join(out)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("count").add_argument("file", type=Path)
    c = sub.add_parser("check")
    c.add_argument("dir", type=Path)
    c.add_argument("--shards", type=int, required=True)
    sub.add_parser("balance").add_argument("dir", type=Path)
    args = ap.parse_args(argv[1:])

    if args.cmd == "count":
        print(count_collected(args.file.read_text()))
        return 0
    shards, collected = load(args.dir)
    if args.cmd == "balance":
        print(balance(shards))
        return 0
    problems = check(shards, collected, args.shards)
    if problems:
        print("### Shard union check: FAILED\n")
        for p in problems:
            print(f"- {p}")
        return 1
    rows = ", ".join(f"{py}: {sum(len(c) for c in s.values())}" for py, s in sorted(shards.items()))
    print(f"### Shard union check\n\nevery collected test ran in exactly one shard ({rows}).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
