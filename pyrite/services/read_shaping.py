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


# --- The CLI search row (#54) -------------------------------------------------

#: Every key a `pyrite search` / `pyrite-read search` result row can carry: the
#: index columns, the body (only with `--include-body` or when asked for), and
#: what the mode measured. `PYRITE_SEARCH_FIELDS` is checked against this, so a
#: misspelt name is refused instead of silently dropping a column.
SEARCH_ROW_FIELDS: tuple[str, ...] = (
    "id", "kb_name", "entry_type", "title", "body", "summary", "file_path", "date",
    "importance", "status", "location", "lifecycle", "metadata", "created_at",
    "updated_at", "indexed_at", "created_by", "modified_by", "snippet",
    "rank", "rrf_score", "similarity", "distance", "legs",
)  # fmt: skip

#: What a caller needs to choose the next read: which entry (`id` + `kb_name`,
#: since an id alone is ambiguous across KBs), what it is, why it matched, and
#: the score key the mode produced. `project_fields` skips a key a row lacks,
#: so a keyword row carries `rank`, a semantic row `similarity`, a hybrid row
#: `rrf_score` as well. `legs` (hybrid: which legs returned the row) is how an
#: `rrf_score` is read and #668 made it part of the row's contract. `distance`
#: (backend-specific, leaving the response) is not here.
SEARCH_DEFAULT_FIELDS: tuple[str, ...] = (
    "id", "kb_name", "entry_type", "title", "snippet", "rank", "rrf_score", "similarity",
    "legs",
)  # fmt: skip

SEARCH_FIELDS_ENV = "PYRITE_SEARCH_FIELDS"


class SearchFieldsError(ValueError):
    """`PYRITE_SEARCH_FIELDS` names a field a search row cannot carry."""


def resolve_search_fields(
    fields: str | None, env_value: str | None = None, *, include_body: bool = False
) -> list[str] | None:
    """The field list for a search row, or ``None`` for the whole row.

    ``fields`` (``--fields``) wins over ``env_value`` (``PYRITE_SEARCH_FIELDS``),
    which wins over the lean default. ``*`` anywhere in either means the whole
    row. A name in the variable that no row carries raises
    :class:`SearchFieldsError`; ``--fields`` keeps its old leniency (an unknown
    name is skipped), so existing scripts are not broken by this change.
    """
    named = parse_fields_param(fields)
    if named is None:
        named = parse_fields_param(env_value)
        unknown = [f for f in named or () if f != "*" and f not in SEARCH_ROW_FIELDS]
        if unknown:
            raise SearchFieldsError(
                f"{SEARCH_FIELDS_ENV} names no search-row field: {', '.join(unknown)}. "
                f"Known: {', '.join(SEARCH_ROW_FIELDS)}, or * for the whole row."
            )
    if named is None:
        named = list(SEARCH_DEFAULT_FIELDS) + (["body"] if include_body else [])
    return None if "*" in named else named
