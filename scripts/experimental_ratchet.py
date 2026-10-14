#!/usr/bin/env python3
"""The experimental ratchet: an experimental test may be broken, never silently (#657).

Tests of experimental surfaces carry ``@pytest.mark.experimental`` (applied
from tests/experimental_surface.py). They do not gate a merge; the CI job
`experimental` runs them and hands the JUnit report here.

Never silently means every change in what the experimental set reports
reaches a person: a new failure, a line added to the known-failures list, a
run that did not complete.

  check        Compare the report with tests/experimental_known_failures.txt
               and, with --open-issues, the open `experimental-broken` issues.
               Exit 1 on a NEW failure (in neither), or when the file grew
               against --base-known (it can only shrink). Exit 2 when the run
               did not complete: no report, or --run-exit other than 0/1.
               A failure an open issue already names is "filed", not new, so
               a PR's red keeps meaning news. Reports a known failure that now
               passes or no longer exists (remove it), and a filed test that
               passes again (close its issue).
  file-issues  On a push to `dev` only (the workflow decides): open or
               comment on an `experimental-broken` issue for each test file
               with a new failure, for a grown known-failures list, and for a
               run that did not complete, including one that left no result
               at all (--job-result names how the job ended). Needs `gh` and a
               token with issues: write; nothing else.

One issue per test file, not per test: an import error fails every test in a
module, and three hundred issues would bury the one that matters. The issue
names each test.

The JUnit report must be xunit1 (``-o junit_family=xunit1``): only that family
records each case's file, from which the pytest node id is rebuilt.
"""

from __future__ import annotations

import argparse
import json
import re
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
    filed: dict[str, int] = field(default_factory=dict)  # failing, named in an open issue
    still_failing: list[str] = field(default_factory=list)
    fixed: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    grown: list[str] = field(default_factory=list)
    filed_now_passing: dict[str, int] = field(default_factory=dict)
    incomplete: str | None = None
    issues_unreadable: bool = False
    messages: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.new and not self.grown and self.incomplete is None


def compare(
    outcomes: dict[str, str], known: set[str], filed: dict[str, int] | None = None
) -> Result:
    filed = filed or {}
    failed = {n for n, o in outcomes.items() if o == "failed"}
    unknown = failed - known
    return Result(
        new=sorted(n for n in unknown if n not in filed),
        filed={n: filed[n] for n in sorted(unknown) if n in filed},
        still_failing=sorted(failed & known),
        fixed=sorted(n for n in known if outcomes.get(n) == "passed"),
        stale=sorted(n for n in known if n not in outcomes),
        filed_now_passing={n: i for n, i in sorted(filed.items()) if outcomes.get(n) == "passed"},
    )


def grown(known: set[str], base: Path) -> list[str]:
    """Entries the head adds to the base's file. No base file: this is the seed."""
    if not base.exists():
        return []
    return sorted(known - read_known(base))


# pytest's exit codes: 0 all passed, 1 some tests failed. Anything else (2
# interrupted, 3 internal error, 4 usage error, 5 no tests collected, a signal,
# or no status because the step was killed) means the report is not the whole
# set, and a partial report must not read as a pass.
COMPLETE_EXITS = {"0", "1"}


def incompleteness(pytest_exit: str | None, junit: Path) -> str | None:
    if not junit.exists():
        return f"no JUnit report at {junit}: the run did not happen or did not finish"
    if pytest_exit is None:
        return None
    if pytest_exit.strip() not in COMPLETE_EXITS:
        status = pytest_exit.strip() or "no status (killed or timed out)"
        return f"pytest exited {status}: the report may be partial"
    return None


_NAMED = re.compile(r"`([^`\s]+\.py(?:::[^`]*)?)`")


def open_issue_tests() -> dict[str, int]:
    """Node id -> number of the open experimental-broken issue that names it
    (in its body or a comment). Raises when the issues cannot be read."""
    issues = json.loads(
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
                "number,title,body,comments",
            ]
        )  # fmt: skip
        or "[]"
    )
    named: dict[str, int] = {}
    for issue in issues:
        if not issue.get("title", "").startswith(TITLE_PREFIX):
            continue
        texts = [issue.get("body") or ""] + [
            c.get("body") or "" for c in issue.get("comments") or []
        ]
        for text in texts:
            for nodeid in _NAMED.findall(text):
                named.setdefault(nodeid, issue["number"])
    return named


def _bullets(items) -> list[str]:
    return [f"- `{n}`" for n in items] + [""]


def summary_markdown(result: Result, known_path: str) -> str:
    out = ["## Experimental tests (non-blocking, #657)", ""]
    if result.incomplete:
        out += ["### The run did not complete", "", result.incomplete, ""]
    if result.ok:
        out += [
            "No new failures. A red experimental test does not block a merge; "
            "a new one is never silent.",
            "",
        ]
    if result.issues_unreadable:
        out += [
            f"Could not read the open `{LABEL}` issues: every unknown failure is counted new.",
            "",
        ]
    if result.new:
        out += [
            f"### New failures ({len(result.new)})",
            "",
            f"Not in `{known_path}` and not in an open `{LABEL}` issue. Fix them; on `dev` "
            "each test file gets an issue.",
            "",
            *_bullets(result.new),
        ]
    if result.grown:
        out += [
            f"### `{known_path}` grew ({len(result.grown)})",
            "",
            "The known-failures list can only shrink: fix the test instead.",
            "",
            *_bullets(result.grown),
        ]
    if result.filed:
        out += [f"### Already filed on `dev` ({len(result.filed)}): not news", ""]
        out += [f"- `{n}` (#{i})" for n, i in result.filed.items()] + [""]
    if result.filed_now_passing:
        out += ["### Filed tests that pass again: close their issues when all do", ""]
        out += [f"- `{n}` (#{i})" for n, i in result.filed_now_passing.items()] + [""]
    if result.fixed:
        out += [
            f"### Known failures that now pass: remove them from `{known_path}`",
            "",
            *_bullets(result.fixed),
        ]
    if result.stale:
        out += [
            f"### Known failures that no longer exist: remove them from `{known_path}`",
            "",
            *_bullets(result.stale),
        ]
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


GROWN_TITLE = f"{TITLE_PREFIX}tests/experimental_known_failures.txt grew"
INCOMPLETE_TITLE = f"{TITLE_PREFIX}the experimental run did not complete"


@dataclass
class _Filing:
    title: str
    intro: str
    tests: list[str]  # named once each; [] for an event reported every time


def _filings(result: dict) -> list[_Filing]:
    filings = []
    if result.get("incomplete"):
        filings.append(
            _Filing(
                INCOMPLETE_TITLE,
                f"The experimental job on `dev` did not produce a complete report: "
                f"{result['incomplete']}",
                [],
            )
        )
    if result.get("grown"):
        filings.append(
            _Filing(
                GROWN_TITLE,
                "Lines were added to `tests/experimental_known_failures.txt`, which can "
                "only shrink. Fix these tests and remove the lines:",
                list(result["grown"]),
            )
        )
    for file, tests in sorted(_by_file(result.get("new", [])).items()):
        filings.append(
            _Filing(
                f"{TITLE_PREFIX}{file}",
                f"Experimental tests in `{file}` newly failing on `dev` "
                "(not in `tests/experimental_known_failures.txt`):",
                tests,
            )
        )
    return filings


def file_issues(
    result: dict | None, sha: str, run_url: str, dry_run: bool = False, job_result: str = ""
) -> int:
    if result is None:
        result = {
            "incomplete": f"the experimental job ended '{job_result or 'unknown'}' and left no "
            "result (a timeout or cancellation stops it before the ratchet runs)"
        }
    filings = _filings(result)
    if not filings:
        return 0
    messages: dict[str, str] = result.get("messages", {})

    if dry_run:
        for f in filings:
            print(f"would file or comment: {f.title} ({len(f.tests)} tests)")
        return 0

    _gh(
        [
            "label", "create", LABEL, "--color", LABEL_COLOR,
            "--description", LABEL_DESCRIPTION, "--force",
        ]
    )  # fmt: skip
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
        )  # fmt: skip
        or "[]"
    )
    by_title = {i["title"]: i["number"] for i in open_issues}
    where = f"Commit: {sha}\nRun: {run_url}"

    for f in filings:
        number = by_title.get(f.title)
        if number is None:
            body = (
                f"{f.intro}\n\n"
                + (f"{_test_lines(f.tests, messages)}\n\n" if f.tests else "")
                + f"{where}\n\n"
                "Experimental tests do not block a merge, but are never silent (#657). "
                "Filed by scripts/experimental_ratchet.py."
            )
            _gh(["issue", "create", "--title", f.title, "--label", LABEL, "--body", body])
            print(f"opened: {f.title}")
            continue
        if f.tests:
            seen = json.loads(_gh(["issue", "view", str(number), "--json", "body,comments"]))
            text = seen.get("body", "") + "\n".join(
                c.get("body", "") for c in seen.get("comments", [])
            )
            unseen = [t for t in f.tests if f"`{t}`" not in text]
            if not unseen:
                print(f"already named in #{number}: {f.title}")
                continue
            body = f"Also at {sha} ({run_url}):\n\n{_test_lines(unseen, messages)}"
        else:
            body = f"Again at {sha} ({run_url}): {f.intro}"
        _gh(["issue", "comment", str(number), "--body", body])
        print(f"commented on #{number}: {f.title}")
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

    check.add_argument(
        "--run-exit",
        help="pytest's exit status; anything but 0 or 1 (or empty) marks the run incomplete",
    )
    check.add_argument(
        "--open-issues",
        action="store_true",
        help=f"treat failures named in an open {LABEL} issue as filed, not new (needs gh)",
    )

    issues = sub.add_parser("file-issues", help="open or comment on experimental-broken issues")
    issues.add_argument("--result-json", type=Path, required=True)
    issues.add_argument("--sha", required=True)
    issues.add_argument("--run-url", required=True)
    issues.add_argument("--job-result", default="", help="the experimental job's conclusion")
    issues.add_argument("--dry-run", action="store_true", help="print, call nothing")

    opts = parser.parse_args(argv)

    if opts.command == "file-issues":
        result = json.loads(opts.result_json.read_text()) if opts.result_json.exists() else None
        return file_issues(result, opts.sha, opts.run_url, opts.dry_run, opts.job_result)

    known = read_known(opts.known)
    filed: dict[str, int] = {}
    unreadable = False
    if opts.open_issues:
        try:
            filed = open_issue_tests()
        except (subprocess.CalledProcessError, OSError, ValueError) as exc:
            print(f"experimental-ratchet: could not read the open {LABEL} issues ({exc})")
            unreadable = True
    incomplete = incompleteness(opts.run_exit, opts.junit)
    outcomes = read_junit(opts.junit) if opts.junit.exists() else {}
    result = compare(outcomes, known, filed)
    result.incomplete = incomplete
    result.issues_unreadable = unreadable
    if opts.junit.exists():
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
    if result.incomplete:
        return 2
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
