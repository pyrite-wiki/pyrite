"""A refused claim release points callers at the supported recovery command."""

import json
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.exceptions import ValidationError
from pyrite.server.mcp_server import _refusal
from pyrite.services.task_service import TaskService
from pyrite.storage.database import PyriteDB
from pyrite.storage.repository import KBRepository


@pytest.fixture
def task_env(tmp_path):
    path = tmp_path / "tasks"
    path.mkdir()
    kb = KBConfig(name="tasks", path=path, kb_type="task")
    config = PyriteConfig(knowledge_bases=[kb], settings=Settings(index_path=tmp_path / "index.db"))
    db = PyriteDB(config.settings.index_path)
    db.register_kb(name="tasks", kb_type="task", path=str(path))
    service = TaskService(config, db)
    repo = KBRepository(kb)
    try:
        yield service, db, repo
    finally:
        db.close()


def _reach_status(service: TaskService, entry_id: str, target: str) -> None:
    if target == "open":
        return
    service.claim_task(entry_id, "tasks", "tester")
    if target == "claimed":
        return
    service.update_task(entry_id, "tasks", status="in_progress")
    if target == "in_progress":
        return
    service.update_task(entry_id, "tasks", status=target)


@pytest.mark.parametrize("status", ["in_progress", "blocked"])
@pytest.mark.parametrize("surface", ["service", "cli", "mcp"])
def test_refused_release_names_reset(task_env, status, surface):
    service, db, repo = task_env
    entry_id = service.create_task("tasks", f"Task in {status}")["entry_id"]
    _reach_status(service, entry_id, status)
    assert repo.load(entry_id).status == status

    if surface == "cli":
        with patch("pyrite.cli.task_commands._get_service", return_value=(service, db)):
            result = CliRunner().invoke(
                app, ["task", "update", entry_id, "-k", "tasks", "-s", "open", "--format", "json"]
            )
        assert result.exit_code != 0
        refusal = json.loads(result.stdout)
        assert "pyrite task reset" not in refusal.get("error", "")
        assert "pyrite task reset" in refusal.get("suggestion", "")
    else:
        with pytest.raises(ValidationError) as caught:
            service.update_task(entry_id, "tasks", status="open")
        assert "pyrite task reset" not in str(caught.value)
        assert caught.value.suggestion is not None
        assert "pyrite task reset" in caught.value.suggestion
        if surface == "mcp":
            refusal = _refusal(caught.value)
            assert "pyrite task reset" not in refusal["error"]
            assert "pyrite task reset" in refusal["suggestion"]

    assert repo.load(entry_id).status == status

    # Test that `task reset` succeeds from each hinted state
    reset_result = service.reset_task(entry_id, "tasks")
    assert reset_result["reset"] is True
    assert reset_result["status"] == "open"
    assert repo.load(entry_id).status == "open"


@pytest.mark.control(reason="Claimed task cannot be reset, so it must not get the reset hint")
def test_claimed_task_does_not_get_reset_hint(task_env):
    service, _, repo = task_env
    entry_id = service.create_task("tasks", "Task in claimed")["entry_id"]
    _reach_status(service, entry_id, "claimed")
    assert repo.load(entry_id).status == "claimed"

    with pytest.raises(ValidationError) as caught:
        service.update_task(entry_id, "tasks", status="open")
    assert "pyrite task reset" not in str(caught.value)
    assert caught.value.suggestion is None or "task reset" not in caught.value.suggestion


@pytest.mark.control(reason="An unclaimed task needs no recovery command")
def test_unclaimed_task_does_not_get_reset_hint(task_env):
    service, _, _ = task_env
    entry_id = service.create_task("tasks", "Unclaimed task")["entry_id"]
    res = service.update_task(entry_id, "tasks", status="open")
    assert res["status"] == "open"


@pytest.mark.control(reason="A different refused transition is not a claim release")
def test_unrelated_refusal_does_not_get_reset_hint(task_env):
    service, _, _ = task_env
    entry_id = service.create_task("tasks", "Unrelated refusal")["entry_id"]
    with pytest.raises(ValidationError) as caught:
        service.update_task(entry_id, "tasks", status="done")
    assert "task reset" not in str(caught.value)
    assert caught.value.suggestion is None
