"""MCP over HTTP serves only the KBs its caller may read (#201, part 3).

MCP resolved a **global tier** from the caller's credential and applied no
per-KB read scoping anywhere in its path, while every REST content route had
been scoped by #180. A plain read-tier user reached private-KB entry
**bodies** over `/mcp`: `kb_get` returned `"zebra behind the wall"`,
`kb_list` named the private KB, `kb_search` counted its hits.

**Why this file drives a real MCP session.** Calling `_dispatch_tool`
directly would prove only that the dispatcher can refuse; it would not prove
that the readable set actually *reaches* the dispatcher on the path a real
connection takes. The leak lived in that wiring, not in the dispatcher. So
these tests build the SDK server the way `mcp_routes.handle_sse` does --
`_get_mcp_server(tier).build_sdk_server(...)` -- and drive it through the
SDK's own in-memory transport with a real `ClientSession`: a real
`initialize` handshake and real `tools/call` JSON-RPC round trips.

**The cache assertion is the point.** The issue claimed the per-tier server
cache blocked per-request scoping. It does not: the cached instance is
identity-free, and identity rides the per-connection closures
`build_sdk_server` already creates for `client_id`.
`test_both_peers_share_one_cached_server_instance` asserts both connections
resolved to the *same* `PyriteMCPServer` object. Without it this whole file
would pass for the wrong reason -- two separate servers would scope
correctly by accident and prove nothing.

**Not over-blocking is half the job.** `TestGrantedPeerStillGetsThrough`
is the other half: a fix that refuses everybody passes every leak test here
and breaks the product.
"""

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from pyrite.config import AuthConfig, KBConfig, PyriteConfig, Settings
from pyrite.server.mcp_server import PyriteMCPServer
from pyrite.services.auth_service import AuthService
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB
from tests.auth_seed import seed_user

PUBLIC, PRIVATE = "public-kb", "private-kb"


# The exact payload a scoped caller gets for a KB that genuinely does not
# exist. A refusal must be byte-identical to it: a caller must not be able to
# tell "you may not read this" from "there is no such KB", or `/mcp` becomes
# an oracle for the existence of private KBs. Both come from the dispatcher's
# `_kb_not_found`: a scoped caller never reaches a handler with a name outside
# its readable set. The code is the contract's `KB_NOT_FOUND` since 0.25.7
# (#66), with the old `NOT_FOUND` carried one release.
def _absent_kb_payload(kb_name):
    return {
        "error": f"KB '{kb_name}' not found",
        "error_code": "KB_NOT_FOUND",
        "retryable": False,
        "suggestion": "Call kb_orient with no kb_name to list the KBs you can read.",
        "legacy_error_code": "NOT_FOUND",
    }


@pytest.fixture
def env():
    """Two KBs (one public, one private), three users, and a live server cache.

    Mirrors `tests/test_private_kb_read_scoping.py`'s fixture so the MCP
    surface is tested against the same world the REST surface is.
    """
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        (tmp / PUBLIC).mkdir()
        (tmp / PRIVATE).mkdir()
        config = PyriteConfig(
            knowledge_bases=[
                KBConfig(name=PUBLIC, path=tmp / PUBLIC, kb_type="generic", default_role="read"),
                KBConfig(name=PRIVATE, path=tmp / PRIVATE, kb_type="generic", default_role="none"),
            ],
            settings=Settings(
                index_path=tmp / "index.db",
                auth=AuthConfig(enabled=True, allow_registration=True, anonymous_tier="read"),
            ),
        )
        db = PyriteDB(config.settings.index_path)
        svc = KBService(config, db)
        svc.create_entry(PUBLIC, "public-note", "Public note", "note", "zebra in the open")
        svc.create_entry(PRIVATE, "secret-note", "Secret note", "note", "zebra behind the wall")
        svc.create_entry(PRIVATE, "secret-two", "Second secret", "note", "another zebra, hidden")
        svc.create_entry(
            PUBLIC,
            "public-event",
            "Public event",
            "event",
            "a zebra was seen",
            date="2021-01-01",
            importance=5,
            tags=["openly/known"],
        )
        svc.create_entry(
            PRIVATE,
            "secret-event",
            "Secret event",
            "event",
            "a zebra was smuggled",
            date="2021-06-15",
            importance=7,
            tags=["confidential/operation-zebra"],
        )

        auth = AuthService(db, config.settings.auth)
        # Seeded the operator's way. peer and peer-granted hold the global
        # read role, which covers every KB without a default_role -- the
        # persona these tests pin as denied a default_role: none KB.
        seed_user(db, "admin-user", "password123", role="admin")
        seed_user(db, "peer", "password123", role="read")
        seed_user(db, "peer-granted", "password123", role="read")
        users = {u["username"]: u for u in auth.list_users()}
        auth.grant_kb_permission(
            users["peer-granted"]["id"], PRIVATE, "read", users["admin-user"]["id"]
        )

        # One cached server per tier, exactly as mcp_routes._get_mcp_server
        # does: identity-free, shared by every caller at that tier.
        cache: dict[str, PyriteMCPServer] = {}

        def server_for_tier(tier):
            if tier not in cache:
                cache[tier] = PyriteMCPServer(config=config, tier=tier)
            return cache[tier]

        try:
            yield {
                "config": config,
                "db": db,
                "auth": auth,
                "users": users,
                "server_for_tier": server_for_tier,
            }
        finally:
            for s in cache.values():
                s.close()
            db.close()


def _readable_for(env, username):
    """Resolve the readable set the way mcp_routes must: from the shared
    helper in api.py, with no Request object anywhere."""
    from pyrite.server.api import readable_kbs_for_user

    user = env["users"][username]
    return readable_kbs_for_user(env["config"], env["db"], user["id"], user["role"])


def _call(env, username, tool, arguments=None, *, tier="read"):
    """Drive one real MCP tool call as `username`, over a real ClientSession.

    Builds the SDK server the way `handle_sse` does -- from the per-tier
    cached `PyriteMCPServer`, with the caller's readable set passed per
    connection -- and runs a genuine `initialize` + `tools/call` round trip
    through the SDK's in-memory transport. Returns the decoded tool result.
    """
    from mcp.shared.memory import create_connected_server_and_client_session

    mcp_server = env["server_for_tier"](tier)
    sdk = mcp_server.build_sdk_server(
        client_id=username,
        readable_kbs=_readable_for(env, username),
    )

    async def _run():
        async with create_connected_server_and_client_session(sdk) as session:
            result = await session.call_tool(tool, arguments or {})
            return json.loads(result.content[0].text)

    return asyncio.run(_run())


def _api_key_call(env, tool, arguments=None, *, tier="read"):
    """An API-key caller: unscoped, per REST's operator-credential rule."""
    from mcp.shared.memory import create_connected_server_and_client_session

    sdk = env["server_for_tier"](tier).build_sdk_server(
        client_id="apikey-deadbeef", readable_kbs=None
    )

    async def _run():
        async with create_connected_server_and_client_session(sdk) as session:
            result = await session.call_tool(tool, arguments or {})
            return json.loads(result.content[0].text)

    return asyncio.run(_run())


def _ids(payload, key="entries"):
    items = payload.get(key) or payload.get("results") or []
    return {i["id"] for i in items}


class TestPeerCannotReachPrivateKBOverMCP:
    """The reproduction from the spike, now refused. Each of these returned
    private-KB content -- bodies included -- to a plain read-tier user."""

    def test_kb_get_on_a_private_entry(self, env):
        # Spike, live: {"entry":{"id":"secret-note","body":"zebra behind the wall"}}
        out = _call(env, "peer", "kb_get", {"entry_id": "secret-note", "kb_name": PRIVATE})
        assert out == _absent_kb_payload(PRIVATE)
        assert "zebra behind the wall" not in json.dumps(out)

    def test_kb_get_with_kb_omitted_does_not_fall_into_a_private_kb(self, env):
        # kb_name omitted makes KBService.get_entry walk every KB in config
        # order, so omitting it was itself a way to reach private content.
        out = _call(env, "peer", "kb_get", {"entry_id": "secret-note"})
        assert "zebra behind the wall" not in json.dumps(out)
        assert out.get("error_code") == "NOT_FOUND"

    def test_kb_list_entries_on_a_private_kb(self, env):
        out = _call(env, "peer", "kb_list_entries", {"kb_name": PRIVATE})
        assert out == _absent_kb_payload(PRIVATE)

    def test_kb_list_omits_the_private_kb(self, env):
        out = _call(env, "peer", "kb_list")
        assert {k["name"] for k in out["knowledge_bases"]} == {PUBLIC}

    def test_kb_search_returns_only_public_hits_and_a_matching_count(self, env):
        # Spike, live: count 2 over MCP where REST said 1.
        out = _call(env, "peer", "kb_search", {"query": "zebra"})
        assert _ids(out, "results") == {"public-note", "public-event"}
        assert out["count"] == len(out["results"]), (
            "count must be computed after filtering, or it reveals how many private entries matched"
        )
        assert "zebra behind the wall" not in json.dumps(out)

    def test_kb_list_entries_across_all_kbs_is_filtered(self, env):
        out = _call(env, "peer", "kb_list_entries", {})
        assert _ids(out) == {"public-note", "public-event"}
        assert out["total"] == 2, "total must count only readable entries"

    def test_kb_recent_is_filtered(self, env):
        out = _call(env, "peer", "kb_recent", {})
        assert _ids(out) == {"public-note", "public-event"}
        assert out["count"] == len(out["entries"])

    def test_kb_timeline_is_filtered(self, env):
        out = _call(env, "peer", "kb_timeline", {})
        assert {e["id"] for e in out["events"]} == {"public-event"}
        assert out["count"] == len(out["events"])

    def test_kb_stats_counts_only_readable_kbs(self, env):
        # Index-wide before: the private KB's name, row and rows in every total.
        out = _call(env, "peer", "kb_stats", {})
        assert set(out["kbs"]) == {PUBLIC}
        assert out["total_entries"] == 2
        assert out["total_tags"] == 1
        assert {t["entry_type"]: t["count"] for t in out["type_counts"]} == {
            "note": 1,
            "event": 1,
        }
        assert PRIVATE not in json.dumps(out)

    def test_kb_tags_does_not_leak_private_tag_names(self, env):
        out = _call(env, "peer", "kb_tags", {})
        tags = {t["tag"] for t in out["tags"]}
        assert not any(t.startswith("confidential/") for t in tags), (
            f"a private KB's tag names leaked through kb_tags: {sorted(tags)}"
        )

    def test_kb_batch_read_drops_unreadable_pairs_into_not_found(self, env):
        # An *error* here would itself reveal the KB. The unreadable pair
        # must be indistinguishable from a pair that simply does not exist.
        out = _call(
            env,
            "peer",
            "kb_batch_read",
            {
                "entries": [
                    {"entry_id": "public-note", "kb_name": PUBLIC},
                    {"entry_id": "secret-note", "kb_name": PRIVATE},
                ]
            },
        )
        assert _ids(out) == {"public-note"}
        assert {(n["entry_id"], n["kb_name"]) for n in out["not_found"]} == {
            ("secret-note", PRIVATE)
        }
        assert "zebra behind the wall" not in json.dumps(out)

    def test_task_list_is_filtered(self, env):
        out = _call(env, "peer", "task_list", {})
        assert all(t.get("kb_name") == PUBLIC for t in out["tasks"])

    def test_kb_discover_neighbors_spanning_returns_only_public_candidates(self, env):
        # With `target_kb` omitted the service searches every KB, so the
        # candidate list itself is the hole: a peer naming only a readable
        # KB could be handed a private entry's title and snippet back as a
        # suggestion. The readable set now goes into the service (#186).
        out = _call(
            env,
            "peer",
            "kb_discover_neighbors",
            # `keyword` pinned: the tool's default is hybrid, which can return
            # nothing in a fixture with an empty vector index -- and an empty
            # answer would make the assertion below vacuous.
            {"entry_id": "public-note", "kb_name": PUBLIC, "mode": "keyword"},
        )
        assert out.get("count", 0) > 0, out
        assert {d["kb_name"] for d in out["discoveries"]} == {PUBLIC}
        assert "secret-note" not in json.dumps(out)
        assert "zebra behind the wall" not in json.dumps(out)


class TestNamingAReadableKBAlongsideAPrivateOneBuysNothing:
    """The #180 rule (`_resolve_kb_names`): **every** KB the call names is
    checked, not the first one found. A call that named a readable KB in one
    parameter and a private one in another must be refused."""

    def test_secondary_target_kb_is_checked(self, env):
        out = _call(
            env,
            "peer",
            "kb_discover_neighbors",
            {"entry_id": "public-note", "kb_name": PUBLIC, "target_kb": PRIVATE},
        )
        assert out == _absent_kb_payload(PRIVATE)

    def test_source_and_target_kb_are_both_checked(self, env):
        out = _call(env, "peer", "kb_batch_suggest", {"source_kb": PUBLIC, "target_kb": PRIVATE})
        assert out == _absent_kb_payload(PRIVATE)
        out = _call(env, "peer", "kb_batch_suggest", {"source_kb": PRIVATE, "target_kb": PUBLIC})
        assert out == _absent_kb_payload(PRIVATE)


class TestTheRefusalIsIndistinguishableFromAbsence:
    """A refusal that says "forbidden", or differs in any byte from the
    answer for a KB that does not exist, turns `/mcp` into an oracle for
    which private KBs exist."""

    def test_refusal_is_byte_identical_to_a_nonexistent_kb(self, env):
        refused = _call(env, "peer", "kb_list_entries", {"kb_name": PRIVATE})
        absent = _call(env, "peer", "kb_list_entries", {"kb_name": "no-such-kb-at-all"})
        assert refused == _absent_kb_payload(PRIVATE)
        assert absent == _absent_kb_payload("no-such-kb-at-all")
        # Same keys, same codes, same message template: only the name differs.
        assert set(refused) == set(absent)
        assert refused["error_code"] == absent["error_code"] == "KB_NOT_FOUND"
        assert refused["retryable"] == absent["retryable"] is False

    def test_the_word_forbidden_never_appears(self, env):
        out = _call(env, "peer", "kb_get", {"entry_id": "secret-note", "kb_name": PRIVATE})
        blob = json.dumps(out).lower()
        for word in ("forbidden", "denied", "permission", "unauthor", "not allowed"):
            assert word not in blob, f"the refusal advertises itself as a refusal: {out}"


class TestGrantedPeerStillGetsThrough:
    """The other half. A fix that refuses everyone passes every test above
    and breaks the product: `peer-granted` holds an explicit read grant on
    the private KB and must see all of it."""

    def test_kb_get_succeeds(self, env):
        out = _call(env, "peer-granted", "kb_get", {"entry_id": "secret-note", "kb_name": PRIVATE})
        assert out["entry"]["body"] == "zebra behind the wall"

    def test_kb_stats_includes_the_private_kb(self, env):
        out = _call(env, "peer-granted", "kb_stats", {})
        assert set(out["kbs"]) == {PUBLIC, PRIVATE}
        assert out["total_entries"] == 5

    def test_kb_list_includes_the_private_kb(self, env):
        out = _call(env, "peer-granted", "kb_list")
        assert {k["name"] for k in out["knowledge_bases"]} == {PUBLIC, PRIVATE}

    def test_kb_search_includes_private_hits(self, env):
        out = _call(env, "peer-granted", "kb_search", {"query": "zebra"})
        assert "secret-note" in _ids(out, "results")

    def test_kb_list_entries_on_the_private_kb_succeeds(self, env):
        out = _call(env, "peer-granted", "kb_list_entries", {"kb_name": PRIVATE})
        assert _ids(out) == {"secret-note", "secret-two", "secret-event"}

    def test_secondary_kb_params_are_allowed_when_readable(self, env):
        out = _call(
            env,
            "peer-granted",
            "kb_discover_neighbors",
            {"entry_id": "public-note", "kb_name": PUBLIC, "target_kb": PRIVATE},
        )
        assert out.get("error_code") not in ("NOT_FOUND", "KB_NOT_FOUND")


class TestAnApiKeyIsNotScoped:
    """An API key is the operator's credential, not a peer's -- the same
    rule REST applies (`readable_kbs` returns None for an API-key caller)."""

    def test_api_key_sees_the_private_kb(self, env):
        out = _api_key_call(env, "kb_list")
        assert {k["name"] for k in out["knowledge_bases"]} == {PUBLIC, PRIVATE}

    def test_api_key_reads_a_private_entry(self, env):
        out = _api_key_call(env, "kb_get", {"entry_id": "secret-note", "kb_name": PRIVATE})
        assert out["entry"]["body"] == "zebra behind the wall"


class TestTheCacheWasNeverTheProblem:
    """The issue said the per-tier cache blocked per-request scoping. It did
    not, and this is the proof: both peers ran through the *same* cached
    `PyriteMCPServer` and got correctly different answers."""

    def test_both_peers_share_one_cached_server_instance(self, env):
        a = env["server_for_tier"]("read")
        b = env["server_for_tier"]("read")
        assert a is b, "fixture no longer shares one instance; the test below proves nothing"

    def test_same_instance_different_answers(self, env):
        instances = []

        def call_recording_instance(username):
            server = env["server_for_tier"]("read")
            instances.append(id(server))
            return _call(env, username, "kb_list")

        plain = call_recording_instance("peer")
        granted = call_recording_instance("peer-granted")

        assert len(set(instances)) == 1, (
            f"the two connections used different server instances {instances}; "
            "this test only means something on one shared instance"
        )
        assert {k["name"] for k in plain["knowledge_bases"]} == {PUBLIC}
        assert {k["name"] for k in granted["knowledge_bases"]} == {PUBLIC, PRIVATE}

    def test_the_readable_set_is_never_stored_on_the_shared_instance(self, env):
        """Scoping rides the per-connection closure, not the shared object.
        If it were stored on the instance, connection B would inherit
        connection A's scope -- the actual hazard the cache creates."""
        server = env["server_for_tier"]("read")
        _call(env, "peer", "kb_list")
        leaked = [
            name for name in vars(server) if "readable" in name.lower() or "scoped" in name.lower()
        ]
        assert not leaked, (
            f"the readable set was stored on the shared server instance ({leaked}); "
            "every other caller at this tier now inherits it"
        )


class TestLocalStdioStaysUnscoped:
    """`run_stdio()` is the local CLI: one user, their own machine, no
    identity to scope by. The default must leave it exactly as it was."""

    def test_default_build_sdk_server_is_unscoped(self, env):
        from mcp.shared.memory import create_connected_server_and_client_session

        sdk = env["server_for_tier"]("read").build_sdk_server()

        async def _run():
            async with create_connected_server_and_client_session(sdk) as session:
                result = await session.call_tool("kb_list", {})
                return json.loads(result.content[0].text)

        out = asyncio.run(_run())
        assert {k["name"] for k in out["knowledge_bases"]} == {PUBLIC, PRIVATE}


class TestResourcesAreScoped:
    """`_read_resource` is a second content chokepoint: it serves
    `pyrite://kbs`, `pyrite://kbs/{name}/entries` and `pyrite://entries/{id}`
    **without** passing through `_dispatch_tool`.

    It is reached here directly, at the internal method, to isolate the
    scoping logic from the SDK transport. The transport itself -- whether
    `resources/read` actually reaches this method and serves its result over
    a real session -- is covered end-to-end by
    `tests/test_mcp_resources_session.py` (#217: the SDK adapter used to
    return a `ReadResourceResult` where the installed SDK expects an
    iterable of `ReadResourceContents`, so nothing reached here at all).
    """

    def test_kbs_resource_lists_only_readable_kbs(self, env):
        server = env["server_for_tier"]("read")
        out = server._read_resource("pyrite://kbs", readable_kbs={PUBLIC})
        names = {k["name"] for k in json.loads(out["contents"][0]["text"])}
        assert names == {PUBLIC}

    def test_kb_entries_resource_refuses_an_unreadable_kb(self, env):
        server = env["server_for_tier"]("read")
        out = server._read_resource(f"pyrite://kbs/{PRIVATE}/entries", readable_kbs={PUBLIC})
        assert out == _absent_kb_payload(PRIVATE)

    def test_entry_resource_refuses_an_entry_in_an_unreadable_kb(self, env):
        server = env["server_for_tier"]("read")
        out = server._read_resource("pyrite://entries/secret-note", readable_kbs={PUBLIC})
        assert "zebra behind the wall" not in json.dumps(out)
        assert out["error_code"] == "NOT_FOUND"

    def test_a_readable_kb_still_works(self, env):
        server = env["server_for_tier"]("read")
        out = server._read_resource("pyrite://entries/public-note", readable_kbs={PUBLIC})
        assert "zebra in the open" in out["contents"][0]["text"]

    def test_unscoped_resources_are_unchanged(self, env):
        server = env["server_for_tier"]("read")
        out = server._read_resource("pyrite://kbs")
        names = {k["name"] for k in json.loads(out["contents"][0]["text"])}
        assert names == {PUBLIC, PRIVATE}


class TestOmittingTheKBNameIsNotAWayIn:
    """Found while implementing, beyond the spike's inventory.

    The spike classified tools by whether they *declare* a KB parameter and
    counted 63 of 70 as covered. But the schemas make `kb_name` **optional**
    on most of them, and with it omitted they span the whole index. Refusing
    a named private KB does nothing about a caller who simply does not name
    one. Declaring a KB parameter is not the same as being scoped by one.
    """

    def test_kb_read_body_cannot_be_used_to_read_a_private_body(self, env):
        # The most direct read there was: it returns body text and nothing else.
        out = _call(env, "peer", "kb_read_body", {"entry_id": "secret-note"})
        assert "zebra behind the wall" not in json.dumps(out)
        assert out["error_code"] == "NOT_FOUND"

    def test_kb_read_body_still_works_on_a_readable_entry(self, env):
        out = _call(env, "peer", "kb_read_body", {"entry_id": "public-note"})
        assert out["body"] == "zebra in the open"

    def test_list_edge_types_does_not_enumerate_private_kbs(self, env):
        out = _call(env, "peer", "list_edge_types", {})
        assert PRIVATE not in json.dumps(out)

    @pytest.mark.parametrize(
        ("tool", "args"),
        [
            ("kb_find_by_status", {"status": "open"}),
            ("kb_find_by_location", {"location": "a"}),
            ("kb_find_by_assignee", {"assignee": "someone"}),
            ("kb_find_overdue", {}),
        ],
    )
    def test_the_finders_return_no_private_rows(self, env, tool, args):
        out = _call(env, "peer", tool, args)
        entries = out.get("entries", [])
        assert all(e.get("kb_name") == PUBLIC for e in entries), (
            f"{tool} returned rows from a KB the caller may not read: {out}"
        )
        assert out["count"] == len(entries), "count must be computed after filtering"

    def test_a_tool_that_cannot_filter_is_refused_not_served(self, env):
        """Fail closed, against a set of tools that is deliberately shrinking.

        The six extensions have all landed (#223): social, zettelkasten,
        encyclopedia, cascade, software-kb and journalism-investigation read
        through the readable set now. The example here comes from the tools
        that keep the fail-closed listing -- journalism-investigation's
        write-path tools, which take no readable set (a caller who may write
        a KB can read it, so the tier guard covers the read). A tool that
        takes no readable set is refused for a scoped caller, not served;
        when one of these learns to filter, this test moves on again.
        """
        server = env["server_for_tier"]("write")
        if "investigation_bulk_edges" not in server.tools:
            pytest.skip("journalism-investigation extension not installed")
        out = server._dispatch_tool(
            "investigation_bulk_edges", {}, client_id="peer", readable_kbs={PUBLIC}
        )
        assert out["error_code"] == "NOT_FOUND"

    def test_the_same_tool_still_works_for_an_unscoped_caller(self, env):
        server = env["server_for_tier"]("write")
        if "investigation_bulk_edges" not in server.tools:
            pytest.skip("journalism-investigation extension not installed")
        out = server._dispatch_tool("investigation_bulk_edges", {}, client_id="operator")
        assert out.get("error_code") != "NOT_FOUND"


class TestPromptsAreScoped:
    """A third KB-content surface, found while wiring the other two and not
    among the spike's criteria: `prompts/get` embeds whole entries -- bodies
    included -- in the prompt text it hands back, and reads across every KB.
    """

    def test_summarize_entry_will_not_summarise_a_private_entry(self, env):
        server = env["server_for_tier"]("read")
        out = server._get_prompt(
            "summarize_entry", {"entry_id": "secret-note"}, readable_kbs={PUBLIC}
        )
        assert "zebra behind the wall" not in json.dumps(out)

    def test_summarize_entry_still_works_on_a_readable_entry(self, env):
        server = env["server_for_tier"]("read")
        out = server._get_prompt(
            "summarize_entry", {"entry_id": "public-note"}, readable_kbs={PUBLIC}
        )
        assert "zebra in the open" in json.dumps(out)

    def test_find_connections_filters_both_entries(self, env):
        server = env["server_for_tier"]("read")
        out = server._get_prompt(
            "find_connections",
            {"entry_a": "public-note", "entry_b": "secret-note"},
            readable_kbs={PUBLIC},
        )
        blob = json.dumps(out)
        assert "zebra in the open" in blob
        assert "zebra behind the wall" not in blob

    def test_daily_briefing_omits_private_events(self, env):
        server = env["server_for_tier"]("read")
        out = server._get_prompt("daily_briefing", {"days": 100000}, readable_kbs={PUBLIC})
        blob = json.dumps(out)
        assert "secret-event" not in blob
        assert "a zebra was smuggled" not in blob

    def test_an_unscoped_caller_sees_everything(self, env):
        server = env["server_for_tier"]("read")
        out = server._get_prompt("summarize_entry", {"entry_id": "secret-note"})
        assert "zebra behind the wall" in json.dumps(out)


class TestTheResolverIsSharedWithREST:
    """Criterion 1: one rule, two callers. If MCP grew its own copy of the
    readable-set computation, the two would drift and a grant honoured by
    REST would be refused by MCP (or worse, the other way round)."""

    def test_mcp_and_rest_resolve_the_same_set(self, env):
        assert _readable_for(env, "peer") == {PUBLIC}
        assert _readable_for(env, "peer-granted") == {PUBLIC, PRIVATE}

    def test_a_global_admin_is_unscoped(self, env):
        assert _readable_for(env, "admin-user") is None

    def test_rest_readable_kbs_delegates_to_the_shared_helper(self, env):
        """`readable_kbs()` must be a thin wrapper, not a second copy."""
        import inspect

        from pyrite.server import api

        source = inspect.getsource(api.readable_kbs)
        assert "readable_kbs_for_user" in source, (
            "api.readable_kbs no longer delegates to the shared helper -- MCP "
            "and REST now have two implementations of one rule"
        )


class TestTheFindersReturnAFullPage:
    """#223: the four core finders narrow in SQL, so a scoped page is full.

    The handlers used to cut the page with `LIMIT` and drop unreadable rows
    afterwards, so a scoped caller asking for two could receive none while
    two readable rows existed below the cut. This fixture gives the private
    rows the later `updated_at`, which puts them first in the sort order --
    exactly the page that used to be cut before the readable rows arrived.
    """

    @pytest.fixture
    def finders_env(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            (tmp / PUBLIC).mkdir()
            (tmp / PRIVATE).mkdir()
            config = PyriteConfig(
                knowledge_bases=[
                    KBConfig(
                        name=PUBLIC, path=tmp / PUBLIC, kb_type="generic", default_role="read"
                    ),
                    KBConfig(
                        name=PRIVATE, path=tmp / PRIVATE, kb_type="generic", default_role="none"
                    ),
                ],
                settings=Settings(index_path=tmp / "index.db"),
            )
            db = PyriteDB(config.settings.index_path)
            for kb in (PUBLIC, PRIVATE):
                db.register_kb(kb, "generic", str(tmp / kb))
            # Raw inserts so `updated_at` is under the test's control: the
            # private rows sort first under `ORDER BY updated_at DESC`.
            for kb, updated in (
                (PRIVATE, "2026-06-01T00:00:00"),
                (PUBLIC, "2026-01-01T00:00:00"),
            ):
                for i in range(2):
                    db._raw_conn.execute(
                        "INSERT INTO entry (id, kb_name, entry_type, title, body, metadata,"
                        " importance, status, assignee, due_date, location,"
                        " created_at, updated_at)"
                        " VALUES (?, ?, 'note', ?, '', '{}', 5, 'open', 'agent:x',"
                        " '2026-01-01', 'Springfield', '2026-01-01T00:00:00', ?)",
                        (f"{kb}-{i}", kb, f"{kb} note {i}", updated),
                    )
            db._raw_conn.commit()
            server = PyriteMCPServer(config=config, tier="read")
            try:
                yield {"server": server}
            finally:
                server.close()
                db.close()

    @pytest.mark.parametrize(
        ("tool", "args"),
        [
            ("kb_find_by_status", {"status": "open"}),
            ("kb_find_by_assignee", {"assignee": "agent:x"}),
            ("kb_find_overdue", {}),
            ("kb_find_by_location", {"location": "Springfield"}),
        ],
    )
    def test_a_scoped_caller_gets_a_full_page(self, finders_env, tool, args):
        out = finders_env["server"]._dispatch_tool(
            tool, {**args, "limit": 2}, client_id="peer", readable_kbs={PUBLIC}
        )
        assert {e["id"] for e in out["entries"]} == {f"{PUBLIC}-0", f"{PUBLIC}-1"}, out
        assert out["count"] == 2
        assert PRIVATE not in json.dumps(out)
