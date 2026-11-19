"""Run the exact CI base-selection shell against real temporary git histories."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="CI bash contract runs on Linux")
SCRIPT = Path(__file__).parents[1] / "scripts/mypy_base.sh"


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True, timeout=15).strip()


def test_event_selection_zero_fallback_and_fetch(tmp_path):
    remote = tmp_path / "remote"
    remote.mkdir()
    git(remote, "init", "-q")
    git(remote, "config", "user.name", "Synthetic")
    git(remote, "config", "user.email", "synthetic@example.invalid")
    git(remote, "commit", "--allow-empty", "-qm", "first")
    first = git(remote, "rev-parse", "HEAD")
    git(remote, "commit", "--allow-empty", "-qm", "second")
    second = git(remote, "rev-parse", "HEAD")
    local = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", "-q", "--depth=1", remote.as_uri(), str(local)], check=True, timeout=15
    )
    for event, before, queue, expected in [
        ("workflow_dispatch", first, first, ""),
        ("push", "0" * 40, "", ""),
        ("push", "--not-a-sha", "", ""),
        ("push", "f" * 40, "", ""),
        ("push", first, "", first),  # shallow clone must fetch the base
        ("merge_group", "", second, second),
    ]:
        env = dict(os.environ, EVENT=event, PUSH_BEFORE=before, QUEUE_BASE=queue)
        result = subprocess.run(
            ["bash", str(SCRIPT)], cwd=local, env=env, capture_output=True, text=True, timeout=20
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == expected
    # In a non-shallow merge-ref checkout, PRs select the first parent.
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=remote,
        env=dict(os.environ, EVENT="pull_request"),
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0 and result.stdout.strip() == first
