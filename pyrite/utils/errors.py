"""Shared agent-facing error shape for the CLI.

A single canonical structure so that errors look the same whether they come from
the CLI, MCP (`mcp_server._error`), or the REST PyriteError handler:

    {
      "error": "human message",
      "error_code": "MACHINE_CODE",
      "suggestion": "optional fix hint",   # omitted when not provided
      "retryable": false,
    }

Promoted from the duplicated `_cli_error` helpers in entry_commands.py and
browse_commands.py (cli-error-shape-consistency).
"""

from __future__ import annotations

import functools
import io
import json
import logging
import os
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import typer
from typer.core import TyperGroup

from ..exceptions import PyriteError

logger = logging.getLogger(__name__)


def build_error(
    message: str,
    error_code: str,
    *,
    suggestion: str | None = None,
    retryable: bool = False,
) -> dict[str, Any]:
    """Build the canonical error dict. `suggestion` is omitted when unset."""
    payload: dict[str, Any] = {
        "error": message,
        "error_code": error_code,
        "retryable": retryable,
    }
    if suggestion:
        payload["suggestion"] = suggestion
    return payload


# The command ran and did only part of what was asked, or left items for a retry.
# 1 stays "refused: nothing was done"; 2 is click's usage error. `ids missing` and
# `ids pin` shipped this meaning first (docs/json-contracts.md, "Exit codes (CLI)").
PARTIAL_EXIT = 3


def exit_unless_whole(failed: int, done: int) -> None:
    """Exit for a command that loops over items: 0 when none failed, 3 when
    some did and some were done, 1 when none was done. Print what happened
    and what did not before calling it."""
    if failed:
        raise typer.Exit(PARTIAL_EXIT if done else 1)


def cli_error(
    message: str,
    output_format: str = "rich",
    *,
    error_code: str = "ERROR",
    suggestion: str | None = None,
    retryable: bool = False,
    extra: dict[str, Any] | None = None,
) -> None:
    """Print a structured (machine formats) or colored single-line (rich) error
    and raise ``typer.Exit(1)``.

    ``extra`` adds keys beside the contract's own in the machine payload (a
    ``did_you_mean`` list, say); it never replaces one of them, and the rich
    line carries the same information in ``suggestion``.

    Machine formats (anything other than ``rich``) get the JSON payload on
    stdout so scripts/agents can parse it; rich gets a human line:

        ERROR [KB_NOT_FOUND]: KB not found: x
            hint: run `pyrite kb list`
    """
    payload = build_error(message, error_code, suggestion=suggestion, retryable=retryable)
    payload = {**(extra or {}), **payload}
    if output_format != "rich":
        typer.echo(json.dumps(payload))
    else:
        _print_error_line(payload)
    raise typer.Exit(1)


def _print_error_line(payload: dict[str, Any]) -> None:
    """The rich form of the error shape, on stdout."""
    from rich.console import Console
    from rich.text import Text

    console = Console()
    # The styled labels are ours and stay markup; the message and the
    # suggestion are data -- an extra like `pyrite[semantic]`, a JSON
    # example, an entry id -- and Rich would parse their brackets as style
    # tags. `pip install pyrite[semantic]` rendered as `pip install
    # pyrite`, a command that does not fix the error it is offered for.
    # Wrapping them in `Text` is what keeps them literal -- passing them as
    # a separate argument is NOT enough, Rich parses each string argument.
    console.print(
        f"[red]ERROR[/red] [[yellow]{payload['error_code']}[/yellow]]:", Text(payload["error"])
    )
    if payload.get("suggestion"):
        console.print("    [dim]hint:[/dim]", Text(payload["suggestion"]))


def cli_error_from(
    exc: PyriteError,
    output_format: str = "rich",
    *,
    extra: dict[str, Any] | None = None,
    error_code: str | None = None,
) -> None:
    """Map a caught ``PyriteError`` to the CLI's error shape and exit.

    ADR-0037 theme 2, §3: "codes live on exception classes." Reads
    ``exc.error_code`` directly -- the same attribute REST's central handler
    (``server/errors.py``) and MCP's ``_refusal`` (``server/mcp_server.py``)
    read -- rather than a hand-kept ``isinstance`` chain
    (``pyrite/cli/__init__.py``'s old ``_cli_err``, which knew about exactly
    three exception types and fell back to a bare ``"ERROR"`` for anything
    else, including every ``StorageError``/``PluginError``/``ConfigError``).
    Every ``PyriteError`` has a class-level code, so there is no fallback
    case left to get wrong.

    The message shown is ``exc.public_message`` when the class sets one
    (safe by construction; see ``pyrite.exceptions``), else ``str(exc)``.
    The CLI has no ``legacy_error_code`` concept -- that is MCP-only, for one
    release, per the maintainer's decision (2026-09-25); a CLI caller was
    never promised the old MCP spelling.

    ``error_code`` replaces the class's code for one documented CLI spelling
    (``NOT_FOUND``; see ``_CLI_CODE_OVERRIDES``), and ``extra`` adds keys as
    ``cli_error``'s does. Always raises ``typer.Exit(1)`` (via ``cli_error``).
    """
    message = exc.public_message or str(exc)
    cli_error(
        message,
        output_format,
        extra=extra,
        error_code=error_code or exc.error_code,
        suggestion=getattr(exc, "suggestion", None),
        retryable=bool(getattr(exc, "retryable", False)),
    )


# The CLI spells a missing entry NOT_FOUND (docs/json-contracts.md, "Error
# codes"); the class says ENTRY_NOT_FOUND. One override, here, until #610 settles
# the spelling for every transport at once.
_CLI_CODE_OVERRIDES = {"ENTRY_NOT_FOUND": "NOT_FOUND"}


def _refusal_payload(exc: BaseException) -> dict[str, Any]:
    """The error shape of a refusal a command on the contract raised.

    A ``ValueError`` is rendered as code ``ERROR``. Transitional (ADR-0046):
    every CLI command caught ``(PyriteError, ValueError)`` and printed that, so
    the root does the same, and logs the original traceback at DEBUG (``-vv``).
    It goes when the services these commands call raise ``ValidationError`` (or
    another ``PyriteError``) for bad input instead of ``ValueError``.
    """
    from ..cli.outcome import REFUSED

    if not isinstance(exc, PyriteError):
        logger.debug("CLI command raised %s", type(exc).__name__, exc_info=exc)
        return {"outcome": REFUSED, **build_error(str(exc), "ERROR")}
    code = _CLI_CODE_OVERRIDES.get(exc.error_code, exc.error_code)
    return {
        "outcome": REFUSED,
        **build_error(
            exc.public_message or str(exc),
            code,
            suggestion=getattr(exc, "suggestion", None),
            retryable=bool(getattr(exc, "retryable", False)),
        ),
    }


def _no_outcome_payload(what_happened: str) -> dict[str, Any]:
    """The answer when a command on the contract left any way but the root's.

    Built from constants and one sentence, so it cannot fail to render.
    """
    from ..cli.outcome import REFUSED

    return {
        "outcome": REFUSED,
        **build_error(
            f"internal error: {what_happened}, so what the command did is unknown; "
            "please report it",
            "NO_OUTCOME",
        ),
    }


def _default_format() -> str:
    from ..cli.output import SHARED_FORMATS

    asked = os.environ.get("PYRITE_FORMAT") or "json"
    return asked if asked in SHARED_FORMATS else "json"


def _flush(stream: Any) -> None:
    try:
        stream.flush()
    except Exception:  # a closed or missing stream has nothing to flush
        pass


def _flush_c_stdio() -> None:
    """Native code that printed with C's ``printf`` left its text in C's own
    buffer, which a pipe does not flush until the process exits. Best effort:
    where there is no libc to ask (Windows), that text is a stated limit."""
    try:
        import ctypes

        ctypes.CDLL(None).fflush(None)
    except Exception:
        pass


class _StdoutWatch:
    """Holds back everything written to stdout while it is on (ADR-0046,
    decision 8), at two levels, into one temporary file:

    - ``sys.stdout`` is replaced: ``print``, ``typer.echo``, a Rich
      ``Console()`` that was given no file, ``sys.stdout.write``.
    - Descriptor 1 is pointed at the file: ``os.write(1, ...)``, a child
      process that inherits stdout, ``sys.__stdout__``, ``/dev/stdout``, and a
      stream or ``Console(file=sys.stdout)`` that a module took at import.

    ``stop`` puts both back as it found them and returns what was written. A
    watch started inside another (a body that runs the app again) saves and
    restores the outer watch's stream, so they nest.

    When descriptor 1 cannot be duplicated (stdout is closed) or no temporary
    file can be made, only ``sys.stdout`` is watched.
    """

    def __init__(self) -> None:
        self._python_stream = sys.stdout
        # What is already in a buffer was written before the body: it is not
        # the body's, and it must not land in the file. Both streams, because
        # `stop` flushes both (under CliRunner they are different objects).
        _flush(self._python_stream)
        _flush(sys.__stdout__)
        _flush_c_stdio()
        self._saved_descriptor: int | None = None
        self._file: Any
        try:
            self._file = tempfile.TemporaryFile()
        except OSError:
            self._file = io.BytesIO()
        else:
            try:
                saved = os.dup(1)
            except OSError:
                pass
            else:
                try:
                    os.dup2(self._file.fileno(), 1)
                except OSError:
                    os.close(saved)
                else:
                    self._saved_descriptor = saved
        self._stream = io.TextIOWrapper(
            self._file, encoding="utf-8", errors="replace", write_through=True
        )
        sys.stdout = self._stream

    def stop(self) -> bytes:
        # A write through the original stream object (kept from import, or
        # ``sys.__stdout__``) may still be in that object's buffer. Flush it
        # while descriptor 1 is still the file, or it lands on the real stdout
        # after the root's document.
        for stream in (self._stream, self._python_stream, sys.__stdout__):
            _flush(stream)
        _flush_c_stdio()
        sys.stdout = self._python_stream
        if self._saved_descriptor is not None:
            os.dup2(self._saved_descriptor, 1)
            os.close(self._saved_descriptor)
            self._saved_descriptor = None
        try:
            self._stream.detach()
            self._file.seek(0)
            return self._file.read()
        except ValueError:
            # The command closed the stream it was given. What it wrote before
            # that cannot be read back, and it is not nothing.
            return b"(the command closed stdout)\n"
        finally:
            self._file.close()


_NOT_RETURNED: Any = object()
_BODY = "__pyrite_body__"


class _BodyGuard:
    """One per run of a root group. It learns from the shared ``--format``
    option which command is about to run, and watches stdout from the moment
    click calls that command's body until the root has decided (decision 7)."""

    def __init__(self) -> None:
        self.entered = False
        #: The object the body returned. The root renders this object and no
        #: other: a group's result callback cannot swap it on the way up.
        self.returned: Any = _NOT_RETURNED
        #: The format asked for, read when the body starts: the body cannot
        #: change the format its answer is printed in.
        self.format: str | None = None
        self._watch: _StdoutWatch | None = None

    def watch_body_of(self, ctx: Any) -> None:
        """Called by the shared option's callback, while click is parsing."""
        from ..cli.output import declares_shared_format, requested_format

        command = ctx.command
        body = getattr(command.callback, _BODY, command.callback)
        if body is None or not declares_shared_format(command):
            return

        @functools.wraps(body)
        def entered(*args: Any, **kwargs: Any) -> Any:
            if not self.entered:
                self.format = requested_format(ctx)
            self._enter()
            self.returned = _NOT_RETURNED
            self.returned = body(*args, **kwargs)
            return self.returned

        setattr(entered, _BODY, body)
        command.callback = entered

    def _enter(self) -> None:
        # Once: a body entered again (``ctx.invoke`` of the running command)
        # is still inside the first watch.
        if not self.entered:
            self.entered = True
            self._watch = _StdoutWatch()

    def stop(self) -> bytes:
        watch, self._watch = self._watch, None
        return watch.stop() if watch is not None else b""


@dataclass
class _Answer:
    """What the root will print and the exit code it will give."""

    code: int
    #: A machine format's one document, already serialised.
    text: str | None = None
    #: ``--format rich``: prints for a person.
    show: Callable[[], None] | None = None


def _error_answer(payload: dict[str, Any], fmt: str) -> _Answer:
    if fmt == "rich":
        return _Answer(1, show=lambda: _print_error_line(payload))
    if fmt == "json":
        return _Answer(1, text=json.dumps(payload))
    from ..formats import format_response

    return _Answer(1, text=format_response(payload, fmt)[0])


def _outcome_answer(outcome: Any, fmt: str) -> _Answer:
    from ..cli.outcome import EXIT_CODES, document, print_fields

    outcome.payload()  # refuses a kind or data the root cannot print, in any format
    code = EXIT_CODES[outcome.kind]
    if fmt != "rich":
        return _Answer(code, text=document(outcome, fmt))

    def show() -> None:
        from rich.console import Console

        (outcome.rich or print_fields)(Console(), outcome.data)

    return _Answer(code, show=show)


def _named(exc: BaseException) -> str:
    """An exception's class, and the exit code it asked for if it had one.
    Never raises: it runs where the root is already answering NO_OUTCOME."""
    try:
        code = getattr(exc, "exit_code", getattr(exc, "code", None))
        return type(exc).__name__ + (f" (exit code {code!r})" if code is not None else "")
    except BaseException:
        return type(exc).__name__


def _emit(answer: _Answer, fmt: str) -> None:
    """Print the answer and exit with its code. A renderer that raises or
    exits is one more way out that is not the root's."""
    if answer.text is not None:
        typer.echo(answer.text)
    elif answer.show is not None:
        try:
            answer.show()
        except KeyboardInterrupt:
            raise
        except BaseException as exc:
            logger.debug("rendering raised %s", type(exc).__name__, exc_info=exc)
            answer = _error_answer(_no_outcome_payload(f"rendering raised {_named(exc)}"), fmt)
            assert answer.show is not None
            answer.show()
    if answer.code:
        raise typer.Exit(answer.code)


class PyriteCLIGroup(TyperGroup):
    """Root command group for `pyrite`, `pyrite-admin` and `pyrite-read`.

    The one point a command's result passes through (ADR-0046). Subcommands
    run inside the root group's ``invoke``, so this covers every command of
    the three CLIs, plugins' included, in a process and under ``CliRunner``.

    **A command that declares the shared ``--format`` option is on the
    contract, and this is its only way out.** From the moment click calls its
    body until this method has decided, exactly these are accepted:

    - a returned value whose type is exactly ``Outcome``, and which is the
      object the body returned: rendered here in the format asked for; the
      exit code is read here from ``EXIT_CODES`` (0 done, 3 partial, 1
      nothing);
    - a raised ``PyriteError``: the error shape plus ``"outcome": "error"``,
      in that format, exit 1;
    - a raised ``ValueError``: the same with code ``ERROR`` (transitional,
      until services raise ``ValidationError``; the traceback goes to the
      DEBUG log);
    - ``KeyboardInterrupt``, which passes through: Ctrl-C must keep working.

    Everything else is answered ``NO_OUTCOME``, exit 1, in the format asked
    for: any other return value (``None``, a dict, an ``Outcome`` subclass),
    any other exception (``typer.Exit``, ``SystemExit``, ``Abort``, a click
    error with its own code, a usage error raised by the body, a crash; the
    ``except`` below lists what is allowed, not what is forbidden), any byte
    written to stdout (``sys.stdout`` or descriptor 1; it is moved to stderr,
    not lost), and a payload or renderer that raises or exits. In a machine
    format stdout then holds one document and nothing else.
    ``tests/test_cli_outcome.py`` plants each of these as the body of every
    command on the contract and runs it through the real roots.

    What this does not cover, on purpose or because nothing in the process
    can:

    - **Before the body.** Click is parsing: ``--help`` exits 0, a usage error
      exits 2, and a parameter callback runs unwatched. (A command on the
      contract declares no callback but the shared option's; the registry
      test checks that.)
    - **Stderr is open**: progress, warnings, the ``-f`` notice. A command that
      writes a failure there and returns ``done`` is not caught.
    - **A rich renderer chooses its words.** The kind, the exit code and every
      machine format are still decided here. Text a renderer printed before it
      raised stays above the ``NO_OUTCOME`` line.
    - **After this method returns**: ``os._exit`` (exit 0, empty stdout, which
      no outcome ever is), an ``atexit`` handler, a thread that outlives the
      command, a buffer of the command's own over descriptor 1 that is
      flushed at exit.
    - **Not this process's stdout as the root holds it**: ``/dev/tty``; a
      duplicate of descriptor 1 (one taken before the body started, or the
      root's own saved one); a child the command forks, which returns through
      this method too and prints a second document.
    - **A command that reaches into the root**: its guard in ``ctx.meta``,
      ``EXIT_CODES``, this module. It shares the interpreter; nothing here can
      stop code that sets out to defeat it. The guard is for mistakes.
    - **A prompt** writes to stdout, so a command on the contract cannot ask
      one (ADR-0046, open question).

    A command that does not declare the shared option is not watched and
    nothing changes for it: it prints its own result, its stdout is never held
    back, and its exceptions pass through, except a refused config save
    (#377), which is one error line and exit 1. If it returns an ``Outcome``
    (a plugin, decision 5), that is rendered here in the default format.
    """

    def invoke(self, ctx):
        from ..cli.outcome import Outcome
        from ..cli.output import BODY_GUARD_META_KEY
        from ..exceptions import ConfigSaveRefusedError

        if BODY_GUARD_META_KEY in ctx.meta:
            # A PyriteCLIGroup mounted under another: the outer one decides.
            return super().invoke(ctx)
        guard = ctx.meta[BODY_GUARD_META_KEY] = _BodyGuard()

        raised: BaseException | None = None
        result: Any = None
        try:
            result = super().invoke(ctx)
        except BaseException as exc:
            if not guard.entered:
                if isinstance(exc, ConfigSaveRefusedError):
                    cli_error(str(exc), error_code="CONFIG_SAVE_REFUSED")
                raise
            raised = exc

        if not guard.entered:
            if type(result) is Outcome:
                _emit(_outcome_answer(result, _default_format()), _default_format())
            return result

        fmt = guard.format or _default_format()
        try:
            # Still watched: serialising a payload can print or raise too.
            try:
                answer = self._decide(guard, fmt, result, raised)
            except KeyboardInterrupt:
                raise
            except BaseException as exc:
                logger.debug("the root could not render the answer", exc_info=exc)
                answer = _error_answer(
                    _no_outcome_payload(f"its result could not be rendered ({_named(exc)})"), fmt
                )
        finally:
            held = guard.stop()
            if held:
                try:
                    sys.stderr.write(held.decode("utf-8", errors="replace"))
                    sys.stderr.flush()
                except Exception:  # no stderr to move it to; the answer still stands
                    pass
        if held:
            answer = _error_answer(
                _no_outcome_payload(
                    f"the command wrote {len(held)} bytes to stdout itself (moved to stderr)"
                ),
                fmt,
            )
        _emit(answer, fmt)
        return result

    @staticmethod
    def _decide(guard: _BodyGuard, fmt: str, result: Any, raised: BaseException | None) -> _Answer:
        """The allow-list. Each branch names something accepted; the last
        lines are everything else."""
        from ..cli.outcome import Outcome

        if isinstance(raised, KeyboardInterrupt):
            raise raised
        if isinstance(raised, PyriteError | ValueError):
            return _error_answer(_refusal_payload(raised), fmt)
        if raised is None and type(result) is Outcome and result is guard.returned:
            return _outcome_answer(result, fmt)

        if raised is not None:
            logger.debug("CLI command raised %s", type(raised).__name__, exc_info=raised)
            what = f"the command raised {_named(raised)}"
        elif result is not guard.returned:
            what = "what the command returned was replaced before it reached the root"
        else:
            what = f"the command returned {type(result).__name__}, not an Outcome"
        return _error_answer(_no_outcome_payload(what), fmt)
