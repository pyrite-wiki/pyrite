"""What ends a software-kb type's live work is declared once, per type (#934)."""

from __future__ import annotations

import pytest
from pyrite_software_kb.entry_types import (
    ADR_STATUSES,
    BACKLOG_STATUSES,
    DESIGN_DOC_STATUSES,
    MILESTONE_STATUSES,
)
from pyrite_software_kb.plugin import SoftwareKBPlugin

from pyrite.models.task import TASK_RESOLVED_STATUSES
from pyrite.schema.core_types import settled_statuses

VOCABULARY = {
    "backlog_item": BACKLOG_STATUSES,
    "adr": ADR_STATUSES,
    "milestone": MILESTONE_STATUSES,
    "design_doc": DESIGN_DOC_STATUSES,
}


@pytest.mark.parametrize("type_name", sorted(VOCABULARY))
def test_every_declared_settled_status_is_in_the_types_own_vocabulary(type_name):
    declared = SoftwareKBPlugin().get_type_metadata()[type_name]["settled_statuses"]

    assert declared
    assert set(declared) <= set(VOCABULARY[type_name])
    assert settled_statuses(type_name) == frozenset(declared)


def test_backlog_settled_contains_what_unblocks_dependents_and_counts_epic_progress():
    assert SoftwareKBPlugin._RESOLVED_STATUSES <= settled_statuses("backlog_item")
    # The scan's set is wider on purpose: a superseded item is not live work,
    # yet it has never unblocked a dependent.
    assert "superseded" in settled_statuses("backlog_item")
    assert "superseded" not in SoftwareKBPlugin._RESOLVED_STATUSES


def test_a_live_status_is_never_declared_settled():
    assert not {"deferred", "proposed", "accepted", "in_progress", "review"} & settled_statuses(
        "backlog_item"
    )


def test_core_task_has_one_definition_of_resolved():
    assert settled_statuses("task") == frozenset(TASK_RESOLVED_STATUSES) == {"done", "cancelled"}
