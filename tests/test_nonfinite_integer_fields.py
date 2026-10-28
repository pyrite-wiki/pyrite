"""Non-finite YAML numbers follow the same fallback as other invalid integers."""

from decimal import Decimal

import pytest

from pyrite.models import NoteEntry, TaskEntry
from pyrite.models.core_types import QAAssessmentEntry
from pyrite.utils.parse import safe_int
from pyrite.utils.yaml import load_yaml


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan"), Decimal("Infinity")])
def test_nonfinite_safe_int_uses_the_callers_default(value):
    assert safe_int(value, 17) == 17


@pytest.mark.parametrize("literal", [".inf", "-.inf", ".nan"])
def test_nonfinite_common_fields_do_not_abort_loading(literal):
    meta = load_yaml(
        f"id: note-1\ntitle: Retain me\nimportance: {literal}\n_schema_version: {literal}\n"
    )
    entry = NoteEntry.from_frontmatter(meta, "The body is still readable.")
    assert entry.id == "note-1"
    assert entry.importance == 5
    assert entry._schema_version == 0
    assert entry.body == "The body is still readable."
    assert str(meta["importance"]) in {"inf", "-inf", "nan"}  # source value was not repaired


@pytest.mark.parametrize("literal", [".inf", "-.inf"])
def test_assessment_fields_fall_back_and_task_keeps_its_own_priority_policy(literal):
    task = TaskEntry.from_frontmatter(load_yaml(f"id: t\ntitle: Task\npriority: {literal}\n"), "")
    assessment = QAAssessmentEntry.from_frontmatter(
        load_yaml(
            f"id: a\ntitle: Assessment\ntier: {literal}\n"
            f"issues_found: {literal}\nissues_resolved: {literal}\n"
        ),
        "",
    )
    assert task.priority == 5
    assert (assessment.tier, assessment.issues_found, assessment.issues_resolved) == (1, 0, 0)


@pytest.mark.parametrize("literal", [".inf", "-.inf", ".nan"])
def test_extension_integer_fields_fall_back_on_nonfinite(literal):
    """Every integer field on core and extension entry types uses safe_int.

    A new integer field added to an entry type must be covered here so a
    non-finite value cannot crash loading.
    """
    from pyrite_social.entry_types import UserProfileEntry
    from pyrite_software_kb.entry_types import (
        ADREntry,
        BacklogItemEntry,
    )

    # Social: reputation, writeup_count
    profile = UserProfileEntry.from_frontmatter(
        load_yaml(
            f"id: u\ntitle: User\nreputation: {literal}\nwriteup_count: {literal}\n"
        ),
        "",
    )
    assert profile.reputation == 0
    assert profile.writeup_count == 0

    # Software-kb: adr_number, rank
    adr = ADREntry.from_frontmatter(
        load_yaml(f"id: a\ntitle: ADR\nadr_number: {literal}\n"), ""
    )
    assert adr.adr_number == 0

    backlog = BacklogItemEntry.from_frontmatter(
        load_yaml(f"id: b\ntitle: Item\nrank: {literal}\n"), ""
    )
    assert backlog.rank == 0


@pytest.mark.parametrize(
    ("value", "expected"), [("12", 12), (3.8, 3), (-2, -2), (None, 9), ("bad", 9)]
)
def test_finite_and_previously_invalid_inputs_are_unchanged(value, expected):
    assert safe_int(value, 9) == expected
