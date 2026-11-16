"""The one resolver for "which type does a create store" (#468).

A create stores the type the caller named, unless a plugin active for the
KB's ``kb_type`` declares that the core type maps to one of its own
(``PyritePlugin.get_type_mappings``). Nothing is inferred from subclassing:
plugins subclass ``NoteEntry`` to reuse its fields, and inferring from that
wrote every ``note`` in a software KB as an ADR.

Read by the write path (``KBService._resolve_entry_type``: every create
surface and the import's managed-field filter) and by
``KBSchema.to_agent_schema`` (orient, ``kb_schema``, ``GET /kbs/{kb}/schema``),
so what orient shows is what a create does.

A later ``kb.yaml`` ``type_map:`` override (a KB's declaration over the
plugin's default, as ADR-0008's amendment sets for vocabularies) belongs
here, read from ``kb_schema``; it is not built.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .kb_schema import KBSchema


def resolve_type_mappings(kb_type: str, kb_schema: KBSchema | None = None) -> dict[str, str]:
    """``{core type: stored type}`` for a KB of ``kb_type``; empty for no kb_type.

    ``kb_schema`` is where a KB's own override would be read; today it is
    unused, and the plugins' declarations are the whole answer.
    """
    from ..plugins import get_registry

    return get_registry().type_mappings_for_kb(kb_type)


def resolve_entry_type(entry_type: str, kb_type: str, kb_schema: KBSchema | None = None) -> str:
    """The type a create of ``entry_type`` stores in a KB of ``kb_type``."""
    return resolve_type_mappings(kb_type, kb_schema).get(entry_type, entry_type)
