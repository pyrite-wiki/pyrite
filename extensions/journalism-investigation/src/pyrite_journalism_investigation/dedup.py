"""Cross-KB entity deduplication.

Detects duplicate entities across multiple KBs using fuzzy matching,
creates ``same_as`` links between them, and provides a merged view.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

from pyrite.services.access_policy import UNSCOPED

from .utils import parse_meta

# Default entity types to scan for duplicates
_DEFAULT_ENTITY_TYPES = ["person", "organization", "asset", "account"]


def find_duplicates(
    db,
    kb_names: list[str] | None = None,
    *,
    entry_types: list[str] | None = None,
    threshold: float = 0.85,
) -> list[dict]:
    """Scan entries across KBs for potential duplicates.

    Match by:
    - Exact title (case-insensitive)
    - Alias overlap (title matches an alias of another entry, or shared alias)
    - Fuzzy title match (SequenceMatcher ratio >= *threshold*)

    Parameters
    ----------
    db:
        PyriteDB instance.
    kb_names:
        KBs to scan. ``None`` means all registered KBs.
    entry_types:
        Filter to specific types. Defaults to person, organization, asset, account.
    threshold:
        Minimum SequenceMatcher ratio for fuzzy matches.

    Returns
    -------
    list[dict]
        Duplicate groups sorted by highest confidence descending::

            [{"canonical": {id, kb_name, title},
              "duplicates": [{id, kb_name, title, match_type, confidence}]}]
    """
    types = entry_types or _DEFAULT_ENTITY_TYPES

    # Collect all candidate entries across KBs
    entries: list[dict[str, Any]] = []
    scan_kbs = kb_names  # None means all KBs via list_entries(kb_name=None)

    for etype in types:
        if scan_kbs:
            for kb in scan_kbs:
                rows = db.list_entries(kb_name=kb, entry_type=etype, limit=5000)
                for r in rows:
                    meta = parse_meta(r)
                    aliases = meta.get("aliases", []) or r.get("aliases", []) or []
                    entries.append(
                        {
                            "id": r["id"],
                            "kb_name": r["kb_name"],
                            "title": r["title"],
                            "entry_type": r.get("entry_type", ""),
                            "aliases": aliases,
                        }
                    )
        else:
            rows = db.list_entries(kb_name=None, entry_type=etype, limit=5000)
            for r in rows:
                meta = parse_meta(r)
                aliases = meta.get("aliases", []) or r.get("aliases", []) or []
                entries.append(
                    {
                        "id": r["id"],
                        "kb_name": r["kb_name"],
                        "title": r["title"],
                        "entry_type": r.get("entry_type", ""),
                        "aliases": aliases,
                    }
                )

    # Build groups using union-find approach
    # Key: (id, kb_name) -> group index
    n = len(entries)
    parent = list(range(n))
    match_info: dict[tuple[int, int], dict] = {}  # (i, j) -> {match_type, confidence}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    # Compare all pairs across different KBs
    for i in range(n):
        for j in range(i + 1, n):
            if entries[i]["kb_name"] == entries[j]["kb_name"]:
                continue  # Only cross-KB dedup

            title_i = entries[i]["title"]
            title_j = entries[j]["title"]
            aliases_i = entries[i]["aliases"]
            aliases_j = entries[j]["aliases"]

            match_type = None
            confidence = 0.0

            # 1. Exact title match (case-insensitive)
            if title_i.lower() == title_j.lower():
                match_type = "exact"
                confidence = 1.0
            else:
                # 2. Alias overlap: title matches alias or shared alias
                aliases_i_lower = {a.lower() for a in aliases_i}
                aliases_j_lower = {a.lower() for a in aliases_j}

                if (
                    title_i.lower() in aliases_j_lower
                    or title_j.lower() in aliases_i_lower
                    or (aliases_i_lower & aliases_j_lower)
                ):
                    match_type = "alias"
                    confidence = 0.95
                else:
                    # 3. Fuzzy title match
                    ratio = SequenceMatcher(None, title_i.lower(), title_j.lower()).ratio()
                    if ratio >= threshold:
                        match_type = "fuzzy"
                        confidence = round(ratio, 4)

            if match_type is not None:
                union(i, j)
                key = (min(i, j), max(i, j))
                # Keep the highest-confidence match
                if key not in match_info or confidence > match_info[key]["confidence"]:
                    match_info[key] = {
                        "match_type": match_type,
                        "confidence": confidence,
                    }

    # Build groups from union-find
    groups_map: dict[int, list[int]] = {}
    for idx in range(n):
        root = find(idx)
        groups_map.setdefault(root, []).append(idx)

    result: list[dict] = []
    for _root, members in groups_map.items():
        if len(members) < 2:
            continue

        # Pick canonical: first by earliest index (stable ordering)
        canonical_idx = members[0]
        canonical = entries[canonical_idx]

        duplicates = []
        for m in members[1:]:
            key = (min(canonical_idx, m), max(canonical_idx, m))
            info = match_info.get(key, {"match_type": "fuzzy", "confidence": threshold})
            duplicates.append(
                {
                    "id": entries[m]["id"],
                    "kb_name": entries[m]["kb_name"],
                    "title": entries[m]["title"],
                    "match_type": info["match_type"],
                    "confidence": info["confidence"],
                }
            )

        # Sort duplicates by confidence descending
        duplicates.sort(key=lambda d: d["confidence"], reverse=True)

        result.append(
            {
                "canonical": {
                    "id": canonical["id"],
                    "kb_name": canonical["kb_name"],
                    "title": canonical["title"],
                },
                "duplicates": duplicates,
            }
        )

    # Sort groups by highest confidence descending
    result.sort(
        key=lambda g: max(d["confidence"] for d in g["duplicates"]),
        reverse=True,
    )
    return result


def create_same_as_links(
    db,
    canonical_id: str,
    canonical_kb: str,
    duplicate_ids: list[dict],
    *,
    dry_run: bool = False,
) -> dict:
    """Create bidirectional ``same_as`` links between canonical and duplicates.

    Parameters
    ----------
    db:
        PyriteDB instance.
    canonical_id:
        Entry ID of the canonical entity.
    canonical_kb:
        KB name of the canonical entity.
    duplicate_ids:
        List of ``{"id": str, "kb_name": str}`` dicts.
    dry_run:
        If True, return preview without creating links.

    Returns
    -------
    dict
        ``{"linked": int, "skipped": int, "links": [{from_id, from_kb, to_id, to_kb}]}``
    """
    linked = 0
    skipped = 0
    links: list[dict] = []

    for dup in duplicate_ids:
        dup_id = dup["id"]
        dup_kb = dup["kb_name"]

        # Check if link already exists
        existing_outlinks = db.get_outlinks(canonical_id, canonical_kb, readable_kbs=UNSCOPED)
        already_linked = any(
            l["id"] == dup_id and l.get("kb_name", "") == dup_kb and l["relation"] == "same_as"
            for l in existing_outlinks
        )
        if already_linked:
            skipped += 1
            continue

        link_record = {
            "from_id": canonical_id,
            "from_kb": canonical_kb,
            "to_id": dup_id,
            "to_kb": dup_kb,
        }
        links.append(link_record)
        linked += 1

        if not dry_run:
            # Get existing entry to preserve its data, then add link
            canonical_entry = db.get_entry(canonical_id, canonical_kb)
            if canonical_entry:
                existing_links = canonical_entry.get("links", [])
                new_link = {
                    "target": dup_id,
                    "kb": dup_kb,
                    "relation": "same_as",
                }
                existing_links.append(new_link)
                canonical_entry["links"] = existing_links
                db.upsert_entry(canonical_entry)

            # Bidirectional: also add reverse link on duplicate
            dup_entry = db.get_entry(dup_id, dup_kb)
            if dup_entry:
                dup_existing_links = dup_entry.get("links", [])
                reverse_link = {
                    "target": canonical_id,
                    "kb": canonical_kb,
                    "relation": "same_as",
                }
                dup_existing_links.append(reverse_link)
                dup_entry["links"] = dup_existing_links
                db.upsert_entry(dup_entry)

    return {"linked": linked, "skipped": skipped, "links": links}


def merge_entity_view(db, entity_id: str, kb_name: str, *, readable_kbs: set[str] | None) -> dict:
    """Build a merged view of an entity across KBs via ``same_as`` links.

    The walk crosses KBs, so it is bounded by ``readable_kbs`` (required;
    ``UNSCOPED`` only for a caller with no caller identity): an appearance in
    a KB outside it is neither followed nor merged, exactly as if the link
    pointed at nothing (P-R4, P-R5).

    Parameters
    ----------
    db:
        PyriteDB instance.
    entity_id:
        Entry ID to start from.
    kb_name:
        KB name of the starting entry.
    readable_kbs:
        The caller's readable set, or ``UNSCOPED``.

    Returns
    -------
    dict
        ``{"canonical": {id, kb_name, title},
          "appearances": [{id, kb_name, title, entry_type}],
          "merged_aliases": [str],
          "merged_tags": [str]}``
    """
    entry = (
        db.get_entry(entity_id, kb_name)
        if readable_kbs is None or kb_name in readable_kbs
        else None
    )
    if entry is None:
        return {
            "canonical": {"id": entity_id, "kb_name": kb_name, "title": ""},
            "appearances": [],
            "merged_aliases": [],
            "merged_tags": [],
        }

    # Collect all same_as-linked entries (BFS)
    visited: set[tuple[str, str]] = set()
    queue: list[tuple[str, str]] = [(entity_id, kb_name)]
    all_entries: list[dict] = []

    while queue:
        eid, ekb = queue.pop(0)
        if (eid, ekb) in visited:
            continue
        visited.add((eid, ekb))

        if readable_kbs is not None and ekb not in readable_kbs:
            continue
        e = db.get_entry(eid, ekb)
        if e is None:
            continue
        all_entries.append(e)

        # Follow same_as outlinks
        outlinks = db.get_outlinks(eid, ekb, readable_kbs=readable_kbs)
        for link in outlinks:
            if link.get("relation") == "same_as":
                target = (link["id"], link.get("kb_name", ekb))
                if target not in visited:
                    queue.append(target)

        # Follow same_as backlinks
        backlinks = db.get_backlinks(eid, ekb, readable_kbs=readable_kbs)
        for bl in backlinks:
            if bl.get("relation") == "same_as":
                target = (bl["id"], bl.get("kb_name", ekb))
                if target not in visited:
                    queue.append(target)

    # Merge aliases and tags
    all_aliases: set[str] = set()
    all_tags: set[str] = set()
    appearances: list[dict] = []

    for e in all_entries:
        appearances.append(
            {
                "id": e["id"],
                "kb_name": e["kb_name"],
                "title": e["title"],
                "entry_type": e.get("entry_type", ""),
            }
        )
        meta = parse_meta(e)
        for alias in meta.get("aliases", []) or e.get("aliases", []) or []:
            all_aliases.add(alias)
        for tag in e.get("tags", []) or []:
            if isinstance(tag, str):
                all_tags.add(tag)

    return {
        "canonical": {
            "id": entity_id,
            "kb_name": kb_name,
            "title": entry["title"],
        },
        "appearances": appearances,
        "merged_aliases": sorted(all_aliases),
        "merged_tags": sorted(all_tags),
    }
