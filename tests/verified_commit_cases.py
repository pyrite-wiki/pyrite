"""Every way the verified-commit lookup can fail or answer ambiguously.

One list, run at three surfaces, so a failure mode cannot be covered at one
and forgotten at another:

- tests/test_verified_commit.py      the shared function, with a supplied runner
- tests/test_test_affected_verified.py   the pre-push path, through
                                     scripts/test-affected, with a fake `gh`
                                     executable on PATH
- tests/test_release_rehearsal.py    the release dry run, same fake `gh`

Each case is what `gh api .../check-runs` does: what it prints and how it
exits. ``CASES[name] = (behaviour, state)`` where ``behaviour(sha)`` returns
``{"stdout", "stderr", "exit"}`` and ``state`` is the verdict the shared
function must give. None of them is verified.
"""

from __future__ import annotations

import json
from collections.abc import Callable

OTHER_SHA = "b" * 40
T1 = "2026-10-13T10:00:00Z"
T2 = "2026-10-13T11:00:00Z"


def run_of(sha: str, name="gate", status="completed", conclusion="success", **more) -> dict:
    return {"name": name, "status": status, "conclusion": conclusion, "head_sha": sha, **more}


def answer(*runs: dict, total: object = "len") -> str:
    """The API's body. ``total`` overrides ``total_count``; None leaves it out."""
    payload: dict = {"check_runs": list(runs)}
    if total == "len":
        payload["total_count"] = len(runs)
    elif total is not None:
        payload["total_count"] = total
    return json.dumps(payload)


def green(sha: str) -> dict:
    """The one answer that verifies: `gate` green on this SHA."""
    return {"stdout": answer(run_of(sha), run_of(sha, "kb")), "stderr": "", "exit": 0}


def _out(make: Callable[[str], str]) -> Callable[[str], dict]:
    return lambda sha: {"stdout": make(sha), "stderr": "", "exit": 0}


def _fail(code: int, stderr: str, stdout: str = "") -> Callable[[str], dict]:
    return lambda sha: {"stdout": stdout, "stderr": stderr, "exit": code}


def _bare(sha: str, **fields) -> dict:
    return {"name": "gate", "status": "completed", "conclusion": "success", **fields}


# name -> (behaviour, the state `verdict_for` must return). The strings are
# verified_commit's state values, spelled out so this file imports nothing.
CASES: dict[str, tuple[Callable[[str], dict], str]] = {
    # --- `gh` itself fails -------------------------------------------------
    "gh is not authenticated": (
        _fail(4, "To get started with GitHub CLI, please run:  gh auth login\n"),
        "unknown",
    ),
    "no network": (
        _fail(1, "error connecting to api.github.com\ncheck your internet connection\n"),
        "unknown",
    ),
    "the API errored (502)": (
        _fail(1, "gh: Bad Gateway (HTTP 502)\n", "<html>502</html>"),
        "unknown",
    ),
    "rate limited": (_fail(1, "gh: API rate limit exceeded (HTTP 403)\n"), "unknown"),
    "the token lacks access": (_fail(1, "gh: Bad credentials (HTTP 401)\n"), "unknown"),
    "the repository is unknown": (_fail(1, "gh: Not Found (HTTP 404)\n"), "unknown"),
    "the SHA is unknown to GitHub": (
        _fail(
            1,
            "gh: No commit found for SHA: 1234 (HTTP 422)\n",
            '{"message":"No commit found for SHA: 1234","status":"422"}',
        ),
        "missing",
    ),
    "gh fails without a word": (_fail(1, ""), "unknown"),
    "gh fails but prints a green answer": (
        lambda sha: {"stdout": answer(run_of(sha)), "stderr": "gh: broken pipe\n", "exit": 1},
        "unknown",
    ),
    # --- the answer is not one a verdict can be read from --------------------
    "empty output": (_out(lambda sha: ""), "unknown"),
    "whitespace output": (_out(lambda sha: "\n"), "unknown"),
    "output that is not JSON": (_out(lambda sha: "<html>502 Bad Gateway</html>"), "unknown"),
    "JSON that is a list": (_out(lambda sha: json.dumps([run_of(sha)])), "unknown"),
    "JSON null": (_out(lambda sha: "null"), "unknown"),
    "an error object with no check_runs": (
        _out(lambda sha: '{"message":"Bad credentials"}'),
        "unknown",
    ),
    "check_runs is not a list": (
        _out(lambda sha: json.dumps({"total_count": 1, "check_runs": run_of(sha)})),
        "unknown",
    ),
    "a check run that is not an object": (
        _out(lambda sha: '{"total_count":1,"check_runs":["gate"]}'),
        "unknown",
    ),
    "a check run with no name": (
        _out(lambda sha: answer({"status": "completed", "conclusion": "success", "head_sha": sha})),
        "unknown",
    ),
    "total_count is absent (maybe one page of several)": (
        _out(lambda sha: answer(run_of(sha), total=None)),
        "unknown",
    ),
    "total_count is not a number": (_out(lambda sha: answer(run_of(sha), total="1")), "unknown"),
    "total_count is a boolean": (_out(lambda sha: answer(run_of(sha), total=True)), "unknown"),
    "the page is truncated": (_out(lambda sha: answer(run_of(sha), total=131)), "unknown"),
    "fewer runs promised than returned": (
        _out(lambda sha: answer(run_of(sha), run_of(sha, "kb"), total=1)),
        "unknown",
    ),
    # --- the answer is about another commit --------------------------------
    "the green run is for another commit": (
        _out(lambda sha: answer(run_of(OTHER_SHA))),
        "unknown",
    ),
    "the green run is for the parent's abbreviation": (
        _out(lambda sha: answer(run_of(sha[:12]))),
        "unknown",
    ),
    "a run carries no head_sha": (_out(lambda sha: answer(_bare(sha))), "unknown"),
    "an unrelated run is for another commit": (
        _out(lambda sha: answer(run_of(sha), run_of(OTHER_SHA, "kb"))),
        "unknown",
    ),
    # --- the required check is not there -------------------------------------
    "no check ran at all": (_out(lambda sha: answer()), "missing"),
    "the check name changed": (_out(lambda sha: answer(run_of(sha, "merge-gate"))), "missing"),
    "a name that only starts like the check": (
        _out(lambda sha: answer(run_of(sha, "gatekeeper"))),
        "missing",
    ),
    "only advisory checks are green": (
        _out(lambda sha: answer(run_of(sha, "kb"), run_of(sha, "smoke"))),
        "missing",
    ),
    # --- the required check is not green ---------------------------------------
    "the check is queued": (
        _out(lambda sha: answer(run_of(sha, status="queued", conclusion=None))),
        "pending",
    ),
    "the check is in progress": (
        _out(lambda sha: answer(run_of(sha, status="in_progress", conclusion=None))),
        "pending",
    ),
    "the check has no status": (
        _out(lambda sha: answer({"name": "gate", "head_sha": sha, "conclusion": "success"})),
        "pending",
    ),
    "the check failed": (_out(lambda sha: answer(run_of(sha, conclusion="failure"))), "failed"),
    "the check was cancelled": (
        _out(lambda sha: answer(run_of(sha, conclusion="cancelled"))),
        "failed",
    ),
    "the check timed out": (
        _out(lambda sha: answer(run_of(sha, conclusion="timed_out"))),
        "failed",
    ),
    "the check is neutral": (_out(lambda sha: answer(run_of(sha, conclusion="neutral"))), "failed"),
    "the check needs action": (
        _out(lambda sha: answer(run_of(sha, conclusion="action_required"))),
        "failed",
    ),
    "the check is stale": (_out(lambda sha: answer(run_of(sha, conclusion="stale"))), "failed"),
    "completed with no conclusion": (
        _out(lambda sha: answer(run_of(sha, conclusion=None))),
        "failed",
    ),
    # --- several runs of the required check ------------------------------------
    "a newer run is red": (
        _out(
            lambda sha: answer(
                run_of(sha, started_at=T1), run_of(sha, conclusion="failure", started_at=T2)
            )
        ),
        "failed",
    ),
    "a rerun is in progress": (
        _out(
            lambda sha: answer(
                run_of(sha, started_at=T1),
                run_of(sha, status="in_progress", conclusion=None, started_at=T2),
            )
        ),
        "pending",
    ),
    "two runs with the same timestamps disagree": (
        _out(
            lambda sha: answer(
                run_of(sha, started_at=T1), run_of(sha, conclusion="failure", started_at=T1)
            )
        ),
        "failed",
    ),
    "an undated red run beside a dated green one": (
        _out(lambda sha: answer(run_of(sha, started_at=T1), run_of(sha, conclusion="failure"))),
        "failed",
    ),
    "an undated queued rerun beside a dated green one": (
        _out(
            lambda sha: answer(
                run_of(sha, started_at=T1), run_of(sha, status="queued", conclusion=None)
            )
        ),
        "pending",
    ),
}

# Answers that DO verify, so each surface can show its skip is reachable and
# the cases above are red for the reason they name.
VERIFIED: dict[str, Callable[[str], dict]] = {
    "gate green": green,
    "a rerun to green": _out(
        lambda sha: answer(
            run_of(sha, conclusion="failure", started_at=T1), run_of(sha, started_at=T2)
        )
    ),
    "two green runs (merge queue and push)": _out(lambda sha: answer(run_of(sha), run_of(sha))),
    "an advisory check is red": _out(
        lambda sha: answer(run_of(sha), run_of(sha, "e2e", conclusion="failure"))
    ),
}


# A stand-in for the `gh` executable, written into a directory that goes FIRST
# on PATH: the real `gh` is never reached, so no test calls GitHub. It logs its
# arguments (one line per call) and answers from $FAKE_GH_DIR/answer.json:
# {"stdout", "stderr", "exit", "sleep"}; with no answer file it fails loudly.
FAKE_GH = """\
#!{python}
import json, os, sys, time
home = os.environ["FAKE_GH_DIR"]
with open(os.path.join(home, "calls.log"), "a") as log:
    log.write(" ".join(sys.argv[1:]) + "\\n")
try:
    with open(os.path.join(home, "answer.json")) as fh:
        answers = json.load(fh)
except OSError:
    sys.stderr.write("fake gh: no answer configured\\n")
    sys.exit(97)
joined = " ".join(sys.argv[1:])
for needle, reply in answers.items():
    if needle in joined:
        time.sleep(reply.get("sleep", 0))
        sys.stdout.write(reply.get("stdout", ""))
        sys.stderr.write(reply.get("stderr", ""))
        sys.exit(reply.get("exit", 0))
sys.stderr.write("fake gh: unexpected call: " + joined + "\\n")
sys.exit(98)
"""

# A stand-in for a checkout's `.venv/bin/python`. It answers the version
# query, logs every `-m pytest ...` invocation to $FAKE_LOG and exits with
# $FAKE_EXIT (or $FAKE_COLLECT_EXIT for a --collect-only), so a case can pass,
# fail or fail to import without running a suite. Anything else -- a script --
# is handed to the real interpreter, as the hook's entry does.
FAKE_VENV_PYTHON = """\
#!/bin/sh
if [ "$1" = "-c" ]; then echo "${FAKE_PY_VERSION:-3.12}"; exit 0; fi
if [ "$1" != "-m" ]; then exec "$REAL_PYTHON" "$@"; fi
echo "$*" >> "$FAKE_LOG"
case "$*" in
  *--collect-only*)
    if [ "${FAKE_COLLECT_EXIT:-0}" != "0" ]; then
      echo "ERROR tests/test_b.py - ModuleNotFoundError: No module named 'hypothesis'"
    fi
    exit "${FAKE_COLLECT_EXIT:-0}";;
esac
exit "${FAKE_EXIT:-0}"
"""
