"""Keep CLI flag assertions independent of Rich's colour rendering."""

import ast
from pathlib import Path

import pytest


def _raw_flag_comparisons(source: str) -> list[int]:
    """Return offending lines, including every pair in chained comparisons."""
    lines = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Compare):
            continue
        for left, operator, right in zip(
            [node.left, *node.comparators[:-1]], node.ops, node.comparators, strict=True
        ):
            if (
                isinstance(operator, (ast.In, ast.NotIn))
                and isinstance(left, ast.Constant)
                and isinstance(left.value, str)
                and left.value.startswith("--")
                and isinstance(right, ast.Attribute)
                and right.attr in {"stdout", "output"}
            ):
                lines.append(left.lineno)
    return lines


@pytest.mark.control(reason="Pins the structural detector independently of migrated assertions")
@pytest.mark.parametrize(
    "source",
    [
        'assert "--flag" in result.stdout',
        'assert "--flag" not in result.output',
        'assert "prefix" != "--flag" in result.stdout',
    ],
)
def test_guard_detects_raw_flag_comparisons(source):
    assert _raw_flag_comparisons(source) == [1]


@pytest.mark.control(reason="Pins the structural detector independently of migrated assertions")
@pytest.mark.parametrize(
    "source",
    [
        'assert "--flag" in plain(result.stdout)',
        'assert "timeline" in result.output',
        "assert flag in result.stdout",
        'assert "--flag" == result.stdout',
    ],
)
def test_guard_allows_normalized_or_unrelated_comparisons(source):
    assert _raw_flag_comparisons(source) == []


def test_cli_help_assertions_are_plain():
    root = Path(__file__).resolve().parents[1]
    paths = sorted({*root.glob("tests/**/test_*.py"), *root.glob("extensions/*/tests/test_*.py")})
    violations = [
        f"{path.relative_to(root).as_posix()}:{line}: use tests.cli_help.plain() "
        "before checking a CLI flag"
        for path in paths
        for line in _raw_flag_comparisons(path.read_text(encoding="utf-8"))
    ]
    assert not violations, "\n".join(violations)
