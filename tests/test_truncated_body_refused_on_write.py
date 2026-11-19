"""ADR-0034 rule 2: a truncated body is never valid input to a write.

A bounded read hands an agent the first `body_chunk_size` characters of a body
plus `body_truncated: true`. An agent that edits what it received and writes the
entry back replaces the whole body with that chunk -- silent data loss produced
by the safety feature. Every write surface that can receive a body refuses an
input carrying the marker.

The round-trip tests are the ones that matter: read over MCP, feed exactly what
came back into the write path, assert the write is refused AND the stored body
is unchanged. The negative tests matter just as much -- a guard that refuses
everything passes a naive test and breaks the product.
"""

import contextlib
import json
import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")

from pyrite.services.body_bounds import DEFAULT_BODY_CHUNK, MAX_BODY_CHUNK

from tests._world import mcp_server as _make_mcp_server

#: Longer than the per-body ceiling, so every read of it truncates no matter
#: what `body_limit` the caller asks for -- ADR-0034 (i) clamps the limit to
#: MAX_BODY_CHUNK, so a body merely longer than the DEFAULT chunk could be
#: fetched whole by a caller passing a bigger limit, and would not be marked.
BIG = "A" * (MAX_BODY_CHUNK + 5000)


# ---------------------------------------------------------------------------
# MCP harness -- the proven helper from test_mcp_server.py, one generic KB.
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _mcp(tier="admin"):
    """An admin-tier MCP server over one writable generic KB."""
    with _make_mcp_server([{"name": "test", "kb_type": "generic"}], tier=tier) as ctx:
        yield ctx["server"]


def _create(server, title, body):
    """Create an entry and return its id."""
    res = server._dispatch_tool(
        "kb_create",
        {"kb_name": "test", "entry_type": "note", "title": title, "body": body},
    )
    assert res.get("created"), res
    return res["entry_id"]


def _stored_body(server, entry_id):
    """The full body on disk, reassembled the way an agent is told to.

    No single read can return it: ADR-0034 (i) clamps any `body_limit` to
    MAX_BODY_CHUNK, so this pages with `body_offset` until `has_more` is
    false -- exactly the continuation the refusal message names. That makes
    this helper a live check that the advice we give actually works.
    """
    parts: list[str] = []
    offset = 0
    while True:
        res = server._dispatch_tool(
            "kb_read_body",
            {"entry_id": entry_id, "kb_name": "test", "body_offset": offset},
        )
        assert "error" not in res, res
        parts.append(res["body"])
        if not res["has_more"]:
            return "".join(parts)
        advanced = res["body_chunk_size"]
        assert advanced > 0, f"has_more with no progress at offset {offset}: {res}"
        offset += advanced


def _assert_refusal(res):
    """The MCP flat error envelope for a refused truncated write.

    TruncatedBodyError inherits the base ValidationError's code, which is
    VALIDATION_FAILED (conductor decision, ADR-0037 theme 2 fix round 1: the
    documented write-pipeline spelling, not the central handler's
    under-visited VALIDATION_ERROR) -- unchanged from dev, no
    legacy_error_code.
    """
    assert res.get("error_code") == "VALIDATION_FAILED", res
    assert "legacy_error_code" not in res, res
    assert res.get("retryable") is False, res
    msg = f"{res.get('error', '')} {res.get('suggestion', '')}"
    assert "body_truncated" in msg, msg
    # The error must teach: name the way to get the whole body.
    assert "kb_read_body" in msg or "body_offset" in msg, msg


# ---------------------------------------------------------------------------
# The round trip -- the test that matters most
# ---------------------------------------------------------------------------


def test_mcp_round_trip_kb_get_into_kb_update_is_refused_and_body_survives():
    """Read truncated over kb_get, write it straight back: refused, body intact."""
    with _mcp() as server:
        entry_id = _create(server, "Big Entry", BIG)

        got = server._dispatch_tool("kb_get", {"entry_id": entry_id, "kb_name": "test"})
        entry = got["entry"]
        assert entry["body_truncated"] is True
        assert len(entry["body"]) == DEFAULT_BODY_CHUNK

        # Exactly what came back, plus the id -- the naive agent round trip.
        res = server._dispatch_tool("kb_update", {**entry, "entry_id": entry_id})

        _assert_refusal(res)
        assert _stored_body(server, entry_id) == BIG


def test_mcp_round_trip_kb_batch_read_into_kb_create_is_refused():
    """The marker arriving via kb_batch_read is refused by kb_create too."""
    with _mcp() as server:
        entry_id = _create(server, "Batch Source", BIG)

        got = server._dispatch_tool(
            "kb_batch_read", {"entries": [{"entry_id": entry_id, "kb_name": "test"}]}
        )
        entry = got["entries"][0]
        assert entry["body_truncated"] is True

        res = server._dispatch_tool(
            "kb_create",
            {
                "kb_name": "test",
                "entry_type": "note",
                "title": "Copy of Batch Source",
                "body": entry["body"],
                "body_truncated": entry["body_truncated"],
                "body_length": entry["body_length"],
            },
        )

        _assert_refusal(res)
        listed = server._dispatch_tool("kb_list_entries", {"kb_name": "test"})
        titles = [e.get("title") for e in listed.get("entries", [])]
        assert "Copy of Batch Source" not in titles


# ---------------------------------------------------------------------------
# The seam between ADR-0034 (i)'s read-side bounds and (ii)'s write refusal.
# These two landed on separate branches; the only place they meet is the
# marker, so these pin that they agree about it.
# ---------------------------------------------------------------------------


def test_budget_exhausted_entry_is_refused_on_write():
    """An entry emptied by (i)'s per-RESPONSE budget still refuses on write.

    `fill_budget` spends PYRITE_BODY_RESPONSE_BUDGET in request order, so a
    later entry can come back with `body: ""` and the marker. That is the most
    dangerous shape on this branch: writing it back replaces a whole body with
    the EMPTY STRING. The guard must treat an empty marked body as a body --
    `has_body` tests `is not None`, not truthiness, for exactly this reason.
    """
    from pyrite.services.body_bounds import BODY_RESPONSE_BUDGET

    with _mcp() as server:
        # Each entry takes the DEFAULT chunk (no body_limit passed), so it
        # takes budget/default entries to spend the budget -- plus two to be
        # sure at least one arrives with nothing left.
        n = (BODY_RESPONSE_BUDGET // DEFAULT_BODY_CHUNK) + 2
        ids = [_create(server, f"Budget Entry {i}", BIG) for i in range(n)]

        got = server._dispatch_tool(
            "kb_batch_read",
            {"entries": [{"entry_id": i, "kb_name": "test"} for i in ids]},
        )
        starved = [e for e in got["entries"] if e.get("body") == ""]
        assert starved, f"budget never ran out across {n} entries: {got}"
        entry = starved[0]
        assert entry["body_truncated"] is True
        assert entry["body_length"] > 0, "a starved entry still reports its true length"

        res = server._dispatch_tool("kb_update", {**entry, "entry_id": entry["id"]})

        _assert_refusal(res)
        assert _stored_body(server, entry["id"]) == BIG


def test_fields_projected_marker_is_still_refused_on_write():
    """(i) keeps the marker through a `fields` projection; (ii) must catch it.

    ADR-0034 rule 2 requires a projection that kept `body` to keep the marker
    keys, so a caller cannot be handed a slice it cannot tell is a slice. The
    write side has to refuse that projected shape -- otherwise the read side's
    promise is kept and the write side's is not.
    """
    with _mcp() as server:
        entry_id = _create(server, "Projected Source", BIG)

        got = server._dispatch_tool(
            "kb_batch_read",
            {
                "entries": [{"entry_id": entry_id, "kb_name": "test"}],
                "fields": ["title", "body"],
            },
        )
        entry = got["entries"][0]
        assert entry["body_truncated"] is True, f"(i) must keep the marker: {entry}"

        res = server._dispatch_tool("kb_update", {**entry, "entry_id": entry_id})

        _assert_refusal(res)
        assert _stored_body(server, entry_id) == BIG


# ---------------------------------------------------------------------------
# MCP regimes: true / false / absent / "true" / marker without a body
# ---------------------------------------------------------------------------


def test_mcp_update_without_marker_still_works():
    """A normal write is unaffected -- the guard must not refuse everything."""
    with _mcp() as server:
        entry_id = _create(server, "Normal Entry", "short body")

        res = server._dispatch_tool(
            "kb_update", {"entry_id": entry_id, "kb_name": "test", "body": "rewritten body"}
        )

        assert res.get("updated") is True, res
        assert _stored_body(server, entry_id) == "rewritten body"


def test_mcp_update_with_marker_false_is_allowed():
    """body_truncated: false means nothing was truncated -- allow the write."""
    with _mcp() as server:
        entry_id = _create(server, "False Marker", "short body")

        res = server._dispatch_tool(
            "kb_update",
            {
                "entry_id": entry_id,
                "kb_name": "test",
                "body": "rewritten body",
                "body_truncated": False,
            },
        )

        assert res.get("updated") is True, res
        assert _stored_body(server, entry_id) == "rewritten body"


def test_mcp_marker_as_string_true_is_refused():
    """A JSON-ish client that sends the string "true" is still refused."""
    with _mcp() as server:
        entry_id = _create(server, "String Marker", BIG)

        res = server._dispatch_tool(
            "kb_update",
            {
                "entry_id": entry_id,
                "kb_name": "test",
                "body": "A" * 8000,
                "body_truncated": "true",
            },
        )

        _assert_refusal(res)
        assert _stored_body(server, entry_id) == BIG


def test_mcp_marker_without_a_body_is_allowed():
    """A metadata-only update carrying the marker writes no body -- allow it."""
    with _mcp() as server:
        entry_id = _create(server, "Metadata Only", BIG)

        res = server._dispatch_tool(
            "kb_update",
            {
                "entry_id": entry_id,
                "kb_name": "test",
                "tags": ["reviewed"],
                "body_truncated": True,
            },
        )

        assert res.get("updated") is True, res
        assert _stored_body(server, entry_id) == BIG


def test_mcp_marker_nested_in_metadata_is_refused():
    """The marker relayed inside `metadata` alongside a body is still refused."""
    with _mcp() as server:
        entry_id = _create(server, "Nested Marker", BIG)

        res = server._dispatch_tool(
            "kb_update",
            {
                "entry_id": entry_id,
                "kb_name": "test",
                "body": "A" * 8000,
                "metadata": {"body_truncated": True, "body_length": 20000},
            },
        )

        _assert_refusal(res)
        assert _stored_body(server, entry_id) == BIG


def test_mcp_body_exactly_chunk_size_without_marker_is_allowed():
    """A body whose length equals the chunk size, unmarked, writes normally."""
    with _mcp() as server:
        entry_id = _create(server, "Exact Chunk", "seed")
        exact = "B" * DEFAULT_BODY_CHUNK

        res = server._dispatch_tool(
            "kb_update", {"entry_id": entry_id, "kb_name": "test", "body": exact}
        )

        assert res.get("updated") is True, res
        assert _stored_body(server, entry_id) == exact


# ---------------------------------------------------------------------------
# MCP: kb_bulk_create -- per-item refusal, per its existing contract
# ---------------------------------------------------------------------------


def test_mcp_bulk_create_refuses_only_the_marked_item():
    """One marked item of three is refused per-item; the others are created."""
    with _mcp() as server:
        res = server._dispatch_tool(
            "kb_bulk_create",
            {
                "kb_name": "test",
                "entries": [
                    {"entry_type": "note", "title": "Bulk Clean One", "body": "fine"},
                    {
                        "entry_type": "note",
                        "title": "Bulk Marked",
                        "body": "A" * 8000,
                        "body_truncated": True,
                        "body_length": 20000,
                    },
                    {"entry_type": "note", "title": "Bulk Clean Two", "body": "also fine"},
                ],
            },
        )

        assert res["total"] == 3, res
        assert res["created"] == 2, res
        assert res["failed"] == 1, res
        results = res["results"]
        assert results[0]["created"] is True
        assert results[2]["created"] is True
        assert results[1]["created"] is False
        assert "body_truncated" in results[1]["error"]

        listed = server._dispatch_tool("kb_list_entries", {"kb_name": "test", "limit": 50})
        titles = [e.get("title") for e in listed.get("entries", [])]
        assert "Bulk Clean One" in titles
        assert "Bulk Clean Two" in titles
        assert "Bulk Marked" not in titles


def test_mcp_bulk_create_without_markers_is_unaffected():
    """A clean bulk create still creates every item."""
    with _mcp() as server:
        res = server._dispatch_tool(
            "kb_bulk_create",
            {
                "kb_name": "test",
                "entries": [
                    {"entry_type": "note", "title": "Clean A", "body": "a"},
                    {"entry_type": "note", "title": "Clean B", "body": "b"},
                ],
            },
        )
        assert res["created"] == 2, res
        assert res["failed"] == 0, res


def test_mcp_task_create_with_marker_is_refused():
    """Every write tool that takes a body is covered, not just the kb_* three."""
    with _mcp() as server:
        res = server._dispatch_tool(
            "task_create",
            {
                "kb_name": "test",
                "title": "Truncated Task",
                "body": "A" * 8000,
                "body_truncated": True,
            },
        )
        _assert_refusal(res)


def test_mcp_allowed_false_marker_is_not_persisted_as_frontmatter():
    """`body_truncated: false` is read transport, not entry content.

    kb_create forwards every unrecognised argument into the entry's
    frontmatter, so an allowed false marker would otherwise be written to disk
    and come back on the next read -- where it would then be echoed into a
    write and start tripping the guard.
    """
    with _mcp() as server:
        res = server._dispatch_tool(
            "kb_create",
            {
                "kb_name": "test",
                "entry_type": "note",
                "title": "False Marker Create",
                "body": "short",
                "body_truncated": False,
                "body_length": 5,
            },
        )
        assert res.get("created") is True, res

        got = server._dispatch_tool("kb_get", {"entry_id": res["entry_id"], "kb_name": "test"})[
            "entry"
        ]
        assert "body_truncated" not in got, got
        assert got.get("metadata", {}).get("body_truncated") is None, got


def test_mcp_bulk_create_allowed_false_marker_is_not_persisted():
    """The same, on the bulk path's own extra-kwargs forwarding."""
    with _mcp() as server:
        res = server._dispatch_tool(
            "kb_bulk_create",
            {
                "kb_name": "test",
                "entries": [
                    {
                        "entry_type": "note",
                        "title": "Bulk False Marker",
                        "body": "short",
                        "body_truncated": False,
                    }
                ],
            },
        )
        assert res["created"] == 1, res

        got = server._dispatch_tool(
            "kb_get", {"entry_id": res["results"][0]["entry_id"], "kb_name": "test"}
        )["entry"]
        assert "body_truncated" not in got, got
        assert got.get("metadata", {}).get("body_truncated") is None, got


def test_mcp_read_tools_are_not_guarded():
    """The marker is a legal *read* parameter shape; reads must not be refused."""
    with _mcp() as server:
        entry_id = _create(server, "Readable", BIG)
        res = server._dispatch_tool(
            "kb_get", {"entry_id": entry_id, "kb_name": "test", "body_truncated": True}
        )
        assert "error" not in res, res


# ---------------------------------------------------------------------------
# REST -- must go through TestClient: the pydantic models would otherwise
# drop the key before any check could see it.
# ---------------------------------------------------------------------------


def _rest_refusal(resp):
    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "VALIDATION_FAILED", detail
    msg = f"{detail.get('message', '')} {detail.get('hint', '')}"
    assert "body_truncated" in msg, msg
    assert "body_offset" in msg or "kb_read_body" in msg, msg


def test_rest_post_entries_refuses_marked_body(rest_api_env):
    client = rest_api_env["client"]
    kb = rest_api_env["events_kb"].name

    resp = client.post(
        "/api/entries",
        json={
            "kb": kb,
            "entry_type": "note",
            "title": "Truncated REST Create",
            "body": "A" * 8000,
            "body_truncated": True,
            "body_length": 20000,
        },
    )

    _rest_refusal(resp)
    listing = client.get("/api/entries", params={"kb": kb, "limit": 200})
    titles = [e["title"] for e in listing.json()["entries"]]
    assert "Truncated REST Create" not in titles


def test_rest_put_entries_refuses_marked_body_and_body_survives(rest_api_env, sample_events):
    client = rest_api_env["client"]
    kb = rest_api_env["events_kb"].name
    entry_id = sample_events[0].id

    before = client.get(f"/api/entries/{entry_id}", params={"kb": kb}).json()["body"]

    resp = client.put(
        f"/api/entries/{entry_id}",
        json={"kb": kb, "body": "A" * 8000, "body_truncated": True},
    )

    _rest_refusal(resp)
    after = client.get(f"/api/entries/{entry_id}", params={"kb": kb}).json()["body"]
    assert after == before


@pytest.mark.parametrize(
    "content_type",
    [
        "Application/JSON",
        "APPLICATION/JSON",
        "application/vnd.api+json",
        "application/json;charset=UTF-8",
    ],
)
def test_rest_put_refuses_under_every_content_type_fastapi_parses(
    rest_api_env, sample_events, content_type
):
    """The guard's JSON test must be no narrower than FastAPI's own.

    Found by the cold read. The guard compared the media type to the exact
    lower-case string `application/json`, while FastAPI parses whenever the
    maintype is `application` and the subtype is `json` or ends `+json`,
    lower-cased first. So `Content-Type: Application/JSON` -- legal, media
    types are case-insensitive (RFC 9110 section 8.3) -- was parsed by
    FastAPI, skipped by the guard, and **written**: a whole entry replaced by
    the fragment, which is the exact loss rule 2 exists to prevent.

    `TestClient(json=...)` always emits lower-case `application/json`, which
    is why no existing test could see this. These send the header explicitly.
    """
    client = rest_api_env["client"]
    kb = rest_api_env["events_kb"].name
    entry_id = sample_events[0].id

    before = client.get(f"/api/entries/{entry_id}", params={"kb": kb}).json()["body"]

    resp = client.put(
        f"/api/entries/{entry_id}",
        content=json.dumps({"kb": kb, "body": "A" * 8000, "body_truncated": True}),
        headers={"content-type": content_type},
    )

    _rest_refusal(resp)
    after = client.get(f"/api/entries/{entry_id}", params={"kb": kb}).json()["body"]
    assert after == before, f"body was overwritten via Content-Type: {content_type}"


def test_rest_put_without_marker_still_works(rest_api_env, sample_events):
    """A normal REST update is unaffected."""
    client = rest_api_env["client"]
    kb = rest_api_env["events_kb"].name
    entry_id = sample_events[0].id

    resp = client.put(
        f"/api/entries/{entry_id}",
        json={"kb": kb, "body": "a perfectly ordinary rewritten body"},
    )

    assert resp.status_code == 200, resp.text
    after = client.get(f"/api/entries/{entry_id}", params={"kb": kb}).json()["body"]
    assert after == "a perfectly ordinary rewritten body"


def test_rest_put_with_marker_false_is_allowed(rest_api_env, sample_events):
    client = rest_api_env["client"]
    kb = rest_api_env["events_kb"].name
    entry_id = sample_events[0].id

    resp = client.put(
        f"/api/entries/{entry_id}",
        json={"kb": kb, "body": "written with an explicit false marker", "body_truncated": False},
    )

    assert resp.status_code == 200, resp.text


def test_rest_patch_body_field_refuses_marked_body(rest_api_env, sample_events):
    """PATCH sets one field by name -- `field: "body"` is a body write too."""
    client = rest_api_env["client"]
    kb = rest_api_env["events_kb"].name
    entry_id = sample_events[0].id

    before = client.get(f"/api/entries/{entry_id}", params={"kb": kb}).json()["body"]

    resp = client.patch(
        f"/api/entries/{entry_id}",
        json={"kb": kb, "field": "body", "value": "A" * 8000, "body_truncated": True},
    )

    _rest_refusal(resp)
    after = client.get(f"/api/entries/{entry_id}", params={"kb": kb}).json()["body"]
    assert after == before


def test_rest_patch_non_body_field_with_marker_is_allowed(rest_api_env, sample_events):
    """PATCH of a non-body field writes no body -- the marker is inert."""
    client = rest_api_env["client"]
    kb = rest_api_env["events_kb"].name
    entry_id = sample_events[0].id

    resp = client.patch(
        f"/api/entries/{entry_id}",
        json={"kb": kb, "field": "importance", "value": "7", "body_truncated": True},
    )

    assert resp.status_code == 200, resp.text


def test_rest_import_refuses_marked_entry_and_imports_the_rest(rest_api_env, tmp_path):
    """POST /entries/import refuses the marked item, imports the clean ones."""
    import json

    client = rest_api_env["client"]
    kb = rest_api_env["events_kb"].name

    payload = [
        {"title": "Import Clean", "entry_type": "note", "body": "fine"},
        {
            "title": "Import Marked",
            "entry_type": "note",
            "body": "A" * 8000,
            "body_truncated": True,
            "body_length": 20000,
        },
    ]
    upload = tmp_path / "import.json"
    upload.write_text(json.dumps(payload))

    with upload.open("rb") as fh:
        resp = client.post(
            "/api/entries/import",
            params={"kb": kb},
            files={"file": ("import.json", fh, "application/json")},
        )

    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["imported"] == 1, result
    assert result["errors"] == 1, result
    assert "body_truncated" in result["error_details"][0]["error"], result

    listing = client.get("/api/entries", params={"kb": kb, "limit": 200})
    titles = [e["title"] for e in listing.json()["entries"]]
    assert "Import Clean" in titles
    assert "Import Marked" not in titles
