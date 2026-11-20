"""Tests for headless KB init command."""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import typer
from typer.testing import CliRunner

from pyrite.cli.init_command import init_kb
from pyrite.config import PyriteConfig, Settings
from pyrite.models.core_types import NoteEntry
from pyrite.storage.entry_writer import create_entry_text
from pyrite.storage.repository import new_entry_text
from pyrite.utils.errors import PARTIAL_EXIT

runner = CliRunner()

# Create a test app to exercise init_kb through CLI runner
test_app = typer.Typer()
test_app.command("init")(init_kb)


@pytest.fixture
def init_env():
    """Test environment for init command."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        db_path = tmpdir / "test_index.db"
        config = PyriteConfig(
            knowledge_bases=[],
            settings=Settings(index_path=db_path),
        )
        yield {"config": config, "tmpdir": tmpdir, "db_path": db_path}


@pytest.mark.cli
class TestInitCommand:
    def test_init_software_template(self, init_env):
        """Init with software template creates expected directories and kb.yaml."""
        kb_path = init_env["tmpdir"] / "my-sw-kb"
        config = init_env["config"]

        with (
            patch("pyrite.config.load_config", return_value=config),
            patch("pyrite.config.save_config"),
        ):
            result = runner.invoke(test_app, ["--template", "software", "--path", str(kb_path)])

        assert result.exit_code == 0, result.output
        assert (kb_path / "kb.yaml").exists()
        assert (kb_path / "adrs").is_dir()
        assert (kb_path / "components").is_dir()
        assert (kb_path / "backlog").is_dir()
        assert (kb_path / "designs").is_dir()
        assert (kb_path / "standards").is_dir()
        assert (kb_path / "runbooks").is_dir()

    def test_init_zettelkasten_template(self, init_env):
        """Init with zettelkasten template creates expected directories."""
        kb_path = init_env["tmpdir"] / "my-zk"
        config = init_env["config"]

        with (
            patch("pyrite.config.load_config", return_value=config),
            patch("pyrite.config.save_config"),
        ):
            result = runner.invoke(test_app, ["--template", "zettelkasten", "--path", str(kb_path)])

        assert result.exit_code == 0, result.output
        assert (kb_path / "kb.yaml").exists()
        assert (kb_path / "zettels").is_dir()
        assert (kb_path / "literature").is_dir()

    def test_init_empty_template(self, init_env):
        """Init with empty template creates kb.yaml but no subdirectories."""
        kb_path = init_env["tmpdir"] / "empty-kb"
        config = init_env["config"]

        with (
            patch("pyrite.config.load_config", return_value=config),
            patch("pyrite.config.save_config"),
        ):
            result = runner.invoke(test_app, ["--template", "empty", "--path", str(kb_path)])

        assert result.exit_code == 0, result.output
        assert (kb_path / "kb.yaml").exists()

    def test_init_idempotent(self, init_env):
        """Second init on same path warns and returns without error."""
        kb_path = init_env["tmpdir"] / "idempotent-kb"
        kb_path.mkdir()
        (kb_path / "kb.yaml").write_text("name: existing\n")
        config = init_env["config"]

        with (
            patch("pyrite.config.load_config", return_value=config),
            patch("pyrite.config.save_config"),
        ):
            result = runner.invoke(test_app, ["--template", "software", "--path", str(kb_path)])

        assert result.exit_code == 0
        assert "already exists" in result.output

    def test_init_custom_name(self, init_env):
        """Init with --name overrides directory name."""
        kb_path = init_env["tmpdir"] / "dir-name"
        config = init_env["config"]

        with (
            patch("pyrite.config.load_config", return_value=config),
            patch("pyrite.config.save_config"),
        ):
            result = runner.invoke(
                test_app,
                ["--template", "research", "--path", str(kb_path), "--name", "custom-name"],
            )

        assert result.exit_code == 0, result.output
        assert "custom-name" in result.output

    def test_init_json_output(self, init_env):
        """Init with --format json produces valid JSON."""
        kb_path = init_env["tmpdir"] / "json-kb"
        config = init_env["config"]

        with (
            patch("pyrite.config.load_config", return_value=config),
            patch("pyrite.config.save_config"),
        ):
            result = runner.invoke(
                test_app, ["--template", "software", "--path", str(kb_path), "--format", "json"]
            )

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["status"] == "created"
        assert data["template"] == "software"
        assert "types" in data

    def test_init_research_template_declares_source(self, cli_world):
        """Research template declares a source type with a sources/ directory (#1384)."""
        from pyrite.cli.init_command import BUILTIN_TEMPLATES

        template = BUILTIN_TEMPLATES["research"]
        assert "source" in template["types"]
        assert template["types"]["source"]["subdirectory"] == "sources/"
        assert "sources" in template["directories"]

        world = cli_world()
        kb_path = world.config_dir / "research-kb"
        result = runner.invoke(test_app, ["--template", "research", "--path", str(kb_path)])

        assert result.exit_code == 0, result.output
        assert (kb_path / "sources").is_dir()

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="create takes the file lock, which refuses to run on Windows",
    )
    def test_create_source_after_research_init(self, cli_world):
        """`create -t source` works after a research init, end to end (#1384).

        The reported failure was the CLI refusing the type (UNDECLARED_TYPE),
        so drive create through the real CLI rather than checking the dict.
        """
        world = cli_world()
        kb_path = world.config_dir / "research-kb"
        result = runner.invoke(test_app, ["--template", "research", "--path", str(kb_path)])
        assert result.exit_code == 0, result.output

        created = world.cli(
            "create",
            "-k",
            "research-kb",
            "-t",
            "source",
            "--title",
            "Source One",
            "-f",
            "author=A",
            "-f",
            "url=https://example.org",
        )
        assert created.exit_code == 0, created.output
        source_file = kb_path / "sources" / "source-one.md"
        assert source_file.is_file(), list(kb_path.rglob("*.md"))
        text = source_file.read_text(encoding="utf-8")
        assert "type: source" in text
        assert "author: A" in text

    @pytest.mark.control(
        reason="the deleted Entry.save/to_markdown is replaced by the create's text (new_entry_text), which the base already has; the property is unchanged (#955 P4)"
    )
    def test_init_reports_entries_indexed_for_seeded_entries(self, init_env):
        """Init on a directory pre-seeded with entries reports entries_indexed matching the count (#909)."""
        kb_path = init_env["tmpdir"] / "seeded-kb"
        notes_dir = kb_path / "notes"
        notes_dir.mkdir(parents=True)
        create_entry_text(
            notes_dir / "n1.md",
            new_entry_text(NoteEntry(id="n1", title="Note 1", body="body 1")),
            root=(notes_dir / "n1.md").parent,
        )
        create_entry_text(
            notes_dir / "n2.md",
            new_entry_text(NoteEntry(id="n2", title="Note 2", body="body 2")),
            root=(notes_dir / "n2.md").parent,
        )
        create_entry_text(
            notes_dir / "n3.md",
            new_entry_text(NoteEntry(id="n3", title="Note 3", body="body 3")),
            root=(notes_dir / "n3.md").parent,
        )
        config = init_env["config"]

        with (
            patch("pyrite.config.load_config", return_value=config),
            patch("pyrite.config.save_config"),
        ):
            result = runner.invoke(
                test_app, ["--template", "software", "--path", str(kb_path), "--format", "json"]
            )

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["status"] == "created"
        assert data["entries_indexed"] == 3

    def test_init_reports_indexing_error_and_exits_partial_when_indexing_fails(self, init_env):
        """When initial indexing fails without mocking, init reports indexing_error and exits PARTIAL_EXIT (#909)."""
        kb_path = init_env["tmpdir"] / "fail-index-kb"
        # Seed an unopenable/corrupt database file so indexing fails at the storage level without mocking
        init_env["db_path"].write_bytes(b"not a valid sqlite database")
        config = init_env["config"]

        with (
            patch("pyrite.config.load_config", return_value=config),
            patch("pyrite.config.save_config"),
        ):
            result = runner.invoke(
                test_app, ["--template", "empty", "--path", str(kb_path), "--format", "json"]
            )

        assert result.exit_code == PARTIAL_EXIT, result.output
        data = json.JSONDecoder().raw_decode(result.output[result.output.index("{") :])[0]
        assert data["status"] == "created"
        assert data["entries_indexed"] == 0
        assert "indexing_error" in data
        assert data["indexing_error"]

    def test_init_intellectual_biography_template(self, init_env):
        """Init with intellectual-biography template creates expected directories and types."""
        kb_path = init_env["tmpdir"] / "bio-kb"
        config = init_env["config"]

        with (
            patch("pyrite.config.load_config", return_value=config),
            patch("pyrite.config.save_config"),
        ):
            result = runner.invoke(
                test_app,
                ["--template", "intellectual-biography", "--path", str(kb_path)],
            )

        assert result.exit_code == 0, result.output
        assert (kb_path / "kb.yaml").exists()
        for subdir in ["notes", "writings", "eras", "events", "people", "sources", "organizations"]:
            assert (kb_path / subdir).is_dir(), f"Missing directory: {subdir}"

    def test_init_intellectual_biography_types(self, init_env):
        """Intellectual-biography template produces valid kb.yaml with all 7 entry types."""
        from pyrite.cli.init_command import BUILTIN_TEMPLATES

        template = BUILTIN_TEMPLATES["intellectual-biography"]
        expected_types = {"note", "writing", "era", "event", "person", "source", "organization"}
        assert set(template["types"].keys()) == expected_types

    def test_init_intellectual_biography_json_output(self, init_env):
        """Intellectual-biography JSON output includes all types."""
        kb_path = init_env["tmpdir"] / "bio-json-kb"
        config = init_env["config"]

        with (
            patch("pyrite.config.load_config", return_value=config),
            patch("pyrite.config.save_config"),
        ):
            result = runner.invoke(
                test_app,
                [
                    "--template",
                    "intellectual-biography",
                    "--path",
                    str(kb_path),
                    "--format",
                    "json",
                ],
            )

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["status"] == "created"
        assert data["template"] == "intellectual-biography"
        assert len(data["types"]) == 7

    def test_init_movement_template(self, init_env):
        """Init with movement template creates expected directories and types."""
        kb_path = init_env["tmpdir"] / "movement-kb"
        config = init_env["config"]

        with (
            patch("pyrite.config.load_config", return_value=config),
            patch("pyrite.config.save_config"),
        ):
            result = runner.invoke(test_app, ["--template", "movement", "--path", str(kb_path)])

        assert result.exit_code == 0, result.output
        assert (kb_path / "kb.yaml").exists()
        for subdir in [
            "notes",
            "writings",
            "eras",
            "events",
            "people",
            "sources",
            "organizations",
            "practices",
        ]:
            assert (kb_path / subdir).is_dir(), f"Missing directory: {subdir}"

    def test_init_movement_types(self, init_env):
        """Movement template produces valid kb.yaml with all 8 entry types."""
        from pyrite.cli.init_command import BUILTIN_TEMPLATES

        template = BUILTIN_TEMPLATES["movement"]
        expected_types = {
            "note",
            "writing",
            "era",
            "event",
            "person",
            "source",
            "organization",
            "practice",
        }
        assert set(template["types"].keys()) == expected_types

    def test_init_movement_has_practice_type(self, init_env):
        """Movement template includes practice type with status validation."""
        from pyrite.cli.init_command import BUILTIN_TEMPLATES

        template = BUILTIN_TEMPLATES["movement"]
        practice = template["types"]["practice"]
        assert practice["description"] == "Method, framework, or technique"
        assert "origin" in practice["optional"]
        assert "status" in practice["optional"]
        # Status is constrained on `practice` itself, not by a KB-wide rule,
        # which would also refuse an event's `status: confirmed` (#555).
        assert set(practice["fields"]["status"]["options"]) == {"active", "deprecated", "evolved"}
        assert not [r for r in template["validation"]["rules"] if r["field"] == "status"]

    def test_init_movement_json_output(self, init_env):
        """Movement JSON output includes all types."""
        kb_path = init_env["tmpdir"] / "movement-json-kb"
        config = init_env["config"]

        with (
            patch("pyrite.config.load_config", return_value=config),
            patch("pyrite.config.save_config"),
        ):
            result = runner.invoke(
                test_app,
                ["--template", "movement", "--path", str(kb_path), "--format", "json"],
            )

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["status"] == "created"
        assert data["template"] == "movement"
        assert len(data["types"]) == 8

    def test_both_new_templates_in_builtin(self):
        """Both intellectual-biography and movement appear in BUILTIN_TEMPLATES."""
        from pyrite.cli.init_command import BUILTIN_TEMPLATES

        assert "intellectual-biography" in BUILTIN_TEMPLATES
        assert "movement" in BUILTIN_TEMPLATES

    def test_init_unknown_template(self, init_env):
        """Unknown template exits with error."""
        kb_path = init_env["tmpdir"] / "bad-template"
        config = init_env["config"]

        with (
            patch("pyrite.config.load_config", return_value=config),
            patch("pyrite.config.save_config"),
        ):
            result = runner.invoke(test_app, ["--template", "nonexistent", "--path", str(kb_path)])

        assert result.exit_code == 1
        assert "Unknown template" in result.output


# --- #1405: a template a plugin supplies registers with the plugin's KB type ---

# The presets an installed extension supplies under the name of a KB type the
# same extension declares (checked against the extensions in this repo; the
# registry test below fails if the list drifts from the installed plugins).
PLUGIN_PRESETS = [
    ("software", "software"),
    ("zettelkasten", "zettelkasten"),
    ("encyclopedia", "encyclopedia"),
    ("social", "social"),
    ("journalism-investigation", "journalism-investigation"),
    ("known-entities", "known-entities"),
]


def _registered_type(world, name):
    result = world.cli("kb", "list", "--format", "json")
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    rows = rows.get("kbs", rows) if isinstance(rows, dict) else rows
    return next(r["type"] for r in rows if r["name"] == name)


@pytest.mark.cli
@pytest.mark.parametrize(("template", "expected"), PLUGIN_PRESETS, ids=lambda v: v)
def test_init_registers_the_plugin_template_kb_type_through_the_cli(
    cli_world, tmp_path, template, expected
):
    world = cli_world()
    result = world.cli(
        "init", "-t", template, "-p", str(tmp_path / "made"), "-n", "made", "--no-examples"
    )
    assert result.exit_code == 0, result.output
    assert _registered_type(world, "made") == expected


@pytest.mark.cli
@pytest.mark.control(reason="built-in templates register generic before and after #1405")
def test_init_builtin_template_registers_generic_through_the_cli(cli_world, tmp_path):
    world = cli_world()
    result = world.cli(
        "init", "-t", "research", "-p", str(tmp_path / "made"), "-n", "made", "--no-examples"
    )
    assert result.exit_code == 0, result.output
    assert _registered_type(world, "made") == "generic"


@pytest.mark.cli
@pytest.mark.control(reason="guards the hand-written preset list above against drift")
def test_plugin_presets_list_matches_the_installed_extensions():
    """A plugin supplying a preset named like a KB type it declares is a case to list."""
    from pyrite.plugins import get_registry

    registry = get_registry()
    declared = set(registry.get_all_kb_types())
    assert {name for name in registry.get_all_kb_presets() if name in declared} == {
        t for t, _ in PLUGIN_PRESETS
    }


@pytest.mark.cli
@pytest.mark.control(reason="--schema-file registers generic before and after #1405")
def test_schema_file_keeps_generic_even_for_a_plugin_template_name(cli_world, tmp_path):
    world = cli_world()
    schema = tmp_path / "schema.yaml"
    schema.write_text("name: custom\ntypes:\n  note:\n    description: n\n")
    result = world.cli(
        "init", "-t", "software", "-p", str(tmp_path / "made"), "-n", "made",
        "--schema-file", str(schema), "--no-examples",
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    assert _registered_type(world, "made") == "generic"


@pytest.mark.cli
def test_init_made_software_kb_answers_like_one_added_with_kb_add_type(
    cli_world, tmp_path, monkeypatch
):
    """The audit's test (#1405 groom 2026-11-16): the same files, registered two ways."""
    import shutil

    from pyrite.plugins import get_registry

    # The CLI runs a plugin without a context. Another test may have left one on
    # the process-wide plugin (tests/test_orient_software.py does), and orient
    # would then resolve `sw` against that test's config and answer "not found".
    registry = get_registry()
    registry.discover()
    for plugin_name in registry.list_plugins():
        monkeypatch.setattr(registry.get_plugin(plugin_name), "ctx", None, raising=False)

    world = cli_world()
    made = world.cli(
        "init", "-t", "software", "-p", str(tmp_path / "sw"), "-n", "sw", "--no-examples"
    )
    assert made.exit_code == 0, made.output
    shutil.copytree(tmp_path / "sw", tmp_path / "sw2")
    kb_yaml = tmp_path / "sw2" / "kb.yaml"
    kb_yaml.write_text(kb_yaml.read_text().replace("name: sw\n", "name: sw2\n", 1))
    added = world.cli("kb", "add", str(tmp_path / "sw2"), "--name", "sw2", "--type", "software")
    assert added.exit_code == 0, added.output

    def create(kb):
        r = world.cli(
            "create", "-k", kb, "-t", "adr", "--title", "Use sqlite", "-b", "x",
            "--allow-undeclared", "--format", "json",
        )  # fmt: skip
        assert r.exit_code == 0, r.output
        return json.loads(r.output)

    def has_orient_block(kb):
        r = world.cli("orient", "-k", kb, "--format", "json")
        assert r.exit_code == 0, r.output
        return '"software": {' in r.output

    for kb in ("sw", "sw2"):
        warnings = create(kb).get("warnings", [])
        assert [w["rule"] for w in warnings] == ["date_recommended"], (kb, warnings)
        assert has_orient_block(kb), kb
