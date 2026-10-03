"""Entry factory -- single point of entry type dispatch."""

import dataclasses as _dc

from ..schema import generate_entry_id
from .base import Entry
from .core_types import get_entry_class
from .generic import GenericEntry


def build_entry(
    entry_type: str,
    *,
    entry_id: str | None = None,
    title: str,
    body: str = "",
    **kwargs,
) -> Entry:
    """Build an entry of the given type with appropriate constructor kwargs.

    All types (core, plugin, unknown) are resolved through the type registry
    and constructed via ``from_frontmatter()``.  Unknown types fall back to
    ``GenericEntry``.

    Args:
        entry_type: The entry type string (e.g., "event", "person", "note")
        entry_id: Optional entry ID. Auto-generated from title if not provided.
        title: Entry title (required)
        body: Entry body content
        **kwargs: Type-specific fields (date, importance, role, participants, etc.)

    Returns:
        An Entry instance of the appropriate type
    """
    if entry_id is None:
        entry_id = generate_entry_id(title)

    resolved_cls = get_entry_class(entry_type)

    if resolved_cls is GenericEntry:
        # A kb.yaml-only type has no dataclass fields of its own, so every
        # kwarg beyond `tags`/`summary`/`metadata` used to be silently
        # dropped here -- `pyrite create -t finding -f severity=high`,
        # MCP `kb_create`, and bulk/import all lost the field, and because
        # it never reached `to_frontmatter()` the schema validator never
        # saw it either, so an enum violation on a custom type was not
        # refused (#386). Route through `GenericEntry.from_frontmatter`,
        # the same path `entry_from_frontmatter` uses, so a bare kwarg
        # lands in `metadata` exactly like an unknown frontmatter key does
        # on load -- one rule for "what happens to a field this type
        # doesn't know about", not two.
        #
        # `id`/`title`/`type` come from this function's own parameters, not
        # from kwargs: a stray `type=` (e.g. an MCP call sending both
        # `entry_type` and `type`) must not override the type the caller
        # actually asked to validate and write under (cold read, #394).
        # `kb_name`/`_entry_type`/`extra_frontmatter`/`body` are Entry
        # bookkeeping, never frontmatter content (`_BASE_CONSUMED_KEYS`
        # minus what a real file's frontmatter could ever contain); a kwarg
        # using one of those names must not leak into the written file
        # either.
        _reserved = {"id", "title", "type", "body", "kb_name", "_entry_type", "extra_frontmatter"}

        # An explicit `metadata=` kwarg is caller data assembled from extra
        # fields, not a file's own nested `metadata:` block -- merge its
        # contents in at the same top level as every other kwarg so
        # `from_frontmatter` treats all of it as "unknown keys to promote",
        # never as `explicit_metadata` (which it would mark
        # `_nested_metadata_keys` and write back nested, reviving the #149
        # layout and hiding the value from schema validation).
        explicit_metadata = kwargs.get("metadata")
        fm: dict = {
            "id": entry_id,
            "title": title,
            "type": entry_type,
        }
        if isinstance(explicit_metadata, dict):
            # The same reserved names are refused inside `metadata=`: a
            # `metadata={"type": ...}` retyped the entry past schema
            # validation (delta cold read, #394).
            fm.update({k: v for k, v in explicit_metadata.items() if k not in _reserved})
        for k, v in kwargs.items():
            if k in _reserved or k == "metadata":
                continue
            fm[k] = v
        return GenericEntry.from_frontmatter(fm, body)

    # Build frontmatter dict for from_frontmatter().
    # Known dataclass fields go as top-level keys; unknown kwargs are
    # collected into metadata so they persist through round-trips.
    _cls_fields = {f.name for f in _dc.fields(resolved_cls)}
    # A file key the type's aliases name (`actors`, `participants`, `source`):
    # the class reads it from the top level, so it goes there, never into the
    # `metadata:` bag where nothing reads it (#697, one name per field).
    from ..schema.field_aliases import file_keys_of, refuse_conflicting_spellings

    _alias_keys = file_keys_of(resolved_cls, entry_type)
    # A request naming a field and its alias with different values is refused
    # here, before either is lifted (#720); equal values fall through to one key.
    refuse_conflicting_spellings(
        resolved_cls,
        entry_type,
        {k: v for k, v in kwargs.items() if k != "metadata"},
        kwargs["metadata"] if isinstance(kwargs.get("metadata"), dict) else {},
    )
    _fm_keys = (
        _cls_fields
        | _alias_keys
        | {
            "type",
            "provenance",
            "sources",
            "links",
            "aliases",
            "created_at",
            "updated_at",
        }
    )

    fm: dict = {
        "id": entry_id,
        "title": title,
        "type": entry_type,
    }
    _extra_meta: dict = {}
    for k, v in kwargs.items():
        if k == "metadata" or k in _fm_keys:
            fm[k] = v
        else:
            _extra_meta[k] = v

    # The REST body and the web form send every non-standard field inside
    # `metadata`. Lift the alias keys out of it so they land top-level too. An
    # explicit top-level argument wins, unless it is an empty default (REST
    # sends `participants: []` when the client gave none).
    if isinstance(fm.get("metadata"), dict):
        bag = fm["metadata"]
        lifted = {mk: mv for mk, mv in bag.items() if mk in _alias_keys}
        for mk, mv in lifted.items():
            if not fm.get(mk):
                fm[mk] = mv
        fm["metadata"] = {mk: mv for mk, mv in bag.items() if mk not in lifted}

    # Merge unknown kwargs into metadata so they survive round-trip
    if _extra_meta:
        existing = fm.get("metadata") or {}
        fm["metadata"] = {**existing, **_extra_meta}

    return resolved_cls.from_frontmatter(fm, body)
