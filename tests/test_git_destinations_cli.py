"""The CLI is held to `git_allowed_hosts` too: git reaches only allowed hosts.

Driven through `pyrite repo subscribe` in a real world (`tests/_world.py`):
the config is read from disk and no GitHub token is saved, so nothing behind
the command is replaced. Only git's own subprocess is watched, to prove the
refusal comes before git runs. The service-level cases are in
`test_git_destinations_are_allowed.py`.
"""

from __future__ import annotations

from unittest.mock import patch

from tests._world import build_world


def test_cli_subscribe_refuses_and_names_the_setting(tmp_path):
    world = build_world(tmp_path, rest=False)
    try:
        with patch("pyrite.services.git_service.subprocess.run") as run:
            result = world.cli("repo", "subscribe", "git@github.com:owner/repo.git")
    finally:
        world.close()
    assert result.exit_code != 0
    assert "git_allowed_hosts" in result.output
    run.assert_not_called()
