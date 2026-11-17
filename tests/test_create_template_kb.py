"""Tests for `create --template` with a KB (#1598).

The skeleton for a type in a KB shows the fields a create of that type in
that KB would write, read from the same place a create reads them; without
`-k` it stays the core-types skeleton it always was.
"""

import contextlib
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import typer
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.cli.init_command import init_kb
from pyrite.config import PyriteConfig, Settings
from pyrite.utils.yaml import load_yaml

runner = CliRunner()

init_app = typer.Typer()
init_app.command("init")(init_kb)


@pytest.fixture
def software_kb():
    """A KB made by `pyrite init -t software`, with a config that knows it."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        config = PyriteConfig(knowledge_bases=[], settings=Settings(index_path=tmpdir / "index.db"))
        kb_path = tmpdir / "proj"
        with (
            patch("pyrite.config.load_config", return_value=config),
            patch("pyrite.config.save_config"),
        ):
            result = runner.invoke(
                init_app,
                [
                    "--template",
                    "software",
                    "--path",
                    str(kb_path),
                    "--name",
                    "proj",
                    "--no-examples",
                ],
            )
        assert result.exit_code == 0, result.output
        yield {"config": config, "tmpdir": tmpdir, "kb_path": kb_path}


@contextlib.contextmanager
def _patch_config(config):
    """Patch load_config to return the test config in all CLI modules."""
    with patch("pyrite.cli.load_config", return_value=config):
        with patch("pyrite.cli.context.load_config", return_value=config):
            yield


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
    def test_template_backlog_item_matches_real_create(self, software_kb):
        """The skeleton has the keys and defaults a create writes."""
        kb_path = software_kb["kb_path"]
        with _patch_config(software_kb["config"]):
            created = runner.invoke(
                app,
                [
                    "create",
                    "-k",
                    "proj",
                    "-t",
                    "backlog_item",
                    "--title",
                    "x",
                    "--format",
                    "json",
                ],
            )
            assert created.exit_code == 0, created.output
            written = _read_frontmatter(kb_path / "backlog" / "x.md")

            result = runner.invoke(
                app,
                ["create", "-k", "proj", "-t", "backlog_item", "--template", "--format", "json"],
            )
            skeleton = _template_frontmatter(result)

        # id and title are the preview's placeholders; tags is the
        # skeleton's own empty list (the create wrote no tags).
        assert skeleton["id"] == ""
        assert skeleton["title"] == "Your Title Here"
        assert skeleton["tags"] == []
        template_fields = {k: v for k, v in skeleton.items() if k not in ("id", "title", "tags")}
        written_fields = {k: v for k, v in written.items() if k not in ("id", "title")}
        assert template_fields == written_fields
        assert template_fields == {
            "type": "backlog_item",
            "importance": 5,
            "kind": "",
            "status": "proposed",
            "priority": "medium",
            "rank": 0,
            "effort": "",
            "assignee": "",
        }

    def test_template_kb_yaml_field_with_declared_default(self, software_kb):
        """A field a KB adds to a core type in kb.yaml appears, at its default."""
        kb_path = software_kb["kb_path"]
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
        software_kb["config"].get_kb("proj").invalidate_schema_cache()
        with _patch_config(software_kb["config"]):
            result = runner.invoke(
                app,
                ["create", "-k", "proj", "-t", "note", "--template", "--format", "json"],
            )
            skeleton = _template_frontmatter(result)
        assert skeleton["severity"] == "low"
        assert skeleton["importance"] == 5

    def test_template_undeclared_type_refuses_like_create(self, software_kb):
        """An undeclared type is refused with the create's error."""
        with _patch_config(software_kb["config"]):
            template = runner.invoke(
                app,
                ["create", "-k", "proj", "-t", "nosuch", "--template", "--format", "json"],
            )
            assert template.exit_code == 1
            template_error = json.loads(template.output)

            created = runner.invoke(
                app,
                ["create", "-k", "proj", "-t", "nosuch", "--title", "y", "--format", "json"],
            )
            assert created.exit_code == 1
            created_error = json.loads(created.output)
        assert template_error["error_code"] == "UNDECLARED_TYPE"
        assert template_error == created_error

    def test_template_undeclared_type_allowed_with_flag(self, software_kb):
        """--allow-undeclared lets the skeleton through, as it lets a create through."""
        with _patch_config(software_kb["config"]):
            result = runner.invoke(
                app,
                [
                    "create",
                    "-k",
                    "proj",
                    "-t",
                    "nosuch",
                    "--template",
                    "--allow-undeclared",
                    "--format",
                    "json",
                ],
            )
            skeleton = _template_frontmatter(result)
        assert skeleton["type"] == "nosuch"
        assert skeleton["importance"] == 5

    def test_template_unknown_kb_refuses(self, software_kb):
        """A -k that names no KB is refused, as a create is."""
        with _patch_config(software_kb["config"]):
            result = runner.invoke(
                app,
                ["create", "-k", "nope", "-t", "note", "--template", "--format", "json"],
            )
        assert result.exit_code == 1
        assert json.loads(result.output)["error_code"] == "KB_NOT_FOUND"

    def test_template_without_kb_is_unchanged(self, software_kb):
        """Without -k, the skeleton is today's core-types one."""
        with _patch_config(software_kb["config"]):
            result = runner.invoke(
                app, ["create", "-t", "person", "--template", "--format", "json"]
            )
            skeleton = _template_frontmatter(result)
        assert skeleton == {
            "id": "",
            "type": "person",
            "title": "Your Title Here",
            "tags": [],
            "role": "",
            "affiliations": [],
        }
