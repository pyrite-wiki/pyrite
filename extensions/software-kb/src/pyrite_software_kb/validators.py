"""Software KB validation rules."""

from pathlib import Path
from typing import Any

from pyrite.services.acting import is_local_process

from .entry_types import (
    ADR_STATUSES,
    BACKLOG_EFFORTS,
    BACKLOG_KINDS,
    BACKLOG_PRIORITIES,
    BACKLOG_STATUSES,
    COMPONENT_KINDS,
    CONVENTION_CATEGORIES,
    DESIGN_DOC_STATUSES,
    MILESTONE_STATUSES,
    RUNBOOK_KINDS,
    STANDARD_CATEGORIES,
    VALIDATION_CATEGORIES,
)


def validate_software_kb(
    entry_type: str, data: dict[str, Any], context: dict[str, Any]
) -> list[dict]:
    """Validate software-kb-specific rules."""
    errors: list[dict] = []

    if entry_type == "adr":
        _validate_adr(data, errors)
    elif entry_type == "design_doc":
        _validate_design_doc(data, errors)
    elif entry_type == "standard":
        _validate_standard(data, errors)
    elif entry_type == "programmatic_validation":
        _validate_programmatic_validation(data, errors)
    elif entry_type == "development_convention":
        _validate_development_convention(data, errors)
    elif entry_type == "component":
        _validate_component(data, errors, context)
    elif entry_type == "backlog_item":
        _validate_backlog_item(data, errors)
    elif entry_type == "runbook":
        _validate_runbook(data, errors)
    elif entry_type == "milestone":
        _validate_milestone(data, errors)
    elif entry_type == "work_log":
        _validate_work_log(data, errors)

    return errors


def _validate_enum(
    data: dict, field: str, allowed: tuple, errors: list[dict], default: str = ""
) -> None:
    value = data.get(field, default)
    if value and value not in allowed:
        errors.append(
            {
                "field": field,
                "rule": "enum",
                "expected": list(allowed),
                "got": value,
            }
        )


def _validate_adr(data: dict[str, Any], errors: list[dict]) -> None:
    _validate_enum(data, "status", ADR_STATUSES, errors, "proposed")

    adr_number = data.get("adr_number")
    if adr_number is not None and adr_number != 0:
        try:
            if int(adr_number) < 1:
                errors.append(
                    {
                        "field": "adr_number",
                        "rule": "min_value",
                        "expected": ">= 1",
                        "got": adr_number,
                    }
                )
        except (TypeError, ValueError):
            errors.append(
                {
                    "field": "adr_number",
                    "rule": "type",
                    "expected": "integer",
                    "got": adr_number,
                }
            )

    status = data.get("status", "proposed")
    if status == "superseded" and not data.get("superseded_by"):
        errors.append(
            {
                "field": "superseded_by",
                "rule": "required_when_superseded",
                "expected": "non-empty superseded_by when status is 'superseded'",
                "got": data.get("superseded_by"),
            }
        )

    if not data.get("date"):
        errors.append(
            {
                "field": "date",
                "rule": "date_recommended",
                "expected": "date for ADR",
                "got": None,
                "severity": "warning",
            }
        )


def _validate_design_doc(data: dict[str, Any], errors: list[dict]) -> None:
    _validate_enum(data, "status", DESIGN_DOC_STATUSES, errors, "draft")


def _validate_standard(data: dict[str, Any], errors: list[dict]) -> None:
    _validate_enum(data, "category", STANDARD_CATEGORIES, errors)


def _validate_programmatic_validation(data: dict[str, Any], errors: list[dict]) -> None:
    _validate_enum(data, "category", VALIDATION_CATEGORIES, errors)
    if not data.get("check_command"):
        errors.append(
            {
                "field": "check_command",
                "rule": "check_command_recommended",
                "expected": "command to run for validation",
                "got": None,
                "severity": "warning",
            }
        )


def _validate_development_convention(data: dict[str, Any], errors: list[dict]) -> None:
    _validate_enum(data, "category", CONVENTION_CATEGORIES, errors)


def _validate_component(
    data: dict[str, Any], errors: list[dict], context: dict[str, Any] | None = None
) -> None:
    _validate_enum(data, "kind", COMPONENT_KINDS, errors)

    component_path = data.get("path")
    if not component_path:
        errors.append(
            {
                "field": "path",
                "rule": "path_recommended",
                "expected": "path linking to code",
                "got": None,
                "severity": "warning",
            }
        )
    elif context and context.get("kb_path") and is_local_process():
        # Only for the operator's own process (ADR-0052 N4). ``path:`` is
        # entry data and may name any place on the server's disk (absolute,
        # or ``..`` out of the KB): whether it exists is not the KB's to say
        # to a network caller, who gets no ``path`` finding either way.
        kb_root = Path(context["kb_path"])
        found = False
        # Walk up from KB root (max 4 levels) since component paths
        # are repo-relative but KB root may be a subdirectory
        candidate = kb_root
        for _ in range(5):
            if (candidate / component_path).exists():
                found = True
                break
            if candidate.parent == candidate:
                break
            candidate = candidate.parent
        if not found:
            errors.append(
                {
                    "field": "path",
                    "rule": "path_not_found",
                    "expected": f"path '{component_path}' to exist on disk",
                    "got": component_path,
                    "severity": "warning",
                }
            )


def _validate_backlog_item(data: dict[str, Any], errors: list[dict]) -> None:
    _validate_enum(data, "kind", BACKLOG_KINDS, errors)
    _validate_enum(data, "status", BACKLOG_STATUSES, errors, "proposed")
    _validate_enum(data, "priority", BACKLOG_PRIORITIES, errors, "medium")

    effort = data.get("effort", "")
    if effort and effort.upper() not in BACKLOG_EFFORTS:
        errors.append(
            {
                "field": "effort",
                "rule": "enum",
                "expected": list(BACKLOG_EFFORTS),
                "got": effort,
            }
        )

    rank = data.get("rank", 0)
    if rank is not None and rank != 0:
        try:
            if int(rank) < 0:
                errors.append(
                    {
                        "field": "rank",
                        "rule": "min_value",
                        "expected": ">= 0",
                        "got": rank,
                    }
                )
        except (TypeError, ValueError):
            errors.append(
                {
                    "field": "rank",
                    "rule": "type",
                    "expected": "integer",
                    "got": rank,
                }
            )


def _validate_milestone(data: dict[str, Any], errors: list[dict]) -> None:
    _validate_enum(data, "status", MILESTONE_STATUSES, errors, "open")


def _validate_work_log(data: dict[str, Any], errors: list[dict]) -> None:
    if not data.get("item_id"):
        errors.append(
            {
                "field": "item_id",
                "rule": "required",
                "expected": "backlog item ID",
                "got": None,
            }
        )


def _validate_runbook(data: dict[str, Any], errors: list[dict]) -> None:
    _validate_enum(data, "runbook_kind", RUNBOOK_KINDS, errors)
