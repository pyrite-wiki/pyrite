"""Deletion reports the readable links it leaves dangling without editing them."""

import json

import pytest

from pyrite.config import AuthConfig, KBConfig
from pyrite.services.auth_service import AuthService
from pyrite.services.kb_service import KBService
from tests._world import build_world
from tests.auth_seed import SESSION_COOKIE, seed_user, sign_in


def make_world(tmp_path, *, scoped=False, linked=True, target_role="write"):
    world = build_world(
        tmp_path,
        kbs=[
            KBConfig(name=n, path=tmp_path / n, default_role="none")
            for n in ("target", "readable", "hidden")
        ],
        settings={"auth": AuthConfig(enabled=scoped)},
    )
    world.add_entry("target", "---\nid: victim\ntype: note\ntitle: Victim\n---\n\n[[victim]]\n")
    if linked:
        world.add_entry("target", "---\nid: wiki\ntype: note\ntitle: Wiki\n---\n\n[[victim]]\n")
        world.add_entry(
            "target",
            "---\nid: front\ntype: note\ntitle: Frontmatter\nlinks:\n  - target: victim\n    relation: references\n---\n\nBody.\n",
        )
        for kb, entry_id in (("readable", "cross"), ("hidden", "private")):
            world.add_entry(
                kb,
                f"---\nid: {entry_id}\ntype: note\ntitle: {entry_id}\n---\n\n[[target:victim]]\n",
            )
    if scoped:
        admin = seed_user(world.db, "admin", role="admin")
        auth = AuthService(world.db, world.config.settings.auth)
        user = auth.register("writer", "password123")
        for kb, role in (("target", target_role), ("readable", "read")):
            auth.grant_kb_permission(user["id"], kb, role, granted_by=admin["id"])
        world.client.cookies.set(SESSION_COOKIE, sign_in(world.app, "writer"))
    return world


def delete(world, surface, *, scoped=False):
    if surface == "cli":
        result = world.cli("delete", "victim", "-k", "target", "--force", "--format", "json")
        assert result.exit_code == 0, result.output
        return json.loads(result.stdout)
    if surface == "mcp":
        result = world.mcp()._dispatch_tool(
            "kb_delete",
            {"entry_id": "victim", "kb_name": "target"},
            readable_kbs={"target", "readable"} if scoped else None,
            writable_kbs={"target"} if scoped else None,
        )
        assert result.get("deleted") is True, result
        return result
    result = world.client.delete("/api/entries/victim?kb=target")
    assert result.status_code == 200, result.text
    return result.json()


@pytest.mark.parametrize("surface", ["cli", "mcp", "rest"])
def test_delete_lists_inbound_links_and_preserves_the_linkers(tmp_path, surface):
    world = make_world(tmp_path)
    try:
        before = {p: p.read_bytes() for p in tmp_path.rglob("*.md") if p.name != "victim.md"}
        result = delete(world, surface)
        assert result["deleted"] is True
        assert result["inbound_link_count"] == 4
        assert {(r["kb_name"], r["id"]) for r in result["inbound_links"]} == {
            ("target", "wiki"),
            ("target", "front"),
            ("readable", "cross"),
            ("hidden", "private"),
        }
        assert result["warnings"]
        assert not (world.kb_path("target") / "victim.md").exists()
        assert all(p.read_bytes() == text for p, text in before.items())
    finally:
        world.close()


@pytest.mark.parametrize("surface", ["mcp", "rest"])
def test_delete_never_lists_or_counts_a_private_backlink(tmp_path, surface):
    world = make_world(tmp_path, scoped=True)
    try:
        result = delete(world, surface, scoped=True)
        assert result["inbound_link_count"] == 3
        assert {(r["kb_name"], r["id"]) for r in result["inbound_links"]} == {
            ("target", "wiki"),
            ("target", "front"),
            ("readable", "cross"),
        }
        assert "private" not in json.dumps(result) and "hidden" not in json.dumps(result)
    finally:
        world.close()


@pytest.mark.parametrize("surface", ["cli", "mcp", "rest"])
def test_a_self_link_does_not_count_as_a_dangling_link(tmp_path, surface):
    world = make_world(tmp_path, linked=False)
    try:
        result = delete(world, surface)
        assert result["inbound_links"] == []
        assert result["inbound_link_count"] == 0
        assert result["warnings"] == []
    finally:
        world.close()


@pytest.mark.control(reason="read access still does not authorize deletion")
def test_delete_still_requires_write_access(tmp_path):
    world = make_world(tmp_path, scoped=True, target_role="read")
    try:
        result = world.client.delete("/api/entries/victim?kb=target")
        assert result.status_code == 403, result.text
        assert (world.kb_path("target") / "victim.md").exists()
    finally:
        world.close()


def test_non_terminal_delete_explains_how_to_confirm(tmp_path):
    world = make_world(tmp_path)
    try:
        result = world.cli("delete", "victim", "-k", "target", "--format", "json")
        assert result.exit_code != 0
        body = json.loads(result.stdout)
        assert body["error_code"] == "USAGE_ERROR"
        assert "--force" in body["suggestion"]
        assert (world.kb_path("target") / "victim.md").is_file()
    finally:
        world.close()


def test_service_requires_the_read_scope_before_it_can_delete(tmp_path):
    world = make_world(tmp_path)
    try:
        with pytest.raises(TypeError, match="readable_kbs"):
            KBService(world.config, world.db).delete_entry("victim", "target")
        assert (world.kb_path("target") / "victim.md").is_file()
    finally:
        world.close()


def test_rich_delete_names_the_dangling_links(tmp_path):
    world = make_world(tmp_path)
    try:
        result = world.cli("delete", "victim", "-k", "target", "--force")
        assert result.exit_code == 0, result.output
        assert "4 indexed inbound links" in result.stdout
        assert "target:wiki" in result.stdout and "readable:cross" in result.stdout
    finally:
        world.close()


@pytest.mark.parametrize("surface", ["cli", "mcp", "rest"])
def test_link_report_uses_the_index_snapshot(tmp_path, surface):
    world = make_world(tmp_path, linked=False)
    try:
        path = world.kb_path("readable") / "new.md"
        text = "---\nid: new\ntype: note\ntitle: New\n---\n\n[[target:victim]]\n"
        path.write_text(text)
        result = delete(world, surface)
        assert result["inbound_links"] == []
        assert result["inbound_link_count"] == 0
        assert path.read_text() == text
    finally:
        world.close()
