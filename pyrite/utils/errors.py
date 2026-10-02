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

import json
from typing import Any

import typer
from typer.core import TyperGroup

from ..exceptions import PyriteError


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
        from rich.console import Console
        from rich.text import Text

        console = Console()
        # The styled labels are ours and stay markup; `message` and
        # `suggestion` are data -- an extra like `pyrite[semantic]`, a JSON
        # example, an entry id -- and Rich would parse their brackets as style
        # tags. `pip install pyrite[semantic]` rendered as `pip install
        # pyrite`, a command that does not fix the error it is offered for.
        # Wrapping them in `Text` is what keeps them literal -- passing them as
        # a separate argument is NOT enough, Rich parses each string argument.
        console.print(f"[red]ERROR[/red] [[yellow]{error_code}[/yellow]]:", Text(message))
        if suggestion:
            console.print("    [dim]hint:[/dim]", Text(suggestion))
    raise typer.Exit(1)


def cli_error_from(exc: PyriteError, output_format: str = "rich") -> None:
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

    Always raises ``typer.Exit(1)`` (via ``cli_error``).
    """
    message = exc.public_message or str(exc)
    cli_error(
        message,
        output_format,
        error_code=exc.error_code,
        suggestion=getattr(exc, "suggestion", None),
        retryable=bool(getattr(exc, "retryable", False)),
    )


class PyriteCLIGroup(TyperGroup):
    """Root command group for `pyrite` and `pyrite-admin`.

    A refused config save (#377) -- from a command or from a service it calls
    -- is one error line naming the file and the KBs, and exit 1; not a
    traceback. Subcommands run inside the root group's ``invoke``, so this
    covers every command of both CLIs, including under ``CliRunner``.
    """

    def invoke(self, ctx):
        from ..exceptions import ConfigSaveRefusedError

        try:
            return super().invoke(ctx)
        except ConfigSaveRefusedError as e:
            cli_error(str(e), error_code="CONFIG_SAVE_REFUSED")
