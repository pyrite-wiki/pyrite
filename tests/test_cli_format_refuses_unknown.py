"""Every ``--format`` that names an *output* format refuses a value it cannot
render, with exit 2 and the shared message, before the command acts (#547).

The list of commands is the registry, not a hand-kept table: every registered
command whose ``--format`` option is not a file format. A new command with a
``--format`` option joins the test by existing. A command is run for real
(``CliRunner`` on its root app, in the scratch HOME and config that
``tests/conftest.py`` gives every test, with a scratch working directory) and
the working directory and the config are compared before and after: ``init``
used to create and register the KB and then crash on the format, so "exit 2" is
not enough, nothing may be written.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click
import pytest
from typer.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from cli_help import plain  # noqa: E402
from test_requested_effect_exit_code import _commands, _on_the_contract  # noqa: E402

#: ``--format`` on these names a *file* format (import: json, yaml, markdown;
#: export: notebooklm, quartz, json, markdown), not an output format. They are
#: refused in their bodies today and are #873/#878's, not this fix's.
FILE_FORMAT_OPTIONS = {
    "pyrite import",
    "pyrite export collection",
    "pyrite export site",
    "pyrite investigation export",
}

#: A command that cannot be invoked safely in a test, with the reason. Empty: the
#: format is checked while the command line is parsed, so no command needs a
#: server, a network or a model to be asked for a format it refuses.
NOT_INVOKABLE: dict[str, str] = {}


def _format_option(cmd):
    return next((p for p in cmd.params if "--format" in getattr(p, "opts", ())), None)


def _with_a_format_option() -> list[str]:
    return sorted(
        name
        for name, cmd in _commands().items()
        if _format_option(cmd) is not None
        and name not in FILE_FORMAT_OPTIONS
        and not _on_the_contract(
            cmd
        )  # those refuse through OUTPUT_FORMAT, tested in test_cli_outcome
    )


def _required_args(cmd) -> list[str]:
    """Placeholder values for whatever the command insists on, so that parsing
    reaches the ``--format`` check rather than stopping at a missing argument."""
    args: list[str] = []
    for p in cmd.params:
        if not p.required or p.name in ("help",):
            continue
        value = p.type.choices[0] if isinstance(p.type, click.Choice) else "x"
        if isinstance(p, click.Argument):
            args.append(value)
        else:
            args += [p.opts[0], value]
    return args


def _tree(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*")}


def _root_app(name: str):
    from pyrite import admin_cli, read_cli
    from pyrite.cli import app

    return {"pyrite": app, "pyrite-read": read_cli.app, "pyrite-admin": admin_cli.app}[
        name.split()[0]
    ]


def test_the_registry_walk_finds_the_legacy_format_options():
    """The parametrization below is only as good as the walk: a walk that found
    nothing would pass vacuously."""
    names = _with_a_format_option()
    assert len(names) >= 60, names
    for expected in ("pyrite init", "pyrite list", "pyrite sw backlog", "pyrite qa fix"):
        assert expected in names


@pytest.mark.parametrize("command", _with_a_format_option())
def test_format_bogus_is_refused_before_the_command_acts(command, tmp_path, monkeypatch):
    if command in NOT_INVOKABLE:
        pytest.skip(NOT_INVOKABLE[command])
    from pyrite.config import CONFIG_FILE

    cmd = _commands()[command]
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("PYRITE_FORMAT", raising=False)
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("COLUMNS", "200")
    before = _tree(tmp_path)
    config_before = CONFIG_FILE.read_bytes() if CONFIG_FILE.exists() else None

    argv = [*command.split()[1:], *_required_args(cmd), "--format", "bogus"]
    result = CliRunner().invoke(_root_app(command), argv)

    # click draws usage errors in a box that wraps at the terminal width
    out = " ".join(plain(result.output).replace("│", " ").split())
    assert result.exit_code == 2, f"{command}: exit {result.exit_code}\n{out}"
    assert "unknown format 'bogus'; choose one of:" in out, f"{command}:\n{out}"
    assert "Traceback" not in out and not (
        result.exception and not isinstance(result.exception, SystemExit)
    ), f"{command}: {result.exception!r}\n{out}"
    assert _tree(tmp_path) == before, f"{command} wrote something before refusing"
    assert (CONFIG_FILE.read_bytes() if CONFIG_FILE.exists() else None) == config_before, (
        f"{command} changed the config before refusing"
    )
