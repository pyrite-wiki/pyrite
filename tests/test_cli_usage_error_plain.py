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
import tomllib
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


#: First-party extension commands, and the directory each installs from
#: (README.md, "Extensions are installed separately").
EXTENSION_COMMANDS = [
    ("sw", "software-kb"),
    ("zettel", "zettelkasten"),
    ("wiki", "encyclopedia"),
    ("social", "social"),
    ("investigation", "journalism-investigation"),
]

#: Each extension directory's distribution name, written out by hand (checked
#: against the extension's own pyproject.toml below).
DISTRIBUTIONS = {
    "software-kb": "pyrite-software-kb",
    "zettelkasten": "pyrite-zettelkasten",
    "encyclopedia": "pyrite-encyclopedia",
    "social": "pyrite-social",
    "journalism-investigation": "pyrite-journalism-investigation",
}

REPO = Path(__file__).resolve().parent.parent


def _declared_version() -> str:
    return tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["version"]


def _install_line(directory: str) -> str:
    """The line the user is told, spelled out from the property: the form that
    needs no clone first, pinned to the release tag, the clone form after."""
    return (
        f"Install it with 'pip install \"{DISTRIBUTIONS[directory]} @ "
        f"git+https://github.com/pyrite-wiki/pyrite@v{_declared_version()}"
        f"#subdirectory=extensions/{directory}\"', "
        f"or from a clone 'pip install -e extensions/{directory}'."
    )


@pytest.fixture
def bare_pyrite():
    """The ``pyrite`` root with none of the first-party extension commands
    registered, whichever extensions this environment has installed.

    The extension groups are mounted onto the module's one ``app`` the first
    time it is resolved; this resolves it, then hides every name in
    ``EXTENSION_COMMANDS`` for the test, so ``pyrite sw`` is an unknown command
    here exactly as on a fresh install (#1457). Yields the app.
    """
    from pyrite.cli import app

    mounted = app.registered_groups  # resolving it mounts the plugin groups
    hidden = {name for name, _ in EXTENSION_COMMANDS}
    mp = pytest.MonkeyPatch()
    mp.setattr(app, "_groups", [g for g in mounted if g.name not in hidden])
    mp.setattr(
        app,
        "registered_commands",
        [c for c in app.registered_commands if c.name not in hidden],
    )
    try:
        yield app
    finally:
        mp.undo()


def _invoke_app(app: typer.Typer, args: list[str]):
    result = CliRunner().invoke(app, args)
    return result.stdout, result.stderr, result.exit_code


def test_the_bare_root_has_no_extension_commands(bare_pyrite):
    """Guards the fixture: if it hid nothing, or hid too much, every test
    below would pass or fail for the wrong reason."""
    names = set(typer.main.get_command(bare_pyrite).list_commands(None))
    assert not names & {name for name, _ in EXTENSION_COMMANDS}
    assert {"kb", "task"} <= names


@pytest.mark.parametrize(("command", "directory"), EXTENSION_COMMANDS)
def test_unknown_extension_command_names_its_install_line(bare_pyrite, command, directory):
    """#1457: typing an extension command without the extension says how to
    install it. The refusal itself is unchanged: exit 2, USAGE_ERROR, the
    same "No such command" error."""
    stdout, stderr, code = _invoke_app(bare_pyrite, [command, "adrs"])
    assert code == 2, (code, stdout, stderr)
    doc = json.loads(stdout)
    assert doc["outcome"] == "error"
    assert doc["error"] == f"No such command '{command}'."
    assert doc["error_code"] == "USAGE_ERROR"
    assert doc["retryable"] is False
    assert doc["suggestion"] == _install_line(directory)
    assert f"Error: No such command '{command}'." in stderr


def test_an_unknown_command_that_is_no_extension_keeps_the_help_hint(bare_pyrite):
    stdout, _, code = _invoke_app(bare_pyrite, ["no-such-command-zz"])
    assert code == 2
    suggestion = json.loads(stdout)["suggestion"]
    assert suggestion.endswith("Try 'pyrite --help' for help."), suggestion
    assert "pip install" not in suggestion


def test_a_typo_inside_a_group_keeps_the_help_hint(bare_pyrite):
    """The install hint is only for a top-level command: a typo inside an
    installed group is not an extension to install."""
    stdout, _, code = _invoke_app(bare_pyrite, ["kb", "no-such-zz"])
    assert code == 2
    suggestion = json.loads(stdout)["suggestion"]
    assert suggestion.endswith("Try 'pyrite kb --help' for help."), suggestion
    assert "pip install" not in suggestion


def test_a_typo_inside_an_installed_extension_group_keeps_the_help_hint(bare_pyrite):
    """The reverse of the install-line case (#1457 review): with the ``sw``
    group installed, ``pyrite sw no-such-zz`` is a typo inside it, and
    installing the extension would not help. The group here is a stand-in, so
    the case holds whichever extensions are installed."""
    stub = typer.Typer()

    @stub.command("adrs")
    def adrs() -> None: ...

    bare_pyrite.add_typer(stub, name="sw")
    stdout, stderr, code = _invoke_app(bare_pyrite, ["sw", "no-such-zz"])
    assert code == 2, (code, stdout, stderr)
    doc = json.loads(stdout)
    assert doc["error"] == "No such command 'no-such-zz'."
    assert doc["suggestion"].endswith("Try 'pyrite sw --help' for help."), doc
    assert "pip install" not in doc["suggestion"]


def test_a_typo_inside_the_real_sw_group_keeps_the_help_hint():
    """The same, end to end against the real extension, where it is installed."""
    pytest.importorskip("pyrite_software_kb.plugin")
    stdout, stderr, code = _invoke("pyrite", ["sw", "no-such-zz"])
    assert code == 2, (code, stdout, stderr)
    suggestion = json.loads(stdout)["suggestion"]
    assert suggestion.endswith("Try 'pyrite sw --help' for help."), suggestion
    assert "pip install" not in suggestion


def test_the_extension_hint_in_rich_format(bare_pyrite):
    """--format rich prints the new hint on the "hint:" line (#1457)."""
    stdout, stderr, code = _invoke_app(bare_pyrite, ["sw", "--format", "rich"])
    assert code == 2, (code, stdout, stderr)
    assert "USAGE_ERROR" in stdout
    assert "hint:" in stdout
    assert _install_line("software-kb") in stdout


def _root_context(app: typer.Typer):
    return typer.main.get_command(app).make_context("pyrite", [], resilient_parsing=True)


# The messages below are click's usage-error text as Typer flattens it. They
# were written against click 8.5.0 / Typer 0.27.3, where an unknown command
# reads `No such command 'x'.`; the install hint matches that wording, so a
# click release that rewords it fails these unit cases instead of the hint
# going missing silently.
def test_the_hint_is_built_from_the_root_context_alone(bare_pyrite):
    from pyrite.utils.errors import _extension_install_hint

    root = _root_context(bare_pyrite)
    assert _extension_install_hint(root, "No such command 'sw'.") == _install_line("software-kb")
    assert _extension_install_hint(root, "No such command 'nope'.") is None
    assert _extension_install_hint(root, "No such option: --sw") is None
    assert _extension_install_hint(None, "No such command 'sw'.") is None


def test_the_hint_is_not_given_below_the_root(bare_pyrite):
    """A context one level down (``pyrite kb``) or on another root's prog name
    is never offered the install line, even for an extension's own name."""
    from pyrite.utils.errors import _extension_install_hint

    kb = typer.main.get_command(bare_pyrite).get_command(None, "kb")
    child = kb.make_context("kb", [], parent=_root_context(bare_pyrite), resilient_parsing=True)
    assert _extension_install_hint(child, "No such command 'sw'.") is None
    other = typer.main.get_command(bare_pyrite).make_context(
        "pyrite-admin", [], resilient_parsing=True
    )
    assert _extension_install_hint(other, "No such command 'sw'.") is None


EXTENSION_PLUGINS = [
    ("software-kb", "pyrite_software_kb.plugin", "SoftwareKBPlugin"),
    ("zettelkasten", "pyrite_zettelkasten.plugin", "ZettelkastenPlugin"),
    ("encyclopedia", "pyrite_encyclopedia.plugin", "EncyclopediaPlugin"),
    ("social", "pyrite_social.plugin", "SocialPlugin"),
    (
        "journalism-investigation",
        "pyrite_journalism_investigation.plugin",
        "JournalismInvestigationPlugin",
    ),
]


@pytest.mark.parametrize(
    ("directory", "module_name", "class_name"),
    EXTENSION_PLUGINS,
    ids=[d for d, _, _ in EXTENSION_PLUGINS],
)
def test_extension_command_table_matches_the_plugin(directory, module_name, class_name):
    """A renamed extension command cannot leave the table behind (#1457). One
    case per plugin: a plugin that is not installed here is skipped by name,
    and the others are still checked."""
    from pyrite.utils.errors import _EXTENSION_COMMAND_INSTALLS

    module = pytest.importorskip(module_name)
    assert Path(module.__file__).resolve().parents[2].name == directory
    names = {name for name, _ in getattr(module, class_name)().get_cli_commands()}
    assert names, module_name
    in_table = {n for n, d in _EXTENSION_COMMAND_INSTALLS.items() if d == directory}
    assert in_table == names


def test_every_table_row_belongs_to_a_known_extension():
    """Whatever is installed: no row names a directory the per-plugin cases do
    not check."""
    from pyrite.utils.errors import _EXTENSION_COMMAND_INSTALLS

    assert set(_EXTENSION_COMMAND_INSTALLS.values()) == {d for d, _, _ in EXTENSION_PLUGINS}
    assert _EXTENSION_COMMAND_INSTALLS == dict(EXTENSION_COMMANDS)


def test_unknown_subcommand_names_the_groups_commands():
    doc = _assert_refusal(*_invoke("pyrite", ["links", "validate"]), names="validate")
    assert "pyrite links has:" in doc["suggestion"]
    assert "check" in doc["suggestion"]
    assert "--help" in doc["suggestion"]


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


def test_the_install_line_is_pinned_to_the_running_version(monkeypatch):
    """A version bump moves the hint with it: the tag in the line is the running
    Pyrite's ``__version__``, which is the version pyproject.toml declares."""
    import pyrite
    from pyrite.utils.errors import extension_install_line

    assert pyrite.__version__ == _declared_version()
    assert f"@v{_declared_version()}#subdirectory=" in extension_install_line("software-kb")
    monkeypatch.setattr(pyrite, "__version__", "9.8.7")
    assert "@v9.8.7#subdirectory=extensions/social" in extension_install_line("social")
    assert "@v0.26" not in extension_install_line("social")


def test_an_unknown_version_gives_only_the_clone_form(monkeypatch):
    """With no release tag to pin (``0+unknown``) the no-clone form would name
    a tag that does not exist; the clone form is all that is true."""
    import pyrite
    from pyrite.utils.errors import extension_install_line

    monkeypatch.setattr(pyrite, "__version__", "0+unknown")
    line = extension_install_line("software-kb")
    assert "git+https" not in line
    assert "pip install -e extensions/software-kb" in line


@pytest.mark.parametrize(("directory", "dist"), sorted(DISTRIBUTIONS.items()))
def test_the_distribution_name_is_the_extensions_own(directory, dist):
    """Whatever is installed: the name in the line is the ``name`` in that
    extension's pyproject.toml, so the pip line names a real distribution."""
    from pyrite.utils.errors import _EXTENSION_DISTRIBUTIONS

    declared = tomllib.loads((REPO / "extensions" / directory / "pyproject.toml").read_text())
    assert declared["project"]["name"] == dist
    assert _EXTENSION_DISTRIBUTIONS[directory] == dist
