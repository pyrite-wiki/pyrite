"""KB counts and health agree across the real user-facing surfaces."""

import json

import pytest

from pyrite.config import KBConfig
from pyrite.services.index_health import health_verdict
from pyrite.services.kb_service import KBService
from pyrite.storage.index import IndexManager
from tests._world import build_world


@pytest.fixture
def world(tmp_path):
    result = build_world(tmp_path, kbs=[KBConfig(name="notes", path=tmp_path / "kb")])
    try:
        yield result
    finally:
        result.close()


def assert_counts(world, server, expected):
    assert world.db.get_kb_stats("notes")["entry_count"] == expected
    assert KBService(world.config, world.db).list_kbs()[0]["entries"] == expected
    assert server.registry.get_kb("notes")["entries"] == expected
    assert world.client.get("/api/kbs/notes").json()["entries"] == expected
    for stats in (
        server._dispatch_tool("kb_stats", {}),
        world.client.get("/api/stats").json(),
    ):
        assert stats["kbs"]["notes"]["entry_count"] == expected
        assert stats["kbs"]["notes"]["actual_count"] == expected
    expected_health = not health_verdict(
        IndexManager(world.db, world.config).check_health("notes")
    )["is_unhealthy"]
    for health in (
        server._dispatch_tool("kb_registry_health", {"name": "notes"}),
        world.client.get("/api/kbs/notes/health").json(),
        json.loads(world.cli("kb", "health", "-k", "notes").stdout),
    ):
        assert health["entry_count"] == expected
        assert health["healthy"] == expected_health


@pytest.mark.parametrize("surface", ["mcp", "cli", "rest"])
def test_counts_follow_creates_updates_and_deletes(world, surface):
    server = world.mcp()
    for number in range(2):
        title = f"Note {number}"
        if surface == "mcp":
            result = server._dispatch_tool(
                "kb_create", {"kb_name": "notes", "title": title, "body": "Body."}
            )
            assert result["created"], result
        elif surface == "cli":
            result = world.cli("create", "-k", "notes", "--title", title, "-b", "Body.")
            assert result.exit_code == 0, result.output
        else:
            result = world.client.post(
                "/api/entries", json={"kb": "notes", "title": title, "body": "Body."}
            )
            assert result.status_code == 200, result.text
        assert_counts(world, server, number + 1)

    entry_id = world.db.get_entries_for_indexing("notes")[0]["id"]
    if surface == "mcp":
        updated = server._dispatch_tool(
            "kb_update", {"kb_name": "notes", "entry_id": entry_id, "title": "Changed"}
        )
        assert updated["updated"], updated
    elif surface == "cli":
        updated = world.cli("update", entry_id, "-k", "notes", "--title", "Changed")
        assert updated.exit_code == 0, updated.output
    else:
        updated = world.client.patch(
            f"/api/entries/{entry_id}", json={"kb": "notes", "field": "title", "value": "Changed"}
        )
        assert updated.status_code == 200, updated.text
    assert_counts(world, server, 2)

    if surface == "mcp":
        deleted = server._dispatch_tool("kb_delete", {"kb_name": "notes", "entry_id": entry_id})
        assert deleted["deleted"], deleted
    elif surface == "cli":
        deleted = world.cli("delete", entry_id, "-k", "notes", "--force")
        assert deleted.exit_code == 0, deleted.output
    else:
        deleted = world.client.delete(f"/api/entries/{entry_id}?kb=notes")
        assert deleted.status_code == 200, deleted.text
    assert_counts(world, server, 1)


def test_health_detects_an_unindexed_file_before_sync(world):
    (world.kb_path("notes") / "new.md").write_text(
        "---\nid: new\ntype: note\ntitle: New\n---\nBody.\n"
    )
    server = world.mcp()
    assert_counts(world, server, 0)
    assert server.registry.health_kb("notes")["healthy"] is False
    result = world.cli("index", "sync", "--no-embed")
    assert result.exit_code == 0, result.output
    assert_counts(world, server, 1)


def test_non_entry_markdown_does_not_make_the_index_unhealthy(world):
    for name in ("README.md", "_index.md", "guide.md"):
        (world.kb_path("notes") / name).write_text("Documentation without frontmatter.\n")
    server = world.mcp()
    assert_counts(world, server, 0)
    assert server.registry.health_kb("notes")["healthy"] is True


@pytest.mark.control(reason="empty and missing KB folders keep their existing health verdict")
def test_empty_and_missing_kb(world):
    server = world.mcp()
    assert_counts(world, server, 0)
    assert server.registry.health_kb("notes")["healthy"] is True
    world.kb_path("notes").rmdir()
    health = server.registry.health_kb("notes")
    assert health["path_exists"] is False
    assert health["healthy"] is False
