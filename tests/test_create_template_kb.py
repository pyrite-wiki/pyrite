"""Tests for `create --template` with a KB (#1598).

The skeleton for a type in a KB shows the fields a create of that type in
that KB would write, read from the same place a create reads them; without
`-k` it stays the core-types skeleton it always was. Every case below runs
the real create in the same KB and compares the skeleton with what it
wrote -- or the two refusals, when both refuse -- so the template cannot
drift from the create. The tests use the `cli_world` fixture, which runs
the real `load_config` against a throwaway config; nothing here patches
it (`tests/test_production_wiring_guard.py` counts the tests that do).
"""

import json

import pytest

from pyrite.config import KBConfig
from pyrite.utils.yaml import load_yaml


def _init_software_kb(world, tmp_path):
    """A KB made by `pyrite init -t software`, in the world's config."""
    assert world.cli(
        "init", "--template", "software", "--path", str(tmp_path / "proj"),
        "--name", "proj", "--no-examples",
    ).exit_code == 0
    return tmp_path / "proj"


def _add_note_severity_field(kb_path):
    """Declare a `note` type with a `severity` default in the KB's kb.yaml."""
    kb_yaml = kb_path / "kb.yaml"
    text = kb_yaml.read_text(encoding="utf-8")
    assert "\n  note:\n" not in text
    kb_yaml.write_text(
        text.replace(
            "\npolicies:",
            "\n  note:\n"
            "    description: Plain note\n"
            "    required:\n"
            "    - title\n"
            "    optional:\n"
            "    - severity\n"
            "    fields:\n"
            "      severity:\n"
            "        type: select\n"
            "        options:\n"
            "        - low\n"
            "        - high\n"
            "        default: low\n"
            "policies:",
            1,
        ),
        encoding="utf-8",
    )


def _template_frontmatter(result):
    """The skeleton's frontmatter as a dict, from a --format json run."""
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    text = data["template"]
    assert text.startswith("---\n")
    assert text.endswith("\n---\n\nYour content here.")
    return load_yaml(text[len("---\n") : -len("\n---\n\nYour content here.")])


def _read_frontmatter(path):
    """A written file's frontmatter as a dict."""
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    end = text.index("\n---", len("---\n"))
    return load_yaml(text[len("---\n") : end])


@pytest.mark.cli
class TestCreateTemplateWithKb:
    @pytest.mark.parametrize(
        "kb_kind,entry_type,template_flags,create_args,ignore",
        [
            # A type the KB declares: the skeleton is what the create wrote.
            ("software", "backlog_item", [], ["--title", "x"], ()),
            # A field the KB adds to a core type, at its declared default.
            ("software-note", "note", [], ["--title", "x"], ()),
            # An in-range importance: the skeleton shows the value the create writes.
            ("software", "note", ["--importance", "7"], ["--title", "x", "--importance", "7"], ()),
            # An out-of-range importance: both refuse, with the same envelope.
            ("software", "note", ["--importance", "42"], ["--title", "x", "--importance", "42"], ()),
            # A plugin type mapping: the skeleton stores the mapped type.
            # `date` is caller-supplied, so it differs from the skeleton's
            # empty default the way `title` does.
            ("journalism", "event", [], ["--title", "x", "-d", "2024-01-01"], ("date",)),
            # An undeclared type under --allow-undeclared in a KB that
            # enforces validation: both refuse, with the same envelope.
            (
                "software",
                "nosuch",
                ["--allow-undeclared"],
                ["--title", "nz", "--allow-undeclared"],
                (),
            ),
        ],
    )
    def test_template_matches_real_create(
        self, cli_world, tmp_path, kb_kind, entry_type, template_flags, create_args, ignore
    ):
        """The skeleton is what a real create writes, or the same refusal."""
        if kb_kind == "journalism":
            world = cli_world(
                kbs=[
                    KBConfig(
                        name="inv2",
                        path=tmp_path / "inv2",
                        kb_type="journalism-investigation",
                    )
                ]
            )
            kb_name, kb_path = "inv2", tmp_path / "inv2"
        else:
            world = cli_world()
            kb_path = _init_software_kb(world, tmp_path)
            kb_name = "proj"
            if kb_kind == "software-note":
                _add_note_severity_field(kb_path)

        created = world.cli(
            "create", "-k", kb_name, "-t", entry_type, *create_args, "--format", "json"
        )
        template = world.cli(
            "create",
            "-k",
            kb_name,
            "-t",
            entry_type,
            "--template",
            *template_flags,
            "--format",
            "json",
        )
        if created.exit_code != 0:
            # Both refuse: the envelopes are identical.
            assert template.exit_code == created.exit_code, template.output
            assert json.loads(template.output) == json.loads(created.output)
            return
        assert template.exit_code == 0, template.output
        skeleton = _template_frontmatter(template)
        written = _read_frontmatter(next(kb_path.rglob("x.md")))
        # id and title are the preview's placeholders, tags is the
        # skeleton's own empty list (the create wrote no tags); the rest
        # is what the create wrote, field for field.
        ignore_fields = {"id", "title", "tags", *ignore}
        assert {k: v for k, v in skeleton.items() if k not in ignore_fields} == {
            k: v for k, v in written.items() if k not in ignore_fields
        }

    def test_template_undeclared_type_refuses_like_create(self, cli_world, tmp_path):
        """An undeclared type is refused with the create's error."""
        world = cli_world()
        _init_software_kb(world, tmp_path)
        template = world.cli(
            "create", "-k", "proj", "-t", "nosuch", "--template", "--format", "json"
        )
        assert template.exit_code == 1
        template_error = json.loads(template.output)

        created = world.cli(
            "create", "-k", "proj", "-t", "nosuch", "--title", "y", "--format", "json"
        )
        assert created.exit_code == 1
        created_error = json.loads(created.output)
        assert template_error["error_code"] == "UNDECLARED_TYPE"
        assert template_error == created_error

    def test_template_unknown_kb_refuses(self, cli_world, tmp_path):
        """A -k that names no KB is refused, as a create is."""
        world = cli_world()
        _init_software_kb(world, tmp_path)
        result = world.cli(
            "create", "-k", "nope", "-t", "note", "--template", "--format", "json"
        )
        assert result.exit_code == 1
        assert json.loads(result.output)["error_code"] == "KB_NOT_FOUND"

    def test_template_without_kb_is_unchanged(self, cli_world):
        """Without -k, the skeleton is today's core-types one."""
        world = cli_world()
        result = world.cli("create", "-t", "person", "--template", "--format", "json")
        skeleton = _template_frontmatter(result)
        assert skeleton == {
            "id": "",
            "type": "person",
            "title": "Your Title Here",
            "tags": [],
            "role": "",
            "affiliations": [],
        }
