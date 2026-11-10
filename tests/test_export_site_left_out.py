"""`pyrite export site` names every entry it leaves out and exits non-zero (#1325).

A row whose file cannot be loaded is no longer dropped silently: the export
prints the entry and the loader's reason, and the exit code follows the
0/3/1 rule of ADR-0046 (`exit_unless_whole`). `--exclude-types` and
`--exclude-status` are asked-for filters, not left-out entries, and stay 0.
"""

import contextlib
import re
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager

from .cli_help import plain

runner = CliRunner()

BAD = "---\nid: {id}\ntitle: {id}\ntype: note\n---\nbody\n"
NO_FRONTMATTER = "just a body, no frontmatter\n"


@pytest.fixture
def config(tmp_path):
    kb_path = tmp_path / "notes"
    kb_path.mkdir()
    (kb_path / "good.md").write_text("---\nid: good\ntitle: Good\ntype: note\n---\nbody\n")
    for name in ("bad1", "bad2"):
        (kb_path / f"{name}.md").write_text(BAD.format(id=name))
    cfg = PyriteConfig(
        knowledge_bases=[KBConfig(name="notes", path=kb_path, kb_type=KBType.GENERIC)],
        settings=Settings(index_path=tmp_path / "index.db", workspace_path=tmp_path / "ws"),
    )
    db = PyriteDB(cfg.settings.index_path)
    IndexManager(db, cfg).index_all()
    db.close()
    return cfg


@contextlib.contextmanager
def _in(config):
    import pyrite.cli  # noqa: F401
    import pyrite.cli.context  # noqa: F401

    with (
        patch("pyrite.config.load_config", return_value=config),
        patch("pyrite.cli.load_config", return_value=config),
        patch("pyrite.cli.context.load_config", return_value=config),
    ):
        yield


def _export(config, *args):
    from pyrite.cli import app

    with _in(config):
        return runner.invoke(app, ["export", "site", "-k", "notes", *args])


def _say(result):
    """Printed words only: no ANSI, no Rich box borders, no wrapping."""
    text = plain(result.output + result.stderr)
    return re.sub(r"[\s│╭╮╰╯─┃┏┓┗┛━]+", " ", text)


def _break(config, name, how):
    path = config.knowledge_bases[0].path / f"{name}.md"
    if how == "invalid-yaml":
        path.write_text(f"---\nid: {name}\ntitle: [unclosed\ntype: note\n---\nbody\n")
    elif how == "no-frontmatter":
        path.write_text(NO_FRONTMATTER)
    elif how == "deleted":
        path.unlink()
    else:  # pragma: no cover - guards the parametrisation
        raise AssertionError(how)


class TestLeftOutEntries:
    @pytest.mark.parametrize("how", ["invalid-yaml", "no-frontmatter", "deleted"])
    def test_one_unloadable_entry_is_named_and_exits_partial(self, config, tmp_path, how):
        _break(config, "bad1", how)
        result = _export(config, "-o", str(tmp_path / "site"))
        text = _say(result)

        assert result.exit_code == 3, text
        assert "1 entry left out" in text, text
        assert "bad1" in text, text
        assert "Exported 2 entries" in text, text

    def test_invalid_yaml_gives_the_loaders_message(self, config, tmp_path):
        _break(config, "bad1", "invalid-yaml")
        text = _say(_export(config, "-o", str(tmp_path / "site")))
        assert "Invalid YAML frontmatter" in text, text

    def test_deleted_file_says_it_is_missing(self, config, tmp_path):
        _break(config, "bad1", "deleted")
        text = _say(_export(config, "-o", str(tmp_path / "site")))
        assert "missing" in text, text

    def test_two_left_out_uses_the_plural_and_names_both(self, config, tmp_path):
        _break(config, "bad1", "invalid-yaml")
        _break(config, "bad2", "deleted")
        result = _export(config, "-o", str(tmp_path / "site"))
        text = _say(result)

        assert result.exit_code == 3, text
        assert "2 entries left out" in text, text
        assert "bad1" in text and "bad2" in text, text
        assert "Exported 1 entry" in text, text
        assert "Exported 1 entries" not in text, text

    def test_nothing_loadable_exits_one(self, config, tmp_path):
        _break(config, "good", "invalid-yaml")
        _break(config, "bad1", "invalid-yaml")
        _break(config, "bad2", "no-frontmatter")
        result = _export(config, "-o", str(tmp_path / "site"))
        text = _say(result)

        assert result.exit_code == 1, text
        assert "3 entries left out" in text, text
        assert "good" in text and "bad1" in text and "bad2" in text, text

    def test_excluded_types_are_not_left_out_and_exit_zero(self, config, tmp_path):
        result = _export(config, "-o", str(tmp_path / "site"), "--exclude-types", "note")
        text = _say(result)

        assert result.exit_code == 0, text
        assert "left out" not in text, text
        assert "Skipped: 3 (filtered)" in text, text

    def test_allow_partial_restores_zero_and_still_names_the_entry(self, config, tmp_path):
        _break(config, "bad1", "invalid-yaml")
        result = _export(config, "-o", str(tmp_path / "site"), "--allow-partial")
        text = _say(result)

        assert result.exit_code == 0, text
        assert "1 entry left out" in text, text
        assert "bad1" in text, text

    def test_allow_partial_covers_nothing_loadable(self, config, tmp_path):
        for name, how in (
            ("good", "invalid-yaml"),
            ("bad1", "invalid-yaml"),
            ("bad2", "no-frontmatter"),
        ):
            _break(config, name, how)
        result = _export(config, "-o", str(tmp_path / "site"), "--allow-partial")
        text = _say(result)

        assert result.exit_code == 0, text
        assert "3 entries left out" in text, text
        assert "No entries could be loaded." in text, text
