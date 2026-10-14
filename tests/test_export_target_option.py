"""`pyrite export` names its destination with --target (#873).

`--format` is the shared output-format option of the outcome contract
(ADR-0046), so the export destination moved to `--target`. `--format`/`-f`
keeps working for one release as a deprecated alias, as `-f` did in #303.
Every test drives the real CLI (`pyrite.cli.app`) against a temporary KB.
"""

import contextlib
import re
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.models.core_types import NoteEntry
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager

from .cli_help import plain

runner = CliRunner()


@pytest.fixture
def config(tmp_path):
    kb_path = tmp_path / "notes"
    kb_path.mkdir()
    for i in range(2):
        NoteEntry(id=f"n{i}", title=f"Note {i}", body=f"body {i}").save(kb_path / f"n{i}.md")
    (kb_path / "stuff.md").write_text(
        "---\nid: stuff\ntitle: Stuff\ntype: collection\nsource_type: query\n"
        "query: 'type:note'\n---\n"
    )
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
        return runner.invoke(app, ["export", *args])


def _say(result):
    """Everything the command printed, as plain words: no ANSI, no Rich box
    borders, no wrapping, so assertions hold under FORCE_COLOR and any COLUMNS."""
    text = plain(result.output + result.stderr)
    return re.sub(r"[\s│╭╮╰╯─┃┏┓┗┛━]+", " ", text)


def _files(path):
    """name -> bytes of every file written, so the alias is compared on content."""
    return {
        str(p.relative_to(path)): p.read_bytes() for p in sorted(path.rglob("*")) if p.is_file()
    }


SITE = ["site", "-k", "notes"]
COLLECTION = ["collection", "q:type:note", "-k", "notes"]


class TestSiteTarget:
    def test_target_writes_the_site(self, config, tmp_path):
        out = tmp_path / "t"
        result = _export(config, *SITE, "-o", str(out), "--target", "quartz")
        assert result.exit_code == 0, result.output
        assert _files(out)
        assert "deprecated" not in result.stderr.lower()

    @pytest.mark.parametrize("flag", ["--format", "-f"])
    def test_old_spelling_writes_the_same_files_and_warns_once(self, config, tmp_path, flag):
        new, old = tmp_path / "new", tmp_path / "old"
        assert _export(config, *SITE, "-o", str(new), "--target", "quartz").exit_code == 0
        result = _export(config, *SITE, "-o", str(old), flag, "quartz")
        assert result.exit_code == 0, result.output
        assert _files(old) == _files(new)
        warnings = [ln for ln in result.stderr.splitlines() if "deprecated" in ln.lower()]
        assert len(warnings) == 1, result.stderr
        assert "--target" in warnings[0]

    def test_default_is_unchanged(self, config, tmp_path):
        out = tmp_path / "d"
        result = _export(config, *SITE, "-o", str(out))
        assert result.exit_code == 0, result.output
        assert _files(out)

    @pytest.mark.parametrize("flag", ["--target", "--format", "-f"])
    def test_unknown_target_is_refused_before_anything_is_written(self, config, tmp_path, flag):
        out = tmp_path / "x"
        result = _export(config, *SITE, "-o", str(out), flag, "hugo")
        assert result.exit_code == 2
        text = _say(result)
        assert "hugo" in text and "quartz" in text
        assert not out.exists()

    def test_same_value_on_both_spellings_is_accepted_and_warns_once(self, config, tmp_path):
        out = tmp_path / "x"
        result = _export(config, *SITE, "-o", str(out), "--target", "quartz", "-f", "quartz")
        assert result.exit_code == 0, result.output
        assert len([ln for ln in result.stderr.splitlines() if "deprecated" in ln.lower()]) == 1

    def test_conflicting_target_and_format_is_a_usage_error(self, config, tmp_path):
        result = _export(
            config, *SITE, "-o", str(tmp_path / "x"), "--target", "quartz", "--format", "hugo"
        )
        assert result.exit_code == 2
        text = _say(result)
        assert "--target" in text and "--format" in text
        assert not (tmp_path / "x").exists()

    def test_help_shows_target_and_hides_the_old_spelling(self, config):
        result = _export(config, "site", "--help")
        text = _say(result)
        assert "--target" in text
        assert "--format" not in text


class TestCollectionTarget:
    def test_target_writes_the_export(self, config, tmp_path):
        out = tmp_path / "t"
        result = _export(config, *COLLECTION, "-o", str(out), "--target", "notebooklm")
        assert result.exit_code == 0, result.output
        assert (out / "_manifest.md").exists()

    @pytest.mark.parametrize("flag", ["--format", "-f"])
    def test_old_spelling_writes_the_same_files_and_warns_once(self, config, tmp_path, flag):
        new, old = tmp_path / "new", tmp_path / "old"
        assert _export(config, *COLLECTION, "-o", str(new), "--target", "notebooklm").exit_code == 0
        result = _export(config, *COLLECTION, "-o", str(old), flag, "notebooklm")
        assert result.exit_code == 0, result.output
        assert _files(old) == _files(new)
        warnings = [ln for ln in result.stderr.splitlines() if "deprecated" in ln.lower()]
        assert len(warnings) == 1, result.stderr
        assert "--target" in warnings[0]

    def test_default_is_unchanged(self, config, tmp_path):
        result = _export(config, *COLLECTION, "-o", str(tmp_path / "d"))
        assert result.exit_code == 0, result.output

    @pytest.mark.parametrize("flag", ["--target", "--format", "-f"])
    def test_unknown_target_is_refused_before_anything_is_written(self, config, tmp_path, flag):
        out = tmp_path / "x"
        result = _export(config, *COLLECTION, "-o", str(out), flag, "hugo")
        assert result.exit_code == 2
        text = _say(result)
        assert "hugo" in text and "notebooklm" in text
        assert not out.exists()

    def test_same_value_on_both_spellings_is_accepted_and_warns_once(self, config, tmp_path):
        result = _export(
            config,
            *COLLECTION,
            "-o",
            str(tmp_path / "x"),
            "--target",
            "notebooklm",
            "-f",
            "notebooklm",
        )
        assert result.exit_code == 0, result.output
        assert len([ln for ln in result.stderr.splitlines() if "deprecated" in ln.lower()]) == 1

    def test_conflicting_target_and_format_is_a_usage_error(self, config, tmp_path):
        result = _export(
            config,
            *COLLECTION,
            "-o",
            str(tmp_path / "x"),
            "--target",
            "notebooklm",
            "-f",
            "other",
        )
        assert result.exit_code == 2
        text = _say(result)
        assert "--target" in text and "--format" in text

    def test_help_shows_target_and_hides_the_old_spelling(self, config):
        result = _export(config, "collection", "--help")
        text = _say(result)
        assert "--target" in text
        assert "--format" not in text
