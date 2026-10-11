"""Ways out of a CLI command, planted through the real root (ADR-0046, decision 6).

Support for ``tests/test_cli_outcome.py``. Nothing here names a command: the
commands come from the registry (every leaf of ``pyrite``, ``pyrite-admin`` and
``pyrite-read`` that declares the shared ``--format`` option), and a shape is a
function that stands in for a command's body. A command that joins the contract
later is walked because it declares the option.

Three things Typer makes necessary:

- It builds a new click tree on every call, so a body is planted on the Typer
  registration (``CommandInfo.callback``), not on a click command.
- It reads the parameters from the function, so the stand-in keeps the
  original's signature (``functools.wraps``).
- Typer 0.27 vendors click: a click class is found from Typer's own public
  names, never by a private import path.

Run as a module it plants one shape and runs the root in this process, for the
tests that need a real process (descriptor 1, a stream kept from import, stdout
closed)::

    python -m tests.outcome_walk [--say-first TEXT] <root> <shape> <command path...> -- <args...>
"""

from __future__ import annotations

import functools
import importlib
import json
import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import typer
from rich.console import Console

from pyrite.cli.outcome import Outcome
from pyrite.cli.output import declares_shared_format
from pyrite.exceptions import KBNotFoundError

ROOTS = {
    "pyrite": "pyrite.cli",
    "pyrite-admin": "pyrite.admin_cli",
    "pyrite-read": "pyrite.read_cli",
}

#: What a planted body writes. If it is on stdout, the body got around the root.
MARK = "PLANTED-BY-THE-BODY"

# What a module can hold from import time, before any watch exists. In a real
# process these are the process's stdout; under pytest they are pytest's
# capture, which is why the shapes that use them run in a real process only.
_STREAM_AT_IMPORT = sys.stdout
_CONSOLE_AT_IMPORT = Console(file=sys.stdout)


def root_app(root: str) -> typer.Typer:
    return importlib.import_module(ROOTS[root]).app


# --------------------------------------------------------------------------
# The registry
# --------------------------------------------------------------------------


def leaves(command, path: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], object]]:
    """Every leaf click command under ``command``, with its path."""
    # Typer vendors its own click: test for the group interface, not click.Group.
    if hasattr(command, "list_commands"):
        ctx = command.make_context(path[-1] if path else "root", [], resilient_parsing=True)
        for name in command.list_commands(ctx):
            yield from leaves(command.get_command(ctx, name), (*path, name))
    else:
        yield path, command


def contract_commands(root: str) -> list[tuple[str, ...]]:
    """The paths of the commands of ``root`` that declare the shared option."""
    tree = typer.main.get_command(root_app(root))
    return [path for path, cmd in leaves(tree) if declares_shared_format(cmd)]


def click_command(app: typer.Typer, path: tuple[str, ...]):
    return dict(leaves(typer.main.get_command(app)))[tuple(path)]


def _typers(app: typer.Typer) -> Iterator[typer.Typer]:
    yield app
    for group in app.registered_groups:
        if group.typer_instance is not None:
            yield from _typers(group.typer_instance)


def _registration(app: typer.Typer, path: tuple[str, ...]):
    """The Typer registration behind a click leaf: Typer's click callback wraps
    the registered function, one ``__wrapped__`` down."""
    function = click_command(app, path).callback.__wrapped__
    for instance in _typers(app):
        for info in instance.registered_commands:
            if info.callback is function:
                return info
    raise LookupError(f"no Typer registration for {' '.join(path)}")


@contextmanager
def planted(app: typer.Typer, path: tuple[str, ...], body: Callable[[], object]):
    """Replace the body of the command at ``path`` with ``body`` for the block."""
    info = _registration(app, path)
    original = info.callback

    @functools.wraps(original)
    def stand_in(*args, **kwargs):
        return body()

    info.callback = stand_in
    try:
        yield
    finally:
        info.callback = original


def args_for(command) -> list[str]:
    """Arguments that get past click's parsing for any command: a value for
    each required parameter, by its type. The body is planted, so no value is
    ever used."""
    args: list[str] = []
    for param in command.params:
        if not param.required:
            continue
        kind = getattr(param.type, "name", "")
        if kind in ("integer", "float"):
            value = "1"
        elif getattr(param.type, "choices", None):
            value = str(list(param.type.choices)[0])
        else:
            value = "x"
        args += [value] if param.param_type_name == "argument" else [param.opts[0], value]
    return args


# --------------------------------------------------------------------------
# The click Typer runs, found without a private path
# --------------------------------------------------------------------------


def _typers_click_exception() -> type[Exception]:
    return next(c for c in typer.BadParameter.__mro__ if c.__name__ == "ClickException")


def _current_context():
    """The running command's context, from the click that ``typer.Context`` is
    built on (its ``globals`` module, beside the module that defines Context)."""
    click_package = typer.Context.__mro__[1].__module__.rsplit(".", 1)[0]
    return importlib.import_module(click_package + ".globals").get_current_context()


# --------------------------------------------------------------------------
# Shapes
# --------------------------------------------------------------------------


def _done() -> Outcome:
    return Outcome.done({"n": 1})


class _OwnCode(Outcome):
    """An Outcome that would pick its own exit code."""

    @property
    def exit_code(self) -> int:  # type: ignore[override]
        return 7


class _NotAnException(BaseException):
    pass


class _PrintsWhenSerialised:
    def __str__(self) -> str:
        print(MARK)
        return "x"


def _own_click_error(base: type[Exception]):
    class _Coded(base):  # type: ignore[misc, valid-type]
        exit_code = 7

    return _Coded("the body chose exit 7")


def _raise(exc: BaseException):
    raise exc


# -- exits ------------------------------------------------------------------


def exit_zero():
    raise typer.Exit(0)


def exit_seven():
    raise typer.Exit(7)


def system_exit_zero():
    raise SystemExit(0)


def sys_exit_with_text():
    sys.exit("done")


def abort():
    raise typer.Abort()


def click_error_with_a_code():
    raise _own_click_error(_typers_click_exception())


def pypi_click_error_with_a_code():
    import click  # the PyPI package; an unrelated class under Typer 0.27

    raise _own_click_error(click.ClickException)


def usage_error_from_the_body():
    raise typer.BadParameter("the body found bad input")


def ctx_exit_zero():
    _current_context().exit(0)


def ctx_fail():
    _current_context().fail("the body says usage")


def ctx_abort():
    _current_context().abort()


# -- crashes ----------------------------------------------------------------


def crash():
    raise KeyError("boom")


def crash_outside_exception():
    raise _NotAnException("boom")


# -- stdout, then a normal return -------------------------------------------


def prints():
    print(MARK)
    return _done()


def echoes():
    typer.echo(MARK)
    return _done()


def console_prints():
    Console().print(MARK)
    return _done()


def writes_sys_stdout():
    sys.stdout.write(MARK + "\n")
    return _done()


def writes_stdout_buffer():
    sys.stdout.buffer.write(MARK.encode() + b"\n")
    return _done()


def json_dumps_to_stdout():
    json.dump({"mark": MARK}, sys.stdout)
    return _done()


def writes_descriptor_1():
    os.write(1, MARK.encode() + b"\n")
    return _done()


def child_process_prints():
    subprocess.run([sys.executable, "-c", f"print({MARK!r})"], check=True)
    return _done()


def writes_dunder_stdout_unflushed():
    (sys.__stdout__ or sys.stdout).write(MARK + "\n")
    return _done()


def writes_dev_stdout():
    with open("/dev/stdout", "w") as stream:
        stream.write(MARK + "\n")
    return _done()


def prints_from_a_close_callback():
    _current_context().call_on_close(lambda: print(MARK))
    return _done()


def prints_then_exits_zero():
    print(MARK)
    raise typer.Exit(0)


def prints_then_refuses():
    print(MARK)
    raise KBNotFoundError("KB not found: nope")


def console_kept_from_import_prints():
    _CONSOLE_AT_IMPORT.print(MARK)
    return _done()


def stream_kept_from_import_unflushed():
    _STREAM_AT_IMPORT.write(MARK + "\n")
    return _done()


def closes_sys_stdout():
    sys.stdout.close()
    return _done()


def puts_stdout_back_and_prints():
    sys.stdout = sys.__stdout__ or sys.stdout
    print(MARK, flush=True)
    return _done()


def logs_to_a_handler_on_the_stream_kept_from_import():
    import logging

    handler = logging.StreamHandler(_STREAM_AT_IMPORT)
    logger = logging.getLogger("tests.outcome_walk.planted")
    logger.addHandler(handler)
    try:
        logger.error(MARK)
    finally:
        logger.removeHandler(handler)
    return _done()


def c_stdio_prints_unflushed():
    import ctypes

    ctypes.CDLL(None).printf(MARK.encode() + b"\n")
    return _done()


def switches_the_format_then_returns():
    """Asked for JSON; tries to be rendered by its own renderer instead."""
    meta = _current_context().meta
    for key in list(meta):
        if "output_format" in key and isinstance(meta[key], str):
            meta[key] = "rich"
    return Outcome.done({"n": 1}, rich=lambda console, data: print(MARK))


class _HostileError(Exception):
    @property
    def exit_code(self):
        raise KeyError("no code")

    def __str__(self) -> str:
        raise KeyError("no text")


class _UnprintableValueError(ValueError):
    def __str__(self) -> str:
        raise KeyError("no text")


def raises_an_exception_that_cannot_be_described():
    raise _HostileError()


def raises_a_value_error_that_cannot_be_printed():
    raise _UnprintableValueError()


# -- wrong returns ----------------------------------------------------------


def returns_none():
    return None


def returns_a_dict():
    return {"n": 1}


def returns_zero():
    return 0


def returns_an_outcome_subclass():
    return _OwnCode.done({"n": 1})


def returns_an_outcome_with_a_forged_kind():
    outcome = _done()
    object.__setattr__(outcome, "kind", "done")
    return outcome


def returns_an_outcome_with_a_list_forced_in():
    outcome = _done()
    object.__setattr__(outcome, "data", [1, 2])
    return outcome


def returns_an_outcome_with_its_own_outcome_key_forced_in():
    outcome = _done()
    object.__setattr__(outcome, "data", {"outcome": "done", "n": 1})
    return outcome


# -- rendering --------------------------------------------------------------


def renderer_raises():
    return Outcome.done({"n": 1}, rich=lambda console, data: _raise(KeyError("renderer")))


def renderer_exits_zero():
    return Outcome.done({"n": 1}, rich=lambda console, data: sys.exit(0))


def renderer_raises_typer_exit():
    return Outcome.nothing({"n": 1}, rich=lambda console, data: _raise(typer.Exit(0)))


def renderer_raises_an_exception_that_cannot_be_described():
    return Outcome.done({"n": 1}, rich=lambda console, data: _raise(_HostileError()))


def payload_that_cannot_be_serialised():
    loop: dict = {}
    loop["loop"] = loop
    return Outcome.done(loop)


def payload_that_prints_when_serialised():
    return Outcome.done({"n": _PrintsWhenSerialised()})


# -- controls: what the root allows -----------------------------------------


def returns_done():
    return Outcome.done({"n": 1})


def returns_partial():
    return Outcome.partial({"n": 1})


def returns_nothing():
    return Outcome.nothing({"n": 1})


def refuses():
    raise KBNotFoundError("KB not found: nope")


def raises_value_error():
    raise ValueError("bad value")


def interrupted():
    raise KeyboardInterrupt


def warns_on_stderr_then_returns():
    print(MARK, file=sys.stderr)
    return _done()


JSON, RICH = "json", "rich"


@dataclass(frozen=True)
class Shape:
    body: Callable[[], object]
    #: The formats in which this shape is a way out (NO_OUTCOME, exit 1).
    formats: tuple[str, ...] = (JSON, RICH)
    #: Does the body write MARK to stdout? Then the root moves it to stderr.
    writes: bool = False
    #: Run only where the path exists / the package is installed.
    needs: Callable[[], bool] = lambda: True
    #: A stream the module took at import: only a real process shows it.
    real_process_only: bool = False


def _has_pypi_click() -> bool:
    try:
        import click  # noqa: F401
    except ImportError:
        return False
    return True


def _has_libc() -> bool:
    try:
        import ctypes

        return hasattr(ctypes.CDLL(None), "printf")
    except Exception:
        return False


#: Every class of way out the root must answer with NO_OUTCOME.
WAYS_OUT: dict[str, Shape] = {
    s.body.__name__: s
    for s in (
        Shape(exit_zero),
        Shape(exit_seven),
        Shape(system_exit_zero),
        Shape(sys_exit_with_text),
        Shape(abort),
        Shape(click_error_with_a_code),
        Shape(pypi_click_error_with_a_code, needs=_has_pypi_click),
        Shape(usage_error_from_the_body),
        Shape(ctx_exit_zero),
        Shape(ctx_fail),
        Shape(ctx_abort),
        Shape(crash),
        Shape(crash_outside_exception),
        Shape(prints, writes=True),
        Shape(echoes, writes=True),
        Shape(console_prints, writes=True),
        Shape(writes_sys_stdout, writes=True),
        Shape(writes_stdout_buffer, writes=True),
        Shape(json_dumps_to_stdout, writes=True),
        Shape(writes_descriptor_1, writes=True),
        Shape(child_process_prints, writes=True),
        Shape(writes_dunder_stdout_unflushed, writes=True),
        Shape(writes_dev_stdout, writes=True, needs=lambda: os.path.exists("/dev/stdout")),
        Shape(prints_from_a_close_callback, writes=True),
        Shape(prints_then_exits_zero, writes=True),
        Shape(prints_then_refuses, writes=True),
        Shape(console_kept_from_import_prints, writes=True, real_process_only=True),
        Shape(stream_kept_from_import_unflushed, writes=True, real_process_only=True),
        Shape(
            logs_to_a_handler_on_the_stream_kept_from_import, writes=True, real_process_only=True
        ),
        Shape(c_stdio_prints_unflushed, writes=True, real_process_only=True, needs=_has_libc),
        Shape(closes_sys_stdout),
        Shape(puts_stdout_back_and_prints, writes=True),
        Shape(raises_an_exception_that_cannot_be_described),
        Shape(raises_a_value_error_that_cannot_be_printed),
        Shape(returns_none),
        Shape(returns_a_dict),
        Shape(returns_zero),
        Shape(returns_an_outcome_subclass),
        Shape(returns_an_outcome_with_a_forged_kind),
        Shape(returns_an_outcome_with_a_list_forced_in),
        Shape(returns_an_outcome_with_its_own_outcome_key_forced_in),
        # A renderer runs for --format rich only; in JSON the outcome is whole.
        Shape(renderer_raises, formats=(RICH,)),
        Shape(renderer_exits_zero, formats=(RICH,)),
        Shape(renderer_raises_typer_exit, formats=(RICH,)),
        Shape(renderer_raises_an_exception_that_cannot_be_described, formats=(RICH,)),
        Shape(payload_that_cannot_be_serialised),
        # In rich the field listing is the renderer, and its words are its own.
        Shape(payload_that_prints_when_serialised, formats=(JSON,), writes=True),
    )
}

#: What the root allows, and the exit code it gives each.
ALLOWED: dict[str, tuple[Callable[[], object], int]] = {
    f.__name__: (f, code)
    for f, code in (
        (returns_done, 0),
        (returns_partial, 3),
        (returns_nothing, 1),
        (refuses, 1),
        (raises_value_error, 1),
        (interrupted, 130),
        (warns_on_stderr_then_returns, 0),
        # The format is read when the body starts; JSON was asked for.
        (switches_the_format_then_returns, 0),
        # A renderer is not called for a machine format.
        (renderer_raises, 0),
    )
}


def main(argv: list[str]) -> None:
    if argv[0] == "--say-first":
        # Text in the stream's buffer before any command runs: not the body's.
        sys.stdout.write(argv[1])
        argv = argv[2:]
    root, shape, *rest = argv
    cut = rest.index("--")
    path, args = tuple(rest[:cut]), rest[cut + 1 :]
    body = WAYS_OUT[shape].body if shape in WAYS_OUT else ALLOWED[shape][0]
    app = root_app(root)
    with planted(app, path, body):
        sys.argv = [root, *path, *args]
        app()


if __name__ == "__main__":
    main(sys.argv[1:])
