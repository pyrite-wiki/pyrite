"""The reader reads every field where the writer writes it (#1025; ADR-0051).

ADR-0051: ``metadata.k`` is a second spelling of field ``k``, one level only,
the same as an alias, and every write lands where the file spells the field
(``spelling_in``, ADR-0042 decision 12). The reader half: for every field,
every reader takes its value from ``spelling_in(file, field)``, the path the
writer writes. A file spelling a field only under ``metadata:`` reads exactly
as if it spelled it at the top level, in the model, the index row, the
single-entry read (CLI, MCP, REST), search and health; and the writers that
appended where nothing read (``pyrite link``, #975's refusal) append there.

The grid is generated from the code, not hand-written: every entry class the
registry knows (and the software-kb plugin's), x every field the class
declares, x every spelling ``spellings_of`` gives. Its oracle is a twin: the
read of a file spelling the field at one spelling equals the read of the file
spelling it at the field's own name, key for key, except that the nested
spelling also stays under ``metadata`` (the read shape "both": an echo of
either spelling back is unchanged). A top-level spelling is a control; the
nested ones are the evidence. Each cell's sample value is hand-written
(``SAMPLES``) and must read as given at the top level (the witness), so a cell
cannot pass because both twins read a default.

Read shape (the maintainer's decision of 2026-10-20, option B): the
single-entry read spells a declared field once, at its own name, and its
``metadata`` holds only keys the type does not declare; the index row, the
storage dict the plugins read, keeps its bag as stored. An undeclared key is
not lifted. Migrations run before the lift, on the file's own spellings (open
question 2): ``schema migrate`` diffs their result against the file.
"""

from __future__ import annotations

import ast
import dataclasses
import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from pyrite.cli import app as cli_app
from pyrite.config import AuthConfig, KBConfig, PyriteConfig, Settings
from pyrite.exceptions import PyriteError, ValidationError
from pyrite.models.core_types import ENTRY_TYPE_REGISTRY
from pyrite.schema.field_aliases import class_field_aliases, spellings_of
from pyrite.services.access_policy import UNSCOPED
from pyrite.services.kb_service import _MANAGED_FIELDS, KBService
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager
from pyrite.storage.repository import KBRepository

ROOT = Path(__file__).resolve().parents[1]
KB = "spell"
BODY = "Shared body text."

# --------------------------------------------------------------------------
# The grid's axes: classes, their declared fields, the spellings
# --------------------------------------------------------------------------

#: Dataclass fields that are not frontmatter fields: the identity line (`id:`,
#: design principle 5) and `type:` (which picks the class before any spelling
#: is read) are never read under `metadata`; the rest are not file keys.
_NOT_A_FIELD = {"id", "type", "body", "metadata", "file_path", "kb_name", "extra_frontmatter"}


def _classes() -> dict[str, type]:
    """Every registered class once, under one type name, plus the software-kb
    plugin's three (their own fields: `rank`, `adr_number`, `path`)."""
    classes: dict[str, type] = {}
    for name, cls in ENTRY_TYPE_REGISTRY.items():
        if cls not in classes.values():
            classes[name] = cls
    try:
        from pyrite_software_kb.entry_types import (  # type: ignore[import-not-found]
            ADREntry,
            BacklogItemEntry,
            ComponentEntry,
        )

        classes.update(adr=ADREntry, backlog_item=BacklogItemEntry, component=ComponentEntry)
    except ImportError:
        pass
    return classes


def _fields(cls: type, type_name: str) -> list[str]:
    """The fields a class declares: its public dataclass fields that are file
    keys, and every alias target; an alias name that is itself a dataclass
    field (`participants`) is a spelling of its target, not a field."""
    names = [
        f.name
        for f in dataclasses.fields(cls)
        if not f.name.startswith("_") and f.name not in _NOT_A_FIELD
    ]
    aliases = class_field_aliases(cls, type_name)
    for target in aliases.values():
        if target not in names:
            names.append(target)
    return [n for n in names if n not in aliases]


#: One sample per field, hand-written: a value the class reads as given at
#: the top level and that is not the field's default (the witness).
SAMPLES: dict[str, Any] = {
    "title": "Spelled title",
    "summary": "A summary.",
    "tags": ["t1"],
    "aliases": ["Alias"],
    "links": [{"target": "bare", "relation": "related_to"}],
    "sources": [{"title": "S", "url": "https://example.org/s"}],
    "provenance": {"created_by": "me"},
    "importance": 8,
    "lifecycle": "archived",
    "created_at": "2026-01-02T03:04:05+00:00",
    "updated_at": "2026-01-03T03:04:05+00:00",
    "location": "Here",
    "coordinates": "1.0,2.0",
    "role": "editor",
    "affiliations": ["Org"],
    "research_status": "complete",
    "org_type": "ngo",
    "jurisdiction": "US",
    "founded": "1999",
    "status": "draft",
    "date": "2026-03-04",
    "start_date": "2026-03-05",
    "end_date": "2026-03-06",
    "due_date": "2026-03-07",
    "actors": ["Ann"],
    "notes": "n",
    "author": "A",
    "document_type": "report",
    "url": "https://example.org",
    "source_entity": "a",
    "target_entity": "b",
    "relationship_type": "knows",
    "date_range": "2020-2021",
    "source_type": "query",
    "query": "q",
    "description": "d",
    "icon": "i",
    "view_config": {"default_view": "grid"},
    "entry_filter": {"type": "note"},
    "folder_path": "f",
    "collection_type": "smart",
    "target_entry": "e",
    "target_kb": "k",
    "tier": 2,
    "qa_status": "fail",
    "issues": [{"kind": "x"}],
    "issues_found": 3,
    "issues_resolved": 1,
    "assessed_at": "2026-01-01",
    "parent": "p",
    "priority": 2,
    "assignee": "ann",
    "assigned_at": "2026-01-01T00:00:00+00:00",
    "status_reason": "why",
    "parked_awaiting": "rfp-due",
    "dependencies": ["d1"],
    "evidence": ["e1"],
    "agent_context": {"run": 1},
    "status_change_log": [{"status": "open", "by": "me"}],
    "kind": "bug",
    "effort": "M",
    "rank": 3,
    "adr_number": 7,
    "deciders": ["me"],
    "superseded_by": "adr-2",
    "path": "pyrite/x.py",
    "owner": "me",
}
SAMPLE_OVERRIDES: dict[tuple[str, str], Any] = {
    ("task", "status"): "in_progress",
    ("backlog_item", "status"): "in_progress",
    ("backlog_item", "priority"): "high",
    ("adr", "status"): "accepted",
    ("component", "kind"): "service",
}

#: A second value per field, for the writers: what an update sets.
SECOND: dict[str, Any] = {
    "created_at": "2027-01-02T03:04:05+00:00",
    "updated_at": "2027-01-03T03:04:05+00:00",
    "date": "2027-03-04",
    "start_date": "2027-03-05",
    "end_date": "2027-03-06",
    "due_date": "2027-03-07",
    "assigned_at": "2027-01-01T00:00:00+00:00",
    # Never the field's default: a cell whose second value reads as the bare
    # file does would pass with both twins reading that default.
    "assessed_at": "2027-01-01",
    "lifecycle": "draft",
    "research_status": "partial",
    "status": "reported",
    "source_type": "manual",
    "collection_type": "curated",
    "qa_status": "warn",
    "kind": "feature",
    "effort": "S",
    "url": "https://example.org/2",
    "priority": 3,
}
SECOND_OVERRIDES: dict[tuple[str, str], Any] = {
    ("task", "status"): "blocked",
    ("backlog_item", "status"): "done",
    ("backlog_item", "priority"): "low",
    ("adr", "status"): "deprecated",
    ("component", "kind"): "module",
}


def _sample(type_name: str, field: str) -> Any:
    return SAMPLE_OVERRIDES.get((type_name, field), SAMPLES[field])


def _second(type_name: str, field: str) -> Any:
    if (type_name, field) in SECOND_OVERRIDES:
        return SECOND_OVERRIDES[(type_name, field)]
    if field in SECOND:
        return SECOND[field]
    value = _sample(type_name, field)
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 1
    if isinstance(value, str):
        return value + "-2"
    if isinstance(value, list):
        return [*value, value[0]] if value and isinstance(value[0], dict) else [*value, "x2"]
    if isinstance(value, dict):
        return {**value, "k2": "v2"}
    raise AssertionError(value)


#: Declared fields a class's `from_frontmatter` never reads from the file at
#: ANY spelling (protocol mixin fields the class took but did not wire up):
#: nothing to lift, a finding outside #1025. `test_the_unread_fields_are_
#: exactly_these` keeps the list exact, so it can only shrink.
UNREAD: frozenset[tuple[str, str]] = frozenset(
    {
        ("person", "location"),
        ("person", "coordinates"),
        ("organization", "location"),
        ("organization", "coordinates"),
        ("event", "coordinates"),
        ("event", "start_date"),
        ("event", "end_date"),
        ("event", "due_date"),
        ("document", "start_date"),
        ("document", "end_date"),
        ("document", "due_date"),
        ("adr", "start_date"),
        ("adr", "end_date"),
        ("adr", "due_date"),
        ("task", "date"),
        ("task", "start_date"),
        ("task", "end_date"),
        ("task", "assigned_at"),
        ("backlog_item", "assigned_at"),
    }
)


def _read_fields(cls: type, type_name: str) -> list[str]:
    return [f for f in _fields(cls, type_name) if (type_name, f) not in UNREAD]


def _cells() -> list[Any]:
    """(type_name, field, spelling): every class x its declared fields x every
    spelling `spellings_of` gives for the field."""
    cells: list[Any] = []
    for type_name, cls in sorted(_classes().items()):
        for field in _read_fields(cls, type_name):
            for spelling in spellings_of(cls, type_name, field):
                cells.append((type_name, field, spelling))
    return cells


def _cell_id(cell: Any) -> str:
    type_name, field, spelling = cell
    return f"{type_name}:{field}:{'.'.join(spelling)}"


def _is_control(cell: Any) -> bool:
    _type_name, _field, spelling = cell
    return spelling[0] != "metadata"


_CONTROL = (
    "a top-level spelling (the field's own name or an alias) is where the reader "
    "always read; pinned so the grid states the property for every spelling "
    "`spellings_of` gives, and the nested ones are the evidence"
)


def _params(cells: list[Any]) -> list[Any]:
    return [
        pytest.param(
            cell,
            id=_cell_id(cell),
            marks=[pytest.mark.control(reason=_CONTROL)] if _is_control(cell) else [],
        )
        for cell in cells
    ]


# --------------------------------------------------------------------------
# Files and KBs
# --------------------------------------------------------------------------


def _file(entry_id: str, type_name: str, keyed: dict[str, Any], field: str | None) -> str:
    """A file with `id`, `type`, a title (unless `title` is the field under
    test, which then comes from `keyed` alone), the body, and `keyed`: each
    key a frontmatter line, its value as JSON (valid YAML flow style, so the
    two twins differ only in where the field is spelled)."""
    lines = [f"id: {entry_id}", f"type: {type_name}"]
    if field != "title":
        lines.append("title: T")
    for key, value in keyed.items():
        lines.append(f"{key}: {json.dumps(value)}")
    return "---\n" + "\n".join(lines) + "\n---\n\n" + BODY + "\n"


def _spelled(spelling: tuple[str, ...], value: Any) -> dict[str, Any]:
    if spelling[0] == "metadata":
        return {"metadata": {spelling[1]: value}}
    return {spelling[0]: value}


def _spelled_id(type_name: str, field: str, spelling: tuple[str, ...]) -> str:
    return f"{type_name}-{field}-{'-'.join(spelling)}".replace("_", "-")


def _twin_id(type_name: str, field: str) -> str:
    """The file spelling the field at its own top-level name."""
    return _spelled_id(type_name, field, (field,))


def _register_software_kb() -> None:
    from pyrite.plugins.registry import get_registry
    from pyrite_software_kb.plugin import SoftwareKBPlugin  # type: ignore[import-not-found]

    registry = get_registry()
    if "backlog_item" not in registry.get_all_entry_types():
        registry.register(SoftwareKBPlugin())


def _config(root: Path, kb_path: Path, type_name: str) -> PyriteConfig:
    kb_type = "software" if type_name in ("adr", "backlog_item", "component") else "generic"
    if kb_type == "software":
        _register_software_kb()
    return PyriteConfig(
        knowledge_bases=[KBConfig(name=KB, path=kb_path, kb_type=kb_type)],
        settings=Settings(index_path=root / "index.db", auto_embed=False),
    )


def _index(config: PyriteConfig) -> None:
    db = PyriteDB(config.settings.index_path)
    try:
        IndexManager(db, config).index_all()
    finally:
        db.close()


_KBS: dict[str, PyriteConfig] = {}


def _kb_for(type_name: str, tmp_path_factory: pytest.TempPathFactory) -> PyriteConfig:
    """One KB per class per worker, built once and never written: for every
    declared field, a file per spelling plus the top-level twin, and `bare`
    (no field spelled; the link target)."""
    if type_name not in _KBS:
        cls = _classes()[type_name]
        root = tmp_path_factory.mktemp(f"spell-{type_name}")
        kb_path = root / "kb"
        (kb_path / "entries").mkdir(parents=True)
        subprocess.run(
            ["git", "init", "-q"], cwd=kb_path, check=True, capture_output=True, timeout=30
        )
        (kb_path / "entries" / "bare.md").write_text(
            _file("bare", type_name, {}, None), encoding="utf-8"
        )
        for field in _fields(cls, type_name):
            value = _sample(type_name, field)
            for spelling in spellings_of(cls, type_name, field):
                entry_id = _spelled_id(type_name, field, spelling)
                (kb_path / "entries" / f"{entry_id}.md").write_text(
                    _file(entry_id, type_name, _spelled(spelling, value), field),
                    encoding="utf-8",
                )
        config = _config(root, kb_path, type_name)
        _index(config)
        _KBS[type_name] = config
    return _KBS[type_name]


# --------------------------------------------------------------------------
# The reads
# --------------------------------------------------------------------------

#: Keys a read carries that are the entry's own identity or the index's own
#: bookkeeping, never a field's value.
_NOT_COMPARED = {
    "id",
    "file_path",
    "content_hash",
    "indexed_at",
    "created_by",
    "modified_by",
    "metadata",
    "file_mtime_ns",
    "file_size",
}
_TIMESTAMPS = ("created_at", "updated_at")


def _plain(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _comparable(shape: dict[str, Any] | None, field: str) -> dict[str, str]:
    """A read's field values, as JSON text. A timestamp the file does not carry
    is the time of indexing, so the other timestamp is left out; a source row
    carries its own row id and its entry's id, which are not the value."""
    assert shape is not None, "the read found nothing"
    out: dict[str, str] = {}
    for key, value in shape.items():
        if key in _NOT_COMPARED:
            continue
        if key in _TIMESTAMPS and key != field:
            continue
        if key == "sources" and isinstance(value, list):
            value = [
                {k: v for k, v in s.items() if k not in ("id", "entry_id")}
                if isinstance(s, dict)
                else s
                for s in value
            ]
        out[key] = _plain(value)
    return out


def _attribute(entry: Any, field: str) -> str:
    """The model attribute holding `field`'s value: the field's own name, or
    the alias the class keeps as its attribute (`participants` for `actors`)."""
    if hasattr(entry, field):
        return field
    for alias, target in class_field_aliases(type(entry), entry.entry_type).items():
        if target == field and hasattr(entry, alias):
            return alias
    raise AssertionError(f"{type(entry).__name__} has no attribute for {field}")


def _model(config: PyriteConfig, entry_id: str, field: str) -> tuple[dict[str, str], str]:
    """The model: every public dataclass attribute that is a field (the
    identity, the `metadata` bag and the bookkeeping left out), and the
    field's own attribute value."""
    entry = KBRepository(config.knowledge_bases[0]).load(entry_id)
    assert entry is not None, f"{entry_id}: the model did not load"
    shape = {
        f.name: getattr(entry, f.name)
        for f in dataclasses.fields(entry)
        if not f.name.startswith("_") and f.name not in _NOT_A_FIELD
    }
    attr = _attribute(entry, field)
    return _comparable(shape, field), _plain(getattr(entry, attr))


def _row(config: PyriteConfig, entry_id: str) -> dict[str, Any]:
    db = PyriteDB(config.settings.index_path)
    try:
        row = db.get_entry(entry_id, KB)
    finally:
        db.close()
    assert row is not None, f"{entry_id}: no row"
    return row


def _get(config: PyriteConfig, entry_id: str) -> dict[str, Any]:
    db = PyriteDB(config.settings.index_path)
    try:
        read = KBService(config, db).get_entry(entry_id, KB, readable_kbs=UNSCOPED)
    finally:
        db.close()
    assert read is not None, f"{entry_id}: get found nothing"
    return read


def _search_hit(config: PyriteConfig, entry_id: str) -> dict[str, Any]:
    """The entry's row among the hits for the body every file shares."""
    db = PyriteDB(config.settings.index_path)
    try:
        hits = db.search("Shared", kb_name=KB, limit=1000, include_archived=True)
    finally:
        db.close()
    by_id = {hit["id"]: hit for hit in hits}
    assert entry_id in by_id, f"{entry_id}: not a search hit ({sorted(by_id)[:5]}...)"
    return by_id[entry_id]


# --------------------------------------------------------------------------
# The grid: a nested spelling reads as the top-level twin, on every surface
# --------------------------------------------------------------------------


@pytest.mark.parametrize("cell", _params(_cells()))
def test_a_field_reads_the_same_at_every_spelling(
    cell: Any, tmp_path_factory: pytest.TempPathFactory
):
    """For every class, field and spelling: the model (`KBRepository.load`),
    the index row (`PyriteDB.get_entry`), the single-entry read
    (`KBService.get_entry`) and a search hit of the file spelling the field at
    `spelling` equal those of the twin spelling it at the field's own name,
    key for key; and the nested spelling stays under `metadata` (both)."""
    type_name, field, spelling = cell
    config = _kb_for(type_name, tmp_path_factory)
    spelled = _spelled_id(type_name, field, spelling)
    twin = _twin_id(type_name, field)

    twin_model, twin_value = _model(config, twin, field)
    _bare_model, bare_value = _model(config, "bare", field)
    assert twin_value != bare_value, (
        f"SAMPLES[{field!r}] = {_sample(type_name, field)!r} is not read as given at the top "
        f"level on a {type_name} (reads {twin_value}, the bare file {bare_value}): the cell "
        "would pass with both twins reading a default; fix the sample"
    )

    spelled_model, _ = _model(config, spelled, field)
    assert spelled_model == twin_model, "the model"
    assert _comparable(_row(config, spelled), field) == _comparable(_row(config, twin), field), (
        "the index row"
    )
    assert _comparable(_get(config, spelled), field) == _comparable(_get(config, twin), field), (
        "the single-entry read"
    )
    # A keyword hit carries the FTS score as `rank`, a key a backlog item's
    # `rank` field shares (a finding, not this theme's); the score differs per
    # row, so it is left out here and the field is read on the other surfaces.
    spelled_hit = _comparable(_search_hit(config, spelled), field)
    twin_hit = _comparable(_search_hit(config, twin), field)
    spelled_hit.pop("rank", None)
    twin_hit.pop("rank", None)
    assert spelled_hit == twin_hit, "a search hit"

    if spelling[0] == "metadata":
        # The read shape (maintainer decision 2026-10-20, option B): the
        # single-entry read spells a declared field once, at its own name,
        # and its `metadata` holds no spelling of it; the index row (the
        # storage dict the plugins read) keeps the bag as stored.
        expected = _plain(json.loads(json.dumps(_sample(type_name, field))))
        row_bag = _row(config, spelled).get("metadata") or {}
        assert _plain(row_bag.get(spelling[1])) == expected, "the row's bag lost the spelling"
        read_bag = _get(config, spelled).get("metadata") or {}
        assert spelling[1] not in read_bag and field not in read_bag, (
            f"the single-entry read spells {field} under `metadata` too: {read_bag}"
        )


@pytest.mark.control(
    reason="a declared field the class never reads from its frontmatter is a gap outside "
    "#1025, with or without the lift; the list is pinned so it can only shrink"
)
@pytest.mark.parametrize("type_name", sorted(_classes()))
def test_the_unread_fields_are_exactly_these(
    type_name: str, tmp_path_factory: pytest.TempPathFactory
):
    """``UNREAD`` names each declared field whose top-level sample reads as the
    bare file does; every other field reads its sample. A class that starts
    reading one of them, or stops reading another, changes this list."""
    cls = _classes()[type_name]
    config = _kb_for(type_name, tmp_path_factory)
    unread = set()
    for field in _fields(cls, type_name):
        _twin, twin_value = _model(config, _twin_id(type_name, field), field)
        _bare, bare_value = _model(config, "bare", field)
        if twin_value == bare_value:
            unread.add((type_name, field))
    assert unread == {u for u in UNREAD if u[0] == type_name}


# --------------------------------------------------------------------------
# Health: a nested spelling is not a double spelling, nor a mismatch
# --------------------------------------------------------------------------

#: `check_health` lists that name files or entries: all empty for a KB whose
#: every file spells each field once.
_PER_FILE_HEALTH = (
    "double_spellings",
    "unindexed_files",
    "stale_entries",
    "content_changed",
    "missing_files",
    "unmodellable_frontmatter",
    "malformed_files",
)


@pytest.mark.control(
    reason="health reads the raw frontmatter and reported nothing for a nested-only file before "
    "the lift too; pinned so the lift makes such a file neither a double spelling nor a mismatch"
)
@pytest.mark.parametrize("type_name", sorted(_classes()))
def test_health_reports_nothing_for_a_kb_spelling_each_field_once(
    type_name: str, tmp_path_factory: pytest.TempPathFactory
):
    config = _kb_for(type_name, tmp_path_factory)
    db = PyriteDB(config.settings.index_path)
    try:
        health = IndexManager(db, config).check_health(kb_name=KB)
    finally:
        db.close()
    for key in _PER_FILE_HEALTH:
        assert not health.get(key), (key, health.get(key))
    for key, value in health.items():
        if isinstance(value, list) and "mismatch" in key:
            assert not value, (key, value)


# --------------------------------------------------------------------------
# The writers: an update lands at the file's spelling and reads back
# --------------------------------------------------------------------------


def _writer_cells() -> list[Any]:
    """(type_name, field): every class x every declared field an update may
    set (the managed fields are refused by design; the spelling grid in
    tests/test_metadata_is_a_spelling_not_a_field.py covers that)."""
    cells = []
    for type_name, cls in sorted(_classes().items()):
        managed = _MANAGED_FIELDS | frozenset(getattr(cls, "managed_fields", frozenset()))
        for field in _read_fields(cls, type_name):
            if field not in managed:
                cells.append((type_name, field))
    return cells


def _update(config: PyriteConfig, entry_id: str, field: str, value: Any) -> str | None:
    """One update through the service; the refusal's text, or None."""
    db = PyriteDB(config.settings.index_path)
    try:
        KBService(config, db).update(entry_id, KB, {field: value})
    except PyriteError as e:
        return type(e).__name__
    finally:
        db.close()
    return None


@pytest.mark.parametrize("cell", [pytest.param(c, id=f"{c[0]}:{c[1]}") for c in _writer_cells()])
def test_an_update_of_a_nested_field_reads_back_like_the_top_level_twin(cell: Any, tmp_path: Path):
    """`update -f k=v` on a file spelling `k` only under `metadata:` writes
    there (ADR-0042 decision 12), adds no top-level `k:`, and the model and
    the row then read `v`, as they do for the twin updated at the top level.
    The cold read's reproduction: update then get disagreed."""
    type_name, field = cell
    cls = _classes()[type_name]
    kb_path = tmp_path / "kb"
    (kb_path / "entries").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=kb_path, check=True, capture_output=True, timeout=30)
    value = _sample(type_name, field)
    nested_path = kb_path / "entries" / "nested.md"
    nested_path.write_text(
        _file("nested", type_name, _spelled(("metadata", field), value), field), encoding="utf-8"
    )
    (kb_path / "entries" / "top.md").write_text(
        _file("top", type_name, {field: value}, field), encoding="utf-8"
    )
    config = _config(tmp_path, kb_path, type_name)
    _index(config)

    second = _second(type_name, field)
    top_refusal = _update(config, "top", field, second)
    nested_refusal = _update(config, "nested", field, second)
    assert nested_refusal == top_refusal, "the twins were not refused alike"
    after = nested_path.read_text(encoding="utf-8")
    assert f"\n{field}:" not in after, f"a top-level {field}: was added beside metadata.{field}"
    if top_refusal is None:
        assert "metadata:" in after, after

    assert _model(config, "nested", field) == _model(config, "top", field), "the model"
    assert _comparable(_row(config, "nested"), field) == _comparable(_row(config, "top"), field), (
        "the index row"
    )
    assert _comparable(_get(config, "nested"), field) == _comparable(_get(config, "top"), field), (
        "the single-entry read"
    )
    del cls


# --------------------------------------------------------------------------
# Acceptance: update then get agree, on every surface, and an echo is a no-op
# --------------------------------------------------------------------------

NESTED_IMPORTANCE = "---\nid: n1\ntitle: N1\ntype: note\nmetadata:\n  importance: 3\n---\n\nText.\n"


def _note_kb(tmp_path: Path, text: str) -> tuple[PyriteConfig, Path]:
    kb_path = tmp_path / "kb"
    (kb_path / "notes").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=kb_path, check=True, capture_output=True, timeout=30)
    path = kb_path / "notes" / "n1.md"
    path.write_text(text, encoding="utf-8")
    config = _config(tmp_path, kb_path, "note")
    _index(config)
    return config, path


def _cli(config: PyriteConfig, *args: str) -> Any:
    with (
        patch("pyrite.cli.load_config", return_value=config),
        patch("pyrite.cli.context.load_config", return_value=config),
    ):
        result = CliRunner().invoke(cli_app, [*args, "--format", "json"])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def _mcp_get(config: PyriteConfig, entry_id: str) -> dict[str, Any]:
    from pyrite.server.mcp_server import PyriteMCPServer

    server = PyriteMCPServer(config, tier="read")
    try:
        result = server._dispatch_tool("kb_get", {"kb_name": KB, "entry_id": entry_id})
    finally:
        server.close()
    assert not result.get("error") and not result.get("isError"), result
    return result["entry"]


def _rest_get(config: PyriteConfig, entry_id: str) -> dict[str, Any]:
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
        res = client.get(f"/api/entries/{entry_id}", params={"kb": KB})
    finally:
        client.close()
        db.close()
    assert res.status_code == 200, res.text
    return res.json()


def test_update_then_get_agree_on_a_field_spelled_only_under_metadata(tmp_path: Path):
    """#1025 as filed: `pyrite update n1 -f importance=8` on a file spelling
    `importance` only under `metadata:` writes `metadata.importance` (where
    the file spells it) and `get` on the CLI, MCP and REST then shows 8."""
    config, path = _note_kb(tmp_path, NESTED_IMPORTANCE)

    _cli(config, "update", "n1", "-k", KB, "-f", "importance=8")

    after = path.read_text(encoding="utf-8")
    assert "metadata:\n  importance: 8\n" in after, after
    assert "\nimportance:" not in after, "a second, top-level importance was added"
    assert _cli(config, "get", "n1", "-k", KB)["importance"] == 8
    assert _mcp_get(config, "n1")["importance"] == 8
    assert _rest_get(config, "n1")["importance"] == 8


def _mcp_update(config: PyriteConfig, args: dict[str, Any]) -> dict[str, Any]:
    from pyrite.server.mcp_server import PyriteMCPServer

    server = PyriteMCPServer(config, tier="write")
    try:
        result = server._dispatch_tool("kb_update", args)
    finally:
        server.close()
    if result.get("error") or result.get("isError"):
        raise ValidationError(str(result))
    return result


def _rest_put(config: PyriteConfig, entry_id: str, body: dict[str, Any]) -> Any:
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
        res = client.put(f"/api/entries/{entry_id}", json={"kb": KB, **body})
    finally:
        client.close()
        db.close()
    assert res.status_code == 200, res.text
    return res.json()


def test_a_single_read_spells_a_nested_field_once_and_an_echo_is_a_no_op(tmp_path: Path):
    """Option B: a read of a file spelling `importance` only under `metadata:`
    carries `importance: 3` and an empty `metadata`; echoing the read back
    (the whole of it through MCP `kb_update`, or the one key through the
    service) leaves the file's bytes unchanged."""
    config, path = _note_kb(tmp_path, NESTED_IMPORTANCE)
    read = _get(config, "n1")
    assert read["importance"] == 3 and read["metadata"] == {}, read
    before = path.read_bytes()

    db = PyriteDB(config.settings.index_path)
    try:
        KBService(config, db).update("n1", KB, {"importance": read["importance"]})
    finally:
        db.close()
    assert path.read_bytes() == before, "echoing the top-level key rewrote the file"
    _mcp_update(config, {"kb_name": KB, "entry_id": "n1", **_mcp_get(config, "n1")})
    assert path.read_bytes() == before, "echoing the whole read rewrote the file"


# --------------------------------------------------------------------------
# Round trips from the decision packet: read, edit, send back
# --------------------------------------------------------------------------

PROVENANCE_NOTE = (
    "---\nid: n3\ntitle: N3\ntype: note\nprovenance:\n  created_by: me\n"
    "metadata:\n  reviewed: false\n---\n\nText.\n"
)
EVIDENCE_TASK = (
    "---\nid: t2\ntitle: T2\ntype: task\nstatus: open\nevidence:\n- e1\n"
    "metadata:\n  reviewed: false\n---\n\nText.\n"
)


def _kb_with(tmp_path: Path, text: str, entry_id: str) -> tuple[PyriteConfig, Path]:
    kb_path = tmp_path / "kb"
    (kb_path / "notes").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=kb_path, check=True, capture_output=True, timeout=30)
    path = kb_path / "notes" / f"{entry_id}.md"
    path.write_text(text, encoding="utf-8")
    config = _config(tmp_path, kb_path, "note")
    _index(config)
    return config, path


def test_a_single_read_spells_a_declared_field_once_and_the_row_keeps_its_bag(tmp_path: Path):
    """A task spelling `evidence` and `provenance` at the top level: the
    single-entry read has each at its own name and `metadata` holds only the
    undeclared `reviewed`; the index row (the storage dict about 45 plugin
    call sites read declared fields from) keeps them in its bag."""
    config, _path = _kb_with(
        tmp_path,
        EVIDENCE_TASK.replace("metadata:", "provenance:\n  created_by: me\nmetadata:"),
        "t2",
    )
    read = _get(config, "t2")
    assert read["evidence"] == ["e1"] and read["provenance"]["created_by"] == "me", read
    assert read["metadata"] == {"reviewed": False}, read
    row_bag = _row(config, "t2")["metadata"]
    assert row_bag["evidence"] == ["e1"] and "provenance" in row_bag, row_bag


def test_rest_put_of_a_reads_metadata_with_a_key_added_writes_it(tmp_path: Path):
    """The blocking item: on a note with `provenance`, PUT the read's
    `metadata` back with one key added. It is written, nothing refused."""
    config, path = _kb_with(tmp_path, PROVENANCE_NOTE, "n3")
    read = _rest_get(config, "n3")
    assert "provenance" not in read["metadata"], read["metadata"]

    _rest_put(config, "n3", {"metadata": {**read["metadata"], "reviewed_by": "me"}})

    after = _get(config, "n3")
    assert after["metadata"]["reviewed_by"] == "me", after["metadata"]
    assert after["provenance"] == {"created_by": "me"} or after["provenance"]["created_by"] == "me"
    text = path.read_text(encoding="utf-8")
    assert "provenance:\n  created_by: me\n" in text
    # The file's spelling: a key the file did not hold lands at the top level
    # (decision 6, as on dev), not under `metadata:`.
    assert "\nreviewed_by: me\n" in text and "  reviewed_by:" not in text, text


@pytest.mark.control(
    reason="passes on dev, where no read carried provenance; refused on this PR's first head "
    "(the read shape 'both'), pinned so option B keeps the echo writable"
)
def test_mcp_echo_of_a_whole_read_with_a_metadata_key_added_writes_it(tmp_path: Path):
    config, path = _kb_with(tmp_path, PROVENANCE_NOTE, "n3")
    read = _mcp_get(config, "n3")
    read["metadata"] = {**read["metadata"], "reviewed_by": "me"}

    _mcp_update(config, {"kb_name": KB, "entry_id": "n3", **read})

    assert _get(config, "n3")["metadata"]["reviewed_by"] == "me"
    text = path.read_text(encoding="utf-8")
    assert "\nreviewed_by: me\n" in text and "  reviewed_by:" not in text, text


def test_mcp_echo_of_a_whole_read_with_importance_changed_on_a_nested_note(tmp_path: Path):
    config, path = _note_kb(tmp_path, NESTED_IMPORTANCE)
    read = _mcp_get(config, "n1")
    read["importance"] = 8

    _mcp_update(config, {"kb_name": KB, "entry_id": "n1", **read})

    assert _get(config, "n1")["importance"] == 8
    after = path.read_text(encoding="utf-8")
    assert "metadata:\n  importance: 8\n" in after and "\nimportance:" not in after, after


def test_mcp_echo_editing_a_top_level_field_and_a_metadata_key_on_a_task(tmp_path: Path):
    """A task spelling `evidence` at the top level: the whole read, with the
    title edited and a `metadata` key added, is written; `evidence` (a managed
    field) rides along at its own name only, so no spelling of it is sent
    under `metadata` and nothing is refused."""
    config, path = _kb_with(tmp_path, EVIDENCE_TASK, "t2")
    read = _mcp_get(config, "t2")
    read["title"] = "T2 edited"
    read["metadata"] = {**read["metadata"], "reviewed_by": "me"}

    _mcp_update(config, {"kb_name": KB, "entry_id": "t2", **read})

    after = _get(config, "t2")
    assert after["title"] == "T2 edited" and after["metadata"]["reviewed_by"] == "me", after
    text = path.read_text(encoding="utf-8")
    assert "evidence:\n- e1\n" in text
    assert "\nreviewed_by: me\n" in text and "  reviewed_by:" not in text, text


# --------------------------------------------------------------------------
# Every field a single-entry read returns reaches every surface
# --------------------------------------------------------------------------


def _surface_reads(config: PyriteConfig, entry_id: str) -> dict[str, dict[str, Any]]:
    """The entry as the service reads it and as REST `GET`, MCP `kb_get` and
    the CLI's JSON return it, each as JSON values."""
    read = json.loads(json.dumps(_get(config, entry_id), default=str))
    return {
        "read": read,
        "rest": _rest_get(config, entry_id),
        "mcp": json.loads(json.dumps(_mcp_get(config, entry_id), default=str)),
        "cli": _cli(config, "get", entry_id, "-k", KB),
    }


@pytest.mark.parametrize("type_name", sorted(_classes()))
def test_every_field_a_read_returns_reaches_every_surface(
    type_name: str, tmp_path_factory: pytest.TempPathFactory
):
    """The property (conductor's delta review, round 2): every field the
    single-entry read returns reaches REST `GET /api/entries/{id}`, MCP
    `kb_get` and `pyrite get --format json`, with the same key and value. A
    read spells a declared field at its own name (option B), so a surface
    with a fixed field list (`EntryResponse`) dropped a task's
    `status_reason`, `evidence` and `parked_awaiting`, a backlog item's
    `rank`, `effort` and `kind`, and every type's `provenance`. One entry per
    registered type, every declared field set."""
    config = _kb_for(type_name, tmp_path_factory)
    cls = _classes()[type_name]
    # The entry spelling every declared field at its own name is the grid's
    # KB itself: one file per field. The cross-surface check wants one entry
    # with them all, so build it from the samples.
    root = tmp_path_factory.mktemp(f"surfaces-{type_name}")
    kb_path = root / "kb"
    (kb_path / "entries").mkdir(parents=True)
    fields = {f: _sample(type_name, f) for f in _read_fields(cls, type_name) if f != "title"}
    fields["title"] = "All fields"
    (kb_path / "entries" / "all.md").write_text(
        _file("all", type_name, fields, "title"), encoding="utf-8"
    )
    (kb_path / "entries" / "bare.md").write_text(
        _file("bare", type_name, {}, None), encoding="utf-8"
    )
    # A field spelled only under `metadata:` with a value the column
    # normalises (`importance: '8'`, a string the model reads as 8): the
    # read spells it once, at its own name, and `metadata` is empty of it
    # (round-2 delta review: the value comparison let it show twice).
    (kb_path / "entries" / "nested.md").write_text(
        _file("nested", type_name, {"metadata": {"importance": "8"}}, None), encoding="utf-8"
    )
    config = _config(root, kb_path, type_name)
    _index(config)

    for entry_id in ("all", "nested"):
        shapes = _surface_reads(config, entry_id)
        read = shapes.pop("read")
        if entry_id == "nested":
            assert read["importance"] == 8, read
            assert "importance" not in read["metadata"], read["metadata"]
        for name, shape in shapes.items():
            missing = [k for k in read if k not in shape]
            assert not missing, f"{name} drops {missing} of the read of {entry_id}"
            differing = {k: (read[k], shape[k]) for k in read if shape[k] != read[k]}
            assert not differing, f"{name} differs from the read of {entry_id} on {differing}"


BOOKKEEPING = ("created_by", "modified_by", "indexed_at", "assignee")

# The one key REST `GET /api/entries/{id}` has that the read does not produce
# for an entry whose type has no such field: `EntryResponse` defaults
# `participants` to `[]`, and the web client reads `entry.participants.length`
# unguarded (web/src/lib/components/entry/EntryMeta.svelte), so dropping it is
# a web change, with the maintainer (round 3). Every other REST key is the
# read's; a new REST-only key fails here.
REST_ONLY = frozenset({"participants"})

A_TASK = (
    "---\nid: t1\ntitle: T1\ntype: task\nstatus: in_progress\nassignee: ada\n"
    "metadata:\n  status_reason: waiting on review\n  parked_awaiting: '#1'\n---\n\nText.\n"
)


def _anonymous_rest_get(config: PyriteConfig, entry_id: str) -> dict[str, Any]:
    """REST `GET /api/entries/{id}` as an anonymous visitor of a public KB:
    auth on, `anonymous_tier: read`, the KB's `default_role: read`, and a
    client with no credential."""
    pytest.importorskip("fastapi", reason="fastapi not installed")
    from fastapi.testclient import TestClient

    from pyrite.server.api import create_app, get_config, get_db

    db = PyriteDB(config.settings.index_path)
    rest = create_app(config)
    rest.dependency_overrides[get_config] = lambda: config
    rest.dependency_overrides[get_db] = lambda: db
    client = TestClient(rest, raise_server_exceptions=False)
    try:
        res = client.get(f"/api/entries/{entry_id}", params={"kb": KB})
    finally:
        client.close()
        db.close()
    assert res.status_code == 200, res.text
    return res.json()


def test_rest_get_and_mcp_kb_get_return_the_same_key_set(tmp_path: Path):
    """The maintainer's decision (round 3, option A): a single-entry read
    returns every key the read produces, the same set on REST `GET
    /api/entries/{id}` and MCP `kb_get`, the bookkeeping fields included
    (`created_by`, `modified_by`, `indexed_at`, `assignee`), and an
    anonymous REST reader of a public KB sees the same keys. One committed
    task, so git attribution fills `created_by` and `modified_by`. The one
    REST-only key today is `REST_ONLY` (above)."""
    kb_path = tmp_path / "kb"
    (kb_path / "tasks").mkdir(parents=True)
    (kb_path / "tasks" / "t1.md").write_text(A_TASK, encoding="utf-8")
    env = {"GIT_AUTHOR_NAME": "Ada", "GIT_AUTHOR_EMAIL": "ada@example.org"}
    env |= {"GIT_COMMITTER_NAME": "Ada", "GIT_COMMITTER_EMAIL": "ada@example.org"}
    for cmd in (
        ["git", "init", "-q"],
        ["git", "add", "tasks/t1.md"],
        ["git", "commit", "-qm", "t1"],
    ):
        subprocess.run(cmd, cwd=kb_path, check=True, capture_output=True, timeout=30, env=env)
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name=KB, path=kb_path, kb_type="generic", default_role="read")],
        settings=Settings(
            index_path=tmp_path / "index.db",
            auto_embed=False,
            auth=AuthConfig(enabled=True, allow_registration=True, anonymous_tier="read"),
        ),
    )
    from pyrite.services.git_service import GitService

    db = PyriteDB(config.settings.index_path)
    try:
        IndexManager(db, config).index_with_attribution(KB, GitService())
    finally:
        db.close()

    rest = _anonymous_rest_get(config, "t1")
    mcp = json.loads(json.dumps(_mcp_get(config, "t1"), default=str))

    assert set(rest) - set(mcp) <= REST_ONLY, sorted(set(rest) - set(mcp) - REST_ONLY)
    assert set(mcp) <= set(rest), sorted(set(mcp) - set(rest))
    assert set(BOOKKEEPING) <= set(mcp), sorted(set(BOOKKEEPING) - set(mcp))
    assert rest["created_by"] == rest["modified_by"] == "Ada"
    assert rest["assignee"] == mcp["assignee"] == "ada"
    assert rest["status_reason"] == mcp["status_reason"] == "waiting on review"
    assert rest["parked_awaiting"] == mcp["parked_awaiting"] == "#1"
    assert "status_reason" not in rest["metadata"] and "parked_awaiting" not in rest["metadata"]


# --------------------------------------------------------------------------
# `metadata.links` is indexed, and `pyrite link` appends there
# --------------------------------------------------------------------------

NESTED_LINKS = (
    "---\nid: n1\ntitle: N1\ntype: note\nmetadata:\n  links:\n  - target: n2\n"
    "    relation: related_to\n---\n\nText.\n"
)
N2 = "---\nid: n2\ntitle: N2\ntype: note\n---\n\nText.\n"


def test_links_spelled_under_metadata_are_outlinks_and_backlinks(tmp_path: Path):
    config, _path = _note_kb(tmp_path, NESTED_LINKS)
    (config.knowledge_bases[0].path / "notes" / "n2.md").write_text(N2, encoding="utf-8")
    _index(config)
    db = PyriteDB(config.settings.index_path)
    try:
        out = {(l["id"], l["relation"]) for l in db.get_outlinks("n1", KB, readable_kbs=None)}
        back = {l["id"] for l in db.get_backlinks("n2", KB, readable_kbs=None)}
    finally:
        db.close()
    assert out == {("n2", "related_to")}, out
    assert back == {"n1"}, back


def test_pyrite_link_appends_where_the_file_spells_links(tmp_path: Path):
    """The #975 refusal lifted: a link on a file spelling `links` only under
    `metadata:` is appended there, indexed, and a repeat is a no-op."""
    config, path = _note_kb(tmp_path, NESTED_LINKS)
    (config.knowledge_bases[0].path / "notes" / "n2.md").write_text(N2, encoding="utf-8")
    (config.knowledge_bases[0].path / "notes" / "n3.md").write_text(
        N2.replace("n2", "n3").replace("N2", "N3"), encoding="utf-8"
    )
    _index(config)

    _cli(config, "link", "n1", "n3", "-k", KB, "--relation", "related_to")
    _cli(config, "link", "n1", "n3", "-k", KB, "--relation", "related_to")

    after = path.read_text(encoding="utf-8")
    assert after.count("target: n3") == 1, after
    assert "\nlinks:" not in after, "a second, top-level links was added"
    db = PyriteDB(config.settings.index_path)
    try:
        out = {l["id"] for l in db.get_outlinks("n1", KB, readable_kbs=None)}
    finally:
        db.close()
    assert out == {"n2", "n3"}, out


# --------------------------------------------------------------------------
# In-repo readers of a single-entry read take a declared field at its own name
# --------------------------------------------------------------------------

QUERY_COLLECTION = (
    "---\nid: c1\ntitle: C1\ntype: collection\nsource_type: query\n"
    "query: 'type:note'\nicon: star\n---\n\nA stored query.\n"
)


@pytest.mark.control(
    reason="passes on dev, where the collection readers found the fields in the read's bag; "
    "failed on this PR's head 8ae4625 once the single-entry read spelled them at their own "
    "names only, pinned so the readers follow the read shape"
)
def test_a_query_collection_reads_its_fields_at_their_own_names(tmp_path: Path):
    """`KBService.get_collection_entries` and REST `GET /api/collections/{id}`
    took a collection's `source_type`, `query`, `icon`... from the read's
    `metadata`; a single-entry read spells them at their own names now, so
    both read them there (CI on the first round-1 head: three stored-query
    tests returned no rows)."""
    pytest.importorskip("fastapi", reason="fastapi not installed")
    from fastapi.testclient import TestClient

    from pyrite.server.api import create_app, get_config, get_db, get_index_mgr

    config, _path = _note_kb(tmp_path, QUERY_COLLECTION.replace("c1", "n1"))
    (config.knowledge_bases[0].path / "notes" / "n2.md").write_text(N2, encoding="utf-8")
    _index(config)
    db = PyriteDB(config.settings.index_path)
    try:
        svc = KBService(config, db)
        entries, total = svc.get_collection_entries("n1", KB, readable_kbs=UNSCOPED)
        assert total >= 1 and {e["id"] for e in entries} >= {"n2"}, (total, entries)
        index_mgr = IndexManager(db, config)
        rest = create_app(config)
        rest.dependency_overrides[get_config] = lambda: config
        rest.dependency_overrides[get_db] = lambda: db
        rest.dependency_overrides[get_index_mgr] = lambda: index_mgr
        client = TestClient(rest, raise_server_exceptions=False)
        try:
            res = client.get("/api/collections/n1", params={"kb": KB})
        finally:
            client.close()
    finally:
        db.close()
    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["source_type"], body["query"], body["icon"]) == ("query", "type:note", "star"), (
        body
    )


# --------------------------------------------------------------------------
# The five hand-lifted keys are read by the rule; the bag keeps the rest
# --------------------------------------------------------------------------

BACKLOG = (
    "---\nid: b1\ntitle: B1\ntype: backlog_item\nkind: bug\nstatus: proposed\n"
    "priority: high\neffort: M\nrank: 3\n---\n\nText.\n"
)
TASK = (
    "---\nid: t1\ntitle: T1\ntype: task\nstatus: blocked\nstatus_reason: why\n"
    "assignee: ann\nparked_awaiting: rfp\ncustom_note: kept\n---\n\nText.\n"
)


def test_a_declared_field_the_row_keeps_in_its_bag_is_read_at_its_own_name(tmp_path: Path):
    """`rank`, `effort` and `kind` are fields of a backlog item, `status_reason`
    and `parked_awaiting` of a task (declared on `TaskEntry` by the
    maintainer's decision of 2026-10-20, so the task tools keep reading it at
    the top): a read spells each at its own name, the row's bag still holds
    it, and the single-entry read's `metadata` does not. An undeclared key
    (`custom_note`) stays in the bag and is not lifted. `TaskService.list_tasks`
    surfaces `parked_awaiting` with or without an assignee filter. No
    hand-written list decides this (`_LIFTED_METADATA_KEYS`)."""
    from pyrite.services.task_service import TaskService

    kb_path = tmp_path / "kb"
    (kb_path / "entries").mkdir(parents=True)
    (kb_path / "entries" / "b1.md").write_text(BACKLOG, encoding="utf-8")
    (kb_path / "entries" / "t1.md").write_text(TASK, encoding="utf-8")
    config = _config(tmp_path, kb_path, "backlog_item")
    _index(config)
    for name, shape in (("row", _row(config, "b1")), ("get", _get(config, "b1"))):
        assert (shape["rank"], shape["effort"], shape["kind"]) == (3, "M", "bug"), (name, shape)
    for name, shape in (("row", _row(config, "t1")), ("get", _get(config, "t1"))):
        assert shape["status_reason"] == "why", (name, shape)
        assert shape["parked_awaiting"] == "rfp", (name, shape)
        assert "custom_note" not in shape, (name, "an undeclared key was lifted")
        assert shape["metadata"]["custom_note"] == "kept", (name, shape)
    assert "parked_awaiting" in _row(config, "t1")["metadata"], "the row's bag lost the field"
    assert "parked_awaiting" not in _get(config, "t1")["metadata"], "spelled twice in a read"
    db = PyriteDB(config.settings.index_path)
    try:
        svc = TaskService(config, db)
        unfiltered = {t["id"]: t for t in svc.list_tasks(kb_name=KB)}
        filtered = {t["id"]: t for t in svc.list_tasks(kb_name=KB, assignee="ann")}
    finally:
        db.close()
    assert unfiltered["t1"]["parked_awaiting"] == "rfp"
    assert filtered["t1"]["parked_awaiting"] == "rfp"


def test_the_hand_written_list_of_lifted_keys_is_gone():
    import pyrite.storage.index as index_module

    assert not hasattr(index_module, "_LIFTED_METADATA_KEYS")
    backend = (ROOT / "pyrite/storage/backends/base_backend.py").read_text(encoding="utf-8")
    for literal in ('"rank"', '"status_reason"', '"parked_awaiting"'):
        assert literal not in backend, f"{literal}: a hand-lifted key in the backend"


# --------------------------------------------------------------------------
# Regimes: a double spelling, and the repo's own files
# --------------------------------------------------------------------------


@pytest.mark.control(
    reason="a double spelling read the top-level value and was reported before the lift; "
    "pinned so the lift changes neither"
)
def test_a_double_spelling_still_reads_the_top_level_and_is_reported(tmp_path: Path):
    config, _path = _note_kb(
        tmp_path,
        "---\nid: n1\ntitle: N1\ntype: note\nimportance: 7\nmetadata:\n  importance: 8\n"
        "---\n\nText.\n",
    )
    assert _get(config, "n1")["importance"] == 7
    db = PyriteDB(config.settings.index_path)
    try:
        health = IndexManager(db, config).check_health(kb_name=KB)
    finally:
        db.close()
    assert [d["field"] for d in health["double_spellings"]] == ["importance"], health


@pytest.mark.control(
    reason="passes on dev, whose read's metadata is the whole bag; the first option-B head "
    "dropped the second value (the delta review's finding 2), pinned so a read never loses it"
)
def test_a_double_spelling_read_keeps_the_second_value_under_metadata(tmp_path: Path):
    """The amendment: a read never loses a value the file holds. A file
    spelling `importance` twice reads the top-level value at its own name
    (decision 3) and the nested value stays under `metadata`, where `index
    health` reports the double spelling; only a nested spelling the read
    already carries at its own name, with the same value, leaves `metadata`."""
    config, _path = _note_kb(
        tmp_path,
        "---\nid: n1\ntitle: N1\ntype: note\nimportance: 7\nmetadata:\n  importance: 8\n"
        "---\n\nText.\n",
    )
    read = _get(config, "n1")
    assert read["importance"] == 7, read
    assert read["metadata"] == {"importance": 8}, read["metadata"]
    assert _row(config, "n1")["metadata"] == {"importance": 8}


REPO_FILES = {
    "kb/notes/pyrite-user-personas-five-surfaces-five-mental-models.md": ("importance", 8),
}
REPO_CONTROLS = {
    # The nested value equals the field's default, so the read agrees with or
    # without the lift; pinned for the file staying as it is.
    "kb/backlog/done/kb-compaction-and-entry-lifecycle.md": ("priority", "medium"),
    "kb/backlog/done/entry-lifecycle-field-and-search-filtering.md": ("priority", "medium"),
}


def _repo_file_reads(rel: str, field: str, value: Any) -> None:
    path = ROOT / rel
    if not path.exists():
        pytest.skip(f"{rel} is not in this checkout")
    _register_software_kb()
    before = path.read_bytes()
    entry = KBRepository(KBConfig(name="pyrite", path=ROOT / "kb", kb_type="software"))._load_entry(
        path
    )
    assert getattr(entry, field) == value, (rel, field, getattr(entry, field))
    assert path.read_bytes() == before, "reading rewrote the file"


@pytest.mark.parametrize("rel", sorted(REPO_FILES))
def test_a_repo_file_spelling_a_field_under_metadata_reads_it(rel: str):
    _repo_file_reads(rel, *REPO_FILES[rel])


@pytest.mark.control(reason="the nested value is the field's default: the read cannot differ")
@pytest.mark.parametrize("rel", sorted(REPO_CONTROLS))
def test_a_repo_file_spelling_a_default_under_metadata_reads_it(rel: str):
    _repo_file_reads(rel, *REPO_CONTROLS[rel])


# --------------------------------------------------------------------------
# Structure: one lift, before every from_frontmatter, after the migrations
# --------------------------------------------------------------------------

#: "<path>::<function>" -> why a `from_frontmatter` call there is a lift site
#: (it reads the file's spellings through `read_view` first) or needs none.
LIFT_SITES = {
    "pyrite/models/core_types.py::entry_from_frontmatter": "the loader's one lift",
    "pyrite/models/base.py::from_markdown": "the class-addressed loader's lift (`Entry.load`)",
    "pyrite/models/factory.py::build_entry": (
        "builds a new entry from a request, not a file; the request's `metadata` envelope "
        "is folded by `_fm_keys` and `refuse_conflicting_spellings`"
    ),
}


def _calls(node: ast.AST) -> list[str]:
    names = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            func = sub.func
            if isinstance(func, ast.Attribute):
                names.append(func.attr)
            elif isinstance(func, ast.Name):
                names.append(func.id)
    return names


def _functions(source: str):
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            yield node


def _sources():
    for base in [ROOT / "pyrite", *sorted((ROOT / "extensions").glob("*/src"))]:
        for path in sorted(base.rglob("*.py")):
            yield path.relative_to(ROOT).as_posix(), path.read_text(encoding="utf-8")


def test_every_from_frontmatter_call_in_production_code_is_a_lift_site():
    """A class's `from_frontmatter` reads top-level keys; the one place that
    reads the file's spellings (`read_view`) is the loader in front of it. A
    new caller of `from_frontmatter` is a reader that skips the rule."""
    found = {}
    for label, source in _sources():
        for node in _functions(source):
            if "from_frontmatter" in _calls(node):
                found[f"{label}::{node.name}"] = node
    unexpected = sorted(set(found) - set(LIFT_SITES))
    assert not unexpected, (
        f"{unexpected} call from_frontmatter outside the loader: read through "
        "entry_from_frontmatter (or add the site to LIFT_SITES with its reason)"
    )
    for label in (
        "pyrite/models/core_types.py::entry_from_frontmatter",
        "pyrite/models/base.py::from_markdown",
    ):
        assert "read_view" in _calls(found[label]), f"{label} does not lift (`read_view`)"


def test_the_row_shaping_and_the_file_read_share_the_one_lift_rule():
    """`IndexManager.read_fields` and `BaseBackend._entry_to_dict` shape the
    same read; each lifts the bag's declared fields through the one rule."""
    sources = dict(_sources())
    for label, name in (
        ("pyrite/storage/index.py", "read_fields"),
        ("pyrite/storage/backends/base_backend.py", "_entry_to_dict"),
    ):
        node = next(n for n in _functions(sources[label]) if n.name == name)
        assert "lift_declared_fields" in _calls(node), f"{label}::{name} does not use the rule"
    # Option B is the single-entry read's alone: it drops the declared fields
    # from its `metadata` through the rule; the row's shaping does not.
    read_fields = next(
        n for n in _functions(sources["pyrite/storage/index.py"]) if n.name == "read_fields"
    )
    assert "read_metadata" in _calls(read_fields)
    row = next(
        n
        for n in _functions(sources["pyrite/storage/backends/base_backend.py"])
        if n.name == "_entry_to_dict"
    )
    assert "read_metadata" not in _calls(row), "the row's bag must stay as stored (plugins read it)"


@pytest.mark.control(
    reason="the repository migrated before it loaded already; pinned so the lift stays in the "
    "loader, after the migrations, which keep reading the file's own spellings"
)
def test_migrations_run_on_the_files_own_spellings_before_the_lift():
    """`_load_entry` migrates the raw frontmatter, then hands it to the loader
    that lifts: `schema migrate` diffs a migration's result against the file,
    so a migration sees the file's own spellings."""
    source = (ROOT / "pyrite/storage/repository.py").read_text(encoding="utf-8")
    node = next(n for n in _functions(source) if n.name == "_load_entry")
    calls = _calls(node)
    assert calls.index("_maybe_migrate") < calls.index("entry_from_frontmatter")
    assert "read_view" not in calls, "the lift belongs to the loader, not the repository"
