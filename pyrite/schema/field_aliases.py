"""Field aliases: the one place that says which key a type reads under another name.

A hand-written file may spell a field the old way (``participants:`` for
``actors:``). ADR-0045 decision 8: a type's *schema* names each alias and its
target, and the platform resolves it; a class attribute holding a bare set of
names (``FRONTMATTER_ALIASES``) has no target to carry.

``field_aliases(type_name, kb_schema)`` merges three layers, lowest priority
first, the precedence ``resolve_type_metadata`` already uses:

1. core schema (``CORE_TYPE_METADATA[type]["field_aliases"]``);
2. plugin ``get_type_metadata()[type]["field_aliases"]``;
3. ``kb.yaml``: ``types: {lead: {field_aliases: {participants: actors}}}``.

Each layer wins per alias key. The result is a flat ``{alias: target}`` map. It
is flat on purpose: B6 (the write path) will need to ask "which key does this
file use for ``actors``?" and can add that beside this function, and link-item
keys (``to`` -> ``target``) can later get an ``items`` form without changing
this shape.

Nothing here writes a file, and ``kb.yaml`` is read, never written (design
principle 7). A malformed declaration is ignored at resolve time and reported
by ``field_aliases_findings`` (``pyrite schema validate``), never a crash.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import date, datetime
from typing import Any

from .kb_schema import KBSchema
from .reserved import RESERVED_FIELD_NAMES

logger = logging.getLogger(__name__)

#: Classes already warned about for keeping the deprecated ``FRONTMATTER_ALIASES``.
_WARNED_CLASSES: set[type] = set()


def _valid_map(raw: Any) -> dict[str, str]:
    """The well-formed ``{str: non-empty str}`` entries of a declared map."""
    if not isinstance(raw, dict):
        return {}
    return {
        k: v
        for k, v in raw.items()
        if isinstance(k, str) and k and isinstance(v, str) and v.strip()
    }


def field_aliases(type_name: str, kb_schema: KBSchema | None = None) -> dict[str, str]:
    """Return ``{alias: target}`` for a type, merged core < plugin < kb.yaml."""
    from .core_types import CORE_TYPE_METADATA

    result: dict[str, str] = _valid_map(CORE_TYPE_METADATA.get(type_name, {}).get("field_aliases"))

    try:
        from ..plugins import get_registry

        plugin_meta = get_registry().get_all_type_metadata().get(type_name, {})
        result.update(_valid_map(plugin_meta.get("field_aliases")))
    except Exception:
        logger.warning("Failed to load plugin field aliases for %s", type_name, exc_info=True)

    if kb_schema is not None and type_name in kb_schema.types:
        result.update(_valid_map(kb_schema.types[type_name].field_aliases))
    return result


def _type_names_of(cls: type, entry_type: str) -> list[str]:
    """The entry type and every registered type whose class this class inherits from.

    A class used to inherit ``FRONTMATTER_ALIASES`` from its base, so a
    third-party ``class Meeting(EventEntry)`` with its own ``entry_type`` read
    ``participants`` as an alias without declaring anything. The schema is keyed
    by type name, so the inheritance has to be followed here: a subclass of a
    class registered as ``event`` has ``event``'s aliases (its own declaration,
    under its own name, wins per key because it is applied last).
    """
    from ..models.core_types import ENTRY_TYPE_REGISTRY

    registered = dict(ENTRY_TYPE_REGISTRY)
    try:
        from ..plugins import get_registry

        registered.update(get_registry().get_all_entry_types())
    except Exception:
        logger.warning("Failed to list plugin entry types for field aliases", exc_info=True)
    by_class: dict[type, list[str]] = {}
    for name, klass in registered.items():
        by_class.setdefault(klass, []).append(name)
    names: list[str] = []
    # Most distant base first, so a nearer class (and finally the type itself) overrides.
    for base in reversed(cls.__mro__):
        for name in by_class.get(base, ()):
            if name not in names:
                names.append(name)
    if entry_type in names:
        names.remove(entry_type)
    names.append(entry_type)
    return names


def class_field_aliases(cls: type, entry_type: str) -> dict[str, str]:
    """``{alias: target}`` for a class: its type and every registered base type, merged.

    The map ``entry_alias_names`` and the write surfaces share, so what a type
    publishes and what a class reads are one answer.
    """
    merged: dict[str, str] = {}
    for type_name in _type_names_of(cls, entry_type):
        merged.update(field_aliases(type_name))
    return merged


def file_keys_of(cls: type, entry_type: str) -> frozenset[str]:
    """Every file key a type's aliases name: the aliases and their targets.

    A create or update that names one of these must land it where the class
    reads it (a top-level key), not under ``metadata:`` -- whichever spelling
    the caller used (CLI ``-f``, MCP, REST, the web form).
    """
    aliases = class_field_aliases(cls, entry_type)
    return frozenset(aliases) | frozenset(aliases.values())


def refuse_conflicting_spellings(
    entry_cls: type, entry_type: str, *sources: dict[str, Any]
) -> None:
    """Refuse a request that names one field twice with different values.

    ADR-0042 decision 6 refuses a *file* carrying a field and its alias; this
    is the same rule for a *request* (#720). ``sources`` are the dicts one write
    carries -- its top-level fields and its ``metadata`` bag -- read together.

    A key the caller sent counts as given, empty or not (design principle 3: a
    write does what was asked; a default the server filled in was not asked, so
    the REST layer forwards only the fields the client set). Two keys for one
    field, or one key sent at the top level and again in ``metadata``, with
    different values are refused; equal values are accepted and name one value.
    Nothing here reads dict order: any two present values that differ refuse,
    and the message names the keys in a fixed order. Every create and update
    calls this one helper, before anything is built or assigned, so a refusal
    writes nothing.
    """
    from ..exceptions import ValidationError

    # Which field each request key names: `field_at` of its path (Andon
    # #994, decision A), so `k`, an alias of `k`, `metadata.k` at the top
    # level and `k` inside the `metadata` envelope are one field, and
    # `metadata.metadata.k` or an unreadable path is left to the resolver's
    # refusal. The first source is the request's top level, the second its
    # `metadata` envelope (#1001 round 3: the rule used to group aliases
    # only, so `{"title": "A", "metadata": {"title": "B"}}` wrote `B`).
    aliases = class_field_aliases(entry_cls, entry_type)
    given: dict[tuple[str, tuple[Any, ...]], list[tuple[str, Any]]] = {}
    for index, source in enumerate(sources):
        prefix: tuple[str, ...] = (METADATA,) if index == 1 else ()
        for key, value in source.items():
            if not isinstance(key, str):
                continue
            try:
                path = prefix + path_segments(key)
                field, rest = field_at(entry_cls, entry_type, path)
            except (NotAFieldError, ValueError):
                continue
            # Named as sent: a key sent at the top level and again in the
            # envelope is "one spelling, sent twice" (the message below).
            given.setdefault((field, rest), []).append((key, value))
    for (target, _rest), items in given.items():
        for i, (key_a, value_a) in enumerate(items):
            for key_b, value_b in items[i + 1 :]:
                if value_a == value_b:
                    continue
                if key_a == key_b:
                    raise ValidationError(
                        f"Cannot set '{key_a}' to two different values on a {entry_type}: it is "
                        "sent at the top level and again in `metadata`. Send it once, or give "
                        "both the same value."
                    )
                first, second = sorted((key_a, key_b), key=lambda k: (k not in aliases, k))
                raise ValidationError(
                    f"Cannot set both '{first}' and '{second}' on a {entry_type} with different "
                    f"values: they are one field ('{target}'). Send one of them, or give both "
                    "the same value."
                )


# --- which field a path denotes, and where a file spells it (Andon #994, decision A) ---

#: The one key that is not a field: ``metadata.k`` is a second spelling of
#: field ``k``, one level only, the same as an alias.
METADATA = "metadata"


class NotAFieldError(ValueError):
    """The path's field would be ``metadata``, which is not a field: the bare
    key, ``metadata.metadata``, or any deeper wrapping (#994)."""


class SpelledTwiceError(ValueError):
    """The file spells one field at two paths (``k:`` and ``metadata.k``, or a
    field and its alias). ``spellings`` are the paths found."""

    def __init__(self, field: str, spellings: list[tuple[str, ...]]):
        self.field = field
        self.spellings = spellings
        shown = ", ".join("`" + ".".join(p) + "`" for p in spellings)
        super().__init__(f"the file spells '{field}' twice: {shown}")


def path_segments(path: Any) -> tuple[Any, ...]:
    """A path as segments, parsed ONCE: a tuple or list is already parsed
    and is returned as given (a string segment in it is a literal key); a
    string is read by ``file_operations``' own parser (``.`` and ``[i]``:
    ``links[0].relation`` -> ``("links", 0, "relation")``), so the request
    boundary, ``field_at`` and ``apply()`` share one reading of a path. A
    string no parser can read raises ``ValueError`` (the surfaces answer
    ``VALIDATION_FAILED``). Anything else is the empty path."""
    from ..utils.field_path import parse_field_path

    if isinstance(path, tuple | list):
        return tuple(path)
    if not isinstance(path, str):
        return ()
    return tuple(parse_field_path(path))


def field_at(cls: type, entry_type: str, path: Any) -> tuple[str, tuple[Any, ...]]:
    """THE answer to "which field do these bytes read as": the field a
    frontmatter path denotes for a type, and the rest of the path below the
    field (``[0].relation`` of ``links[0].relation``) -- Andon #994, decision A.

    ``metadata`` is not a field. ``metadata.k`` is a second spelling of field
    ``k``, one level only, the same as an alias; so the first segment, or the
    one after a leading ``metadata``, names the field, and an alias names its
    target (``participants`` -> ``actors``, ``class_field_aliases``). A path
    whose field would be ``metadata`` -- the bare key, ``metadata.metadata``,
    ``metadata.metadata.k`` -- raises ``NotAFieldError``. A path that does not start
    with a key (``[0]``, an empty path) raises ``ValueError``. Every refusal
    an update makes is decided on the field this returns for the operation's
    output path; nothing reads a path's first segment any more.
    """
    segs = path_segments(path)
    if not segs or not isinstance(segs[0], str):
        raise ValueError(f"an operation's path must start with a key, got {path!r}")
    if segs[0] == METADATA:
        if len(segs) == 1 or not isinstance(segs[1], str):
            raise NotAFieldError(
                "`metadata` is not a field: a key is spelled `k` or `metadata.k`, "
                f"never `metadata` itself (got {path!r})"
            )
        key, rest = segs[1], segs[2:]
        if key == METADATA:
            raise NotAFieldError(
                "`metadata` is not a field: `metadata.k` spells `k` one level only, "
                f"so `metadata.metadata...` names nothing (got {path!r})"
            )
    else:
        key, rest = segs[0], segs[1:]
    aliases = class_field_aliases(cls, entry_type)
    return aliases.get(key, key), tuple(rest)


def spellings_of(cls: type, entry_type: str, field: str) -> list[tuple[str, ...]]:
    """Every path at which a file (or a request) may spell ``field`` for a
    type, in the order a reader prefers them: the field's own name, each
    alias of it, then the same under ``metadata``. What ``field_at`` folds,
    this unfolds; the grid test generates its cases from here."""
    aliases = class_field_aliases(cls, entry_type)
    names = [field, *(a for a, t in aliases.items() if t == field and a != field)]
    return [(n,) for n in names] + [(METADATA, n) for n in names]


def spelling_in(
    file_values: Mapping[str, Any], cls: type, entry_type: str, field: str
) -> tuple[str, ...]:
    """The one path the file spells ``field`` at (``spellings_of`` order):
    ``("actors",)``, ``("participants",)``, ``("metadata", "actors")``...
    A field the file does not spell is spelled at its own top-level name (the
    place a reader looks first). A file that spells the field twice raises
    ``SpelledTwiceError``: an update of that field is refused and the file is
    reported (``double_spellings``), never edited at one spelling while the
    other stands."""
    found = _spellings_present(file_values, cls, entry_type, field)
    if len(found) > 1:
        raise SpelledTwiceError(field, found)
    return found[0] if found else (field,)


def values_identical(a: Any, b: Any) -> bool:
    """The same YAML value, type included: ``True`` is not ``1`` and ``1`` is
    not ``1.0`` (Python's ``==`` says they are), a list or a mapping compares
    item by item the same way. What "an equal pair" means for the merge of
    a doubly spelled field (#1001 round 4, item 1): a spelling is removed
    only when nothing the author wrote is lost.

    "Type" is the YAML kind (``_yaml_kind``), not the parser's class: the
    round-trip parser reads ``x``, ``'x'`` and ``"x"`` as three ``str``
    subclasses and a block mapping as its own mapping class, and they are
    one value each (#1013: a quoted pair was called a disagreement)."""
    if _yaml_kind(a) != _yaml_kind(b):
        return False
    if isinstance(a, Mapping):
        return a.keys() == b.keys() and all(values_identical(a[k], b[k]) for k in a)
    if isinstance(a, list | tuple):
        return len(a) == len(b) and all(values_identical(x, y) for x, y in zip(a, b, strict=True))
    return bool(a == b)


def _yaml_kind(value: Any) -> str:
    """The YAML kind a parsed value is: null, bool, int, float, str, date,
    timestamp, map or seq (``bool`` before ``int`` and ``datetime`` before
    ``date``, each being a subclass of the other); anything else its class."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, datetime):
        return "timestamp"
    if isinstance(value, date):
        return "date"
    if isinstance(value, Mapping):
        return "map"
    if isinstance(value, list | tuple):
        return "seq"
    return type(value).__name__


def double_spellings(
    file_values: Mapping[str, Any], cls: type, entry_type: str
) -> dict[str, list[tuple[str, ...]]]:
    """Every field the file spells more than once, with its paths: the
    report's side of the rule (``index health``, ``qa validate``). Fields are
    every top-level key and every key of the ``metadata`` block, folded to
    their field by ``field_at``; a key that is no field (``metadata`` as a
    name inside the block) is left out."""
    out: dict[str, list[tuple[str, ...]]] = {}
    nested = file_values.get(METADATA)
    keys: list[Any] = [(k,) for k in file_values if k != METADATA]
    if isinstance(nested, Mapping):
        keys += [(METADATA, k) for k in nested]
    seen: set[str] = set()
    for path in keys:
        if not isinstance(path[-1], str):
            continue
        try:
            field, _rest = field_at(cls, entry_type, path)
        except ValueError:
            continue
        if field in seen:
            continue
        seen.add(field)
        found = _spellings_present(file_values, cls, entry_type, field)
        if len(found) > 1:
            out[field] = found
    return out


#: Dataclass fields of an entry class that are not frontmatter fields: the
#: identity line (``id:``, design principle 5) and ``type:`` (which picks the
#: class before any spelling is read) are read at the top level only; the
#: rest are not file keys at all.
_NOT_FIELDS = frozenset(
    {"id", "type", "body", "metadata", "file_path", "kb_name", "extra_frontmatter"}
)


#: ``declared_fields`` per (class, type name): the alias map behind it walks
#: the plugin registry, and a read asks for every entry. Cleared whenever a
#: plugin is registered or discovered (``clear_declared_fields_cache``), the
#: one event that changes a type's aliases.
_DECLARED_FIELDS: dict[tuple[type, str], frozenset[str]] = {}


def clear_declared_fields_cache() -> None:
    """Forget the cached ``declared_fields``: the plugin registry changed."""
    _DECLARED_FIELDS.clear()


def declared_fields(cls: type, entry_type: str) -> frozenset[str]:
    """The fields a type declares: the class's public dataclass fields that are
    file keys, and every alias target. What the reader lifts (``read_view``,
    ``lift_declared_fields``) and what a single-entry read keeps out of its
    ``metadata`` (``read_metadata``): a key that is no field of the type is
    the ``metadata`` bag's, at every spelling."""
    import dataclasses

    cached = _DECLARED_FIELDS.get((cls, entry_type))
    if cached is not None:
        return cached
    if not dataclasses.is_dataclass(cls):
        return frozenset()
    aliases = class_field_aliases(cls, entry_type)
    names = {
        f.name
        for f in dataclasses.fields(cls)
        if not f.name.startswith("_") and f.name not in _NOT_FIELDS
    }
    # A dataclass field named like an alias (`participants`, the attribute
    # `actors` is read into) is a spelling of its target, not a field.
    names -= set(aliases)
    names |= set(aliases.values())
    result = frozenset(names)
    _DECLARED_FIELDS[(cls, entry_type)] = result
    return result


def read_view(file_values: dict[str, Any], cls: type, entry_type: str) -> dict[str, Any]:
    """The file's frontmatter as a class reads it: a declared field the file
    spells only under ``metadata:`` is spelled at that name at the top level
    too (#1025; ADR-0051, the reader half). The one place the reader reads a
    spelling: every ``from_frontmatter`` reads top-level keys of this view,
    and takes each field's value from ``spelling_in(file, field)``, the path
    the writer writes (ADR-0042 decision 12).

    The view adds the top-level key and leaves ``metadata`` as the file has
    it (the model's bag, and the index row's, keep the nested spelling: the
    storage shape); the single-entry read then spells the field once, at its
    own name (``read_metadata``, option B of the maintainer's decision of
    2026-10-20). ``capture_extra_frontmatter`` keeps the raw mapping as
    ``_source_frontmatter``. A field spelled twice is left as it is: the
    model reads the top-level spelling (decision 3) and ``double_spellings``
    reports the file. A key the type does not declare is the bag's and is
    not lifted. Returns ``file_values`` itself when there is nothing to
    lift."""
    nested = file_values.get(METADATA)
    if not isinstance(nested, Mapping) or not nested:
        return file_values
    declared = declared_fields(cls, entry_type)
    view: dict[str, Any] | None = None
    for key in nested:
        if not isinstance(key, str) or key == METADATA:
            continue
        try:
            field, _rest = field_at(cls, entry_type, (METADATA, key))
        except ValueError:
            continue
        if field not in declared:
            continue
        try:
            path = spelling_in(file_values, cls, entry_type, field)
        except SpelledTwiceError:
            continue
        if path != (METADATA, key):
            continue
        if view is None:
            view = dict(file_values)
        view[key] = nested[key]
    return view if view is not None else file_values


def lift_declared_fields(
    result: dict[str, Any], bag: Mapping[str, Any] | None, cls: type, entry_type: str
) -> None:
    """The row's read shape: a declared field the row keeps in its ``metadata``
    bag (a field with no column of its own: a backlog item's ``rank``, a
    task's ``status_reason``) is read at its own name too; the bag keeps it,
    and a key the type does not declare stays the bag's (#1025). One rule for
    ``IndexManager.read_fields`` and the backend's row, in place of a
    hand-written list of five keys. Where the bag spells the field is
    ``spelling_in``'s answer (the field's name or an alias); a bag spelling
    it twice reads the field's own name, the model's reading (decision 3)."""
    if not isinstance(bag, Mapping) or not bag:
        return
    for field in sorted(declared_fields(cls, entry_type)):
        if field in result:
            continue
        try:
            path = spelling_in(bag, cls, entry_type, field)
        except SpelledTwiceError:
            path = (field,)
        if path[0] != METADATA and path[-1] in bag:
            result[field] = bag[path[-1]]


def read_metadata(
    bag: Mapping[str, Any] | None,
    result: Mapping[str, Any],
    cls: type,
    entry_type: str,
    *,
    file_values: Mapping[str, Any] | None = None,
    agrees: Callable[[str, Any, Any], bool] | None = None,
) -> dict[str, Any]:
    """The single-entry read's ``metadata``: the bag, less every key that spells
    a declared field the read carries at its own name (``field_at`` folded,
    so an alias of a declared field is a spelling of it). A read spells each
    field once, at its own name, and ``metadata`` holds only keys the type
    does not declare (option B of the maintainer's decision of 2026-10-20 on
    #1025); the index row keeps the whole bag, the storage shape the plugins
    read.

    Which bag keys leave is decided by the file's spellings (``file_values``,
    the raw frontmatter): a field the file does not spell at the top level
    has one value, the one the lift carried to its own name, so its bag key
    is removed whatever a column made of the value (``metadata: {importance:
    '8'}`` reads ``importance: 8`` and nothing under ``metadata``; round-2
    delta review). A field the file spells at the top level too is a double
    spelling, or a declared field the class never reads from its frontmatter
    (whose column is empty): there the value comparison decides, and a read
    never loses a value the file holds (``importance: 7`` with ``metadata:
    {importance: 8}`` reads 7 at its own name and keeps 8 under ``metadata``,
    where ``index health`` reports it). ``agrees(field, read_value,
    bag_value)`` says whether the read's value is the bag's; the caller knows
    how its columns spell a value (a TEXT column holds ``2`` as ``"2"``), the
    default compares the two as JSON text. Without ``file_values`` every key
    is treated as a double spelling (the comparison alone decides)."""
    if not isinstance(bag, Mapping) or not bag:
        return {}
    same = agrees or _same_json_text
    declared = declared_fields(cls, entry_type)
    source = file_values if isinstance(file_values, Mapping) else None
    out: dict[str, Any] = {}
    for key, value in bag.items():
        if isinstance(key, str) and key != METADATA:
            try:
                field, _rest = field_at(cls, entry_type, (key,))
            except ValueError:
                field = key
            if field in declared and field in result:
                if source is not None and not _spelled_at_top(source, cls, entry_type, field):
                    continue
                if same(field, result[field], value):
                    continue
        out[key] = value
    return out


def _spelled_at_top(file_values: Mapping[str, Any], cls: type, entry_type: str, field: str) -> bool:
    """Whether the file spells ``field`` at the top level, by its own name or
    an alias (a spelling under ``metadata:`` does not count)."""
    return any(
        path[0] != METADATA for path in _spellings_present(file_values, cls, entry_type, field)
    )


def _same_json_text(_field: str, a: Any, b: Any) -> bool:
    """Equal once both are spelled as JSON (a read is JSON on every surface)."""
    import json

    return json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)


def _spellings_present(
    file_values: Mapping[str, Any], cls: type, entry_type: str, field: str
) -> list[tuple[str, ...]]:
    nested = file_values.get(METADATA)
    nested = nested if isinstance(nested, Mapping) else {}
    found: list[tuple[str, ...]] = []
    for path in spellings_of(cls, entry_type, field):
        holder = nested if path[0] == METADATA else file_values
        if path[-1] in holder:
            found.append(path)
    return found


def attribute_for(entry: Any, key: str) -> str | None:
    """The model attribute an update to file key ``key`` should set, or None.

    ``None`` means ``key`` is not one of the entry type's alias or target keys,
    or the model has no attribute for either spelling. ``actors`` on an event
    sets ``participants`` (the attribute), ``source`` on a relationship sets
    ``source_entity``.
    """
    aliases = class_field_aliases(type(entry), entry.entry_type)
    if key not in aliases and key not in aliases.values():
        return None
    if hasattr(entry, key):
        return key
    for alias, target in aliases.items():
        if key == target and hasattr(entry, alias):
            return alias
        if key == alias and hasattr(entry, target):
            return target
    return None


def entry_alias_names(entry: Any) -> frozenset[str]:
    """Alias names for a loaded entry: the schema's, plus the deprecated class attribute.

    The one reader the model layer uses (``models/base.py``). A class that
    still sets ``FRONTMATTER_ALIASES`` is honoured for one more release and
    warned about once; the attribute carries no target, so it can only warn.
    """
    cls = type(entry)
    names: set[str] = set(class_field_aliases(cls, entry.entry_type))
    declared = getattr(type(entry), "FRONTMATTER_ALIASES", frozenset())
    if declared:
        if cls not in _WARNED_CLASSES:
            _WARNED_CLASSES.add(cls)
            logger.warning(
                "%s sets FRONTMATTER_ALIASES %s; this class attribute is deprecated and "
                "will be removed. Declare `field_aliases: {alias: target}` for type '%s' in "
                "the plugin's get_type_metadata() instead (ADR-0045).",
                cls.__name__,
                sorted(declared),
                entry.entry_type,
            )
        names |= set(declared)
    return frozenset(names)


def undeclared_class_aliases(
    cls: type, entry_type: str, kb_schema: KBSchema | None = None
) -> set[str]:
    """Names a class keeps in ``FRONTMATTER_ALIASES`` that the schema gives no target.

    The conformance check for ADR-0045's "every alias names a target": empty
    for a class that went through the schema.
    """
    declared = set(getattr(cls, "FRONTMATTER_ALIASES", ()) or ())
    resolved: set[str] = set()
    for type_name in _type_names_of(cls, entry_type):
        resolved |= set(field_aliases(type_name, kb_schema))
    return declared - resolved


def _class_keys(type_name: str) -> set[str]:
    """Keys the type's registered class reads: its dataclass fields, and every alias target."""
    import dataclasses

    from ..models.core_types import ENTRY_TYPE_REGISTRY

    cls = ENTRY_TYPE_REGISTRY.get(type_name)
    if cls is None:
        try:
            from ..plugins import get_registry

            cls = get_registry().get_all_entry_types().get(type_name)
        except Exception:
            cls = None
    if cls is None or not dataclasses.is_dataclass(cls):
        return set()
    keys = {f.name for f in dataclasses.fields(cls)}
    keys |= set(class_field_aliases(cls, type_name).values())
    return keys


def field_aliases_findings(kb_schema: KBSchema) -> list[dict[str, str]]:
    """Problems in the ``field_aliases`` a ``kb.yaml`` declares. Each is ``{where, message}``.

    Reports, naming the type: a declaration that is not a map; an empty or
    non-string target; an alias equal to its target; a chain (a target that is
    itself an alias); a reserved name as alias or target; an alias that is also
    a declared field of the type; a target that is *not* a declared field of
    the type. Declared means: the type's ``kb.yaml`` ``fields``/``required``/
    ``optional``, the core type's fields, or a plugin field schema. The core
    field list names the file keys (``actors``, ``source_entity``), never the
    old spellings, so a core type declares no alias as a field.
    """
    from .core_types import CORE_TYPES

    plugin_fields: dict[str, dict] = {}
    try:
        from ..plugins import get_registry

        plugin_fields = get_registry().get_all_field_schemas()
    except Exception:
        logger.warning("Failed to load plugin field schemas", exc_info=True)
    out: list[dict[str, str]] = []

    def add(type_name: str, message: str) -> None:
        out.append({"where": f"<type:{type_name}>", "message": f"Type '{type_name}': {message}"})

    for type_name, ts in kb_schema.types.items():
        raw = ts.field_aliases
        if not raw:
            continue
        if not isinstance(raw, dict):
            add(
                type_name,
                f"`field_aliases` must be a map of alias: target, got {type(raw).__name__}; ignored",
            )
            continue
        declared = (
            set(ts.fields)
            | set(ts.required)
            | set(ts.optional)
            | set(CORE_TYPES.get(type_name, {}).get("fields", {}))
            | set(plugin_fields.get(type_name, {}))
        )
        # A target also counts as declared when the type's class has it as a
        # dataclass field, or the schema layers already name it as the target
        # of an alias (`actors` on an event type): the class reads it.
        readable = set(declared) | _class_keys(type_name)
        for alias, target in raw.items():
            if not isinstance(alias, str) or not alias:
                add(type_name, f"`field_aliases` has a non-string alias {alias!r}; ignored")
                continue
            if not isinstance(target, str) or not target.strip():
                add(
                    type_name,
                    f"`field_aliases` alias '{alias}' has no target ({target!r}); ignored",
                )
                continue
            if alias == target:
                add(type_name, f"`field_aliases` alias '{alias}' is its own target")
            elif target in raw:
                add(
                    type_name,
                    f"`field_aliases` alias '{alias}' -> '{target}' is a chain: "
                    f"'{target}' is itself an alias",
                )
            if alias in RESERVED_FIELD_NAMES:
                add(type_name, f"`field_aliases` alias '{alias}' is a reserved name")
            if target in RESERVED_FIELD_NAMES:
                add(type_name, f"`field_aliases` target '{target}' is a reserved name")
            if alias in declared:
                add(
                    type_name,
                    f"`field_aliases` alias '{alias}' is also a declared field of the type",
                )
            if alias != target and target not in readable and target not in RESERVED_FIELD_NAMES:
                add(
                    type_name,
                    f"`field_aliases` target '{target}' (for alias '{alias}') is not a "
                    "declared field of the type: declare it under `fields:`",
                )
    return out
