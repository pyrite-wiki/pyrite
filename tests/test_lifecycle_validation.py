"""Lifecycle is active or archived; invalid values already on disk are preserved."""

import json
from pathlib import Path

import pytest

from pyrite.config import KBConfig
from pyrite.utils.yaml import load_yaml
from tests._world import build_world


@pytest.fixture
def world(tmp_path, request):
    built = build_world(
        tmp_path,
        kbs=[KBConfig(name="notes", path=tmp_path / "notes")],
        rest=request.node.callspec.params.get("surface") == "rest",
    )
    try:
        built.add_entry("notes", "---\nid: base\ntype: note\ntitle: Base\n---\nBody.\n")
        assert built.cli("orient", "-k", "notes").exit_code == 0
        yield built
    finally:
        built.close()


def _write(world, surface, operation, fields):
    """Use each public write spelling, including metadata for genuine JSON null."""
    if surface == "cli":
        args = (
            ["create", "-k", "notes", "-t", "note", "--title", "New", "-b", "Body."]
            if operation == "create"
            else ["update", "base", "-k", "notes"]
        )
        metadata = {}
        for field, value in fields.items():
            if field == "title":
                args += ["--title", value]
            elif field == "lifecycle" and isinstance(value, str):
                args += (
                    ["--lifecycle", value]
                    if operation == "update"
                    else ["-f", f"lifecycle={value}"]
                )
            else:
                metadata[field] = value
        if metadata:
            args += ["-f", f"metadata={json.dumps(metadata)}"]
        result = world.cli(*args, "--format", "json")
        return result.exit_code == 0, json.loads(result.stdout)

    metadata = {key: value for key, value in fields.items() if key != "title"}
    data = {"metadata": metadata}
    if operation == "create":
        data.update(entry_type="note", title="New", body="Body.")
    if "title" in fields:
        data["title"] = fields["title"]
    if surface == "mcp":
        server = world.mcp("write")
        assert "error" not in server._dispatch_tool("kb_orient", {"kb_name": "notes"})
        if operation == "update":
            data["entry_id"] = "base"
        result = server._dispatch_tool(f"kb_{operation}", {"kb_name": "notes", **data})
        return "error" not in result, result
    if operation == "create":
        result = world.client.post("/api/entries", json={"kb": "notes", **data})
    else:
        result = world.client.put("/api/entries/base", json={"kb": "notes", **data})
    body = result.json()
    if result.status_code != 200:
        assert result.status_code == 400, body
    return result.status_code == 200, body.get("detail", body)


def _files(world):
    return {path: path.read_bytes() for path in world.kb_path("notes").rglob("*.md")}


def _refusal(body, value):
    assert (body.get("error_code") or body.get("code")) == "VALIDATION_FAILED", body
    rows = [row for row in body["errors"] if row["field"] == "lifecycle"]
    assert len(rows) == 1, body
    row = rows[0]
    assert row["rule"] == "enum", row
    assert row["expected"] == ["active", "archived"], row
    assert row["got"] == value, row
    assert "lifecycle" in row["message"].lower(), row
    assert "active" in row["message"] and "archived" in row["message"], row


@pytest.mark.parametrize("surface", ["cli", "rest", "mcp"])
@pytest.mark.parametrize("operation", ["create", "update"])
@pytest.mark.parametrize("value", ["banana", "Archived", 3, None, []])
def test_invalid_lifecycle_is_refused_before_file_or_index_changes(
    world, surface, operation, value
):
    files_before = _files(world)
    row_before = world.db.get_entry("base", "notes")

    succeeded, body = _write(world, surface, operation, {"lifecycle": value})

    assert not succeeded, body
    _refusal(body, value)
    assert _files(world) == files_before
    assert world.db.get_entry("base", "notes") == row_before
    assert world.db.get_entry("new", "notes") is None


@pytest.mark.parametrize("surface", ["cli", "rest", "mcp"])
@pytest.mark.parametrize("value", ["banana", 3, None])
def test_unrelated_edit_warns_and_preserves_invalid_lifecycle_but_another_bad_value_is_refused(
    world, surface, value
):
    literal = value if isinstance(value, str) else json.dumps(value)
    original = (
        "---\nid: base\ntype: note\ntitle: Base\n"
        f"lifecycle: {literal} # authored value\ncustom: 'leave this alone'\n---\n\nBody.\n"
    ).encode()
    path = world.add_entry("notes", original.decode())
    indexed_lifecycle = world.db.get_entry("base", "notes")["lifecycle"]

    succeeded, body = _write(world, surface, "update", {"title": "Renamed"})

    assert succeeded, body
    warnings = [row for row in body["warnings"] if row["field"] == "lifecycle"]
    assert len(warnings) == 1, body
    assert warnings[0]["rule"] == "model_validation"
    assert warnings[0]["severity"] == "warning"
    assert repr(value) in warnings[0]["message"]
    assert path.read_bytes() == original.replace(b"title: Base", b"title: Renamed")
    assert world.db.get_entry("base", "notes")["lifecycle"] == indexed_lifecycle

    before = path.read_bytes()
    succeeded, body = _write(world, surface, "update", {"lifecycle": "Archived"})

    assert not succeeded, body
    _refusal(body, "Archived")
    assert path.read_bytes() == before
    assert world.db.get_entry("base", "notes")["lifecycle"] == indexed_lifecycle

    succeeded, body = _write(world, surface, "update", {"lifecycle": "archived"})

    assert succeeded, body
    assert not body.get("warnings"), body
    assert path.read_bytes() == before.replace(
        f"lifecycle: {literal}".encode(), b"lifecycle: archived"
    )
    assert world.db.get_entry("base", "notes")["lifecycle"] == "archived"


@pytest.mark.control(
    reason="Valid lifecycle values and omission must retain their existing behavior"
)
@pytest.mark.parametrize("surface", ["cli", "rest", "mcp"])
@pytest.mark.parametrize(
    ("fields", "expected"),
    [({}, "active"), ({"lifecycle": "active"}, "active"), ({"lifecycle": "archived"}, "archived")],
    ids=["absent", "active", "archived"],
)
def test_valid_lifecycle_create_and_update_stay_valid(world, surface, fields, expected):
    succeeded, body = _write(world, surface, "create", fields)
    assert succeeded, body
    assert not body.get("warnings"), body
    succeeded, body = _write(world, surface, "update", {"title": "Renamed", **fields})
    assert succeeded, body
    assert not body.get("warnings"), body
    for entry_id in ("new", "base"):
        row = world.db.get_entry(entry_id, "notes")
        assert row["lifecycle"] == expected
        path = next(world.kb_path("notes").rglob(f"{entry_id}.md"))
        frontmatter = load_yaml(path.read_text().split("---", 2)[1])
        assert frontmatter.get("lifecycle", "active") == expected
        if not fields:
            assert "lifecycle" not in frontmatter

    before = _files(world)
    result = world.cli("qa", "validate", "notes", "--format", "json")
    assert result.exit_code == 0, result.stdout
    issues = json.loads(result.stdout)["issues"]
    assert not [row for row in issues if row.get("field") == "lifecycle"], issues
    assert _files(world) == before


@pytest.mark.parametrize("value", ["banana", 3, None])
@pytest.mark.parametrize("scope", ["entry", "kb"])
def test_qa_reports_invalid_file_lifecycle_without_changing_files(world, value, scope):
    indexed_before = world.db.get_entry("base", "notes")
    path = Path(indexed_before["file_path"])
    path.write_text(
        f"---\nid: base\ntype: note\ntitle: Base\nlifecycle: {json.dumps(value)}\n---\nBody.\n",
    )
    assert indexed_before["lifecycle"] == "active"
    before = _files(world)
    args = ["qa", "validate", "notes"]
    if scope == "entry":
        args += ["--entry", "base"]

    result = world.cli(*args, "--format", "json")

    assert result.exit_code == 1, result.stdout
    body = json.loads(result.stdout)
    issues = [row for row in body["issues"] if row.get("field") == "lifecycle"]
    assert len(issues) == 1, body
    assert issues[0]["entry_id"] == "base"
    assert issues[0]["rule"] == "invalid_lifecycle"
    assert issues[0]["severity"] == "error"
    assert "lifecycle" in issues[0]["message"].lower()
    assert _files(world) == before
    assert world.db.get_entry("base", "notes") == indexed_before


@pytest.mark.parametrize("entry_type", ["event", "finding"])
def test_event_and_declared_generic_type_inherit_lifecycle_validation(tmp_path, entry_type):
    kb_path = tmp_path / "typed"
    kb_path.mkdir()
    (kb_path / "kb.yaml").write_text("types:\n  event: {}\n  finding: {}\n")
    world = build_world(tmp_path, kbs=[KBConfig(name="typed", path=kb_path)], rest=False)
    try:
        assert world.cli("orient", "-k", "typed").exit_code == 0
        args = ["create", "-k", "typed", "-t", entry_type, "--title", "New", "-b", "Body."]
        if entry_type == "event":
            args += ["-f", "date=2026-01-01"]

        result = world.cli(*args, "-f", "lifecycle=banana", "--format", "json")

        assert result.exit_code == 1, result.stdout
        body = json.loads(result.stdout)
        _refusal(body, "banana")
        assert [row["field"] for row in body["errors"]] == ["lifecycle"]
        assert not list(kb_path.rglob("*.md"))
        assert world.db.get_entry("new", "typed") is None
    finally:
        world.close()


@pytest.mark.parametrize("surface", ["rest"])
@pytest.mark.parametrize(("literal", "shown"), [(".nan", "nan"), (".inf", "inf")])
def test_rest_qa_serializes_non_finite_lifecycle_from_the_file(world, surface, literal, shown):
    indexed_before = world.db.get_entry("base", "notes")
    path = Path(indexed_before["file_path"])
    path.write_text(f"---\nid: base\ntype: note\ntitle: Base\nlifecycle: {literal}\n---\nBody.\n")
    before = _files(world)
    for endpoint in ("/api/qa/validate/base", "/api/qa/validate"):
        response = world.client.get(endpoint, params={"kb": "notes"})

        assert response.status_code == 200, response.text
        body = response.json()
        json.dumps(body, allow_nan=False)
        issues = [row for row in body["issues"] if row.get("field") == "lifecycle"]
        assert len(issues) == 1, body
        assert issues[0]["rule"] == "invalid_lifecycle"
        assert issues[0]["severity"] == "error"
        assert issues[0]["got"] == shown
        assert issues[0]["expected"] == ["active", "archived"]
    assert _files(world) == before
    assert world.db.get_entry("base", "notes") == indexed_before
