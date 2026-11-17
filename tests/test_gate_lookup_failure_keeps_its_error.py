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

from pyrite.exceptions import KBNotFoundError, StorageError
from tests.test_board_config_refuses import (
    KB,
    MARKER,
    MCP_CALLS,
    _cli,
    _cli_json,
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


def test_every_caller_of_the_gate_has_a_cell():
    """A sixth caller of ``_evaluate_gate`` cannot be added without a cell."""
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


@pytest.mark.parametrize("regime", sorted(_regimes()))
@pytest.mark.parametrize("tool", sorted(GATE_OF_TOOL))
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


@pytest.mark.parametrize("regime", sorted(_regimes()))
@pytest.mark.parametrize("raised", ["StorageError", "OSError"])
def test_check_ready_on_the_cli_gives_the_causes_code_and_exit_code(
    world,  # noqa: F811
    raised,
    regime,
    monkeypatch,
):
    """The oracle is `sw pull-next`, which looks dependencies up with no gate.
    A Pyrite error is rendered in the CLI's error shape with its own code; any
    other exception is not Pyrite's to render, so it escapes the command as it
    does from `pull-next` (exit 1, no JSON) and never as the board's refusal."""
    make = RAISED[raised]
    _write_board(world, _NO_GATES)
    _the_lookup_raises(monkeypatch, make)
    oracle = _cli(world, ["sw", "pull-next", "-k", KB])
    (world.kb_path(KB) / "board.yaml").unlink()
    board = _regimes()[regime]
    if board is not None:
        _write_board(world, board)
    before = _snapshot(world)

    result = _cli(world, ["sw", "check-ready", "item-ready", "-k", KB])

    assert result.exit_code != 0 and result.exit_code == oracle.exit_code, (result, oracle)
    assert "board.yaml" not in result.output and MARKER not in result.output, result.output
    assert "VALIDATION_FAILED" not in result.output, result.output
    if raised == "StorageError":
        answer, oracle_answer = _cli_json(result), _cli_json(oracle)
        assert answer["error_code"] == oracle_answer["error_code"] != "VALIDATION_FAILED"
        assert answer["retryable"] == oracle_answer["retryable"], answer
        assert answer["error"] == oracle_answer["error"], answer
    else:
        assert type(result.exception) is type(oracle.exception) is OSError, result.exception
        assert _the_gate_names_the_lookup(regime, "in_progress")
    assert _snapshot(world) == before
