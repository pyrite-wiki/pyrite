"""The one definition of a verified commit.

    A commit is VERIFIED when every required check (`gate`) is green on that
    exact SHA, in the GitHub repository it is being pushed to or released from.

Precisely, and `decide` is the only place it is produced: for each required
name there is at least one check run with exactly that name, made by the
GitHub Actions app, for exactly the full SHA asked about; and in every
workflow run (check suite) that has one, its newest -- by a `started_at` this
code has validated, when the runs disagree -- is completed with conclusion
`success`. `skipped` is not success. A run from another app is not the check.

What it is evidence of: CI accepted this commit. Every job `gate` needs ended
in success or was skipped by CI's change classifier. For a commit the merge
queue tested that is the full matrix; for a pull-request run it is one
interpreter, and jobs judged irrelevant did not run. It is not evidence that
any particular selection of tests ran, which is why the hook still runs when
the full suite or the experimental tests are asked for by name.

Two programs need that answer and, until 0.25.7, each had its own idea of it.
`scripts/release.py` asked GitHub (ADR-0032: the tagged commit is one CI
already passed). The pre-push hook (`scripts/test-affected`) knew only its own
pass stamps, so on release day it re-ran the full local suite on a commit CI
had passed hours earlier, in a venv that was missing a dev dependency, and
blocked the push to `main`. Both now call `verdict_for` here, and
`tests/test_verified_commit.py` fails if a second lookup appears under
`scripts/`.

The rule this module exists to keep: FAIL CLOSED. "Verified" lets a push
through with no local test run, so it is the only answer that needs evidence.
Anything else -- no `gh`, no network, a timeout, an error from the API, an
answer that is not the shape expected, a page that may be truncated, a run
that belongs to another commit or cannot be attributed to CI, runs that
disagree and cannot be ordered, a check that is pending, red, skipped, renamed
or absent, a remote that is not github.com -- is "not verified", and the caller does what it did before this
module existed. Nothing here raises on a bad answer; it returns a verdict that
is not PASSED.

No retries and exactly one request: a hook that hangs a push is worse than a
hook that runs the suite.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

# The checks a verified commit needs, by name. `gate` is the required check on
# dev and main: it needs the jobs that must pass and fails on any of their
# failures, so requiring it requires them. It runs `if: always()`, so it is
# never itself skipped (and a skipped one is not a pass: see `_one`). Advisory checks (the breadth jobs that run after the
# gate) are deliberately not here -- see `decide`.
REQUIRED_CHECKS = ("gate",)

PASSED = "passed"
FAILED = "failed"
PENDING = "pending"
MISSING = "missing"
# The lookup did not produce an answer that can be trusted. Never a pass.
UNKNOWN = "unknown"

# The hook's bound on the one request. Long enough for a slow connection,
# short enough that a dead one costs a push seconds and not minutes.
LOOKUP_TIMEOUT_SECONDS = 8

# One page must hold every check run of the commit: the verdict reads every
# run of each required check, and a run on a page that was not read is a red
# rerun reported as the older green one. 100 is the API's maximum; the
# default of 30 was already within six of overflowing (24 runs on a dev commit
# that the merge queue and the push to dev had each tested, 2026-10-13). A
# commit with more than this is UNKNOWN, never read in part.
_PAGE_SIZE = 100

_SHA = re.compile(r"[0-9a-f]{40}([0-9a-f]{24})?")
_SLUG = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


# ---------------------------------------------------------------------------
# the verdict (pure, over the check runs of one commit)
# ---------------------------------------------------------------------------


# Whose check runs count. A check run is a name anyone with a token can post:
# another app's `gate`, green, is not this repository's CI saying anything.
CI_APP = "github-actions"

# The only shape of timestamp that is ordered: GitHub's own, UTC with a Z.
# Strings in this shape sort as times. Anything else -- absent, a number, an
# offset, prose -- is not compared at all.
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


class UnreadableAnswerError(Exception):
    """The answer is not one a verdict can be read from."""


def matches(check_name: str, required: str) -> bool:
    """The required check is the run with exactly that name.

    This used to accept `<name> (anything)` too, so that requiring `test`
    matched the matrix leg `test (3.12)`. That made a lone `gate (docs)` a
    green `gate`. A matrix leg is now required by its own full name
    (`--require-check "test (3.12)"`).
    """
    return check_name == required


def _one(check: dict) -> str:
    if check.get("status") != "completed":
        return PENDING
    # `success` and nothing else. Not `skipped`: `gate` runs `if: always()`,
    # so a skipped gate means the workflow is no longer the one this rule was
    # written against -- and for any other required check, "it did not run"
    # is not "it passed".
    if check.get("conclusion") != "success":
        return FAILED
    return PASSED


def _worst(verdicts: list[str]) -> str:
    for state in (FAILED, PENDING):
        if state in verdicts:
            return state
    return PASSED


def _own_runs(checks: list[dict], name: str) -> list[dict]:
    """The runs named `name` that this repository's CI made.

    A run that does not say which app made it cannot be attributed, so the
    answer is unreadable; a run another app made is not the required check and
    is left out -- it can neither verify the commit nor outrank CI's own run.
    """
    own = []
    for check in checks:
        if not matches(str(check.get("name") or ""), name):
            continue
        app = check.get("app")
        slug = app.get("slug") if isinstance(app, dict) else None
        if not isinstance(slug, str) or not slug:
            raise UnreadableAnswerError(f"a `{name}` run does not say which app made it")
        if slug == CI_APP:
            own.append(check)
    return own


def _suite_of(check: dict, name: str) -> int:
    suite = check.get("check_suite")
    suite_id = suite.get("id") if isinstance(suite, dict) else None
    if isinstance(suite_id, bool) or not isinstance(suite_id, int):
        raise UnreadableAnswerError(f"a `{name}` run does not say which workflow run it is part of")
    return suite_id


def _newest_in_suite(runs: list[dict], name: str) -> str:
    """The verdict of one workflow run's `name` check.

    Several runs in one check suite are reruns of the same job, and the newest
    is the verdict: a rerun to green must be able to unblock a release. When
    they all say the same thing no order is needed. When they disagree, the
    order has to come from a `started_at` this code has checked on EVERY one
    of them -- an undated or malformed run sorted "oldest" would let a dated
    green run speak for a red or queued rerun -- and a tie goes to the worst.
    """
    verdicts = [_one(run) for run in runs]
    if len(set(verdicts)) == 1:
        return verdicts[0]
    stamps = [run.get("started_at") for run in runs]
    if not all(isinstance(s, str) and _TIMESTAMP.fullmatch(s) for s in stamps):
        raise UnreadableAnswerError(
            f"the `{name}` runs disagree and cannot be put in order "
            "(a `started_at` is missing or not a UTC timestamp)"
        )
    newest = max(stamps)
    return _worst([v for v, s in zip(verdicts, stamps, strict=True) if s == newest])


def decide(checks: list[dict], required: tuple[str, ...] | None = None) -> str:
    """Verdict over the NAMED checks a verified commit requires, for one SHA.

    PASSED means: for every required name, this repository's CI (the GitHub
    Actions app) has at least one run with exactly that name, and in EVERY
    workflow run that has one, its newest is completed with conclusion
    `success`. This is the only place PASSED is produced.

    Gating on "every check on the commit" is wrong and would get worse: `e2e`
    runs on pushes to `main` and is deliberately *not* in `gate`'s needs
    (ADR-0032 §3a keeps breadth out of the merge gate), so a red or slow e2e
    must not block a tag. What must be green is `gate` -- and `gate` is green
    only when the jobs it needs are.

    Every workflow run, not the newest overall: the merge queue and the push
    to dev each run `gate` on one SHA, and a green `gate` in one workflow run
    says nothing about a red one in another. Within a workflow run the newest
    wins, so a rerun to green counts (`_newest_in_suite`).

    A required check that is absent is MISSING, not a pass. An answer that
    cannot be attributed or ordered is UNKNOWN, not a pass.

    This function only reads a verdict; it cannot start a run.
    """
    required = required or REQUIRED_CHECKS
    if not checks:
        return MISSING

    verdicts = []
    try:
        for name in required:
            own = _own_runs(checks, name)
            if not own:
                return MISSING
            suites: dict[int, list[dict]] = {}
            for run in own:
                suites.setdefault(_suite_of(run, name), []).append(run)
            verdicts += [_newest_in_suite(runs, name) for runs in suites.values()]
    except UnreadableAnswerError:
        return UNKNOWN
    return _worst(verdicts)


def describe(checks: list[dict], required: tuple[str, ...]) -> str:
    relevant = [c for c in checks if any(matches(str(c.get("name") or ""), r) for r in required)]
    return (
        ", ".join(f"{c.get('name')}={c.get('conclusion') or c.get('status')}" for c in relevant)
        or "none"
    )


# ---------------------------------------------------------------------------
# which repository
# ---------------------------------------------------------------------------

# The whole URL, anchored at both ends, host included. Unanchored, this read
# `https://notgithub.com/a/b.git` and `https://example.org/mirror/github.com/
# a/b` as `a/b` on GitHub -- and the lookup then asked GitHub about a commit
# being pushed somewhere else. https (optionally with a user or token before
# the host), scp-style ssh, and ssh://; nothing else, plain http included.
_REMOTE = re.compile(
    r"(?:https://(?:[^@/\s]+@)?github\.com/|(?:ssh://)?git@github\.com[:/])"
    r"(?P<slug>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?"
)


def remote_slug(url: str) -> str | None:
    """`owner/repo` from a git remote URL that IS github.com, else None.

    Both forms have to be understood because both are in use on the
    maintainer's machines: `https://github.com/owner/repo(.git)` and
    `git@github.com:owner/repo.git` (and its `ssh://` spelling).
    """
    match = _REMOTE.fullmatch(url.strip()) if isinstance(url, str) else None
    return match.group("slug") if match else None


# ---------------------------------------------------------------------------
# the lookup
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Verdict:
    """What is known about one commit. Only PASSED is verified."""

    state: str
    sha: str
    slug: str
    required: tuple[str, ...]
    # PASSED/FAILED/PENDING: the required checks as GitHub reported them
    # (`gate=success`). MISSING/UNKNOWN: why there is no trustworthy answer.
    detail: str
    checks: tuple[dict, ...] = ()

    @property
    def verified(self) -> bool:
        return self.state == PASSED


def lookup_command(slug: str, sha: str) -> list[str]:
    """THE request. Check runs, not workflow runs: what is required is the
    named check `gate`, a job inside ci.yml, and `gh run list` only knows the
    workflow as a whole. `tests/test_verified_commit.py` pins that no other
    file under scripts/ composes this."""
    return ["gh", "api", f"repos/{slug}/commits/{sha}/check-runs?per_page={_PAGE_SIZE}"]


def read_check_runs(raw: str, sha: str) -> list[dict]:
    """The commit's check runs, or UnreadableAnswerError saying what was wrong.

    Strict on purpose. Each tolerance here would be a way to call a commit
    verified on an answer nobody understood: an empty body read as "no runs",
    a truncated page read as the whole list, a run for another commit (the
    path accepts a branch name as readily as a SHA) read as this one's.
    """
    if not raw.strip():
        raise UnreadableAnswerError("the answer was empty")
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise UnreadableAnswerError(f"the answer was not JSON ({exc})") from exc
    if not isinstance(payload, dict):
        raise UnreadableAnswerError("the answer was not a JSON object")
    runs = payload.get("check_runs")
    if not isinstance(runs, list):
        raise UnreadableAnswerError("the answer has no `check_runs` list")
    total = payload.get("total_count")
    if isinstance(total, bool) or not isinstance(total, int):
        raise UnreadableAnswerError(
            "the answer has no `total_count`, so it may be one page of several"
        )
    if total != len(runs):
        raise UnreadableAnswerError(
            f"the answer holds {len(runs)} of {total} check runs; "
            "the newest run of a check may be among those not read"
        )
    for run in runs:
        if not isinstance(run, dict):
            raise UnreadableAnswerError("a check run was not a JSON object")
        if not isinstance(run.get("name"), str) or not run["name"]:
            raise UnreadableAnswerError("a check run has no name")
        if run.get("head_sha") != sha:
            raise UnreadableAnswerError(
                f"a check run ({run['name']}) is for commit "
                f"{str(run.get('head_sha'))[:12]}, not {sha[:12]}"
            )
    return runs


def _why_unknown(checks: list[dict], required: tuple[str, ...]) -> str:
    """The reason `decide` could not read a verdict, in its own words."""
    try:
        for name in required:
            suites: dict[int, list[dict]] = {}
            for run in _own_runs(checks, name):
                suites.setdefault(_suite_of(run, name), []).append(run)
            for runs in suites.values():
                _newest_in_suite(runs, name)
    except UnreadableAnswerError as exc:
        return str(exc)
    return "the required check's runs could not be read as a verdict"


def _last_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else "no output"


def verdict_for(
    sha: str,
    slug: str,
    run: Callable[[list[str]], str],
    required: tuple[str, ...] | None = None,
) -> Verdict:
    """Ask GitHub, once, whether `sha` is verified on `slug`.

    `run` executes the command and returns its stdout, raising on any failure
    (a missing executable, a non-zero exit, a timeout). It is the one seam:
    release.py passes its `_check_output`, the hook passes `run_bounded`, and
    the tests pass a function. Whatever it raises becomes UNKNOWN -- except
    GitHub's "No commit found", which is MISSING: the commit was never pushed,
    and the caller's advice for that differs.

    Never raises for a bad answer; KeyboardInterrupt still passes through.
    """
    required = tuple(required or REQUIRED_CHECKS)

    def verdict(state: str, detail: str, checks: list[dict] | None = None) -> Verdict:
        return Verdict(state, sha, slug, required, detail, tuple(checks or ()))

    # A SHA, in full: anything shorter or symbolic is a ref the API would
    # resolve for itself, to a commit this caller never named.
    if not isinstance(sha, str) or not _SHA.fullmatch(sha):
        return verdict(UNKNOWN, f"{sha!r} is not a full commit SHA")
    if not isinstance(slug, str) or not _SLUG.fullmatch(slug):
        return verdict(UNKNOWN, f"{slug!r} is not an owner/name repository")
    try:
        raw = run(lookup_command(slug, sha))
    except Exception as exc:  # noqa: BLE001 - every failure of the lookup is "not verified"
        text = str(exc) or type(exc).__name__
        if "No commit found" in text:
            return verdict(MISSING, f"{slug} has no commit {sha[:12]}")
        return verdict(UNKNOWN, f"the lookup failed: {_last_line(text)}")
    try:
        checks = read_check_runs(raw if isinstance(raw, str) else "", sha)
        state = decide(checks, required)
        if state == UNKNOWN:
            return verdict(UNKNOWN, _why_unknown(checks, required), checks)
    except UnreadableAnswerError as exc:
        return verdict(UNKNOWN, str(exc))
    except Exception as exc:  # noqa: BLE001 - a bug in reading is not a green light
        return verdict(UNKNOWN, f"the answer could not be read ({type(exc).__name__}: {exc})")
    if state == MISSING:
        return verdict(MISSING, f"no run of {', '.join(required)} on {sha[:12]}", checks)
    return verdict(state, describe(checks, required), checks)


def run_bounded(cmd: list[str], timeout: float = LOOKUP_TIMEOUT_SECONDS) -> str:
    """Run the lookup the way a git hook must: once, bounded, never asking.

    stdin is closed and prompts are disabled so an unauthenticated `gh` fails
    instead of waiting for a person; the timeout kills the child. Raises on a
    missing executable, a timeout or a non-zero exit -- all of which
    `verdict_for` turns into "not verified".
    """
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        stdin=subprocess.DEVNULL,
        env={**os.environ, "GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1"},
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"{cmd[0]} exited {proc.returncode}: {_last_line(proc.stderr or proc.stdout)}"
        )
    return proc.stdout
