"""PATCH refuses misspelt body keys before it can silently clear a field (#1221)."""

import json

import pytest

from pyrite.config import KBConfig
from tests._world import build_world


@pytest.fixture
def patch_world(tmp_path):
    world = build_world(tmp_path / "world", kbs=[KBConfig(name="demo", path=tmp_path / "kb")])
    try:
        path = world.add_entry(
            "demo",
            "---\nid: note\ntitle: Note\ntype: note\ncustom: kept\ntags: [one]\n---\nBody.\n",
        )
        yield world, path
    finally:
        world.close()


@pytest.mark.parametrize("extra", [{"vaule": "done"}, {"vaule": None, "unknown": False}])
@pytest.mark.parametrize("content_type", ["application/json", "application/problem+json"])
def test_patch_rejects_undeclared_keys_without_writing(patch_world, extra, content_type):
    world, path = patch_world
    before = path.read_bytes(), path.stat().st_mtime_ns, world.db.get_entry("note", "demo")
    response = world.client.patch(
        "/api/entries/note",
        content=json.dumps({"kb": "demo", "field": "custom", **extra}),
        headers={"content-type": content_type},
    )
    assert response.status_code == 400, response.text
    error = response.json()["detail"]
    assert error["code"] == "VALIDATION_FAILED", error
    for key in extra:
        assert key in error["message"], error
    assert (
        path.read_bytes(),
        path.stat().st_mtime_ns,
        world.db.get_entry("note", "demo"),
    ) == before


@pytest.mark.control(reason="an explicit null is a valid set, not a misspelt value")
def test_patch_accepts_explicit_null(patch_world):
    import yaml

    world, path = patch_world
    response = world.client.patch(
        "/api/entries/note", json={"kb": "demo", "field": "custom", "value": None}
    )
    assert response.status_code == 200, response.text
    assert response.json()["changed"] is True
    assert yaml.safe_load(path.read_text().split("---", 2)[1])["custom"] is None


@pytest.mark.control(reason="append remains a supported operation with the declared value key")
def test_patch_accepts_declared_append(patch_world):
    import yaml

    world, path = patch_world
    response = world.client.patch(
        "/api/entries/note", json={"kb": "demo", "field": "tags", "value": "two", "op": "append"}
    )
    assert response.status_code == 200, response.text
    assert yaml.safe_load(path.read_text().split("---", 2)[1])["tags"] == ["one", "two"]


@pytest.mark.parametrize("marker", [True, False])
def test_patch_accepts_transport_markers_for_non_body_fields(patch_world, marker):
    import yaml

    world, path = patch_world
    before_body = world.client.get("/api/entries/note", params={"kb": "demo"}).json()["body"]
    response = world.client.patch(
        "/api/entries/note",
        json={
            "kb": "demo",
            "field": "custom",
            "value": "changed",
            "body_truncated": marker,
            "body_length": 100,
            "body_offset": 0,
            "body_chunk_size": 10,
        },
    )
    assert response.status_code == 200, response.text
    frontmatter = yaml.safe_load(path.read_text().split("---", 2)[1])
    assert frontmatter["custom"] == "changed"
    assert (
        not {"body_truncated", "body_length", "body_offset", "body_chunk_size"} & frontmatter.keys()
    )
    assert (
        world.client.get("/api/entries/note", params={"kb": "demo"}).json()["body"] == before_body
    )
