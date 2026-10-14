#!/usr/bin/env python3
"""Ratchet for the advisory mypy job (#885): red means a change ADDED type errors.

    mypy pyrite/ --ignore-missing-imports > mypy.out || true
    python scripts/mypy_ratchet.py mypy.out            # compare to the baseline
    python scripts/mypy_ratchet.py mypy.out --update   # after fixing errors

`.mypy-baseline.json` records, per file, how many errors mypy reports and which
ones. Identity of an error is (code, message), never the line number, so edits
that shift lines do not look like new errors. The verdict is per file: a file
whose count rose fails the job and the errors above its baseline are printed;
fixing 3 errors in one file cannot hide 3 new ones in another. A file absent
from the baseline has a baseline of 0.

`--update` rewrites the baseline from the output, and refuses when any file
rose unless `--allow-increase`, so the baseline only ratchets down.

Exit: 0 no file rose; 1 a file rose (or `--update` refused); 2 the input is
not mypy output (a crash must not read as "zero errors").
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_BASELINE = REPO / ".mypy-baseline.json"

_ERROR = re.compile(
    r"^(?P<file>[^:\n]+?):(?P<line>\d+)(?::\d+)?: error: (?P<msg>.*?)(?:  \[(?P<code>[\w-]+)\])?$"
)
_SUMMARY = re.compile(r"^(Found \d+ errors? in|Success: no issues found)")

Errors = dict[str, Counter]  # file -> Counter of "code|message"


class NotMypyOutputError(ValueError):
    pass


def _identity(code: str | None, msg: str) -> str:
    # "defined at line 12" style references move with edits; the rest is stable.
    msg = re.sub(r"line \d+", "line N", msg)
    return f"{code or '-'}|{msg}"


def parse(output: str) -> Errors:
    """Group mypy's error lines by file. Raises NotMypyOutputError without a summary line."""
    errors: Errors = {}
    saw_summary = False
    for raw in output.splitlines():
        line = raw.strip()
        if _SUMMARY.match(line):
            saw_summary = True
            continue
        m = _ERROR.match(line)
        if m:
            errors.setdefault(m["file"], Counter())[_identity(m["code"], m["msg"])] += 1
    if not saw_summary:
        raise NotMypyOutputError(
            "no mypy summary line ('Found N errors' / 'Success'): mypy did not finish"
        )
    return errors


def to_baseline(errors: Errors) -> dict:
    files = {
        f: {"count": sum(c.values()), "errors": dict(sorted(c.items()))}
        for f, c in sorted(errors.items())
    }
    return {"total": sum(v["count"] for v in files.values()), "files": files}


def counts(errors: Errors) -> dict[str, int]:
    return {f: sum(c.values()) for f, c in errors.items()}


def risen(errors: Errors, baseline: dict) -> dict[str, list[str]]:
    """Files whose count rose -> the error identities above their baseline."""
    out: dict[str, list[str]] = {}
    for f, c in errors.items():
        base = baseline.get("files", {}).get(f, {"count": 0, "errors": {}})
        if sum(c.values()) > base["count"]:
            extra = c - Counter(base["errors"])
            out[f] = sorted(extra.elements()) or [f"(count rose from {base['count']})"]
    return dict(sorted(out.items()))


def summary(errors: Errors, baseline: dict, rose: dict[str, list[str]]) -> str:
    now = sum(counts(errors).values())
    was = baseline.get("total", 0)
    fell = {
        f: b["count"] - sum(errors.get(f, Counter()).values())
        for f, b in baseline.get("files", {}).items()
        if b["count"] > sum(errors.get(f, Counter()).values())
    }
    lines = [
        "### mypy ratchet",
        f"errors: {now} (baseline {was}, delta {now - was:+d})",
        f"files that rose: {len(rose)}; files that fell: {len(fell)}",
    ]
    for f, ids in rose.items():
        lines.append(f"- RISE {f}: +{len(ids)}")
    if not rose and now < was:
        lines.append(
            "Fixed errors? Lower the baseline: `python scripts/mypy_ratchet.py mypy.out --update`."
        )
    return "\n".join(lines) + "\n"


def run(
    output: str, baseline_path: Path, *, update: bool = False, allow_increase: bool = False
) -> tuple[int, str]:
    """The whole decision as a pure function: (exit code, text for the log)."""
    try:
        errors = parse(output)
    except NotMypyOutputError as e:
        return 2, f"mypy_ratchet: {e}\n"
    baseline = (
        json.loads(baseline_path.read_text())
        if baseline_path.exists()
        else {"total": 0, "files": {}}
    )
    rose = risen(errors, baseline)
    text = summary(errors, baseline, rose)
    if rose:
        text += "\nNew type errors:\n"
        for f, ids in rose.items():
            for ident in ids:
                code, _, msg = ident.partition("|")
                text += f"  {f}: {msg}  [{code}]\n"
    if update:
        if rose and not allow_increase:
            return (
                1,
                text
                + "\n--update refused: a file's count rose. Fix the new errors, or pass --allow-increase.\n",
            )
        baseline_path.write_text(json.dumps(to_baseline(errors), indent=1, sort_keys=True) + "\n")
        return 0, text + f"\nbaseline written: {baseline_path}\n"
    return (1 if rose else 0), text


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("output", nargs="?", default="-", help="mypy output file (default: stdin)")
    p.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    p.add_argument(
        "--update", action="store_true", help="rewrite the baseline (refuses to raise it)"
    )
    p.add_argument("--allow-increase", action="store_true", help="with --update, accept a rise")
    a = p.parse_args(argv)
    output = sys.stdin.read() if a.output == "-" else Path(a.output).read_text()
    code, text = run(output, a.baseline, update=a.update, allow_increase=a.allow_increase)
    sys.stdout.write(text)
    step = os.environ.get("GITHUB_STEP_SUMMARY")
    if step:
        with open(step, "a") as fh:
            fh.write(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
