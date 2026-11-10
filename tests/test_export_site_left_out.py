"""`pyrite export site` names every entry it leaves out and exits non-zero (#1325).

A row whose file cannot be loaded is no longer dropped silently: the export
prints the entry and the loader's reason, and the exit code follows the
0/3/1 rule of ADR-0046 (`exit_unless_whole`). `--exclude-types` and
`--exclude-status` are asked-for filters, not left-out entries, and stay 0.

Every test drives the real CLI against a `tests._world` world (#1077): the
config is read from disk, nothing patches `load_config`.
"""

import re

import pytest

from .cli_help import plain

BAD = "---\nid: {id}\ntitle: {id}\ntype: note\n---\nbody\n"
GOOD = "---\nid: good\ntitle: Good\ntype: note\n---\nbody\n"
NO_FRONTMATTER = "just a body, no frontmatter\n"


def _say(result):
    """Printed words only: no ANSI, no Rich box borders, no wrapping."""
    text = plain(result.output + (getattr(result, "stderr", "") or ""))
    return re.sub(r"[\s│╭╮╰╯─┃┏┓┗┛━]+", " ", text)


@pytest.fixture
def world(cli_world):
    world = cli_world()
    world.add_entry("test-kb", GOOD)
    for name in ("bad1", "bad2"):
        world.add_entry("test-kb", BAD.format(id=name))
    return world


def _break(world, name, how):
    path = world.kb_path("test-kb") / f"{name}.md"
    if how == "invalid-yaml":
        path.write_text(f"---\nid: {name}\ntitle: [unclosed\ntype: note\n---\nbody\n")
    elif how == "no-frontmatter":
        path.write_text(NO_FRONTMATTER)
    elif how == "deleted":
        path.unlink()
    else:  # pragma: no cover - guards the parametrisation
        raise AssertionError(how)


def _export(world, *args):
    return world.cli("export", "site", "-k", "test-kb", "-o", str(world.root / "site"), *args)


def _export_collection(world, *args):
    return world.cli("export", "collection", *args, "-k", "test-kb", "-o", str(world.root / "out"))


class TestLeftOutEntries:
    @pytest.mark.parametrize("how", ["invalid-yaml", "no-frontmatter", "deleted"])
    def test_one_unloadable_entry_is_named_and_exits_partial(self, world, how):
        _break(world, "bad1", how)
        result = _export(world)
        text = _say(result)

        assert result.exit_code == 3, text
        assert "1 entry left out" in text, text
        assert "bad1" in text, text
        assert "Exported 2 entries" in text, text

    def test_invalid_yaml_gives_the_loaders_message(self, world):
        _break(world, "bad1", "invalid-yaml")
        text = _say(_export(world))
        assert "Invalid YAML frontmatter" in text, text

    def test_deleted_file_says_it_is_missing(self, world):
        _break(world, "bad1", "deleted")
        text = _say(_export(world))
        assert "missing" in text, text

    def test_two_left_out_uses_the_plural_and_names_both(self, world):
        _break(world, "bad1", "invalid-yaml")
        _break(world, "bad2", "deleted")
        result = _export(world)
        text = _say(result)

        assert result.exit_code == 3, text
        assert "2 entries left out" in text, text
        assert "bad1" in text and "bad2" in text, text
        assert "Exported 1 entry" in text, text
        assert "Exported 1 entries" not in text, text

    def test_nothing_loadable_exits_one(self, world):
        _break(world, "good", "invalid-yaml")
        _break(world, "bad1", "invalid-yaml")
        _break(world, "bad2", "no-frontmatter")
        result = _export(world)
        text = _say(result)

        assert result.exit_code == 1, text
        assert "3 entries left out" in text, text
        assert "good" in text and "bad1" in text and "bad2" in text, text

    def test_excluded_types_are_not_left_out_and_exit_zero(self, world):
        result = _export(world, "--exclude-types", "note")
        text = _say(result)

        assert result.exit_code == 0, text
        assert "left out" not in text, text
        assert "Skipped: 3 (filtered)" in text, text

    def test_allow_partial_restores_zero_and_still_names_the_entry(self, world):
        _break(world, "bad1", "invalid-yaml")
        result = _export(world, "--allow-partial")
        text = _say(result)

        assert result.exit_code == 0, text
        assert "1 entry left out" in text, text
        assert "bad1" in text, text

    def test_allow_partial_covers_nothing_loadable(self, world):
        for name, how in (
            ("good", "invalid-yaml"),
            ("bad1", "invalid-yaml"),
            ("bad2", "no-frontmatter"),
        ):
            _break(world, name, how)
        result = _export(world, "--allow-partial")
        text = _say(result)

        assert result.exit_code == 0, text
        assert "3 entries left out" in text, text
        assert "No entries could be loaded." in text, text

    def test_a_row_the_filters_drop_is_not_counted_as_left_out(self, world):
        """A broken row of an excluded type is asked for, not left out."""
        _break(world, "bad1", "invalid-yaml")
        result = _export(world, "--exclude-types", "note")
        text = _say(result)

        assert result.exit_code == 0, text
        assert "left out" not in text, text

    def test_init_prints_next_steps_even_when_an_entry_is_left_out(self, world):
        _break(world, "bad1", "invalid-yaml")
        result = _export(world, "--init")
        text = _say(result)

        assert result.exit_code == 3, text
        assert "1 entry left out" in text, text
        assert "Next steps:" in text, text


class TestLeftOutTextIsPrintedNotParsed:
    """An id, a path and a YAML message are data: Rich prints them, never parses them."""

    def test_a_reason_holding_markup_is_printed_literally(self, world):
        (world.kb_path("test-kb") / "bad1.md").write_text(
            "---\nid: bad1\ntitle: [/draft] notes\ntype: note\n---\nbody\n"
        )
        result = _export(world)
        text = _say(result)

        assert result.exit_code == 3, text
        assert "[/draft]" in text, text

    def test_a_name_holding_brackets_is_printed_literally(self, cli_world):
        world = cli_world()
        world.add_entry("test-kb", GOOD)
        world.add_entry("test-kb", BAD.format(id="brackets[1]"))
        (world.kb_path("test-kb") / "brackets[1].md").unlink()
        result = _export(world)
        text = _say(result)

        assert result.exit_code == 3, text
        assert "brackets[1]" in text, text


class TestCollectionLeftOutRows:
    """`export collection` names its unloadable rows the same way, both modes."""

    def test_query_mode_names_a_row_it_could_not_load(self, world):
        _break(world, "bad1", "invalid-yaml")
        result = _export_collection(world, "q:type:note")
        text = _say(result)

        assert result.exit_code == 3, text
        assert "1 entry left out" in text, text
        assert "bad1" in text, text
        assert "Invalid YAML frontmatter" in text, text

    def test_folder_mode_names_a_broken_file_and_ignores_a_readme(self, world):
        folder = world.kb_path("test-kb") / "stuff"
        folder.mkdir()
        (folder / "ok.md").write_text("---\nid: ok\ntitle: Ok\ntype: note\n---\nbody\n")
        (folder / "README.md").write_text("# Just a readme\n")
        (folder / "broken.md").write_text(
            "---\nid: broken\ntitle: [unclosed\ntype: note\n---\nbody\n"
        )
        world.add_entry(
            "test-kb",
            "---\nid: stuff\ntitle: Stuff\ntype: collection\nsource_type: folder\n"
            "folder_path: stuff\n---\n",
        )
        result = _export_collection(world, "stuff")
        text = _say(result)

        assert result.exit_code == 3, text
        assert "1 entry left out" in text, text
        assert "broken" in text, text
        assert "README" not in text, text
