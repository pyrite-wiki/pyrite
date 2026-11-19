"""REST must refuse fields it cannot write, before changing an entry."""

import os
from pathlib import Path

import pytest


@pytest.fixture
def write_world(tmp_path):
    from pyrite.config import KBConfig
    from tests._world import build_world

    world = build_world(tmp_path / "world", kbs=[KBConfig(name="demo", path=tmp_path / "kb")])
    try:
        path = world.add_entry("demo", "---\nid: note\ntitle: Note\ntype: note\n---\nOriginal.\n")
        yield world, path
    finally:
        world.close()


_SHAPES = [
    "declared",
    "status",
    "vaule",
    "several",
    "unknown_invalid",
    "metadata_k",
    "metadata_custom",
    "truncated",
    "non_json",
] + [
    f"{key}_{value}"
    for key in ("body_truncated", "body_length", "body_offset", "body_chunk_size")
    for value in ("false", "null")
]


@pytest.mark.parametrize("verb", ["POST", "PUT", "PATCH"])
@pytest.mark.parametrize("shape", _SHAPES)
def test_entry_write_shapes(verb, shape, write_world):
    import json

    from pyrite.services.body_bounds import MARKER_KEYS
    from pyrite.utils.frontmatter import load_frontmatter

    world, path = write_world
    root = path.parent
    before_files = {p: p.read_bytes() for p in root.rglob("*.md")}
    before_row = world.db.get_entry("note", "demo")
    payload = {"kb": "demo"}
    if verb == "POST":
        payload.update(title="New note", entry_type="note", body="Complete")
    elif verb == "PUT":
        payload.update(title="Changed", body="Complete")
    else:
        payload.update(field="body", value="Complete")
    unknown = {}
    if shape in ("status", "vaule"):
        unknown = {shape: "sent"}
    elif shape == "several":
        unknown = {"status": "sent", "vaule": None, "typo": False}
    elif shape == "unknown_invalid":
        unknown = {"typo": "sent"}
        payload.update({"op": "invalid"} if verb == "PATCH" else {"importance": 11})
    elif shape in ("metadata_k", "metadata_custom"):
        key = "k" if shape == "metadata_k" else "custom"
        if verb == "PATCH":
            payload.update(field=f"metadata.{key}", value="stored")
        else:
            payload["metadata"] = {key: "stored"}
    elif shape == "truncated":
        payload["body_truncated"] = True
    elif shape.endswith(("_false", "_null")):
        key, value = shape.rsplit("_", 1)
        payload[key] = False if value == "false" else None
    payload.update(unknown)
    url = "/api/entries" if verb == "POST" else "/api/entries/note"
    if shape == "non_json":
        response = world.client.request(
            verb, url, content=b"plain text", headers={"content-type": "text/plain"}
        )
        assert response.status_code == 400, response.text
        assert response.json()["detail"]["code"] == "INVALID_BODY"
    else:
        response = world.client.request(
            verb,
            url,
            content=json.dumps(payload),
            headers={"content-type": "application/problem+json"},
        )
        assert response.status_code == (400 if unknown or shape == "truncated" else 200), (
            response.text
        )
    if response.status_code != 200:
        if shape != "non_json":
            detail = response.json()["detail"]
            assert detail["code"] == "VALIDATION_FAILED"
            assert detail["retryable"] is False
            for key in unknown:
                assert key in detail["message"]
        assert {p: p.read_bytes() for p in root.rglob("*.md")} == before_files
        assert world.db.get_entry("note", "demo") == before_row
    else:
        if verb == "PUT":
            assert response.json()["ignored"] == []
        target = response.json()["id"] if verb == "POST" else "note"
        stored = world.client.get(f"/api/entries/{target}", params={"kb": "demo"}).json()
        if shape in ("metadata_k", "metadata_custom"):
            assert stored["metadata"][key] == "stored"
        else:
            assert stored["body"].strip() == "Complete"
        for file in root.rglob("*.md"):
            frontmatter, _ = load_frontmatter(file.read_text(encoding="utf-8"))
            assert not set(MARKER_KEYS) & frontmatter.keys()


@pytest.mark.skipif(os.name == "nt", reason="Real entry publication requires POSIX")
def test_declared_put_fields_and_create_metadata_keep_working(make_client):
    client, config, _ = make_client()
    kb = Path(config.knowledge_bases[0].path)
    (kb / "kb.yaml").write_text(
        "name: test-kb\ntypes:\n  note:\n    fields:\n      status: {type: select, options: [draft, accepted]}\n",
        encoding="utf-8",
    )
    created = client.post(
        "/api/entries",
        json={
            "kb": "test-kb",
            "title": "Example",
            "entry_type": "note",
            "body": "Body",
            "metadata": {"status": "accepted"},
        },
    )
    assert created.status_code == 200, created.text
    entry_id = created.json()["id"]
    original = client.get(f"/api/entries/{entry_id}", params={"kb": "test-kb"})
    assert original.status_code == 200
    assert original.json()["metadata"]["status"] == "accepted"
    result = client.put(
        f"/api/entries/{entry_id}",
        json={
            "kb": "test-kb",
            "title": "Updated",
            "body": "Updated body",
            "body_truncated": False,
            "tags": ["checked"],
            "metadata": {"status": "draft"},
        },
    )
    assert result.status_code == 200, result.text
    read = client.get(f"/api/entries/{entry_id}", params={"kb": "test-kb"})
    assert read.status_code == 200
    entry = read.json()
    assert entry["title"] == "Updated"
    assert entry["body"] == "Updated body"
    assert entry["tags"] == ["checked"]
    assert entry["metadata"]["status"] == "draft"


@pytest.mark.skipif(os.name == "nt", reason="Real entry publication requires POSIX")
def test_post_writes_declared_adr_status_from_metadata(make_client):
    client, config, _ = make_client()
    created = client.post(
        "/api/entries",
        json={
            "kb": "test-kb",
            "title": "Decision",
            "entry_type": "adr",
            "allow_undeclared": True,
            "metadata": {"status": "accepted"},
        },
    )
    assert created.status_code == 200, created.text
    from pyrite.utils.frontmatter import load_frontmatter

    path = next(Path(config.knowledge_bases[0].path).rglob("*decision*.md"))
    frontmatter, _ = load_frontmatter(path.read_text(encoding="utf-8"))
    assert frontmatter["status"] == "accepted"


@pytest.mark.parametrize("truncated", [False, True])
def test_put_read_transport_markers_are_not_entry_content(make_client, truncated):
    client, config, _ = make_client()
    created = client.post(
        "/api/entries",
        json={"kb": "test-kb", "title": "Transport test", "entry_type": "note", "body": "Original"},
    )
    assert created.status_code == 200, created.text
    entry_id = created.json()["id"]
    marker = {"body_truncated": truncated, "body_length": 8, "body_offset": 0, "body_chunk_size": 8}
    result = client.put(
        f"/api/entries/{entry_id}", json={"kb": "test-kb", "body": "Complete", **marker}
    )
    if truncated:
        assert result.status_code == 400, result.text
        assert result.json()["detail"]["code"] == "VALIDATION_FAILED"
    else:
        assert result.status_code == 200, result.text
    path = next(Path(config.knowledge_bases[0].path).rglob("*transport-test*.md"))
    from pyrite.utils.frontmatter import load_frontmatter

    frontmatter, body = load_frontmatter(path.read_text(encoding="utf-8"))
    assert not (set(marker) & set(frontmatter))
    assert body.strip() == ("Original" if truncated else "Complete")
