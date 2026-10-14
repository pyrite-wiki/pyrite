"""Follow-ups to the affected-test selector's cold read (#370)."""

import os
import subprocess
import sys
from pathlib import Path
import pytest
from tests.test_test_affected import ta, _write, _git, SCRIPT


@pytest.fixture
def repo(tmp_path):
    _write(tmp_path, "pyrite/__init__.py", "")
    _write(tmp_path, "tests/test_words.py", "WORDS = 'agents'\n")
    _write(tmp_path, "tests/test_pointer.py", "PATH = 'worker.md'\n")
    _git(tmp_path, "init", "-q", "-b", "dev")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "initial")
    return tmp_path


def test_skill_content_does_not_select_tests_for_generic_directory_word(repo):
    selection = ta.select(repo, [".claude/agents/unreferenced.md"])
    assert "tests/test_words.py" not in selection.files
    assert "tests/test_pointer.py" in ta.select(repo, [".claude/agents/worker.md"]).files


@pytest.mark.parametrize("bad", ["PRE_COMMIT_FROM_REF", "PRE_COMMIT_TO_REF"])
def test_push_ref_failure_names_actual_range_and_ref_variables(repo, bad):
    head = _git(repo, "rev-parse", "HEAD").strip()
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"PYRITE_PUSH_FULL", "PRE_COMMIT_FROM_REF", "PRE_COMMIT_TO_REF"}
    }
    env.update(PRE_COMMIT_FROM_REF=head, PRE_COMMIT_TO_REF=head)
    env[bad] = "not-a-real-ref"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(repo), "--list"],
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 2
    assert "pushed range" in result.stderr
    assert "not-a-real-ref" in result.stderr
    assert "PRE_COMMIT_FROM_REF" in result.stderr
    assert "PRE_COMMIT_TO_REF" in result.stderr
    assert "fetch origin dev" not in result.stderr


def test_core_selection_includes_authentication_and_session_smokes():
    required = {
        "tests/test_auth_service.py::TestLogin::test_login_success",
        "tests/test_auth_service.py::TestLogin::test_login_wrong_password",
        "tests/test_auth_service.py::TestSessions::test_verify_session_valid",
    }
    assert required <= set(ta.select(SCRIPT.parent.parent, []).core)


def test_contributor_docs_state_push_range_and_multi_ref_limits():
    text = (SCRIPT.parent.parent / "CONTRIBUTING.md").read_text(encoding="utf-8")
    assert "three-dot" in text and "two-dot" in text
    assert "first non-deletion ref" in text
    assert "push one branch at a time" in text


@pytest.mark.parametrize(
    "path",
    [
        "kb/backlog/done/fast-commit-hooks-full-suite-at-pre-push-ci-is-the-gate.md",
        "kb/backlog/machine-wide-suite-lock-one-full-suite-at-a-time-xdist-workers-bounded-by-memory.md",
    ],
)
def test_old_hook_notes_point_to_current_selector(path):
    text = (SCRIPT.parent.parent / path).read_text(encoding="utf-8")
    assert "scripts/test-affected" in text
    assert "#356" in text
