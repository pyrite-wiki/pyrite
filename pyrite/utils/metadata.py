"""Metadata parsing utilities."""

import json
from collections.abc import Iterable, Mapping
from typing import Any


def parse_metadata(raw: Any) -> dict[str, Any]:
    """Parse metadata that may be a JSON string, dict, or None.

    Returns an empty dict for any unparseable input.
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def declared_over_bag(entry: Mapping[str, Any], keys: Iterable[str]) -> dict[str, Any]:
    """An entry dict's ``metadata`` bag with the named fields read from the
    dict's own keys where it carries them.

    A single-entry read (``KBService.get_entry``) spells a declared field at
    its own name only and keeps ``metadata`` for keys the type does not
    declare (#1025, ADR-0051 proposed amendment 2026-10-20); an index row
    carries such a field in its bag and, lifted, at its own name. A reader
    that wants a collection's ``query`` or a task's ``evidence`` from either
    shape takes the dict's own key first and the bag second. The returned
    top-level dictionary is a projection: adding declared keys must not
    write them into the entry's undeclared metadata bag.
    """
    out = dict(parse_metadata(entry.get("metadata")))
    for key in keys:
        value = entry.get(key)
        if value is not None and value != "":
            out[key] = value
    return out
