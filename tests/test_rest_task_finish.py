"""REST ``POST /api/tasks/{task_id}/finish`` with the same rules as ``task finish`` (#1745).

Covers every rule of ``TaskService.finish_task`` over REST, beside the CLI and
MCP cells in ``tests/test_task_finish_walk.py`` and
``tests/test_mcp_tools_record_the_caller.py``:

- the caller must hold the task (signed-in account or label on an operator key);
- an operator (operator API key / auth-disabled operator) may override with
  ``operator_override: true``, recorded as ``by: "operator", override: true``;
- a signed-in non-operator cannot use ``operator_override`` or spoof another
  caller's ``by``;
- every refusal writes nothing (the task file remains byte-identical); holder-rule
  and task validation refusals answer with the canonical REST error shape
  ``{"detail": {"code", "message", "retryable", ...}}``, while the KB write-tier
  refusal keeps the existing guard answer;
- the walk to ``done`` (every declared status in ``TASK_WORKFLOW``, direct edge
  vs ``walk: true``, evidence/reason, prior log items preserved, KB-declared
  strict/relaxed workflows) and the answer shape match CLI and MCP identically.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from pyrite.config import AuthConfig, KBConfig, PyriteConfig, Settings
from pyrite.models.task import TASK_WORKFLOW
from pyrite.server.api import create_app
from pyrite.services.auth_service import AuthService
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB
from tests._world import build_world
from tests.auth_seed import seed_and_sign_in

KB = "work"
WHO = "agent:w"
OPERATOR_KEY = "rest-finish-operator-key"
READ_KEY = "rest-finish-read-key"
REFUSED = None

EXPECTED_WALK: dict[str, list[str] | None] = {
    "open": REFUSED,
    "claimed": ["claimed", "in_progress", "done"],
    "in_progress": ["in_progress", "done"],
    "blocked": ["blocked", "in_progress", "done"],
    "review": ["review", "done"],
    "done": REFUSED,
    "failed": REFUSED,
    "cancelled": REFUSED,
}


def _task(entry_id: str, extra: str = "", body: str = "Work.\n") -> str:
    return f"---\ntype: task\nid: {entry_id}\ntitle: Task {entry_id}\n{extra}---\n\n{body}"


def _front(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8").split("---", 2)[1])


def _edges(front: dict) -> list[tuple[str, str]]:
    return [(i["from"], i["to"]) for i in front.get("status_change_log") or []]


def _pairs(states: list[str]) -> list[tuple[str, str]]:
    return list(zip(states, states[1:], strict=False))


def _assert_canonical_error(resp, expected_status: int, expected_code: str) -> dict:
    assert resp.status_code == expected_status, resp.text
    body = resp.json()
    assert "detail" in body and isinstance(body["detail"], dict), body
    detail = body["detail"]
    assert detail["code"] == expected_code, detail
    assert isinstance(detail["message"], str) and detail["message"], detail
    assert detail["retryable"] is False, detail
    return detail


@pytest.fixture
def world(tmp_path):
    w = build_world(tmp_path, kbs=[KBConfig(name=KB, path=tmp_path / "kb", kb_type="generic")])
    with w.pointed():
        yield w
    w.close()


def _post_finish(client: TestClient, task_id: str, payload: dict | None = None, **kwargs):
    return client.post(
        f"/api/tasks/{task_id}/finish",
        params={"kb": KB},
        json={} if payload is None else payload,
        **kwargs,
    )


# -- walk, evidence, and one shape across CLI, MCP and REST --------------------


@pytest.mark.parametrize("start", TASK_WORKFLOW["states"])
def test_rest_finish_walk_from_each_declared_status(world, start):
    path = world.add_entry(KB, _task("t", f"status: {start}\nassignee: {WHO}\n"))
    before = path.read_bytes()

    resp = _post_finish(world.client, "t", {"walk": True, "reason": "shipped", "by": WHO})

    expected = EXPECTED_WALK[start]
    if expected is REFUSED:
        _assert_canonical_error(resp, 422, "VALIDATION_FAILED")
        assert path.read_bytes() == before
        return
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["finished"] is True
    assert data["task_id"] == "t"
    assert data["status"] == "done"
    assert data["path"] == expected
    assert data["status_reason"] == "shipped"
    assert (data["by"], data["override"]) == (WHO, False)
    front = _front(path)
    assert front["status"] == "done"
    assert front["status_reason"] == "shipped"
    assert front["assignee"] == WHO
    assert _edges(front) == _pairs(expected)
    for item in front["status_change_log"]:
        assert item["by"] == WHO
        assert item["comment"] == "shipped"
        assert "override" not in item


def test_rest_finish_without_walk_takes_a_direct_edge(world):
    path = world.add_entry(KB, _task("t", f"status: in_progress\nassignee: {WHO}\n"))

    resp = _post_finish(world.client, "t", {"by": WHO})

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["path"] == ["in_progress", "done"]
    front = _front(path)
    assert front["status"] == "done"
    assert _edges(front) == [("in_progress", "done")]
    assert "status_reason" not in front


def test_rest_finish_without_walk_from_claimed_is_refused_and_names_the_path(world):
    path = world.add_entry(KB, _task("t", f"status: claimed\nassignee: {WHO}\n"))
    before = path.read_bytes()

    resp = _post_finish(world.client, "t", {"reason": "r", "by": WHO})

    detail = _assert_canonical_error(resp, 422, "VALIDATION_FAILED")
    assert "claimed → in_progress → done" in detail["message"]
    assert path.read_bytes() == before


def test_rest_finish_keeps_earlier_log_items(world):
    path = world.add_entry(
        KB,
        _task(
            "t",
            f"status: claimed\nassignee: {WHO}\nstatus_change_log:\n"
            "- {date: '2026-01-01T00:00:00Z', from: open, to: claimed, by: x, comment: c}\n",
        ),
    )

    resp = _post_finish(world.client, "t", {"walk": True, "reason": "r", "by": WHO})

    assert resp.status_code == 200, resp.text
    assert _edges(_front(path)) == [
        ("open", "claimed"),
        ("claimed", "in_progress"),
        ("in_progress", "done"),
    ]


def test_cli_mcp_and_rest_finish_return_one_shape(world):
    for tid in ("c", "m", "r"):
        world.add_entry(KB, _task(tid, f"status: claimed\nassignee: {WHO}\n"))

    cli_res = world.cli(
        "task",
        "finish",
        "c",
        "-k",
        KB,
        "--walk",
        "--reason",
        "done",
        "--by",
        WHO,
        "--format",
        "json",
    )
    assert cli_res.exit_code == 0, cli_res.stdout
    cli = json.loads(cli_res.stdout)
    cli.pop("outcome")

    mcp = world.mcp("write")._dispatch_tool(
        "task_finish", {"task_id": "m", "kb_name": KB, "walk": True, "reason": "done", "by": WHO}
    )

    rest_resp = _post_finish(world.client, "r", {"walk": True, "reason": "done", "by": WHO})
    assert rest_resp.status_code == 200, rest_resp.text
    rest = rest_resp.json()

    assert (cli.pop("task_id"), mcp.pop("task_id"), rest.pop("task_id")) == ("c", "m", "r")
    assert cli == mcp == rest


# -- holder rule and operator override (auth-disabled / operator surface) ------


@pytest.mark.parametrize(
    "payload",
    [{"walk": True, "by": "agent:b"}, {"walk": True}, {"walk": True, "by": ""}],
    ids=["another-agent", "no-caller", "empty-caller"],
)
def test_rest_non_holder_is_refused_and_file_is_byte_identical(world, payload):
    path = world.add_entry(KB, _task("t", "status: claimed\nassignee: agent:a\n"))
    before = path.read_bytes()

    resp = _post_finish(world.client, "t", payload)

    detail = _assert_canonical_error(resp, 403, "FORBIDDEN")
    assert "finish requires the caller to hold the task" in detail["message"]
    assert path.read_bytes() == before


def test_rest_operator_override_finishes_and_records_operator(world):
    path = world.add_entry(KB, _task("t", "status: claimed\nassignee: agent:a\n"))

    resp = _post_finish(world.client, "t", {"walk": True, "operator_override": True})

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert (data["by"], data["override"]) == ("operator", True)
    front = _front(path)
    assert front["status"] == "done"
    assert front["assignee"] == "agent:a"
    assert [(i["by"], i["override"]) for i in front["status_change_log"]] == [
        ("operator", True),
        ("operator", True),
    ]


# -- canonical error shapes for missing task, non-task, missing KB -------------


def test_rest_finish_missing_task_is_404_entry_not_found(world):
    resp = _post_finish(world.client, "no-such-task", {"by": WHO})
    _assert_canonical_error(resp, 404, "ENTRY_NOT_FOUND")


def test_rest_finish_non_task_entry_is_422_validation_failed(world):
    path = world.add_entry(KB, "---\ntype: note\nid: n1\ntitle: Note\n---\n\nBody.\n")
    before = path.read_bytes()

    resp = _post_finish(world.client, "n1", {"by": WHO})

    _assert_canonical_error(resp, 422, "VALIDATION_FAILED")
    assert path.read_bytes() == before


def test_rest_finish_missing_kb_is_404_kb_not_found(world):
    resp = world.client.post("/api/tasks/t/finish", params={"kb": "no-such-kb"}, json={"by": WHO})
    _assert_canonical_error(resp, 404, "KB_NOT_FOUND")


# -- multi-user auth: signed-in accounts, operator API key, read-only key ------


@pytest.fixture
def auth_env(tmp_path):
    kb_dir = tmp_path / "kb"
    kb_dir.mkdir()
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name=KB, path=kb_dir, kb_type="generic", default_role="none")],
        settings=Settings(
            index_path=tmp_path / "index.db",
            auth=AuthConfig(enabled=True),
            api_keys=[
                {
                    "name": "operator",
                    "key_hash": hashlib.sha256(OPERATOR_KEY.encode()).hexdigest(),
                    "role": "admin",
                },
                {
                    "name": "reader",
                    "key_hash": hashlib.sha256(READ_KEY.encode()).hexdigest(),
                    "role": "read",
                },
            ],
        ),
    )
    with PyriteDB(config.settings.index_path) as db:
        KBService(config, db).create_entry(KB, "t1", "Task 1", "task", "Work.", status="open")
    app = create_app(config=config)
    with TestClient(app) as client:
        seed_and_sign_in(client, "root", "password123", role="admin")
        with PyriteDB(config.settings.index_path) as db:
            auth = AuthService(db, config.settings.auth)
            root_id = next(u for u in auth.list_users() if u["username"] == "root")["id"]
            alice_id = auth.create_user("alice", "password123", role="write")["id"]
            bob_id = auth.create_user("bob", "password123", role="write")["id"]
            auth.grant_kb_permission(alice_id, KB, "write", root_id)
            auth.grant_kb_permission(bob_id, KB, "write", root_id)

        def _login(username: str) -> dict[str, str]:
            r = client.post("/auth/login", json={"username": username, "password": "password123"})
            assert r.status_code == 200, r.text
            return {"Cookie": f"pyrite_session={r.cookies['pyrite_session']}"}

        # Clear root cookie from client jar so each request uses explicit headers.
        client.cookies.clear()
        yield {
            "client": client,
            "kb_dir": kb_dir,
            "alice_headers": _login("alice"),
            "bob_headers": _login("bob"),
            "alice_key": f"user:{alice_id}",
            "bob_key": f"user:{bob_id}",
            "operator_headers": {"X-API-Key": OPERATOR_KEY},
            "read_headers": {"X-API-Key": READ_KEY},
        }


def _task_file(kb_dir: Path, task_id: str = "t1") -> Path:
    matches = list(kb_dir.rglob(f"{task_id}.md"))
    assert len(matches) == 1, matches
    return matches[0]


def test_signed_in_holder_finishes_without_by_and_records_account(auth_env):
    client = auth_env["client"]
    start = client.post(
        "/api/tasks/t1/start",
        params={"kb": KB},
        json={},
        headers=auth_env["alice_headers"],
    )
    assert start.status_code == 200 and start.json()["claimed"] is True, start.text

    resp = _post_finish(client, "t1", {"reason": "shipped"}, headers=auth_env["alice_headers"])

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["finished"] is True
    assert data["status"] == "done"
    assert (data["by"], data["override"]) == (auth_env["alice_key"], False)
    front = _front(_task_file(auth_env["kb_dir"]))
    assert [(i["to"], i["by"]) for i in front["status_change_log"]] == [
        ("claimed", auth_env["alice_key"]),
        ("in_progress", auth_env["alice_key"]),
        ("done", auth_env["alice_key"]),
    ]


def test_signed_in_non_holder_is_refused_and_file_is_byte_identical(auth_env):
    client = auth_env["client"]
    start = client.post(
        "/api/tasks/t1/start",
        params={"kb": KB},
        json={},
        headers=auth_env["alice_headers"],
    )
    assert start.status_code == 200 and start.json()["claimed"] is True, start.text
    task_path = _task_file(auth_env["kb_dir"])
    before = task_path.read_bytes()

    resp = _post_finish(client, "t1", {}, headers=auth_env["bob_headers"])

    detail = _assert_canonical_error(resp, 403, "FORBIDDEN")
    assert "finish requires the caller to hold the task" in detail["message"]
    assert task_path.read_bytes() == before


def test_signed_in_non_holder_spoofing_by_is_refused_and_file_is_byte_identical(auth_env):
    client = auth_env["client"]
    start = client.post(
        "/api/tasks/t1/start",
        params={"kb": KB},
        json={},
        headers=auth_env["alice_headers"],
    )
    assert start.status_code == 200 and start.json()["claimed"] is True, start.text
    task_path = _task_file(auth_env["kb_dir"])
    before = task_path.read_bytes()

    resp = _post_finish(
        client,
        "t1",
        {"by": auth_env["alice_key"]},
        headers=auth_env["bob_headers"],
    )

    _assert_canonical_error(resp, 403, "FORBIDDEN")
    assert task_path.read_bytes() == before


def test_signed_in_non_operator_cannot_use_operator_override(auth_env):
    client = auth_env["client"]
    start = client.post(
        "/api/tasks/t1/start",
        params={"kb": KB},
        json={},
        headers=auth_env["alice_headers"],
    )
    assert start.status_code == 200 and start.json()["claimed"] is True, start.text
    task_path = _task_file(auth_env["kb_dir"])
    before = task_path.read_bytes()

    resp = _post_finish(client, "t1", {"operator_override": True}, headers=auth_env["bob_headers"])

    _assert_canonical_error(resp, 403, "FORBIDDEN")
    assert task_path.read_bytes() == before


def test_operator_key_override_finishes_another_users_task_and_records_operator(auth_env):
    client = auth_env["client"]
    start = client.post(
        "/api/tasks/t1/start",
        params={"kb": KB},
        json={},
        headers=auth_env["alice_headers"],
    )
    assert start.status_code == 200 and start.json()["claimed"] is True, start.text
    task_path = _task_file(auth_env["kb_dir"])

    # Without override, operator key naming no caller is refused.
    before = task_path.read_bytes()
    refused = _post_finish(client, "t1", {}, headers=auth_env["operator_headers"])
    _assert_canonical_error(refused, 403, "FORBIDDEN")
    assert task_path.read_bytes() == before

    # With explicit operator_override, it succeeds and records operator.
    resp = _post_finish(
        client,
        "t1",
        {"operator_override": True, "reason": "admin close"},
        headers=auth_env["operator_headers"],
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["finished"] is True
    assert (data["by"], data["override"]) == ("operator", True)
    front = _front(task_path)
    last = front["status_change_log"][-1]
    assert (last["by"], last["override"], last["comment"]) == (
        "operator",
        True,
        "admin close",
    )


def test_read_only_key_cannot_finish_task(auth_env):
    client = auth_env["client"]
    start = client.post(
        "/api/tasks/t1/start",
        params={"kb": KB},
        json={},
        headers=auth_env["alice_headers"],
    )
    assert start.status_code == 200 and start.json()["claimed"] is True, start.text
    task_path = _task_file(auth_env["kb_dir"])
    before = task_path.read_bytes()

    resp = _post_finish(client, "t1", {"operator_override": True}, headers=auth_env["read_headers"])

    assert resp.status_code == 403, resp.text
    assert task_path.read_bytes() == before


def test_anonymous_cannot_finish_task(auth_env):
    client = auth_env["client"]
    start = client.post(
        "/api/tasks/t1/start",
        params={"kb": KB},
        json={},
        headers=auth_env["alice_headers"],
    )
    assert start.status_code == 200 and start.json()["claimed"] is True, start.text
    task_path = _task_file(auth_env["kb_dir"])
    before = task_path.read_bytes()

    client.cookies.clear()
    resp = _post_finish(client, "t1", {})

    assert resp.status_code == 401, resp.text
    assert task_path.read_bytes() == before
