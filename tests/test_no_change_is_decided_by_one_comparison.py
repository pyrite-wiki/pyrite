"""An update decides "no change" in one place (#1704).

The property: one function, ADR-0042 decision 3's ``values_equal`` in
``pyrite/storage/file_operations.py``, decides whether the value an
update names is the value the file already holds. ``apply``'s no-change
check -- the model in ``_model_apply`` and its narrow twin in
``_set_node``, with the list alignment in ``_plan``/``_lcs`` -- already
called it; the service's two judgements (the ``unchanged`` report in
``split_echoed_update``, the kept-row judgement in ``_validate_write``)
call it now too, and the service's own type-strict wrapper is deleted.
Before, the service compared type-strictly while ``apply`` compared by
decision 3, so the two disagreed about a respelled number: the file
stayed byte-identical, but the field was not reported under
``unchanged``. Because the one comparison is decision 3's own, every
"same value" decision an update makes agrees with it, including the
YAML loader's timestamp spellings (``+5``, a one-digit hour; #991): a
value the file already holds is never respelled.

The maintainer's decision on #1704 (2026-11-19) settles the reported
case: "an update that sends `5` over a file's `5.0` is reported
unchanged and the file is left untouched. It is not stored as `5`." So
under the one comparison a number in another spelling IS the same
value, on every surface. The grid below pins ``5`` and ``5.0`` over a
file's ``importance: 5.0`` as a no-change on MCP ``kb_update``, REST
``PUT``/``PATCH`` and CLI ``update -f``: reported as a no-change (REST
``PUT``, which the endpoint puts through the echo comparison by design
(#569 item 1), and an echo-shaped ``kb_update`` name the field under
``unchanged``; a named update answers ``changed: false``), and the
file's bytes untouched. An echo of a read likewise lists every field
it did not change under ``unchanged``, whatever the number's spelling.
Where the value on disk is not already that number, nothing here
changes: an accepted importance that actually changes a value is
still stored as its int (#1588).

``values_identical`` keeps the type-strict rule the doubly spelled
field merge needs; it is not an update judgement and no update path
calls it for one. Decision 3's comparison carries the rest: ``.nan``
is the same value as itself, at the top level and inside a list or a
mapping, so a file holding one stays updatable; and a string that
spells a date or a timestamp (its ISO form, or the same instant, in
any spelling the YAML loader reads) is the same value, so a date sent
back as text is not a change.

The structural checker walks the three modules' sources with ``ast``.
What it cannot see: a comparison inlined without calling the function
(a hand-rolled ``==`` in a new helper would pass it), and a fourth
caller judging sameness for some new purpose. The behavioural cells
catch a wrong answer on the paths they drive; a new judgement is a
review matter.

Planted violations go through the same checker and each must be found,
so a checker that finds nothing cannot pass.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.schema.field_aliases import values_identical
from pyrite.services.kb_service import KBService, _frontmatter_span
from pyrite.storage.database import PyriteDB
from pyrite.storage.file_operations import (
    AddSubkey,
    OperationRefusedError,
    Set,
    apply,
    values_equal,
)
from pyrite.storage.index import IndexManager

KB = "ro"
ROOT = Path(__file__).resolve().parent.parent

KB_YAML = """\
name: ro
types:
  note:
    required: [title]
    subdirectory: notes/
  event:
    required: [title, date]
    subdirectory: events/
"""

NOTE = "---\nid: n1\ntitle: N1\ntype: note\nimportance: 5.0\n---\n\nbody\n"
EVENT = "---\nid: e1\ntitle: E1\ntype: event\ndate: 2026-01-02\n---\n\nbody\n"
NAN_NOTE = "---\nid: n2\ntitle: N2\ntype: note\nratio: .nan\n---\n\nbody\n"


@pytest.fixture
def env(tmp_path):
    kb_path = tmp_path / KB
    (kb_path / "notes").mkdir(parents=True)
    (kb_path / "events").mkdir(parents=True)
    (kb_path / "kb.yaml").write_text(KB_YAML)
    (kb_path / "notes" / "n1.md").write_text(NOTE)
    (kb_path / "notes" / "n2.md").write_text(NAN_NOTE)
    (kb_path / "events" / "e1.md").write_text(EVENT)
    db_path = tmp_path / "index.db"
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name=KB, path=kb_path, kb_type="generic")],
        settings=Settings(index_path=db_path, auto_embed=False),
    )
    db = PyriteDB(db_path)
    IndexManager(db, config).index_all()
    db.close()
    return {"config": config, "kb_path": kb_path, "db_path": db_path, "tmp_path": tmp_path}


def _file(env, entry_id):
    (found,) = env["kb_path"].rglob(f"{entry_id}.md")
    return found


def _importance_line(env, entry_id="n1"):
    text = _file(env, entry_id).read_text()
    (line,) = [line for line in text.splitlines() if line.startswith("importance:")]
    return line


def _send(env, surface, entry_id, fields):
    """One update through ``surface``; returns the surface's answer dict."""
    config = env["config"]
    if surface == "mcp":
        from pyrite.server.mcp_server import PyriteMCPServer

        server = PyriteMCPServer(config, tier="admin")
        try:
            return server._dispatch_tool(
                "kb_update", {"kb_name": KB, "entry_id": entry_id, **fields}
            )
        finally:
            server.close()
    if surface == "cli":
        from tests._world import build_world

        # A world over the env's own KB directory and index (#1077): the
        # CLI reads the world's config.yaml through the real load_config,
        # pointed at the same files and index the env built.
        world = build_world(
            env["tmp_path"] / "world-cli",
            kbs=[KBConfig(name=KB, path=env["kb_path"], kb_type="generic")],
            settings={"index_path": env["db_path"]},
            rest=False,
        )
        try:
            args = ["update", entry_id, "-k", KB, "--format", "json"]
            for k, v in fields.items():
                args += ["-f", f"{k}={v}"]
            res = world.cli(*args)
        finally:
            world.close()
        assert res.exit_code == 0, res.output
        return json.loads(res.output[res.output.index("{") :])
    if surface in ("rest-put", "rest-patch"):
        from tests._world import build_world

        # The same world over REST: create_app's own get_db and lifespan,
        # no dependency overrides.
        world = build_world(
            env["tmp_path"] / f"world-{surface}",
            kbs=[KBConfig(name=KB, path=env["kb_path"], kb_type="generic")],
            settings={"index_path": env["db_path"]},
        )
        try:
            if surface == "rest-put":
                resp = world.client.put(f"/api/entries/{entry_id}", json={"kb": KB, **fields})
            else:
                ((field, value),) = fields.items()
                resp = world.client.patch(
                    f"/api/entries/{entry_id}",
                    json={"kb": KB, "field": field, "value": value},
                )
            assert resp.status_code == 200, resp.text
            return resp.json()
        finally:
            world.close()
    raise AssertionError(surface)


# --- the decided form: a respelled number is a no-change ----------------------


@pytest.mark.parametrize("surface", ["mcp", "rest-patch", "cli"])
@pytest.mark.parametrize("sent", [5, 5.0])
def test_a_named_update_that_only_respells_the_number_changes_nothing(env, surface, sent):
    """``5`` and ``5.0`` over the file's ``importance: 5.0`` are the same
    value (the maintainer's decision on #1704): the surface reports a
    no-change and the file is left byte-identical. It is not stored
    as ``5``."""
    res = _send(env, surface, "n1", {"importance": sent})
    assert res.get("changed") is False, res
    assert _file(env, "n1").read_text() == NOTE


@pytest.mark.parametrize("sent", [5, 5.0])
def test_rest_put_reports_a_respelled_number_unchanged_whichever_spelling(env, sent):
    """REST PUT is compared against the reading by the endpoint's own
    design (#569 item 1: every PUT goes through the echo comparison),
    and the two spellings no longer split there: ``5`` and ``5.0`` are
    both listed under ``unchanged``, with the file untouched."""
    res = _send(env, "rest-put", "n1", {"importance": sent})
    assert "importance" in res.get("unchanged", []), res
    assert res.get("changed") is False, res
    assert _file(env, "n1").read_text() == NOTE


@pytest.mark.parametrize("sent", [5, 5.0])
def test_a_named_update_that_only_respells_the_number_changes_nothing_in_process(env, sent):
    db = PyriteDB(env["db_path"])
    try:
        res = KBService(env["config"], db).update("n1", KB, {"importance": sent})
    finally:
        db.close()
    assert res.changed is False
    assert _file(env, "n1").read_text() == NOTE


def test_an_update_of_another_field_keeps_the_files_own_spelling(env):
    """The control: a field the update does not name keeps the file's
    bytes, ``5.0`` included."""
    res = _send(env, "mcp", "n1", {"title": "Renamed"})
    assert res.get("updated") is True, res
    assert _importance_line(env) == "importance: 5.0"


# --- echoes -------------------------------------------------------------------


def test_a_faithful_echo_reports_the_field_unchanged_and_keeps_its_spelling(env):
    """``kb_get`` reads the file's ``5.0`` as ``5``; sending the read back
    with the body edited lists ``importance`` under ``unchanged`` and the
    file keeps ``5.0`` byte for byte."""
    from pyrite.server.mcp_server import PyriteMCPServer

    server = PyriteMCPServer(env["config"], tier="admin")
    try:
        got = server._dispatch_tool("kb_get", {"kb_name": KB, "entry_id": "n1"})["entry"]
        assert got["importance"] == 5
        res = server._dispatch_tool(
            "kb_update", {**got, "kb_name": KB, "entry_id": "n1", "body": "edited"}
        )
    finally:
        server.close()
    assert res.get("updated") is True, res
    assert "importance" in res.get("unchanged", []), res
    assert _importance_line(env) == "importance: 5.0"


def test_an_echo_spelling_the_number_as_a_float_is_still_unchanged(env):
    """The same echo with ``importance`` spelled ``5.0`` -- not the
    reading's spelling -- is still the same value under the one
    comparison: it is listed under ``unchanged`` and the file keeps
    ``5.0`` byte for byte."""
    from pyrite.server.mcp_server import PyriteMCPServer

    server = PyriteMCPServer(env["config"], tier="admin")
    try:
        got = server._dispatch_tool("kb_get", {"kb_name": KB, "entry_id": "n1"})["entry"]
        res = server._dispatch_tool(
            "kb_update",
            {**got, "kb_name": KB, "entry_id": "n1", "body": "edited", "importance": 5.0},
        )
    finally:
        server.close()
    assert res.get("updated") is True, res
    assert "importance" in res.get("unchanged", []), res
    assert _importance_line(env) == "importance: 5.0"


# --- the temporal clause and .nan, end to end ---------------------------------


def test_a_date_sent_as_its_iso_text_is_not_a_change(env):
    db = PyriteDB(env["db_path"])
    try:
        res = KBService(env["config"], db).update("e1", KB, {"date": "2026-01-02"})
    finally:
        db.close()
    assert res.changed is False
    assert _file(env, "e1").read_text() == EVENT


def test_a_file_holding_nan_stays_updatable_and_a_nan_set_is_no_change(env):
    db = PyriteDB(env["db_path"])
    try:
        svc = KBService(env["config"], db)
        res = svc.update("n2", KB, {"title": "Renamed"})
        assert res.changed is True
        assert "ratio: .nan" in _file(env, "n2").read_text()
        res = svc.update("n2", KB, {"ratio": float("nan")})
        assert res.changed is False
    finally:
        db.close()


# --- apply, and the comparison itself -----------------------------------------

APPLY_TEXT = "---\ndate: 2026-01-02\nscore: 5.0\nscores: [5.0]\n---\n\nbody\n"


def _apply(text, op):
    return apply(text, [op], _frontmatter_span(text))


def test_apply_a_respelled_number_is_unchanged():
    new, report = _apply(APPLY_TEXT, Set("score", 5))
    assert report.results[0].outcome == "unchanged"
    assert new == APPLY_TEXT
    new, report = _apply(APPLY_TEXT, Set("score", 5.0))
    assert report.results[0].outcome == "unchanged"
    assert new == APPLY_TEXT
    # The control: a different number is still a change, stored as sent.
    new, report = _apply(APPLY_TEXT, Set("score", 6))
    assert report.results[0].outcome == "changed"
    assert "score: 6\n" in new


def test_apply_a_respelled_list_item_and_a_respelled_list_are_unchanged():
    new, report = _apply(APPLY_TEXT, Set("scores[0]", 5))
    assert report.results[0].outcome == "unchanged"
    assert new == APPLY_TEXT
    new, report = _apply(APPLY_TEXT, Set("scores", [5]))
    assert report.results[0].outcome == "unchanged"
    assert new == APPLY_TEXT
    # The control: a different item is still a change, stored as sent.
    new, report = _apply(APPLY_TEXT, Set("scores", [6]))
    assert report.results[0].outcome == "changed"
    assert "scores: [6]" in new


def test_apply_a_date_sent_as_its_iso_text_is_unchanged():
    new, report = _apply(APPLY_TEXT, Set("date", "2026-01-02"))
    assert report.results[0].outcome == "unchanged"
    assert new == APPLY_TEXT


def test_apply_a_yaml_only_timestamp_spelling_over_the_same_instant_is_unchanged():
    """A plain (undeclared) field holding a timestamp: spellings only
    the YAML loader reads (``+5``, a one-digit hour; #991) sent over
    the same instant are the same value under decision 3, so the file
    keeps its own spelling byte for byte. A different instant is
    still a change."""
    text = "---\nts: 2026-01-01 10:00:00+05:00\n---\n\nbody\n"
    new, report = _apply(text, Set("ts", "2026-01-01 10:00:00 +5"))
    assert report.results[0].outcome == "unchanged"
    assert new == text
    new, report = _apply(text, Set("ts", "2026-01-01 5:00:00 +0"))
    assert report.results[0].outcome == "unchanged"
    assert new == text
    # The control: a different instant is a change, stored as sent.
    new, report = _apply(text, Set("ts", "2026-01-01 6:00:00 +0"))
    assert report.results[0].outcome == "changed"
    assert new != text


def test_apply_add_subkey_with_a_respelled_value_is_a_no_change():
    """AddSubkey over a key the file holds with the same number in another
    spelling: under the one comparison the value is the same, so the
    operation is a no-change -- the file is left untouched and the
    "already has it; use set" refusal does not fire."""
    text = "---\nmetadata:\n  score: 5.0\n---\n\nbody\n"
    new, report = _apply(text, AddSubkey("metadata", "score", 5))
    assert report.results[0].outcome == "unchanged"
    assert new == text


def test_apply_add_subkey_with_a_different_value_is_refused_as_present():
    """The refusal is for a key that is present with a different value:
    AddSubkey naming ``score`` with ``6`` over the file's ``5.0`` is
    refused, as before."""
    text = "---\nmetadata:\n  score: 5.0\n---\n\nbody\n"
    with pytest.raises(OperationRefusedError, match="already has"):
        _apply(text, AddSubkey("metadata", "score", 6))


def test_the_comparison_itself():
    from datetime import UTC, date, datetime, timedelta, timezone

    nan = float("nan")
    # `values_identical` stays type-strict for the doubly spelled field
    # merge; it is not the update comparison.
    assert not values_identical(1, 1.0)
    assert not values_identical(True, 1)
    # The update comparison, decision 3's `values_equal`: a number in
    # another spelling is the same value (the maintainer's decision on
    # #1704), at any depth. A bool is not a number, and a string is not
    # a number. `.nan` is the same value as itself, at any depth.
    assert values_equal(5, 5.0)
    assert values_equal(5.0, 5)
    assert values_equal([5], [5.0])
    assert values_equal({"a": 5}, {"a": 5.0})
    assert not values_equal(5, 5.5)
    assert not values_equal(True, 1)
    assert not values_equal(True, 1.0)
    assert not values_equal("5", 5)
    assert values_equal(nan, nan)
    assert values_equal([nan], [nan])
    assert values_equal({"a": nan}, {"a": nan})
    assert values_equal(date(2026, 1, 2), "2026-01-02")
    assert values_equal("2026-01-02", date(2026, 1, 2))
    assert not values_equal(date(2026, 1, 2), "2026-01-03")
    assert values_equal(datetime(2026, 1, 2, 10, 0, tzinfo=UTC), "2026-01-02T10:00:00+00:00")
    assert values_equal({"d": date(2026, 1, 2)}, {"d": "2026-01-02"})
    # The temporal clause includes the spellings only the YAML loader
    # reads (`+5`, a one-digit hour; #991): the same instant in such a
    # spelling is the same value, a different instant is not.
    plus5 = timezone(timedelta(hours=5))
    held = datetime(2026, 1, 1, 10, 0, tzinfo=plus5)
    assert values_equal(held, "2026-01-01 10:00:00 +5")
    assert values_equal(held, "2026-01-01 5:00:00 +0")
    assert not values_equal(held, "2026-01-01 6:00:00 +0")


# --- the structural pin: there is one comparison ------------------------------

_SOURCES = {
    "schema": "pyrite/schema/field_aliases.py",
    "service": "pyrite/services/kb_service.py",
    "storage": "pyrite/storage/file_operations.py",
}


def _functions(tree: ast.Module) -> dict[str, list[ast.FunctionDef]]:
    found: dict[str, list[ast.FunctionDef]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            found.setdefault(node.name, []).append(node)
    return found


def _called_names(fn: ast.FunctionDef) -> set[str]:
    return {
        node.func.id
        for node in ast.walk(fn)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


def _violations(sources: dict[str, str]) -> list[str]:
    """Ways ``sources`` (role -> text) breaks the one-comparison rule."""
    problems: list[str] = []
    trees = {role: ast.parse(text) for role, text in sources.items()}
    funcs = {role: _functions(tree) for role, tree in trees.items()}

    for role, tree in trees.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_same_yaml_value":
                problems.append(f"{role}: a second same-value wrapper, _same_yaml_value")
            if isinstance(node, ast.Name) and node.id == "_same_yaml_value":
                problems.append(f"{role}: _same_yaml_value is still called")
            if isinstance(node, ast.FunctionDef) and node.name == "same_yaml_value":
                problems.append(f"{role}: a second same-value comparison, same_yaml_value")
            if isinstance(node, ast.Name) and node.id == "same_yaml_value":
                problems.append(f"{role}: same_yaml_value is still called")

    for name in ("split_echoed_update", "_validate_write"):
        calls = set().union(*(_called_names(fn) for fn in funcs["service"].get(name, [])))
        if "values_equal" not in calls:
            problems.append(f"service: {name} does not call values_equal")
        if "values_identical" in calls:
            problems.append(f"service: {name} still decides sameness with values_identical")

    storage = funcs["storage"]
    for name in ("_model_apply", "_set_node", "_plan", "_lcs"):
        calls = set().union(*(_called_names(fn) for fn in storage.get(name, [])))
        if "values_equal" not in calls:
            problems.append(f"storage: {name} does not call values_equal")
    return problems


def _real_sources() -> dict[str, str]:
    return {role: (ROOT / rel).read_text() for role, rel in _SOURCES.items()}


def test_one_comparison_decides_an_updates_no_change():
    assert _violations(_real_sources()) == []


def test_the_checker_finds_a_second_wrapper():
    sources = _real_sources()
    sources["service"] += "\n\ndef _same_yaml_value(a, b):\n    return True\n"
    assert any("_same_yaml_value" in p for p in _violations(sources))


def test_the_checker_finds_a_resurrected_second_comparison():
    sources = _real_sources()
    sources["schema"] += "\n\ndef same_yaml_value(a, b):\n    return True\n"
    assert any("same_yaml_value" in p for p in _violations(sources))


def test_the_checker_finds_the_unchanged_report_using_another_comparison():
    sources = _real_sources()
    sources["service"] = sources["service"].replace(
        "values_equal(reading[key], value)", "values_identical(reading[key], value)", 1
    )
    assert any("split_echoed_update" in p for p in _violations(sources))
