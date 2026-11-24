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


def persist_entry(kb_service: Any, data: dict[str, Any]) -> None:
    """Write one new entry through `kb_service`: a file and an index row,
    with the pipeline's read-only, id, type and validator checks.

    Every caller passes a `KBService`: over MCP the plugin context's, so the
    entry lands where the landing rule says -- a user's change set on a
    `landing: review` KB (ADR-0044 §1); from the CLI one over the operator's
    config. There is no index-only write: a row with no file is lost by the
    next index rebuild, and it skips every check the pipeline makes.
    """
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


def require_writable_kb(kb_service: Any, kb_name: str) -> None:
    """Raise unless ``kb_name`` is a KB this batch may write.

    The batch tools check once, before the first entry, so a read-only KB
    refuses the whole call with `KBReadOnlyError` -- a refusal the MCP
    dispatcher maps to its envelope -- rather than reporting one error per
    entry. The KB is found through the one resolver (#952). A KB that does
    not exist is not refused here: a KB the caller named was refused by the
    dispatcher already, so this is the plugin's default KB reached with a
    blank name, never answered KB_NOT_FOUND (#846); each create reports it,
    and an empty batch has nothing to report. `KBService` checks again on
    every create.
    """
    from pyrite.exceptions import KBNotFoundError, KBReadOnlyError

    try:
        kb_config = kb_service.config.require_kb(kb_name, db=kb_service.db)
    except KBNotFoundError:
        return
    if kb_config.read_only:
        raise KBReadOnlyError(f"KB is read-only: {kb_name}")
