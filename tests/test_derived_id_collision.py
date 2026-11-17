"""Tests for derived entry ID collision disambiguation (#1682).

When a new entry's ID is derived from its title and collides with an existing
entry of a *different* title (such as long task titles that truncate at 80
characters at a word boundary), create derives a unique ID by appending a short
hash suffix of the title. The same title still answers ENTRY_EXISTS.
"""

import json
from pathlib import Path

from pyrite.services.kb_service import KBService
from pyrite.storage.repository import KBRepository
from pyrite.utils.yaml import dump_yaml

LONG_BASE = (
    "Research the county sheriff's 2024 detention contract renewal and the "
    "subsequent amendments filed with the board"
)
TITLE_ONE = f"{LONG_BASE} part one"
TITLE_TWO = f"{LONG_BASE} part two"


def test_task_create_long_title_collision_both_succeed(cli_world):
    """Two tasks sharing the first 80+ chars of title both succeed with distinct IDs."""
    world = cli_world()
    res1 = world.cli("task", "create", TITLE_ONE, "-k", "test-kb", "--format", "json")
    assert res1.exit_code == 0, res1.output
    data1 = json.loads(res1.stdout)
    id1 = data1["entry_id"]

    res2 = world.cli("task", "create", TITLE_TWO, "-k", "test-kb", "--format", "json")
    assert res2.exit_code == 0, res2.output
    data2 = json.loads(res2.stdout)
    id2 = data2["entry_id"]

    assert id1 != id2
    assert id1 in id2 or id2.startswith(id1[:50])


def test_task_create_same_long_title_refused_entry_exists(cli_world):
    """Creating the same long title twice is refused with ENTRY_EXISTS."""
    world = cli_world()
    res1 = world.cli("task", "create", TITLE_ONE, "-k", "test-kb", "--format", "json")
    assert res1.exit_code == 0, res1.output

    res2 = world.cli("task", "create", TITLE_ONE, "-k", "test-kb", "--format", "json")
    assert res2.exit_code == 1, res2.output
    data2 = json.loads(res2.stdout)
    assert data2.get("error_code") == "ENTRY_EXISTS"


def test_task_create_short_title_unchanged_id(cli_world):
    """A title under 80 characters keeps its exact slug without any hash suffix."""
    world = cli_world()
    res = world.cli("task", "create", "Fix user login flow", "-k", "test-kb", "--format", "json")
    assert res.exit_code == 0, res.output
    data = json.loads(res.stdout)
    assert data["entry_id"] == "fix-user-login-flow"


def test_explicit_id_never_suffixed_refuses_when_taken(cli_world):
    """An explicit ID from caller is never suffixed and is refused if taken."""
    world = cli_world()
    res1 = world.cli(
        "create",
        "-k",
        "test-kb",
        "-t",
        "note",
        "--field",
        "id=custom-fixed-id",
        "--title",
        "First Note",
        "--body",
        "Body 1",
        "--format",
        "json",
    )
    assert res1.exit_code == 0, res1.output

    res2 = world.cli(
        "create",
        "-k",
        "test-kb",
        "-t",
        "note",
        "--field",
        "id=custom-fixed-id",
        "--title",
        "Second Note with Different Title",
        "--body",
        "Body 2",
        "--format",
        "json",
    )
    assert res2.exit_code == 1, res2.output
    data2 = json.loads(res2.stdout)
    assert data2.get("error_code") == "ENTRY_EXISTS"


def test_pyrite_create_long_title_collision(cli_world):
    """Generic pyrite create disambiguates derived ID collisions for distinct titles."""
    world = cli_world()
    res1 = world.cli(
        "create",
        "-k",
        "test-kb",
        "-t",
        "note",
        "--title",
        TITLE_ONE,
        "--body",
        "Body 1",
        "--format",
        "json",
    )
    assert res1.exit_code == 0, res1.output
    data1 = json.loads(res1.stdout)

    res2 = world.cli(
        "create",
        "-k",
        "test-kb",
        "-t",
        "note",
        "--title",
        TITLE_TWO,
        "--body",
        "Body 2",
        "--format",
        "json",
    )
    assert res2.exit_code == 0, res2.output
    data2 = json.loads(res2.stdout)

    assert data1["entry_id"] != data2["entry_id"]


def test_mcp_kb_create_and_duplicate_refusal(cli_world):
    """MCP kb_create disambiguates distinct titles and refuses identical titles."""
    world = cli_world()
    server = world.mcp()

    res1 = server._dispatch_tool(
        "kb_create",
        {"kb_name": "test-kb", "title": TITLE_ONE, "entry_type": "note", "body": "Body 1"},
    )
    assert "error" not in res1, res1
    id1 = res1["entry_id"]

    res2 = server._dispatch_tool(
        "kb_create",
        {"kb_name": "test-kb", "title": TITLE_TWO, "entry_type": "note", "body": "Body 2"},
    )
    assert "error" not in res2, res2
    id2 = res2["entry_id"]

    assert id1 != id2

    # Retrying the second title is refused with ENTRY_EXISTS
    res3 = server._dispatch_tool(
        "kb_create",
        {"kb_name": "test-kb", "title": TITLE_TWO, "entry_type": "note", "body": "Body 3"},
    )
    assert res3.get("error_code") == "ENTRY_EXISTS", res3


def test_task_decompose_colliding_titles(cli_world):
    """task decompose creates children with distinct IDs when their titles collide."""
    world = cli_world()
    parent_res = world.cli("task", "create", "Parent Task", "-k", "test-kb", "--format", "json")
    assert parent_res.exit_code == 0, parent_res.output
    parent_id = json.loads(parent_res.stdout)["entry_id"]

    res = world.cli(
        "task",
        "decompose",
        parent_id,
        "-k",
        "test-kb",
        "-c",
        TITLE_ONE,
        "-c",
        TITLE_TWO,
        "--format",
        "json",
    )
    assert res.exit_code == 0, res.output
    data = json.loads(res.stdout)
    assert data["decomposed"] is True
    children = data["children"]
    assert len(children) == 2
    assert children[0]["entry_id"] != children[1]["entry_id"]
    assert children[0]["created"] is True
    assert children[1]["created"] is True


def test_bulk_create_validate_only_disambiguation(cli_world):
    """bulk_create with validate_only handles derived ID collision in-batch."""
    world = cli_world()
    kb_svc = KBService(world.config, world.db)
    specs = [
        {"title": TITLE_ONE, "entry_type": "note", "body": "1"},
        {"title": TITLE_TWO, "entry_type": "note", "body": "2"},
        {"title": TITLE_ONE, "entry_type": "note", "body": "duplicate of 1"},
    ]
    results = kb_svc.bulk_create_entries("test-kb", specs, validate_only=True)
    assert len(results) == 3
    assert results[0].get("valid") is True
    assert results[1].get("valid") is True
    assert results[0]["entry_id"] != results[1]["entry_id"]
    assert results[2].get("error_code") == "ENTRY_EXISTS"


def test_existing_idless_files_do_not_change_id(cli_world):
    """Existing files without id in frontmatter index to the exact same IDs."""
    world = cli_world()
    from pyrite.schema import generate_entry_id

    expected_id = generate_entry_id(TITLE_ONE)
    world.add_entry(
        "test-kb",
        f"---\ntitle: {TITLE_ONE}\ntype: note\n---\n\nLegacy content.\n",
        filename=f"notes/{expected_id}.md",
    )

    repo = KBRepository(world.config.get_kb("test-kb"))
    entry = repo.load(expected_id)
    assert entry is not None
    assert entry.title == TITLE_ONE
