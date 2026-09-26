"""Query functions for journalism-investigation data.

Pure functions that take a DB and filters, return structured results.
Used by both MCP handlers and CLI commands.
"""

from typing import Any

from .utils import parse_meta, strip_wikilink

# Map user-facing entity type names to DB-stored types.
# Core types like "person" and "organization" get resolved to plugin
# subtypes ("actor", "cascade_org") by KBService._resolve_entry_type.
ENTITY_TYPE_ALIASES: dict[str, list[str]] = {
    "person": ["person", "actor"],
    "organization": ["organization", "cascade_org"],
    "asset": ["asset"],
    "account": ["account"],
}


def query_timeline(
    db: Any,
    kb_name: str,
    *,
    from_date: str = "",
    to_date: str = "",
    actor: str = "",
    event_type: str = "",
    min_importance: int = 0,
    limit: int = 50,
) -> dict[str, Any]:
    """Query investigation events by date range, actor, and type."""
    actor_filter = actor.lower()

    event_types = ["investigation_event", "transaction", "legal_action"]
    if event_type and event_type in event_types:
        event_types = [event_type]

    events = []
    for etype in event_types:
        results = db.list_entries(kb_name=kb_name, entry_type=etype, limit=5000)
        for r in results:
            imp = int(r.get("importance", 5))
            if min_importance and imp < min_importance:
                continue
            date = str(r.get("date", ""))
            if from_date and date < from_date:
                continue
            if to_date and date > to_date:
                continue
            meta = parse_meta(r)
            actors = meta.get("actors") or []
            if actor_filter and not any(actor_filter in a.lower() for a in actors):
                continue
            events.append(
                {
                    "id": r.get("id"),
                    "title": r.get("title"),
                    "type": etype,
                    "date": date,
                    "importance": imp,
                    "actors": actors,
                }
            )
            if len(events) >= limit:
                break
        if len(events) >= limit:
            break
    events.sort(key=lambda e: e.get("date", ""))
    trimmed = events[:limit]
    summary = f"Found {len(trimmed)} events between {from_date or 'start'} and {to_date or 'now'}"
    return {"count": len(trimmed), "events": trimmed, "summary": summary}


def query_entities(
    db: Any,
    kb_name: str,
    *,
    entity_type: str = "",
    min_importance: int = 0,
    jurisdiction: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    """Query investigation entities by type, importance, and jurisdiction."""
    jurisdiction_filter = jurisdiction.lower()

    if entity_type and entity_type in ENTITY_TYPE_ALIASES:
        db_types = ENTITY_TYPE_ALIASES[entity_type]
    elif entity_type:
        db_types = [entity_type]
    else:
        db_types = []
        for aliases in ENTITY_TYPE_ALIASES.values():
            db_types.extend(aliases)

    entities = []
    for etype in db_types:
        results = db.list_entries(kb_name=kb_name, entry_type=etype, limit=5000)
        for r in results:
            imp = int(r.get("importance", 5))
            if min_importance and imp < min_importance:
                continue
            meta = parse_meta(r)
            jur = str(meta.get("jurisdiction", "")).lower()
            if jurisdiction_filter and jurisdiction_filter not in jur:
                continue
            entities.append(
                {
                    "id": r.get("id"),
                    "title": r.get("title"),
                    "type": etype,
                    "importance": imp,
                }
            )
    entities.sort(key=lambda e: e["importance"], reverse=True)
    trimmed = entities[:limit]
    type_label = entity_type or "entities"
    summary = f"Found {len(trimmed)} {type_label} in {kb_name}"
    return {"count": len(trimmed), "entities": trimmed, "summary": summary}


def query_network(
    db: Any,
    kb_name: str,
    entry_id: str,
    limit: int = 50,
    offset: int = 0,
    *,
    readable_kbs: set[str] | None,
) -> dict[str, Any]:
    """Get connection network for an entity, paged in both directions.

    `readable_kbs` is the caller's readable set (`None`: unscoped): a link
    from a KB outside it is dropped, one into such a KB reads as a link to a
    missing entry, and the totals count only what the caller can see
    (P-R4, P-R5).

    `limit` and `offset` apply to each direction separately, and the response
    carries the true totals plus `truncated` so a caller can tell what it did
    not see. `limit <= 0` means "no cap", matching `PyriteDB.get_backlinks`.

    The pages are cut here rather than in the storage layer: `get_outlinks` has
    no pagination parameters, and sorting a page after the fact would let rows
    repeat or vanish between pages. One deterministic order, then slice.
    """
    entry = db.get_entry(entry_id, kb_name)
    if not entry:
        return {"error": f"Entry '{entry_id}' not found"}

    def _ordered(links: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # `or ""`, not `.get("title", "")`: `get_outlinks` LEFT JOINs the target
        # entry, so a link to a page nobody has written yet has the key
        # *present* with the value None, and a default never fires. Sorting
        # None against a str raises TypeError, which turned an ordinary
        # dangling wikilink into a failed tool call. `id` comes from the same
        # join and gets the same treatment.
        return sorted(links, key=lambda link: (link.get("title") or "", link.get("id") or ""))

    outlinks = _ordered(db.get_outlinks(entry_id, kb_name, readable_kbs=readable_kbs))
    backlinks = _ordered(db.get_backlinks(entry_id, kb_name, readable_kbs=readable_kbs))

    total_outlinks, total_backlinks = len(outlinks), len(backlinks)
    start = max(offset, 0)
    window = slice(start, start + limit) if limit > 0 else slice(start, None)
    page_outlinks, page_backlinks = outlinks[window], backlinks[window]

    return {
        "center": {"id": entry_id, "title": entry.get("title", "")},
        "outlinks": page_outlinks,
        "backlinks": page_backlinks,
        "totals": {"outlinks": total_outlinks, "backlinks": total_backlinks},
        "limit": limit,
        "offset": start,
        "truncated": (start + len(page_outlinks) < total_outlinks)
        or (start + len(page_backlinks) < total_backlinks),
    }


def query_sources(
    db: Any,
    kb_name: str,
    *,
    reliability: str = "",
    classification: str = "",
    from_date: str = "",
    to_date: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    """Query source documents by reliability, classification, and date."""
    results = db.list_entries(kb_name=kb_name, entry_type="document_source", limit=5000)
    sources = []
    for r in results:
        meta = parse_meta(r)
        rel = meta.get("reliability", "unknown")
        if reliability and rel != reliability:
            continue
        cls = meta.get("classification", "")
        if classification and cls != classification:
            continue
        date = str(r.get("date", meta.get("obtained_date", "")))
        if from_date and date < from_date:
            continue
        if to_date and date > to_date:
            continue
        sources.append(
            {
                "id": r.get("id"),
                "title": r.get("title"),
                "reliability": rel,
                "classification": cls,
                "date": date,
            }
        )
    sources.sort(key=lambda s: s.get("date", ""))
    trimmed = sources[:limit]
    # Build tier breakdown
    tier_counts: dict[str, int] = {}
    for s in trimmed:
        rel = s.get("reliability", "unknown")
        tier_counts[rel] = tier_counts.get(rel, 0) + 1
    tier_parts = ", ".join(f"{v} {k}" for k, v in sorted(tier_counts.items()))
    summary = (
        f"Found {len(trimmed)} sources ({tier_parts})"
        if tier_parts
        else f"Found {len(trimmed)} sources"
    )
    return {"count": len(trimmed), "sources": trimmed, "summary": summary}


def query_claims(
    db: Any,
    kb_name: str,
    *,
    claim_status: str = "",
    confidence: str = "",
    min_importance: int = 0,
    limit: int = 50,
) -> dict[str, Any]:
    """Query claims by status, confidence, and importance."""
    results = db.list_entries(kb_name=kb_name, entry_type="claim", limit=5000)
    claims = []
    for r in results:
        imp = int(r.get("importance", 5))
        if min_importance and imp < min_importance:
            continue
        meta = parse_meta(r)
        status = meta.get("claim_status", "unverified")
        if claim_status and status != claim_status:
            continue
        conf = meta.get("confidence", "low")
        if confidence and conf != confidence:
            continue
        claims.append(
            {
                "id": r.get("id"),
                "title": r.get("title"),
                "assertion": meta.get("assertion", ""),
                "claim_status": status,
                "confidence": conf,
                "importance": imp,
                "evidence_count": len(meta.get("evidence_refs", []) or []),
            }
        )
    claims.sort(key=lambda c: c["importance"], reverse=True)
    trimmed = claims[:limit]
    # Build status breakdown
    status_counts: dict[str, int] = {}
    for c in trimmed:
        st = c.get("claim_status", "unverified")
        status_counts[st] = status_counts.get(st, 0) + 1
    status_parts = ", ".join(f"{v} {k}" for k, v in sorted(status_counts.items()))
    summary = (
        f"Found {len(trimmed)} claims ({status_parts})"
        if status_parts
        else f"Found {len(trimmed)} claims"
    )
    return {"count": len(trimmed), "claims": trimmed, "summary": summary}


def query_evidence_chain(
    db: Any,
    kb_name: str,
    claim_id: str,
) -> dict[str, Any]:
    """Trace evidence chain from claim to source documents."""
    claim = db.get_entry(claim_id, kb_name)
    if not claim:
        return {"error": f"Claim '{claim_id}' not found"}

    meta = parse_meta(claim)

    evidence_refs = meta.get("evidence_refs", []) or []
    chain: list[dict[str, Any]] = []
    gaps: list[str] = []

    if not evidence_refs:
        gaps.append(f"Claim '{claim_id}' has no evidence references")

    for ref in evidence_refs:
        eid = strip_wikilink(ref)
        evidence = db.get_entry(eid, kb_name)
        if not evidence:
            gaps.append(f"Evidence '{eid}' not found")
            chain.append({"evidence_id": eid, "status": "missing"})
            continue

        emeta = parse_meta(evidence)

        source_doc_ref = emeta.get("source_document", "")
        source_doc_id = strip_wikilink(source_doc_ref)

        source_info = None
        if source_doc_id:
            source = db.get_entry(source_doc_id, kb_name)
            if source:
                smeta = parse_meta(source)
                source_info = {
                    "id": source_doc_id,
                    "title": source.get("title", ""),
                    "reliability": smeta.get("reliability", "unknown"),
                }
            else:
                gaps.append(f"Source document '{source_doc_id}' not found")
        else:
            gaps.append(f"Evidence '{eid}' has no source document link")

        chain.append(
            {
                "evidence_id": eid,
                "title": evidence.get("title", ""),
                "evidence_type": emeta.get("evidence_type", ""),
                "reliability": emeta.get("reliability", "unknown"),
                "source_document": source_info,
            }
        )

    return {
        "claim": {
            "id": claim_id,
            "title": claim.get("title", ""),
            "assertion": meta.get("assertion", ""),
            "claim_status": meta.get("claim_status", "unverified"),
            "confidence": meta.get("confidence", "low"),
        },
        "evidence_chain": chain,
        "gaps": gaps,
    }
