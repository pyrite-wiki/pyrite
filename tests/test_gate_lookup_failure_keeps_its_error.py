"""A gate's blockers lookup that fails keeps its own error (#1585).

The board's refusal (``BoardConfigError``: ``VALIDATION_FAILED``, not
retryable, naming ``board.yaml``) is raised only for a cause in ``board.yaml``'s
content. The criterion ``no_open_blockers`` reads the KB's index, not the file,
so a failure of that lookup (the index cannot be read, a storage error, a
timeout) reaches the caller as itself, with its own code and ``retryable``, on
every tool and command that evaluates a gate, and also when the KB has no
``board.yaml`` (the built-in board names the criterion too). A criterion that
could not be evaluated is still never recorded as passed.

The oracle is what the dispatcher answers for the same exception outside a
gate: ``sw_claim`` looks dependencies up before it evaluates its gate.
"""

from __future__ import annotations

import ast
import json
import sqlite3
from pathlib import Path

import pytest
import typer

from pyrite.exceptions import KBNotFoundError, StorageError
from tests.test_board_config_refuses import (
    KB,
    MARKER,
    MCP_CALLS,
    _cli,
    _mcp,
    _snapshot,
    _write_board,
    world,  # noqa: F401  (the fixture)
)

_PLUGIN = (
    Path(__file__).resolve().parent.parent
    / "extensions/software-kb/src/pyrite_software_kb/plugin.py"
)

#: What the lookup raises: Pyrite's own errors and the ones a locked,
#: unreadable or slow index raises in practice.
RAISED = {
    "StorageError": lambda: StorageError(f"index failed {MARKER}"),
    "KBNotFoundError": lambda: KBNotFoundError(f"kb gone {MARKER}"),
    "OSError": lambda: OSError(5, f"disk {MARKER}"),
    "TimeoutError": lambda: TimeoutError(f"slow {MARKER}"),
    "sqlite3.OperationalError": lambda: sqlite3.OperationalError(f"locked {MARKER}"),
}

#: The tool that calls ``_evaluate_gate`` -> the status of the gate it evaluates.
GATE_OF_TOOL = {
    "sw_check_ready": "in_progress",
    "sw_claim": "in_progress",
    "sw_transition": "in_progress",
    "sw_review": "done",
    "sw_refine": "in_progress",
}
_METHOD_OF_TOOL = {f"_mcp_{t.removeprefix('sw_')}": t for t in GATE_OF_TOOL}

_NAMING_BLOCKERS = "gates:\n" + "".join(
    f"  {status}:\n    policy: enforce\n    criteria:\n"
    "      - {text: no blockers, checker: no_open_blockers}\n"
    for status in ("in_progress", "done")
)
#: A valid board that declares no gate: where the oracle's call is made.
_NO_GATES = "lanes: []\n"


def _regimes() -> dict[str, str | None]:
    return {"a-board-naming-no_open_blockers": _NAMING_BLOCKERS, "no-board-yaml": None}


def _the_gate_names_the_lookup(regime: str, status: str) -> bool:
    """Whether the gate for ``status`` on this board has ``no_open_blockers``."""
    from pyrite_software_kb.board import DEFAULT_BOARD_CONFIG

    if regime == "no-board-yaml":
        gate = DEFAULT_BOARD_CONFIG["gates"].get(status, {})
        return any(c.get("checker") == "no_open_blockers" for c in gate.get("criteria", []))
    return True


@pytest.mark.control(reason="guards a future sixth caller; the five callers exist on the base")
def test_every_caller_of_the_gate_has_a_cell():
    """A sixth caller of ``_evaluate_gate`` in ``plugin.py`` cannot be added
    without a cell. It reads only ``plugin.py``: a caller added in another
    module is not covered by this guard."""
    tree = ast.parse(_PLUGIN.read_text())
    callers: set[str] = set()
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef) and fn.name != "_evaluate_gate":
            for node in ast.walk(fn):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "_evaluate_gate"
                ):
                    callers.add(fn.name)
    assert callers == set(_METHOD_OF_TOOL), callers
    assert set(GATE_OF_TOOL) <= set(MCP_CALLS)


def _the_lookup_raises(monkeypatch, make) -> list[int]:
    from pyrite_software_kb.plugin import SoftwareKBPlugin

    calls: list[int] = []

    def raiser(self, *a, **k):
        calls.append(1)
        raise make()

    monkeypatch.setattr(SoftwareKBPlugin, "_get_dependency_status", raiser)
    return calls


def _no_exception_text(answer: dict, oracle: dict, raised: str) -> None:
    """Pyrite's own error keeps its own text (the oracle's); any other
    exception's text reaches no answer."""
    if raised in ("StorageError", "KBNotFoundError"):
        assert answer.get("error") == oracle.get("error"), (answer, oracle)
    else:
        assert MARKER not in json.dumps(answer), answer


def _oracle(world, make, monkeypatch) -> dict:  # noqa: F811
    """The dispatcher's answer to the exception outside a gate: ``sw_claim`` on
    a board with no gate, whose earlier lookup raises it."""
    _write_board(world, _NO_GATES)
    _the_lookup_raises(monkeypatch, make)
    answer = _mcp(world, "sw_claim", MCP_CALLS["sw_claim"])
    assert answer.get("claimed") is not True, answer
    assert answer.get("error_code") not in (None, "VALIDATION_FAILED"), answer
    (world.kb_path(KB) / "board.yaml").unlink()
    return answer


def _tool_cells():
    """(tool, regime) cells; those the base already answers rightly are controls:
    `sw_claim` looks dependencies up before its gate, and the built-in board's
    `done` gate does not read the index, so `sw_review` never reaches it."""
    cells = []
    for tool in sorted(GATE_OF_TOOL):
        for regime in sorted(_regimes()):
            if tool == "sw_claim":
                why = "sw_claim answers the lookup's own error before its gate (the oracle)"
            elif tool == "sw_review" and not _the_gate_names_the_lookup(regime, "done"):
                why = "the built-in board's done gate never reads the index: nothing fails"
            else:
                cells.append(pytest.param(tool, regime))
                continue
            cells.append(pytest.param(tool, regime, marks=pytest.mark.control(reason=why)))
    return cells


@pytest.mark.parametrize(("tool", "regime"), _tool_cells())
@pytest.mark.parametrize("raised", sorted(RAISED))
def test_a_failed_blockers_lookup_keeps_its_own_error(world, raised, tool, regime, monkeypatch):  # noqa: F811
    make = RAISED[raised]
    oracle = _oracle(world, make, monkeypatch)
    board = _regimes()[regime]
    if board is not None:
        _write_board(world, board)
    before = _snapshot(world)
    calls = _the_lookup_raises(monkeypatch, make)

    answer = _mcp(world, tool, MCP_CALLS[tool])

    if not _the_gate_names_the_lookup(regime, GATE_OF_TOOL[tool]) and tool != "sw_claim":
        # The built-in board's `done` gate does not read the index: nothing
        # failed, so nothing is refused.
        assert calls == [] and "error_code" not in answer, answer
        return
    assert calls, "the lookup was not reached"
    assert answer.get("ready") is not True and answer.get("claimed") is not True, answer
    assert answer.get("transitioned") is not True and answer.get("reviewed") is not True, answer
    assert answer.get("error_code") == oracle["error_code"], (answer, oracle)
    assert answer.get("retryable") == oracle["retryable"], (answer, oracle)
    assert "board.yaml" not in json.dumps(answer), answer
    _no_exception_text(answer, oracle, raised)
    assert _snapshot(world) == before


#: A board whose gates name a checker that reads the item's tags: the gate
#: builds the checker's entry from the index (`checker_entry`, #1587).
_NAMING_TAGS = "gates:\n" + "".join(
    f"  {status}:\n    policy: enforce\n    criteria:\n"
    "      - {text: has tags, checker: has_tags}\n"
    for status in ("in_progress", "done")
)


@pytest.mark.parametrize("tool", sorted(GATE_OF_TOOL))
@pytest.mark.parametrize("raised", sorted(RAISED))
def test_a_failed_checker_entry_lookup_keeps_its_own_error(world, raised, tool, monkeypatch):  # noqa: F811
    """The entry a named checker sees is read from the index (#1587), so a
    failure of that read is the index's, never board.yaml's (#1585)."""
    import pyrite.services.checker_entry as checker_entry_module

    make = RAISED[raised]
    oracle = _oracle(world, make, monkeypatch)
    monkeypatch.undo()  # the oracle's blockers lookup works again
    _write_board(world, _NAMING_TAGS)
    before = _snapshot(world)
    calls: list[int] = []

    def raiser(*a, **k):
        calls.append(1)
        raise make()

    monkeypatch.setattr(checker_entry_module, "checker_entry", raiser)

    answer = _mcp(world, tool, MCP_CALLS[tool])

    assert calls, "the builder was not reached"
    assert answer.get("ready") is not True and answer.get("claimed") is not True, answer
    assert answer.get("transitioned") is not True and answer.get("reviewed") is not True, answer
    assert answer.get("error_code") == oracle["error_code"], (answer, oracle)
    assert answer.get("retryable") == oracle["retryable"], (answer, oracle)
    assert "board.yaml" not in json.dumps(answer), answer
    _no_exception_text(answer, oracle, raised)
    assert _snapshot(world) == before


#: The `pyrite sw` commands that call a handler which evaluates a gate (the
#: CLI never reaches `_mcp_claim` or `_mcp_transition`: those commands go
#: through KBService and do not consult the board, #1288). name -> (argv,
#: the gate's status, whether it prints the CLI's error shape as JSON).
CLI_GATE_COMMANDS = {
    "sw check-ready": (["sw", "check-ready", "item-ready", "-k", KB], "in_progress", True),
    "sw refine": (["sw", "refine", "-k", KB], "in_progress", True),
    "sw review -o approved": (
        ["sw", "review", "item-review", "-o", "approved", "--reviewer", "r", "-k", KB],
        "done",
        False,  # always the rich line
    ),
}


def _cli_cells():
    cells = []
    for name, (_argv, status, _json) in CLI_GATE_COMMANDS.items():
        for regime in sorted(_regimes()):
            if _the_gate_names_the_lookup(regime, status):
                cells.append(pytest.param(name, regime))
            else:
                why = "the built-in board's done gate never reads the index: nothing fails"
                cells.append(pytest.param(name, regime, marks=pytest.mark.control(reason=why)))
    return cells


@pytest.mark.parametrize(("command", "regime"), _cli_cells())
@pytest.mark.parametrize("raised", sorted(RAISED))
def test_a_gate_command_renders_the_causes_error(
    world,  # noqa: F811
    raised,
    regime,
    command,
    monkeypatch,
    caplog,
):
    """Every `pyrite sw` command that evaluates a gate answers a failed
    blockers lookup in the CLI's error shape (exit 1, JSON where the command
    prints JSON), with the code and `retryable` the MCP tool answers for the
    same exception: its own for a Pyrite error, `INTERNAL` (retryable, a fixed
    text, the cause in the log) for one Pyrite did not raise. Never an
    uncaught exception, never `board.yaml`, never `VALIDATION_FAILED`."""
    make = RAISED[raised]
    oracle = _oracle(world, make, monkeypatch)  # the MCP dispatcher's answer
    argv, status, prints_json = CLI_GATE_COMMANDS[command]
    board = _regimes()[regime]
    if board is not None:
        _write_board(world, board)
    before = _snapshot(world)
    calls = _the_lookup_raises(monkeypatch, make)

    with caplog.at_level("ERROR"):
        result = _cli(world, argv)

    if not _the_gate_names_the_lookup(regime, status):
        assert calls == [] and result.exit_code == 0, result.output
        return
    assert calls, "the lookup was not reached"
    assert isinstance(result.exception, SystemExit), repr(result.exception)  # not a traceback
    assert result.exit_code != 0, result.output
    if raised == "StorageError":
        assert result.exit_code == 1, result.output
    out = result.stdout
    assert "board.yaml" not in out and "VALIDATION_FAILED" not in out, out
    assert MARKER not in out or raised in ("StorageError", "KBNotFoundError"), out
    if prints_json:
        answer = json.loads(out[out.index("{") :])
        assert answer["error_code"] == oracle["error_code"], (answer, oracle)
        assert answer["retryable"] == oracle["retryable"], (answer, oracle)
        assert answer.get("ready") is not True, answer
        if raised != "KBNotFoundError":
            assert answer["error"] == oracle["error"], (answer, oracle)
    else:
        assert f"[{oracle['error_code']}]" in out, out
    if raised not in ("StorageError", "KBNotFoundError"):
        # The cause is in the log, so an `out` free of it is a real result.
        assert MARKER in caplog.text, "the cause was not logged"
    assert _snapshot(world) == before


@pytest.mark.parametrize(
    "command_args",
    [
        ["sw", "check-ready", "item-ready", "-k", KB, "-f", "rich"],
        ["sw", "refine", "-k", KB, "-f", "rich"],
    ],
)
def test_a_gate_command_rich_output_renders_internal_error(
    world,  # noqa: F811
    command_args,
    monkeypatch,
):
    """sw check-ready and sw refine without --format json render ERROR [INTERNAL]
    and do not leak internal exception details (#1630)."""
    make = RAISED["OSError"]
    _write_board(world, _NAMING_BLOCKERS)
    _the_lookup_raises(monkeypatch, make)
    result = _cli(world, command_args)
    assert result.exit_code != 0, result.output
    assert "ERROR [INTERNAL]" in result.stdout, result.stdout
    assert MARKER not in result.stdout, result.stdout


@pytest.mark.parametrize(
    ("exc", "expected_type", "expected_exit_code"),
    [
        (typer.Exit(0), typer.Exit, 0),
        (typer.Abort(), typer.Abort, None),
        (typer.BadParameter("x"), typer.BadParameter, 2),
    ],
)
def test_refusal_rendered_propagates_typer_control_flow(exc, expected_type, expected_exit_code):
    """#1630: Typer's own control-flow exceptions (typer.Exit, typer.Abort,
    typer.BadParameter) must propagate unchanged from _refusal_rendered rather
    than being caught and converted to an INTERNAL response."""
    from pyrite_software_kb.cli import _refusal_rendered

    with pytest.raises(expected_type) as exc_info:
        _refusal_rendered(lambda: (_ for _ in ()).throw(exc), "json")

    assert exc_info.value is exc
    if expected_exit_code is not None:
        assert getattr(exc_info.value, "exit_code", None) == expected_exit_code


def test_refusal_rendered_propagates_memory_error():
    """#1630: unlike the MCP dispatcher which catches all Exception subclasses,
    the CLI lets MemoryError propagate so fatal out-of-memory errors terminate
    the process rather than returning an INTERNAL response."""
    from pyrite_software_kb.cli import _refusal_rendered

    with pytest.raises(MemoryError):
        _refusal_rendered(lambda: (_ for _ in ()).throw(MemoryError()), "json")
