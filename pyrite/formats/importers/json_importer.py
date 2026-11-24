"""JSON format importer."""

import json
from typing import Any

from pyrite.exceptions import caller_owned


def import_json(data: str | bytes) -> list[dict[str, Any]]:
    """Parse a JSON array of entries into entry specs.

    Expected format: array of objects with at least {title}; the wrapped form
    {"entries": [...]} and a single object are accepted too. `type` is read as
    `entry_type` when `entry_type` is absent.

    Every other key is carried through unchanged -- the entry's own fields
    (`role`, `kind`, `status`, ...) and ADR-0034's truncation keys alike. The
    importer only parses: deciding what a valid entry is, refusing a body
    marked truncated and stripping the marker are the write pipeline's job
    (`KBService`, #378). A key whitelist here used to drop type-specific
    fields before validation could see them, so an import wrote entries that
    `pyrite create` would have refused.
    """
    if isinstance(data, bytes):
        try:
            data = data.decode("utf-8")
        except UnicodeDecodeError as e:  # the caller's upload: its bytes, its detail
            raise caller_owned(e) from None

    try:
        parsed = json.loads(data)
    except ValueError as e:  # json's text quotes only the caller's upload
        raise caller_owned(e) from None

    if isinstance(parsed, dict):
        # Single entry or wrapped format
        if "entries" in parsed:
            parsed = parsed["entries"]
        else:
            parsed = [parsed]

    entries = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        entry = {k: v for k, v in item.items() if k != "type"}
        entry.setdefault("id", "")
        entry.setdefault("title", "Untitled")
        entry.setdefault("body", "")
        entry["entry_type"] = item.get("entry_type", item.get("type", "note"))
        entry.setdefault("tags", [])
        entries.append(entry)
    return entries
