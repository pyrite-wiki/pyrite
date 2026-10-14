"""Promote a corroborated claim to an edge-entity (ownership, membership, funding)."""

import logging
from typing import Any

from pyrite.exceptions import public_text
from pyrite.schema import generate_entry_id
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB

logger = logging.getLogger(__name__)
# Under `pyrite`, where configure_logging puts the handler: a record on this
# module's own logger would fall to the last-resort handler (#848).
_masked_log = logging.getLogger("pyrite.ext.journalism_investigation")

PROMOTABLE_STATUSES = {"corroborated", "partially_verified"}
VALID_EDGE_TYPES = {"ownership", "membership", "funding"}

# Each edge type's own required relationship endpoints (validate_investigation_entry,
# extensions/journalism-investigation/src/pyrite_journalism_investigation/validators.py,
# has always declared these -- #424: promote_claim_to_edge never actually set them, a gap
# invisible until #379 fixed the validator's signature and validation started running for
# real). The caller (CLI option, MCP argument) supplies these by name in `endpoint_fields`.
EDGE_TYPE_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "ownership": ("owner", "asset"),
    "membership": ("person", "organization"),
    "funding": ("funder", "recipient"),
}


def _endpoint_fields_problem(edge_type: str, endpoint_fields: Any) -> str | None:
    """Why `endpoint_fields` is unusable, or None. Only the edge type's own
    endpoint names are accepted, each a string: the value comes straight from
    an MCP argument and is passed to create_entry, so an extra key could set
    any other field or collide with create_entry's own arguments."""
    if not isinstance(endpoint_fields, dict):
        return "endpoint_fields must be an object of field names to values"
    allowed = EDGE_TYPE_REQUIRED_FIELDS.get(edge_type, ())
    extra = sorted(k for k in endpoint_fields if k not in allowed)
    if extra:
        return (
            f"endpoint_fields for '{edge_type}' accepts only {', '.join(allowed)}; "
            f"not: {', '.join(map(str, extra))}"
        )
    not_strings = sorted(k for k, v in endpoint_fields.items() if not isinstance(v, str))
    if not_strings:
        return f"endpoint_fields values must be strings: {', '.join(not_strings)}"
    return None


def _missing_endpoint_fields(edge_type: str, endpoint_fields: dict[str, str]) -> list[str]:
    """Required fields for `edge_type` that are absent, empty, or
    whitespace-only in `endpoint_fields`. A whitespace-only string (`"   "`)
    is truthy in Python, so `not endpoint_fields.get(f)` alone would let it
    through as "present" -- `.strip()` catches it the same as an empty
    string or a missing key."""
    required = EDGE_TYPE_REQUIRED_FIELDS.get(edge_type, ())
    return [f for f in required if not endpoint_fields.get(f, "").strip()]


def promote_claim_to_edge(
    *,
    db: PyriteDB,
    kb_name: str,
    claim_id: str,
    edge_type: str,
    kb_service: KBService,
    endpoint_fields: dict[str, str] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Convert a corroborated claim into an edge-entity entry.

    Args:
        db: Database instance for reading the claim.
        kb_name: Knowledge base name.
        claim_id: ID of the claim entry to promote.
        edge_type: Edge type to create (ownership, membership, funding).
        kb_service: KBService for creating the new entry.
        endpoint_fields: The edge type's own required relationship fields --
            ``{"owner": ..., "asset": ...}`` for ownership, ``{"funder": ...,
            "recipient": ...}`` for funding, ``{"person": ..., "organization": ...}``
            for membership. Validated up front (same check for a real run and a
            dry run) so a promotion missing them is refused with a clear message
            rather than surfacing the plugin validator's raw SchemaViolationError,
            or -- pre-#424 -- silently creating an entry the validator would have
            refused if it had ever run.
        dry_run: If True, return what would be created without creating.

    Returns:
        Result dict with created entry info, or error dict.
    """
    if endpoint_fields is None:
        endpoint_fields = {}

    # Validate edge_type
    if edge_type not in VALID_EDGE_TYPES:
        return {"error": f"Invalid edge_type: {edge_type}. Must be one of {VALID_EDGE_TYPES}"}

    # Load the claim from DB
    claim = db.get_entry(claim_id, kb_name)
    if claim is None:
        return {"error": f"Claim not found: {claim_id}"}

    # Verify claim type
    if claim.get("entry_type") != "claim":
        return {"error": f"Entry {claim_id} is not a claim (type: {claim.get('entry_type')})"}

    # Check claim status from metadata
    metadata = claim.get("metadata", {})
    claim_status = metadata.get("claim_status", "unverified")
    if claim_status not in PROMOTABLE_STATUSES:
        return {
            "error": (
                f"Claim {claim_id} has status '{claim_status}'. "
                f"Only claims with status {PROMOTABLE_STATUSES} can be promoted."
            )
        }

    # The edge type's own relationship fields -- checked here, before dry_run
    # branches, so a dry run refuses bad or missing endpoints with the same
    # clear message a real run gives, rather than the plugin validator's raw
    # SchemaViolationError. This is *not* the whole story: the KB's schema,
    # plugin validators, the exists check and the read-only check all run
    # only through kb_service below, which is why both branches build and
    # submit the same spec to kb_service (#427) instead of the dry run
    # returning early once these endpoint checks pass.
    problem = _endpoint_fields_problem(edge_type, endpoint_fields)
    if problem:
        return {"error": problem}
    missing = _missing_endpoint_fields(edge_type, endpoint_fields)
    if missing:
        required = EDGE_TYPE_REQUIRED_FIELDS[edge_type]
        return {
            "error": (
                f"Promoting to '{edge_type}' requires {', '.join(required)}; "
                f"missing: {', '.join(missing)}"
            )
        }

    # Derive edge entry properties from the claim
    claim_title = claim.get("title", "")
    edge_title = f"{claim_title} [{edge_type}]"
    edge_id = generate_entry_id(edge_title)
    importance = claim.get("importance", 5)

    # One spec, fed to kb_service by both branches: a dry run validates
    # exactly what a real run would write.
    spec = {
        "id": edge_id,
        "title": edge_title,
        "entry_type": edge_type,
        "body": f"Promoted from claim [[{claim_id}]].",
        "importance": importance,
        "links": [
            {"target": claim_id, "relation": "sourced_from"},
        ],
        **endpoint_fields,
    }

    if dry_run:
        # bulk_create_entries(validate_only=True) skips the read-only check
        # (it has to: a validate-only call against a KB with no write access
        # is exactly how a caller previews one), so it is checked here,
        # matching _writable_kb's own message, before the KB schema and
        # plugin validators run.
        kb_config = kb_service.config.get_kb(kb_name)
        if not kb_config:
            return {"error": f"KB not found: {kb_name}"}
        if kb_config.read_only:
            return {"error": f"KB is read-only: {kb_name}"}

        result = kb_service.bulk_create_entries(kb_name, [spec], validate_only=True)[0]
        if not result.get("valid"):
            return {"error": result.get("error", "validation failed")}

        proposed = {
            "entry_id": result["entry_id"],
            "title": edge_title,
            "edge_type": edge_type,
            "importance": importance,
            "sourced_from": claim_id,
            **endpoint_fields,
        }
        return {
            "dry_run": True,
            "proposed": proposed,
            "edge_type": edge_type,
            "source_claim": claim_id,
        }

    # Create the edge-entity entry
    try:
        kb_service.create(kb_name, spec)
        return {
            "created": edge_id,
            "edge_type": edge_type,
            "title": edge_title,
            "source_claim": claim_id,
        }
    except Exception as e:
        # A StorageError/PluginError/ConfigError (or a subclass that doesn't
        # set its own) can carry server-side detail in str(e) -- a real
        # path, a driver's own text. public_message, when set, is what
        # every other transport already shows (ADR-0037 theme 2 round 2,
        # item 1); the real detail still reaches the log.
        return {"error": public_text(e, log=_masked_log)}
