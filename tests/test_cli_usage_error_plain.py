"""A usage error is a JSON refusal on stdout, exit 2 (#303; ADR-0046, amendment
2026-10-15), whatever route Typer would have drawn it by.

Typer draws a click error in ``typer.core._main``: a Rich box on stderr (rich
installed), click's three plain lines (``TYPER_USE_RICH=0``, rich missing), or
an exception to the caller (``standalone_mode=False``). ``PyriteCLIGroup``
answers it before Typer sees it, so the document does not depend on the route.

Routes a click error or ``Abort`` can take out of ``PyriteCLIGroup.main``
(the table is in ``_answered``'s docstring) and the test that enters each:

==========================  =====================================  ==========
route                       outcome                                test
==========================  =====================================  ==========
rich on                     document, exit 2                       walk, smoke
``TYPER_USE_RICH=0``        same                                   smoke
rich not installed          same                                   no_rich
non-standalone              the exception, as before               non_standalone
``Abort`` / EOF at prompt   ``Aborted!`` on stderr, exit 1         abort
non-usage ClickException    document ``ERROR``, its exit code      non_usage
==========================  =====================================  ==========

Every command is walked in-process (``CliRunner`` through the root's ``main``,
as the console script does); one subprocess per root enters the real entry
point with stderr piped, and one pty puts a terminal on stderr.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from pyrite.utils.errors import PyriteCLIGroup, _click_exception, _format_in
from tests.outcome_walk import ROOTS, leaves, root_app

#: Box-drawing characters Rich uses for its panels, and any ANSI escape.
BOX = re.compile("[─-╿]")
ANSI = "\x1b"

BAD_OPTION = "--no-such-option-zz"


def _every_command() -> list[tuple[str, tuple[str, ...]]]:
    # A command that takes any arguments (a removed command's stub, say) has no
    # unknown option to reject.
    return [
        (root, path)
        for root in ROOTS
        for path, cmd in leaves(typer.main.get_command(root_app(root)))
        if not (
            cmd.context_settings.get("ignore_unknown_options")
            or cmd.context_settings.get("allow_extra_args")
        )
    ]


COMMANDS = _every_command()


def _assert_refusal(stdout: str, stderr: str, code: int, *, names: str | None = None) -> dict:
    assert code == 2, (code, stdout, stderr)
    assert not BOX.search(stderr), stderr
    assert ANSI not in stderr, repr(stderr)
    doc = json.loads(stdout)  # one document, nothing else
    assert doc["outcome"] == "error"
    assert doc["error_code"] == "USAGE_ERROR"
    assert doc["retryable"] is False
    assert doc["error"]
    assert "--help" in doc["suggestion"]
    if names:
        assert names in doc["error"], doc
    return doc


def _invoke(root: str, args: list[str], **kwargs):
    result = CliRunner().invoke(root_app(root), args, **kwargs)
    return result.stdout, result.stderr, result.exit_code


@pytest.mark.control(reason="guards the walk itself: it passes with or without the fix")
def test_the_walk_finds_the_commands():
    """A walk that finds nothing would make the parametrised test vacuous."""
    assert len(COMMANDS) > 100
    assert {root for root, _ in COMMANDS} == set(ROOTS)


@pytest.mark.parametrize(
    ("root", "path"), COMMANDS, ids=[f"{r}:{' '.join(p)}" for r, p in COMMANDS]
)
def test_unknown_option_is_a_json_refusal_on_every_command(root, path):
    _assert_refusal(*_invoke(root, [*path, BAD_OPTION]), names=BAD_OPTION)


@pytest.mark.parametrize(
    ("root", "path"), COMMANDS, ids=[f"{r}:{' '.join(p)}" for r, p in COMMANDS]
)
def test_unknown_option_is_a_json_refusal_without_rich(root, path, monkeypatch):
    """Rich not installed: Typer takes click's own ``e.show()`` route."""
    monkeypatch.setattr("typer.core.HAS_RICH", False)
    _assert_refusal(*_invoke(root, [*path, BAD_OPTION]), names=BAD_OPTION)


@pytest.mark.parametrize("root", list(ROOTS))
def test_unknown_command_is_a_json_refusal(root):
    _assert_refusal(*_invoke(root, ["no-such-command-zz"]), names="no-such-command-zz")


@pytest.mark.parametrize("root", list(ROOTS))
def test_an_error_in_the_roots_own_arguments_is_a_json_refusal(root):
    _assert_refusal(*_invoke(root, [BAD_OPTION]), names=BAD_OPTION)


def test_missing_argument_is_a_json_refusal():
    doc = _assert_refusal(*_invoke("pyrite", ["get"]))
    assert "Missing" in doc["error"]


def test_bad_choice_is_a_json_refusal():
    _assert_refusal(
        *_invoke("pyrite", ["kb", "list", "--format", "no-such-format-zz"]),
        names="no-such-format-zz",
    )


def test_bad_pyrite_format_environment_is_a_json_refusal():
    _assert_refusal(*_invoke("pyrite", ["task", "list"], env={"PYRITE_FORMAT": "xml"}), names="xml")


def test_format_flag_and_short_flag_together_is_a_json_refusal():
    _assert_refusal(*_invoke("pyrite", ["task", "list", "-f", "json", "--format", "json"]))


def test_explicit_format_yaml_prints_the_refusal_as_yaml():
    stdout, stderr, code = _invoke("pyrite", ["task", "list", "--format", "yaml", BAD_OPTION])
    assert code == 2
    assert "outcome: error" in stdout and "USAGE_ERROR" in stdout
    assert not BOX.search(stderr)


def test_non_standalone_callers_still_get_the_exception():
    """``standalone_mode=False`` wants the click error, not a printed answer."""
    command = typer.main.get_command(root_app("pyrite"))
    with pytest.raises(_click_exception()) as raised:
        command.main(["kb", "list", BAD_OPTION], standalone_mode=False)
    assert BAD_OPTION in str(raised.value.format_message())


def test_the_standalone_mark_does_not_outlive_the_run():
    """After an answered error, a later non-standalone call raises again."""
    command = typer.main.get_command(root_app("pyrite"))
    with pytest.raises(SystemExit) as exited:
        command.main(["kb", "list", BAD_OPTION])
    assert exited.value.code == 2
    with pytest.raises(_click_exception()):
        command.main(["kb", "list", BAD_OPTION], standalone_mode=False)


def _planted_app(body) -> typer.Typer:
    app = typer.Typer(cls=PyriteCLIGroup)

    @app.command("plant")
    def plant() -> None:  # a body that is not on the shared-format contract
        body()

    @app.command("other")
    def other() -> None:
        pass

    return app


def test_abort_at_a_prompt_is_aborted_on_stderr_exit_1():
    app = _planted_app(lambda: typer.prompt("name"))
    result = CliRunner().invoke(app, ["plant"], input="")  # EOF at the prompt
    assert result.exit_code == 1
    assert "Aborted!" in result.stderr
    assert BOX.search(result.stderr) is None
    assert "{" not in result.stdout


def test_a_raised_abort_is_aborted_on_stderr_exit_1():
    def body():
        raise typer.Abort()

    result = CliRunner().invoke(_planted_app(body), ["plant"])
    assert result.exit_code == 1
    assert result.stderr == "Aborted!\n"
    assert result.stdout == ""


def test_a_non_usage_click_exception_is_a_document_with_its_own_exit_code():
    class Refused(_click_exception()):  # type: ignore[misc]
        exit_code = 1

    def body():
        raise Refused("cannot do that")

    result = CliRunner().invoke(_planted_app(body), ["plant"])
    doc = json.loads(result.stdout)
    assert result.exit_code == 1
    assert doc["error_code"] == "ERROR"
    assert doc["error"] == "cannot do that"
    assert "Error: cannot do that" in result.stderr


def test_typer_still_behaves_the_way_the_root_relies_on():
    """Canary. ``PyriteCLIGroup`` answers a click error by raising ``typer.Exit``
    from inside the group, so Typer must (1) end a standalone run with
    ``sys.exit(code)`` for an ``Exit`` and for an ``Abort`` (1), (2) return the
    code when not standalone, (3) hand the click classes out through Typer's
    public names, and (4) pass a group's own ``Exit`` through ``make_context``
    and ``invoke``. Only public classes and behaviour are checked, so it holds
    across the allowed range (``typer>=0.12.3``; the classes' module paths
    differ between releases and are never named). If it fails after a Typer
    upgrade, ``_answered`` in pyrite/utils/errors.py needs a new route."""

    class Plain(typer.core.TyperGroup):
        def invoke(self, ctx):
            raise typer.Exit(2)

    class Aborting(typer.core.TyperGroup):
        def invoke(self, ctx):
            raise typer.Abort()

    command = Plain(name="x")
    with pytest.raises(SystemExit) as exited:
        command.main([])
    assert exited.value.code == 2
    assert command.main([], standalone_mode=False) == 2
    with pytest.raises(SystemExit) as aborted:
        Aborting(name="x").main([])
    assert aborted.value.code == 1
    assert _click_exception().__name__ == "ClickException"
    assert any(c.__name__ == "UsageError" for c in typer.BadParameter.__mro__)


@pytest.mark.parametrize("root", list(ROOTS))
@pytest.mark.parametrize("rich", [True, False], ids=["rich", "no-rich"])
def test_bare_invocation_is_help_not_a_document(root, rich, monkeypatch):
    """``no_args_is_help``: click raises ``NoArgsIsHelpError``, a ``UsageError``
    that is help. Dev prints the help (stdout with rich, stderr without) and
    exits 2; no document, no empty ``Error:`` line."""
    monkeypatch.setattr("typer.core.HAS_RICH", rich)
    stdout, stderr, code = _invoke(root, [])
    assert code == 2
    help_text = stdout if rich else stderr
    assert "Usage:" in help_text and "Commands" in help_text
    assert "USAGE_ERROR" not in stdout + stderr
    assert (stderr if rich else stdout) == ""


@pytest.mark.parametrize("root", list(ROOTS))
def test_real_entry_point_bare_invocation_without_rich(root):
    proc = _run(root, [], TYPER_USE_RICH="0")
    assert proc.returncode == 2
    assert "Usage:" in proc.stderr and proc.stdout == ""


def _printing_then(error):
    def body():
        typer.echo("partial result")
        raise error

    return body


@pytest.mark.parametrize(
    "error",
    [typer.BadParameter("second item bad"), _click_exception()("cannot do that")],
    ids=["usage", "other"],
)
def test_text_then_a_click_error_keeps_the_error_on_stderr(error):
    """stdout is one thing: the command's own text, or the document."""
    result = CliRunner().invoke(_planted_app(_printing_then(error)), ["plant"])
    assert result.stdout == "partial result\n"
    assert "Error:" in result.stderr
    assert result.exit_code == error.exit_code


def test_a_click_error_before_any_output_is_still_a_document():
    def body():
        raise typer.BadParameter("bad before output")

    doc = json.loads(CliRunner().invoke(_planted_app(body), ["plant"]).stdout)
    assert doc["error_code"] == "USAGE_ERROR"


def test_a_closed_stdout_is_left_alone(monkeypatch):
    """``sys.stdout`` is None when descriptor 1 was closed at start."""
    from pyrite.utils.errors import _answered, _STANDALONE

    token = _STANDALONE.set(True)
    try:
        monkeypatch.setattr(sys, "stdout", None)
        with _answered(watch_stdout=True):
            assert sys.stdout is None
    finally:
        _STANDALONE.reset(token)


def test_standalone_mode_is_read_from_positional_arguments_too():
    command = typer.main.get_command(root_app("pyrite"))
    with pytest.raises(_click_exception()):
        command.main(["kb", "list", BAD_OPTION], "pyrite", None, False)


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["task", "list", "-f", "yaml"], "yaml"),
        (["task", "list", "--format=yaml"], "yaml"),
        (["task", "list", "--format", "xml"], "json"),
        (["export", "--format", "yaml", BAD_OPTION], "json"),
        (["export", "-f", "yaml"], "json"),
    ],
)
def test_the_format_a_usage_error_is_printed_in(argv, expected, monkeypatch):
    monkeypatch.delenv("PYRITE_FORMAT", raising=False)
    assert _format_in(argv) == expected


# -- the real entry point ----------------------------------------------------


def _entry_point(root: str) -> str:
    return str(Path(sys.executable).with_name(root))


def _env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in ("PYRITE_FORMAT", "FORCE_COLOR")}
    env.pop("NO_COLOR", None)
    env.update(extra)
    return env


def _run(root: str, args: list[str], **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_entry_point(root), *args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
        env=_env(**env),
    )


@pytest.mark.parametrize("root", list(ROOTS))
@pytest.mark.parametrize("rich", ["1", "0"], ids=["rich", "TYPER_USE_RICH=0"])
def test_real_entry_point_stderr_piped(root, rich):
    proc = _run(
        root, ["config" if root != "pyrite-admin" else "kb", BAD_OPTION], TYPER_USE_RICH=rich
    )
    _assert_refusal(proc.stdout, proc.stderr, proc.returncode, names=BAD_OPTION)


def test_main_entry_is_a_json_refusal():
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.argv[1:] = ['kb', 'list', sys.argv[1]]; from pyrite.cli import main; main()",
            BAD_OPTION,
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
        env=_env(),
    )
    _assert_refusal(proc.stdout, proc.stderr, proc.returncode, names=BAD_OPTION)


def test_help_is_unchanged_when_stderr_is_piped():
    """--help is answered on stdout and stays Rich; only errors are documents."""
    proc = _run("pyrite", ["--help"])
    assert proc.returncode == 0
    assert "Usage" in proc.stdout


@pytest.mark.skipif(sys.platform == "win32", reason="needs a pty")
def test_a_terminal_on_stderr_gets_the_same_document_and_no_box():
    """The answer does not depend on a terminal: stderr on a pty, stdout piped."""
    import pty

    master, slave = pty.openpty()
    try:
        proc = subprocess.Popen(
            [_entry_point("pyrite"), "kb", "list", BAD_OPTION],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=slave,
            env=_env(COLUMNS="100"),
        )
        out, _ = proc.communicate(timeout=120)
        # Read before the slave end closes: macOS drops unread bytes with it.
        os.set_blocking(master, False)
        try:
            text = os.read(master, 65536).decode("utf-8", errors="replace")
        except BlockingIOError:
            text = ""
    finally:
        os.close(master)
        os.close(slave)
    assert proc.returncode == 2
    assert json.loads(out)["error_code"] == "USAGE_ERROR"
    assert BAD_OPTION in text  # the one plain line for a person
    assert not BOX.search(text), text
