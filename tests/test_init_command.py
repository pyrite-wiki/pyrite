"""Tests for headless KB init command."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import typer
from typer.testing import CliRunner

from pyrite.cli.init_command import init_kb
from pyrite.config import PyriteConfig, Settings

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
