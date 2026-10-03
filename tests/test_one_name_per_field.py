"""#697 -- one name per field, on every surface that takes a field name.

What the schema publishes for ``event`` (``actors``) and ``relationship``
(``source_entity``, ``target_entity``) -- ``create --template``,
``to_agent_schema``, ``/api/entries/type-schemas`` -- is what create and update
accept, and it lands in the file key dev writes, never under ``metadata:``.
The old spelling (``participants``, ``source``, ``target``) lands in the same
key. A field name enters through:

====================  =====================================================
surface               entry point
====================  =====================================================
build_entry           ``build_entry(type, **kwargs)``
service               ``KBService.create`` / ``KBService.update``
CLI                   ``pyrite create -f k=v`` / ``pyrite update -f k=v``
MCP                   ``kb_create`` / ``kb_update`` arguments
REST create           ``POST /api/entries`` -- the ``participants`` field,
                      and ``metadata`` (what the web form sends)
REST update           ``PATCH /api/entries/{id}`` (field/value),
                      ``PUT /api/entries/{id}`` (``metadata``)
====================  =====================================================

The expected files are literals, written from what dev wrote for the name it
published (``participants`` on an event, ``source_entity`` on a relationship),
not computed by the code under test. Every real-service surface is entered; no
mocks stand between the name and the file.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.exceptions import ValidationError
from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.models.factory import build_entry
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager
from pyrite.utils.yaml import load_yaml

KB = "k"
ANY = object()  # a cell that pins the key and not the value's shape

EVENT_FILE = "---\nid: e1\ntitle: E1\ntype: event\ndate: '2025-01-01'\n---\n\nbody\n"
REL_FILE = (
    "---\nid: r1\ntitle: R1\ntype: relationship\nsource_entity: a\ntarget_entity: b\n---\n\nbody\n"
)
EXISTING = {
    "event": ("events/e1.md", "e1", EVENT_FILE),
    "relationship": ("relationships/r1.md", "r1", REL_FILE),
}

# (entry type, published or old name, value as a client sends it, comma form for -f).
# Expected: the file keys that must hold, as literals. `forbidden` keys must be absent.
CREATE_ROWS = [
    ("event", "actors", ["x", "y"], "x,y", {"actors": ["x", "y"]}),
    ("event", "participants", ["x", "y"], "x,y", {"actors": ["x", "y"]}),
    ("relationship", "source_entity", "x", "x", {"source_entity": "x", "target_entity": ""}),
    ("relationship", "target_entity", "x", "x", {"source_entity": "", "target_entity": "x"}),
    ("relationship", "source", "x", "x", {"source_entity": "x", "target_entity": ""}),
    ("relationship", "target", "x", "x", {"source_entity": "", "target_entity": "x"}),
]
UPDATE_ROWS = [
    ("event", "actors", ["x", "y"], "x,y", {"actors": ["x", "y"], "date": "2025-01-01"}),
    ("event", "participants", ["x", "y"], "x,y", {"actors": ["x", "y"], "date": "2025-01-01"}),
    ("relationship", "source_entity", "x", "x", {"source_entity": "x", "target_entity": "b"}),
    ("relationship", "target_entity", "x", "x", {"source_entity": "a", "target_entity": "x"}),
    ("relationship", "source", "x", "x", {"source_entity": "x", "target_entity": "b"}),
    ("relationship", "target", "x", "x", {"source_entity": "a", "target_entity": "x"}),
]
#: Keys that must never appear: the bag, and the old spelling beside the new.
FORBIDDEN = ("metadata", "participants", "source", "target")


#: (type, name) -> surfaces where dev already wrote that name to its file key.
#: Those cells are controls: they pin that the routing added for the new names
#: keeps what worked. Every other cell failed on dev (the name landed under
#: `metadata:`, or was refused), and is the evidence for the change.
_DEV_WORKED_CREATE = {
    ("event", "participants"): {"build_entry", "cli", "mcp", "rest_participants", "service"},
    ("relationship", "source_entity"): {"build_entry", "cli", "mcp", "service"},
    ("relationship", "target_entity"): {"build_entry", "cli", "mcp", "service"},
}
_DEV_WORKED_UPDATE = {
    ("event", "participants"): {"cli", "mcp", "rest_patch", "service"},
    ("relationship", "source_entity"): {"cli", "mcp", "rest_patch", "service"},
    ("relationship", "target_entity"): {"cli", "mcp", "rest_patch", "service"},
}


def _cells(rows, surfaces, worked):
    cells = []
    for entry_type, name, value, comma, expected in rows:
        for surface in surfaces:
            marks = []
            if surface in worked.get((entry_type, name), ()):
                marks.append(
                    pytest.mark.control(
                        reason=f"dev already wrote {name} on a {entry_type} to its file key "
                        f"through {surface}; this pins that the new routing keeps it"
                    )
                )
            cells.append(
                pytest.param(
                    surface, entry_type, name, value, comma, expected,
                    marks=marks, id=f"{surface}-{entry_type}-{name}",
                )
            )  # fmt: skip
    return cells


def _frontmatter(text: str) -> dict:
    return load_yaml(text.split("---")[1])


def _assert_lands(fm: dict, expected: dict, name: str) -> None:
    for key, value in expected.items():
        assert key in fm, (name, key, fm)
        if value is not ANY:
            assert fm[key] == value, (name, key, fm)
    for key in FORBIDDEN:
        # `participants`/`source`/`target` are only forbidden as file keys; the
        # published relationship keys contain them as prefixes, never equal.
        assert key not in fm, (name, key, "landed under a key nothing reads", fm)


def _env(tmp_path: Path, files: dict[str, str]) -> tuple[PyriteConfig, Path]:
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "kb.yaml").write_text("name: k\nkb_type: generic\n", encoding="utf-8")
    for name, text in files.items():
        path = kb / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    db_path = tmp_path / "index.db"
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name=KB, path=kb, kb_type=KBType.GENERIC)],
        settings=Settings(index_path=db_path),
    )
    db = PyriteDB(db_path)
    try:
        IndexManager(db, config).index_all()
    finally:
        db.close()
    return config, kb


def _written(kb: Path) -> dict:
    """The frontmatter of the new entry the write created (its id is `n`)."""
    found = list(kb.rglob("n.md"))
    assert len(found) == 1, f"expected one new file under {kb}, found {found}"
    return _frontmatter(found[0].read_text(encoding="utf-8"))


def _service(config: PyriteConfig, fn):
    db = PyriteDB(config.settings.index_path)
    try:
        return fn(KBService(config, db))
    finally:
        db.close()


def _mcp(config: PyriteConfig, tool: str, args: dict):
    from pyrite.server.mcp_server import PyriteMCPServer

    server = PyriteMCPServer(config, tier="write")
    try:
        return server._dispatch_tool(tool, args)
    finally:
        server.close()


def _rest(config: PyriteConfig, method: str, path: str, body: dict) -> int:
    return _rest_full(config, method, path, body)[0]


def _rest_full(config: PyriteConfig, method: str, path: str, body: dict) -> tuple[int, str]:
    pytest.importorskip("fastapi", reason="fastapi not installed")
    from starlette.testclient import TestClient

    from pyrite.server.api import create_app, get_config, get_db, get_index_worker
    from pyrite.services.index_worker import IndexWorker

    application = create_app(config=config)
    db = PyriteDB(config.settings.index_path)
    application.dependency_overrides[get_config] = lambda: config
    application.dependency_overrides[get_db] = lambda: db
    worker = IndexWorker(db, config)
    application.dependency_overrides[get_index_worker] = lambda: worker
    try:
        res = getattr(TestClient(application), method)(path, json=body)
        return res.status_code, res.text
    finally:
        worker.wait_for_idle(timeout=10)
        db.close()
        state_db = getattr(application.state, "pyrite_db", None)
        if state_db is not None and state_db is not db:
            state_db.close()


def _cli_raw(config: PyriteConfig, *args: str):
    with patch("pyrite.cli.context.load_config", return_value=config):
        return CliRunner().invoke(app, list(args))


def _cli(config: PyriteConfig, *args: str) -> int:
    result = _cli_raw(config, *args)
    assert result.exit_code == 0, result.output
    return result.exit_code


def _create_spec(entry_type: str, name: str, value) -> dict:
    spec = {"entry_type": entry_type, "title": "N", name: value}
    if entry_type == "event":
        spec["date"] = "2025-01-02"
    return spec


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

CREATE_SURFACES = ["build_entry", "service", "cli", "mcp", "rest_metadata", "rest_participants"]


@pytest.mark.parametrize(
    ("surface", "entry_type", "name", "value", "comma", "expected"),
    _cells(CREATE_ROWS, CREATE_SURFACES, _DEV_WORKED_CREATE),
)
def test_create_lands_the_name_in_its_file_key(
    tmp_path, surface, entry_type, name, value, comma, expected
):
    if surface == "rest_participants" and name != "participants":
        pytest.skip("POST /api/entries has one named list field, `participants`")
    config, kb = _env(tmp_path, {})
    spec = _create_spec(entry_type, name, value)

    if surface == "build_entry":
        kwargs = {k: v for k, v in spec.items() if k not in ("entry_type", "title")}
        text = build_entry(entry_type, title="N", **kwargs).to_markdown()
        _assert_lands(_frontmatter(text), expected, f"{surface}:{name}")
        return
    if surface == "service":
        _service(config, lambda svc: svc.create(KB, dict(spec)))
    elif surface == "cli":
        extra = ["-f", "date=2025-01-02"] if entry_type == "event" else []
        _cli(
            config,
            "create",
            "-k",
            KB,
            "-t",
            entry_type,
            "--title",
            "N",
            "-f",
            f"{name}={comma}",
            *extra,
        )
    elif surface == "mcp":
        res = _mcp(config, "kb_create", {"kb_name": KB, **spec})
        assert res.get("created") is True, res
    else:  # REST: the participants field, or the metadata bag the web form fills
        body = {"kb": KB, "entry_type": entry_type, "title": "N"}
        if entry_type == "event":
            body["date"] = "2025-01-02"
        if surface == "rest_participants":
            body["participants"] = value
        else:
            body["metadata"] = {name: value}
        assert _rest(config, "post", "/api/entries", body) == 200

    fm = _written(kb)
    # A REST body for a relationship also carries the model's `participants: []`
    # default; it is not a field of the type and is outside this table.
    if entry_type == "relationship" and surface.startswith("rest"):
        fm.pop("metadata", None)
        fm.pop("participants", None)
    _assert_lands(fm, expected, f"{surface}:{name}")


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------

UPDATE_SURFACES = ["service", "cli", "mcp", "rest_patch", "rest_put_metadata"]


@pytest.mark.parametrize(
    ("surface", "entry_type", "name", "value", "comma", "expected"),
    _cells(UPDATE_ROWS, UPDATE_SURFACES, _DEV_WORKED_UPDATE),
)
def test_update_lands_the_name_in_its_file_key(
    tmp_path, surface, entry_type, name, value, comma, expected
):
    rel, entry_id, text = EXISTING[entry_type]
    config, kb = _env(tmp_path, {rel: text})

    if surface == "service":
        _service(config, lambda svc: svc.update(entry_id, KB, {name: value}))
    elif surface == "cli":
        _cli(config, "update", entry_id, "-k", KB, "-f", f"{name}={comma}")
    elif surface == "mcp":
        res = _mcp(config, "kb_update", {"entry_id": entry_id, "kb_name": KB, name: value})
        assert res.get("updated") is True, res
    elif surface == "rest_patch":
        # PATCH carries one string `value`; a list field takes it as sent.
        sent = comma if entry_type == "relationship" else "x"
        assert (
            _rest(
                config,
                "patch",
                f"/api/entries/{entry_id}",
                {"kb": KB, "field": name, "value": sent},
            )
            == 200
        )
        if entry_type == "event":
            expected = {"actors": ANY, "date": "2025-01-01"}
    else:
        assert (
            _rest(config, "put", f"/api/entries/{entry_id}", {"kb": KB, "metadata": {name: value}})
            == 200
        )

    _assert_lands(
        _frontmatter((kb / rel).read_text(encoding="utf-8")), expected, f"{surface}:{name}"
    )


# ---------------------------------------------------------------------------
# What the schema publishes is what these accept
# ---------------------------------------------------------------------------


def test_every_published_name_of_a_changed_type_is_in_the_rows():
    """The rows above cover each field name the core schema publishes for the
    two types whose list this branch changed, so a new published name cannot be
    added without a row (and so a surface that ignores it)."""
    from pyrite.schema.core_types import CORE_TYPES

    covered = {name for t, name, *_ in CREATE_ROWS}
    for entry_type, aliased in (
        ("event", {"actors"}),
        ("relationship", {"source_entity", "target_entity"}),
    ):
        published = set(CORE_TYPES[entry_type]["fields"])
        assert aliased <= published
        assert aliased <= covered, (entry_type, aliased - covered)


# ---------------------------------------------------------------------------
# A request that names both spellings of one field (#720, ADR-0042 decision 6)
# ---------------------------------------------------------------------------
#
# ADR-0042 decision 6 refuses a *file* that carries a field and its alias. A
# *request* that does is refused the same way when the values differ -- the
# error names both keys and nothing is written -- and accepted when they are
# equal, writing one key. Before this, each surface picked a winner by the
# order it happened to lift keys in (build_entry kept the alias and dropped the
# explicit top-level value).

#: (type, alias, target, value, a different value, expected file keys for the equal value)
PAIRS = [
    ("event", "participants", "actors", ["x", "y"], ["z"], {"actors": ["x", "y"]}),
    (
        "relationship",
        "source",
        "source_entity",
        "x",
        "y",
        {"source_entity": "x", "target_entity": ""},
    ),
    (
        "relationship",
        "target",
        "target_entity",
        "x",
        "y",
        {"source_entity": "", "target_entity": "x"},
    ),
]
PAIR_IDS = ["participants+actors", "source+source_entity", "target+target_entity"]
CONFLICT_CREATE_SURFACES = [
    "build_entry",
    "service",
    "service_mixed",
    "cli",
    "mcp",
    "rest_metadata",
    "rest_participants",
]
CONFLICT_UPDATE_SURFACES = [
    "service",
    "service_mixed",
    "cli",
    "mcp",
    "rest_put_metadata",
    "rest_patch",
]


def _comma(v) -> str:
    return ",".join(v) if isinstance(v, list) else v


def _assert_refused_naming(text: str, alias: str, target: str, where: str) -> None:
    assert f"'{alias}'" in text and f"'{target}'" in text, (where, text)


def _conflict_create(tmp_path, surface, pair, equal):
    entry_type, alias, target, value, other, _ = pair
    config, kb = _env(tmp_path, {})
    second = value if equal else other
    spec = {"entry_type": entry_type, "title": "N"}
    if entry_type == "event":
        spec["date"] = "2025-01-02"
    # Returns (refusal text or None); the caller reads the files.
    if surface == "build_entry":
        kwargs = {k: v for k, v in spec.items() if k not in ("entry_type", "title")}
        try:
            entry = build_entry(entry_type, title="N", **kwargs, **{alias: value, target: second})
        except ValidationError as e:
            return config, kb, str(e), None
        return config, kb, None, _frontmatter(entry.to_markdown())
    if surface in ("service", "service_mixed"):
        if surface == "service":
            spec.update({alias: value, target: second})
        else:  # one spelling at the top level, the other in the metadata bag
            spec.update({alias: value, "metadata": {target: second}})
        try:
            _service(config, lambda svc: svc.create(KB, dict(spec)))
        except ValidationError as e:
            return config, kb, str(e), None
    elif surface == "cli":
        extra = ["-f", "date=2025-01-02"] if entry_type == "event" else []
        res = _cli_raw(
            config,
            "create", "-k", KB, "-t", entry_type, "--title", "N",
            "-f", f"{alias}={_comma(value)}", "-f", f"{target}={_comma(second)}",
            *extra,
        )  # fmt: skip
        if res.exit_code != 0:
            return config, kb, res.output, None
    elif surface == "mcp":
        res = _mcp(config, "kb_create", {"kb_name": KB, **spec, alias: value, target: second})
        if res.get("created") is not True:
            assert res["error_code"] == "VALIDATION_FAILED", res
            return config, kb, str(res), None
    else:
        body = {"kb": KB, "entry_type": entry_type, "title": "N"}
        if entry_type == "event":
            body["date"] = "2025-01-02"
        if surface == "rest_participants":
            body["participants"] = value
            body["metadata"] = {target: second}
        else:
            body["metadata"] = {alias: value, target: second}
        status, text = _rest_full(config, "post", "/api/entries", body)
        if status != 200:
            assert status == 400, (status, text)
            return config, kb, text, None
    return config, kb, None, _written(kb)


@pytest.mark.parametrize("surface", CONFLICT_CREATE_SURFACES)
@pytest.mark.parametrize("pair", PAIRS, ids=PAIR_IDS)
def test_create_naming_both_spellings_with_different_values_is_refused(tmp_path, surface, pair):
    entry_type, alias, target = pair[:3]
    if surface == "rest_participants" and alias != "participants":
        pytest.skip("POST /api/entries has one named list field, `participants`")
    config, kb, refusal, fm = _conflict_create(tmp_path, surface, pair, equal=False)
    assert refusal is not None, f"{surface} accepted both spellings with different values: {fm}"
    _assert_refused_naming(refusal, alias, target, surface)
    assert list(kb.rglob("n.md")) == [], "a refused create wrote a file"


@pytest.mark.parametrize("surface", CONFLICT_CREATE_SURFACES)
@pytest.mark.parametrize("pair", PAIRS, ids=PAIR_IDS)
def test_create_naming_both_spellings_with_equal_values_writes_one_key(tmp_path, surface, pair):
    alias = pair[1]
    if surface == "rest_participants" and alias != "participants":
        pytest.skip("POST /api/entries has one named list field, `participants`")
    config, kb, refusal, fm = _conflict_create(tmp_path, surface, pair, equal=True)
    assert refusal is None, refusal
    if pair[0] == "relationship" and surface.startswith("rest"):
        fm.pop("metadata", None)
        fm.pop("participants", None)
    _assert_lands(fm, pair[5], surface)


def _conflict_update(tmp_path, surface, pair, equal):
    entry_type, alias, target, value, other, _ = pair
    rel, entry_id, text = EXISTING[entry_type]
    config, kb = _env(tmp_path, {rel: text})
    second = value if equal else other
    refusal = None
    if surface in ("service", "service_mixed"):
        if surface == "service":
            updates = {alias: value, target: second}
        else:
            updates = {alias: value, "metadata": {target: second}}
        try:
            _service(config, lambda svc: svc.update(entry_id, KB, updates))
        except ValidationError as e:
            refusal = str(e)
    elif surface == "cli":
        res = _cli_raw(
            config, "update", entry_id, "-k", KB,
            "-f", f"{alias}={_comma(value)}", "-f", f"{target}={_comma(second)}",
        )  # fmt: skip
        if res.exit_code != 0:
            refusal = res.output
    elif surface == "mcp":
        res = _mcp(
            config, "kb_update", {"entry_id": entry_id, "kb_name": KB, alias: value, target: second}
        )
        if res.get("updated") is not True:
            assert res["error_code"] == "VALIDATION_FAILED", res
            refusal = str(res)
    elif surface == "rest_put_metadata":
        status, body = _rest_full(
            config,
            "put",
            f"/api/entries/{entry_id}",
            {"kb": KB, "metadata": {alias: value, target: second}},
        )
        if status != 200:
            assert status == 400, (status, body)
            refusal = body
    else:  # PATCH carries one field: a second spelling cannot be named in the request
        pytest.skip("PATCH /api/entries/{id} names one field per request")
    return kb / rel, text, refusal


@pytest.mark.parametrize("surface", CONFLICT_UPDATE_SURFACES)
@pytest.mark.parametrize("pair", PAIRS, ids=PAIR_IDS)
def test_update_naming_both_spellings_with_different_values_is_refused(tmp_path, surface, pair):
    path, before, refusal = _conflict_update(tmp_path, surface, pair, equal=False)
    assert refusal is not None, f"{surface} accepted both spellings with different values"
    _assert_refused_naming(refusal, pair[1], pair[2], surface)
    assert path.read_text(encoding="utf-8") == before, "a refused update changed the file"


@pytest.mark.parametrize("surface", CONFLICT_UPDATE_SURFACES)
@pytest.mark.parametrize("pair", PAIRS, ids=PAIR_IDS)
def test_update_naming_both_spellings_with_equal_values_writes_one_key(tmp_path, surface, pair):
    path, _, refusal = _conflict_update(tmp_path, surface, pair, equal=True)
    assert refusal is None, refusal
    expected = {"actors": ["x", "y"], "date": "2025-01-01"}
    if pair[0] == "relationship":
        expected = {"source_entity": "a", "target_entity": "b"}
        expected[pair[2]] = "x"
    _assert_lands(_frontmatter(path.read_text(encoding="utf-8")), expected, surface)


def test_put_metadata_source_does_not_overwrite_a_source_entity_it_contradicts(tmp_path):
    """The reviewer's PUT case (#701): ``metadata.source`` replaced ``source_entity``."""
    rel, entry_id, text = EXISTING["relationship"]
    config, kb = _env(tmp_path, {rel: text})
    status, body = _rest_full(
        config,
        "put",
        f"/api/entries/{entry_id}",
        {"kb": KB, "metadata": {"source": "wiki", "source_entity": "wiki"}},
    )
    assert status == 200, body  # both spellings, one value: accepted, one key
    status, body = _rest_full(
        config,
        "put",
        f"/api/entries/{entry_id}",
        {"kb": KB, "metadata": {"source": "wiki", "source_entity": "other"}},
    )
    assert status == 400, body
    _assert_refused_naming(body, "source", "source_entity", "put")
    assert _frontmatter((kb / rel).read_text(encoding="utf-8"))["source_entity"] == "wiki"
