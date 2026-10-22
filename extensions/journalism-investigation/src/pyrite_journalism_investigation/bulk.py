"""Bulk edge creation for journalism investigations.

Provides batch validation and creation of connection entries
(ownership, membership, funding) with atomic validation and
efficient single-pass indexing.
"""

from typing import Any

from pyrite.exceptions import PyriteError
from pyrite.schema import generate_entry_id
from pyrite.utils.errors import public_text

from .utils import persist_entry, strip_wikilink

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


def validate_edge_batch(edges: list[dict]) -> dict[str, Any]:
    """Validate a batch of edge definitions before creating them.

    Each edge dict: ``{"type": str, "fields": dict, "title": optional str}``.

    Returns ``{"valid": int, "invalid": int, "errors": [...]}``.
    """
    valid = 0
    invalid = 0
    errors: list[dict[str, Any]] = []

    for idx, edge in enumerate(edges):
        edge_errors: list[str] = []
        edge_type = edge.get("type", "")

        if edge_type not in EDGE_TYPES:
            edge_errors.append(
                f"Invalid type '{edge_type}'. Must be one of: {', '.join(sorted(EDGE_TYPES))}"
            )
        else:
            fields = edge.get("fields", {}) or {}
            for req in EDGE_TYPES[edge_type]["required"]:
                if not fields.get(req):
                    edge_errors.append(f"Missing required field: {req}")

        if edge_errors:
            invalid += 1
            errors.append({"index": idx, "edge": edge, "errors": edge_errors})
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
    db,
    kb_name: str,
    edges: list[dict],
    *,
    dry_run: bool = False,
    kb_service: Any = None,
) -> dict[str, Any]:
    """Create connection entries in batch.

    Auto-generates titles and entry IDs if not provided. Skips
    duplicates (entries whose ID already exists). If *dry_run* is
    ``True``, validates only and returns what would be created.

    Returns ``{"created": int, "skipped": int, "errors": int,
    "entries": [...]}``.
    """
    created = 0
    skipped = 0
    error_count = 0
    entries: list[dict[str, Any]] = []

    for edge in edges:
        edge_type = edge.get("type", "")
        fields = edge.get("fields", {}) or {}

        # Validate first
        validation = validate_edge_batch([edge])
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

        # Build metadata from all fields
        metadata = dict(fields)

        # Through the KB service over MCP (a file, landing by the rule);
        # the index alone from the CLI (`utils.persist_entry`). An edge the
        # write refuses is reported and the batch goes on: the edges before
        # it are already written.
        try:
            persist_entry(
                db,
                kb_service,
                {
                    "id": entry_id,
                    "kb_name": kb_name,
                    "title": title,
                    "entry_type": edge_type,
                    "metadata": metadata,
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
                    "errors": [public_text(e)],
                    "error_code": e.error_code,
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
