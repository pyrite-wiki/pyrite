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


ACTIONS = {"slug": "github-actions"}


RUN_URL = "https://github.com/acme/widget/actions/runs/{run}/job/{job}"


def run_of(sha: str, name="gate", status="completed", conclusion="success", **more) -> dict:
    """One check run as this repository's own CI reports it: from the GitHub
    Actions app, in a check suite (one per workflow run; reruns share it)."""
    return {
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "head_sha": sha,
        "app": ACTIONS,
        "check_suite": {"id": 1},
        **more,
    }


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
    run = run_of(sha, **fields)
    del run["head_sha"]
    return run


def _without(sha: str, *drop: str, **fields) -> dict:
    run = run_of(sha, **fields)
    for key in drop:
        del run[key]
    return run


OTHER_APP = {"slug": "some-ci-bot"}
SUITE2 = {"id": 2}


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
        _out(lambda sha: answer(_without(sha, "name"))),
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
        _out(lambda sha: answer(_without(sha, "status"))),
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
        "unknown",
    ),
    "an undated queued rerun beside a dated green one": (
        _out(
            lambda sha: answer(
                run_of(sha, started_at=T1), run_of(sha, status="queued", conclusion=None)
            )
        ),
        "unknown",
    ),
    # --- fix round 1: four inputs the first version read as green ---------------
    # 1. `gate` is `if: always()`: a skipped gate means the workflow changed.
    "the required check was skipped": (
        _out(lambda sha: answer(run_of(sha, conclusion="skipped"))),
        "failed",
    ),
    # 2. the name alone is not the check: it must be exact, and from Actions.
    "only a matrix-style leg of the name exists": (
        _out(lambda sha: answer(run_of(sha, "gate (docs)"))),
        "missing",
    ),
    "the only green run of the name is another app's": (
        _out(lambda sha: answer(run_of(sha, app=OTHER_APP))),
        "missing",
    ),
    "a newer green run from another app beside a red one from CI": (
        _out(
            lambda sha: answer(
                run_of(sha, conclusion="failure", started_at=T1),
                run_of(sha, app=OTHER_APP, started_at=T2),
            )
        ),
        "failed",
    ),
    "a newer green run from another workflow beside a red one": (
        _out(
            lambda sha: answer(
                run_of(sha, conclusion="failure", started_at=T1),
                run_of(sha, check_suite=SUITE2, started_at=T2),
            )
        ),
        "failed",
    ),
    "the run does not say which app made it": (
        _out(lambda sha: answer(_without(sha, "app"))),
        "unknown",
    ),
    "the app is not an object": (
        _out(lambda sha: answer(run_of(sha, app="github-actions"))),
        "unknown",
    ),
    "the app has no slug": (_out(lambda sha: answer(run_of(sha, app={"id": 15368}))), "unknown"),
    "the run does not say which workflow run it belongs to": (
        _out(lambda sha: answer(_without(sha, "check_suite"))),
        "unknown",
    ),
    "the check suite has no id": (
        _out(lambda sha: answer(run_of(sha, check_suite={}))),
        "unknown",
    ),
    # 3. "newest" needs a timestamp that can be trusted.
    "a red run with only a later completed_at beside a dated green one": (
        _out(
            lambda sha: answer(
                run_of(sha, started_at=T1, completed_at=T1),
                run_of(sha, conclusion="failure", completed_at=T2),
            )
        ),
        "unknown",
    ),
    "a timestamp that is a number": (
        _out(
            lambda sha: answer(
                run_of(sha, started_at=T2), run_of(sha, conclusion="failure", started_at=99)
            )
        ),
        "unknown",
    ),
    "a timestamp with an offset instead of Z": (
        _out(
            lambda sha: answer(
                run_of(sha, started_at=T1),
                run_of(sha, conclusion="failure", started_at="2026-10-13T09:30:00-02:00"),
            )
        ),
        "unknown",
    ),
    "a timestamp that is not a time": (
        _out(
            lambda sha: answer(
                run_of(sha, started_at=T2), run_of(sha, conclusion="failure", started_at="soon")
            )
        ),
        "unknown",
    ),
    # fix round 2: the right shape is not a real instant.
    "a green run stamped at an impossible later time beside a real red one": (
        _out(
            lambda sha: answer(
                run_of(sha, conclusion="failure", started_at=T2),
                run_of(sha, started_at="9999-99-99T99:99:99Z"),
            )
        ),
        "unknown",
    ),
    "a green run stamped on a day that does not exist beside a real red one": (
        _out(
            lambda sha: answer(
                run_of(sha, conclusion="failure", started_at=T2),
                run_of(sha, started_at="2026-11-31T00:00:00Z"),
            )
        ),
        "unknown",
    ),
    "red runs disagree with nothing to order them": (
        _out(lambda sha: answer(run_of(sha), run_of(sha, conclusion="failure"))),
        "unknown",
    ),
}

# 4. Remote URLs that only look like this GitHub repository. None may be read
# as `acme/widget`; each surface asserts no request is made for them.
NOT_GITHUB_REMOTES = [
    "https://notgithub.com/acme/widget.git",
    "https://example.org/mirror/github.com/acme/widget",
    "https://github.com.evil.example/acme/widget.git",
    "https://github.com@evil.example/acme/widget.git",
    "git@notgithub.com:acme/widget.git",
    "ssh://git@evil.example/github.com/acme/widget.git",
    "http://github.com/acme/widget.git",
    "/srv/git/github.com/acme/widget.git",
    "file:///srv/github.com/acme/widget.git",
    "https://github.com/acme/widget/extra",
    "https://github.com/acme",
    "https://gitlab.com/acme/widget.git",
    # fix round 2: where a pattern and a URL parser disagree about the host,
    # and paths that are not a repository.
    "https://evil.example#@github.com/acme/widget",
    "https://evil.example?@github.com/acme/widget",
    "https://evil.example/@github.com/acme/widget",
    "https://github.com/acme/widget?x=1",
    "https://github.com/acme/widget#main",
    "https://github.com:8443/acme/widget.git",
    "https://github.com/acme/..",
    "https://github.com/../widget",
    "https://github.com/./widget",
    "https://github.com/acme/.git",
    "git@github.com:acme/..",
    "git@github.com:../widget.git",
    "ssh://root@github.com/acme/widget.git",
    "https://GITHUB.com.evil.example/acme/widget",
]
GITHUB_REMOTES = [
    "https://github.com/acme/widget",
    "https://github.com/acme/widget.git",
    "https://github.com/acme/widget/",
    "https://token@github.com/acme/widget.git",
    "git@github.com:acme/widget.git",
    "ssh://git@github.com/acme/widget.git",
]

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
    "the merge queue and the push to dev, two workflow runs, both green": _out(
        lambda sha: answer(
            run_of(sha, started_at=T1), run_of(sha, check_suite=SUITE2, started_at=T2)
        )
    ),
    "green runs that agree need no order": _out(
        lambda sha: answer(run_of(sha), run_of(sha, started_at="garbage"))
    ),
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
