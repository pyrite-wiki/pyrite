"""`pyrite.__version__` must not drift from the packaged version.

It sat at 0.12.0 while pyproject.toml reached 0.24.1: a hand-maintained copy
that nothing checked. See
kb/backlog/done/single-source-of-truth-for-the-version-asserted-by-a-test.md.
"""

import re
import tomllib
from importlib import metadata
from pathlib import Path

import pyrite

REPO = Path(__file__).resolve().parent.parent


def test_dunder_version_matches_pyproject():
    declared = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["version"]
    assert pyrite.__version__ == declared


def test_dunder_version_is_not_a_hardcoded_literal():
    source = (REPO / "pyrite" / "__init__.py").read_text()
    assert not re.search(r'^__version__\s*=\s*["\']', source, re.MULTILINE)


def test_falls_back_to_installed_metadata_outside_a_checkout(monkeypatch, tmp_path):
    # Simulate site-packages: no pyproject.toml beside the package.
    monkeypatch.setattr(pyrite, "__file__", str(tmp_path / "pyrite" / "__init__.py"))
    assert pyrite._read_version() == metadata.version("pyrite")


class TestCliVersionFlag:
    """`pyrite --version` is the first thing a user runs after installing.

    Until 0.24.3 it did not exist: the CLI answered `No such option:
    --version`, and nothing noticed because `pyrite.__version__` -- which the
    tests above do check -- is a different surface. The release script's
    step (c) runs this exact command against a fresh install from the release
    SHA, so a missing flag now fails the release rather than the user.
    """

    def _run(self, *argv):
        from typer.testing import CliRunner

        from pyrite.cli import app

        return CliRunner().invoke(app, list(argv))

    def test_version_flag_prints_the_version(self):
        result = self._run("--version")
        assert result.exit_code == 0, result.output
        assert pyrite.__version__ in result.output

    def test_version_flag_does_not_require_a_subcommand(self):
        """`no_args_is_help=True` makes a bare invocation exit non-zero; the
        version option must short-circuit before that, not inherit it."""
        result = self._run("--version")
        assert result.exit_code == 0
        assert "Usage:" not in result.output

    def test_version_flag_needs_no_config_or_kb(self, tmp_path, monkeypatch):
        """It must answer on a machine with no KB configured at all -- that is
        the state someone is in when they run it."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("PYRITE_CONFIG", str(tmp_path / "nonexistent.yaml"))
        result = self._run("--version")
        assert result.exit_code == 0
        assert pyrite.__version__ in result.output
