"""FollowTheMoney (FtM) import/export for Pyrite journalism-investigation.

Enables interop with OCCRP's Aleph platform by converting between FtM JSON
entities and Pyrite KB entries.
"""

import json
import logging
from typing import Any

from .utils import parse_meta, persist_entry

logger = logging.getLogger(__name__)

# =========================================================================
# Schema mapping: FtM schema -> Pyrite entry_type
# =========================================================================

_FTM_TO_PYRITE_TYPE: dict[str, str] = {
    "Person": "person",
    "Organization": "organization",
    "Company": "organization",
    "LegalEntity": "organization",
    "Ownership": "ownership",
    "Membership": "membership",
    "Payment": "transaction",
    "RealEstate": "asset",
    "BankAccount": "account",
    "CourtCase": "legal_action",
}

_PYRITE_TO_FTM_SCHEMA: dict[str, str] = {
    "person": "Person",
    "organization": "Organization",
    "ownership": "Ownership",
    "membership": "Membership",
    "transaction": "Payment",
    "asset": "RealEstate",
    "account": "BankAccount",
    "legal_action": "CourtCase",
}


def _first(props: dict, key: str, default: str = "") -> str:
    """Get the first value from an FtM property list."""
    values = props.get(key, [])
    if values and isinstance(values, list):
        return str(values[0])
    return default


# =========================================================================
# ftm_to_pyrite
# =========================================================================


def ftm_to_pyrite(ftm_entity: dict) -> dict | None:
    """Convert a single FtM entity dict to a Pyrite entry dict.

    Returns None for unmappable schemas.
    """
    schema = ftm_entity.get("schema", "")
    entry_type = _FTM_TO_PYRITE_TYPE.get(schema)
    if entry_type is None:
        return None

    ftm_id = ftm_entity.get("id", "")
    props = ftm_entity.get("properties", {})
    title = _first(props, "name") or f"{schema} {ftm_id}"

    entry: dict[str, Any] = {
        "id": f"ftm-{ftm_id}",
        "title": title,
        "entry_type": entry_type,
        "metadata": {},
    }

    # Schema-specific property mapping
    if schema == "Person":
        meta = {}
        for key in (
            "nationality",
            "birthDate",
            "gender",
            "country",
            "firstName",
            "lastName",
            "idNumber",
        ):
            val = _first(props, key)
            if val:
                meta[key] = val
        entry["metadata"] = meta

    elif schema in ("Organization", "Company", "LegalEntity"):
        meta = {}
        for key in (
            "jurisdiction",
            "registrationNumber",
            "incorporationDate",
            "country",
            "address",
        ):
            val = _first(props, key)
            if val:
                meta[key] = val
        entry["metadata"] = meta

    elif schema == "Ownership":
        title = _first(props, "name") or f"Ownership {ftm_id}"
        entry["title"] = title
        meta: dict[str, Any] = {}
        for src_key, dst_key in [
            ("owner", "owner"),
            ("asset", "asset"),
            ("percentage", "percentage"),
            ("startDate", "start_date"),
            ("endDate", "end_date"),
        ]:
            val = _first(props, src_key)
            if val:
                meta[dst_key] = val
        entry["metadata"] = meta

    elif schema == "Membership":
        title = _first(props, "name") or f"Membership {ftm_id}"
        entry["title"] = title
        meta = {}
        for src_key, dst_key in [
            ("member", "person"),
            ("organization", "organization"),
            ("role", "role"),
            ("startDate", "start_date"),
            ("endDate", "end_date"),
        ]:
            val = _first(props, src_key)
            if val:
                meta[dst_key] = val
        entry["metadata"] = meta

    elif schema == "Payment":
        title = _first(props, "name") or f"Payment {ftm_id}"
        entry["title"] = title
        meta = {}
        for src_key, dst_key in [
            ("payer", "sender"),
            ("beneficiary", "receiver"),
            ("amount", "amount"),
            ("currency", "currency"),
            ("purpose", "purpose"),
        ]:
            val = _first(props, src_key)
            if val:
                meta[dst_key] = val
        entry["metadata"] = meta
        date = _first(props, "date")
        if date:
            entry["date"] = date

    elif schema == "RealEstate":
        meta: dict[str, Any] = {"asset_type": "real_estate"}
        for src_key, dst_key in [
            ("country", "jurisdiction"),
            ("registrationNumber", "registration_number"),
            ("address", "address"),
        ]:
            val = _first(props, src_key)
            if val:
                meta[dst_key] = val
        entry["metadata"] = meta

    elif schema == "BankAccount":
        meta: dict[str, Any] = {"account_type": "bank"}
        for src_key, dst_key in [
            ("bankName", "institution"),
            ("iban", "iban"),
            ("holder", "holder"),
            ("currency", "currency"),
        ]:
            val = _first(props, src_key)
            if val:
                meta[dst_key] = val
        entry["metadata"] = meta
        # Use bankName or iban as title if no name
        if entry["title"].startswith("BankAccount"):
            bank = _first(props, "bankName")
            iban = _first(props, "iban")
            if bank:
                entry["title"] = f"Account at {bank}"
            elif iban:
                entry["title"] = f"Account {iban}"

    elif schema == "CourtCase":
        meta = {}
        for src_key, dst_key in [
            ("caseNumber", "case_number"),
            ("court", "jurisdiction"),
        ]:
            val = _first(props, src_key)
            if val:
                meta[dst_key] = val
        entry["metadata"] = meta
        date = _first(props, "date")
        if date:
            entry["date"] = date

    return entry


# =========================================================================
# pyrite_to_ftm
# =========================================================================


def pyrite_to_ftm(entry: dict) -> dict | None:
    """Convert a Pyrite entry dict to an FtM JSON entity.

    Returns None for unmappable entry types.
    """
    entry_type = entry.get("entry_type", "")
    schema = _PYRITE_TO_FTM_SCHEMA.get(entry_type)
    if schema is None:
        return None

    meta = entry.get("metadata") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except (json.JSONDecodeError, TypeError):
            meta = {}

    entry_id = entry.get("id", "")
    # Strip ftm- prefix if present for the FtM ID
    ftm_id = entry_id[4:] if entry_id.startswith("ftm-") else entry_id
    title = entry.get("title", "")

    props: dict[str, list[str]] = {}

    if schema == "Person":
        props["name"] = [title]
        for key in (
            "nationality",
            "birthDate",
            "gender",
            "country",
            "firstName",
            "lastName",
            "idNumber",
        ):
            val = meta.get(key)
            if val:
                props[key] = [str(val)]

    elif schema == "Organization":
        props["name"] = [title]
        for key in (
            "jurisdiction",
            "registrationNumber",
            "incorporationDate",
            "country",
            "address",
        ):
            val = meta.get(key)
            if val:
                props[key] = [str(val)]

    elif schema == "Ownership":
        props["name"] = [title]
        for pyrite_key, ftm_key in [
            ("owner", "owner"),
            ("asset", "asset"),
            ("percentage", "percentage"),
            ("start_date", "startDate"),
            ("end_date", "endDate"),
        ]:
            val = meta.get(pyrite_key)
            if val:
                props[ftm_key] = [str(val)]

    elif schema == "Membership":
        props["name"] = [title]
        for pyrite_key, ftm_key in [
            ("person", "member"),
            ("organization", "organization"),
            ("role", "role"),
            ("start_date", "startDate"),
            ("end_date", "endDate"),
        ]:
            val = meta.get(pyrite_key)
            if val:
                props[ftm_key] = [str(val)]

    elif schema == "Payment":
        props["name"] = [title]
        for pyrite_key, ftm_key in [
            ("sender", "payer"),
            ("receiver", "beneficiary"),
            ("amount", "amount"),
            ("currency", "currency"),
            ("purpose", "purpose"),
        ]:
            val = meta.get(pyrite_key)
            if val:
                props[ftm_key] = [str(val)]
        date = entry.get("date")
        if date:
            props["date"] = [str(date)]

    elif schema == "RealEstate":
        props["name"] = [title]
        for pyrite_key, ftm_key in [
            ("jurisdiction", "country"),
            ("registration_number", "registrationNumber"),
            ("address", "address"),
        ]:
            val = meta.get(pyrite_key)
            if val:
                props[ftm_key] = [str(val)]

    elif schema == "BankAccount":
        props["name"] = [title]
        for pyrite_key, ftm_key in [
            ("institution", "bankName"),
            ("iban", "iban"),
            ("holder", "holder"),
            ("currency", "currency"),
        ]:
            val = meta.get(pyrite_key)
            if val:
                props[ftm_key] = [str(val)]

    elif schema == "CourtCase":
        props["name"] = [title]
        for pyrite_key, ftm_key in [
            ("case_number", "caseNumber"),
            ("jurisdiction", "court"),
        ]:
            val = meta.get(pyrite_key)
            if val:
                props[ftm_key] = [str(val)]
        date = entry.get("date")
        if date:
            props["date"] = [str(date)]

    return {
        "id": ftm_id,
        "schema": schema,
        "properties": props,
    }


# =========================================================================
# import_ftm
# =========================================================================


def import_ftm(
    db,
    kb_name: str,
    ftm_entities: list[dict],
    *,
    dry_run: bool = False,
    kb_service: Any = None,
) -> dict:
    """Import a batch of FtM entities into a KB.

    `kb_service` (MCP) writes each entry as a file through the KB service;
    None (the CLI) writes the index alone -- see `utils.persist_entry`.

    Returns summary dict with counts and entry details.
    """
    imported = 0
    skipped = 0
    unmapped = 0
    errors = 0
    unmapped_schemas: list[str] = []
    entries: list[dict[str, Any]] = []

    for ftm_entity in ftm_entities:
        schema = ftm_entity.get("schema", "")

        # Convert
        pyrite_entry = ftm_to_pyrite(ftm_entity)
        if pyrite_entry is None:
            unmapped += 1
            if schema and schema not in unmapped_schemas:
                unmapped_schemas.append(schema)
            logger.info("Skipping unmappable FtM schema: %s", schema)
            continue

        entry_id = pyrite_entry["id"]

        # Check for duplicates
        existing = db.get_entry(entry_id, kb_name)
        if existing is not None:
            skipped += 1
            entries.append(
                {
                    "id": entry_id,
                    "title": pyrite_entry["title"],
                    "type": pyrite_entry["entry_type"],
                    "status": "skipped",
                }
            )
            continue

        if dry_run:
            imported += 1
            entries.append(
                {
                    "id": entry_id,
                    "title": pyrite_entry["title"],
                    "type": pyrite_entry["entry_type"],
                    "status": "would_import",
                }
            )
            continue

        # Persist
        try:
            entry_data: dict[str, Any] = {
                "id": entry_id,
                "kb_name": kb_name,
                "title": pyrite_entry["title"],
                "entry_type": pyrite_entry["entry_type"],
                "metadata": pyrite_entry.get("metadata", {}),
            }
            if "date" in pyrite_entry:
                entry_data["date"] = pyrite_entry["date"]
            persist_entry(db, kb_service, entry_data)
            imported += 1
            entries.append(
                {
                    "id": entry_id,
                    "title": pyrite_entry["title"],
                    "type": pyrite_entry["entry_type"],
                    "status": "imported",
                }
            )
        except Exception as exc:
            logger.error("Error importing FtM entity %s: %s", ftm_entity.get("id"), exc)
            errors += 1
            entries.append(
                {
                    "id": entry_id,
                    "title": pyrite_entry.get("title", ""),
                    "type": pyrite_entry.get("entry_type", ""),
                    "status": "error",
                }
            )

    return {
        "imported": imported,
        "skipped": skipped,
        "unmapped": unmapped,
        "unmapped_schemas": unmapped_schemas,
        "errors": errors,
        "entries": entries,
    }


# =========================================================================
# export_ftm
# =========================================================================

# All entry types that can be exported
_EXPORTABLE_TYPES = list(_PYRITE_TO_FTM_SCHEMA.keys())


def export_ftm(
    db,
    kb_name: str,
    *,
    entry_types: list[str] | None = None,
) -> list[dict]:
    """Export KB entries as FtM JSON.

    Args:
        db: PyriteDB instance.
        kb_name: KB to export from.
        entry_types: Optional filter — only export these entry types.

    Returns:
        List of FtM entity dicts.
    """
    types_to_export = entry_types if entry_types else _EXPORTABLE_TYPES
    result: list[dict] = []

    for etype in types_to_export:
        if etype not in _PYRITE_TO_FTM_SCHEMA:
            continue
        rows = db.list_entries(kb_name=kb_name, entry_type=etype, limit=5000)
        for row in rows:
            meta = parse_meta(row)
            entry = {
                "id": row.get("id", ""),
                "title": row.get("title", ""),
                "entry_type": etype,
                "date": row.get("date"),
                "metadata": meta,
            }
            ftm = pyrite_to_ftm(entry)
            if ftm is not None:
                result.append(ftm)

    return result
