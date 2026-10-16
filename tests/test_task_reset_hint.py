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
def task(tmp_path):
    path = tmp_path / "tasks"
    path.mkdir()
    kb = KBConfig(name="tasks", path=path, kb_type="task")
    config = PyriteConfig(knowledge_bases=[kb], settings=Settings(index_path=tmp_path / "index.db"))
    db = PyriteDB(config.settings.index_path)
    db.register_kb(name="tasks", kb_type="task", path=str(path))
    service = TaskService(config, db)
    entry_id = service.create_task("tasks", "Recover a stale claim")["entry_id"]
    repo = KBRepository(kb)
    try:
        yield service, db, repo, entry_id
    finally:
        db.close()


@pytest.mark.parametrize("status", ["claimed", "in_progress", "blocked"])
@pytest.mark.parametrize("surface", ["service", "cli", "mcp"])
def test_refused_release_names_reset(task, status, surface):
    service, db, repo, entry_id = task
    entry = repo.load(entry_id)
    entry.status = status
    repo.save(entry)
    if surface == "cli":
        with patch("pyrite.cli.task_commands._get_service", return_value=(service, db)):
            result = CliRunner().invoke(
                app, ["task", "update", entry_id, "-k", "tasks", "-s", "open", "-f", "json"]
            )
        assert result.exit_code != 0
        refusal = json.loads(result.stdout)
    else:
        with pytest.raises(ValidationError) as caught:
            service.update_task(entry_id, "tasks", status="open")
        refusal = (
            _refusal(caught.value)
            if surface == "mcp"
            else {"error": str(caught.value), "suggestion": caught.value.suggestion}
        )
    assert "pyrite task reset" in refusal["error"]
    assert "pyrite task reset" in refusal["suggestion"]
    assert repo.load(entry_id).status == status


@pytest.mark.control(reason="An unclaimed task needs no recovery command")
def test_unclaimed_task_does_not_get_reset_hint(task):
    service, _, _, entry_id = task
    assert service.update_task(entry_id, "tasks", status="open")["status"] == "open"


@pytest.mark.control(reason="A different refused transition is not a claim release")
def test_unrelated_refusal_does_not_get_reset_hint(task):
    service, _, _, entry_id = task
    with pytest.raises(ValidationError) as caught:
        service.update_task(entry_id, "tasks", status="done")
    assert "task reset" not in str(caught.value)
    assert caught.value.suggestion is None
