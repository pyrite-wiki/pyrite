"""Shared ordering and relationship projection for ADR listing surfaces."""

import logging
from typing import Any

logger = logging.getLogger(__name__)

ADR_RELATIONS = ("supersedes", "superseded_by", "amends", "amended_by")


def adr_sort_key(adr: dict) -> tuple[bool, int, str, str]:
    """Sort numbered ADRs numerically, then unnumbered records by stable identity."""
    number = adr.get("adr_number")
    if number is None:
        number = (adr.get("_meta") or {}).get("adr_number")
    if isinstance(number, bool):
        number = None
    elif isinstance(number, str) and number.isdecimal():
        number = int(number)
    elif not isinstance(number, int):
        number = None
    if number is not None and number <= 0:
        number = None

    return (
        number is None,
        number if number is not None else 0,
        str(adr.get("kb_name", "")),
        str(adr.get("id", "")),
    )


def adr_relations(db, adr: dict, *, readable_kbs: set[str] | None) -> dict[str, list[dict]]:
    """Return visible ADR links in both directions for one listed ADR."""
    relations = {name: [] for name in ADR_RELATIONS}
    entry_id = adr["id"]
    kb_name = adr["kb_name"]

    for link in db.get_outlinks(entry_id, kb_name, readable_kbs=readable_kbs):
        relation = link.get("relation")
        if relation not in relations or link.get("entry_type") != "adr":
            continue
        relations[relation].append(
            {"id": link["id"], "kb_name": link["kb_name"], "title": link["title"]}
        )

    for link in db.get_backlinks(entry_id, kb_name, readable_kbs=readable_kbs):
        relation = link.get("relation")
        if relation not in relations or link.get("entry_type") != "adr":
            continue
        relations[relation].append(
            {"id": link["id"], "kb_name": link["kb_name"], "title": link["title"]}
        )

    for items in relations.values():
        items.sort(key=lambda item: (item["kb_name"], item["id"]))
    return relations


def format_adr_relations(relations: dict[str, list[dict]]) -> str:
    """Format only present links for the human-readable ADR table."""
    parts = []
    for relation in ADR_RELATIONS:
        targets = relations.get(relation, [])
        if targets:
            identities = ", ".join(f"{item['kb_name']}:{item['id']}" for item in targets)
            parts.append(f"{relation}: {identities}")
    return "; ".join(parts) or "—"


def resolve_adr_kb(
    config: Any, kb_name: str | None = None, *, readable_kbs: set[str] | None
) -> Any:
    """Pick the KB holding ADRs: explicitly named, the single configured KB,
    otherwise the (single) KB whose schema declares the ``adr`` type.

    Returns ``None`` when ambiguous or when no KB exists (#1082, #1175).
    """
    kbs = [
        kb
        for kb in (getattr(config, "knowledge_bases", []) or [])
        if readable_kbs is None or kb.name in readable_kbs
    ]
    if kb_name:
        kb = config.get_kb(kb_name)
        return kb if kb is not None and (readable_kbs is None or kb.name in readable_kbs) else None

    if len(kbs) == 1:
        return kbs[0]
    if not kbs:
        return None

    adr_kbs = []
    for kb in kbs:
        try:
            if kb.kb_schema and kb.kb_schema.get_type_schema("adr"):
                adr_kbs.append(kb)
        except Exception:
            continue
    return adr_kbs[0] if len(adr_kbs) == 1 else None


def adr_numbers_in_files(kb_conf: Any) -> set[int]:
    """The ``adr_number`` values the KB's files hold, read without the index.

    A KB registered but never indexed has no rows, and numbering from the
    index alone answered ``adr-0001`` for a KB whose files already carry
    ADR-0001, so the write refused with ``ENTRY_EXISTS`` for an entry it
    never looked for (#1082, #1175).
    """
    from pyrite.exceptions import FrontmatterError
    from pyrite.storage.repository import KBRepository
    from pyrite.utils.frontmatter import load_frontmatter
    from pyrite.utils.kb_files import KBFileOutsideRootError

    numbers: set[int] = set()
    repo = KBRepository(kb_conf)
    for path in repo.list_files():
        try:
            # Inside the KB, as every entry load is: a file that is not the
            # KB's (a link out, a hard link) gives no number.
            loaded = load_frontmatter(repo._read_inside(path).decode("utf-8"))
        except (OSError, UnicodeDecodeError, FrontmatterError, KBFileOutsideRootError) as exc:
            logger.warning("Skipping unreadable ADR numbering file %s: %s", path, exc)
            continue
        if loaded is None:
            continue
        number = loaded[0].get("adr_number")
        if isinstance(number, bool):
            continue
        if isinstance(number, int):
            numbers.add(number)
        elif isinstance(number, str) and number.strip().isdigit():
            numbers.add(int(number.strip()))
    return numbers


def next_adr_number(kb_conf: Any) -> int:
    """One past the highest ADR number the KB's files hold.

    The number comes from the files only; index rows don't count, so a stale
    row whose file is gone does not advance it (#1082, #1175).
    """
    return max(adr_numbers_in_files(kb_conf) | {0}) + 1
