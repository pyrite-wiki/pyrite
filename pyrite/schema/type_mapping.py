"""The one resolver for "which type does a create store" (#468).

A create stores the type the caller named, unless a plugin active for the
KB's ``kb_type`` declares that the core type maps to one of its own
(``PyritePlugin.get_type_mappings``) AND the KB's vocabulary declares that
pair's target, or declares no types (#1526: the key need not be declared;
when a pair applies the stored type is the only type checked). Nothing is inferred from subclassing: plugins subclass
``NoteEntry`` to reuse its fields, and inferring from that wrote every
``note`` in a software KB as an ADR.

Read by the write path (``KBService._resolve_entry_type``: every create
surface and the import's managed-field filter) and by
``KBSchema.to_agent_schema`` (MCP ``kb_schema`` and ``kb_orient``, REST
``/kbs/{kb}/schema`` and ``/kbs/{kb}/orient``, CLI ``orient``,
``pyrite-admin schema``), so what those show is what a create does.

A later ``kb.yaml`` ``type_map:`` override (a KB's declaration over the
plugin's default, as ADR-0008's amendment sets for vocabularies) belongs
here, read from ``kb_schema``; it is not built.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .kb_schema import KBSchema


def resolve_type_mappings(kb_type: str, kb_schema: KBSchema | None = None) -> dict[str, str]:
    """``{core type: stored type}`` a create applies in a KB of ``kb_type``.

    The plugins' declared pairs for the KB type (none for an empty
    ``kb_type``), less every pair whose target the KB's declared vocabulary
    (``kb_schema.declared_types()``) leaves out, when it declares any. A pair
    with an undeclared target would store a type the KB does not have, so it
    is not a mapping this KB applies and not one any surface may show. The key
    need not be declared (#1526, option (a)): a KB made from the
    journalism-investigation preset declares ``investigation_event`` but not
    ``event``, and a create of ``event`` there stores ``investigation_event``;
    the create checks only the stored type (``KBService._prepare``).

    This is the one place that decides whether a pair applies. It takes no
    ``allow_undeclared``: the override never changes which pair applies.
    """
    from ..plugins import get_registry

    mappings = get_registry().type_mappings_for_kb(kb_type)
    declared = set(kb_schema.declared_types()) if kb_schema is not None else set()
    if declared:
        mappings = {k: v for k, v in mappings.items() if v in declared}
    return mappings


def resolve_entry_type(entry_type: str, kb_type: str, kb_schema: KBSchema | None = None) -> str:
    """The type a create of ``entry_type`` stores in a KB of ``kb_type``."""
    return resolve_type_mappings(kb_type, kb_schema).get(entry_type, entry_type)
