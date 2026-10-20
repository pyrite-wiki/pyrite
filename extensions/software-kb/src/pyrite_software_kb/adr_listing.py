"""Shared ordering and relationship projection for ADR listing surfaces."""

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
