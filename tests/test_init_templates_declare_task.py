"""Regression tests for #860.

`pyrite task create` was refused in a KB made by `pyrite init -t software` or
`-t zettelkasten` because neither template declared the `task` type (the
maintainer decided on 2026-10-02, in #595's groom, that both declare it).

Four template copies must declare it, since `pyrite init` asks installed
plugins first and falls back to the built-in copy:
  - extensions/software-kb preset + built-in software template
  - extensions/zettelkasten preset + built-in zettelkasten template

The fix adds a deepcopy of TASK_KB_PRESET["types"]["task"] to each. These
tests fail before that fix (task create exits 1 with SCHEMA_VIOLATION) and
pass after, through the real CLI wiring: init -> task create -> index health.
"""

import json
import tempfile
from contextlib import nullcontext as _nullcontext
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

# Exercises init_kb through the CLI runner, as tests/test_init_command.py does.
init_app = typer.Typer()
init_app.command("init")(init_kb)


def _init_kb(template: str, tmpdir: Path, config: PyriteConfig, *, builtin_only: bool):
    """Init a KB, optionally forcing the built-in template path (no plugins)."""
    kb_path = tmpdir / f"kb-{template}"
    kb_name = f"kb-{template}"

    def _no_plugin_kb_presets():
        # Hiding only the KB presets forces the built-in template copy while
        # leaving the rest of the registry (validators, entry types) working
        # for init's indexing step. Named class-method target in
        # pyrite.plugins (traceable) — not an unresolved instance attribute;
        # see tests/test_tests_drive_the_surface_not_the_mock.py.
        return patch(
            "pyrite.plugins.registry.PluginRegistry.get_all_kb_presets",
            return_value={},
        )

    with (
        patch("pyrite.config.load_config", return_value=config),
        patch("pyrite.config.save_config"),
        # builtin_only forces the built-in template copy in init_command.py;
        # otherwise the installed plugin presets take precedence (normal dev
        # setup).
        _no_plugin_kb_presets() if builtin_only else _nullcontext(),
    ):
        result = runner.invoke(
            init_app,
            ["--template", template, "--path", str(kb_path), "--name", kb_name, "--no-examples"],
        )
    assert result.exit_code == 0, result.output
    return kb_path, kb_name


def _template_test_env(tmpdir: Path, config: PyriteConfig, template: str, builtin_only: bool):
    kb_path, kb_name = _init_kb(template, tmpdir, config, builtin_only=builtin_only)
    kb_yaml = load_yaml((kb_path / "kb.yaml").read_text(encoding="utf-8"))
    assert "task" in kb_yaml["types"], f"{template}: kb.yaml does not declare the task type"
    assert (kb_path / "tasks").is_dir(), f"{template}: tasks/ directory not created"
    return kb_name, kb_path


@pytest.mark.cli
@pytest.mark.parametrize("template", ["software", "zettelkasten"])
@pytest.mark.parametrize("builtin_only", [False, True], ids=["plugin-preset", "builtin-template"])
def test_task_create_works_in_fresh_kb(template, builtin_only):
    """task create succeeds without --allow-undeclared in a fresh KB (#860).

    plugin-preset: the extension preset path (normal dev setup).
    builtin-template: the built-in copy in pyrite/cli/init_command.py.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        config = PyriteConfig(knowledge_bases=[], settings=Settings(index_path=tmpdir / "index.db"))
        kb_name, kb_path = _template_test_env(tmpdir, config, template, builtin_only)

        with patch("pyrite.cli.context.load_config", return_value=config):
            create = runner.invoke(
                app, ["task", "create", "--title", "t", "-k", kb_name, "--format", "json"]
            )
        assert create.exit_code == 0, create.output
        assert json.loads(create.stdout)["created"] is True
        # the groom asks that task create "writes tasks/t.md"
        assert (kb_path / "tasks" / "t.md").is_file(), list((kb_path / "tasks").iterdir())

        with patch("pyrite.cli.context.load_config", return_value=config):
            health = runner.invoke(app, ["index", "health", "-k", kb_name])
        assert health.exit_code == 0, health.output
        undeclared = json.loads(health.stdout).get("undeclared_types", [])
        # undeclared_types holds {"kb", "type", "count"} dicts, not type names
        assert not any(t["type"] == "task" for t in undeclared), undeclared
