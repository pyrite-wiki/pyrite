"""Bulk edge creation for journalism investigations.

Provides batch validation and creation of connection entries
(ownership, membership, funding). Every entry is created through
`KBService`, as every tool write is: a file on disk and an
index row, with the read-only, id and validator checks of the pipeline.
"""

from typing import Any

from pyrite.exceptions import PyriteError, refusal_fields
from pyrite.schema import generate_entry_id
from pyrite.utils.errors import public_text

from .utils import persist_entry, require_writable_kb, strip_wikilink

# Required fields per edge type and their roles for title generation.
EDGE_TYPES: dict[str, dict[str, Any]] = {
    "ownership": {
        "required": ("owner", "asset"),
        "title_template": "{owner} owns {asset}",
    },
    "membership": {
        "required": ("person", "organization"),
        "title_template": "{person} member of {organization}",
    },
    "funding": {
        "required": ("funder", "recipient"),
        "title_template": "{funder} funds {recipient}",
    },
}


# Keys that belong to the entry itself (`KBService.create_entry`'s own
# parameters), never to an edge's fields.
RESERVED_FIELDS = frozenset({"id", "title", "body", "entry_type", "type", "kb_name", "entry_id"})


def _not_an_object(field: str, subject: str) -> dict[str, Any]:
    """The refusal row for a value that must be a JSON object and is not."""
    return {
        "field": field,
        "rule": "type",
        "message": f"{subject} must be an object",
        "expected": "object",
    }


def validate_edge_batch(edges: list[dict]) -> dict[str, Any]:
    """Validate a batch of edge definitions before creating them.

    Each edge dict: ``{"type": str, "fields": dict, "title": optional str}``.

    Returns ``{"valid": int, "invalid": int, "errors": [...]}``. Each entry
    names the edge it refused (``index``, ``edge``) and carries ``errors``: one
    row per refused field, in the shape `docs/json-contracts.md` defines for a
    refusal row. The create path copies those rows onto the record it refused,
    so ``entries[i].errors`` holds rows on this branch too.
    """
    valid = 0
    invalid = 0
    errors: list[dict[str, Any]] = []

    for idx, edge in enumerate(edges):
        rows: list[dict[str, Any]] = []
        # The shape checks come first: every check after them reads the edge
        # as a mapping. A non-object edge has no field to name, so its row
        # names the edge itself (`field: "edge"`); the per-edge `errors` list
        # is always rows, so a caller reads one shape for every refusal.
        if not isinstance(edge, dict):
            invalid += 1
            errors.append(
                {"index": idx, "edge": edge, "errors": [_not_an_object("edge", "An edge")]}
            )
            continue
        if not isinstance(edge.get("fields", {}) or {}, dict):
            invalid += 1
            errors.append(
                {"index": idx, "edge": edge, "errors": [_not_an_object("fields", "'fields'")]}
            )
            continue
        edge_type = edge.get("type", "")

        if edge_type not in EDGE_TYPES:
            allowed = sorted(EDGE_TYPES)
            rows.append(
                {
                    "field": "type",
                    "rule": "enum",
                    "message": f"Invalid type '{edge_type}'. Must be one of: {', '.join(allowed)}",
                    "got": edge_type,
                    "expected": allowed,
                }
            )
        else:
            fields = edge.get("fields", {}) or {}
            for req in EDGE_TYPES[edge_type]["required"]:
                if not fields.get(req):
                    rows.append(
                        {
                            "field": req,
                            "rule": "required",
                            "message": f"Missing required field: {req}",
                        }
                    )
            for key in sorted(RESERVED_FIELDS & set(fields)):
                rows.append(
                    {
                        "field": key,
                        "rule": "reserved",
                        "message": f"Reserved field name: {key} (set it on the edge, not in fields)",
                    }
                )

        if rows:
            invalid += 1
            errors.append({"index": idx, "edge": edge, "errors": rows})
        else:
            valid += 1

    return {"valid": valid, "invalid": invalid, "errors": errors}


def _generate_title(edge_type: str, fields: dict[str, Any]) -> str:
    """Auto-generate a title from edge type and fields."""
    spec = EDGE_TYPES[edge_type]
    template = spec["title_template"]
    # Strip wikilinks from field values for readable titles
    values = {key: strip_wikilink(fields[key]) for key in spec["required"]}
    return template.format(**values)


def create_edge_batch(
    kb_service,
    kb_name: str,
    edges: list[dict],
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create connection entries in batch, through ``kb_service``.

    Auto-generates titles and entry IDs if not provided. Skips
    duplicates (entries whose ID already exists). If *dry_run* is
    ``True``, validates only and returns what would be created, writing
    nothing. A read-only or unknown KB refuses the whole batch (raises).

    Returns ``{"created": int, "skipped": int, "errors": int,
    "entries": [...]}``.
    """
    if not dry_run:
        require_writable_kb(kb_service, kb_name)
    db = kb_service.db
    created = 0
    skipped = 0
    error_count = 0
    entries: list[dict[str, Any]] = []

    for edge in edges:
        # Validate first: a malformed edge is an error for that edge alone.
        validation = validate_edge_batch([edge])
        edge_type = edge.get("type", "") if isinstance(edge, dict) else ""
        fields = (edge.get("fields", {}) or {}) if isinstance(edge, dict) else {}
        if validation["invalid"] > 0:
            error_count += 1
            entries.append(
                {
                    "id": None,
                    "title": None,
                    "type": edge_type,
                    "status": "error",
                    "errors": validation["errors"][0]["errors"],
                }
            )
            continue

        # Generate title and ID
        title = edge.get("title") or _generate_title(edge_type, fields)
        entry_id = generate_entry_id(title)

        if dry_run:
            entries.append(
                {
                    "id": entry_id,
                    "title": title,
                    "type": edge_type,
                    "status": "would_create",
                }
            )
            continue

        # Check for existing entry (duplicate detection)
        existing = db.get_entry(entry_id, kb_name)
        if existing is not None:
            skipped += 1
            entries.append(
                {
                    "id": entry_id,
                    "title": title,
                    "type": edge_type,
                    "status": "skipped",
                }
            )
            continue

        # Through the KB service, from MCP and the CLI alike: a file and an
        # index row, landing by the rule (ADR-0044). The caller's fields go
        # under `metadata`, where create_entry refuses a managed field
        # (#1010). An edge the write refuses is reported and the batch goes
        # on: the edges before it are already written. A file at the id
        # with no index row is such a refusal (ENTRY_EXISTS): create never
        # replaces, and the caller is told rather than shown a skip.
        try:
            persist_entry(
                kb_service,
                {
                    "id": entry_id,
                    "kb_name": kb_name,
                    "title": title,
                    "entry_type": edge_type,
                    "metadata": dict(fields),
                },
            )
        except PyriteError as e:
            error_count += 1
            entries.append(
                {
                    "id": entry_id,
                    "title": title,
                    "type": edge_type,
                    "status": "error",
                    "error": public_text(e),
                    "error_code": e.error_code,
                    **refusal_fields(e),
                }
            )
            continue

        created += 1
        entries.append(
            {
                "id": entry_id,
                "title": title,
                "type": edge_type,
                "status": "created",
            }
        )

    return {
        "created": created,
        "skipped": skipped,
        "errors": error_count,
        "entries": entries,
    }
