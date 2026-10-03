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
    """Refuse a request that names a field and its alias with different values.

    ADR-0042 decision 6 refuses a *file* carrying both spellings; this is the
    same rule for a *request* (#720). ``sources`` are the dicts one write
    carries -- its top-level fields and its ``metadata`` bag -- read together.
    Equal values are accepted (they name one value). An empty value (``None``,
    ``""``, ``[]``, ``{}``) is "not given": REST sends ``participants: []`` when
    the client set none. Every create and update calls this one helper, before
    anything is built or assigned, so a refusal writes nothing.
    """
    from ..exceptions import ValidationError

    aliases = class_field_aliases(entry_cls, entry_type)
    targets = set(aliases.values())
    seen: dict[str, tuple[str, Any]] = {}
    for source in sources:
        for key, value in source.items():
            if key not in aliases and key not in targets:
                continue
            if value is None or value == "" or value == [] or value == {}:
                continue
            target = aliases.get(key, key)
            prior = seen.get(target)
            if prior is None:
                seen[target] = (key, value)
            elif prior[0] != key and prior[1] != value:
                first, second = sorted((prior[0], key), key=lambda k: k in aliases, reverse=True)
                raise ValidationError(
                    f"Cannot set both '{first}' and '{second}' on a {entry_type} with different "
                    f"values: they are one field ('{target}'). Send one of them, or give both "
                    "the same value."
                )


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
