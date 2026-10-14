#!/usr/bin/env python3
"""Merge the shards' `.test_durations` files into one.

Each CI shard reads the committed `.test_durations` (pytest-split) and, with
--store-durations, rewrites it with the timings of the tests IT ran, keeping
everything else as it read it. So a test's fresh timing is the value that
differs from the old file. This takes the old file plus the shards' copies and
writes one file with every fresh timing.

    python scripts/merge_test_durations.py --old .test_durations --out merged shard1 shard2 shard3

CI uploads the result as the `test-durations` artifact (`.gz`); committing it as
`.test_durations.gz` refreshes the balance (the shards gunzip it to
`.test_durations` before pytest-split reads it). Nothing depends on that for
correctness: a missing or stale file makes pytest-split split by count or by
the average, never drop a test.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from pathlib import Path


def merge(old: dict[str, float], shards: list[dict[str, float]]) -> dict[str, float]:
    merged = dict(old)
    for shard in shards:
        for test, secs in shard.items():
            if secs != old.get(test):
                merged[test] = secs
    return dict(sorted(merged.items()))


def _load(path: Path) -> dict[str, float]:
    try:
        raw = gzip.open(path, "rt").read() if path.suffix == ".gz" else path.read_text()
        data = json.loads(raw)
    except (OSError, ValueError, EOFError):
        return {}
    return data if isinstance(data, dict) else {}


def write(path: Path, durations: dict[str, float]) -> None:
    """Compact, rounded to the millisecond; `.gz` to stay under the repo's 500 KB file limit."""
    text = json.dumps({k: round(v, 3) for k, v in durations.items()}, separators=(",", ":")) + "\n"
    if path.suffix == ".gz":
        with open(path, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
            gz.write(text.encode())
    else:
        path.write_text(text)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--old", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("shards", nargs="*", type=Path)
    args = ap.parse_args(argv[1:])
    merged = merge(_load(args.old), [_load(p) for p in args.shards])
    write(args.out, merged)
    print(f"{len(merged)} timings")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
