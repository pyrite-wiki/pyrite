"""A file its type cannot load is read partially: the declared type, the fields
its loader accepts, and ``load_problems`` naming the rest (ADR-0042 8a point 5,
decision 9; #1261).

A refused field is absent from the read (REST: ``null``), never the type's
default or a placeholder (``0``, ``"open"``, ``[]``, ``{}``). A refusal no
field explains answers ``ENTRY_UNREADABLE``, never "not found".

The partial view is built in one place, ``KBRepository.load_for_read``, and
called from one place, ``KBService._entry_file``; its entry never leaves the
read (``_read_entry`` returns a dict). Problems travel beside the entry, never
on it: ``Entry`` has no ``load_problems`` attribute. Both are structural tests
below, because the partial model's defaults are exactly what rounds 1 and 2 of
#1311 let a writer, a validator and a hook see. Writes are
``tests/test_unloadable_file_is_never_written.py``; the shapes and the
injected loaders are shared with it.
"""

from __future__ import annotations

import ast
import dataclasses
import json
from pathlib import Path

import pytest

from pyrite.config import KBConfig
from tests.test_unloadable_file_is_never_written import (  # noqa: F401 - the autouse fixture
    REL,
    SHAPES,
    _loaders_that_refuse,
    _text,
)

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def make_world(tmp_path):
    import contextlib

    from tests._world import build_world

    stack = contextlib.ExitStack()
    count = {"n": 0}

    def make(text: str):
        count["n"] += 1
        kb = tmp_path / f"sw{count['n']}"
        (kb / REL).parent.mkdir(parents=True, exist_ok=True)
        (kb / REL).write_text(text, encoding="utf-8")
        w = build_world(
            tmp_path / f"w{count['n']}", kbs=[KBConfig(name="sw", path=kb, kb_type="software")]
        )
        stack.callback(w.close)
        stack.enter_context(w.pointed())
        return w

    with stack:
        yield make


def _cli_get(world):
    result = world.cli("get", "x", "-k", "sw", "--format", "json")
    return result.exit_code, result.stdout + (getattr(result, "stderr", "") or "")


def _read_cli_get(world):
    from pyrite.read_cli import app as read_app

    result = world.cli("get", "x", "-k", "sw", "--format", "json", app=read_app)
    return result.exit_code, result.stdout + (getattr(result, "stderr", "") or "")


def _rest_get(world):
    resp = world.client.get("/api/entries/x", params={"kb": "sw"})
    return resp.status_code, resp.text


def _mcp_get(world):
    res = world.mcp()._dispatch_tool("kb_get", {"entry_id": "x", "kb_name": "sw"})
    return (0 if "entry" in res else 1), json.dumps(res, default=str)


SURFACES = {"cli": _cli_get, "pyrite-read": _read_cli_get, "rest": _rest_get, "mcp": _mcp_get}
OK = {"cli": 0, "pyrite-read": 0, "rest": 200, "mcp": 0}

ISOLATED = [
    pytest.param(shape, entry_type, id=f"{shape}-{entry_type}")
    for shape, per_type in SHAPES.items()
    for entry_type, spec in per_type.items()
    if spec.field
]
UNISOLATED = [
    pytest.param(shape, entry_type, id=f"{shape}-{entry_type}")
    for shape, per_type in SHAPES.items()
    for entry_type, spec in per_type.items()
    if not spec.field
]


def _entry(surface: str, text: str) -> dict:
    data = json.loads(text)
    return data["entry"] if surface == "mcp" else data


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize(("shape", "entry_type"), ISOLATED)
def test_a_refused_field_is_absent_from_every_read_and_named(
    make_world, shape, entry_type, surface
):
    spec = SHAPES[shape][entry_type]
    world = make_world(_text(entry_type, spec, body="Zebra body.\n"))

    status, text = SURFACES[surface](world)

    assert status == OK[surface], text
    data = _entry(surface, text)
    assert data["entry_type"] == entry_type
    assert "Zebra body." in data["body"]
    # The field the file key denotes: `metadata.rank` is `rank`.
    key = spec.field
    field = key.split(".", 1)[1] if key.startswith("metadata.") else key
    # Absent, or REST's null for a fixed column; never a default or placeholder.
    assert data.get(field) is None, f"{field} presented as {data[field]!r}"
    if field != "metadata":
        assert field not in (data.get("metadata") or {})
    assert [p["field"] for p in data["load_problems"]] == [key]
    problem = data["load_problems"][0]
    assert set(problem) == {"field", "rule", "severity", "message"}
    assert problem["rule"] == "load"
    assert spec.loader in problem["message"]


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize(("shape", "entry_type"), UNISOLATED)
def test_a_refusal_no_field_explains_is_entry_unreadable_never_not_found(
    make_world, shape, entry_type, surface
):
    spec = SHAPES[shape][entry_type]
    world = make_world(_text(entry_type, spec))

    status, text = SURFACES[surface](world)

    assert status != OK[surface], text
    if surface == "rest":
        assert status == 422, text
    assert "ENTRY_UNREADABLE" in text, text
    assert "not found" not in text.lower(), text
    assert REL in text and spec.loader in text, text


def test_a_rest_read_omits_a_refused_column_and_never_nulls_a_list(make_world):
    # EntryResponse defaults `metadata` to {}: for a refused `metadata:` that
    # default would read as "the file has an empty metadata", so the column is
    # left out of the JSON. Every other list stays a list: a client indexes
    # `.length` on them (round 4, B2: `participants` was null on any partial
    # read, and EntryMeta.svelte threw on it).
    world = make_world(_text("task", SHAPES["metadata-whole"]["task"]))

    data = world.client.get("/api/entries/x", params={"kb": "sw"}).json()

    assert "metadata" not in data
    assert [p["field"] for p in data["load_problems"]] == ["metadata"]
    for key in ("tags", "participants", "sources", "outlinks", "backlinks"):
        assert isinstance(data[key], list), (key, data[key])


@pytest.mark.parametrize("column", ["tags", "participants", "sources"])
def test_a_rest_read_omits_a_refused_list_column_rather_than_nulling_it(make_world, column):
    # A list column the type itself refuses is left out (absent, as on the
    # other surfaces), never `null` and never a `[]` standing for the file's
    # value. The other list columns keep their lists.
    from pyrite.models.task import TaskEntry
    from tests.test_unloadable_file_is_never_written import Shape

    original = TaskEntry.from_frontmatter.__func__

    def refusing(klass, meta, body=""):
        if column in meta:
            raise ValueError(f"{column} must be a list of strings")
        return original(klass, meta, body)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(TaskEntry, "from_frontmatter", classmethod(refusing))
        world = make_world(_text("task", Shape(f"{column}: oops\n")))

        data = world.client.get("/api/entries/x", params={"kb": "sw"}).json()

    assert column not in data, data
    assert [p["field"] for p in data["load_problems"]] == [column]
    for other in {"tags", "participants", "sources", "outlinks", "backlinks"} - {column}:
        assert isinstance(data[other], list), (other, data[other])


@pytest.mark.control(reason="a file that loads reads as before, with no load_problems key")
@pytest.mark.parametrize("surface", SURFACES)
def test_a_file_that_loads_has_no_load_problems(make_world, surface):
    from tests.test_unloadable_file_is_never_written import Shape

    world = make_world(_text("backlog_item", Shape()))

    status, text = SURFACES[surface](world)

    assert status == OK[surface], text
    assert "load_problems" not in _entry(surface, text)


# -- structure: one partial read, and no problems on the model -----------------


def _calls_to(name: str) -> list[str]:
    sites = []
    sources = sorted((REPO / "pyrite").rglob("*.py")) + sorted(
        REPO.glob("extensions/*/src/**/*.py")
    )
    for src in sources:
        tree = ast.parse(src.read_text(encoding="utf-8"))
        for scope in ast.walk(tree):
            if not isinstance(scope, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for node in ast.walk(scope):
                func = node.func if isinstance(node, ast.Call) else None
                called = getattr(func, "attr", None) or getattr(func, "id", None)
                if called == name:
                    sites.append(f"{src.relative_to(REPO).as_posix()}::{scope.name}")
    return sites


def test_load_for_read_has_one_caller_the_single_entry_read():
    # Innermost scopes only: the function, not the class around it.
    sites = sorted(set(_calls_to("load_for_read")) - {"pyrite/services/kb_service.py::KBService"})
    assert sites == ["pyrite/services/kb_service.py::_entry_file"], sites


@pytest.mark.control(
    reason="a ratchet against #1311 rounds 1-2 (Entry.load_problems); dev never had it"
)
def test_an_entry_never_carries_load_problems():
    from pyrite.models import Entry
    from tests.test_loader_failure_keeps_the_type import TYPES

    for name, (_origin, cls) in {"entry": ("core", Entry), **TYPES}.items():
        assert not hasattr(cls, "load_problems"), name
        assert "load_problems" not in {f.name for f in dataclasses.fields(cls)}, name
