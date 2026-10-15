"""Extensions Pyrite once shipped and has since deleted, and the KB types they owned.

A KB is its files (principle 1): deleting an extension loses no content, it
only stops declaring the KB's types. A user whose config still says
``kb_type: cascade-timeline`` is told so once, in plain words, by the
surfaces that describe a KB (``orient``) and its health (``index health``),
instead of meeting silently undeclared types. This module is the one place
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


def removed_extension_notice(kb_name: str, kb_type: str | None) -> str | None:
    """One sentence for a KB whose type belonged to a removed extension, or None."""
    ext = removed_extension_for_kb_type(kb_type)
    if ext is None:
        return None
    return (
        f"KB '{kb_name}' has kb_type '{kb_type}', which belonged to the {ext.name} "
        f"extension, removed in {ext.removed_in}. {ext.replacement}"
    )
