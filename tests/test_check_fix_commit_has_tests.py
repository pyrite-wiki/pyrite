"""Tests for scripts/check_fix_commit_has_tests.py -- ci-make-green-and-
load-bearing item 5: `fix:`-prefixed commits must touch tests/, mechanizing
Iron Law 1 (no production code without a failing test first) at the commit
boundary. Regression motivation: 40e7a39 shipped a fix with zero test
lines.

The `--range` tests below cover ci-parity-lint-extensions-and-enforce-the-
fix-needs-a-test-rule: the same rule, enforced in CI over a PR's whole
commit range (outside contributors' PRs never run the local commit-msg
hook), not just the single commit about to be made locally.
"""

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_fix_commit_has_tests.py"

sys.path.insert(0, str(SCRIPT.parent))

from check_fix_commit_has_tests import is_fix_commit, touches_tests  # noqa: E402


class TestIsFixCommit:
    def test_fix_prefix_is_a_fix_commit(self):
        assert is_fix_commit("fix: correct the thing\n\nBody here.") is True

    def test_feat_prefix_is_not_a_fix_commit(self):
        assert is_fix_commit("feat: add the thing") is False

    def test_docs_prefix_is_not_a_fix_commit(self):
        assert is_fix_commit("docs: fix the typo in README") is False

    def test_fix_must_be_at_start_of_subject(self):
        """'fix' appearing later in the subject doesn't count -- only a
        literal 'fix:' prefix on the subject line."""
        assert is_fix_commit("refactor: fix up naming (not a fix: commit)") is False

    def test_empty_message_is_not_a_fix_commit(self):
        assert is_fix_commit("") is False

    @pytest.mark.parametrize(
        "subject",
        [
            "fix(cli): correct the thing",
            "fix!: drop the old flag",
            "fix(api)!: rename the field",
            "fix(a/b-c): scope with punctuation\n\nBody.",
        ],
    )
    def test_scoped_and_breaking_fixes_are_fix_commits(self, subject):
        """#1074: a fifth of `fix` commits are `fix(scope):`; they were unchecked."""
        assert is_fix_commit(subject) is True

    @pytest.mark.parametrize("subject", ["Fix the crash", "Fix: the crash", "FIX: the crash"])
    def test_matching_is_case_sensitive_on_purpose(self, subject):
        """Conventional Commits types are lower case; 'Fix the crash' is prose, not
        a typed commit, and the gate must not guess at prose (#1074)."""
        assert is_fix_commit(subject) is False

    @pytest.mark.parametrize(
        "subject",
        [
            "fixup: squash me",
            "fix(cli) correct the thing",
            "fix correct the thing",
            "feat(fix): add a fixer",
            "refactor!: not a fix",
        ],
    )
    def test_near_misses_are_not_fix_commits(self, subject):
        assert is_fix_commit(subject) is False


class TestTouchesTests:
    def test_top_level_tests_dir_counts(self):
        assert touches_tests(["tests/test_foo.py", "pyrite/foo.py"]) is True

    def test_extension_tests_dir_counts(self):
        assert touches_tests(["extensions/software-kb/tests/test_bar.py"]) is True

    def test_no_test_files_is_false(self):
        assert touches_tests(["pyrite/foo.py", "README.md"]) is False

    def test_empty_list_is_false(self):
        assert touches_tests([]) is False

    def test_path_containing_tests_substring_but_not_dir_does_not_count(self):
        """A filename like 'latest_stats.py' contains the substring 'test'
        but is not under a tests/ directory -- must not false-positive."""
        assert touches_tests(["pyrite/latest_stats.py"]) is False

    # #159: the frontend's tests do not live under a tests/ directory -- vitest
    # files sit beside their source (`*.test.ts`) and Playwright specs under
    # web/e2e/ (`*.spec.ts`). A `fix:` that ships with one of those has a test;
    # with CI now enforcing this rule on PR ranges, not counting them would
    # fail every honest frontend fix.
    @pytest.mark.parametrize(
        "path",
        [
            "web/e2e/ports.test.ts",
            "web/src/lib/utils/sanitize.test.ts",
            "web/src/lib/stores/search.test.js",
            "web/e2e/graph.spec.ts",
        ],
    )
    def test_frontend_test_files_count(self, path):
        assert touches_tests([path, "web/src/lib/utils/sanitize.ts"]) is True

    @pytest.mark.parametrize(
        "path",
        [
            "web/src/lib/utils/sanitize.ts",  # source, not a test
            "web/e2e/fixtures.ts",  # e2e support file, not a spec
            "docs/writing.test.ts.md",  # not under web/, not a test file
            "pyrite/foo.test.ts",  # the frontend rule is scoped to web/
        ],
    )
    def test_frontend_non_test_files_do_not_count(self, path):
        assert touches_tests([path]) is False


class TestRangeMode:
    """`--range BASE HEAD` judges the pull request as a whole, the mode CI uses
    (a PR's commit range, not the single commit about to be made locally)."""

    def _git(self, repo, *args):
        import os

        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com",
        }
        return subprocess.run(
            ["git", *args],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
            env=env,
            timeout=120,
        )

    def _commit(self, repo, path: str, message: str, content: str = "x"):
        f = repo / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)
        self._git(repo, "add", path)
        self._git(repo, "commit", "-m", message)

    def _repo(self, tmp_path):
        repo = tmp_path / "scratch"
        repo.mkdir(parents=True, exist_ok=True)
        self._git(repo, "init", "-q")
        self._commit(repo, "README.md", "chore: seed", "seed")
        return repo

    @pytest.mark.parametrize(
        ("shape_id", "commits", "expected_code", "expected_in_stderr"),
        [
            (
                "test_then_fix",
                [
                    ("tests/test_thing.py", "test: add failing test"),
                    ("pyrite/thing.py", "fix: make the test pass"),
                ],
                0,
                None,
            ),
            (
                "fix_then_test",
                [
                    ("pyrite/thing.py", "fix: make the change"),
                    ("tests/test_thing.py", "test: cover the fix"),
                ],
                0,
                None,
            ),
            (
                "fix_with_its_test",
                [
                    ("tests/test_thing.py", "fix: fix with test in same commit"),
                ],
                0,
                None,
            ),
            (
                "fix_alone",
                [
                    ("pyrite/thing.py", "fix: fix without any test"),
                ],
                1,
                ["fix: fix without any test", "tests/"],
            ),
            (
                "two_fixes_and_one_test",
                [
                    ("pyrite/a.py", "fix: first fix"),
                    ("pyrite/b.py", "fix: second fix"),
                    ("tests/test_ab.py", "test: cover both fixes"),
                ],
                0,
                None,
            ),
            (
                "two_fixes_and_no_test",
                [
                    ("pyrite/a.py", "fix: first fix"),
                    ("pyrite/b.py", "fix: second fix"),
                ],
                1,
                ["fix: first fix", "fix: second fix", "tests/"],
            ),
            (
                "scoped_breaking_fix_with_test",
                [
                    ("tests/test_api.py", "test: add test"),
                    ("pyrite/api.py", "fix(api)!: breaking fix"),
                ],
                0,
                None,
            ),
            (
                "scoped_breaking_fix_alone",
                [
                    ("pyrite/api.py", "fix(api)!: breaking fix without test"),
                ],
                1,
                ["fix(api)!: breaking fix without test", "tests/"],
            ),
            (
                "docs_only_fix_prefix",
                [
                    ("docs/readme.md", "fix: typo in docs"),
                ],
                1,
                ["fix: typo in docs", "tests/"],
            ),
            (
                "docs_commit_without_fix_prefix",
                [
                    ("docs/readme.md", "docs: fix typo in docs"),
                ],
                0,
                None,
            ),
            (
                "frontend_test_path",
                [
                    ("web/src/button.ts", "fix(web): repair button"),
                    ("web/src/button.test.ts", "test(web): add button test"),
                ],
                0,
                None,
            ),
            (
                "no_fix_commit",
                [
                    ("pyrite/thing.py", "feat: add shiny feature"),
                    ("pyrite/other.py", "refactor: simplify helper"),
                ],
                0,
                None,
            ),
        ],
    )
    def test_range_shapes(self, tmp_path, shape_id, commits, expected_code, expected_in_stderr):
        repo = self._repo(tmp_path / shape_id)
        base = self._git(repo, "rev-parse", "HEAD").stdout.strip()
        for path, msg in commits:
            self._commit(repo, path, msg)
        head = self._git(repo, "rev-parse", "HEAD").stdout.strip()

        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--range", base, head],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == expected_code, result.stderr
        if expected_in_stderr:
            for snippet in expected_in_stderr:
                assert snippet in result.stderr

    def test_range_pr_merged_dev_into_itself_with_test(self, tmp_path):
        """Regime: a PR that merged dev into itself.

        In GitHub Actions, the merge ref HEAD has parent 1 = dev tip,
        parent 2 = PR branch tip. CI passes HEAD^1 HEAD to --range,
        so dev's commits are excluded from the range.
        """
        repo = self._repo(tmp_path)
        dev_start = self._git(repo, "rev-parse", "HEAD").stdout.strip()

        # Branch out for PR
        self._git(repo, "checkout", "-b", "feature")
        self._commit(repo, "tests/test_feature.py", "test: add test on branch")
        self._commit(repo, "pyrite/feature.py", "fix: fix bug on branch")

        # Advance dev independently
        self._git(repo, "checkout", "-b", "dev", dev_start)
        self._commit(repo, "pyrite/unrelated.py", "feat: upstream change on dev")
        dev_tip = self._git(repo, "rev-parse", "HEAD").stdout.strip()

        # PR branch merges dev into itself
        self._git(repo, "checkout", "feature")
        self._git(repo, "merge", "dev", "-m", "merge dev into feature")

        # Synthetic CI merge commit: parent 1 = dev_tip, parent 2 = feature
        self._git(repo, "checkout", "-b", "ci_merge", dev_tip)
        self._git(repo, "merge", "feature", "--no-ff", "-m", "Merge pull request")

        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--range", "HEAD^1", "HEAD"],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stderr

    def test_range_pr_merged_dev_into_itself_without_test(self, tmp_path):
        """Regime: a PR that merged dev into itself, but has no tests."""
        repo = self._repo(tmp_path)
        dev_start = self._git(repo, "rev-parse", "HEAD").stdout.strip()

        # Branch out for PR with fix but NO tests
        self._git(repo, "checkout", "-b", "feature")
        self._commit(repo, "pyrite/feature.py", "fix: fix bug without test")

        # Advance dev independently (even if dev had tests, dev commits are excluded)
        self._git(repo, "checkout", "-b", "dev", dev_start)
        self._commit(repo, "tests/test_unrelated.py", "test: upstream test on dev")
        dev_tip = self._git(repo, "rev-parse", "HEAD").stdout.strip()

        # PR branch merges dev into itself
        self._git(repo, "checkout", "feature")
        self._git(repo, "merge", "dev", "-m", "merge dev into feature")

        # Synthetic CI merge commit
        self._git(repo, "checkout", "-b", "ci_merge", dev_tip)
        self._git(repo, "merge", "feature", "--no-ff", "-m", "Merge pull request")

        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--range", "HEAD^1", "HEAD"],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 1
        assert "fix: fix bug without test" in result.stderr


class TestLocalCommitMsgHook:
    """The local commit-msg hook runs on a single commit being prepared."""

    def _git(self, repo, *args):
        import os

        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com",
        }
        return subprocess.run(
            ["git", *args],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
            env=env,
            timeout=60,
        )

    def test_local_hook_passes_when_fix_has_staged_test(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        self._git(repo, "init", "-q")
        test_file = repo / "tests" / "test_a.py"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("def test_ok(): pass\n")
        self._git(repo, "add", "tests/test_a.py")

        msg_file = tmp_path / "msg.txt"
        msg_file.write_text("fix: fix bug with test\n")

        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(msg_file)],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr

    def test_local_hook_fails_when_fix_has_no_staged_test(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        self._git(repo, "init", "-q")
        src_file = repo / "pyrite" / "a.py"
        src_file.parent.mkdir(parents=True)
        src_file.write_text("x = 1\n")
        self._git(repo, "add", "pyrite/a.py")

        msg_file = tmp_path / "msg.txt"
        msg_file.write_text("fix: fix bug without test\n")

        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(msg_file)],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 1
        assert "staged file" in result.stderr
        assert "fix:" in result.stderr

    def test_local_hook_passes_for_non_fix_commit(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        self._git(repo, "init", "-q")
        src_file = repo / "pyrite" / "a.py"
        src_file.parent.mkdir(parents=True)
        src_file.write_text("x = 1\n")
        self._git(repo, "add", "pyrite/a.py")

        msg_file = tmp_path / "msg.txt"
        msg_file.write_text("feat: add feature\n")

        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(msg_file)],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr


class TestTheErrorNamesEveryFixSpelling:
    def test_the_range_error_names_the_scoped_spelling(self, tmp_path):
        import os

        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@x",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@x",
        }

        def git(*a):
            subprocess.run(
                ["git", "-C", str(tmp_path), *a],
                check=True,
                capture_output=True,
                env=env,
                timeout=60,
            )

        git("init", "-q", "-b", "main")
        (tmp_path / "a.py").write_text("1\n")
        git("add", "a.py")
        git("commit", "-q", "-m", "base")
        (tmp_path / "a.py").write_text("2\n")
        git("add", "a.py")
        git("commit", "-q", "-m", "fix(core): no test")
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--range", "HEAD~1", "HEAD"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 1
        assert "fix(scope):" in result.stderr
        assert "fix(core): no test" in result.stderr
