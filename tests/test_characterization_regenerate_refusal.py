"""PYRITE_CHARACTERIZATION_REGENERATE=1 under pytest-xdist refuses loudly (#712).

`pytest tests/characterization -n 4` used to rewrite only the shard-owned
files the invoking worker held, with no error for the others: the next
reader saw a stale golden look like fresh evidence. Parallel *comparisons*
stay untouched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.characterization import golden_io


@pytest.fixture
def goldens(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(golden_io, "GOLDEN_DIR", tmp_path)
    return tmp_path


def test_a_parallel_regenerate_refuses_before_any_write(
    goldens: Path, monkeypatch: pytest.MonkeyPatch
):
    """An xdist worker cannot regenerate goldens: no file is touched."""
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    monkeypatch.delenv("PYTEST_XDIST_WORKER_COUNT", raising=False)
    monkeypatch.setenv(golden_io.REGENERATE_ENV, "1")
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw3")

    with pytest.raises(RuntimeError) as raised:
        golden_io.save("rest", {"rest | principal | state": {"a": 1}})

    message = str(raised.value)
    assert "serially" in message
    assert list(goldens.rglob("*.json")) == []


def test_a_parallel_regenerate_refuses_even_without_a_worker_env(
    goldens: Path, monkeypatch: pytest.MonkeyPatch
):
    """A wrapper that strips only the worker id is still caught by the count."""
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    monkeypatch.delenv("PYTEST_XDIST_WORKER_COUNT", raising=False)
    monkeypatch.setenv(golden_io.REGENERATE_ENV, "1")
    monkeypatch.setenv("PYTEST_XDIST_WORKER_COUNT", "4")

    with pytest.raises(RuntimeError) as raised:
        golden_io.save("mcp", {"mcp | principal | state": {}})

    assert "serially" in str(raised.value)
    assert list(goldens.rglob("*.json")) == []


def test_a_parallel_compare_run_is_untouched(
    goldens: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Comparing goldens under pytest-xdist stays allowed."""
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")
    golden_path = goldens / "rest.rest.json"
    golden_path.write_text('{"rest | principal | state": {"a": 1}}\n', encoding="utf-8")

    golden_io.save("rest", {"rest | principal | state": {"a": 1}})

    assert golden_path.read_text(encoding="utf-8") == '{"rest | principal | state": {"a": 1}}\n'


def test_a_serial_regenerate_writes_some_goldens(
    goldens: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without xdist the regenerate path stays available."""
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    monkeypatch.delenv("PYTEST_XDIST_WORKER_COUNT", raising=False)
    monkeypatch.setenv(golden_io.REGENERATE_ENV, "1")
    golden_path = goldens / "error_bodies.json"
    golden_path.write_text('{"old": {}}\n', encoding="utf-8")

    golden_io.save("error_bodies", {"new": {"a": 1}})

    assert list(goldens.rglob("*.json")) == [golden_path]
