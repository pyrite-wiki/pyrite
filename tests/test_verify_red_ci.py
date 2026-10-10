"""verify-red: do a pull request's new tests notice its change? (#352, redesigned in 0.26)

`scripts/verify_red_ci.py` never touches the developer's tree. It checks out the
merge base into a throwaway `git worktree` under $TMPDIR, overlays the PR's
test-side files (committed or not) and runs the PR's new and edited tests: that
is the run *without the fix*. Then it overlays the rest of the change and runs
them again *with the fix*. The tree is removed; nothing is ever restored.

Every scenario here builds a real git repository in tmp and runs the real
script and real pytest subprocesses. The developer's tree is compared byte for
byte before and after each run, including one killed with SIGKILL.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "verify_red_ci.py"
CI_PATH = REPO / ".github" / "workflows" / "ci.yml"


def _load():
    spec = importlib.util.spec_from_file_location("verify_red_ci", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["verify_red_ci"] = module  # dataclasses resolve their module by name
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def vr():
    return _load()


# ---------------------------------------------------------------------------
# Pure decisions
# ---------------------------------------------------------------------------


class TestTestSide:
    """Test-side files are overlaid into the run without the fix; everything
    else the PR changes is the fix."""

    @pytest.mark.control(reason="unchanged test, fixture and production-path boundaries")
    @pytest.mark.parametrize(
        ("path", "test_side"),
        [
            ("tests/test_x.py", True),
            ("tests/fixtures/data.json", True),
            ("conftest.py", True),
            ("extensions/cascade/tests/test_plugin.py", True),
            ("extensions/cascade/tests/conftest.py", True),
            ("pyrite/services/kb_service.py", False),
            ("extensions/cascade/src/pyrite_cascade/plugin.py", False),
            ("scripts/release.py", False),
            (".github/workflows/ci.yml", False),
            ("pyproject.toml", False),
            ("kb/backlog/x.md", False),
            ("pyrite/tests_util.py", False),
        ],
    )
    def test_split(self, vr, path, test_side):
        assert vr.is_test_side(path) is test_side

    @pytest.mark.parametrize(
        "path",
        ["tests/unit/helpers.py", "tests/__init__.py", "extensions/cascade/tests/helpers.py"],
    )
    def test_helpers_are_code_under_test(self, vr, path):
        assert not vr.is_test_side(path)

    def test_only_test_modules_are_run(self, vr):
        assert vr.is_test_file("tests/unit/test_nested.py")
        assert vr.is_test_file("extensions/cascade/tests/test_plugin.py")
        assert not vr.is_test_file("tests/conftest.py")
        assert not vr.is_test_file("tests/fixtures/data.py")
        assert not vr.is_test_file("pyrite/test_mode.py")


@pytest.mark.control(reason="kept from the old runner: passes without this change too")
class TestWhichTestsThePRTouched:
    BASE = (
        "def test_same():\n    assert 1\n\n\n"
        "def test_edited():\n    assert 1\n\n\n"
        "class TestK:\n    def test_m(self):\n        assert 1\n"
    )

    def test_new_and_edited_tests_only(self, vr):
        head = (
            "def test_same():\n    assert 1\n\n\n"
            "def test_edited():\n    assert 2\n\n\n"
            "class TestK:\n    def test_m(self):\n        assert 1\n\n"
            "    def test_n(self):\n        assert 1\n"
        )
        assert vr.touched_tests(self.BASE, head) == {("test_edited",), ("TestK", "test_n")}

    def test_formatting_only_edits_are_not_edits(self, vr):
        reformatted = (
            "# a comment\n\ndef test_same():\n    assert (1)  # why\n\n\n\n"
            "def test_edited():\n    assert 1\n"
            "class TestK:\n    def test_m(self):\n\n        assert 1\n"
        )
        assert vr.touched_tests(self.BASE, reformatted) == set()

    def test_a_decorator_change_is_a_change(self, vr):
        head = self.BASE.replace("def test_same", "@pytest.mark.slow\ndef test_same")
        assert vr.touched_tests(self.BASE, head) == {("test_same",)}

    def test_a_new_file_touches_every_test(self, vr):
        assert vr.touched_tests(None, self.BASE) == {
            ("test_same",),
            ("test_edited",),
            ("TestK", "test_m"),
        }

    def test_a_base_that_does_not_parse_counts_as_absent(self, vr):
        assert vr.touched_tests("def (:\n", self.BASE) == vr.touched_tests(None, self.BASE)


class TestAddedIdentifiers:
    def test_names_the_change_defines_that_the_base_did_not(self, vr):
        base = "def old():\n    pass\n\n\nclass C:\n    def m(self):\n        pass\n"
        head = (
            base
            + "\n\ndef new_fn():\n    pass\n\n\nclass C2:\n    attr = 1\n\n"
            + "    def meth(self):\n        pass\n\n\nCONST = 3\n"
        )
        added = vr.defined_names(head) - vr.defined_names(base)
        assert added == {"new_fn", "C2", "attr", "meth", "CONST"}

    def test_names_inside_function_bodies_do_not_count(self, vr):
        # B3: an unrelated nested get() must not turn "'NoneType' object has no
        # attribute 'get'" into import-only.
        head = "def helper():\n    def get():\n        pass\n\n    x = 1\n    return get\n"
        added = vr.defined_names(head) - vr.defined_names(None)
        assert added == {"helper"}
        exc = {"types": ["AttributeError"], "args": ["'NoneType' object has no attribute 'get'"]}
        verdict, _ = vr.classify(
            {"outcome": "passed"},
            {"outcome": "failed", "exc": exc},
            collect_failed=False,
            control=None,
            added=added,
        )
        assert verdict == vr.RED

    def test_a_new_module_adds_its_dotted_name(self, vr):
        assert vr.module_names("pyrite/services/newmod.py") == {
            "pyrite.services.newmod",
            "newmod",
        }
        assert vr.module_names("extensions/x/src/pyrite_x/sub/__init__.py") == {
            "pyrite_x.sub",
            "sub",
        }


def _exc(types: list[str], *texts: str) -> dict:
    return {"types": types, "args": list(texts)}


PASSED = {"outcome": "passed"}


class TestClassify:
    ADDED = frozenset({"helper", "pyrite.newmod", "newmod"})

    def verdict(self, vr, with_fix, without, *, collect_failed=False, control=None):
        return vr.classify(
            with_fix, without, collect_failed=collect_failed, control=control, added=self.ADDED
        )[0]

    def test_red(self, vr):
        failed = {"outcome": "failed", "exc": _exc(["AssertionError"], "assert 0 == 4")}
        assert self.verdict(vr, PASSED, failed) == vr.RED

    def test_passes_without_the_fix(self, vr):
        assert self.verdict(vr, PASSED, PASSED) == vr.UNEXPECTED

    def test_a_declared_control_is_not_a_warning(self, vr):
        assert self.verdict(vr, PASSED, PASSED, control="a still-works guard") == vr.CONTROL

    def test_a_control_without_a_reason_is_rejected(self, vr):
        verdict, detail = vr.classify(
            PASSED, PASSED, collect_failed=False, control="", added=self.ADDED
        )
        assert verdict == vr.UNEXPECTED
        assert "needs a reason" in detail

    def test_a_collection_failure_without_the_fix_is_import_only(self, vr):
        assert self.verdict(vr, PASSED, None, collect_failed=True) == vr.IMPORT_ONLY

    def test_an_import_error_naming_an_added_name_is_import_only(self, vr):
        exc = _exc(["ImportError", "Exception"], "cannot import name 'helper' from 'pyrite'")
        assert self.verdict(vr, PASSED, {"outcome": "failed", "exc": exc}) == vr.IMPORT_ONLY

    def test_a_missing_module_the_pr_adds_is_import_only(self, vr):
        exc = _exc(
            ["ModuleNotFoundError", "ImportError"],
            "No module named 'pyrite.newmod'",
        )
        assert self.verdict(vr, PASSED, {"outcome": "failed", "exc": exc}) == vr.IMPORT_ONLY

    def test_an_attribute_error_on_a_name_the_pr_does_not_add_is_red(self, vr):
        exc = _exc(["AttributeError"], "'NoneType' object has no attribute 'upper'")
        assert self.verdict(vr, PASSED, {"outcome": "failed", "exc": exc}) == vr.RED

    def test_an_assertion_that_quotes_an_added_name_is_still_red(self, vr):
        # #368: the old pattern searched the whole failure message.
        exc = _exc(["AssertionError"], "module 'pyrite' has no attribute 'helper'")
        assert self.verdict(vr, PASSED, {"outcome": "failed", "exc": exc}) == vr.RED

    @pytest.mark.parametrize(
        ("with_fix", "without", "detail"),
        [
            ({"outcome": "failed"}, PASSED, "failed with the fix"),
            ({"outcome": "skipped"}, PASSED, "skipped with the fix"),
            (None, PASSED, "not run with the fix"),
            (PASSED, {"outcome": "skipped"}, "skipped without the fix"),
            (PASSED, None, "not run without the fix"),
        ],
    )
    def test_no_claim(self, vr, with_fix, without, detail):
        verdict, text = vr.classify(
            with_fix, without, collect_failed=False, control=None, added=self.ADDED
        )
        assert verdict == vr.NA
        assert detail in text


def test_the_summary_line(vr):
    counts = {vr.RED: 3, vr.IMPORT_ONLY: 0, vr.UNEXPECTED: 0, vr.NA: 1, vr.CONTROL: 0}
    assert vr.summary_line(counts) == (
        "verify-red: 3 red · 0 import-only · 0 unexpected pass · 1 n/a"
    )
    counts[vr.CONTROL] = 2
    assert vr.summary_line(counts).endswith(" · 2 control")


# ---------------------------------------------------------------------------
# A real repository: `dev` has a broken `pyrite.add`, the branch fixes it.
# ---------------------------------------------------------------------------

BROKEN = (
    "def add(a, b):\n    return a - b\n\n\ndef make():\n    return None\n\n\nclass C:\n    pass\n"
)
FIXED = (
    "def add(a, b):\n    return a + b\n\n\n"
    "def make():\n    return 'x'\n\n\n"
    "class C:\n    def method(self):\n        return 1\n\n\n"
    "def helper():\n    return 1\n"
)
OLD_TESTS = "from pyrite import add\n\n\ndef test_unchanged():\n    assert add(0, 0) == 0\n"
# test_unchanged is reformatted only: the AST is the same, so it is pre-existing.
PR_TESTS = """\
import pytest

from pyrite import add, make


def test_unchanged():
    assert add(0, 0) == (0)


def test_real():
    assert add(2, 2) == 4


def test_vacuous():
    assert callable(add)


@pytest.mark.control(reason="add(0, 0) was already right")
def test_declared_control():
    assert add(0, 0) == 0


@pytest.mark.control
def test_docstring_control():
    'A still-works guard: add(0, 0) was already right.'
    assert add(0, 0) == 0


@pytest.mark.control
def test_bare_control():
    assert add(0, 0) == 0 * 2


def test_lazy_import():
    from pyrite import helper

    assert helper() == 1


def test_attribute_on_a_value():
    assert make().upper() == "X"


def test_assertion_quoting_an_added_name():
    assert add(2, 3) == 5, "module 'pyrite' has no attribute 'helper'"


@pytest.mark.parametrize("n", ["a::b"])  # a parameter id holding "::"
def test_param(n):
    assert add(2, 2) == 4
"""
NEW_FILE_TESTS = "from pyrite import helper\n\n\ndef test_helper():\n    assert helper() == 1\n"
PATCH_TESTS = """\
from unittest import mock

import pytest

import pyrite


@pytest.fixture
def patched(monkeypatch):
    monkeypatch.setattr("pyrite.helper", lambda: 2)


def test_mock_patch_string():
    with mock.patch("pyrite.helper", return_value=2):
        assert pyrite.add(1, 1) == 2


def test_mock_patch_object():
    with mock.patch.object(pyrite.C, "method", return_value=2):
        assert pyrite.add(1, 1) == 2


def test_monkeypatch_setattr_string(monkeypatch):
    monkeypatch.setattr("pyrite.helper", lambda: 2)
    assert pyrite.add(1, 1) == 2


def test_monkeypatch_delattr(monkeypatch):
    monkeypatch.delattr(pyrite.C, "method")
    assert pyrite.add(1, 1) == 2


def test_patched_in_a_fixture(patched):
    assert pyrite.add(1, 1) == 2
"""


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def make_repo(root: Path) -> Path:
    r = root / "repo"
    (r / "pyrite").mkdir(parents=True)
    (r / "tests").mkdir()
    git(r, "init", "-q", "-b", "dev")
    git(r, "config", "user.email", "t@example.com")
    git(r, "config", "user.name", "t")
    (r / ".gitignore").write_text("__pycache__/\n")
    # The repo's own --tb=short (pyproject), and no `pythonpath`: the script must
    # make the tests import the throwaway tree, not this interpreter's installed
    # Pyrite (#189). --strict-markers, and no `control` marker registered: the
    # merge base of a real PR predates the marker too.
    (r / "pytest.ini").write_text("[pytest]\naddopts = -v --tb=short --strict-markers\n")
    (r / "pyrite" / "__init__.py").write_text(BROKEN)
    (r / "tests" / "test_add.py").write_text(OLD_TESTS)
    git(r, "add", ".")
    git(r, "commit", "-q", "-m", "base")
    git(r, "checkout", "-q", "-b", "fix/add")
    return r


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return make_repo(tmp_path)


def commit_all(repo: Path, msg: str = "fix: add adds") -> None:
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", msg)


def _env(tmp: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    (tmp / "tmpdir").mkdir(exist_ok=True)
    env = {
        **os.environ,
        "TMPDIR": str(tmp / "tmpdir"),  # the throwaway tree lands here, where a test can see it
        "GITHUB_STEP_SUMMARY": str(tmp / "summary.md"),
        "GITHUB_ACTIONS": "true",
        **(extra or {}),
    }
    for var in ("PYTEST_ADDOPTS", "PYTEST_XDIST_WORKER", "PYTEST_CURRENT_TEST"):
        env.pop(var, None)
    return env


def run_vr(
    repo: Path, tmp: Path, *args: str, env: dict[str, str] | None = None, cwd: Path | None = None
) -> tuple[subprocess.CompletedProcess[str], str]:
    summary = tmp / "summary.md"
    summary.unlink(missing_ok=True)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--base", "dev", "--python", sys.executable, *args],
        cwd=cwd or repo,
        env=env or _env(tmp),
        capture_output=True,
        text=True,
        timeout=180,
    )
    return result, summary.read_text() if summary.exists() else ""


def snapshot(repo: Path) -> tuple[str, str, dict[str, str]]:
    """The index, `git status` and every file's bytes outside .git: what the developer has."""
    index = hashlib.sha256((repo / ".git" / "index").read_bytes()).hexdigest()
    status = git(repo, "status", "--porcelain=v1", "-uall", "--ignored")
    files = {
        str(p.relative_to(repo)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(repo.rglob("*"))
        if p.is_file() and ".git" not in p.relative_to(repo).parts
    }
    return index, status, files


def worktrees(repo: Path) -> list[str]:
    return [
        ln
        for ln in git(repo, "worktree", "list", "--porcelain").splitlines()
        if ln.startswith("worktree ")
    ]


def verdict_of(summary: str, test: str) -> str:
    """The verdict cell of a table row; 'red' when the test has no row (red is not listed)."""
    rows = [ln for ln in summary.splitlines() if ln.startswith(f"| `{test}`")]
    assert len(rows) <= 1, rows
    return rows[0].split("|")[2].strip() if rows else "red"


# ---------------------------------------------------------------------------
# One realistic PR, run once, read by several tests.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pr_run(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("pr")
    repo = make_repo(tmp)
    (repo / "pyrite" / "__init__.py").write_text(FIXED)
    (repo / "tests" / "test_add.py").write_text(PR_TESTS)
    (repo / "tests" / "test_helper.py").write_text(NEW_FILE_TESTS)
    (repo / "tests" / "test_patch.py").write_text(PATCH_TESTS)
    (repo / "tests" / "test_broken.py").write_text(
        "import not_a_module_anywhere  # noqa: F401\n\n\ndef test_broken():\n    pass\n"
    )
    commit_all(repo)
    before = snapshot(repo)
    result, summary = run_vr(repo, tmp, "--json", str(tmp / "evidence.json"))
    # Everything below reads this run: a run that did nothing must not pass them.
    assert result.returncode == 0, (result.stdout, result.stderr)
    return {
        "repo": repo,
        "tmp": tmp,
        "result": result,
        "summary": summary,
        "before": before,
        "after": snapshot(repo),
        "evidence": json.loads((tmp / "evidence.json").read_text())
        if (tmp / "evidence.json").exists()
        else None,
    }


def test_the_run_succeeds(pr_run):
    assert pr_run["result"].returncode == 0, (pr_run["result"].stdout, pr_run["result"].stderr)


def test_the_summary_line_counts_only_the_prs_own_tests(pr_run):
    line = "verify-red: 4 red · 7 import-only · 2 unexpected pass · 1 n/a · 2 control"
    assert line in pr_run["summary"], pr_run["summary"]
    assert line in pr_run["result"].stdout
    assert "1 pre-existing test" in pr_run["summary"]


@pytest.mark.parametrize(
    ("test", "verdict"),
    [
        ("tests/test_add.py::test_real", "red"),
        ("tests/test_add.py::test_param[a::b]", "red"),
        ("tests/test_add.py::test_attribute_on_a_value", "red"),
        ("tests/test_add.py::test_assertion_quoting_an_added_name", "red"),
        ("tests/test_add.py::test_vacuous", "unexpected pass"),
        ("tests/test_add.py::test_declared_control", "control"),
        ("tests/test_add.py::test_docstring_control", "control"),
        ("tests/test_add.py::test_bare_control", "unexpected pass"),
        ("tests/test_add.py::test_lazy_import", "import-only"),
        ("tests/test_helper.py::test_helper", "import-only"),
        ("tests/test_patch.py::test_mock_patch_string", "import-only"),
        ("tests/test_patch.py::test_mock_patch_object", "import-only"),
        ("tests/test_patch.py::test_monkeypatch_setattr_string", "import-only"),
        ("tests/test_patch.py::test_monkeypatch_delattr", "import-only"),
        ("tests/test_patch.py::test_patched_in_a_fixture", "import-only"),
    ],
)
def test_each_new_test_gets_its_verdict(pr_run, test, verdict):
    assert verdict_of(pr_run["summary"], test) == verdict, pr_run["summary"]


def test_a_file_that_does_not_collect_with_the_fix_is_no_claim(pr_run):
    row = [ln for ln in pr_run["summary"].splitlines() if "test_broken.py::test_broken" in ln]
    assert row and "n/a" in row[0] and "does not collect with the fix" in row[0], row


def test_pre_existing_tests_are_a_count_not_rows(pr_run):
    assert "test_unchanged" not in pr_run["summary"]


def test_only_an_unexpected_pass_is_annotated(pr_run):
    warnings = [ln for ln in pr_run["result"].stdout.splitlines() if ln.startswith("::warning")]
    assert len(warnings) == 2, warnings
    assert "tests/test_add.py::test_bare_control" in warnings[0]
    assert "tests/test_add.py::test_vacuous" in warnings[1]
    assert "file=tests/test_add.py" in warnings[1]


def test_a_bare_control_marker_is_rejected(pr_run):
    row = [ln for ln in pr_run["summary"].splitlines() if "test_bare_control" in ln]
    assert row and "needs a reason" in row[0], pr_run["summary"]


def test_the_developers_tree_is_untouched(pr_run):
    assert pr_run["after"] == pr_run["before"]


def test_the_throwaway_tree_is_gone(pr_run):
    assert len(worktrees(pr_run["repo"])) == 1
    assert list((pr_run["tmp"] / "tmpdir").iterdir()) == []


def test_the_evidence_file(pr_run):
    ev = pr_run["evidence"]
    assert ev["verify_red"] == {
        "red": 4,
        "import-only": 7,
        "unexpected pass": 2,
        "n/a": 1,
        "control": 2,
        "pre-existing": 1,
    }
    assert ev["merge_base"] == git(pr_run["repo"], "merge-base", "dev", "HEAD")
    assert ev["head"] == git(pr_run["repo"], "rev-parse", "HEAD")
    assert ev["merge_commit"] == ev["head"]  # no --head-sha: HEAD is the head
    assert ev["fix_commits"] == 1
    assert ev["diff_coverage"] is None


# ---------------------------------------------------------------------------
# Scenarios, one repository each
# ---------------------------------------------------------------------------


def test_uncommitted_work_is_what_is_verified(repo: Path, tmp_path: Path) -> None:
    # A worker before its first commit: the fix is an unstaged edit, the test an
    # untracked file. Both are overlaid; neither is touched.
    (repo / "pyrite" / "__init__.py").write_text(FIXED)
    (repo / "tests" / "test_new.py").write_text(
        "from pyrite import add\n\n\ndef test_new():\n    assert add(2, 2) == 4\n"
    )
    before = snapshot(repo)
    result, summary = run_vr(repo, tmp_path)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "verify-red: 1 red · 0 import-only · 0 unexpected pass · 0 n/a" in summary, summary
    assert snapshot(repo) == before


def test_the_merge_base_not_the_bases_tip(repo: Path, tmp_path: Path) -> None:
    # dev fixed `add` too after the branch was cut. Against dev's tip the PR's test
    # would pass without the PR's fix; against the merge base it is red.
    (repo / "pyrite" / "__init__.py").write_text(FIXED)
    (repo / "tests" / "test_new.py").write_text(
        "from pyrite import add\n\n\ndef test_new():\n    assert add(2, 2) == 4\n"
    )
    commit_all(repo)
    git(repo, "checkout", "-q", "dev")
    (repo / "pyrite" / "__init__.py").write_text(FIXED + "\n# dev's own fix\n")
    commit_all(repo, "fix: dev fixes add")
    git(repo, "checkout", "-q", "fix/add")
    result, summary = run_vr(repo, tmp_path)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "verify-red: 1 red" in summary, summary


def test_a_renamed_test_file_is_not_all_new(repo: Path, tmp_path: Path) -> None:
    # #368: a rename is not an edit of every test in the file.
    git(repo, "mv", "tests/test_add.py", "tests/test_sum.py")
    (repo / "tests" / "test_sum.py").write_text(
        OLD_TESTS + "\n\ndef test_real():\n    assert add(2, 2) == 4\n"
    )
    (repo / "pyrite" / "__init__.py").write_text(FIXED)
    commit_all(repo)
    result, summary = run_vr(repo, tmp_path)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "verify-red: 1 red · 0 import-only · 0 unexpected pass · 0 n/a" in summary, summary
    assert "1 pre-existing test" in summary


def test_a_file_the_change_deletes_is_absent_with_the_fix(repo: Path, tmp_path: Path) -> None:
    # The run with the fix must match the working tree, deletions included.
    (repo / "pyrite" / "legacy.py").write_text("OLD = 1\n")
    commit_all(repo, "base: legacy")
    git(repo, "branch", "-f", "dev", "HEAD")
    git(repo, "rm", "-q", "pyrite/legacy.py")
    (repo / "tests" / "test_new.py").write_text(
        "import importlib.util\n\n\ndef test_legacy_is_gone():\n"
        "    assert importlib.util.find_spec('pyrite.legacy') is None\n"
    )
    commit_all(repo, "refactor: drop legacy")
    result, summary = run_vr(repo, tmp_path)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "verify-red: 1 red · 0 import-only · 0 unexpected pass · 0 n/a" in summary, summary


def test_a_conftest_that_needs_the_fix_is_no_claim_not_a_crash(repo: Path, tmp_path: Path) -> None:
    # Without the fix pytest cannot load the conftest and runs nothing: every test
    # is a row saying so, and the run still finishes.
    (repo / "pyrite" / "__init__.py").write_text(FIXED)
    (repo / "tests" / "conftest.py").write_text("from pyrite import helper  # noqa: F401\n")
    (repo / "tests" / "test_new.py").write_text(NEW_FILE_TESTS)
    commit_all(repo)
    result, summary = run_vr(repo, tmp_path)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "verify-red: 0 red · 0 import-only · 0 unexpected pass · 1 n/a" in summary, summary
    assert "not run without the fix" in summary
    assert "the without run recorded no test" in result.stderr


def test_a_test_only_pr_with_a_fragment_kb_and_docs_is_nothing_to_verify(
    repo: Path, tmp_path: Path
) -> None:
    # B1: a changelog fragment, a KB entry or a doc cannot change what a test
    # does, so a PR that adds a test and only those has nothing to verify.
    (repo / "tests" / "test_new.py").write_text(
        "from pyrite import add\n\n\ndef test_zero():\n    assert add(0, 0) == 0\n"
    )
    for path in ("changelog.d/zero.added.md", "kb/backlog/zero.md", "docs/zero.md", "README.md"):
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text("text\n")
    commit_all(repo, "test: add(0, 0)")
    result, summary = run_vr(repo, tmp_path)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert (
        "verify-red: nothing to verify (tests-only change: nothing to grade against)" in summary
    ), summary
    assert "::warning" not in result.stdout


def test_a_package_the_pr_adds_is_not_importable_without_the_fix(
    repo: Path, tmp_path: Path
) -> None:
    # B2: the head's editable install (here: the developer's tree, at the end of
    # sys.path like site-packages) would serve a package the PR adds to the run
    # without the fix, and its test would read "unexpected pass".
    (repo / "newpkg").mkdir()
    (repo / "newpkg" / "__init__.py").write_text("def f():\n    return 1\n")
    (repo / "tests" / "test_newpkg.py").write_text(
        "from newpkg import f\n\n\ndef test_f():\n    assert f() == 1\n"
    )
    commit_all(repo, "feat: newpkg")
    wrapper = tmp_path / "python"
    wrapper.write_text(
        "#!/usr/bin/env bash\n"
        f'export PYTHONPATH="${{PYTHONPATH:+$PYTHONPATH:}}{repo}"\n'
        f'exec "{sys.executable}" "$@"\n'
    )
    wrapper.chmod(0o755)
    result, summary = run_vr(repo, tmp_path, "--python", str(wrapper))
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "verify-red: 0 red · 1 import-only · 0 unexpected pass · 0 n/a" in summary, summary


def test_a_package_the_pr_adds_that_imports_from_elsewhere_is_refused(
    repo: Path, tmp_path: Path
) -> None:
    # B2 and #189 together: if something ahead of the stub serves the new package
    # (another checkout on the path), the run is refused rather than trusted.
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "newpkg").mkdir(parents=True)
    (elsewhere / "newpkg" / "__init__.py").write_text("def f():\n    return 1\n")
    (repo / "newpkg").mkdir()
    (repo / "newpkg" / "__init__.py").write_text("def f():\n    return 1\n")
    (repo / "tests" / "test_newpkg.py").write_text(
        "from newpkg import f\n\n\ndef test_f():\n    assert f() == 1\n"
    )
    commit_all(repo, "feat: newpkg")
    wrapper = tmp_path / "python"
    wrapper.write_text(
        "#!/usr/bin/env bash\n"
        f'export PYTHONPATH="{elsewhere}${{PYTHONPATH:+:$PYTHONPATH}}"\n'
        f'exec "{sys.executable}" "$@"\n'
    )
    wrapper.chmod(0o755)
    result, _ = run_vr(repo, tmp_path, "--python", str(wrapper))
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert "newpkg resolves outside" in result.stderr, result.stderr


def test_both_runs_get_a_scratch_home_and_config(repo: Path, tmp_path: Path) -> None:
    # New tests run against buggy code: nothing may land in the developer's
    # HOME or Pyrite config (the 2026-09-23 incident class).
    home = tmp_path / "home"
    home.mkdir()
    (repo / "pyrite" / "__init__.py").write_text(FIXED)
    (repo / "tests" / "test_new.py").write_text(
        "import os\nfrom pathlib import Path\n\nfrom pyrite import add\n\n\n"
        "def test_new():\n"
        "    for var in ('HOME', 'PYRITE_CONFIG_DIR', 'PYRITE_DATA_DIR', 'XDG_CONFIG_HOME',\n"
        "                'XDG_DATA_HOME', 'XDG_CACHE_HOME', 'XDG_STATE_HOME'):\n"
        "        (Path(os.environ[var]) / f'written-{var}').write_text('x')\n"
        "    (Path.home() / 'written-home').write_text('x')\n"
        "    assert add(2, 2) == 4\n"
    )
    commit_all(repo)
    env = _env(
        tmp_path,
        {
            "HOME": str(home),
            "PYRITE_CONFIG_DIR": str(home / "cfg"),
            "PYRITE_DATA_DIR": str(home / "data"),
            "XDG_CONFIG_HOME": str(home / "xdg"),
        },
    )
    result, summary = run_vr(repo, tmp_path, env=env)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "verify-red: 1 red · 0 import-only · 0 unexpected pass · 0 n/a" in summary, summary
    assert list(home.rglob("*")) == []


def test_the_scratch_env_keeps_the_model_cache(vr, tmp_path: Path, monkeypatch) -> None:
    # Models are a read-mostly download cache, not config: a scratch HOME must not
    # turn every embedding test into "n/a" by hiding them.
    monkeypatch.setenv("HF_HOME", "/models")
    env = vr.scratch_env(tmp_path)
    assert env["HF_HOME"] == "/models"
    assert env["HOME"] == env["PYRITE_CONFIG_DIR"] == env["PYRITE_DATA_DIR"] == str(tmp_path)


@pytest.mark.control(
    reason="dev's in-place runner never pruned; this pins the scope of the new one"
)
def test_only_its_own_stale_trees_are_pruned(repo: Path, tmp_path: Path) -> None:
    # Someone else's worktree whose directory is missing (an unmounted disk, a
    # moved checkout) is not this tool's to forget.
    other = tmp_path / "elsewhere" / "wt"
    git(repo, "worktree", "add", "-q", "--detach", str(other))
    subprocess.run(["rm", "-rf", str(other)], check=True)
    (repo / "pyrite" / "__init__.py").write_text(FIXED)
    (repo / "tests" / "test_new.py").write_text(
        "from pyrite import add\n\n\ndef test_new():\n    assert add(2, 2) == 4\n"
    )
    commit_all(repo)
    result, _ = run_vr(repo, tmp_path)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert any(str(other.name) in w for w in worktrees(repo)), worktrees(repo)


def test_a_run_with_the_fix_that_records_nothing_is_an_infra_error(
    repo: Path, tmp_path: Path
) -> None:
    # A conftest or plugin that breaks pytest outright is the check failing,
    # not every test being "n/a": exit 2, and say why where the table would be.
    (repo / "pyrite" / "__init__.py").write_text(FIXED)
    (repo / "tests" / "conftest.py").write_text("raise RuntimeError('boom in conftest')\n")
    (repo / "tests" / "test_new.py").write_text(
        "from pyrite import add\n\n\ndef test_new():\n    assert add(2, 2) == 4\n"
    )
    commit_all(repo)
    result, summary = run_vr(repo, tmp_path)
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert "boom in conftest" in summary, summary
    assert "boom in conftest" in result.stderr


def test_no_code_change_is_nothing_to_verify(repo: Path, tmp_path: Path) -> None:
    (repo / "tests" / "test_add.py").write_text(PR_TESTS.replace("make", "add"))
    commit_all(repo, "test: more tests")
    result, summary = run_vr(repo, tmp_path, "--json", str(tmp_path / "e.json"))
    assert result.returncode == 0, result.stderr
    assert "verify-red: nothing to verify" in summary
    assert "::warning" not in result.stdout
    assert json.loads((tmp_path / "e.json").read_text())["verify_red"] is None


def test_a_code_change_with_no_new_test_is_a_warning(repo: Path, tmp_path: Path) -> None:
    (repo / "pyrite" / "__init__.py").write_text(FIXED)
    commit_all(repo, "refactor")
    result, summary = run_vr(repo, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "verify-red: nothing to verify" in summary
    warnings = [ln for ln in result.stdout.splitlines() if ln.startswith("::warning")]
    assert len(warnings) == 1 and "no new or edited test" in warnings[0], result.stdout


@pytest.mark.control(reason="the #189 check is kept: the old runner refused this too")
def test_a_package_that_resolves_outside_the_tree_is_refused(repo: Path, tmp_path: Path) -> None:
    # #189: an interpreter whose `import pyrite` lands in another checkout (a
    # symlinked .venv's editable install) would verify the wrong code.
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "pyrite").mkdir(parents=True)
    (elsewhere / "pyrite" / "__init__.py").write_text(FIXED)
    wrapper = tmp_path / "python"
    wrapper.write_text(
        "#!/usr/bin/env bash\n"
        f'export PYTHONPATH="{elsewhere}${{PYTHONPATH:+:$PYTHONPATH}}"\n'
        # No implicit cwd entry: the real bug had no local path to the tree at all.
        "export PYTHONSAFEPATH=1\n"
        f'exec "{sys.executable}" "$@"\n'
    )
    wrapper.chmod(0o755)
    (repo / "pyrite" / "__init__.py").write_text(FIXED)
    (repo / "tests" / "test_add.py").write_text(PR_TESTS)
    commit_all(repo)
    before = snapshot(repo)
    result, _ = run_vr(repo, tmp_path, "--python", str(wrapper))
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert "resolves outside" in result.stderr, result.stderr
    assert snapshot(repo) == before
    assert len(worktrees(repo)) == 1


PINNED_MTIME = 1_700_000_000
SAME_SIZE_FIX = BROKEN.replace("a - b", "a + b")


def test_a_same_size_fix_is_not_served_stale_bytecode(repo: Path, tmp_path: Path) -> None:
    # CPython trusts a .pyc whose source has the same mtime (whole seconds) and
    # size. The run without the fix imports the merge-base source; the fix that
    # replaces it is the same size. A post-checkout hook pins every checked-out
    # file's mtime and the overlay keeps the developer's (pinned too), so without
    # PYTHONDONTWRITEBYTECODE the run with the fix would import the broken code.
    hook = repo / ".git" / "hooks" / "post-checkout"
    hook.write_text(
        "#!/usr/bin/env bash\n"
        f'exec "{sys.executable}" -c "import os, pathlib\n'
        "for f in pathlib.Path('.').rglob('*.py'):\n"
        f"    '.git' in f.parts or os.utime(f, ({PINNED_MTIME}, {PINNED_MTIME}))\"\n"
    )
    hook.chmod(0o755)
    (repo / "pyrite" / "__init__.py").write_text(SAME_SIZE_FIX)
    (repo / "tests" / "test_new.py").write_text(
        "from pyrite import add\n\n\ndef test_new():\n    assert add(2, 2) == 4\n"
    )
    commit_all(repo)
    os.utime(repo / "pyrite" / "__init__.py", (PINNED_MTIME, PINNED_MTIME))
    result, summary = run_vr(repo, tmp_path)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "verify-red: 1 red · 0 import-only · 0 unexpected pass · 0 n/a" in summary, summary


def test_a_hung_run_keeps_what_it_recorded(repo: Path, tmp_path: Path) -> None:
    (repo / "pyrite" / "__init__.py").write_text(FIXED)
    (repo / "tests" / "test_new.py").write_text(
        "import time\n\nfrom pyrite import add\n\n\n"
        "def test_a_real():\n    assert add(2, 2) == 4\n\n\n"
        "def test_b_hangs_without_the_fix():\n"
        "    if add(2, 2) != 4:\n        time.sleep(60)\n"
    )
    commit_all(repo)
    before = snapshot(repo)
    result, summary = run_vr(repo, tmp_path, "--timeout", "8")
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "verify-red: 1 red · 0 import-only · 0 unexpected pass · 1 n/a" in summary, summary
    row = [ln for ln in summary.splitlines() if "test_b_hangs" in ln]
    assert row and "timed out" in row[0], summary
    assert snapshot(repo) == before


def test_a_killed_run_leaves_the_developers_tree_alone(repo: Path, tmp_path: Path) -> None:
    # SIGKILL: no handler, no finally. The developer's tree was never touched, so
    # there is nothing to restore; the stale throwaway tree is only a registered
    # worktree, and the next run prunes it once its directory is gone.
    pidfile, flag = tmp_path / "hung.pid", tmp_path / "hang"
    flag.write_text("1")
    (repo / "pyrite" / "__init__.py").write_text(FIXED)
    (repo / "tests" / "test_new.py").write_text(
        "import os\nimport time\n\nfrom pyrite import add\n\n\n"
        "def test_new():\n"
        f"    if add(2, 2) != 4 and os.path.exists({str(flag)!r}):  # hang without the fix\n"
        f"        open({str(pidfile)!r}, 'w').write(str(os.getpid()))\n"
        "        time.sleep(120)\n"
        "    assert add(2, 2) == 4\n"
    )
    commit_all(repo)
    (repo / "tests" / "test_wip.py").write_text("def test_wip():\n    pass\n")  # untracked
    before = snapshot(repo)

    env = _env(tmp_path)
    driver = subprocess.Popen(
        [sys.executable, str(SCRIPT), "--base", "dev", "--python", sys.executable],
        cwd=repo,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    hung = None
    try:
        deadline = time.monotonic() + 60
        while not (pidfile.exists() and pidfile.read_text().strip()):
            assert driver.poll() is None, "the driver exited before the run hung"
            assert time.monotonic() < deadline, "the run never started"
            time.sleep(0.1)
        hung = int(pidfile.read_text())
        driver.send_signal(signal.SIGKILL)
        driver.wait(timeout=30)
    finally:
        if driver.poll() is None:
            driver.kill()
        if hung:
            try:
                os.kill(hung, signal.SIGKILL)
            except ProcessLookupError:
                pass

    assert snapshot(repo) == before
    stale = list((tmp_path / "tmpdir").glob("verify-red-*"))
    assert len(stale) == 1 and len(worktrees(repo)) == 2, (stale, worktrees(repo))

    # The OS clears $TMPDIR; the next run drops the registration left behind.
    subprocess.run(["rm", "-rf", str(stale[0])], check=True)
    flag.unlink()
    result, summary = run_vr(repo, tmp_path, env=env)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "verify-red: 1 red" in summary, summary
    assert len(worktrees(repo)) == 1, worktrees(repo)
    assert snapshot(repo) == before


def test_diff_coverage_is_carried_into_the_evidence(repo: Path, tmp_path: Path) -> None:
    (repo / "pyrite" / "__init__.py").write_text(FIXED)
    (repo / "tests" / "test_new.py").write_text(
        "from pyrite import add\n\n\ndef test_new():\n    assert add(2, 2) == 4\n"
    )
    commit_all(repo)
    dc = tmp_path / "diff-cover.json"
    dc.write_text(
        json.dumps({"total_percent_covered": 75, "total_num_lines": 20, "total_num_violations": 5})
    )
    out = tmp_path / "evidence.json"
    result, summary = run_vr(
        repo, tmp_path, "--json", str(out), "--diff-cover-json", str(dc), "--pr", "42"
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    ev = json.loads(out.read_text())
    assert ev["pr"] == 42
    assert ev["diff_coverage"] == {"percent": 75, "lines": 20, "uncovered": 5}
    assert ev["verify_red"]["red"] == 1
    assert "diff coverage: 75% of 20 changed lines" in summary


def test_diff_coverage_of_no_lines_is_not_100_percent(repo: Path, tmp_path: Path) -> None:
    # B5: diff-cover reports 100% when no measured line changed. That is no data.
    (repo / "pyrite" / "__init__.py").write_text(FIXED)
    (repo / "tests" / "test_new.py").write_text(
        "from pyrite import add\n\n\ndef test_new():\n    assert add(2, 2) == 4\n"
    )
    commit_all(repo)
    dc = tmp_path / "diff-cover.json"
    dc.write_text(
        json.dumps({"total_percent_covered": 100, "total_num_lines": 0, "total_num_violations": 0})
    )
    out = tmp_path / "evidence.json"
    result, summary = run_vr(
        repo, tmp_path, "--json", str(out), "--diff-cover-json", str(dc), "--head-sha", "abc123"
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    ev = json.loads(out.read_text())
    assert ev["diff_coverage"] == {"percent": None, "lines": 0, "uncovered": 0}
    assert "diff coverage: n/a (no changed line is measured)" in summary
    assert "100%" not in summary
    # B5: the PR's head is what joins to the PR; the merge commit CI checks out is kept apart.
    assert ev["head"] == "abc123"
    assert ev["merge_commit"] == git(repo, "rev-parse", "HEAD")


# ---------------------------------------------------------------------------
# The local entry point
# ---------------------------------------------------------------------------


def test_verify_red_sh_is_the_same_code_path(repo: Path, tmp_path: Path) -> None:
    (repo / "pyrite" / "__init__.py").write_text(FIXED)
    (repo / "tests" / "test_new.py").write_text(
        "from pyrite import add\n\n\ndef test_new():\n    assert add(2, 2) == 4\n"
    )
    commit_all(repo)
    env = _env(tmp_path, {"VERIFY_RED_BASE": "dev", "VERIFY_RED_PYTHON": sys.executable})
    env.pop("GITHUB_STEP_SUMMARY")
    env.pop("GITHUB_ACTIONS")
    result = subprocess.run(
        ["bash", str(REPO / "scripts" / "verify-red.sh")],
        cwd=repo / "tests",  # from a subdirectory
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "verify-red: 1 red · 0 import-only · 0 unexpected pass · 0 n/a" in result.stdout
    assert "::warning" not in result.stdout  # annotations are for CI only


# ---------------------------------------------------------------------------
# The CI wiring
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ci() -> dict:
    return yaml.safe_load(CI_PATH.read_text())


def _runs(job: dict) -> str:
    return "\n".join(s.get("run", "") for s in job["steps"])


class TestTheVerifyRedJob:
    @pytest.mark.control(reason="kept from the old job")
    def test_it_runs_on_pull_request_only(self, ci):
        condition = ci["jobs"]["verify-red"]["if"]
        assert "github.event_name == 'pull_request'" in condition, condition
        assert "||" not in condition, f"another event could reach it: {condition}"

    @pytest.mark.control(reason="kept from the old job")
    def test_it_is_not_a_gate(self, ci):
        assert ci["jobs"]["gate"]["needs"] == ["changes", "kb", "test", "frontend"]

    @pytest.mark.control(reason="kept from the old job")
    def test_it_is_read_only(self, ci):
        assert ci["jobs"]["verify-red"].get("permissions") == {"contents": "read"}

    def test_it_is_bounded(self, ci):
        job = ci["jobs"]["verify-red"]
        m = re.search(r"--timeout (\d+)", _runs(job))
        assert m, "the job must pass --timeout"
        setup = 5 * 60  # checkout, Python, uv, the installs
        assert 2 * int(m.group(1)) + setup <= job["timeout-minutes"] * 60

    @pytest.mark.control(reason="kept from the old job")
    def test_it_runs_the_script_against_the_merge_base_with_the_pr_base(self, ci):
        job = ci["jobs"]["verify-red"]
        run = _runs(job)
        assert "scripts/verify_red_ci.py" in run
        assert "github.event.pull_request.base.sha" in run
        checkout = next(s for s in job["steps"] if s.get("uses", "").startswith("actions/checkout"))
        assert checkout.get("with", {}).get("fetch-depth") == 0, "the merge base must be in history"

    def test_it_publishes_the_evidence_artifact(self, ci):
        job = ci["jobs"]["verify-red"]
        assert "--json test-evidence.json" in _runs(job)
        # The checkout is the merge commit; the evidence must join to the PR's head.
        assert '--head-sha "${{ github.event.pull_request.head.sha }}"' in _runs(job)
        upload = next(
            s for s in job["steps"] if s.get("uses", "").startswith("actions/upload-artifact")
        )
        assert upload["with"]["name"] == "test-evidence"
        assert upload["with"]["path"] == "test-evidence.json"

    def test_it_waits_for_diff_coverage_but_not_for_success(self, ci):
        job = ci["jobs"]["verify-red"]
        assert "test" in job["needs"]
        assert "!cancelled()" in job["if"], "a red suite must not hide the evidence"
        assert "--diff-cover-json" in _runs(job)


class TestDiffCoverage:
    def _test_job(self, ci) -> dict:
        return ci["jobs"]["test"]

    def test_the_312_pr_leg_measures_coverage_without_a_threshold(self, ci):
        run = _runs(self._test_job(ci))
        assert "--cov=pyrite" in run
        # [tool.coverage.report] fail_under would otherwise fail the suite (and gate).
        assert "--cov-fail-under=0" in run
        assert "--cov-report=xml" in run

    def test_only_the_312_pr_leg_pays_for_it(self, ci):
        step = next(s for s in self._test_job(ci)["steps"] if "--cov=pyrite" in s.get("run", ""))
        run = yaml.safe_dump(step, width=1000)
        assert "matrix.python-version == '3.12'" in run
        assert "github.event_name == 'pull_request'" in run

    def test_diff_cover_is_advisory(self, ci):
        steps = self._test_job(ci)["steps"]
        step = next(s for s in steps if "diff-cover" in s.get("run", ""))
        assert "--fail-under=80" in step["run"]
        assert "--compare-branch" in step["run"]
        assert "github.event.pull_request.base.sha" in step["run"]
        assert step.get("continue-on-error") is True
        assert "GITHUB_STEP_SUMMARY" in step["run"]

    def test_diff_cover_is_a_dev_dependency(self):
        import tomllib

        dev = tomllib.loads((REPO / "pyproject.toml").read_text())["project"][
            "optional-dependencies"
        ]["dev"]
        assert any(d.startswith("diff-cover") for d in dev), dev

    def test_the_control_marker_is_registered(self):
        import tomllib

        markers = tomllib.loads((REPO / "pyproject.toml").read_text())["tool"]["pytest"][
            "ini_options"
        ]["markers"]
        assert any(m.startswith("control(reason):") for m in markers), markers


# ---------------------------------------------------------------------------
# Who runs it: the worker once, CI always; the conductor reads CI.
# ---------------------------------------------------------------------------


def _words(path: Path) -> str:
    return " ".join(path.read_text().split())


def test_the_conductor_reads_the_ci_result_and_does_not_rerun_it():
    review = _words(REPO / ".claude" / "skills" / "pyrite-conductor" / "review.md")
    assert "read the `verify-red` job's summary line" in review
    assert "do not re-run it locally" in review
    # The job runs the PR's own copy: a PR that changes it cannot vouch for itself.
    assert "the PR itself touches `scripts/verify*red*` or the `verify-red` job" in review
    # ...and the manual check runs dev's copy from the PR's worktree, never the PR's own copy.
    assert "git show origin/dev:scripts/$f" in review
    assert "The manual check runs **dev's** copy, never the PR's" in review
    assert "0 red, or only import-only reds, goes back" in review


def test_the_worker_runs_it_once_and_pastes_the_line():
    dev = _words(REPO / ".claude" / "skills" / "pyrite-dev" / "SKILL.md")
    assert "scripts/verify-red.sh" in dev
    assert "paste its summary line" in dev
    assert "0 red, or only import-only reds, is not done" in dev
    assert '@pytest.mark.control(reason="...")' in dev


@pytest.mark.parametrize(
    "test_file",
    [
        # a module that skips itself (an optional dependency CI does not install)
        "import pytest\n\npytest.importorskip('no_such_module_here')\n\n"
        "from pyrite import add\n\n\ndef test_new():\n    assert add(2, 2) == 4\n",
        # a class pytest cannot collect (it has an __init__)
        "from pyrite import add\n\n\nclass TestNew:\n    def __init__(self):\n        pass\n\n"
        "    def test_new(self):\n        assert add(2, 2) == 4\n",
    ],
    ids=["module-skip", "uncollectable-class"],
)
def test_a_new_test_that_does_not_run_with_the_fix_is_no_claim_not_an_infra_error(
    repo: Path, tmp_path: Path, test_file: str
) -> None:
    # #393 delta cold read: pytest ran and collected the file, it just ran no
    # test from it. That is n/a, not "the check could not run" (exit 2).
    (repo / "pyrite" / "__init__.py").write_text(FIXED)
    (repo / "tests" / "test_new.py").write_text(test_file)
    commit_all(repo)
    result, summary = run_vr(repo, tmp_path)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "verify-red: 0 red" in summary, summary


def test_changed_helper_under_tests_is_the_fix(repo: Path, tmp_path: Path) -> None:
    (repo / "tests" / "helpers.py").write_text("def answer():\n    return 0\n")
    commit_all(repo, "base: helper")
    git(repo, "branch", "-f", "dev", "HEAD")
    (repo / "tests" / "helpers.py").write_text("def answer():\n    return 42\n")
    (repo / "tests" / "test_helper.py").write_text(
        "from helpers import answer\n\n\ndef test_answer():\n    assert answer() == 42\n"
    )
    commit_all(repo, "fix: test helper")
    result, summary = run_vr(repo, tmp_path)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "verify-red: 1 red \u00b7 0 import-only \u00b7 0 unexpected pass \u00b7 0 n/a" in summary
