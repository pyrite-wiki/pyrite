"""Shared utilities for journalism-investigation plugin."""

import json
from typing import Any


def parse_meta(entry_dict: dict[str, Any]) -> dict[str, Any]:
    """Parse metadata from a DB entry dict, handling JSON strings."""
    meta = entry_dict.get("metadata") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except (json.JSONDecodeError, TypeError):
            meta = {}
    return meta


def strip_wikilink(ref: str) -> str:
    """Extract entry ID from a wikilink reference like [[some-id]]."""
    return ref.strip().strip("[]").replace("[[", "").replace("]]", "")


def persist_entry(db: Any, kb_service: Any, data: dict[str, Any]) -> None:
    """Write one new entry: through `kb_service` when given, else into the
    index alone.

    Over MCP the handler passes the plugin context's `kb_service`, so the
    entry is a file and lands where the landing rule says -- a user's change
    set on a `landing: review` KB (ADR-0044 §1). The index-only branch is the
    CLI's (the operator, always canonical) and is kept as it was; it writes
    no file, so a rebuilt index loses it.
    """
    if kb_service is None:
        db.upsert_entry(data)
        return
    fields = {
        k: v for k, v in data.items() if k not in ("id", "kb_name", "title", "entry_type", "body")
    }
    kb_service.create_entry(
        kb_name=data["kb_name"],
        entry_id=data["id"],
        title=data["title"],
        entry_type=data["entry_type"],
        body=data.get("body", ""),
        **fields,
    )
