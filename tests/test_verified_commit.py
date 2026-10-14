"""scripts/verified_commit.py: the one definition of a verified commit.

A commit is verified when every required check (`gate`) is green on that exact
SHA on GitHub. `scripts/release.py` and the pre-push hook
(`scripts/test-affected`) both ask this module; before 0.25.7's retro each had
its own answer, and the hook re-ran the local suite on a commit CI had passed.

"Verified" lets a push through untested, so the property pinned here is FAIL
CLOSED: every way the lookup can fail, or answer with something ambiguous, is
"not verified". The same list is run through the hook's own entry point in
tests/test_test_affected_verified.py.

Nothing here calls GitHub: the runner is a function the test supplies, and the
bounded runner is exercised against local commands only.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import verified_commit as vc  # noqa: E402

from tests.verified_commit_cases import (  # noqa: E402
    CASES,
    GITHUB_REMOTES,
    NOT_GITHUB_REMOTES,
    VERIFIED,
    answer,
)
from tests.verified_commit_cases import run_of as _run_of  # noqa: E402

SHA = "a" * 40
OTHER = "b" * 40
SLUG = "acme/widget"


def run_of(name="gate", status="completed", conclusion="success", sha=SHA, **more) -> dict:
    return _run_of(sha, name, status, conclusion, **more)


def gh(stdout: str):
    return lambda cmd: stdout


def raising(exc: BaseException):
    def run(cmd):
        raise exc

    return run


def as_runner(behaviour):
    """A shared case as the runner would see it: stdout, or the exception
    `run_bounded` raises for a non-zero exit."""

    def run(cmd):
        reply = behaviour(SHA)
        if reply["exit"] != 0:
            tail = [line for line in (reply["stderr"] or reply["stdout"]).splitlines() if line]
            raise RuntimeError(f"gh exited {reply['exit']}: {tail[-1] if tail else 'no output'}")
        return reply["stdout"]

    return run


# Every way the lookup fails or answers ambiguously -> the state it must have:
# the shared list (also run through the hook and the release dry run), plus
# what only a supplied runner can do.
NOT_VERIFIED = {name: (as_runner(behaviour), state) for name, (behaviour, state) in CASES.items()}
NOT_VERIFIED.update(
    {
        "gh is not installed": (raising(FileNotFoundError(2, "No such file", "gh")), vc.UNKNOWN),
        "the request timed out": (raising(subprocess.TimeoutExpired(["gh"], 8)), vc.UNKNOWN),
        "an exception with no message": (raising(RuntimeError()), vc.UNKNOWN),
        "the runner returned no string": (lambda cmd: None, vc.UNKNOWN),
    }
)


class TestFailClosed:
    @pytest.mark.parametrize("case", sorted(NOT_VERIFIED))
    def test_it_is_not_verified(self, case):
        run, state = NOT_VERIFIED[case]
        verdict = vc.verdict_for(SHA, SLUG, run)
        assert verdict.verified is False, case
        assert verdict.state == state, (case, verdict)
        assert verdict.detail, "a refusal that does not say why cannot be acted on"

    @pytest.mark.parametrize(
        "sha",
        ["a" * 39, "a" * 41, "A" * 40, "dev", "HEAD", "abc1234", "", "a" * 40 + "\n", None],
    )
    def test_anything_but_a_full_sha_is_not_asked_about(self, sha):
        """The path takes a branch name as readily as a SHA, and would answer
        for whatever that branch points at."""
        asked = []
        verdict = vc.verdict_for(sha, SLUG, lambda cmd: asked.append(cmd) or answer(run_of()))
        assert not verdict.verified and verdict.state == vc.UNKNOWN
        assert asked == []

    @pytest.mark.parametrize("slug", ["", "acme", "acme/widget/x", "acme/wid get", "a/b?x=1", None])
    def test_anything_but_owner_name_is_not_asked_about(self, slug):
        asked = []
        verdict = vc.verdict_for(SHA, slug, lambda cmd: asked.append(cmd) or answer(run_of()))
        assert not verdict.verified and verdict.state == vc.UNKNOWN
        assert asked == []

    def test_an_interrupt_is_not_swallowed(self):
        with pytest.raises(KeyboardInterrupt):
            vc.verdict_for(SHA, SLUG, raising(KeyboardInterrupt()))


class TestVerified:
    def test_the_required_check_green_on_this_sha_is_verified(self):
        verdict = vc.verdict_for(SHA, SLUG, gh(answer(run_of(), run_of("kb"))))
        assert verdict.verified and verdict.state == vc.PASSED
        assert verdict.detail == "gate=success"
        assert (verdict.sha, verdict.slug, verdict.required) == (SHA, SLUG, ("gate",))

    def test_a_sha256_commit_id_is_a_full_sha_too(self):
        sha = "c" * 64
        assert vc.verdict_for(sha, SLUG, gh(answer(run_of(sha=sha)))).verified

    def test_a_rerun_to_green_is_verified(self):
        runs = answer(
            run_of(conclusion="failure", started_at="2026-10-13T10:00:00Z"),
            run_of(started_at="2026-10-13T11:00:00Z"),
        )
        assert vc.verdict_for(SHA, SLUG, gh(runs)).verified

    def test_two_green_runs_at_the_same_moment_are_verified(self):
        """The merge queue and the push to dev both run `gate` on one SHA."""
        runs = answer(run_of(), run_of())
        assert vc.verdict_for(SHA, SLUG, gh(runs)).verified

    def test_an_advisory_red_check_does_not_unverify(self):
        """`e2e` is not in `gate`'s needs (ADR-0032 section 3a)."""
        runs = answer(run_of(), run_of("e2e", conclusion="failure"))
        assert vc.verdict_for(SHA, SLUG, gh(runs)).verified

    def test_every_required_check_must_be_green(self):
        runs = answer(run_of(), run_of("kb", conclusion="failure"))
        assert vc.verdict_for(SHA, SLUG, gh(runs), required=("gate", "kb")).state == vc.FAILED
        assert vc.verdict_for(SHA, SLUG, gh(runs), required=("gate",)).verified

    def test_the_default_required_check_is_gate(self):
        assert vc.REQUIRED_CHECKS == ("gate",)


class TestWhichRepository:
    @pytest.mark.parametrize("url", NOT_GITHUB_REMOTES)
    def test_a_url_that_only_contains_the_host_is_not_github(self, url):
        assert vc.remote_slug(url) is None

    @pytest.mark.parametrize("url", GITHUB_REMOTES)
    def test_every_spelling_of_github_is_understood(self, url):
        assert vc.remote_slug(url) == "acme/widget"

    @pytest.mark.parametrize("url", ["", "   ", "origin", None, 7])
    def test_nothing_is_not_a_repository(self, url):
        assert vc.remote_slug(url) is None


class TestEveryAnswerThatVerifies:
    @pytest.mark.parametrize("case", sorted(VERIFIED))
    def test_it_is_verified(self, case):
        assert vc.verdict_for(SHA, SLUG, as_runner(VERIFIED[case])).verified, case

    def test_an_unknown_verdict_says_what_could_not_be_read(self):
        verdict = vc.verdict_for(
            SHA, SLUG, as_runner(CASES["the run does not say which app made it"][0])
        )
        assert verdict.state == vc.UNKNOWN and "which app" in verdict.detail


class TestTheDetailNamesOnlyWhatWasCounted:
    """What a refusal prints is what the maintainer acts on. It lists the runs
    the verdict counted and no others, and names the run that is not green."""

    def test_another_apps_red_run_is_not_printed_beside_a_pass(self):
        other = run_of(conclusion="failure", app={"slug": "some-ci-bot"})
        verdict = vc.verdict_for(SHA, SLUG, gh(answer(run_of(), other)))
        assert verdict.verified
        assert verdict.detail == "gate=success"

    def test_one_line_per_workflow_run_counted(self):
        both = answer(run_of(), run_of(check_suite={"id": 2}))
        assert vc.verdict_for(SHA, SLUG, gh(both)).detail == "gate=success, gate=success"

    def test_only_the_newest_of_a_reruns_runs_is_printed(self):
        rerun = answer(
            run_of(conclusion="failure", started_at="2026-10-13T10:00:00Z"),
            run_of(started_at="2026-10-13T11:00:00Z"),
        )
        assert vc.verdict_for(SHA, SLUG, gh(rerun)).detail == "gate=success"

    def test_the_run_that_is_not_green_is_named_by_its_url(self):
        url = "https://github.com/acme/widget/actions/runs/777/job/42"
        runs = answer(
            run_of(),
            run_of(conclusion="cancelled", check_suite={"id": 2}, html_url=url),
            run_of(check_suite={"id": 3}),
        )
        verdict = vc.verdict_for(SHA, SLUG, gh(runs))
        assert verdict.state == vc.FAILED
        assert verdict.detail == f"gate=success, gate=cancelled ({url}), gate=success"
        assert verdict.not_green == (("gate", "cancelled", url, "777"),)

    def test_a_url_that_is_not_this_repositorys_run_is_not_printed(self):
        """The URL comes from the API and is printed for a person to open."""
        runs = answer(
            run_of(conclusion="failure", check_suite={"id": 9}, html_url="https://evil.example/x")
        )
        verdict = vc.verdict_for(SHA, SLUG, gh(runs))
        assert "evil.example" not in verdict.detail
        assert verdict.detail == "gate=failure (check suite 9)"
        assert verdict.not_green == (("gate", "failure", "check suite 9", None),)


class TestOneRequest:
    def test_it_asks_exactly_once_whatever_the_answer(self):
        for case, (run, _state) in NOT_VERIFIED.items():
            asked = []

            def counting(cmd, _run=run, _asked=asked):
                _asked.append(cmd)
                return _run(cmd)

            vc.verdict_for(SHA, SLUG, counting)
            assert len(asked) == 1, f"{case}: {len(asked)} requests (a retry can hang a push)"

    def test_the_request_names_the_sha_the_repo_and_a_whole_page(self):
        asked = []
        vc.verdict_for(SHA, SLUG, lambda cmd: asked.append(cmd) or answer(run_of()))
        (cmd,) = asked
        assert cmd[:2] == ["gh", "api"]
        assert cmd[2] == f"repos/{SLUG}/commits/{SHA}/check-runs?per_page=100"
        # A read: no method flag, no field (which would make it a POST).
        assert len(cmd) == 3

    def test_the_hooks_timeout_is_short(self):
        assert 0 < vc.LOOKUP_TIMEOUT_SECONDS <= 10


class TestBoundedRunner:
    """`run_bounded` against local commands: what the hook's request does when
    the program is missing, fails, or never answers."""

    def test_stdout_of_a_command_that_succeeds(self):
        assert vc.run_bounded([sys.executable, "-c", "print('ok')"]).strip() == "ok"

    def test_a_missing_executable_raises(self):
        with pytest.raises(OSError):
            vc.run_bounded(["pyrite-no-such-command-anywhere", "api"])

    def test_a_non_zero_exit_raises_with_the_last_line_of_stderr(self):
        script = (
            "import sys; sys.stderr.write('first\\ngh: Bad credentials (HTTP 401)\\n'); sys.exit(1)"
        )
        with pytest.raises(RuntimeError, match="exited 1: gh: Bad credentials"):
            vc.run_bounded([sys.executable, "-c", script])

    def test_a_command_that_never_answers_is_killed_at_the_timeout(self):
        # The outcome does not depend on load: a slow start still times out.
        with pytest.raises(subprocess.TimeoutExpired):
            vc.run_bounded([sys.executable, "-c", "import time; time.sleep(600)"], timeout=0.5)

    def test_it_never_waits_for_a_person(self):
        """An unauthenticated `gh` must fail, not prompt: stdin is closed and
        prompting is switched off."""
        script = (
            "import os, sys; print(repr(sys.stdin.read()), os.environ.get('GH_PROMPT_DISABLED'))"
        )
        assert vc.run_bounded([sys.executable, "-c", script]).split() == ["''", "1"]

    def test_the_default_timeout_is_the_pinned_one(self, monkeypatch):
        seen = {}

        def fake_run(cmd, **kwargs):
            seen.update(kwargs)
            return subprocess.CompletedProcess(cmd, 0, "out", "")

        monkeypatch.setattr(vc.subprocess, "run", fake_run)
        vc.run_bounded(["gh"])
        assert seen["timeout"] == vc.LOOKUP_TIMEOUT_SECONDS
        # Under pytest stdin is already empty, so the closed stdin is pinned
        # here rather than by reading it.
        assert seen["stdin"] is subprocess.DEVNULL

    def test_a_timeout_through_the_lookup_is_not_verified(self):
        def slow(cmd):
            return vc.run_bounded([sys.executable, "-c", "import time; time.sleep(600)"], 0.5)

        verdict = vc.verdict_for(SHA, SLUG, slow)
        assert verdict.state == vc.UNKNOWN and "timed out" in verdict.detail


class TestOneDefinition:
    """Design principle 8: the rule lives in one place, and a test fails when
    new code goes around it."""

    def _load_test_affected(self):
        import importlib.machinery
        import importlib.util

        path = REPO / "scripts" / "test-affected"
        loader = importlib.machinery.SourceFileLoader("test_affected_for_vc", str(path))
        spec = importlib.util.spec_from_loader("test_affected_for_vc", loader)
        module = importlib.util.module_from_spec(spec)
        sys.modules["test_affected_for_vc"] = module
        loader.exec_module(module)
        return module

    def test_the_release_script_decides_with_the_shared_function(self):
        import release

        assert release.ci_decision is vc.decide
        assert release.remote_slug is vc.remote_slug
        assert release.DEFAULT_REQUIRED_CHECKS is vc.REQUIRED_CHECKS

    def test_the_hook_script_uses_the_same_module(self):
        module = self._load_test_affected()
        assert module.verified_commit.verdict_for is vc.verdict_for

    def test_no_other_script_composes_the_lookup_or_reads_its_answer(self):
        """A second lookup is a second definition. The words a lookup cannot be
        written without appear in verified_commit.py and nowhere else under
        scripts/."""
        offenders = []
        for path in sorted((REPO / "scripts").iterdir()):
            if not path.is_file() or path.name == "verified_commit.py":
                continue
            try:
                text = path.read_text()
            except UnicodeDecodeError:
                continue
            for needle in ("check-runs", "check_runs", "check-suites", "commits/{", "/status"):
                if needle in text:
                    offenders.append(f"{path.name}: {needle}")
        assert offenders == []

    def test_only_a_passed_verdict_is_verified(self):
        for state in (vc.FAILED, vc.PENDING, vc.MISSING, vc.UNKNOWN, "", "success", None):
            assert not vc.Verdict(state, SHA, SLUG, ("gate",), "x").verified
        assert vc.Verdict(vc.PASSED, SHA, SLUG, ("gate",), "x").verified
