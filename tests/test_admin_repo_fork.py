from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from pyrite import admin_cli
from pyrite.config import PyriteConfig, Settings
from pyrite.services.git_service import GitService
from pyrite.services.repo_service import RepoService


@pytest.mark.parametrize("success", [True, False])
def test_admin_fork_runs_service_and_reports_outcome(tmp_path, monkeypatch, success):
    config = PyriteConfig(settings=Settings(index_path=tmp_path / "index.db"))
    monkeypatch.setattr(admin_cli, "load_config", lambda: config)
    monkeypatch.setattr(RepoService, "_get_token", lambda self: "fake-token")
    monkeypatch.setattr("pyrite.services.repo_service.check_config_save", lambda config: None)
    fork = Mock(
        return_value=(
            success,
            {
                "clone_url": "https://github.com/test/project.git",
                "full_name": "test/project",
                "error": "fork refused",
            },
        )
    )
    monkeypatch.setattr(GitService, "fork_repo", fork)
    clone = Mock(return_value={"success": True, "repo": "test/project", "path": str(tmp_path)})
    monkeypatch.setattr(RepoService, "_clone_and_register", clone)
    remote = Mock()
    monkeypatch.setattr(GitService, "add_remote", remote)

    result = CliRunner().invoke(
        admin_cli.app, ["repo", "fork", "https://github.com/upstream/project"]
    )

    fork.assert_called_once_with("upstream", "project", "fake-token")
    assert result.exit_code == (0 if success else 1), result.output
    if success:
        clone.assert_called_once_with(
            "https://github.com/test/project.git", "test/project", depth=None
        )
        remote.assert_called_once_with(tmp_path, "upstream", "https://github.com/upstream/project")
        assert "Forked and cloned: test/project" in result.output
    else:
        clone.assert_not_called()
        remote.assert_not_called()
        assert "fork refused" in result.output
        assert "Forked and cloned" not in result.output
