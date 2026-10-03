#!/usr/bin/env python3
"""The experimental ratchet: an experimental test may be broken, never silently (#657).

Tests of experimental surfaces carry ``@pytest.mark.experimental`` (applied
from tests/experimental_surface.py). They do not gate a merge; the CI job
`experimental` runs them and hands the JUnit report here.

  check        Compare the report with tests/experimental_known_failures.txt.
               Exit 1 on a failure that is not in the file (a NEW failure), or
               when the file grew against --base-known (it can only shrink).
               Report a known failure that now passes, or no longer exists,
               so it can be removed. Exit 2 when the report is missing: a run
               that never happened is not a pass.
  file-issues  On a push to `dev` only (the workflow decides): for each test
               file with a new failure, open an `experimental-broken` issue
               naming the tests, the commit and the run, or comment on the
               open one for that file with the tests it does not name yet.
               Needs `gh` and a token with issues: write; nothing else.

One issue per test file, not per test: an import error fails every test in a
module, and three hundred issues would bury the one that matters. The issue
names each test.

The JUnit report must be xunit1 (``-o junit_family=xunit1``): only that family
records each case's file, from which the pytest node id is rebuilt.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path

LABEL = "experimental-broken"
LABEL_COLOR = "d93f0b"
LABEL_DESCRIPTION = "An experimental test newly failing on dev (#657)"
TITLE_PREFIX = f"{LABEL}: "
MESSAGE_LIMIT = 600

# --------------------------------------------------------------------------
# Reading


def _nodeid(case: ET.Element) -> str:
    file = case.get("file") or ""
    classname = case.get("classname") or ""
    name = case.get("name") or ""
    if not file:
        raise ValueError(f"testcase {classname}::{name} has no file: run with junit_family=xunit1")
    if not classname:  # a collection error: the module itself failed
        return file
    module = file[: -len(".py")].replace("/", ".") if file.endswith(".py") else file
    rest = classname[len(module) + 1 :] if classname.startswith(module + ".") else ""
    parts = [file, *(p for p in rest.split(".") if p), name]
    return "::".join(parts)


def read_junit(path: Path) -> dict[str, str]:
    """Node id -> "passed" | "failed" | "skipped". A failure, an error (setup,
    teardown, collection) and an xfail-strict pass all count as failed."""
    outcomes: dict[str, str] = {}
    root = ET.parse(path).getroot()
    for case in root.iter("testcase"):
        tags = {child.tag for child in case}
        if tags & {"failure", "error"}:
            outcome = "failed"
        elif "skipped" in tags:
            outcome = "skipped"
        else:
            outcome = "passed"
        nodeid = _nodeid(case)
        # A test can appear twice (call passed, teardown errored): worst wins.
        if outcomes.get(nodeid) != "failed":
            outcomes[nodeid] = outcome
    return outcomes


def read_messages(path: Path) -> dict[str, str]:
    messages: dict[str, str] = {}
    for case in ET.parse(path).getroot().iter("testcase"):
        for child in case:
            if child.tag in ("failure", "error"):
                text = (child.get("message") or child.text or "").strip()
                messages[_nodeid(case)] = text[:MESSAGE_LIMIT]
    return messages


def read_known(path: Path) -> set[str]:
    """One node id per line; ``#`` starts a comment (after two spaces when it
    follows a node id, since a parameter id may contain ``#``)."""
    known: set[str] = set()
    for raw in path.read_text().splitlines():
        line = raw.split("  #", 1)[0].strip()
        if line and not line.startswith("#"):
            known.add(line)
    return known


# --------------------------------------------------------------------------
# Comparing


@dataclass
class Result:
    new: list[str] = field(default_factory=list)
    still_failing: list[str] = field(default_factory=list)
    fixed: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    grown: list[str] = field(default_factory=list)
    messages: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.new and not self.grown


def compare(outcomes: dict[str, str], known: set[str]) -> Result:
    failed = {n for n, o in outcomes.items() if o == "failed"}
    return Result(
        new=sorted(failed - known),
        still_failing=sorted(failed & known),
        fixed=sorted(n for n in known if outcomes.get(n) == "passed"),
        stale=sorted(n for n in known if n not in outcomes),
    )


def grown(known: set[str], base: Path) -> list[str]:
    """Entries the head adds to the base's file. No base file: this is the seed."""
    if not base.exists():
        return []
    return sorted(known - read_known(base))


def summary_markdown(result: Result, known_path: str) -> str:
    out = ["## Experimental tests (non-blocking, #657)", ""]
    if result.ok:
        out.append(
            "No new failures. A red experimental test does not block a merge; "
            "a new one is never silent."
        )
    if result.new:
        out += [
            f"### New failures ({len(result.new)})",
            "",
            f"Not in `{known_path}`. Fix them; on `dev` each test file gets an `{LABEL}` issue.",
            "",
        ]
        out += [f"- `{n}`" for n in result.new]
        out.append("")
    if result.grown:
        out += [
            f"### `{known_path}` grew ({len(result.grown)})",
            "",
            "The known-failures list can only shrink: fix the test instead.",
            "",
        ]
        out += [f"- `{n}`" for n in result.grown]
        out.append("")
    if result.fixed:
        out += [f"### Known failures that now pass: remove them from `{known_path}`", ""]
        out += [f"- `{n}`" for n in result.fixed]
        out.append("")
    if result.stale:
        out += [f"### Known failures that no longer exist: remove them from `{known_path}`", ""]
        out += [f"- `{n}`" for n in result.stale]
        out.append("")
    if result.still_failing:
        out += [f"Still failing, already known: {len(result.still_failing)}.", ""]
    return "\n".join(out)


# --------------------------------------------------------------------------
# Filing


def _by_file(nodeids: list[str]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for nodeid in nodeids:
        groups.setdefault(nodeid.split("::", 1)[0], []).append(nodeid)
    return groups


def _gh(args: list[str]) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def _test_lines(nodeids: list[str], messages: dict[str, str]) -> str:
    lines = []
    for nodeid in nodeids:
        lines.append(f"- `{nodeid}`")
        message = messages.get(nodeid, "").splitlines()
        if message:
            lines.append(f"  > {message[0][:200]}")
    return "\n".join(lines)


def file_issues(result: dict, sha: str, run_url: str, dry_run: bool = False) -> int:
    new: list[str] = result.get("new", [])
    if not new:
        return 0
    messages: dict[str, str] = result.get("messages", {})
    groups = _by_file(new)

    if dry_run:
        for file, tests in groups.items():
            print(f"would file or comment: {TITLE_PREFIX}{file} ({len(tests)} tests)")
        return 0

    _gh(
        [
            "label",
            "create",
            LABEL,
            "--color",
            LABEL_COLOR,
            "--description",
            LABEL_DESCRIPTION,
            "--force",
        ]
    )
    open_issues = json.loads(
        _gh(
            [
                "issue",
                "list",
                "--label",
                LABEL,
                "--state",
                "open",
                "--limit",
                "500",
                "--json",
                "number,title",
            ]
        )
        or "[]"
    )
    by_title = {i["title"]: i["number"] for i in open_issues}

    for file, tests in sorted(groups.items()):
        title = f"{TITLE_PREFIX}{file}"
        number = by_title.get(title)
        if number is None:
            body = (
                f"Experimental tests in `{file}` newly failing on `dev` "
                f"(not in `tests/experimental_known_failures.txt`).\n\n"
                f"{_test_lines(tests, messages)}\n\n"
                f"Commit: {sha}\nRun: {run_url}\n\n"
                "Experimental tests do not block a merge (#657). Fix the test or "
                "the feature; the known-failures list can only shrink. Filed by "
                "scripts/experimental_ratchet.py."
            )
            _gh(["issue", "create", "--title", title, "--label", LABEL, "--body", body])
            print(f"opened: {title}")
            continue
        seen = json.loads(_gh(["issue", "view", str(number), "--json", "body,comments"]))
        text = seen.get("body", "") + "\n".join(c.get("body", "") for c in seen.get("comments", []))
        unseen = [t for t in tests if f"`{t}`" not in text]
        if not unseen:
            print(f"already named in #{number}: {file}")
            continue
        body = f"Also newly failing at {sha} ({run_url}):\n\n{_test_lines(unseen, messages)}"
        _gh(["issue", "comment", str(number), "--body", body])
        print(f"commented on #{number}: {file}")
    return 0


# --------------------------------------------------------------------------
# CLI


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="compare a JUnit report with the known failures")
    check.add_argument("--junit", type=Path, required=True)
    check.add_argument("--known", type=Path, required=True)
    check.add_argument("--base-known", type=Path, help="the base branch's known-failures file")
    check.add_argument("--summary", type=Path, help="append the markdown report here")
    check.add_argument("--result-json", type=Path, help="write the result for file-issues")

    issues = sub.add_parser("file-issues", help="open or comment on experimental-broken issues")
    issues.add_argument("--result-json", type=Path, required=True)
    issues.add_argument("--sha", required=True)
    issues.add_argument("--run-url", required=True)
    issues.add_argument("--dry-run", action="store_true", help="print, call nothing")

    opts = parser.parse_args(argv)

    if opts.command == "file-issues":
        return file_issues(
            json.loads(opts.result_json.read_text()), opts.sha, opts.run_url, opts.dry_run
        )

    if not opts.junit.exists():
        print(f"experimental-ratchet: no report at {opts.junit}; the run did not happen")
        return 2
    known = read_known(opts.known)
    result = compare(read_junit(opts.junit), known)
    result.messages = {n: m for n, m in read_messages(opts.junit).items() if n in result.new}
    if opts.base_known is not None:
        result.grown = grown(known, opts.base_known)

    report = summary_markdown(result, str(opts.known))
    print(report)
    if opts.summary is not None:
        with opts.summary.open("a") as fh:
            fh.write(report + "\n")
    if opts.result_json is not None:
        opts.result_json.write_text(json.dumps(asdict(result), indent=2))
    for nodeid in result.fixed + result.stale:
        print(f"::warning title=experimental ratchet::remove {nodeid} from {opts.known}")
    if result.grown:
        print(f"experimental-ratchet: {opts.known} can only shrink; it grew by {len(result.grown)}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
