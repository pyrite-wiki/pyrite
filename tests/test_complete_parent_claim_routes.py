"""Every task claim/write route uses the derived-completion refusal."""

import json

import pytest
from typer.testing import CliRunner

from pyrite.config import save_config
from pyrite.cli import app
from pyrite.exceptions import ValidationError
from pyrite.services.kb_service import KBService
from pyrite.services.task_service import TaskService
from pyrite.storage.database import PyriteDB
from tests._world import build_world
from tests.test_derived_task_completion import _build, _mcp

ROUTES = [
    "claim",
    "update",
    "generic_update",
    "operation_update",
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
        save_config(config)
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
    if route.startswith("rest_"):
        world = build_world(config.settings.index_path.parent / "rest-world", config=config)
        try:
            response = (
                world.client.post(
                    "/api/tasks/parent/claim", params={"kb": "work"}, json={"assignee": "worker"}
                )
                if route == "rest_claim"
                else world.client.patch(
                    "/api/entries/parent",
                    json={"kb": "work", "field": "status", "value": "claimed"},
                )
            )
            assert response.status_code in (200, 400), response.text
            result = response.json()
            return result.get("detail", result)
        finally:
            world.close()
    db = PyriteDB(config.settings.index_path)
    try:
        svc = TaskService(config, db)
        try:
            if route == "claim":
                return svc.claim_task("parent", "work", "worker")
            if route == "update":
                return svc.update_task("parent", "work", status="claimed")
            if route == "operation_update":
                from pyrite.storage.file_operations import Set

                written = svc.kb_svc.apply_operations("parent", "work", [Set("status", "claimed")])
                return {"updated": True, "status": written.entry.status}
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
    assert "pyrite task update parent -k work -s done" in message, refusal
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


@pytest.mark.parametrize("status", ["open", "in_progress"])
def test_explicit_done_is_allowed(tmp_path, status):
    config, _ = _build(tmp_path, {"parent": (status, ""), "child": ("done", "parent")})
    db = PyriteDB(config.settings.index_path)
    try:
        assert (
            TaskService(config, db).update_task("parent", "work", status="done")["status"] == "done"
        )
    finally:
        db.close()


@pytest.mark.control(reason="An unfinished parent cannot skip the task workflow")
def test_open_parent_with_unresolved_child_cannot_skip_to_done(tmp_path):
    config, path = _build(tmp_path, {"parent": ("open", ""), "child": ("open", "parent")})
    before = (path / "tasks/parent.md").read_bytes()
    db = PyriteDB(config.settings.index_path)
    try:
        with pytest.raises(ValidationError, match="Cannot move task"):
            TaskService(config, db).update_task("parent", "work", status="done")
        assert (path / "tasks/parent.md").read_bytes() == before
    finally:
        db.close()


@pytest.mark.parametrize(
    ("field", "value"), [("title", "Updated parent title"), ("body", "Updated notes.")]
)
@pytest.mark.parametrize("operations", [False, True])
def test_unrelated_edit_of_claimed_complete_parent_is_allowed(tmp_path, field, value, operations):
    config, path = _build(tmp_path, {"parent": ("claimed", ""), "child": ("done", "parent")})
    db = PyriteDB(config.settings.index_path)
    try:
        svc = KBService(config, db)
        if operations:
            from pyrite.storage.file_operations import ReplaceBody, Set

            op = ReplaceBody(value) if field == "body" else Set(field, value)
            written = svc.apply_operations("parent", "work", [op])
        else:
            written = svc.update("parent", "work", {field: value})
        assert written.changed
        assert written.entry.status == "claimed"
        assert getattr(written.entry, field).strip() == value
        stored = (path / "tasks/parent.md").read_text(encoding="utf-8")
        assert value in stored
        assert db.get_entry("parent", "work")["status"] == "claimed"
    finally:
        db.close()
