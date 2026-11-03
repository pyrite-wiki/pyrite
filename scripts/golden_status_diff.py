#!/usr/bin/env python3
"""Report golden outcome changes, never body-level scope or filtered-list widening.

Read both revisions from git. Warning-only: no API credentials or PR comments.
"""

import argparse
import collections
import importlib.util
import json
import os
import subprocess
from pathlib import Path

GOLDENS = "tests/characterization/goldens"


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True, encoding="utf-8")


def read_revision(repo, ref):
    result = {}
    for name in git(repo, "ls-tree", "-r", "--name-only", ref, "--", GOLDENS).splitlines():
        if not name.endswith(".json"):
            continue
        surface = Path(name).name.split(".")[0]
        data = json.loads(git(repo, "show", f"{ref}:{name}"))
        for key, value in data.items():
            identity = (surface, key)
            if identity in result:
                raise ValueError(f"Duplicate golden case: {identity}")
            result[identity] = value
    return result


def refusal_codes():
    # Read the declared class codes, including inherited codes on subclasses.
    # Family roots express refusal semantics; no second table of code strings.
    source = Path(__file__).resolve().parents[1] / "pyrite/exceptions.py"
    spec = importlib.util.spec_from_file_location("golden_refusals", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    families = (
        module.AccessDenied,
        module.EntryNotFoundError,
        module.KBNotFoundError,
        module.KBReadOnlyError,
        module.KBProtectedError,
    )
    return {
        cls.error_code
        for cls in vars(module).values()
        if isinstance(cls, type) and issubclass(cls, families) and cls is not module.AccessDenied
    }


def outcome(surface, value):
    if value is None:
        return "absent"
    if surface in {"rest", "access_deciding"} or (surface == "unscoped" and "status" in value):
        return str(value["status"])
    return str(value.get("error_code") or ("error" if value.get("error") else "ok"))


def escape(text):
    return str(text).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def report(before, after, codes):
    counts = collections.Counter()
    details = []
    warnings = []
    for surface, key in sorted(before.keys() | after.keys()):
        old, new = before.get((surface, key)), after.get((surface, key))
        if old == new:
            continue
        if surface == "error_bodies":
            details.append(f"- Error mapping changed: `{escape(key)}` (not access widening)")
            continue
        a, b = outcome(surface, old), outcome(surface, new)
        if a == b:
            continue
        parts = key.split(" | ")
        principal = parts[1] if len(parts) > 1 else "unknown"
        counts[(surface, principal, a, b)] += 1
        widening = (
            (a in {"401", "403", "404", "422"} and b.startswith("2"))
            if surface in {"rest", "access_deciding"}
            or (surface == "unscoped" and a.isdigit() and b.isdigit())
            else a in codes and b == "ok"
        )
        flag = " **DENY → ALLOW**" if widening else ""
        details.append(f"- `{surface}` `{escape(key)}`: {a} → {b}{flag}")
        if widening:
            warnings.append(f"{surface}: {key}: {a} -> {b}")
    lines = [
        "## Golden outcome changes",
        "",
        "| Surface | Principal | Before | After | Cases |",
        "|---|---|---|---|---|",
    ]
    lines += [
        f"| {s} | {escape(p)} | {a} | {b} | {n} |" for (s, p, a, b), n in sorted(counts.items())
    ]
    lines += [
        "",
        *(details or ["No outcome-class changes."]),
        "",
        "Body-level scope changes are outside this report.",
    ]
    return "\n".join(lines) + "\n", warnings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--base", default="HEAD^1")
    parser.add_argument("--head", default="HEAD")
    args = parser.parse_args()
    summary, warnings = report(
        read_revision(args.repo, args.base), read_revision(args.repo, args.head), refusal_codes()
    )
    print(summary)
    if path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(path).open("a", encoding="utf-8") as stream:
            stream.write(summary)
    for warning in warnings:
        safe = warning.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::warning::{safe}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
