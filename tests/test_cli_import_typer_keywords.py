"""The command lines parse on every Typer version ``pyproject.toml`` allows (#1597)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_no_typer_option_passes_click_type():
    """No call under ``pyrite/`` or ``extensions/`` passes ``click_type=``.

    Typer 0.29 removed that keyword: ``typer.Option(..., click_type=...)`` raises
    ``TypeError`` when the module is imported, so every command line dies at
    start-up. CI installs the locked Typer (0.27.3), which still accepts it, so
    no behavioural test sees the crash; only a read of the source does. Use
    ``parser=`` (kept in 0.29) or ``min=``/``max=``.
    """
    found = []
    for base in ("pyrite", "extensions"):
        for path in sorted((ROOT / base).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    for kw in node.keywords:
                        if kw.arg == "click_type":
                            found.append(f"{path.relative_to(ROOT)}:{kw.value.lineno}")

    assert not found, f"click_type= was removed in Typer 0.29; use parser=: {found}"


@pytest.mark.parametrize("value", ["-0.1", "abc"])
def test_threshold_below_zero_or_not_a_number_is_a_usage_error(cli_world, value):
    world = cli_world()

    result = world.cli("qa", "duplicates", "-t", "task", "--threshold", value)

    assert result.exit_code == 2, result.output


@pytest.mark.parametrize(
    "args", [["--threshold", "0"], ["--threshold", "1"], []], ids=["0", "1", "default"]
)
def test_threshold_at_a_bound_or_left_out_is_accepted(cli_world, args):
    world = cli_world()

    result = world.cli("qa", "duplicates", "-t", "task", *args)

    assert result.exit_code == 0, result.output
