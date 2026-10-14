"""The advisory mypy job is red only when a change adds type errors (#885).

Pure-function tests on sample mypy output; `run` is the decision the CI step
makes, so these enter it the way CI does (output text in, exit code out).
"""

import importlib.util
import json
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "mypy_ratchet", Path(__file__).resolve().parent.parent / "scripts" / "mypy_ratchet.py"
)
ratchet = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ratchet)


def out(*errs: tuple[str, int, str, str]) -> str:
    lines = [f"{f}:{n}: error: {msg}  [{code}]" for f, n, msg, code in errs]
    lines.append(
        f"Found {len(errs)} errors in 1 file (checked 2 source files)"
        if errs
        else "Success: no issues found"
    )
    return "\n".join(lines) + "\n"


A1 = (
    "pyrite/a.py",
    10,
    'Incompatible return value type (got "int", expected "str")',
    "return-value",
)
A2 = ("pyrite/a.py", 20, 'Item "None" of "X | None" has no attribute "y"', "union-attr")
B1 = ("pyrite/b.py", 5, 'Argument 1 to "f" has incompatible type "str"; expected "int"', "arg-type")


@pytest.fixture
def baseline(tmp_path):
    p = tmp_path / "base.json"
    code, _ = ratchet.run(out(A1, A2), p, update=True, allow_increase=True)
    assert code == 0
    return p


def test_unchanged_passes(baseline):
    code, text = ratchet.run(out(A1, A2), baseline)
    assert code == 0 and "delta +0" in text


def test_line_shifts_are_not_new_errors(baseline):
    shifted = ("pyrite/a.py", 99, A1[2], A1[3])
    code, _ = ratchet.run(out(shifted, A2), baseline)
    assert code == 0


def test_a_file_that_rises_fails_and_is_named_with_the_new_error(baseline):
    code, text = ratchet.run(out(A1, A2, B1), baseline)
    assert code == 1
    assert "pyrite/b.py" in text and 'expected "int"' in text


def test_a_second_copy_of_a_known_error_is_a_rise(baseline):
    code, text = ratchet.run(out(A1, A1, A2), baseline)
    assert code == 1 and "pyrite/a.py" in text


def test_a_drop_cannot_hide_a_rise_elsewhere(baseline):
    code, text = ratchet.run(out(A1, B1), baseline)  # A2 fixed, B1 new
    assert code == 1
    assert "RISE pyrite/b.py" in text and "RISE pyrite/a.py" not in text


def test_a_drop_alone_passes_and_says_how_to_lower(baseline):
    code, text = ratchet.run(out(A1), baseline)
    assert code == 0 and "--update" in text


def test_update_lowers_the_baseline(baseline):
    code, _ = ratchet.run(out(A1), baseline, update=True)
    assert code == 0
    data = json.loads(baseline.read_text())
    assert data["total"] == 1 and data["files"]["pyrite/a.py"]["count"] == 1


def test_update_refuses_to_raise(baseline):
    before = baseline.read_text()
    code, text = ratchet.run(out(A1, A2, B1), baseline, update=True)
    assert code == 1 and "refused" in text
    assert baseline.read_text() == before


def test_update_raises_only_with_allow_increase(baseline):
    code, _ = ratchet.run(out(A1, A2, B1), baseline, update=True, allow_increase=True)
    assert code == 0 and json.loads(baseline.read_text())["total"] == 3


def test_output_without_a_summary_is_not_zero_errors(baseline):
    code, text = ratchet.run("Traceback (most recent call last):\n", baseline)
    assert code == 2 and "did not finish" in text


def test_step_summary_is_written(baseline, tmp_path, monkeypatch):
    summary = tmp_path / "summary.md"
    mypy_out = tmp_path / "mypy.out"
    mypy_out.write_text(out(A1, A2))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert ratchet.main([str(mypy_out), "--baseline", str(baseline)]) == 0
    assert "baseline 2" in summary.read_text()


def test_committed_baseline_is_well_formed():
    data = json.loads((Path(__file__).resolve().parent.parent / ".mypy-baseline.json").read_text())
    assert data["total"] == sum(f["count"] for f in data["files"].values())
