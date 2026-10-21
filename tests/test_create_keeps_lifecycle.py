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


# -- the groom's acceptance: build_entry itself (#1097) -----------------------

import pytest

from pyrite.models.factory import build_entry

TYPES = ["note", "event", "person", "finding"]  # `finding`: declared only in a kb.yaml (generic)


def _extra(entry_type: str) -> dict:
    # `event` and `person` need nothing else to build; `date` keeps `event` valid.
    return {"date": "2026-01-01"} if entry_type == "event" else {}


@pytest.mark.parametrize("entry_type", TYPES)
def test_build_entry_keeps_the_lifecycle_it_is_given(entry_type):
    entry = build_entry(entry_type, title="A", lifecycle="archived", **_extra(entry_type))

    assert entry.lifecycle == "archived"
    assert entry.to_frontmatter().get("lifecycle") == "archived"


@pytest.mark.parametrize("entry_type", TYPES)
def test_build_entry_keeps_a_lifecycle_sent_inside_metadata(entry_type):
    """The REST regime: the body model has no `lifecycle` key, so a client sends
    every non-standard field inside `metadata`."""
    entry = build_entry(
        entry_type, title="A", metadata={"lifecycle": "archived"}, **_extra(entry_type)
    )
    fm = entry.to_frontmatter()

    assert entry.lifecycle == "archived"
    assert fm.get("lifecycle") == "archived"
    assert "lifecycle" not in (fm.get("metadata") or {}), fm  # spelled once, not twice


@pytest.mark.parametrize("entry_type", TYPES)
def test_build_entry_without_a_lifecycle_is_active_and_writes_no_key(entry_type):
    entry = build_entry(entry_type, title="A", **_extra(entry_type))

    assert entry.lifecycle == "active"
    assert "lifecycle" not in entry.to_frontmatter()


def test_a_rest_create_keeps_a_lifecycle_sent_inside_metadata(make_client):
    client, config, _db = make_client()

    resp = client.post(
        "/api/entries",
        json={
            "kb": "test-kb",
            "entry_type": "note",
            "title": "RestArch",
            "metadata": {"lifecycle": "archived"},
        },
    )

    assert resp.status_code == 200, resp.text
    path = next(config.get_kb("test-kb").path.rglob("restarch.md"))
    assert _frontmatter(path).count("lifecycle: archived") == 1, path.read_text()


# -- the groom's second regime: an invalid value behaves as `update` does -----


@pytest.mark.parametrize("value", ["archived", "bogus"])
def test_a_create_treats_a_lifecycle_value_as_update_lifecycle_does(cli_world, value):
    """Whatever `update --lifecycle V` does with V -- accept it, or refuse it --
    `create -f lifecycle=V` does the same: same exit code, same value written."""
    world = cli_world()
    assert world.cli("create", "-k", "test-kb", "--title", "Base", "-b", "x").exit_code == 0
    updated = world.cli("update", "base", "-k", "test-kb", "--lifecycle", value, "--format", "json")
    created = world.cli(
        "create", "-k", "test-kb", "--title", "New", "-b", "x", "-f", f"lifecycle={value}",
        "--format", "json",
    )  # fmt: skip

    assert created.exit_code == updated.exit_code, (created.output, updated.output)

    def written(stem: str) -> list[str]:
        files = list(world.kb_path("test-kb").rglob(f"{stem}.md"))
        return [ln for f in files for ln in _frontmatter(f).splitlines() if "lifecycle" in ln]

    assert written("new") == written("base"), (written("new"), written("base"))
