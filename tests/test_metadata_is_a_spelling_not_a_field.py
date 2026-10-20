"""`metadata` is not a field: `metadata.k` is a second spelling of field `k` (Andon #994, decision A).

The maintainer's decision, 2026-10-17: ``metadata.k``, in a file or a request,
is a second spelling of field ``k``, one level only, the same as an alias. A
field has one spelling per file. A file that spells a field twice is reported,
and an update of that field is refused. A path whose field would be
``metadata`` is refused, at any depth.

One enforcement point: ``field_at(type, path) -> (field, rest)`` and
``spelling_in(file, type, field) -> path`` in ``pyrite/schema/field_aliases.py``.
Every refusal an update makes is decided on ``field_at`` of the operation's
output path, on every surface.

The grid below is generated from the code, not hand-written: the entry
classes the registry knows x their managed fields x the spellings
``spellings_of`` generates (each wrapped in ``metadata.`` up to depth 3) x the
operation classes x the surfaces. Its oracle checks the effect, not a message:
the request is refused, or every managed field reads back unchanged in both
the model (the file reloaded) and the index row.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from pyrite.cli import app as cli_app
from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.exceptions import PyriteError, ValidationError
from pyrite.models.core_types import ENTRY_TYPE_REGISTRY, get_entry_class
from pyrite.services.kb_service import _MANAGED_FIELDS, KBService
from pyrite.storage.database import PyriteDB
from pyrite.storage.file_operations import Append, Remove, Set, Unset
from pyrite.storage.index import IndexManager
from pyrite.storage.repository import KBRepository

KB = "grid"


# --------------------------------------------------------------------------
# The rule, as functions
# --------------------------------------------------------------------------


def test_field_at_folds_one_metadata_level_and_an_alias():
    from pyrite.schema.field_aliases import field_at

    note = get_entry_class("note")
    assert field_at(note, "note", "evidence") == ("evidence", ())
    assert field_at(note, "note", "metadata.evidence") == ("evidence", ())
    assert field_at(note, "note", "links[0].relation") == ("links", (0, "relation"))
    assert field_at(note, "note", ("metadata", "links", 0, "x")) == ("links", (0, "x"))
    event = get_entry_class("event")
    assert field_at(event, "event", "participants") == ("actors", ())
    assert field_at(event, "event", "metadata.participants") == ("actors", ())
    # a sub-key named `metadata` inside a field's value is that field's
    assert field_at(note, "note", "extra.metadata") == ("extra", ("metadata",))


@pytest.mark.parametrize(
    "path",
    ["metadata", "metadata.metadata", "metadata.metadata.evidence", ("metadata",), ("metadata", 0)],
)
def test_a_path_whose_field_would_be_metadata_is_not_a_field(path: Any):
    from pyrite.schema.field_aliases import NotAFieldError, field_at

    with pytest.raises(NotAFieldError):
        field_at(get_entry_class("note"), "note", path)


def test_spellings_of_unfolds_what_field_at_folds():
    from pyrite.schema.field_aliases import field_at, spellings_of

    event = get_entry_class("event")
    spellings = spellings_of(event, "event", "actors")
    assert spellings == [
        ("actors",),
        ("participants",),
        ("metadata", "actors"),
        ("metadata", "participants"),
    ]
    assert all(field_at(event, "event", p) == ("actors", ()) for p in spellings)


def test_spelling_in_is_the_files_own_spelling_or_the_fields_name():
    from pyrite.schema.field_aliases import SpelledTwiceError, spelling_in

    event = get_entry_class("event")
    assert spelling_in({"participants": ["a"]}, event, "event", "actors") == ("participants",)
    assert spelling_in({"metadata": {"actors": ["a"]}}, event, "event", "actors") == (
        "metadata",
        "actors",
    )
    assert spelling_in({"title": "x"}, event, "event", "actors") == ("actors",)
    with pytest.raises(SpelledTwiceError) as info:
        spelling_in({"actors": ["a"], "metadata": {"actors": ["b"]}}, event, "event", "actors")
    assert info.value.spellings == [("actors",), ("metadata", "actors")]
    with pytest.raises(SpelledTwiceError):
        spelling_in({"actors": ["a"], "participants": ["b"]}, event, "event", "actors")


def test_double_spellings_lists_every_field_spelled_twice():
    from pyrite.schema.field_aliases import double_spellings

    task = get_entry_class("task")
    found = double_spellings(
        {"evidence": ["real"], "rank": 1, "metadata": {"evidence": ["fake"], "rank": 2, "ok": 3}},
        task,
        "task",
    )
    assert found == {
        "evidence": [("evidence",), ("metadata", "evidence")],
        "rank": [("rank",), ("metadata", "rank")],
    }


# --------------------------------------------------------------------------
# The grid: entry classes x managed fields x spellings x operations x surfaces
# --------------------------------------------------------------------------

#: A file per class, with the class's own managed fields present where it has
#: them, so "unchanged" means something. Values are plain scalars and lists
#: the model reads back as they are.
_FILES: dict[str, str] = {
    "note": "---\nid: note-1\ntitle: Note\ntype: note\nextra: 1\n---\n\nText.\n",
    "task": (
        "---\nid: task-1\ntitle: Task\ntype: task\nstatus: open\nevidence: [e0]\n"
        "status_change_log:\n- status: open\n  by: me\nagent_context: {run: 1}\n"
        "assigned_at: 2026-01-01T00:00:00Z\n---\n\nText.\n"
    ),
    "adr": "---\nid: adr-1\ntitle: Adr\ntype: adr\nadr_number: 7\nstatus: proposed\n---\n\nText.\n",
}


def _classes() -> dict[str, type]:
    """The entry classes the grid walks: every registered class keyed by one
    type name each (the base classes once), plus the software-kb plugin's
    ``adr`` (its own managed field) when the plugin imports."""
    classes: dict[str, type] = {}
    for name, cls in ENTRY_TYPE_REGISTRY.items():
        if cls not in classes.values():
            classes[name] = cls
    try:
        from pyrite_software_kb.entry_types import ADREntry  # type: ignore[import-not-found]

        classes["adr"] = ADREntry
    except ImportError:
        pass
    return classes


def _managed(cls: type) -> list[str]:
    """``_MANAGED_FIELDS`` and the class's own, minus ``links``/``sources``
    (a whole item may be appended to or removed from them by design; the
    item-operation tests cover that exemption)."""
    own = frozenset(getattr(cls, "managed_fields", frozenset()))
    return sorted((_MANAGED_FIELDS | own) - {"links", "sources"})


def _wrapped(path: tuple[str, ...], depth: int) -> tuple[str, ...]:
    return ("metadata",) * depth + path


def _cells() -> list[Any]:
    """The grid's cells, generated: the base managed fields on one class
    (``note``; the base rule does not depend on the class), each class's own
    managed fields on that class; every spelling ``spellings_of`` gives,
    wrapped in ``metadata.`` to depth 3; the four operation classes; the
    surfaces that can send the spelling (REST PUT only the ``metadata``
    forms, as a nested mapping; it has no other path form)."""
    try:
        from pyrite.schema.field_aliases import spellings_of
    except ImportError:
        # Before the fix the module these cells prove has no `spellings_of`;
        # the grid is still generated, from the alias map the base already
        # has, so the cells collect and go red for what they test (a write
        # that reaches the file) rather than for a missing name.
        # `test_spellings_of_unfolds_what_field_at_folds` pins that the two
        # generators agree.
        from pyrite.schema.field_aliases import class_field_aliases

        def spellings_of(cls: type, entry_type: str, field: str) -> list[tuple[str, ...]]:
            aliases = class_field_aliases(cls, entry_type)
            names = [field, *(a for a, t in aliases.items() if t == field and a != field)]
            return [(n,) for n in names] + [("metadata", n) for n in names]

    cells: list[Any] = []
    seen: set[Any] = set()
    base_done = False
    for type_name, cls in sorted(_classes().items()):
        own = frozenset(getattr(cls, "managed_fields", frozenset()))
        fields = [f for f in _managed(cls) if f in own]
        if not base_done and type_name == "note":
            fields = _managed(cls)
            base_done = True
        if not fields or type_name not in _FILES:
            continue
        for field in fields:
            for spelling in spellings_of(cls, type_name, field):
                for depth in range(0, 3):
                    path = _wrapped(spelling, depth)
                    # `metadata.k` wrapped once is `k` wrapped twice: one cell.
                    if len(path) > 3 or (type_name, path) in seen:
                        continue
                    seen.add((type_name, path))
                    for op in (Set, Append, Remove, Unset):
                        for surface in ("service", "mcp", "rest.patch", "cli"):
                            cells.append((type_name, field, path, op, surface))
                        if op is Set and path[0] == "metadata":
                            cells.append((type_name, field, path, op, "rest.put"))
            # The `metadata` envelope (`metadata: {key: value}` on the
            # service, REST PUT and MCP) with the whole spelling inside ONE
            # key: `k`, `k[0]`, `k[0].x`, `metadata.k`, `metadata.metadata.k`
            # (#1001 review: the key was read as a literal field name by the
            # refusal and as a path by `apply()`). A cell's path is the
            # one-element tuple holding that key.
            for spelling in spellings_of(cls, type_name, field):
                base_key = _dotted(spelling)
                for key in (
                    base_key,
                    f"{base_key}[0]",
                    f"{base_key}[0].x",
                    f"metadata.{base_key}",
                    f"metadata.metadata.{base_key}",
                ):
                    if (type_name, ("envelope", key)) in seen:
                        continue
                    seen.add((type_name, ("envelope", key)))
                    for surface in ("service.envelope", "rest.put.envelope", "mcp.envelope"):
                        cells.append((type_name, field, (key,), Set, surface))
    return cells


def _cell_id(cell: Any) -> str:
    type_name, _field, path, op, surface = cell
    return f"{type_name}:{'.'.join(path)}:{op.__name__.lower()}:{surface}"


_KBS: dict[str, tuple[PyriteConfig, Path]] = {}


def _kb_for(type_name: str, tmp_path_factory: pytest.TempPathFactory) -> tuple[PyriteConfig, Path]:
    """One KB per class per worker, built once; every cell snapshots the
    file before it runs, so a cell that wrongly writes fails on its own."""
    if type_name not in _KBS:
        root = tmp_path_factory.mktemp(f"grid-{type_name}")
        kb_path = root / "kb"
        (kb_path / "entries").mkdir(parents=True)
        subprocess.run(
            ["git", "init", "-q"], cwd=kb_path, check=True, capture_output=True, timeout=30
        )
        path = kb_path / "entries" / f"{type_name}-1.md"
        path.write_bytes(_FILES[type_name].encode("utf-8"))
        kb_type = "software" if type_name == "adr" else "generic"
        config = PyriteConfig(
            knowledge_bases=[KBConfig(name=KB, path=kb_path, kb_type=kb_type)],
            settings=Settings(index_path=root / "index.db", auto_embed=False),
        )
        if type_name == "adr":
            from pyrite.plugins.registry import PluginRegistry
            from pyrite_software_kb.plugin import SoftwareKBPlugin

            PluginRegistry().register(SoftwareKBPlugin())
        db = PyriteDB(config.settings.index_path)
        try:
            IndexManager(db, config).index_all()
        finally:
            db.close()
        _KBS[type_name] = (config, path)
    return _KBS[type_name]


def _dotted(path: tuple[str, ...]) -> str:
    return ".".join(path)


def _nested(path: tuple[str, ...], value: Any) -> dict[str, Any]:
    out: Any = value
    for seg in reversed(path):
        out = {seg: out}
    return out


def _send(
    surface: str, config: PyriteConfig, entry_id: str, op: type, path: tuple[str, ...]
) -> Any:
    """One operation through one surface; raises ``PyriteError`` on a refusal."""
    value: Any = "x"
    dotted = _dotted(path)
    if surface.endswith(".envelope"):
        # `metadata: {key: value}`, the key carrying the whole spelling.
        envelope = {"metadata": {path[0]: value}}
        if surface == "service.envelope":
            db = PyriteDB(config.settings.index_path)
            try:
                return KBService(config, db).update(entry_id, KB, envelope)
            finally:
                db.close()
        if surface == "mcp.envelope":
            from pyrite.server.mcp_server import PyriteMCPServer

            server = PyriteMCPServer(config, tier="write")
            try:
                result = server._dispatch_tool(
                    "kb_update", {"kb_name": KB, "entry_id": entry_id, **envelope}
                )
            finally:
                server.close()
            if result.get("error") or result.get("isError"):
                raise ValidationError(str(result))
            return result
        surface, path = "rest.put", ("metadata", path[0])
    if surface == "service":
        db = PyriteDB(config.settings.index_path)
        try:
            svc = KBService(config, db)
            if op is Set:
                return svc.update(entry_id, KB, {dotted: value})
            if op is Append:
                return svc.update(entry_id, KB, {}, append={dotted: value})
            if op is Remove:
                return svc.update(entry_id, KB, {}, remove={dotted: value})
            return svc.update(entry_id, KB, {}, unset=[dotted])
        finally:
            db.close()
    if surface == "mcp":
        from pyrite.server.mcp_server import PyriteMCPServer

        args: dict[str, Any] = {"kb_name": KB, "entry_id": entry_id}
        if op is Set:
            args[dotted] = value
        elif op is Append:
            args["append"] = {dotted: value}
        elif op is Remove:
            args["remove"] = {dotted: value}
        else:
            args["unset"] = [dotted]
        server = PyriteMCPServer(config, tier="write")
        try:
            result = server._dispatch_tool("kb_update", args)
        finally:
            server.close()
        if result.get("error") or result.get("isError"):
            raise ValidationError(str(result))
        return result
    if surface in ("rest.patch", "rest.put"):
        pytest.importorskip("fastapi", reason="fastapi not installed")
        from fastapi.testclient import TestClient

        from pyrite.server.api import create_app, get_config, get_db, get_index_mgr

        db = PyriteDB(config.settings.index_path)
        index_mgr = IndexManager(db, config)
        rest = create_app(config)
        rest.dependency_overrides[get_config] = lambda: config
        rest.dependency_overrides[get_db] = lambda: db
        rest.dependency_overrides[get_index_mgr] = lambda: index_mgr
        client = TestClient(rest, raise_server_exceptions=False)
        try:
            if surface == "rest.put":
                res = client.put(
                    f"/api/entries/{entry_id}", json={"kb": KB, **_nested(path, value)}
                )
            else:
                body: dict[str, Any] = {"kb": KB, "field": dotted}
                if op is Set:
                    body["value"] = value
                elif op is Append:
                    body.update(value=value, op="append")
                elif op is Remove:
                    body.update(value=value, op="remove")
                else:
                    body["op"] = "unset"
                res = client.patch(f"/api/entries/{entry_id}", json=body)
        finally:
            client.close()
            db.close()
        if res.status_code != 200:
            raise ValidationError(res.text)
        assert res.status_code != 500, res.text
        return res.json()
    if surface == "cli":
        args = ["update", entry_id, "-k", KB, "--format", "json"]
        if op is Set:
            args += ["-f", f"{dotted}={value}"]
        elif op is Append:
            args += ["--append", f"{dotted}={value}"]
        elif op is Remove:
            args += ["--remove", f"{dotted}={value}"]
        else:
            args += ["--unset", dotted]
        with (
            patch("pyrite.cli.load_config", return_value=config),
            patch("pyrite.cli.context.load_config", return_value=config),
        ):
            result = CliRunner().invoke(cli_app, args)
        if result.exit_code != 0:
            raise ValidationError(result.output)
        return result.output
    raise AssertionError(surface)


def _managed_values(
    config: PyriteConfig, type_name: str, entry_id: str, cls: type
) -> dict[str, Any]:
    """Every managed field of the entry, as the model reads the file and as
    the index row holds it (its column, and its `metadata` JSON)."""
    entry = KBRepository(config.knowledge_bases[0]).load(entry_id)
    assert entry is not None, "the entry's file vanished"
    db = PyriteDB(config.settings.index_path)
    try:
        row = db.get_entry(entry_id, KB) or {}
    finally:
        db.close()
    out: dict[str, Any] = {}
    row_meta = row.get("metadata") or {}
    if isinstance(row_meta, str):
        row_meta = json.loads(row_meta)
    for field in _managed(cls):
        out[f"model.{field}"] = json.dumps(getattr(entry, field, None), sort_keys=True, default=str)
        out[f"model.metadata.{field}"] = json.dumps(
            (entry.metadata or {}).get(field), sort_keys=True, default=str
        )
        out[f"row.{field}"] = json.dumps(row.get(field), sort_keys=True, default=str)
        out[f"row.metadata.{field}"] = json.dumps(row_meta.get(field), sort_keys=True, default=str)
    return out


def _control_reason(cell: Any) -> str | None:
    """Why a cell is a deliberate control on 939910c (#973 round 1's head)
    rather than evidence: the grid states the invariant for every spelling,
    and most spellings were already refused there. ``None`` is a cell that
    wrote on 939910c (the `metadata.metadata.k` form, set or append: the
    one-level strip left a nested key the refusal never saw)."""
    _type_name, _field, path, op, surface = cell
    if surface.endswith(".envelope"):
        if "." not in path[0] and "[" not in path[0]:
            return (
                "a plain field name inside the `metadata` envelope was refused on be6bd08 "
                "and 939910c (the one-level fold); the keys carrying `.` or `[i]` are the "
                "evidence (#1001 review)"
            )
        if "[" in path[0]:
            # `apply()` refuses an index into a value that is not a list of
            # that shape (a scalar, a mapping, an absent key, the absent
            # `metadata:` block the `metadata.`-prefixed keys walk into), so
            # most of these were refused on 939910c for the wrong reason; the
            # label explains that pass. Where the fixture does hold the list
            # (`status_change_log[0]`, `status_change_log[0].x`, `evidence[0]`)
            # the cell wrote on 939910c and is red whatever the label says
            # (verify-red reports a failing control as red).
            return (
                "an index into a value the fixture does not hold as a list of that shape "
                "was refused by apply()'s shape check on 939910c, not by the managed-field "
                "rule; the cells whose field is such a list are red regardless of this label"
            )
        return None
    depth = 0
    while depth < len(path) and path[depth] == "metadata":
        depth += 1
    if depth < 2:
        return (
            "the `k` and `metadata.k` spellings were refused on 939910c by the first-segment "
            "check and the one-level strip (round 1's hand grid); the generated grid states "
            "the invariant for every spelling"
        )
    if op in (Remove, Unset):
        return (
            "nothing sat at that nested key on 939910c, so a remove or unset of it wrote "
            "nothing; the fix refuses it, for the right reason"
        )
    return None


_CELLS = _cells()
_PARAMS = [
    pytest.param(c, marks=pytest.mark.control(reason=reason), id=_cell_id(c))
    if (reason := _control_reason(c))
    else pytest.param(c, id=_cell_id(c))
    for c in _CELLS
]


@pytest.mark.parametrize("cell", _PARAMS)
def test_no_spelling_of_a_managed_field_reaches_the_file_or_the_index(
    cell: Any, tmp_path_factory: pytest.TempPathFactory
):
    type_name, field, path, op, surface = cell
    cls = _classes()[type_name]
    config, file_path = _kb_for(type_name, tmp_path_factory)
    entry_id = f"{type_name}-1"
    before_bytes = file_path.read_bytes()
    before = _managed_values(config, type_name, entry_id, cls)

    try:
        try:
            _send(surface, config, entry_id, op, path)
        except PyriteError:
            assert file_path.read_bytes() == before_bytes, "refused, yet the file changed"
            return

        # Not refused (MCP sets a bare managed key aside, #455; a remove of
        # an absent item is a no-op): then nothing may have reached the file
        # -- a managed field has no spelling a write may land at, nested or
        # not -- and nothing managed may have moved, in the model or the row.
        assert file_path.read_bytes() == before_bytes, "not refused, and the file changed"
        after = _managed_values(config, type_name, entry_id, cls)
        assert after == before, {k: (before[k], after[k]) for k in before if before[k] != after[k]}
    finally:
        # A cell that wrote (the defect) must not hand the next cell a file
        # that already holds its value, where the same write is a no-op and
        # the cell passes for nothing: every cell starts from the same file.
        if file_path.read_bytes() != before_bytes:
            file_path.write_bytes(before_bytes)
            db = PyriteDB(config.settings.index_path)
            try:
                IndexManager(db, config).index_kb(KB)
            finally:
                db.close()


# --------------------------------------------------------------------------
# A field spelled twice: reader agreement, the report, the refusal
# --------------------------------------------------------------------------

TWICE = (
    "---\nid: t2\ntitle: Twice\ntype: task\nstatus: open\nevidence: [real]\n"
    "metadata:\n  evidence: [fake]\n  rank: 2\nrank: 1\n---\n\nText.\n"
)


def _twice_kb(tmp_path: Path) -> tuple[PyriteConfig, Path]:
    kb_path = tmp_path / "kb"
    (kb_path / "tasks").mkdir(parents=True)
    path = kb_path / "tasks" / "t2.md"
    path.write_text(TWICE, encoding="utf-8")
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name=KB, path=kb_path, kb_type="generic")],
        settings=Settings(index_path=tmp_path / "index.db", auto_embed=False),
    )
    db = PyriteDB(config.settings.index_path)
    try:
        IndexManager(db, config).index_all()
    finally:
        db.close()
    return config, path


def test_the_model_and_the_index_row_agree_on_a_field_spelled_twice(tmp_path: Path):
    """The reader must agree with itself (#994): for `evidence: [real]` and
    `metadata: {evidence: [fake]}` the model reads `[real]`; the row said
    `[fake]`, because the entry's metadata bag overrode the model's reading
    in the row's `metadata` JSON. The model's reading wins in both."""
    config, _path = _twice_kb(tmp_path)
    entry = KBRepository(config.knowledge_bases[0]).load("t2")
    assert entry is not None and entry.evidence == ["real"]
    db = PyriteDB(config.settings.index_path)
    try:
        row = db.get_entry("t2", KB)
    finally:
        db.close()
    assert row is not None
    assert row["metadata"].get("evidence") == ["real"], row["metadata"]
    assert row["metadata"].get("rank") == 1, row["metadata"]


def test_a_field_spelled_twice_is_reported_by_health_and_validate(tmp_path: Path):
    from pyrite.services.access_policy import UNSCOPED
    from pyrite.services.qa_service import QAService

    config, _path = _twice_kb(tmp_path)
    db = PyriteDB(config.settings.index_path)
    try:
        health = IndexManager(db, config).check_health(KB)
        report = QAService(config, db).validate_kb(KB, readable_kbs=UNSCOPED)
    finally:
        db.close()
    rows = sorted(health["double_spellings"], key=lambda r: r["field"])
    assert [(r["id"], r["field"], r["spellings"]) for r in rows] == [
        ("t2", "evidence", ["evidence", "metadata.evidence"]),
        ("t2", "rank", ["rank", "metadata.rank"]),
    ]
    issues = sorted(
        (i for i in report["issues"] if i.get("rule") == "field_spelled_twice"),
        key=lambda i: i["field"],
    )
    assert [(i["entry_id"], i["field"], i["severity"]) for i in issues] == [
        ("t2", "evidence", "warning"),
        ("t2", "rank", "warning"),
    ]


@pytest.mark.parametrize("surface", ["service", "mcp", "rest.patch", "cli"])
def test_an_update_of_a_field_spelled_twice_is_refused_on_every_surface(
    tmp_path: Path, surface: str
):
    """`rank` is no managed field, so the only thing that refuses it is the
    double spelling: the update is refused, nothing written, whichever
    spelling the request uses."""
    config, path = _twice_kb(tmp_path)
    before = path.read_bytes()
    for spelling in (("rank",), ("metadata", "rank")):
        with pytest.raises(PyriteError, match="twice"):
            _send(surface, config, "t2", Set, spelling)
    assert path.read_bytes() == before


def test_an_in_process_write_to_a_field_spelled_twice_is_refused_too(tmp_path: Path):
    """`apply_operations` (no `restrict`) may write managed fields, but never
    one the file spells twice: it could not know which spelling the reader
    would believe."""
    config, path = _twice_kb(tmp_path)
    before = path.read_bytes()
    db = PyriteDB(config.settings.index_path)
    try:
        with pytest.raises(ValidationError, match="twice"):
            KBService(config, db).apply_operations("t2", KB, [Append("evidence", "e1")])
    finally:
        db.close()
    assert path.read_bytes() == before


@pytest.mark.control(
    reason="939910c's one-level strip already edited a key the file held only under "
    "`metadata:` there; the statement is kept so `spelling_in` cannot lose it"
)
def test_a_field_spelled_once_under_metadata_is_edited_there(tmp_path: Path):
    """The other half of the rule: a file that spells `rank` only as
    `metadata.rank` is edited at that spelling, whichever the request used,
    and no top-level `rank:` appears."""
    text = "---\nid: n3\ntitle: N3\ntype: note\nmetadata:\n  rank: 2\n---\n\nText.\n"
    kb_path = tmp_path / "kb"
    (kb_path / "notes").mkdir(parents=True)
    path = kb_path / "notes" / "n3.md"
    path.write_text(text, encoding="utf-8")
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name=KB, path=kb_path, kb_type="generic")],
        settings=Settings(index_path=tmp_path / "index.db", auto_embed=False),
    )
    db = PyriteDB(config.settings.index_path)
    try:
        IndexManager(db, config).index_all()
        KBService(config, db).update("n3", KB, {"rank": 5})
    finally:
        db.close()
    after = path.read_text(encoding="utf-8")
    assert "metadata:\n  rank: 5\n" in after, after
    assert "\nrank:" not in after, after


def test_create_places_a_declared_field_sent_under_metadata_at_its_own_key(tmp_path: Path):
    """The create request's `metadata` envelope (REST, MCP, the web form)
    is a spelling too: a declared field sent as `metadata.k` lands at `k`.
    939910c nested it and emitted the field's default at the top level, so
    Pyrite itself wrote a file spelling `source_type` twice (`folder` on top,
    `query` nested) and the row's bag-wins merge hid it; with the model's
    reading winning, every stored-query collection read as a folder."""
    kb_path = tmp_path / "kb"
    (kb_path / "entries").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=kb_path, check=True, capture_output=True, timeout=30)
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name=KB, path=kb_path, kb_type="generic")],
        settings=Settings(index_path=tmp_path / "index.db", auto_embed=False),
    )
    db = PyriteDB(config.settings.index_path)
    try:
        IndexManager(db, config).index_all()
        svc = KBService(config, db)
        svc.create_entry(
            kb_name=KB,
            entry_id="c1",
            entry_type="collection",
            title="C",
            body="",
            metadata={"source_type": "query", "query": "type:note", "colour": "red"},
        )
        entry = KBRepository(config.knowledge_bases[0]).load("c1")
        row = db.get_entry("c1", KB)
    finally:
        db.close()
    assert entry is not None and row is not None
    text = next(kb_path.rglob("c1.md")).read_text(encoding="utf-8")
    assert "\nsource_type: query\n" in text and "\nquery: type:note\n" in text, text
    assert "\n  source_type:" not in text and "\n  query:" not in text, text
    # an undeclared key keeps create's nested placement (#178), spelled once
    assert "metadata:\n  colour: red\n" in text, text
    assert (entry.source_type, entry.query) == ("query", "type:note")
    assert (row["metadata"].get("source_type"), row["metadata"].get("query")) == (
        "query",
        "type:note",
    )
    from pyrite.schema.field_aliases import double_spellings

    assert double_spellings(entry._source_frontmatter, type(entry), "collection") == {}


def test_a_top_level_aliases_key_resolves_a_wikilink(tmp_path: Path):
    """`aliases` has no column: the row holds it only in its `metadata`
    JSON, where the wikilink resolver's alias pass reads it (`$.aliases`),
    and before #994 only a file that nested `aliases` under `metadata:` put
    it there. The field's own spelling resolves too: the row carries the
    model's reading for either spelling."""
    from pyrite.services.access_policy import UNSCOPED
    from pyrite.services.wikilink_service import WikilinkService

    kb_path = tmp_path / "kb"
    (kb_path / "notes").mkdir(parents=True)
    (kb_path / "notes" / "n4.md").write_text(
        "---\nid: n4\ntitle: N4\ntype: note\naliases: [Hidden moniker]\n---\n\nText.\n",
        encoding="utf-8",
    )
    (kb_path / "notes" / "n5.md").write_text(
        "---\nid: n5\ntitle: N5\ntype: note\nmetadata:\n  aliases: [Nested moniker]\n---\n\nText.\n",
        encoding="utf-8",
    )
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name=KB, path=kb_path, kb_type="generic")],
        settings=Settings(index_path=tmp_path / "index.db", auto_embed=False),
    )
    db = PyriteDB(config.settings.index_path)
    try:
        IndexManager(db, config).index_all()
        svc = WikilinkService(config, db)
        top = svc.resolve_entry("Hidden moniker", readable_kbs=UNSCOPED)
        nested = svc.resolve_entry("Nested moniker", readable_kbs=UNSCOPED)
        row = db.get_entry("n4", KB)
    finally:
        db.close()
    # The row's own `aliases` column (#1001, decision 5): a read spells a
    # field where the file does, so nothing appears under `metadata`.
    assert row is not None and row["aliases"] == ["Hidden moniker"]
    assert "aliases" not in row["metadata"], row["metadata"]
    assert top is not None and top["id"] == "n4", top
    assert nested is not None and nested["id"] == "n5", nested


_TWO_SPELLINGS = [
    pytest.param({"title": "A", "metadata": {"title": "B"}}, id="title-and-envelope"),
    pytest.param({"custom": "A", "metadata.custom": "B"}, id="custom-and-dotted"),
    pytest.param({"assignee": "A", "metadata": {"assignee": "B"}}, id="assignee-and-envelope"),
    pytest.param({"tags": ["x"], "metadata": {"tags": ["y"]}}, id="tags-and-envelope"),
    pytest.param({"metadata.custom": "A", "metadata": {"custom": "B"}}, id="dotted-and-envelope"),
]


@pytest.mark.parametrize("surface", ["service", "rest.put", "mcp", "cli"])
@pytest.mark.parametrize("updates", _TWO_SPELLINGS)
def test_two_spellings_of_one_field_in_one_request_are_refused(
    tmp_path: Path, surface: str, updates: dict[str, Any]
):
    """The request-level rule (#720) grouped aliases only, so a field sent at
    two spellings in one request (`k` and `metadata.k`, at the top level or
    through the `metadata` envelope) wrote the last one and dropped the
    other. Under decision A the spellings are one field: differing values
    are refused and nothing is written (#1001 round 3)."""
    text = (
        "---\nid: t1\ntitle: T\ntype: task\nstatus: open\nassignee: me\ncustom: c\n"
        "tags: [a]\n---\n\nText.\n"
    )
    kb_path = tmp_path / "kb"
    (kb_path / "tasks").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=kb_path, check=True, capture_output=True, timeout=30)
    path = kb_path / "tasks" / "t1.md"
    path.write_text(text, encoding="utf-8")
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name=KB, path=kb_path, kb_type="generic")],
        settings=Settings(index_path=tmp_path / "index.db", auto_embed=False),
    )
    db = PyriteDB(config.settings.index_path)
    try:
        IndexManager(db, config).index_all()
    finally:
        db.close()
    if surface == "rest.put" and not any(k in ("title", "tags") for k in updates):
        pytest.skip(
            "REST PUT carries only title/body/tags/importance at the top level; every other "
            "key travels inside `metadata`, so these two spellings cannot both be sent"
        )
    if surface == "cli" and "metadata.custom" in updates and "metadata" in updates:
        pytest.skip("the CLI spells both of these `-f metadata.custom=`; one `-f` key, sent twice")
    before = path.read_bytes()
    with pytest.raises(PyriteError) as info:
        _send_request(surface, config, "t1", updates)
    assert "two different values" in str(info.value) or "one field" in str(info.value), str(
        info.value
    )
    assert path.read_bytes() == before


@pytest.mark.control(reason="equal values at two spellings name one value (#720), as before")
def test_two_spellings_with_one_value_write_once(tmp_path: Path):
    text = "---\nid: t1\ntitle: T\ntype: task\nstatus: open\ncustom: c\n---\n\nText.\n"
    kb_path = tmp_path / "kb"
    (kb_path / "tasks").mkdir(parents=True)
    path = kb_path / "tasks" / "t1.md"
    path.write_text(text, encoding="utf-8")
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name=KB, path=kb_path, kb_type="generic")],
        settings=Settings(index_path=tmp_path / "index.db", auto_embed=False),
    )
    db = PyriteDB(config.settings.index_path)
    try:
        IndexManager(db, config).index_all()
        KBService(config, db).update("t1", KB, {"custom": "A", "metadata": {"custom": "A"}})
    finally:
        db.close()
    after = path.read_text(encoding="utf-8")
    assert "\ncustom: A\n" in after and "metadata:" not in after, after


def _send_request(surface: str, config: PyriteConfig, entry_id: str, updates: dict[str, Any]):
    """A whole update request (several keys) through one surface; raises
    ``PyriteError`` on a refusal."""
    if surface == "service":
        db = PyriteDB(config.settings.index_path)
        try:
            return KBService(config, db).update(entry_id, KB, updates)
        finally:
            db.close()
    if surface == "mcp":
        from pyrite.server.mcp_server import PyriteMCPServer

        server = PyriteMCPServer(config, tier="write")
        try:
            result = server._dispatch_tool(
                "kb_update", {"kb_name": KB, "entry_id": entry_id, **updates}
            )
        finally:
            server.close()
        if result.get("error") or result.get("isError"):
            raise ValidationError(str(result))
        return result
    if surface == "cli":
        args = ["update", entry_id, "-k", KB, "--format", "json"]
        for key, value in updates.items():
            if key == "metadata":
                for mk, mv in value.items():
                    args += ["-f", f"metadata.{mk}={_cli_value(mv)}"]
            else:
                args += ["-f", f"{key}={_cli_value(value)}"]
        with (
            patch("pyrite.cli.load_config", return_value=config),
            patch("pyrite.cli.context.load_config", return_value=config),
        ):
            result = CliRunner().invoke(cli_app, args)
        if result.exit_code != 0:
            raise ValidationError(result.output)
        return result.output
    pytest.importorskip("fastapi", reason="fastapi not installed")
    from fastapi.testclient import TestClient

    from pyrite.server.api import create_app, get_config, get_db, get_index_mgr

    db = PyriteDB(config.settings.index_path)
    index_mgr = IndexManager(db, config)
    rest = create_app(config)
    rest.dependency_overrides[get_config] = lambda: config
    rest.dependency_overrides[get_db] = lambda: db
    rest.dependency_overrides[get_index_mgr] = lambda: index_mgr
    client = TestClient(rest, raise_server_exceptions=False)
    try:
        # REST PUT carries `title`/`tags`/`metadata`; a dotted key or `custom`
        # travels inside `metadata`, so the two spellings meet there.
        body: dict[str, Any] = {"kb": KB}
        envelope: dict[str, Any] = dict(updates.get("metadata") or {})
        for key, value in updates.items():
            if key == "metadata":
                continue
            if key in ("title", "tags", "body", "importance"):
                body[key] = value
            else:
                envelope[key] = value
        body["metadata"] = envelope
        res = client.put(f"/api/entries/{entry_id}", json=body)
    finally:
        client.close()
        db.close()
    if res.status_code != 200:
        raise ValidationError(res.text)
    assert res.status_code != 500, res.text
    return res.json()


def _cli_value(value: Any) -> str:
    """A value as `-f key=value` sends it: a list as JSON, else the text."""
    return json.dumps(value) if isinstance(value, list | dict) else str(value)


# --------------------------------------------------------------------------
# The maintainer's decisions on #1001 (2026-10-17)
# --------------------------------------------------------------------------


def _one_file_kb(tmp_path: Path, folder: str, name: str, text: str) -> tuple[PyriteConfig, Path]:
    kb_path = tmp_path / "kb"
    (kb_path / folder).mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=kb_path, check=True, capture_output=True, timeout=30)
    path = kb_path / folder / f"{name}.md"
    path.write_text(text, encoding="utf-8")
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name=KB, path=kb_path, kb_type="generic")],
        settings=Settings(index_path=tmp_path / "index.db", auto_embed=False),
    )
    db = PyriteDB(config.settings.index_path)
    try:
        IndexManager(db, config).index_all()
    finally:
        db.close()
    return config, path


def test_qa_fix_merges_an_equal_double_spelling_into_the_top_level_spelling(tmp_path: Path):
    """Decision 2: an update of a field the file spells twice stays refused
    even when both spellings agree; `qa fix` merges the equal pair into the
    field's own top-level spelling, losing nothing. Here `custom` and
    `metadata.custom` both hold `x`; after the fix only `custom: x` stands
    and the other keys keep their bytes."""
    from pyrite.services.access_policy import UNSCOPED
    from pyrite.services.qa_fix_service import QAFixService
    from pyrite.services.qa_service import QAService

    text = (
        "---\nid: n6\ntitle: N6\ntype: note\ncustom: x\nmetadata:\n  custom: x\n  other: 1\n"
        "---\n\nText.\n"
    )
    config, path = _one_file_kb(tmp_path, "notes", "n6", text)
    db = PyriteDB(config.settings.index_path)
    try:
        svc = KBService(config, db)
        with pytest.raises(ValidationError):
            svc.update("n6", KB, {"custom": "y"})
        qa = QAService(config, db)
        report = QAFixService(config, db).fix_kb(
            KB,
            dry_run=False,
            fix_rules=["field_spelled_twice"],
            validate_kb_fn=lambda kb: qa.validate_kb(kb, readable_kbs=UNSCOPED),
        )
    finally:
        db.close()
    assert [f["field"] for f in report["fixed"]] == ["custom"], report
    after = path.read_text(encoding="utf-8")
    assert "\ncustom: x\n" in after and "  custom:" not in after, after
    assert "metadata:\n  other: 1\n" in after, after
    # and the field is editable again
    db = PyriteDB(config.settings.index_path)
    try:
        KBService(config, db).update("n6", KB, {"custom": "y"})
    finally:
        db.close()
    assert "\ncustom: y\n" in path.read_text(encoding="utf-8")


def test_qa_fix_leaves_a_disagreeing_double_spelling_to_a_hand_fix(tmp_path: Path):
    from pyrite.services.access_policy import UNSCOPED
    from pyrite.services.qa_fix_service import QAFixService
    from pyrite.services.qa_service import QAService

    text = "---\nid: n7\ntitle: N7\ntype: note\ncustom: x\nmetadata:\n  custom: y\n---\n\nText.\n"
    config, path = _one_file_kb(tmp_path, "notes", "n7", text)
    before = path.read_bytes()
    db = PyriteDB(config.settings.index_path)
    try:
        qa = QAService(config, db)
        report = QAFixService(config, db).fix_kb(
            KB,
            dry_run=False,
            fix_rules=["field_spelled_twice"],
            validate_kb_fn=lambda kb: qa.validate_kb(kb, readable_kbs=UNSCOPED),
        )
    finally:
        db.close()
    assert report["fixed"] == [], report
    assert [m["reason"] for m in report["manual"]] == ["spellings_disagree"], report
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(
            "---\nid: g1\ntitle: G\ntype: widget\nrank: 1\nmetadata:\n  rank: 2\n---\n\nText.\n",
            id="generic",
            marks=pytest.mark.control(
                reason="on 939910c the nested value won and `extra_frontmatter` carried the "
                "top-level one, so both came back; the loss appeared with this PR's own "
                "precedence flip (be6bd08, decision 3) and is fixed here (`_shadowed_nested`)"
            ),
        ),
        pytest.param(
            "---\nid: g3\ntitle: G\ntype: note\nrank: 1\nmetadata:\n  rank: 2\n---\n\nText.\n",
            id="note",
            marks=pytest.mark.control(
                reason="a core type keeps the top-level value in `extra_frontmatter` and the "
                "nested one in its bag; pinned so the statement holds for every class"
            ),
        ),
        pytest.param(
            "---\nid: g2\ntitle: G\ntype: task\nstatus: open\nrank: 1\nmetadata:\n  rank: 2\n"
            "---\n\nText.\n",
            id="task",
        ),
    ],
)
def test_a_save_through_the_model_keeps_both_values_of_a_doubly_spelled_key(text: str):
    """Decision 4: the model reads the top-level spelling (decision 3) and a
    save through the model loses nothing: `rank: 1` and `metadata: {rank: 2}`
    both come back."""
    from pyrite.models.core_types import entry_from_frontmatter
    from pyrite.storage.repository import new_entry_text
    from pyrite.utils.frontmatter import load_frontmatter

    meta, body = load_frontmatter(text)
    entry = entry_from_frontmatter(meta, body)
    fm = entry.to_frontmatter()
    assert fm.get("rank") == 1 and fm.get("metadata", {}).get("rank") == 2, fm
    # The model's own emitter is gone (#978): the one text a model still
    # produces is a new file's, through `apply()`.
    saved, _ = load_frontmatter(new_entry_text(entry))
    assert saved.get("rank") == 1 and saved.get("metadata", {}).get("rank") == 2, saved


def test_a_read_spells_aliases_where_the_file_does(tmp_path: Path):
    """Decision 5, as #1025 reads it (the maintainer's option B, 2026-10-20):
    a file with a top-level `aliases:` reads it at the top level and nothing
    under `metadata`; a file that nests it reads it at the top level too (the
    reader reads where the writer writes). The single-entry read spells it
    once, at its own name; the index row, the storage dict, keeps the bag as
    stored, nested spelling included."""
    kb_path = tmp_path / "kb"
    (kb_path / "notes").mkdir(parents=True)
    (kb_path / "notes" / "a1.md").write_text(
        "---\nid: a1\ntitle: A1\ntype: note\naliases: [Top]\n---\n\nText.\n", encoding="utf-8"
    )
    (kb_path / "notes" / "a2.md").write_text(
        "---\nid: a2\ntitle: A2\ntype: note\nmetadata:\n  aliases: [Nested]\n---\n\nText.\n",
        encoding="utf-8",
    )
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name=KB, path=kb_path, kb_type="generic")],
        settings=Settings(index_path=tmp_path / "index.db", auto_embed=False),
    )
    db = PyriteDB(config.settings.index_path)
    try:
        IndexManager(db, config).index_all()
        from pyrite.services.access_policy import UNSCOPED

        svc = KBService(config, db)
        top_row = db.get_entry("a1", KB)
        top_read = svc.get_entry("a1", KB, readable_kbs=UNSCOPED)
        nested_row = db.get_entry("a2", KB)
        nested_read = svc.get_entry("a2", KB, readable_kbs=UNSCOPED)
    finally:
        db.close()
    for shape in (top_row, top_read):
        assert shape["aliases"] == ["Top"] and "aliases" not in shape["metadata"], shape
    for shape in (nested_row, nested_read):
        assert shape["aliases"] == ["Nested"], shape
    assert nested_row["metadata"].get("aliases") == ["Nested"], nested_row
    assert "aliases" not in nested_read["metadata"], nested_read


def _create_cells() -> list[Any]:
    """Decision 6: every managed field, at every spelling `spellings_of`
    gives (and each under `metadata.`), as one key of a create's `metadata`
    envelope, through the service, REST POST and MCP `kb_create`."""
    try:
        from pyrite.schema.field_aliases import spellings_of
    except ImportError:
        # Before the fix (see `_cells`): generated from the alias map the
        # base already has, so the cells collect and go red for what they
        # test (a managed field written by a create) rather than for a
        # missing name.
        from pyrite.schema.field_aliases import class_field_aliases

        def spellings_of(cls: type, entry_type: str, field: str) -> list[tuple[str, ...]]:
            aliases = class_field_aliases(cls, entry_type)
            names = [field, *(a for a, t in aliases.items() if t == field and a != field)]
            return [(n,) for n in names] + [("metadata", n) for n in names]

    cells: list[Any] = []
    base_done = False
    for type_name, cls in sorted(_classes().items()):
        own = frozenset(getattr(cls, "managed_fields", frozenset()))
        fields = [f for f in _managed(cls) if f in own]
        if not base_done and type_name == "note":
            fields = _managed(cls)
            base_done = True
        if not fields or type_name not in _FILES:
            continue
        for field in fields:
            keys: list[str] = []
            for spelling in spellings_of(cls, type_name, field):
                keys += [_dotted(spelling), f"metadata.{_dotted(spelling)}"]
            for key in dict.fromkeys(keys):
                for surface in ("service.create", "rest.post", "mcp.create"):
                    cells.append((type_name, field, key, surface))
    return cells


_CREATE_CELLS = _create_cells()


@pytest.mark.parametrize(
    "cell", [pytest.param(c, id=f"{c[0]}:{c[2]}:{c[3]}") for c in _CREATE_CELLS]
)
def test_create_refuses_a_managed_field_sent_in_the_metadata_envelope(
    cell: Any, tmp_path_factory: pytest.TempPathFactory
):
    type_name, _field, key, surface = cell
    config, _path = _kb_for(type_name, tmp_path_factory)
    kb_path = config.knowledge_bases[0].path
    new_id = f"new-{abs(hash((type_name, key, surface))) % 10**8}"
    before = sorted(p.name for p in kb_path.rglob("*.md"))
    envelope = {"metadata": {key: "x"}}
    with pytest.raises(PyriteError):
        _create(surface, config, type_name, new_id, envelope)
    assert sorted(p.name for p in kb_path.rglob("*.md")) == before, "refused, yet a file appeared"


def _create(surface: str, config: PyriteConfig, type_name: str, new_id: str, extra: dict[str, Any]):
    spec: dict[str, Any] = {
        "entry_type": type_name,
        "title": f"New {new_id}",
        "id": new_id,
        **extra,
    }
    if surface == "service.create":
        db = PyriteDB(config.settings.index_path)
        try:
            return KBService(config, db).create(KB, spec)
        finally:
            db.close()
    if surface == "mcp.create":
        from pyrite.server.mcp_server import PyriteMCPServer

        server = PyriteMCPServer(config, tier="write")
        try:
            result = server._dispatch_tool("kb_create", {"kb_name": KB, **spec})
        finally:
            server.close()
        if result.get("error") or result.get("isError"):
            raise ValidationError(str(result))
        return result
    pytest.importorskip("fastapi", reason="fastapi not installed")
    from fastapi.testclient import TestClient

    from pyrite.server.api import create_app, get_config, get_db, get_index_mgr

    db = PyriteDB(config.settings.index_path)
    index_mgr = IndexManager(db, config)
    rest = create_app(config)
    rest.dependency_overrides[get_config] = lambda: config
    rest.dependency_overrides[get_db] = lambda: db
    rest.dependency_overrides[get_index_mgr] = lambda: index_mgr
    client = TestClient(rest, raise_server_exceptions=False)
    try:
        body = {"kb": KB, "entry_type": type_name, "title": spec["title"], **extra}
        res = client.post("/api/entries", json=body)
    finally:
        client.close()
        db.close()
    if res.status_code >= 400:
        raise ValidationError(res.text)
    assert res.status_code != 500, res.text
    return res.json()


def test_a_top_level_alias_in_an_unreadable_kb_does_not_resolve_for_a_scoped_caller(
    tmp_path: Path,
):
    """The alias pass reads the `aliases` column and the bag in one `OR`;
    the caller's read scope must bind both branches (an unparenthesised
    `OR ... AND kb_name` let a private KB's top-level alias resolve for a
    scoped caller, caught by `test_read_scope_queries`)."""
    from pyrite.services.access_policy import UNSCOPED
    from pyrite.services.wikilink_service import WikilinkService

    kbs: list[KBConfig] = []
    for name in ("pub", "priv"):
        kb_path = tmp_path / name
        (kb_path / "notes").mkdir(parents=True)
        (kb_path / "notes" / f"{name}-1.md").write_text(
            f"---\nid: {name}-1\ntitle: {name.title()}\ntype: note\naliases: [Hidden {name}]\n"
            "---\n\nText.\n",
            encoding="utf-8",
        )
        kbs.append(KBConfig(name=name, path=kb_path, kb_type="generic"))
    config = PyriteConfig(
        knowledge_bases=kbs, settings=Settings(index_path=tmp_path / "index.db", auto_embed=False)
    )
    db = PyriteDB(config.settings.index_path)
    try:
        IndexManager(db, config).index_all()
        svc = WikilinkService(config, db)
        assert svc.resolve_entry("Hidden priv", readable_kbs={"pub"}) is None
        assert svc.resolve_entry("Hidden pub", readable_kbs={"pub"})["id"] == "pub-1"
        assert svc.resolve_entry("Hidden priv", readable_kbs=UNSCOPED)["id"] == "priv-1"
    finally:
        db.close()


# --------------------------------------------------------------------------
# Round 4 delta review (#1001): the merge is type-strict and the fix's own;
# create names a field once; an emptied block goes; `type` is a hand fix
# --------------------------------------------------------------------------


def _fix_double_spellings(config: PyriteConfig) -> dict[str, Any]:
    from pyrite.services.access_policy import UNSCOPED
    from pyrite.services.qa_fix_service import QAFixService
    from pyrite.services.qa_service import QAService

    db = PyriteDB(config.settings.index_path)
    try:
        qa = QAService(config, db)
        return QAFixService(config, db).fix_kb(
            KB,
            dry_run=False,
            fix_rules=["field_spelled_twice"],
            validate_kb_fn=lambda kb: qa.validate_kb(kb, readable_kbs=UNSCOPED),
        )
    finally:
        db.close()


@pytest.mark.parametrize(
    ("top", "nested"),
    [
        pytest.param("true", "1", id="bool-vs-int"),
        pytest.param("1", "1.0", id="int-vs-float"),
        pytest.param("[1, 2]", "[1, 2.0]", id="list-item-type"),
    ],
)
def test_qa_fix_keeps_a_pair_whose_values_differ_in_type(tmp_path: Path, top: str, nested: str):
    """Item 1: Python's `==` calls `True` and `1`, `1` and `1.0` equal; the
    merge must not. A pair that is not the same YAML value, type included,
    is a hand fix and the file keeps both."""
    text = f"---\nid: n8\ntitle: N8\ntype: note\nflag: {top}\nmetadata:\n  flag: {nested}\n---\n\nText.\n"
    config, path = _one_file_kb(tmp_path, "notes", "n8", text)
    before = path.read_bytes()
    report = _fix_double_spellings(config)
    assert report["fixed"] == [], report
    assert [m["reason"] for m in report["manual"]] == ["spellings_disagree"], report
    assert path.read_bytes() == before


@pytest.mark.parametrize("surface", ["service.create", "rest.post", "mcp.create"])
def test_create_refuses_two_spellings_of_one_field_with_different_values(
    surface: str, tmp_path_factory: pytest.TempPathFactory
):
    """Item 2: create applies decision 1 as update does: `title: A` with
    `metadata: {title: B}` is refused and no file appears; equal values
    write the field once."""
    config, _path = _kb_for("note", tmp_path_factory)
    kb_path = config.knowledge_bases[0].path
    before = sorted(p.name for p in kb_path.rglob("*.md"))
    with pytest.raises(PyriteError) as info:
        _create(surface, config, "note", "two-titles", {"metadata": {"title": "B"}})
    assert "two different values" in str(info.value) or "one field" in str(info.value), str(
        info.value
    )
    assert sorted(p.name for p in kb_path.rglob("*.md")) == before, "refused, yet a file appeared"


@pytest.mark.control(reason="equal values at two spellings write the field once (#720)")
def test_create_writes_a_field_named_twice_with_one_value_once(tmp_path: Path):
    kb_path = tmp_path / "kb"
    (kb_path / "notes").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=kb_path, check=True, capture_output=True, timeout=30)
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name=KB, path=kb_path, kb_type="generic")],
        settings=Settings(index_path=tmp_path / "index.db", auto_embed=False),
    )
    db = PyriteDB(config.settings.index_path)
    try:
        IndexManager(db, config).index_all()
        KBService(config, db).create(
            KB,
            {"entry_type": "note", "title": "Same", "id": "same", "metadata": {"custom": "x"}},
        )
        KBService(config, db).create(
            KB,
            {
                "entry_type": "note",
                "title": "Twice",
                "id": "twice",
                "custom": "x",
                "metadata": {"custom": "x"},
            },
        )
    finally:
        db.close()
    text = next(kb_path.rglob("twice.md")).read_text(encoding="utf-8")
    assert text.count("custom: x") == 1, text


def test_the_spelling_merge_is_not_open_to_an_update(tmp_path: Path):
    """Item 3: decision 2 says an update of a doubly spelled field is
    refused, equal values or not; only `qa fix`'s own call may remove a
    spelling. `unset: ["metadata.custom"]` through the service is refused."""
    text = "---\nid: n9\ntitle: N9\ntype: note\ncustom: x\nmetadata:\n  custom: x\n---\n\nText.\n"
    config, path = _one_file_kb(tmp_path, "notes", "n9", text)
    before = path.read_bytes()
    db = PyriteDB(config.settings.index_path)
    try:
        svc = KBService(config, db)
        with pytest.raises(ValidationError):
            svc.update("n9", KB, {}, unset=["metadata.custom"])
        with pytest.raises(ValidationError):
            svc.apply_operations("n9", KB, [Unset(("metadata", "custom"))])
    finally:
        db.close()
    assert path.read_bytes() == before


def test_the_merge_removes_an_emptied_metadata_block(tmp_path: Path):
    """Item 4: when the merge removes the last nested key, no `metadata: {}`
    is left behind: bytes the author never wrote do not appear."""
    text = "---\nid: n10\ntitle: N10\ntype: note\ncustom: x\nmetadata:\n  custom: x\n---\n\nText.\n"
    config, path = _one_file_kb(tmp_path, "notes", "n10", text)
    report = _fix_double_spellings(config)
    assert [f["field"] for f in report["fixed"]] == ["custom"], report
    after = path.read_text(encoding="utf-8")
    assert "metadata" not in after, after
    assert "\ncustom: x\n" in after, after


def test_qa_fix_leaves_a_doubly_spelled_type_to_a_hand_fix(tmp_path: Path):
    """Item 5: `type` spelled twice with equal values is not `failed` ("type
    is fixed once created"); it is listed as a hand fix."""
    text = "---\nid: n11\ntitle: N11\ntype: note\nmetadata:\n  type: note\n---\n\nText.\n"
    config, path = _one_file_kb(tmp_path, "notes", "n11", text)
    before = path.read_bytes()
    report = _fix_double_spellings(config)
    assert report["failed"] == [], report
    assert [m["reason"] for m in report["manual"]] == ["field_fixed_once_created"], report
    assert path.read_bytes() == before


# --------------------------------------------------------------------------
# The maintainer's go for round 5 (#1001, comment 6236339417): each of the
# round-4 review's five items has a stated property; the tests above pin an
# instance of each, the grids below the property.
# --------------------------------------------------------------------------


def _spellings_fn():
    """``spellings_of``, or the same generator over the alias map the base
    has (see ``_cells``), so the grids below collect and go red on the base
    for what they test."""
    try:
        from pyrite.schema.field_aliases import spellings_of
    except ImportError:
        from pyrite.schema.field_aliases import class_field_aliases

        def spellings_of(cls: type, entry_type: str, field: str) -> list[tuple[str, ...]]:
            aliases = class_field_aliases(cls, entry_type)
            names = [field, *(a for a, t in aliases.items() if t == field and a != field)]
            return [(n,) for n in names] + [("metadata", n) for n in names]

    return spellings_of


def _frontmatter_of(path: Path) -> dict[str, Any]:
    from pyrite.utils.frontmatter import load_frontmatter

    meta, _body = load_frontmatter(path.read_text(encoding="utf-8"))
    return meta


def _same_yaml_value(a: Any, b: Any) -> bool:
    """The oracle's own statement of item 1: two YAML values are the same
    when they are of one type and, item by item, equal; `true` and `1`, `1`
    and `1.0`, a date and its text are not (Python's `==` says some are)."""
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(_same_yaml_value(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(_same_yaml_value(x, y) for x, y in zip(a, b, strict=True))
    return bool(a == b)


#: YAML texts a file may spell a value as, chosen so Python's `==` and YAML
#: equality disagree on many pairs: `1`/`1.0`/`true`, `[1]`/`[1.0]`,
#: `{a: 1}`/`{a: true}`, a date and its quoted text, an empty list and map;
#: and pairs that are one YAML value in two notations (`x`, `'x'`, `"x"`:
#: the parser gives each its own scalar class, #1013).
_YAML_TEXTS = [
    "1",
    "1.0",
    "true",
    "'1'",
    "'true'",
    "null",
    "x",
    "'x'",
    '"x"',
    "[1]",
    "[1.0]",
    "[true]",
    "{a: 1}",
    "{a: true}",
    "2026-01-01",
    "'2026-01-01'",
    "[]",
    "{}",
]


def _equality_cells() -> list[Any]:
    """Every ordered pair of ``_YAML_TEXTS`` whose values Python calls equal
    (the pairs where the property bites, the same text included), plus the
    pairs that differ outright."""
    import yaml

    cells: list[Any] = []
    for top in _YAML_TEXTS:
        for nested in _YAML_TEXTS:
            try:
                equal = yaml.safe_load(top) == yaml.safe_load(nested)
            except TypeError:
                equal = False
            if equal or (top, nested) in {
                ("x", "1"),
                ("null", "x"),
                ("[]", "{}"),
                ("2026-01-01", "'2026-01-01'"),
                ("1", "'1'"),
                ("true", "'true'"),
                ("[1]", "[true]"),
            }:
                cells.append((top, nested))
    return cells


@pytest.mark.parametrize(
    ("top", "nested"), [pytest.param(t, n, id=f"{t}|{n}") for t, n in _equality_cells()]
)
def test_a_double_spelling_merges_iff_both_spell_the_same_yaml_value(
    tmp_path: Path, top: str, nested: str
):
    """Item 1, the property: `qa fix` merges a pair spelled `k: a` and
    `metadata: {k: b}` exactly when `a` and `b` are the same YAML value,
    type included (``_same_yaml_value``); then the file holds `k` at the top
    level alone, reading the value it had. Any other pair is a hand fix
    (`spellings_disagree`) and the bytes stay."""
    import yaml

    text = f"---\nid: n12\ntitle: N12\ntype: note\nflag: {top}\nmetadata:\n  flag: {nested}\n---\n\nText.\n"
    config, path = _one_file_kb(tmp_path, "notes", "n12", text)
    before_bytes = path.read_bytes()
    before = _frontmatter_of(path)
    report = _fix_double_spellings(config)
    assert report["failed"] == [], report
    if _same_yaml_value(yaml.safe_load(top), yaml.safe_load(nested)):
        assert [f["field"] for f in report["fixed"]] == ["flag"], report
        after = _frontmatter_of(path)
        assert "metadata" not in after, after
        assert _same_yaml_value(after["flag"], before["flag"]), (after, before)
        assert "  flag:" not in path.read_text(encoding="utf-8")
    else:
        assert report["fixed"] == [], report
        assert [(m["field"], m["reason"]) for m in report["manual"]] == [
            ("flag", "spellings_disagree")
        ], report
        assert path.read_bytes() == before_bytes


def _create_pair_cells() -> list[Any]:
    """Item 2: for fields with two or more spellings (`k`/`metadata.k`, and
    `actors`/`participants` with each under `metadata.`), every pair of
    distinct spellings × the three create surfaces. A cell is
    ``(type, field, spelling_a, spelling_b, value_a, value_b, surface)``."""
    spellings_of = _spellings_fn()
    cells: list[Any] = []
    for type_name, field, value_a, value_b in (
        ("note", "title", "A", "B"),
        ("note", "tags", ["a"], ["b"]),
        ("note", "custom", "A", "B"),
        ("event", "actors", ["A"], ["B"]),
    ):
        cls = get_entry_class(type_name)
        spellings = spellings_of(cls, type_name, field)
        for i, first in enumerate(spellings):
            for second in spellings[i + 1 :]:
                for surface in ("service.create", "rest.post", "mcp.create"):
                    cells.append((type_name, field, first, second, value_a, value_b, surface))
    return cells


def _create_request(spelling_a: tuple[str, ...], a: Any, spelling_b: tuple[str, ...], b: Any):
    extra: dict[str, Any] = {}
    envelope: dict[str, Any] = {}
    for spelling, value in ((spelling_a, a), (spelling_b, b)):
        if spelling[0] == "metadata":
            envelope[spelling[1]] = value
        else:
            extra[spelling[0]] = value
    if envelope:
        extra["metadata"] = envelope
    return extra


def _skip_unless_rest_carries(surface: str, extra: dict[str, Any]) -> None:
    carried = {"title", "tags", "participants", "metadata", "date"}
    if surface == "rest.post" and not set(extra) <= carried:
        pytest.skip("REST POST's body carries no such top-level key (n/a on this surface)")


def _create_cell_id(c: Any) -> str:
    return f"{c[0]}:{_dotted(c[2])}+{_dotted(c[3])}:{c[6]}"


def _create_pair_params(write_half: bool) -> list[Any]:
    """The cells, with the ones the base already got right labelled controls
    (verify-red against 8b26895): the #720 rule there grouped an alias and
    its target across the top level and the envelope, so every
    `actors`/`participants` pair was refused and, equal, written once; and an
    undeclared key sent as `custom` and `metadata.custom` met in the bag, so
    equal values were one key. The evidence is a declared field with no
    alias spelled `k` and `metadata.k` (`title`, `tags`; and `custom` for the
    refusal), which the base nested as a second spelling."""
    params: list[Any] = []
    for c in _create_pair_cells():
        type_name, field = c[0], c[1]
        reason = None
        if type_name == "event":
            reason = (
                "the base's #720 rule grouped an alias and its target from both sources, so "
                "this pair was already refused (and written once when equal); the `note` "
                "cells are the evidence"
            )
        elif write_half and field == "custom":
            reason = (
                "two spellings of an undeclared key met in the base's `metadata` bag, so an "
                "equal pair was one key there too; the declared fields are the evidence"
            )
        params.append(
            pytest.param(c, marks=pytest.mark.control(reason=reason), id=_create_cell_id(c))
            if reason
            else pytest.param(c, id=_create_cell_id(c))
        )
    return params


@pytest.mark.parametrize("cell", _create_pair_params(write_half=False))
def test_create_refuses_every_pair_of_spellings_of_one_field_with_different_values(
    cell: Any, tmp_path: Path
):
    """Item 2, the property: a create that names one field at two spellings
    (`k` and `metadata.k`, an alias and its target, either under
    `metadata.`) with different values is refused on every create surface,
    and no file appears."""
    type_name, _field, first, second, value_a, value_b, surface = cell
    extra = _create_request(first, value_a, second, value_b)
    if type_name == "event":
        extra["date"] = "2026-01-01"
    _skip_unless_rest_carries(surface, extra)
    config, _path = _one_file_kb(tmp_path, "notes", "seed", _FILES["note"])
    kb_path = config.knowledge_bases[0].path
    before = sorted(p.name for p in kb_path.rglob("*.md"))
    with pytest.raises(PyriteError) as info:
        _create(surface, config, type_name, "twice", extra)
    assert "different values" in str(info.value), str(info.value)
    assert sorted(p.name for p in kb_path.rglob("*.md")) == before, "refused, yet a file appeared"


@pytest.mark.parametrize("cell", _create_pair_params(write_half=True))
def test_create_writes_a_field_named_at_two_spellings_with_one_value_once(
    cell: Any, tmp_path: Path
):
    """Item 2, the other half: equal values at two spellings name one value
    (#720), and the file spells the field exactly once, at one of its
    spellings, reading that value."""
    type_name, field, first, second, value_a, _value_b, surface = cell
    extra = _create_request(first, value_a, second, value_a)
    if type_name == "event":
        extra["date"] = "2026-01-01"
    _skip_unless_rest_carries(surface, extra)
    config, _path = _one_file_kb(tmp_path, "notes", "seed", _FILES["note"])
    kb_path = config.knowledge_bases[0].path
    _create(surface, config, type_name, "once", extra)
    written = [p for p in kb_path.rglob("*.md") if p.name != "seed.md"]
    assert len(written) == 1, written
    fm = _frontmatter_of(written[0])
    nested = fm.get("metadata") or {}
    spelled = [
        s
        for s in _spellings_fn()(get_entry_class(type_name), type_name, field)
        if s[-1] in (nested if s[0] == "metadata" else fm)
    ]
    assert len(spelled) == 1, (spelled, fm)
    holder = nested if spelled[0][0] == "metadata" else fm
    assert holder[spelled[0][-1]] == value_a, fm


def _merge_closed_cells() -> list[Any]:
    cells: list[Any] = []
    for agree in (True, False):
        for path in (("custom",), ("metadata", "custom")):
            for op in (Set, Append, Remove, Unset):
                for surface in ("service", "mcp", "rest.patch", "cli"):
                    cells.append((agree, path, op, surface))
    return cells


@pytest.mark.parametrize(
    "cell",
    [
        pytest.param(
            c, id=f"{'equal' if c[0] else 'differ'}:{_dotted(c[1])}:{c[2].__name__.lower()}:{c[3]}"
        )
        for c in _merge_closed_cells()
    ],
)
def test_no_update_of_a_field_spelled_twice_removes_a_spelling_on_any_surface(
    cell: Any, tmp_path: Path
):
    """Item 3, the property: the merge is `qa fix`'s own call. An update of
    a field the file spells twice is refused for every operation kind, at
    either spelling, on every surface, whether or not the two spellings
    agree; the bytes stay. (`test_qa_fix_merges_an_equal_double_spelling_
    into_the_top_level_spelling` is the one caller the exception is for.)"""
    agree, path, op, surface = cell
    nested = "[x]" if agree else "[y]"
    text = (
        f"---\nid: n13\ntitle: N13\ntype: note\ncustom: [x]\nmetadata:\n  custom: {nested}\n"
        "---\n\nText.\n"
    )
    config, file_path = _one_file_kb(tmp_path, "notes", "n13", text)
    before = file_path.read_bytes()
    with pytest.raises(PyriteError):
        _send(surface, config, "n13", op, path)
    assert file_path.read_bytes() == before


#: Item 4: the shapes a `metadata:` block may take around an equal pair,
#: with the fields merged and what the block holds afterwards (None: the
#: block is gone). The last shape keeps a key that is no second spelling,
#: so the block stays with that key alone (#1013's shape, which merges; the
#: issue's probe failed on quoting, item 1's grid above).
_EMPTIED_BLOCKS: list[Any] = [
    pytest.param("notes", "custom: x\nmetadata:\n  custom: x\n", ["custom"], None, id="block"),
    pytest.param("notes", "custom: x\nmetadata: {custom: x}\n", ["custom"], None, id="flow"),
    pytest.param(
        "notes",
        "custom: x\nother: 2\nmetadata:\n  custom: x\n  other: 2\n",
        ["custom", "other"],
        None,
        id="two-pairs",
    ),
    pytest.param(
        "notes",
        "custom: x\nmetadata:\n  # the author's note\n  custom: x\n",
        ["custom"],
        None,
        id="comment",
    ),
    pytest.param(
        "notes",
        "custom: [a, b]\nmetadata:\n  custom:\n  - a\n  - b\n",
        ["custom"],
        None,
        id="list",
    ),
    pytest.param(
        "events",
        "date: 2026-01-01\nactors: [A]\nmetadata:\n  participants: [A]\n",
        ["actors"],
        None,
        id="alias-pair",
    ),
    pytest.param(
        "notes",
        "custom: x\nmetadata:\n  custom: x\n  other: 1\n",
        ["custom"],
        {"other": 1},
        id="other-key",
    ),
]


@pytest.mark.parametrize(("folder", "fields", "merged", "block_after"), _EMPTIED_BLOCKS)
def test_the_merge_never_leaves_an_empty_metadata_block(
    tmp_path: Path, folder: str, fields: str, merged: list[str], block_after: dict | None
):
    """Item 4, the property: whatever shape the block has (block or flow
    style, one pair or several, a comment, a list value, an alias pair, a
    key of its own beside the pair), after `qa fix` the file holds no
    `metadata` key with nothing under it: a block the merge empties is
    gone whole, one it does not empty keeps its other keys; every merged
    field stands at the top level alone, with the value it had."""
    type_name = "event" if folder == "events" else "note"
    text = f"---\nid: b1\ntitle: B1\ntype: {type_name}\n{fields}---\n\nText.\n"
    config, path = _one_file_kb(tmp_path, folder, "b1", text)
    before = _frontmatter_of(path)
    report = _fix_double_spellings(config)
    assert report["failed"] == [], report
    assert sorted(f["field"] for f in report["fixed"]) == sorted(merged), report
    after_text = path.read_text(encoding="utf-8")
    after = _frontmatter_of(path)
    assert "metadata: {}" not in after_text and "metadata:\n---" not in after_text, after_text
    if block_after is None:
        assert "metadata" not in after, after_text
    else:
        assert after["metadata"] == block_after, after_text
    for field in merged:
        assert field in after and after[field] == before[field], (field, after_text)
        assert f"  {field}:" not in after_text, after_text


@pytest.mark.parametrize(
    ("type_name", "top", "nested"),
    [
        pytest.param("note", "note", "note", id="note-equal"),
        pytest.param("note", "note", "task", id="note-differ"),
        pytest.param("task", "task", "task", id="task-equal"),
        pytest.param("task", "task", "note", id="task-differ"),
    ],
)
def test_a_doubly_spelled_type_is_a_hand_fix_whatever_the_values(
    tmp_path: Path, type_name: str, top: str, nested: str
):
    """Item 5, the property: `type` spelled twice is listed as a hand fix
    (`field_fixed_once_created`) for every class and whether or not the two
    spellings agree; it is never `fixed` and never `failed`, and the bytes
    stay."""
    extra = "status: open\n" if type_name == "task" else ""
    text = (
        f"---\nid: t1\ntitle: T1\ntype: {top}\n{extra}metadata:\n  type: {nested}\n---\n\nText.\n"
    )
    folder = "tasks" if type_name == "task" else "notes"
    config, path = _one_file_kb(tmp_path, folder, "t1", text)
    before = path.read_bytes()
    report = _fix_double_spellings(config)
    assert report["failed"] == [] and report["fixed"] == [], report
    assert [(m["field"], m["reason"]) for m in report["manual"]] == [
        ("type", "field_fixed_once_created")
    ], report
    assert path.read_bytes() == before
