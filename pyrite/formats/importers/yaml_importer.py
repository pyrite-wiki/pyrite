"""YAML format importer."""

from typing import Any

from pyrite.exceptions import CALLER_REQUEST, caller_owned
from pyrite.utils.yaml import load_yaml_document


def import_yaml(data: str | bytes) -> list[dict[str, Any]]:
    """Parse YAML array of entries.

    Expected format: array of objects with at least {title, body}.
    Accepts {"entries": [...]} wrapper or bare list.
    """
    if isinstance(data, bytes):
        try:
            data = data.decode("utf-8")
        except UnicodeDecodeError as e:  # the caller's upload: its bytes, its detail
            raise caller_owned(e) from None

    parsed = load_yaml_document(data, origin=CALLER_REQUEST)  # the caller's upload
    if parsed is None:
        return []

    if isinstance(parsed, dict):
        if "entries" in parsed:
            parsed = parsed["entries"]
        else:
            parsed = [parsed]

    if not isinstance(parsed, list):
        parsed = [parsed]

    entries = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        entry = {
            "id": item.get("id", ""),
            "title": item.get("title", "Untitled"),
            "body": item.get("body", ""),
            "entry_type": item.get("entry_type", item.get("type", "note")),
            "tags": item.get("tags", []),
        }
        for key in ("date", "importance", "summary", "status", "sources", "links", "metadata"):
            if key in item:
                entry[key] = item[key]
        entries.append(entry)
    return entries
