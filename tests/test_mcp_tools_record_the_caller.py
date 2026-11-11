"""A tool that records a person records the connection's principal.

Over a connection with a known identity -- an MCP SSE session opened with a
user's session credential -- a tool, core or plugin, that records who did
something (who voted, wrote, reviewed, claimed or is assigned) records that
user. An argument
naming the same user is accepted; an argument naming anyone else is refused
with a clear error and records nothing (maintainer decision, 2026-10-02).

A connection with no identity -- an operator key, auth disabled, local stdio
-- has no principal to record, so there the argument is used as before.

Driven over the real `/mcp/sse` transport with `test_mcp_sse_session`'s ASGI
driver, so the identity is the one `mcp_routes` resolves from the credential,
not one a test hands the dispatcher.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pyrite.config import AuthConfig, KBConfig, PyriteConfig, Settings
from pyrite.server.api import create_app
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB
from tests.auth_seed import seed_and_sign_in
from tests.test_mcp_sse_session import OPERATOR_KEY, _bearer, _cookie, _login, _run, _SSE, _World

SOCIAL, WIKI, SW, TASKS = "social-kb", "wiki-kb", "sw-kb", "tasks-kb"


@pytest.fixture
def env(monkeypatch):
    """``alice`` and ``bob``: global write, with a write grant on each KB."""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        for name in (SOCIAL, WIKI, SW, TASKS):
            (tmp / name).mkdir()
        config = PyriteConfig(
            knowledge_bases=[
                KBConfig(name=SOCIAL, path=tmp / SOCIAL, kb_type="social", default_role="none"),
                KBConfig(name=WIKI, path=tmp / WIKI, kb_type="encyclopedia", default_role="none"),
                KBConfig(name=SW, path=tmp / SW, kb_type="software", default_role="none"),
                KBConfig(name=TASKS, path=tmp / TASKS, kb_type="generic", default_role="none"),
            ],
            settings=Settings(
                index_path=tmp / "index.db",
                api_key=OPERATOR_KEY,
                auto_embed=False,
                auth=AuthConfig(enabled=True, allow_registration=True, max_sessions_per_user=5),
            ),
        )
        with PyriteDB(config.settings.index_path) as db:
            svc = KBService(config, db)
            for item, status in (("item-open", "accepted"), ("item-done", "review")):
                svc.create_entry(
                    SW,
                    item,
                    item,
                    "backlog_item",
                    "body",
                    kind="feature",
                    status=status,
                    priority="high",
                    effort="S",
                )
            svc.create_entry(TASKS, "open-task", "Open task", "task", "body", status="open")
            # A vote and a review name an entry that exists: the dispatcher
            # finds the entry before the handler runs (#1310).
            svc.create_entry(
                SOCIAL, "a-writeup", "A writeup", "writeup", "body", author_id="someone"
            )
            svc.create_entry(WIKI, "an-article", "An article", "article", "body")
        app = create_app(config=config)
        with TestClient(app) as client:
            seed_and_sign_in(client, "root", "password123", role="admin")
            world = _World(app, config)

            def setup(auth):
                ids = {
                    n: auth.create_user(n, "password123", role="write")["id"]
                    for n in ("alice", "bob")
                }
                root = next(u for u in auth.list_users() if u["username"] == "root")["id"]
                for user_id in ids.values():
                    for kb in (SOCIAL, WIKI, SW, TASKS):
                        auth.grant_kb_permission(user_id, kb, "write", root)
                return ids

            world.ids = world.auth(setup)

            # A user whose username is another account's id (bob's).
            def digits(auth):
                name = str(world.ids["bob"])
                user_id = auth.create_user(name, "password123", role="write")["id"]
                root = next(u for u in auth.list_users() if u["username"] == "root")["id"]
                for kb in (SOCIAL, WIKI, SW, TASKS):
                    auth.grant_kb_permission(user_id, kb, "write", root)
                return name, user_id

            digits_name, world.ids["digits"] = world.auth(digits)
            world.tokens = {
                "alice": _login(app, "alice"),
                "bob": _login(app, "bob"),
                "digits": _login(app, digits_name),
            }
            yield client, world, config


def _call(client, world, headers, tool: str, args: dict) -> dict:
    out: dict = {}

    async def scenario(tg):
        sse = await _SSE(world.app, headers).open(tg)
        await world.initialize(sse, headers)
        out.update(await world.call(sse, tool, args, headers))

    _run(client, scenario)
    return out


def _alice(world) -> dict:
    return _cookie(world.tokens["alice"])


def _as(world, name: str) -> dict:
    return _cookie(world.tokens[name])


def _key(world, name: str) -> str:
    """The key an account's records are stored under."""
    return f"user:{world.ids[name]}"


def _rows(config, sql: str, *params) -> list[dict]:
    with PyriteDB(config.settings.index_path) as db:
        return [dict(r) for r in db._raw_conn.execute(sql, params).fetchall()]


def _votes(config) -> list[tuple[str, int]]:
    return sorted(
        (r["user_id"], r["value"]) for r in _rows(config, "SELECT user_id, value FROM social_vote")
    )


def _refused(result: dict) -> bool:
    return result.get("error_code") == "FORBIDDEN"


VOTE = {"entry_id": "a-writeup", "kb_name": SOCIAL, "value": 1}


def _posts(config) -> list[Path]:
    """The social KB's files, but the writeup the votes are cast on."""
    return [p for p in config.get_kb(SOCIAL).path.rglob("*.md") if p.stem != "a-writeup"]


class TestAScopedCallerIsRecordedAsThemselves:
    def test_a_vote_naming_another_user_is_refused_and_records_nothing(self, env):
        client, world, config = env
        result = _call(client, world, _alice(world), "social_vote", {**VOTE, "user_id": "bob"})
        assert _refused(result), result
        assert "bob" in result["error"] and _key(world, "alice") in result["error"]
        assert "Omit" in result["suggestion"], "the refusal says how to act as yourself"
        assert _votes(config) == []

    def test_a_vote_with_no_user_records_the_callers_account(self, env):
        client, world, config = env
        result = _call(client, world, _alice(world), "social_vote", VOTE)
        assert result.get("voted") is True, result
        assert _votes(config) == [(_key(world, "alice"), 1)]

    @pytest.mark.control(reason="a caller naming its own key was recorded before this change too")
    def test_a_vote_naming_the_callers_own_key_records_the_caller(self, env):
        client, world, config = env
        args = {**VOTE, "user_id": _key(world, "alice")}
        result = _call(client, world, _alice(world), "social_vote", args)
        assert result.get("voted") is True, result
        assert _votes(config) == [(_key(world, "alice"), 1)]

    def test_a_username_is_not_an_identity_argument(self, env):
        # The recorded identity never comes from an argument: even the
        # caller's own username is a different string from their key.
        client, world, config = env
        result = _call(client, world, _alice(world), "social_vote", {**VOTE, "user_id": "alice"})
        assert _refused(result), result
        assert _votes(config) == []

    def test_a_digits_only_username_is_never_taken_for_an_account(self, env):
        # One account's username is another account's id. Neither can reach
        # the other's records, and a label stored earlier under that string
        # belongs to no account.
        client, world, config = env
        bob_id = str(world.ids["bob"])
        with PyriteDB(config.settings.index_path) as db:
            db._raw_conn.execute(
                "INSERT INTO social_vote (entry_id, kb_name, user_id, value, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("a-writeup", SOCIAL, bob_id, -1, "2026-01-01T00:00:00+00:00"),
            )
            db._raw_conn.commit()
        mine = _call(client, world, _as(world, "digits"), "social_vote", VOTE)
        bobs = _call(client, world, _as(world, "bob"), "social_vote", VOTE)
        naming_bob = _call(
            client, world, _as(world, "digits"), "social_vote", {**VOTE, "user_id": bob_id}
        )
        assert mine.get("voted") and bobs.get("voted"), (mine, bobs)
        assert _refused(naming_bob), naming_bob
        assert _votes(config) == sorted(
            [(bob_id, -1), (_key(world, "digits"), 1), (_key(world, "bob"), 1)]
        )

    def test_a_post_naming_another_author_is_refused_and_writes_no_file(self, env):
        client, world, config = env
        args = {"kb_name": SOCIAL, "title": "Not mine", "body": "text", "author_id": "bob"}
        result = _call(client, world, _alice(world), "social_post", args)
        assert _refused(result), result
        assert _posts(config) == []

    def test_a_post_with_no_author_is_written_as_the_callers_account(self, env):
        client, world, config = env
        args = {"kb_name": SOCIAL, "title": "Mine", "body": "text"}
        result = _call(client, world, _alice(world), "social_post", args)
        assert result.get("created") is True, result
        [written] = _posts(config)
        assert f"author_id: {_key(world, 'alice')}" in written.read_text()

    def test_a_wiki_review_naming_another_reviewer_is_refused(self, env):
        client, world, config = env
        args = {
            "entry_id": "an-article",
            "kb_name": WIKI,
            "action": "approve",
            "reviewer_id": "bob",
        }
        result = _call(client, world, _alice(world), "wiki_submit_review", args)
        assert _refused(result), result
        assert _rows(config, "SELECT * FROM encyclopedia_review") == []

    def test_a_wiki_review_with_no_reviewer_records_the_callers_account(self, env):
        client, world, config = env
        args = {"entry_id": "an-article", "kb_name": WIKI, "action": "approve"}
        result = _call(client, world, _alice(world), "wiki_submit_review", args)
        assert result.get("reviewed") is True, result
        rows = _rows(config, "SELECT reviewer_id FROM encyclopedia_review")
        assert rows == [{"reviewer_id": _key(world, "alice")}]

    def test_a_claim_for_another_assignee_is_refused_and_leaves_the_item_open(self, env):
        client, world, config = env
        args = {"item_id": "item-open", "kb_name": SW, "assignee": "bob"}
        result = _call(client, world, _alice(world), "sw_claim", args)
        assert _refused(result), result
        [row] = _rows(config, "SELECT status, assignee FROM entry WHERE id = 'item-open'")
        assert row["status"] == "accepted" and not row["assignee"], row

    def test_a_claim_with_no_assignee_assigns_the_callers_account(self, env):
        client, world, config = env
        result = _call(
            client, world, _alice(world), "sw_claim", {"item_id": "item-open", "kb_name": SW}
        )
        assert result.get("claimed") is True, result
        [row] = _rows(config, "SELECT status, assignee FROM entry WHERE id = 'item-open'")
        assert (row["status"], row["assignee"]) == ("in_progress", _key(world, "alice"))

    def test_a_review_naming_another_reviewer_is_refused_and_leaves_the_item_in_review(self, env):
        client, world, config = env
        args = {"item_id": "item-done", "kb_name": SW, "outcome": "approved", "reviewer": "bob"}
        result = _call(client, world, _alice(world), "sw_review", args)
        assert _refused(result), result
        [row] = _rows(config, "SELECT status FROM entry WHERE id = 'item-done'")
        assert row["status"] == "review"
        assert _rows(config, "SELECT * FROM review") == []

    def test_a_review_with_no_reviewer_records_the_callers_account(self, env):
        client, world, config = env
        args = {"item_id": "item-done", "kb_name": SW, "outcome": "approved"}
        result = _call(client, world, _alice(world), "sw_review", args)
        assert result.get("reviewed") is True, result
        reviewers = [r["reviewer"] for r in _rows(config, "SELECT reviewer FROM review")]
        assert reviewers == [_key(world, "alice")]


class TestACallerWithNoIdentityNamesThePerson:
    @pytest.mark.control(reason="an operator key has no session identity: the argument is a label")
    def test_an_operator_key_vote_records_the_named_label(self, env):
        client, world, config = env
        result = _call(
            client, world, _bearer(OPERATOR_KEY), "social_vote", {**VOTE, "user_id": "bob"}
        )
        assert result.get("voted") is True, result
        assert _votes(config) == [("bob", 1)]

    def test_an_operator_key_vote_naming_nobody_is_refused_clearly(self, env):
        client, world, config = env
        result = _call(client, world, _bearer(OPERATOR_KEY), "social_vote", VOTE)
        assert result.get("error_code") == "VALIDATION_FAILED", result
        assert "user_id" in result["error"] and "user_id" in result["suggestion"]
        assert _votes(config) == []

    @pytest.mark.control(
        reason="the local operator has no session identity: the argument is a label"
    )
    def test_a_local_call_records_the_named_label(self, env):
        _, _, config = env
        from pyrite.server.mcp_server import PyriteMCPServer
        from pyrite.services.access_policy import Principal

        server = PyriteMCPServer(config=config, tier="write")
        try:
            result = server._dispatch_tool(
                "sw_claim",
                {"item_id": "item-open", "kb_name": SW, "assignee": "bob"},
                client_kind="local",
                principal=Principal.local(),
            )
        finally:
            server.close()
        assert result.get("claimed") is True, result
        [row] = _rows(config, "SELECT assignee FROM entry WHERE id = 'item-open'")
        assert row["assignee"] == "bob"


def _task(config, task_id="open-task") -> dict:
    [row] = _rows(config, "SELECT status, assignee FROM entry WHERE id = ?", task_id)
    return row


class TestCoreTaskToolsRecordTheCaller:
    def test_a_claim_for_another_assignee_is_refused_and_leaves_the_task_open(self, env):
        client, world, config = env
        args = {"task_id": "open-task", "kb_name": TASKS, "assignee": "bob"}
        result = _call(client, world, _alice(world), "task_claim", args)
        assert _refused(result), result
        assert _task(config)["status"] == "open" and not _task(config)["assignee"]

    def test_a_claim_with_no_assignee_assigns_the_callers_account(self, env):
        client, world, config = env
        result = _call(
            client, world, _alice(world), "task_claim", {"task_id": "open-task", "kb_name": TASKS}
        )
        assert result.get("claimed") is True, result
        assert _task(config)["assignee"] == _key(world, "alice")

    def test_a_task_created_for_another_assignee_is_refused_and_not_written(self, env):
        client, world, config = env
        args = {"kb_name": TASKS, "title": "For bob", "assignee": "bob"}
        result = _call(client, world, _alice(world), "task_create", args)
        assert _refused(result), result
        assert _rows(config, "SELECT id FROM entry WHERE title = 'For bob'") == []

    @pytest.mark.control(
        reason="a task created with no assignee was unassigned before the change too"
    )
    def test_a_task_created_with_no_assignee_stays_unassigned(self, env):
        client, world, config = env
        result = _call(
            client, world, _alice(world), "task_create", {"kb_name": TASKS, "title": "Nobody's"}
        )
        assert "error" not in result, result
        [row] = _rows(config, "SELECT assignee FROM entry WHERE title = ?", "Nobody's")
        assert not row["assignee"]

    def test_an_update_assigning_someone_else_is_refused(self, env):
        client, world, config = env
        args = {"task_id": "open-task", "kb_name": TASKS, "assignee": "bob"}
        result = _call(client, world, _alice(world), "task_update", args)
        assert _refused(result), result
        assert not _task(config)["assignee"]

    def test_another_account_cannot_clear_or_take_a_claim(self, env):
        client, world, config = env
        claim = {"task_id": "open-task", "kb_name": TASKS}
        assert _call(client, world, _alice(world), "task_claim", claim).get("claimed")
        cleared = _call(client, world, _as(world, "bob"), "task_update", {**claim, "assignee": ""})
        taken = _call(
            client,
            world,
            _as(world, "bob"),
            "task_update",
            {**claim, "assignee": _key(world, "bob")},
        )
        assert _refused(cleared) and _refused(taken), (cleared, taken)
        assert _task(config)["assignee"] == _key(world, "alice")

    @pytest.mark.control(reason="a holder could always clear their own claim")
    def test_the_holder_can_clear_their_own_claim(self, env):
        client, world, config = env
        claim = {"task_id": "open-task", "kb_name": TASKS}
        assert _call(client, world, _alice(world), "task_claim", claim).get("claimed")
        result = _call(client, world, _alice(world), "task_update", {**claim, "assignee": ""})
        assert "error" not in result, result
        assert not _task(config)["assignee"]

    def test_a_decomposed_child_for_another_assignee_is_refused(self, env):
        client, world, config = env
        args = {
            "parent_id": "open-task",
            "kb_name": TASKS,
            "children": [{"title": "Child for bob", "assignee": "bob"}],
        }
        result = _call(client, world, _alice(world), "task_decompose", args)
        assert _refused(result), result
        assert _rows(config, "SELECT id FROM entry WHERE title = 'Child for bob'") == []

    @pytest.mark.control(reason="an operator key has no session identity: the argument is a label")
    def test_an_operator_key_claim_records_the_named_label(self, env):
        client, world, config = env
        args = {"task_id": "open-task", "kb_name": TASKS, "assignee": "agent:worker-1"}
        result = _call(client, world, _bearer(OPERATOR_KEY), "task_claim", args)
        assert result.get("claimed") is True, result
        assert _task(config)["assignee"] == "agent:worker-1"


class TestRestRoutesRecordTheCaller:
    """The REST routes that write the same records follow the same rule."""

    CLAIM = f"/api/tasks/open-task/claim?kb={TASKS}"

    def _review(self, **extra):
        return {
            "entry_id": "open-task",
            "kb_name": TASKS,
            "reviewer_type": "user",
            "result": "pass",
            **extra,
        }

    def test_a_rest_claim_naming_another_assignee_is_refused(self, env):
        client, world, config = env
        r = client.post(self.CLAIM, json={"assignee": "bob"}, headers=_alice(world))
        assert r.status_code == 403, r.text
        assert not _task(config)["assignee"]

    def test_a_rest_claim_with_no_assignee_assigns_the_callers_account(self, env):
        client, world, config = env
        r = client.post(self.CLAIM, json={}, headers=_alice(world))
        assert r.status_code == 200 and r.json().get("claimed"), r.text
        assert _task(config)["assignee"] == _key(world, "alice")

    def test_a_rest_review_naming_another_reviewer_is_refused(self, env):
        client, world, config = env
        r = client.post("/api/reviews", json=self._review(reviewer="bob"), headers=_alice(world))
        assert r.status_code == 403, r.text
        assert _rows(config, "SELECT * FROM review") == []

    def test_a_rest_review_with_no_reviewer_records_the_callers_account(self, env):
        client, world, config = env
        r = client.post("/api/reviews", json=self._review(), headers=_alice(world))
        assert r.status_code == 200, r.text
        assert r.json()["reviewer"] == _key(world, "alice")

    @pytest.mark.control(reason="an operator key has no session identity: the body names a label")
    def test_an_operator_key_rest_review_records_the_named_label(self, env):
        client, world, config = env
        headers = {"X-API-Key": OPERATOR_KEY}
        r = client.post("/api/reviews", json=self._review(reviewer="ci-bot"), headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["reviewer"] == "ci-bot"


class TestTheDispatcherDecidesNotTheHandler:
    """Person arguments are set from the principal before any handler runs,
    so a handler the dispatcher cannot inspect is held the same."""

    def _server(self, config, monkeypatch, handler):
        import functools

        from pyrite.plugins import get_registry
        from pyrite.plugins.identity import CALLER, PERSON_ARGUMENTS
        from pyrite.server.mcp_server import PyriteMCPServer

        class Wrapped:
            name = "wrapped_plugin"
            capabilities: set = set()

            def get_mcp_tools(self, tier):
                if tier not in ("write", "admin"):
                    return {}
                return {
                    "wrapped_record": {
                        "description": "records a person",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "kb_name": {"type": "string"},
                                "who": {"type": "string"},
                            },
                            "required": ["kb_name"],
                        },
                        "handler": functools.partial(handler),
                        PERSON_ARGUMENTS: {"who": CALLER},
                    }
                }

        registry = get_registry()
        registry.discover()
        monkeypatch.setitem(registry._plugins, "wrapped_plugin", Wrapped())
        return PyriteMCPServer(config=config, tier="write")

    def test_a_wrapped_handler_receives_the_callers_key_not_the_argument(self, env, monkeypatch):
        _, world, config = env
        from pyrite.services.access_policy import Principal

        seen: list = []

        def record(args, **kwargs):
            seen.append(args.get("who"))
            return {"ok": True}

        server = self._server(config, monkeypatch, record)
        alice = Principal.user(world.ids["alice"], "write")
        scope = {"readable_kbs": {SOCIAL}, "writable_kbs": {SOCIAL}}
        try:
            mine = server._dispatch_tool(
                "wrapped_record", {"kb_name": SOCIAL}, principal=alice, **scope
            )
            other = server._dispatch_tool(
                "wrapped_record", {"kb_name": SOCIAL, "who": "bob"}, principal=alice, **scope
            )
            anonymous = server._dispatch_tool(
                "wrapped_record",
                {"kb_name": SOCIAL, "who": "bob"},
                principal=Principal.anonymous("write"),
                **scope,
            )
            no_principal = server._dispatch_tool(
                "wrapped_record", {"kb_name": SOCIAL, "who": "bob"}, **scope
            )
        finally:
            server.close()
        assert mine == {"ok": True} and seen == [_key(world, "alice")], (mine, seen)
        assert _refused(other) and _refused(no_principal), (other, no_principal)
        # An anonymous write has no place to land under the landing rule
        # (ADR-0044), which refuses it before the person rule is reached.
        assert anonymous.get("error_code") in ("FORBIDDEN", "LANDING_REFUSED"), anonymous
        assert len(seen) == 1, seen  # no refused call reached the handler
