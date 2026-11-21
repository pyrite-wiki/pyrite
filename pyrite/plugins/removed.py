"""Extensions Pyrite once shipped and has since deleted, and the KB types they owned.

A KB is its files (principle 1): deleting an extension loses no content, it
only stops declaring the KB's types. A user whose config still says
``kb_type: cascade-timeline`` is told so once, in plain words, by the
surfaces that describe a KB (``orient``) and its health (``index health``),
instead of meeting a type nothing explains. This module is the one place
that rule lives; the surfaces only read it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RemovedExtension:
    name: str
    kb_types: tuple[str, ...]
    entry_types: tuple[str, ...]
    removed_in: str  # the change that removed it
    replacement: str  # one sentence: what to do instead


REMOVED_EXTENSIONS: dict[str, RemovedExtension] = {
    "cascade": RemovedExtension(
        name="cascade",
        kb_types=("cascade-research", "cascade-timeline", "cascade-solidarity"),
        entry_types=(
            "actor",
            "cascade_org",
            "cascade_event",
            "timeline_event",
            "theme",
            "victim",
            "statistic",
            "mechanism",
            "scene",
            "solidarity_event",
        ),
        removed_in="#953",
        replacement=(
            "The files are still a KB and read and write as generic entries; declare the "
            "types you keep in kb.yaml, or install journalism-investigation and set "
            "`kb_type: journalism-investigation` for investigative types. The cascade "
            "public site moved to Hugo."
        ),
    ),
}


def removed_extension_for_kb_type(kb_type: str | None) -> RemovedExtension | None:
    """The removed extension that declared ``kb_type``, or None."""
    if not kb_type:
        return None
    for ext in REMOVED_EXTENSIONS.values():
        if kb_type in ext.kb_types:
            return ext
    return None


def removed_extension_for_entry_types(entry_types) -> RemovedExtension | None:
    """The removed extension that declared any of ``entry_types``, or None.

    A type name a KB declares itself in ``kb.yaml`` is its own; this is for
    the undeclared ones a deleted plugin used to declare.
    """
    held = set(entry_types or ())
    for ext in REMOVED_EXTENSIONS.values():
        if held & set(ext.entry_types):
            return ext
    return None


def removed_entry_types_held(ext: RemovedExtension, entry_types) -> list[str]:
    """Of ``entry_types``, the ones ``ext`` declared, in the order it did."""
    held = set(entry_types or ())
    return [t for t in ext.entry_types if t in held]


def removed_extension_report(
    kb_name: str, kb_type: str | None, entry_types=(), declared_types=()
) -> dict | None:
    """What a KB's health row and orient notice say about a removed extension,
    or None when the KB has nothing of one: ``extension``, ``entry_types`` (the
    held types the extension declared and the KB's kb.yaml does not) and ``message``.

    ``entry_types`` is what the KB holds (the index's distinct types): a
    generic KB that still has ``timeline_event`` files gets the same notice
    as one registered with ``cascade-timeline``. ``declared_types`` are the
    types its kb.yaml declares; those are its own and are left out (#1468).
    """
    declared = set(declared_types or ())
    undeclared = [t for t in (entry_types or ()) if t not in declared]
    ext = removed_extension_for_kb_type(kb_type)
    if ext is not None:
        head = f"KB '{kb_name}' has kb_type '{kb_type}', which belonged to the {ext.name} extension"
    else:
        ext = removed_extension_for_entry_types(undeclared)
        if ext is None:
            return None
        head = (
            f"KB '{kb_name}' holds entries of type "
            f"{', '.join(removed_entry_types_held(ext, undeclared))}, which belonged to the "
            f"{ext.name} extension"
        )
    held = removed_entry_types_held(ext, undeclared)
    # Not "undeclared": a kb.yaml that declares no types is unrestricted
    # (#809), so health lists nothing for these; they are entries of a type
    # no extension declares any more.
    held_note = (
        f" Nothing declares its {', '.join(held)} entries any more."
        if held and ext.kb_types and kb_type in ext.kb_types
        else ""
    )
    return {
        "extension": ext.name,
        "entry_types": held,
        "message": f"{head}, removed in {ext.removed_in}.{held_note} {ext.replacement}",
    }


def removed_extension_notice(
    kb_name: str, kb_type: str | None, entry_types=(), declared_types=()
) -> str | None:
    """The message of ``removed_extension_report``, or None."""
    report = removed_extension_report(kb_name, kb_type, entry_types, declared_types)
    return report["message"] if report else None
