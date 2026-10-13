"""Every task claim/write route uses the derived-completion refusal."""

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.exceptions import ValidationError
from pyrite.server import api
from pyrite.server.api import create_app
from pyrite.services.kb_service import KBService
from pyrite.services.task_service import TaskService
from pyrite.storage.database import PyriteDB
from tests.test_derived_task_completion import _build, _mcp

ROUTES = [
    "claim",
    "update",
    "generic_update",
    "cli_claim",
    "cli_update",
    "mcp_claim",
    "mcp_update",
    "rest_claim",
    "rest_patch",
    "software_cas",
]


def request_claim(config, route):
    if route.startswith("cli_"):
        command = (
            ["task", "claim", "parent", "-k", "work", "-a", "worker", "-f", "json"]
            if route == "cli_claim"
            else ["task", "update", "parent", "-k", "work", "-s", "claimed", "-f", "json"]
        )
        with patch("pyrite.cli.context.load_config", return_value=config):
            result = CliRunner().invoke(app, command)
        return json.loads(result.stdout)
    if route.startswith("mcp_"):
        tool, args = (
            ("task_claim", {"task_id": "parent", "kb_name": "work", "assignee": "worker"})
            if route == "mcp_claim"
            else (
                "kb_update",
                {"entry_id": "parent", "kb_name": "work", "status": "claimed"},
            )
        )
        return _mcp(config, tool, args, tier="write")
    db = PyriteDB(config.settings.index_path)
    try:
        if route.startswith("rest_"):
            application = create_app(config=config)
            application.dependency_overrides[api.get_db] = lambda: db
            client = TestClient(application)
            response = (
                client.post(
                    "/api/tasks/parent/claim", params={"kb": "work"}, json={"assignee": "worker"}
                )
                if route == "rest_claim"
                else client.patch(
                    "/api/entries/parent",
                    json={"kb": "work", "field": "status", "value": "claimed"},
                )
            )
            assert response.status_code in (200, 400), response.text
            result = response.json()
            return result.get("detail", result)
        svc = TaskService(config, db)
        try:
            if route == "claim":
                return svc.claim_task("parent", "work", "worker")
            if route == "update":
                return svc.update_task("parent", "work", status="claimed")
            if route == "generic_update":
                written = svc.kb_svc.update("parent", "work", {"status": "claimed"})
                return {"updated": True, "status": written.entry.status}
            # Software transitions delegate to this exact CAS. Their public
            # sw_transition tool accepts only backlog_item, never task.
            return svc.kb_svc.claim_entry("parent", "work", "worker", to_status="claimed")
        except ValidationError as error:
            return {"error": str(error)}
    finally:
        db.close()


@pytest.mark.parametrize("route", ROUTES)
def test_complete_parent_refused_then_claimable_after_open_child(tmp_path, route):
    config, path = _build(tmp_path, {"parent": ("open", ""), "child": ("done", "parent")})
    config.settings.auto_embed = False
    before = (path / "tasks/parent.md").read_bytes()
    refusal = request_claim(config, route)
    message = refusal.get("error", refusal.get("message", ""))
    assert "counts as done" in message, refusal
    assert "pyrite task create" in message, refusal
    assert "--parent parent" in message, refusal
    assert (path / "tasks/parent.md").read_bytes() == before
    db = PyriteDB(config.settings.index_path)
    try:
        TaskService(config, db).create_task("work", "New work", parent="parent")
    finally:
        db.close()
    success = request_claim(config, route)
    assert "error" not in success and "message" not in success, success
    db = PyriteDB(config.settings.index_path)
    try:
        assert db.get_entry("parent", "work")["status"] == "claimed"
    finally:
        db.close()


@pytest.mark.control(reason="Explicit completion is not a claim")
def test_explicit_done_is_allowed(tmp_path):
    config, _ = _build(tmp_path, {"parent": ("in_progress", ""), "child": ("done", "parent")})
    db = PyriteDB(config.settings.index_path)
    try:
        assert (
            TaskService(config, db).update_task("parent", "work", status="done")["status"] == "done"
        )
    finally:
        db.close()
