"""pytest plugin that scripts/verify_red_ci.py loads into both of its runs.

It keeps only the tests the driver asks for (``VERIFY_RED_SELECT``: a JSON list
of ``[file, class..., function]`` keys) and appends one JSON line per report to
``VERIFY_RED_RECORD`` as it happens, so a run killed by the driver's timeout
still leaves what it finished. A failure is recorded from the exception object
itself -- its type's MRO and its ``args`` -- never from a traceback, so
the repo's ``--tb`` setting cannot change a verdict.
"""

from __future__ import annotations

import json
import os
import re

import pytest


def _write(record: dict) -> None:
    with open(os.environ["VERIFY_RED_RECORD"], "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def key(nodeid: str) -> tuple[str, ...]:
    """``tests/x.py::TestA::test_b[1]`` -> ``("tests/x.py", "TestA", "test_b")``."""
    return tuple(nodeid.split("[", 1)[0].split("::"))  # a parameter id may hold "::"


def pytest_configure(config: pytest.Config) -> None:
    # Registered here too: the run without the fix may use a pyproject from
    # before the marker existed, and --strict-markers would stop it.
    config.addinivalue_line("markers", "control(reason): a deliberate negative control")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("VERIFY_RED_BASE_COLLECT"):
        return  # the merge base's own cells are recorded whole, in pytest_collection_finish
    with open(os.environ["VERIFY_RED_SELECT"], encoding="utf-8") as fh:
        wanted = {tuple(k) for k in json.load(fh)}
    keep = [i for i in items if key(i.nodeid) in wanted]
    drop = [i for i in items if key(i.nodeid) not in wanted]
    if drop:
        config.hook.pytest_deselected(items=drop)
        items[:] = keep


def pytest_collectreport(report: pytest.CollectReport) -> None:
    if report.failed:
        _write({"collect": report.nodeid})
    elif report.nodeid.endswith(".py"):
        # Collected (or skipped as a whole): pytest ran even if no test in it
        # did, so "recorded nothing" is not a broken conftest or plugin.
        _write({"collected": report.nodeid})


def _mark_of(m):
    return getattr(m, "mark", m)


def _own_controls(item: pytest.Item) -> list:
    """The control markers the test declares itself: on the function (a decorator,
    however the marker was built) or on one of its parameters (``pytest.param(marks=)``)."""
    declared = list(getattr(getattr(item, "obj", None), "pytestmark", []) or [])
    spec = getattr(item, "callspec", None)
    declared += list(getattr(spec, "marks", []) or [])
    return [m for m in map(_mark_of, declared) if m.name == "control"]


def _reason(item: pytest.Item, marks: list) -> str | None:
    """None without a marker; else its reason, "" when it gives none."""
    if not marks:
        return None
    marker = marks[0]
    reason = marker.kwargs.get("reason") or (marker.args[0] if marker.args else "")
    doc = getattr(getattr(item, "function", None), "__doc__", None) or ""
    return str(reason or doc).strip()


def _control_state(item: pytest.Item, phase: str) -> tuple[str | None, str | None, list[str]]:
    """(the test's own control reason, any control reason, where the rest came from).

    The rest is everything that is not written on the function or a parameter:
    a mark on the class or module (``pytestmark``, through a constant too), and a
    mark added while pytest runs -- by a collection hook, a fixture's
    ``request.applymarker`` or the test body -- told apart by the phase in which
    it first shows. A mark a hook slips into the function's own ``pytestmark``
    before the items are built cannot be told from a decorator (docs/testing.md).
    """
    own = _own_controls(item)
    found = list(item.iter_markers_with_node("control"))
    levels = sorted(
        {
            f"a {'class' if isinstance(n, pytest.Class) else 'module'}-level mark"
            if isinstance(n, pytest.Class | pytest.Module)
            else f"a mark on {type(n).__name__}"
            for n, _ in found
            if n is not item
        }
    )
    bad = ["declared at module or class level (" + ", ".join(levels) + ")"] if levels else []
    excess = sum(n is item for n, _ in found) - len(own)
    if excess > 0:
        seen = getattr(item, "_vr_added", None)
        if seen is None:
            seen = item._vr_added = {
                "collect": "added by a collection hook (a conftest pytest_collection_modifyitems)",
                "setup": "added during setup (an autouse fixture's request.applymarker, or a setup hook)",
                "call": "added inside the test (request.applymarker or node.add_marker)",
                "teardown": "added during teardown",
            }[phase]
        bad.append(seen)
    return _reason(item, own), _reason(item, own or [mk for _, mk in found]), bad


def _values(item: pytest.Item) -> str:
    """The cell's parameter values as recorded at collection. Its identity is the
    function and these, never the generated node id."""
    spec = getattr(item, "callspec", None)
    if spec is None:
        return ""
    text = repr(sorted(spec.params.items(), key=lambda kv: kv[0]))
    return re.sub(r" at 0x[0-9a-f]+", "", text)


def pytest_collection_finish(session: pytest.Session) -> None:
    for item in session.items:
        _, any_, bad = _control_state(item, "collect")  # also primes where a late mark came from
        if os.environ.get("VERIFY_RED_BASE_COLLECT"):
            _write({"base": item.nodeid, "values": _values(item), "control": any_, "bad": bad})


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    report = yield
    own, any_, bad = _control_state(item, call.when)
    record = {
        "nodeid": item.nodeid,
        "when": call.when,
        "outcome": report.outcome,
        "control": own,
        "any_control": any_,
        "bad": bad,
        "values": _values(item),
    }
    if report.failed and call.excinfo is not None:
        exc = call.excinfo.value
        record["exc"] = {
            "types": [c.__name__ for c in type(exc).__mro__],
            "args": [a for a in exc.args if isinstance(a, str)],
        }
    _write(record)
    return report
