"""scripts/experimental_ratchet.py: an experimental test may be broken, never
silently (#657).

The `experimental` CI job runs ``-m experimental`` and hands its JUnit report
to this script. The script fails the job only on a failure that is not in
tests/experimental_known_failures.txt, reports a known failure that now
passes so it can be removed, and refuses a known-failures file that grew
(the list can only shrink). On a push to `dev` a second job files or
comments on an `experimental-broken` issue per test file.

The issue filing is exercised against a fake `gh` on PATH that records its
argv and answers from a JSON fixture; nothing here talks to GitHub.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "experimental_ratchet.py"
KNOWN = Path(__file__).resolve().parent / "experimental_known_failures.txt"


def _load():
    loader = importlib.machinery.SourceFileLoader("experimental_ratchet", str(SCRIPT))
    spec = importlib.util.spec_from_loader("experimental_ratchet", loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules["experimental_ratchet"] = module
    loader.exec_module(module)
    return module


ratchet = _load()


def _case(classname: str, name: str, file: str, outcome: str = "passed") -> str:
    inner = {
        "passed": "",
        "failed": '<failure message="AssertionError: boom">trace</failure>',
        "error": '<error message="failed on teardown">trace</error>',
        "skipped": '<skipped message="no postgres" type="pytest.skip" />',
    }[outcome]
    return (
        f'<testcase classname="{classname}" name="{name}" file="{file}" time="0">{inner}</testcase>'
    )


def _junit(tmp_path: Path, *cases: str) -> Path:
    path = tmp_path / "junit.xml"
    path.write_text(
        '<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest">'
        + "".join(cases)
        + "</testsuite></testsuites>"
    )
    return path


def _known(tmp_path: Path, *lines: str, name: str = "known.txt") -> Path:
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n")
    return path


class TestNodeIds:
    """JUnit (xunit1) carries the file; the node id is rebuilt from it."""

    def test_a_class_method_with_a_parameter(self, tmp_path):
        junit = _junit(
            tmp_path,
            _case(
                "extensions.journalism-investigation.tests.test_x.TestK",
                "test_one[a::b.c]",
                "extensions/journalism-investigation/tests/test_x.py",
                "failed",
            ),
        )
        assert ratchet.read_junit(junit) == {
            "extensions/journalism-investigation/tests/test_x.py::TestK::test_one[a::b.c]": "failed"
        }

    def test_a_module_level_test_and_outcomes(self, tmp_path):
        junit = _junit(
            tmp_path,
            _case("tests.test_a", "test_p", "tests/test_a.py"),
            _case("tests.test_a", "test_e", "tests/test_a.py", "error"),
            _case("tests.test_a", "test_s", "tests/test_a.py", "skipped"),
        )
        assert ratchet.read_junit(junit) == {
            "tests/test_a.py::test_p": "passed",
            "tests/test_a.py::test_e": "failed",
            "tests/test_a.py::test_s": "skipped",
        }

    def test_a_collection_error_is_the_file(self, tmp_path):
        junit = _junit(tmp_path, _case("", "tests.test_broken", "tests/test_broken.py", "error"))
        assert ratchet.read_junit(junit) == {"tests/test_broken.py": "failed"}


class TestCompare:
    def test_a_new_failure_fails(self, tmp_path):
        junit = _junit(tmp_path, _case("tests.test_a", "test_x", "tests/test_a.py", "failed"))
        result = ratchet.compare(ratchet.read_junit(junit), set())
        assert result.new == ["tests/test_a.py::test_x"]
        assert not result.ok

    def test_a_known_failure_does_not_fail(self, tmp_path):
        junit = _junit(tmp_path, _case("tests.test_a", "test_x", "tests/test_a.py", "failed"))
        result = ratchet.compare(ratchet.read_junit(junit), {"tests/test_a.py::test_x"})
        assert result.ok and result.still_failing == ["tests/test_a.py::test_x"]

    def test_a_known_failure_that_passes_is_reported_for_removal(self, tmp_path):
        junit = _junit(tmp_path, _case("tests.test_a", "test_x", "tests/test_a.py"))
        result = ratchet.compare(ratchet.read_junit(junit), {"tests/test_a.py::test_x"})
        assert result.ok and result.fixed == ["tests/test_a.py::test_x"]

    def test_a_known_failure_that_no_longer_exists_is_reported_stale(self, tmp_path):
        junit = _junit(tmp_path, _case("tests.test_a", "test_y", "tests/test_a.py"))
        result = ratchet.compare(ratchet.read_junit(junit), {"tests/test_a.py::test_gone"})
        assert result.ok and result.stale == ["tests/test_a.py::test_gone"]

    def test_a_skipped_known_failure_is_neither_fixed_nor_stale(self, tmp_path):
        junit = _junit(tmp_path, _case("tests.test_a", "test_x", "tests/test_a.py", "skipped"))
        result = ratchet.compare(ratchet.read_junit(junit), {"tests/test_a.py::test_x"})
        assert result.ok and not result.fixed and not result.stale


class TestKnownFailuresFile:
    def test_comments_and_blank_lines_are_ignored(self, tmp_path):
        path = _known(tmp_path, "# header", "", "tests/test_a.py::test_x  # why: #123")
        assert ratchet.read_known(path) == {"tests/test_a.py::test_x"}

    def test_the_committed_file_parses_and_names_no_security_test(self):
        from tests import experimental_surface

        known = ratchet.read_known(KNOWN)
        assert all(experimental_surface.is_experimental(n) for n in known), known

    def test_it_can_only_shrink(self, tmp_path):
        base = _known(tmp_path, "tests/test_a.py::test_x", name="base.txt")
        head = _known(tmp_path, "tests/test_a.py::test_x", "tests/test_a.py::test_y")
        assert ratchet.grown(ratchet.read_known(head), base) == ["tests/test_a.py::test_y"]

    def test_shrinking_is_fine(self, tmp_path):
        base = _known(tmp_path, "tests/test_a.py::test_x", name="base.txt")
        head = _known(tmp_path, "# empty")
        assert ratchet.grown(ratchet.read_known(head), base) == []

    def test_no_base_file_is_the_seed(self, tmp_path):
        head = _known(tmp_path, "tests/test_a.py::test_x")
        assert ratchet.grown(ratchet.read_known(head), tmp_path / "absent.txt") == []


def _run(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
        timeout=60,
    )


class TestCheckCommand:
    def test_exit_status_and_summary(self, tmp_path):
        junit = _junit(
            tmp_path,
            _case("tests.test_a", "test_new", "tests/test_a.py", "failed"),
            _case("tests.test_a", "test_known", "tests/test_a.py", "failed"),
            _case("tests.test_a", "test_fixed", "tests/test_a.py"),
        )
        known = _known(tmp_path, "tests/test_a.py::test_known", "tests/test_a.py::test_fixed")
        summary = tmp_path / "summary.md"
        result_json = tmp_path / "result.json"
        proc = _run(
            "check",
            "--junit",
            str(junit),
            "--known",
            str(known),
            "--summary",
            str(summary),
            "--result-json",
            str(result_json),
        )
        assert proc.returncode == 1, proc.stdout + proc.stderr
        text = summary.read_text()
        assert "tests/test_a.py::test_new" in text
        assert "tests/test_a.py::test_fixed" in text and "remove" in text.lower()
        data = json.loads(result_json.read_text())
        assert data["new"] == ["tests/test_a.py::test_new"]

    def test_only_known_failures_pass(self, tmp_path):
        junit = _junit(tmp_path, _case("tests.test_a", "test_known", "tests/test_a.py", "failed"))
        known = _known(tmp_path, "tests/test_a.py::test_known")
        proc = _run("check", "--junit", str(junit), "--known", str(known))
        assert proc.returncode == 0, proc.stdout + proc.stderr

    def test_a_grown_file_fails_even_when_every_test_is_known(self, tmp_path):
        junit = _junit(tmp_path, _case("tests.test_a", "test_known", "tests/test_a.py", "failed"))
        known = _known(tmp_path, "tests/test_a.py::test_known")
        base = _known(tmp_path, "# nothing", name="base.txt")
        proc = _run(
            "check", "--junit", str(junit), "--known", str(known), "--base-known", str(base)
        )
        assert proc.returncode == 1
        assert "only shrink" in proc.stdout + proc.stderr

    def test_a_missing_report_fails_closed(self, tmp_path):
        """No JUnit file means the run never happened: that is not a pass."""
        known = _known(tmp_path, "# none")
        proc = _run("check", "--junit", str(tmp_path / "absent.xml"), "--known", str(known))
        assert proc.returncode == 2


# -- issue filing, against a fake gh ---------------------------------------

FAKE_GH = textwrap.dedent(
    """\
    #!{python}
    import json, os, sys
    log = os.environ["FAKE_GH_LOG"]
    with open(log, "a") as fh:
        fh.write(json.dumps(sys.argv[1:]) + "\\n")
    if os.environ.get("FAKE_GH_FAIL"):
        sys.exit("gh: HTTP 403")
    state = json.load(open(os.environ["FAKE_GH_STATE"]))
    args = sys.argv[1:]
    if args[:2] == ["issue", "list"]:
        print(json.dumps(state.get("open", [])))
    elif args[:2] == ["issue", "view"]:
        print(json.dumps(state["views"][args[2]]))
    elif args[:2] == ["issue", "create"]:
        print("https://github.com/o/r/issues/999")
    """
)


@pytest.fixture
def gh_env(tmp_path):
    """A fake `gh` on PATH: (env for a subprocess, a reader of its calls)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(FAKE_GH.format(python=sys.executable))
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "gh.log"
    state = tmp_path / "state.json"

    def env(open_issues: list | None = None, views: dict | None = None, fail: bool = False):
        state.write_text(json.dumps({"open": open_issues or [], "views": views or {}}))
        out = {
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "FAKE_GH_LOG": str(log),
            "FAKE_GH_STATE": str(state),
        }
        if fail:
            out["FAKE_GH_FAIL"] = "1"
        return out

    def calls():
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    return env, calls


@pytest.fixture
def fake_gh(tmp_path, gh_env):
    make_env, calls = gh_env

    def run(result, open_issues: list | None = None, views: dict | None = None, *extra):
        result_json = tmp_path / "result.json"
        if result is not None:
            result_json.write_text(json.dumps(result))
        proc = _run(
            "file-issues",
            "--result-json",
            str(result_json),
            "--sha",
            "abc1234",
            "--run-url",
            "https://github.com/o/r/actions/runs/1",
            *extra,
            env=make_env(open_issues, views),
        )
        return proc, calls()

    return run


def _result(*new: str) -> dict:
    return {"new": list(new), "fixed": [], "stale": [], "still_failing": [], "messages": {}}


def _body(call: list[str]) -> str:
    return call[call.index("--body") + 1]


class TestFileIssues:
    def test_nothing_new_calls_nothing(self, fake_gh):
        proc, calls = fake_gh(_result())
        assert proc.returncode == 0 and calls == []

    def test_a_new_failure_opens_an_issue_naming_test_commit_and_run(self, fake_gh):
        proc, calls = fake_gh(_result("tests/test_a.py::test_x"))
        assert proc.returncode == 0, proc.stderr
        assert calls[0][:3] == ["label", "create", "experimental-broken"]
        (create,) = [c for c in calls if c[:2] == ["issue", "create"]]
        assert create[create.index("--title") + 1] == "experimental-broken: tests/test_a.py"
        assert create[create.index("--label") + 1] == "experimental-broken"
        body = _body(create)
        assert "tests/test_a.py::test_x" in body
        assert "abc1234" in body and "https://github.com/o/r/actions/runs/1" in body

    def test_one_issue_per_file_not_per_test(self, fake_gh):
        """An import error can fail every test in a module: one issue, not 300."""
        tests = [f"extensions/x/tests/test_m.py::test_{i}" for i in range(300)]
        proc, calls = fake_gh(_result(*tests, "tests/test_b.py::test_y"))
        creates = [c for c in calls if c[:2] == ["issue", "create"]]
        assert len(creates) == 2
        assert all(t in _body(creates[0]) + _body(creates[1]) for t in tests[:3])

    def test_an_open_issue_for_the_file_gets_a_comment_instead(self, fake_gh):
        open_issues = [{"number": 42, "title": "experimental-broken: tests/test_a.py"}]
        views = {"42": {"body": "- `tests/test_a.py::test_old`", "comments": []}}
        proc, calls = fake_gh(_result("tests/test_a.py::test_x"), open_issues, views)
        assert not [c for c in calls if c[:2] == ["issue", "create"]]
        (comment,) = [c for c in calls if c[:2] == ["issue", "comment"]]
        assert comment[2] == "42"
        assert "tests/test_a.py::test_x" in _body(comment) and "abc1234" in _body(comment)

    def test_a_test_the_issue_already_names_is_not_commented_again(self, fake_gh):
        """Every dev push re-runs the job; a still-broken test is not news."""
        open_issues = [{"number": 42, "title": "experimental-broken: tests/test_a.py"}]
        views = {"42": {"body": "x", "comments": [{"body": "- `tests/test_a.py::test_x`"}]}}
        proc, calls = fake_gh(_result("tests/test_a.py::test_x"), open_issues, views)
        assert proc.returncode == 0
        assert not [c for c in calls if c[:2] in (["issue", "create"], ["issue", "comment"])]

    def test_a_title_that_only_starts_the_same_is_another_file(self, fake_gh):
        open_issues = [{"number": 7, "title": "experimental-broken: tests/test_a.py.bak"}]
        proc, calls = fake_gh(_result("tests/test_a.py::test_x"), open_issues)
        assert [c for c in calls if c[:2] == ["issue", "create"]]

    def test_dry_run_prints_and_calls_nothing(self, fake_gh):
        proc, calls = fake_gh(_result("tests/test_a.py::test_x"), None, None, "--dry-run")
        assert proc.returncode == 0 and calls == []
        assert "experimental-broken: tests/test_a.py" in proc.stdout


# -- Property 1: never silently (#657 review) ------------------------------


class TestAnIncompleteRunIsNotAPass:
    """The ratchet knows pytest's exit status: an interrupted, crashed or
    timed-out run, or a missing report, is reported, never green."""

    def _check(self, tmp_path, junit, *extra):
        known = _known(tmp_path, "# none")
        result_json = tmp_path / "result.json"
        proc = _run(
            "check", "--junit", str(junit), "--known", str(known),
            "--result-json", str(result_json), *extra,
        )  # fmt: skip
        return proc, json.loads(result_json.read_text())

    @pytest.mark.parametrize("code", ["2", "3", "4", "5", "", "137"])
    def test_a_run_that_did_not_complete_fails(self, tmp_path, code):
        junit = _junit(tmp_path, _case("tests.test_a", "test_x", "tests/test_a.py"))
        proc, result = self._check(tmp_path, junit, "--run-exit", code)
        assert proc.returncode == 2, proc.stdout
        assert result["incomplete"]

    @pytest.mark.parametrize("code", ["0", "1"])
    def test_a_completed_run_is_judged_on_its_report(self, tmp_path, code):
        junit = _junit(tmp_path, _case("tests.test_a", "test_x", "tests/test_a.py"))
        proc, result = self._check(tmp_path, junit, "--run-exit", code)
        assert proc.returncode == 0 and result["incomplete"] is None

    def test_no_report_still_writes_a_result_to_file(self, tmp_path):
        proc, result = self._check(tmp_path, tmp_path / "absent.xml", "--run-exit", "0")
        assert proc.returncode == 2
        assert "no JUnit report" in result["incomplete"]


class TestAFailureAlreadyFiledIsNotNews:
    """Once dev has an open experimental-broken issue naming a test, that test
    failing on a PR (or the next dev push) is reported as filed, not new: red
    keeps meaning news. A filed test that passes again is named so its issue
    can be closed."""

    ISSUE = {
        "number": 42,
        "title": "experimental-broken: tests/test_a.py",
        "body": "- `tests/test_a.py::test_x`\n",
        "comments": [{"body": "- `tests/test_a.py::test_y`"}],
    }

    def _check(self, tmp_path, gh_env, junit, **gh):
        make_env, calls = gh_env
        known = _known(tmp_path, "# none")
        result_json = tmp_path / "result.json"
        proc = _run(
            "check", "--junit", str(junit), "--known", str(known), "--open-issues",
            "--run-exit", "1", "--result-json", str(result_json),
            env=make_env(**gh),
        )  # fmt: skip
        return proc, json.loads(result_json.read_text()), calls()

    def test_a_filed_failure_is_not_new(self, tmp_path, gh_env):
        junit = _junit(
            tmp_path,
            _case("tests.test_a", "test_x", "tests/test_a.py", "failed"),
            _case("tests.test_a", "test_y", "tests/test_a.py", "failed"),
        )
        proc, result, calls = self._check(tmp_path, gh_env, junit, open_issues=[self.ISSUE])
        assert proc.returncode == 0, proc.stdout
        assert result["new"] == []
        assert result["filed"] == {"tests/test_a.py::test_x": 42, "tests/test_a.py::test_y": 42}
        assert "#42" in proc.stdout
        (listing,) = [c for c in calls if c[:2] == ["issue", "list"]]
        assert "body,comments" in ",".join(listing) or "number,title,body,comments" in listing

    def test_an_unfiled_failure_is_still_new(self, tmp_path, gh_env):
        junit = _junit(tmp_path, _case("tests.test_b", "test_z", "tests/test_b.py", "failed"))
        proc, result, _ = self._check(tmp_path, gh_env, junit, open_issues=[self.ISSUE])
        assert proc.returncode == 1 and result["new"] == ["tests/test_b.py::test_z"]

    def test_a_filed_test_that_passes_again_is_named_for_closing(self, tmp_path, gh_env):
        junit = _junit(tmp_path, _case("tests.test_a", "test_x", "tests/test_a.py"))
        proc, result, _ = self._check(tmp_path, gh_env, junit, open_issues=[self.ISSUE])
        assert result["filed_now_passing"] == {"tests/test_a.py::test_x": 42}
        assert "close" in proc.stdout.lower()

    def test_issues_that_cannot_be_read_make_every_failure_new(self, tmp_path, gh_env):
        """Fail loud: an API error must not hide a failure as 'filed'."""
        junit = _junit(tmp_path, _case("tests.test_a", "test_x", "tests/test_a.py", "failed"))
        proc, result, _ = self._check(tmp_path, gh_env, junit, open_issues=[self.ISSUE], fail=True)
        assert proc.returncode == 1 and result["new"] == ["tests/test_a.py::test_x"]
        assert "could not read" in proc.stdout


class TestEveryKindOfNewsIsFiledOnDev:
    def test_a_grown_known_failures_list_is_filed(self, fake_gh):
        result = {**_result(), "grown": ["tests/test_a.py::test_x"]}
        proc, calls = fake_gh(result)
        assert proc.returncode == 0, proc.stderr
        (create,) = [c for c in calls if c[:2] == ["issue", "create"]]
        title = create[create.index("--title") + 1]
        assert "experimental_known_failures.txt" in title
        assert "tests/test_a.py::test_x" in _body(create) and "abc1234" in _body(create)

    def test_a_run_that_did_not_complete_is_filed(self, fake_gh):
        result = {**_result(), "incomplete": "pytest exited 2 (interrupted)"}
        proc, calls = fake_gh(result)
        (create,) = [c for c in calls if c[:2] == ["issue", "create"]]
        assert "did not complete" in create[create.index("--title") + 1]
        assert "pytest exited 2" in _body(create)

    def test_no_result_at_all_is_filed_with_the_job_conclusion(self, fake_gh):
        proc, calls = fake_gh(None, None, None, "--job-result", "cancelled")
        assert proc.returncode == 0, proc.stderr
        (create,) = [c for c in calls if c[:2] == ["issue", "create"]]
        assert "cancelled" in _body(create)
        assert "https://github.com/o/r/actions/runs/1" in _body(create)

    def test_an_incomplete_run_comments_on_the_open_issue(self, fake_gh):
        open_issues = [
            {"number": 5, "title": "experimental-broken: the experimental run did not complete"}
        ]
        views = {"5": {"body": "earlier", "comments": []}}
        proc, calls = fake_gh({**_result(), "incomplete": "timed out"}, open_issues, views)
        (comment,) = [c for c in calls if c[:2] == ["issue", "comment"]]
        assert comment[2] == "5" and "abc1234" in _body(comment)
