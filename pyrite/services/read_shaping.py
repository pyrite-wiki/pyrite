"""One field-projection rule for every read surface (issue #193).

`?fields=` on the REST routes, `--fields` on the CLI and the MCP `fields`
argument all mean the same thing: return the named fields, always keep the
identity pair, and never invent a key the record does not have. Four spellings
of that rule existed and drifted apart (#179, #192, #134); this is the one
helper they call.

The identity pair is two fields, not four. That matches the MCP contract
`_project_fields` has always implemented, and the REST routes return the
projected payload directly (a `JSONResponse`), so no response model's required
set applies to a projection.
"""

from __future__ import annotations

from typing import Any

# Always kept, even when the caller's `fields` omits them: a row without an `id`
# or a `kb_name` cannot be attributed to an entry or to a KB, which is how
# `found` and `not_found` came to disagree and how the CLI dropped both (#134,
# #179, #192).
IDENTITY_FIELDS: tuple[str, ...] = ("id", "kb_name")


def parse_fields_param(fields: str | list[str] | None) -> list[str] | None:
    """Split a comma-separated field list into a clean list, or ``None``.

    A list of strings (the JSON body of ``POST /entries/batch``) is cleaned the
    same way, item by item, so both forms of a request mean the same thing.

    ``None``, ``""``, ``","`` and ``" "`` all mean "no projection". Duplicates
    are dropped while the caller's order is kept.
    """
    if not fields:
        return None
    items = fields if isinstance(fields, list) else fields.split(",")
    parsed = [field.strip() for field in items if field.strip()]
    return list(dict.fromkeys(parsed)) or None


def project_fields(
    record: dict[str, Any],
    fields: list[str] | None,
    *,
    required: tuple[str, ...] = IDENTITY_FIELDS,
) -> dict[str, Any]:
    """Reduce ``record`` to ``fields`` plus ``required``, in that order.

    A falsy ``fields`` returns the record unchanged, so callers can pass every
    row through the helper without branching. Keys the record does not have are
    skipped rather than materialised as ``None``.
    """
    if not fields:
        return record
    keys = dict.fromkeys((*required, *fields))
    return {key: record[key] for key in keys if key in record}
