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


def parse_cli_fields(fields: str | None) -> list[str] | None:
    """`--fields` on the CLI: ``parse_fields_param``, plus ``*`` meaning every field.

    ``*`` anywhere in the list is "no projection" (``None``), the same as not
    passing the flag but explicit, so a caller can override ``PYRITE_SEARCH_FIELDS``.
    Every CLI command that takes ``--fields`` calls this one function (REST and
    MCP keep ``parse_fields_param``, where ``*`` is just an unknown name).
    """
    parsed = parse_fields_param(fields)
    return None if parsed and "*" in parsed else parsed


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
#: what the mode measured. It only silences the unknown-name warning: a name in
#: this list that every row on the page lacks (`status`) is not warned about.
#: Nothing is refused.
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


def resolve_search_fields(
    fields: str | None, env_value: str | None = None, *, include_body: bool = False
) -> list[str] | None:
    """The field list for a search row, or ``None`` for the whole row.

    ``fields`` (``--fields``) wins over ``env_value`` (``PYRITE_SEARCH_FIELDS``),
    which wins over the lean default. ``*`` anywhere in either means the whole
    row. Names are case-sensitive and never refused here: a row can carry keys
    no fixed list knows (semantic rows from a KB's index carry ``fips``,
    ``state``, ``rowid``), so the check is made against the rows actually
    returned, by :func:`unmatched_search_fields`.
    """
    named = parse_cli_fields(fields)
    if fields and parse_fields_param(fields):
        return named
    named = parse_cli_fields(env_value)
    if parse_fields_param(env_value):
        return named
    return list(SEARCH_DEFAULT_FIELDS) + (["body"] if include_body else [])


def unmatched_search_fields(rows: list[dict[str, Any]], named: list[str] | None) -> list[str]:
    """Requested names no returned row carries and no search row is known to.

    A known name a row lacks (``status`` on an entry with none) is fine: the
    row is simply without it. A name that is neither a known column nor present
    in any row is a typo or a case slip (``Title``), and the caller says so.
    """
    return [f for f in named or () if f not in SEARCH_ROW_FIELDS and not any(f in r for r in rows)]
