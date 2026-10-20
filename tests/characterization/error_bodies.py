"""One instance of every `PyriteError` subclass, and the body each transport's
real mapping function gives it (ADR-0037 theme 0's error-body acceptance).

This does not drive a live request per class: several classes need elaborate
multi-step setup to reach naturally (`LastAdminError` needs exactly one
admin then a demote attempt; `StorageError` needs a corrupt database), and
theme 0 is a characterization of the **mapping**, not a proof that every
class is reachable from every transport today (that proof, where it's cheap,
lives in `test_error_bodies.py`'s handful of live-request cross-checks
instead). So this module builds one instance of each class directly and
runs it through each transport's own, real, unmodified mapping function:

- REST: `pyrite.server.errors.error_response(exc)` -- the exact
  (status, ``{"detail": {...}}``) pair the registered exception handler
  returns (ADR-0037 theme 2 factored the handler's body out into this
  function precisely so a characterization can call the real logic
  instead of keeping a parallel copy of it in sync by hand).
- MCP: `pyrite.server.mcp_server._refusal(exc)`, called directly -- the
  exact function `_dispatch_tool` calls on every refusal.
- CLI: what the root group prints when the real `pyrite create` -- a command
  on the outcome contract (ADR-0046) -- raises the error. `create`'s body is
  replaced by one that raises (`tests/outcome_walk.py`'s `planted`), and the
  command runs through `CliRunner` and the real `PyriteCLIGroup`, so the cell is
  what `cli_error_from`'s successor, the root's `_refusal_payload`, really
  prints: the class's own `error_code` (`ENTRY_NOT_FOUND` spelt `NOT_FOUND`, the
  one documented CLI override), `public_message` where the class sets one, the
  class's `suggestion`, and the additive `"outcome": "error"`. The one thing
  `create` itself adds -- the CLI-phrased hint on an undeclared type -- is
  applied by calling the same `_with_cli_hint` its `except` calls. This column
  used to replicate `_refusal_exit`/`_cli_error` by hand, which the migration
  deleted, and went on pinning `"error_code": "ERROR"` for classes the CLI no
  longer prints that way.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any

import pyrite.exceptions as exc_module
from pyrite.exceptions import PyriteError, ValidationError
from pyrite.server.errors import error_response
from pyrite.server.mcp_server import _refusal

# One buildable instance per concrete PyriteError subclass. Most take a
# plain message; a few need required extra fields (see their __init__
# signatures) -- built with the smallest valid value.
_BUILDERS: dict[str, Any] = {
    "AmbiguousEntryError": lambda: exc_module.AmbiguousEntryError(
        "characterization-entry", candidate_kbs=["kb-a", "kb-b"]
    ),
    "UndeclaredTypeError": lambda: exc_module.UndeclaredTypeError(
        "type 'x' is not declared", declared_types=["note", "event"]
    ),
    "SchemaViolationError": lambda: exc_module.SchemaViolationError(
        "schema violation", errors=[{"field": "title", "error": "required"}]
    ),
    "TruncatedBodyError": lambda: exc_module.TruncatedBodyError(
        "body was truncated", suggestion="fetch the full body and retry"
    ),
    "ConfigSaveRefusedError": lambda: exc_module.ConfigSaveRefusedError(
        "config changed on disk", config_file="/config.yaml", dropped=["some-kb"]
    ),
    "ConfigFileUnreadableError": lambda: exc_module.ConfigFileUnreadableError(
        "config file unreadable", config_file="/config.yaml"
    ),
    "EvaluationFailedError": lambda: exc_module.EvaluationFailedError(
        "characterization-entry", "the model's reply was not JSON"
    ),
}


def all_pyrite_error_classes() -> list[type[PyriteError]]:
    """Every concrete `PyriteError` subclass in `pyrite.exceptions`, sorted
    by name for a stable golden key order."""
    classes = [
        c
        for _, c in vars(exc_module).items()
        if inspect.isclass(c) and issubclass(c, PyriteError) and c is not PyriteError
    ]
    return sorted(set(classes), key=lambda c: c.__name__)


def build_instance(cls: type[PyriteError]) -> PyriteError:
    builder = _BUILDERS.get(cls.__name__)
    if builder is not None:
        return builder()
    return cls(f"characterization message for {cls.__name__}")


def rest_body(exc: PyriteError) -> dict[str, Any]:
    """What the registered exception handler answers for `exc` --
    `pyrite.server.errors.error_response`, the same function
    `register_pyrite_exception_handler`'s handler calls, so this tracks the
    real handler exactly rather than a parallel copy of its table.

    ADR-0037 theme 2: the body is now `{"detail": {"code", "message",
    "retryable", "hint"?}}`, not the old flat `{"code", "message"}` --
    the same wrapper `HTTPException(detail={...})` sites already answered."""
    status_code, content = error_response(exc)
    return {"status": status_code, "body": content}


def mcp_body(exc: PyriteError) -> dict[str, Any]:
    """`_refusal(exc)` -- the exact function `_dispatch_tool` calls."""
    return _refusal(exc)


def cli_body(exc: PyriteError) -> dict[str, Any]:
    """The `--format json` document `pyrite create` prints when it raises `exc`:
    the real command, the real root group, only the body is the raise."""
    import json

    from typer.testing import CliRunner

    from pyrite.cli import app
    from pyrite.cli.entry_commands import _with_cli_hint
    from tests.outcome_walk import args_for, click_command, planted

    def body():
        raise _with_cli_hint(exc) if isinstance(exc, ValidationError) else exc

    command = click_command(app, ("create",))
    with planted(app, ("create",), body):
        result = CliRunner().invoke(app, ["create", *args_for(command), "--format", "json"])
    assert result.exit_code == 1, result.output
    return json.loads(result.stdout)


@dataclass(frozen=True)
class ErrorBodyCase:
    class_name: str
    rest: dict[str, Any]
    mcp: dict[str, Any]
    cli: dict[str, Any]


def all_error_body_cases() -> list[ErrorBodyCase]:
    cases = []
    for cls in all_pyrite_error_classes():
        exc = build_instance(cls)
        cases.append(
            ErrorBodyCase(
                class_name=cls.__name__,
                rest=rest_body(exc),
                mcp=mcp_body(exc),
                cli=cli_body(exc),
            )
        )
    return cases
