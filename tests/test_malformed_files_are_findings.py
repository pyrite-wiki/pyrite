"""Validation reports files that cannot enter the index."""

import json

import pytest

from pyrite.config import KBConfig
from pyrite.storage.index import IndexManager
from tests._world import build_world


@pytest.fixture
def world(tmp_path):
    result = build_world(tmp_path, kbs=[KBConfig(name="notes", path=tmp_path / "kb")], rest=False)
    try:
        yield result
    finally:
        result.close()


@pytest.mark.parametrize(
    "text",
    [
        "---\nid: broken\ntags: [unclosed\n---\nBody.\n",
        "---\n- not a mapping\n---\nBody.\n",
        "---\nid: broken\ntitle: No closing delimiter\n",
        "---\nid: broken\ntype: note\ntitle: [not, text]\n---\nBody.\n",
    ],
    ids=["yaml", "list-root", "no-closing-delimiter", "list-title"],
)
def test_every_validator_reports_a_malformed_file(world, text):
    path = world.kb_path("notes") / "broken.md"
    path.write_text(text)
    world.index()

    qa = world.cli("qa", "validate", "notes")
    assert qa.exit_code == 1, qa.output
    ci = world.cli("ci", "--kb", "notes", "--format", "json")
    assert ci.exit_code == 1, ci.output
    mcp = world.mcp("read")._dispatch_tool("kb_qa_validate", {"kb_name": "notes"})
    reports = [
        json.loads(qa.stdout)["issues"],
        json.loads(ci.stdout)["kbs"][0]["issues"],
        mcp["issues"],
    ]
    for issues in reports:
        malformed = [i for i in issues if i["rule"] == "malformed_frontmatter"]
        assert len(malformed) == 1, issues
        assert malformed[0]["severity"] == "error"
        assert "broken.md" in malformed[0]["message"]
        assert str(world.kb_path("notes")) not in malformed[0]["message"]

    validation = world.cli("kb", "validate", "notes")
    assert validation.exit_code == 2, validation.output
    body = json.loads(validation.stdout)
    assert body["has_drift"] is True
    rows = body["kbs"][0]["malformed_frontmatter"]
    assert len(rows) == 1
    assert rows[0]["path"] == "broken.md"
    assert rows[0]["error"]

    rich = world.cli("kb", "validate", "notes", "--format", "rich")
    assert rich.exit_code == 2, rich.output
    assert "Malformed: broken.md" in rich.stdout
    assert rows[0]["error"] in rich.stdout
    assert str(world.kb_path("notes")) not in rich.stdout


def test_sync_prints_a_kb_relative_path_on_one_line(world):
    relative = "nested/" + "long-name-" * 15 + ".md"
    path = world.kb_path("notes") / relative
    path.parent.mkdir()
    path.write_text("---\ntags: [unclosed\n---\n")

    result = world.cli("index", "sync", "-k", "notes", "--no-embed")

    assert result.exit_code == 0, result.output
    assert any(relative in line for line in result.stdout.splitlines()), result.stdout
    assert str(world.kb_path("notes")) not in result.stdout


@pytest.mark.control(reason="an undecodable file remains unreadable rather than malformed YAML")
def test_undecodable_file_is_not_counted_twice(world):
    (world.kb_path("notes") / "binary.md").write_bytes(b"---\ntitle: \xff\n---\n")
    world.index()
    health = IndexManager(world.db, world.config).check_health("notes")
    assert len(health["unreadable_files"]) == 1
    assert health["malformed_frontmatter"] == []
    result = world.cli("qa", "validate", "notes")
    assert result.exit_code == 1, result.output
    assert not any(
        i["rule"] == "malformed_frontmatter" for i in json.loads(result.stdout)["issues"]
    )
