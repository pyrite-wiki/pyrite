"""Only the holder of a held task can change its assignee, on every write path.

A task is *held* when its assignee is an account (``user:<id>``). A signed-in
caller who is not that account cannot take it, hand it on or clear it,
whichever surface the write comes through: the task tools, the generic entry
tools (``kb_update``, ``kb_create``, ``kb_bulk_create``) and the REST entry
routes (``POST``, ``PUT`` and ``PATCH /api/entries``). Nor can a signed-in
caller create an entry that names another account as its assignee. The rule
lives in the KB write pipeline (``KBService``), so a surface cannot skip it.

The operator, bound as one (the CLI, stdio, an operator key), is not held to
it; a caller with no identity is never the operator
(tests/test_holder_rule_holds_an_unidentified_caller.py).

Driven over real ``/mcp/sse`` sessions and the REST app, with the
credentials ``mcp_routes`` and ``verify_api_key`` resolve.
"""

from __future__ import annotations

import pytest

from tests.test_mcp_sse_session import OPERATOR_KEY, _bearer
from tests import test_mcp_tools_record_the_caller as callers
from tests.test_mcp_tools_record_the_caller import TASKS, _alice, _as, _call, _key, _rows, _task

#: The world of signed-in accounts the caller tests use: alice and bob,
#: each with a write grant, over real /mcp/sse sessions and the REST app.
env = callers.env


def _hold(client, world) -> None:
    """alice claims the open task: it is now held by her account."""
    claim = {"task_id": "open-task", "kb_name": TASKS}
    assert _call(client, world, _alice(world), "task_claim", claim).get("claimed")


def _refused(result) -> bool:
    return isinstance(result, dict) and result.get("error_code") == "FORBIDDEN"


def _file_assignee(config) -> str:
    text = next(config.get_kb(TASKS).path.rglob("open-task.md")).read_text()
    for line in text.splitlines():
        if line.startswith("assignee:"):
            return line.split(":", 1)[1].strip()
    return ""


def _still_alices(world, config) -> None:
    assert _task(config)["assignee"] == _key(world, "alice")
    assert _file_assignee(config) == _key(world, "alice")


class TestMcp:
    @pytest.mark.parametrize(
        "args",
        [
            {"metadata": {"assignee": ""}},
            {"metadata": {"assignee": "bob"}},
            {"unset": ["assignee"]},
        ],
        ids=["clear", "take-as-label", "unset"],
    )
    def test_kb_update_by_another_account_is_refused(self, env, args):
        client, world, config = env
        _hold(client, world)
        bob_takes = {"metadata": {"assignee": _key(world, "bob")}}
        for attempt in (args, bob_takes):
            result = _call(
                client,
                world,
                _as(world, "bob"),
                "kb_update",
                {"entry_id": "open-task", "kb_name": TASKS, **attempt},
            )
            assert _refused(result), (attempt, result)
        _still_alices(world, config)

    @pytest.mark.control(reason="a holder could always change their own task")
    def test_the_holder_can_clear_it_with_kb_update(self, env):
        client, world, config = env
        _hold(client, world)
        result = _call(
            client,
            world,
            _alice(world),
            "kb_update",
            {"entry_id": "open-task", "kb_name": TASKS, "metadata": {"assignee": ""}},
        )
        assert "error" not in result, result
        assert not _task(config)["assignee"]

    @pytest.mark.control(reason="an update that leaves the assignee alone was never refused")
    def test_another_account_may_still_edit_other_fields(self, env):
        client, world, config = env
        _hold(client, world)
        result = _call(
            client,
            world,
            _as(world, "bob"),
            "kb_update",
            {"entry_id": "open-task", "kb_name": TASKS, "body": "notes from bob"},
        )
        assert "error" not in result, result
        _still_alices(world, config)

    def test_kb_create_naming_another_account_is_refused(self, env):
        client, world, config = env
        result = _call(
            client,
            world,
            _alice(world),
            "kb_create",
            {
                "kb_name": TASKS,
                "entry_type": "task",
                "title": "Pinned on bob",
                "metadata": {"assignee": _key(world, "bob")},
            },
        )
        assert _refused(result), result
        assert _rows(config, "SELECT id FROM entry WHERE title = 'Pinned on bob'") == []

    def test_kb_bulk_create_naming_another_account_is_refused(self, env):
        client, world, config = env
        entries = [
            {
                "entry_type": "task",
                "title": "Bulk pinned on bob",
                "metadata": {"assignee": _key(world, "bob")},
            }
        ]
        result = _call(
            client, world, _alice(world), "kb_bulk_create", {"kb_name": TASKS, "entries": entries}
        )
        assert _rows(config, "SELECT id FROM entry WHERE title = 'Bulk pinned on bob'") == []
        assert "FORBIDDEN" in str(result), result

    @pytest.mark.control(reason="an operator key has no session identity and is not held")
    def test_an_operator_key_may_reassign_a_held_task(self, env):
        client, world, config = env
        _hold(client, world)
        result = _call(
            client,
            world,
            _bearer(OPERATOR_KEY),
            "kb_update",
            {"entry_id": "open-task", "kb_name": TASKS, "metadata": {"assignee": "agent:w"}},
        )
        assert "error" not in result, result
        assert _task(config)["assignee"] == "agent:w"


class TestRest:
    URL = "/api/entries/open-task"

    def test_patch_by_another_account_is_refused(self, env):
        client, world, config = env
        _hold(client, world)
        for value in ("", "bob", _key(world, "bob")):
            r = client.patch(
                self.URL,
                json={"kb": TASKS, "field": "assignee", "value": value},
                headers=_as(world, "bob"),
            )
            assert r.status_code == 403, (value, r.text)
        r = client.patch(
            self.URL,
            json={"kb": TASKS, "field": "assignee", "op": "unset"},
            headers=_as(world, "bob"),
        )
        assert r.status_code == 403, r.text
        _still_alices(world, config)

    def test_put_by_another_account_is_refused(self, env):
        client, world, config = env
        _hold(client, world)
        r = client.put(
            self.URL,
            json={"kb": TASKS, "metadata": {"assignee": _key(world, "bob")}},
            headers=_as(world, "bob"),
        )
        assert r.status_code == 403, r.text
        _still_alices(world, config)

    def test_post_naming_another_account_is_refused(self, env):
        client, world, config = env
        r = client.post(
            "/api/entries",
            json={
                "kb": TASKS,
                "entry_type": "task",
                "title": "Rest pinned on bob",
                "metadata": {"assignee": _key(world, "bob")},
            },
            headers=_alice(world),
        )
        assert r.status_code == 403, r.text
        assert _rows(config, "SELECT id FROM entry WHERE title = 'Rest pinned on bob'") == []

    @pytest.mark.control(reason="a holder could always change their own task")
    def test_the_holder_can_patch_it(self, env):
        client, world, config = env
        _hold(client, world)
        r = client.patch(
            self.URL, json={"kb": TASKS, "field": "assignee", "value": ""}, headers=_alice(world)
        )
        assert r.status_code == 200, r.text
        assert not _task(config)["assignee"]


class TestClaims:
    def test_an_open_task_held_by_another_account_cannot_be_claimed(self, env):
        """An open task whose assignee is alice's account (the operator set
        it): the claim's compare-and-swap would take it; the holder rule
        refuses it first, and neither the index nor the file changes."""
        client, world, config = env
        held = {
            "entry_id": "open-task",
            "kb_name": TASKS,
            "metadata": {"assignee": _key(world, "alice")},
        }
        assert "error" not in _call(client, world, _bearer(OPERATOR_KEY), "kb_update", held)
        result = _call(
            client,
            world,
            _as(world, "bob"),
            "task_claim",
            {"task_id": "open-task", "kb_name": TASKS},
        )
        assert result.get("error_code") == "FORBIDDEN", result
        _still_alices(world, config)
        assert _task(config)["status"] in (None, "", "open")
