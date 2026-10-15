"""Tests for LLM-assisted rubric evaluation.

Covers prompt construction, response parsing, evaluation flow,
and batching behavior.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

import pyrite.exceptions as _exceptions
from pyrite.services.llm_rubric_evaluator import LLMRubricEvaluator

#: getattr, so this file collects on a base without the class and verify-red
#: sees each not-run regime fail for its behaviour (no raise), not an import.
EvaluationFailedError = getattr(_exceptions, "EvaluationFailedError", LookupError)

#: The reply was made complete (every item answered) for #795; what these
#: tests check did not change.
complete_reply = pytest.mark.control(
    reason="existing behaviour; only the reply was completed (#795)"
)


@pytest.fixture
def mock_llm():
    """Mock LLMService with configurable status and complete."""
    llm = MagicMock()
    llm.status.return_value = {
        "configured": True,
        "provider": "anthropic",
        "model": "claude-sonnet-4-20250514",
    }
    llm.complete = AsyncMock(return_value="[]")
    return llm


@pytest.fixture
def evaluator(mock_llm):
    return LLMRubricEvaluator(mock_llm)


@pytest.fixture
def sample_entry():
    return {
        "id": "test-entry-1",
        "kb_name": "test-kb",
        "entry_type": "note",
        "title": "Test Entry",
        "body": "This is a test entry body with some content.",
        "metadata": json.dumps({"tags": ["test"]}),
    }


SAMPLE_RUBRIC_ITEMS = [
    "Entry body explains the why, not just the what",
    "Confidence level matches evidence strength",
]


def _reply(failing: dict[str, tuple[float, str]] | None = None, *, fence: bool = False) -> str:
    """A full answer: every item passes but those in ``failing``. The model
    must answer every item it was asked (#795); one of two is no answer."""
    rows = []
    for item in SAMPLE_RUBRIC_ITEMS:
        if failing and item in failing:
            confidence, reasoning = failing[item]
            rows.append(
                {"item": item, "pass": False, "confidence": confidence, "reasoning": reasoning}
            )
        else:
            rows.append({"item": item, "pass": True, "confidence": 0.9, "reasoning": "ok"})
    text = json.dumps(rows)
    return f"```json\n{text}\n```" if fence else text


# =========================================================================
# Prompt Construction
# =========================================================================


class TestPromptConstruction:
    def test_system_prompt_includes_json_format(self, evaluator):
        prompt = evaluator._build_system_prompt()
        assert "JSON" in prompt
        assert '"pass"' in prompt
        assert '"confidence"' in prompt
        assert '"reasoning"' in prompt

    def test_user_prompt_includes_entry_content(self, evaluator, sample_entry):
        prompt = evaluator._build_user_prompt(sample_entry, SAMPLE_RUBRIC_ITEMS, "")
        assert "Test Entry" in prompt
        assert "test entry body" in prompt
        assert "note" in prompt

    def test_user_prompt_includes_rubric_items(self, evaluator, sample_entry):
        prompt = evaluator._build_user_prompt(sample_entry, SAMPLE_RUBRIC_ITEMS, "")
        for item in SAMPLE_RUBRIC_ITEMS:
            assert item in prompt

    def test_user_prompt_includes_guidelines(self, evaluator, sample_entry):
        guidelines = "Entries should be thorough and well-sourced."
        prompt = evaluator._build_user_prompt(sample_entry, SAMPLE_RUBRIC_ITEMS, guidelines)
        assert guidelines in prompt

    def test_user_prompt_includes_metadata(self, evaluator, sample_entry):
        prompt = evaluator._build_user_prompt(sample_entry, SAMPLE_RUBRIC_ITEMS, "")
        assert "tags" in prompt

    def test_user_prompt_truncates_long_body(self, evaluator, sample_entry):
        sample_entry["body"] = "x" * 10000
        prompt = evaluator._build_user_prompt(sample_entry, SAMPLE_RUBRIC_ITEMS, "")
        assert "[... truncated]" in prompt


# =========================================================================
# Response Parsing
# =========================================================================


class TestResponseParsing:
    def test_valid_json_array(self, evaluator, sample_entry):
        response = json.dumps(
            [
                {
                    "item": "Entry body explains the why, not just the what",
                    "pass": False,
                    "confidence": 0.8,
                    "reasoning": "Body only states facts",
                },
                {
                    "item": "Confidence level matches evidence strength",
                    "pass": True,
                    "confidence": 0.9,
                    "reasoning": "Matches well",
                },
            ]
        )
        issues = evaluator._parse_response(response, sample_entry, SAMPLE_RUBRIC_ITEMS)
        assert len(issues) == 1
        assert issues[0]["rule"] == "llm_rubric_violation"
        assert issues[0]["rubric_item"] == "Entry body explains the why, not just the what"
        assert issues[0]["confidence"] == 0.8
        assert issues[0]["message"] == "Body only states facts"

    @complete_reply
    def test_json_in_code_fence(self, evaluator, sample_entry):
        response = _reply({SAMPLE_RUBRIC_ITEMS[0]: (0.7, "No why")}, fence=True)
        issues = evaluator._parse_response(response, sample_entry, SAMPLE_RUBRIC_ITEMS)
        assert len(issues) == 1
        assert issues[0]["confidence"] == 0.7

    def test_malformed_json_did_not_run(self, evaluator, sample_entry):
        with pytest.raises(EvaluationFailedError, match="not JSON"):
            evaluator._parse_response("not json at all", sample_entry, SAMPLE_RUBRIC_ITEMS)

    @pytest.mark.parametrize("reply", ["", "  "])
    def test_empty_response_did_not_run(self, evaluator, sample_entry, reply):
        with pytest.raises(EvaluationFailedError, match="empty"):
            evaluator._parse_response(reply, sample_entry, SAMPLE_RUBRIC_ITEMS)

    def test_a_reply_that_is_not_a_list_did_not_run(self, evaluator, sample_entry):
        with pytest.raises(EvaluationFailedError, match="not a JSON list"):
            evaluator._parse_response('{"pass": true}', sample_entry, SAMPLE_RUBRIC_ITEMS)

    @pytest.mark.parametrize(
        "reply",
        [
            "[]",
            json.dumps([{"item": SAMPLE_RUBRIC_ITEMS[0], "pass": True}]),
            json.dumps([{"item": i, "pass": "yes"} for i in SAMPLE_RUBRIC_ITEMS]),
            json.dumps([{"item": i} for i in SAMPLE_RUBRIC_ITEMS]),
        ],
        ids=["no-items", "one-of-two", "pass-not-boolean", "no-pass-key"],
    )
    def test_an_item_left_unanswered_did_not_run(self, evaluator, sample_entry, reply):
        """An item the model did not judge is not a pass (#795)."""
        with pytest.raises(EvaluationFailedError, match="did not answer"):
            evaluator._parse_response(reply, sample_entry, SAMPLE_RUBRIC_ITEMS)

    @pytest.mark.parametrize(
        "item", [[SAMPLE_RUBRIC_ITEMS[0]], {"text": SAMPLE_RUBRIC_ITEMS[0]}], ids=["list", "object"]
    )
    def test_an_item_that_is_not_a_string_is_unanswered(self, evaluator, sample_entry, item):
        """Not a TypeError from the set lookup: an answer that names no item (#950)."""
        rows = [{"item": item, "pass": True}, {"item": SAMPLE_RUBRIC_ITEMS[1], "pass": True}]
        with pytest.raises(EvaluationFailedError, match="did not answer 1 of 2"):
            evaluator._parse_response(json.dumps(rows), sample_entry, SAMPLE_RUBRIC_ITEMS)

    def test_a_non_string_item_beside_full_answers_is_ignored(self, evaluator, sample_entry):
        rows = [*json.loads(_reply()), {"item": ["stray"], "pass": False}]
        assert evaluator._parse_response(json.dumps(rows), sample_entry, SAMPLE_RUBRIC_ITEMS) == []

    @pytest.mark.parametrize("confidence", ["high", None, True, [0.9]])
    def test_a_failed_item_needs_a_numeric_confidence(self, evaluator, sample_entry, confidence):
        rows = json.loads(_reply({SAMPLE_RUBRIC_ITEMS[0]: (0.5, "no why")}))
        rows[0]["confidence"] = confidence
        with pytest.raises(EvaluationFailedError, match="not a number"):
            evaluator._parse_response(json.dumps(rows), sample_entry, SAMPLE_RUBRIC_ITEMS)

    @complete_reply
    def test_all_pass_no_issues(self, evaluator, sample_entry):
        issues = evaluator._parse_response(_reply(), sample_entry, SAMPLE_RUBRIC_ITEMS)
        assert issues == []

    @complete_reply
    def test_unknown_item_filtered_out(self, evaluator, sample_entry):
        """Items not in rubric_items list are filtered out."""
        rows = json.loads(_reply())
        rows.append(
            {"item": "Some unknown rubric item", "pass": False, "confidence": 0.8, "reasoning": "x"}
        )
        issues = evaluator._parse_response(json.dumps(rows), sample_entry, SAMPLE_RUBRIC_ITEMS)
        assert issues == []


# =========================================================================
# Evaluation Flow
# =========================================================================


class TestEvaluation:
    def test_stub_provider_did_not_run(self, sample_entry):
        """Stub provider (not configured) is a check that did not run, not a pass."""
        llm = MagicMock()
        llm.status.return_value = {"configured": False, "provider": "stub", "model": ""}
        ev = LLMRubricEvaluator(llm)
        with pytest.raises(EvaluationFailedError, match="no model"):
            asyncio.run(ev.evaluate(sample_entry, SAMPLE_RUBRIC_ITEMS))
        llm.complete.assert_not_called()

    def test_is_available_reflects_status(self, mock_llm):
        ev = LLMRubricEvaluator(mock_llm)
        assert ev.is_available() is True

        mock_llm.status.return_value = {"configured": False, "provider": "stub", "model": ""}
        assert ev.is_available() is False

    def test_failed_items_produce_issues(self, mock_llm, sample_entry):
        mock_llm.complete = AsyncMock(
            return_value=_reply({SAMPLE_RUBRIC_ITEMS[0]: (0.72, "lacks rationale")})
        )
        ev = LLMRubricEvaluator(mock_llm)
        issues = asyncio.run(ev.evaluate(sample_entry, SAMPLE_RUBRIC_ITEMS))
        assert len(issues) == 1
        assert issues[0]["rule"] == "llm_rubric_violation"
        # A warning: a model finding makes the record `warn`, never `pass`,
        # and counts at `ci`'s default severity (#795).
        assert issues[0]["severity"] == "warning"
        assert issues[0]["confidence"] == 0.72
        assert issues[0]["message"] == "lacks rationale"

    @complete_reply
    def test_evaluator_field_shows_provider_model(self, mock_llm, sample_entry):
        mock_llm.complete = AsyncMock(return_value=_reply({SAMPLE_RUBRIC_ITEMS[0]: (0.8, "bad")}))
        ev = LLMRubricEvaluator(mock_llm)
        issues = asyncio.run(ev.evaluate(sample_entry, SAMPLE_RUBRIC_ITEMS))
        assert issues[0]["evaluator"] == "anthropic/claude-sonnet-4-20250514"

    def test_llm_error_did_not_run(self, mock_llm, sample_entry):
        """An LLM call that raised is a check that did not run, with its reason."""
        mock_llm.complete = AsyncMock(side_effect=RuntimeError("API error"))
        ev = LLMRubricEvaluator(mock_llm)
        with pytest.raises(EvaluationFailedError, match="API error"):
            asyncio.run(ev.evaluate(sample_entry, SAMPLE_RUBRIC_ITEMS))


# =========================================================================
# Batching
# =========================================================================


class TestBatching:
    @complete_reply
    def test_multiple_items_single_call(self, mock_llm, sample_entry):
        """Multiple rubric items result in a single LLM call."""
        mock_llm.complete = AsyncMock(return_value=_reply())
        ev = LLMRubricEvaluator(mock_llm)
        asyncio.run(ev.evaluate(sample_entry, SAMPLE_RUBRIC_ITEMS))
        assert mock_llm.complete.call_count == 1

    def test_mixed_pass_fail(self, mock_llm, sample_entry):
        """Only failures returned as issues."""
        mock_llm.complete = AsyncMock(
            return_value=json.dumps(
                [
                    {
                        "item": "Entry body explains the why, not just the what",
                        "pass": False,
                        "confidence": 0.8,
                        "reasoning": "bad",
                    },
                    {
                        "item": "Confidence level matches evidence strength",
                        "pass": True,
                        "confidence": 0.9,
                        "reasoning": "ok",
                    },
                ]
            )
        )
        ev = LLMRubricEvaluator(mock_llm)
        issues = asyncio.run(ev.evaluate(sample_entry, SAMPLE_RUBRIC_ITEMS))
        assert len(issues) == 1
        assert issues[0]["rubric_item"] == "Entry body explains the why, not just the what"

    def test_empty_rubric_items_no_call(self, mock_llm, sample_entry):
        """No rubric items -> no LLM call."""
        ev = LLMRubricEvaluator(mock_llm)
        issues = asyncio.run(ev.evaluate(sample_entry, []))
        assert issues == []
        mock_llm.complete.assert_not_called()

    @complete_reply
    def test_body_truncated_in_prompt(self, mock_llm, sample_entry):
        """Long body is truncated in the prompt."""
        sample_entry["body"] = "x" * 10000
        mock_llm.complete = AsyncMock(return_value=_reply())
        ev = LLMRubricEvaluator(mock_llm)
        asyncio.run(ev.evaluate(sample_entry, SAMPLE_RUBRIC_ITEMS))
        call_args = mock_llm.complete.call_args
        prompt = call_args[0][0]
        assert "[... truncated]" in prompt
