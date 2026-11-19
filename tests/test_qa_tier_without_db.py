"""Tier preflight has no database, and no need to construct an invalid service."""

import pytest

from pyrite.exceptions import AINotConfiguredError, InvalidQATierError
from pyrite.services.qa_service import require_qa_tier


@pytest.mark.parametrize("tier", [True, 0, 3])
def test_invalid_tier_does_not_consult_the_model(tier):
    def not_called():
        raise AssertionError("availability was evaluated before validating the tier")

    with pytest.raises(InvalidQATierError):
        require_qa_tier(tier, llm_available=not_called)


def test_structural_tier_needs_neither_index_nor_model():
    def not_called():
        raise AssertionError("tier1 consulted a model")

    require_qa_tier(1, llm_available=not_called)


def test_model_tier_uses_the_existing_refusal():
    with pytest.raises(AINotConfiguredError):
        require_qa_tier(2, llm_available=lambda: False)
    require_qa_tier(2, llm_available=lambda: True)
