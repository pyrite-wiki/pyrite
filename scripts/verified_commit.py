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
from datetime import datetime
from urllib.parse import urlsplit

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
_SEGMENT = re.compile(r"[A-Za-z0-9_.-]+")


def _is_slug(value: object) -> bool:
    """`owner/name`, each a real path segment: never `.`, `..` or `.git`."""
    if not isinstance(value, str):
        return False
    parts = value.split("/")
    return len(parts) == 2 and all(
        _SEGMENT.fullmatch(part) and part.strip(".") and part != ".git" for part in parts
    )


# ---------------------------------------------------------------------------
# the verdict (pure, over the check runs of one commit)
# ---------------------------------------------------------------------------


# Whose check runs count. A check run is a name anyone with a token can post:
# another app's `gate`, green, is not this repository's CI saying anything.
CI_APP = "github-actions"

# The only timestamp that is ordered: GitHub's own spelling, UTC with a Z,
# and a real instant. The shape alone is not enough -- `9999-99-99T99:99:99Z`
# has it, and as a string it sorts after every real time.
_TIMESTAMP = "%Y-%m-%dT%H:%M:%SZ"
_TIMESTAMP_SHAPE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


def _instant(value: object) -> datetime | None:
    """The instant a `started_at` names, or None if it names none."""
    if not isinstance(value, str) or not _TIMESTAMP_SHAPE.fullmatch(value):
        return None
    try:
        return datetime.strptime(value, _TIMESTAMP)
    except ValueError:
        return None


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


def _newest_in_suite(runs: list[dict], name: str) -> tuple[dict, str]:
    """One workflow run's `name` check: the run that speaks for it, and its
    verdict.

    Several runs in one check suite are reruns of the same job, and the newest
    is the verdict: a rerun to green must be able to unblock a release. When
    they all say the same thing no order is needed. When they disagree, the
    order has to come from a `started_at` this code has parsed as a real
    instant on EVERY one of them -- an undated or malformed run sorted
    "oldest", or an impossible date sorted "newest", would let a green run
    speak for a red or queued one -- and a tie goes to the worst.
    """
    verdicts = [_one(run) for run in runs]
    if len(set(verdicts)) == 1:
        return runs[-1], verdicts[0]
    instants = [_instant(run.get("started_at")) for run in runs]
    if any(instant is None for instant in instants):
        raise UnreadableAnswerError(
            f"the `{name}` runs disagree and cannot be put in order "
            "(a `started_at` is missing or is not a real UTC timestamp)"
        )
    newest = max(instants)
    tied = [(r, v) for r, v, i in zip(runs, verdicts, instants, strict=True) if i == newest]
    worst = _worst([v for _r, v in tied])
    return next((r, v) for r, v in tied if v == worst)


def counted_runs(checks: list[dict], required: tuple[str, ...]) -> list[tuple[dict, str]] | None:
    """The runs a verdict is read from, each with its own verdict: for every
    required name, one per workflow run (check suite) of this repository's CI.
    None when a required name has no such run. Raises UnreadableAnswerError
    when a run cannot be attributed or ordered.

    `decide` and the printed detail both read this, so what a refusal prints
    is exactly what was counted -- never another app's run of the same name,
    never a superseded attempt.
    """
    counted: list[tuple[dict, str]] = []
    for name in required:
        own = _own_runs(checks, name)
        if not own:
            return None
        suites: dict[int, list[dict]] = {}
        for run in own:
            suites.setdefault(_suite_of(run, name), []).append(run)
        counted += [_newest_in_suite(runs, name) for runs in suites.values()]
    return counted


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
    try:
        counted = counted_runs(checks, required)
    except UnreadableAnswerError:
        return UNKNOWN
    if counted is None:
        return MISSING
    return _worst([verdict for _run, verdict in counted])


_RUN_URL = re.compile(
    r"https://github\.com/(?P<slug>[^/\s]+/[^/\s]+)/actions/runs/(?P<run>\d+)(/[\w/]*)?"
)


def _where(run: dict, slug: str) -> tuple[str, str | None]:
    """How to point a person at one run: its URL and workflow run id when the
    API gave a URL that is this repository's Actions run, else its check
    suite. The URL is the API's and is printed for someone to open, so
    anything else is not printed at all."""
    for key in ("html_url", "details_url"):
        url = run.get(key)
        match = _RUN_URL.fullmatch(url) if isinstance(url, str) else None
        if match and match.group("slug") == slug:
            return url, match.group("run")
    suite = run.get("check_suite")
    return f"check suite {suite.get('id') if isinstance(suite, dict) else '?'}", None


def describe(counted: list[tuple[dict, str]], slug: str) -> str:
    """The counted runs as `name=conclusion`, the ones that are not green
    followed by where to find them."""
    parts = []
    for run, verdict in counted:
        said = f"{run.get('name')}={run.get('conclusion') or run.get('status')}"
        parts.append(said if verdict == PASSED else f"{said} ({_where(run, slug)[0]})")
    return ", ".join(parts) or "none"


# ---------------------------------------------------------------------------
# which repository
# ---------------------------------------------------------------------------

_SCP_STYLE = re.compile(r"git@github\.com:(?P<path>[^:\s]+)")


def remote_slug(url: str) -> str | None:
    """`owner/repo` from a git remote URL that IS github.com, else None.

    The forms in use on the maintainer's machines: `https://github.com/owner/
    repo(.git)`, `git@github.com:owner/repo.git`, and its `ssh://` spelling.

    The host is decided by ONE parser, `urlsplit`, and must be exactly
    github.com. Two earlier versions used a pattern over the whole string: the
    first was unanchored (`https://notgithub.com/a/b.git` read as `a/b`), the
    second let `#` and `?` into the part before `@`, so `https://evil.org#@
    github.com/a/b` read as GitHub while every URL parser, and git, go to
    evil.org. A query, a fragment, a port, a scheme other than https or ssh,
    or a path that is not exactly two real segments is not a repository here.
    """
    if not isinstance(url, str):
        return None
    url = url.strip()
    scp = _SCP_STYLE.fullmatch(url)
    if scp:
        path = scp.group("path")
    else:
        try:
            parts = urlsplit(url)
            port = parts.port
        except ValueError:
            return None
        if parts.scheme not in ("https", "ssh") or parts.hostname != "github.com":
            return None
        if port is not None or parts.query or parts.fragment or "?" in url or "#" in url:
            return None
        if parts.password is not None and parts.scheme == "ssh":
            return None
        if parts.scheme == "ssh" and parts.username != "git":
            return None
        path = parts.path.removeprefix("/")
    path = path.removesuffix("/")
    path = path.removesuffix(".git")
    return path if _is_slug(path) else None


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
    # The counted runs that are not green, each as (name, conclusion, where,
    # workflow run id or None): what a refusal needs to name the run to re-run.
    not_green: tuple[tuple[str, str, str, str | None], ...] = ()

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
        counted_runs(checks, required)
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

    def verdict(
        state: str,
        detail: str,
        checks: list[dict] | None = None,
        not_green: tuple = (),
    ) -> Verdict:
        return Verdict(state, sha, slug, required, detail, tuple(checks or ()), not_green)

    # A SHA, in full: anything shorter or symbolic is a ref the API would
    # resolve for itself, to a commit this caller never named.
    if not isinstance(sha, str) or not _SHA.fullmatch(sha):
        return verdict(UNKNOWN, f"{sha!r} is not a full commit SHA")
    if not _is_slug(slug):
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
    counted = counted_runs(checks, required) or []
    not_green = tuple(
        (str(run.get("name")), str(run.get("conclusion") or run.get("status")), *_where(run, slug))
        for run, each in counted
        if each != PASSED
    )
    return verdict(state, describe(counted, slug), checks, not_green)


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
