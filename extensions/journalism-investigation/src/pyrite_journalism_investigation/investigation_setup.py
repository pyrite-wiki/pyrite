"""Investigation guided setup and status reporting.

Provides functions for:
- Creating new investigations with initial entities and questions
- Building comprehensive status reports for context rebuild
"""

from typing import Any

from pyrite.exceptions import PyriteError
from pyrite.schema import generate_entry_id
from pyrite.utils.errors import public_text

from .queries import (
    ENTITY_TYPE_ALIASES,
)
from .utils import parse_meta, persist_entry


def create_investigation(
    db: Any,
    kb_name: str,
    title: str,
    scope: str = "",
    key_questions: list[str] | None = None,
    initial_entities: list[dict[str, str]] | None = None,
    kb_service: Any = None,
) -> dict[str, Any]:
    """Create a new investigation with optional initial entities.

    Args:
        db: PyriteDB instance
        kb_name: Target KB name
        title: Investigation title
        scope: Description of investigation scope
        key_questions: List of key questions to investigate
        initial_entities: List of {"name": ..., "type": ...} dicts for initial entities
        kb_service: writes the entries as files through the KB service (MCP);
            None writes the index alone (the CLI) -- see `utils.persist_entry`

    Returns:
        Dict with created investigation ID and any created entities
    """
    if not title.strip():
        return {"error": "Investigation title is required"}
    # Without this the insert below dies on the entry->kb foreign key and the
    # caller sees a raw IntegrityError instead of being told which KB is missing.
    if db.get_kb_stats(kb_name) is None:
        return {
            "error": f"KB '{kb_name}' not found",
            "error_code": "NOT_FOUND",
            "suggestion": "Pass kb_name, or create an investigation KB first",
        }

    entry_id = generate_entry_id(title)
    if db.get_entry(entry_id, kb_name) is not None:
        # Refused before anything is written, so a retry is told plainly.
        return {
            "error": f"An entry '{entry_id}' already exists in KB '{kb_name}'",
            "error_code": "ENTRY_EXISTS",
            "suggestion": "Choose another title, or work in the existing investigation",
        }

    # Sort the initial entities before any write: one that already exists (in
    # the KB, or earlier in this list) is linked, never created again, so it
    # cannot fail the call after the investigation's own entry is written.
    to_create: list[dict[str, str]] = []
    linked: list[dict[str, str]] = []
    seen: set[str] = set()
    for ent in initial_entities or []:
        ent_name = ent.get("name", "")
        ent_type = ent.get("type", "person")
        if not ent_name:
            continue
        ent_id = generate_entry_id(ent_name)
        existing = None if ent_id in seen else db.get_entry(ent_id, kb_name)
        if ent_id in seen or existing is not None:
            item = {"id": ent_id, "title": ent_name, "type": ent_type}
            found = (existing or {}).get("entry_type")
            if found and found != ent_type:
                # Linked as it is, never retyped; the caller is told.
                item["type"] = found
                item["requested_type"] = ent_type
                item["warning"] = (
                    f"'{ent_id}' already exists as a {found}, not a {ent_type}; "
                    "it is linked as it is"
                )
            linked.append(item)
        else:
            to_create.append({"id": ent_id, "title": ent_name, "type": ent_type})
        seen.add(ent_id)

    # Build body from scope, key questions, and links to the initial entities
    body_parts = []
    if scope:
        body_parts.append(scope)
    if key_questions:
        body_parts.append("\n## Key Questions\n")
        for q in key_questions:
            body_parts.append(f"- {q}")
    entity_ids = list(dict.fromkeys(e["id"] for e in to_create + linked))
    if entity_ids:
        body_parts.append("\n## Initial Entities\n")
        for ent_id in entity_ids:
            body_parts.append(f"- [[{ent_id}]]")

    body = "\n".join(body_parts)

    try:
        persist_entry(
            db,
            kb_service,
            {
                "id": entry_id,
                "kb_name": kb_name,
                "title": title,
                "entry_type": "note",
                "body": body,
                "importance": 8,
                "tags": ["investigation"],
                "metadata": {"investigation_status": "active"},
            },
        )
    except PyriteError as e:
        return {"error": public_text(e), "error_code": e.error_code}

    result: dict[str, Any] = {"created": entry_id, "title": title}

    if initial_entities:
        created: list[dict[str, str]] = []
        errors: list[dict[str, str]] = []
        for ent in to_create:
            try:
                persist_entry(
                    db,
                    kb_service,
                    {
                        "id": ent["id"],
                        "kb_name": kb_name,
                        "title": ent["title"],
                        "entry_type": ent["type"],
                        "importance": 5,
                        "tags": ["investigation"],
                    },
                )
                created.append(ent)
            except PyriteError as e:
                # One entity refused is reported; the rest still go ahead.
                errors.append({**ent, "error": public_text(e), "error_code": e.error_code})
        result["entities_created"] = created
        result["entities_linked"] = linked
        if errors:
            result["entity_errors"] = errors

    return result


def build_investigation_status(
    db: Any,
    kb_name: str,
) -> dict[str, Any]:
    """Build a comprehensive investigation status report.

    Gathers counts of entities, events, claims, and sources,
    identifies unverified claims, and produces a summary.
    """
    # Count entities
    entity_count = 0
    for aliases in ENTITY_TYPE_ALIASES.values():
        for etype in aliases:
            try:
                results = db.list_entries(kb_name=kb_name, entry_type=etype, limit=10000)
                entity_count += len(results)
            except Exception:
                pass

    # Count events
    event_types = ["investigation_event", "transaction", "legal_action"]
    event_count = 0
    for etype in event_types:
        try:
            results = db.list_entries(kb_name=kb_name, entry_type=etype, limit=10000)
            event_count += len(results)
        except Exception:
            pass

    # Count and break down claims
    claim_results = db.list_entries(kb_name=kb_name, entry_type="claim", limit=10000)
    claim_count = len(claim_results)
    claim_breakdown: dict[str, int] = {}
    unverified_claims: list[dict[str, Any]] = []

    for r in claim_results:
        meta = parse_meta(r)
        status = meta.get("claim_status", "unverified")
        claim_breakdown[status] = claim_breakdown.get(status, 0) + 1
        if status == "unverified":
            unverified_claims.append(
                {
                    "id": r.get("id", ""),
                    "title": r.get("title", ""),
                    "importance": int(r.get("importance", 5)),
                }
            )

    # Count sources
    source_results = db.list_entries(kb_name=kb_name, entry_type="document_source", limit=10000)
    source_count = len(source_results)

    # Build summary
    parts = [
        f"{entity_count} entities",
        f"{event_count} events",
        f"{claim_count} claims",
        f"{source_count} sources",
    ]
    if unverified_claims:
        parts.append(f"{len(unverified_claims)} unverified claims need attention")
    summary = f"Investigation has {', '.join(parts[:4])}."
    if unverified_claims:
        summary += f" {len(unverified_claims)} unverified claims need attention."

    return {
        "summary": summary,
        "entity_count": entity_count,
        "event_count": event_count,
        "claim_count": claim_count,
        "source_count": source_count,
        "claim_breakdown": claim_breakdown,
        "unverified_claims": unverified_claims,
    }
