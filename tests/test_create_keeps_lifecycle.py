"""#1097: a create or import that asks for `lifecycle: archived` writes it.

`lifecycle` is a base field, and every subclass constructor leaves it at
"active": only the loader (`entry_from_frontmatter`) restored it from the
file's frontmatter. `build_entry()` did not, so the create/import paths wrote
an active entry and said nothing.
"""

import json


def _frontmatter(path):
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), text
    return text.split("---", 2)[1]


def test_a_create_writes_the_lifecycle_it_was_given(cli_world):
    world = cli_world()
    made = world.cli(
        "create",
        "-k",
        "test-kb",
        "-t",
        "note",
        "--title",
        "Arch2",
        "-b",
        "x",
        "-f",
        "lifecycle=archived",
        "--format",
        "json",
    )
    assert made.exit_code == 0, made.output

    path = next(world.kb_path("test-kb").rglob("arch2.md"))
    assert "lifecycle: archived" in _frontmatter(path), path.read_text()


def test_an_import_writes_the_lifecycle_it_was_given(cli_world):
    world = cli_world()
    export = world.root / "lifecycle.json"
    export.write_text(
        json.dumps([{"title": "Arch", "entry_type": "note", "lifecycle": "archived", "body": "b"}]),
        encoding="utf-8",
    )

    result = world.cli("import", str(export), "-k", "test-kb")
    assert result.exit_code == 0, result.output

    path = next(world.kb_path("test-kb").rglob("arch.md"))
    assert "lifecycle: archived" in _frontmatter(path), path.read_text()


def test_a_create_without_a_lifecycle_does_not_grow_the_key(cli_world):
    """The control: a file that never had the key must not gain `active`."""
    world = cli_world()
    made = world.cli("create", "-k", "test-kb", "-t", "note", "--title", "Plain", "-b", "x")
    assert made.exit_code == 0, made.output

    path = next(world.kb_path("test-kb").rglob("plain.md"))
    assert "lifecycle" not in _frontmatter(path), path.read_text()
