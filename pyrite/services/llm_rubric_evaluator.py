"""LLM-assisted rubric evaluation for judgment-only rubric items.

Handles rubric items that require semantic judgment (e.g., "Entry body explains
the why, not just the what") by batching them into a single LLM call per entry.

An empty list means the model answered every item and passed them all, or
there were no items to ask about. Anything short of an answer -- no model, a
call that raised, a reply that is empty, not JSON, not a list, or silent on an
item -- raises ``EvaluationFailedError`` with the reason (#795): a check that
did not run is never reported as one that found nothing.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from ..exceptions import EvaluationFailedError
from ..utils.metadata import parse_metadata

if TYPE_CHECKING:
    from .llm_service import LLMService

logger = logging.getLogger(__name__)

# Maximum body length to include in prompt (chars)
MAX_BODY_LENGTH = 4000


class LLMRubricEvaluator:
    """Evaluates judgment-only rubric items using an LLM."""

    def __init__(self, llm_service: LLMService) -> None:
        self._llm = llm_service

    def is_available(self) -> bool:
        """Check if LLM is configured and available."""
        return self._llm.status()["configured"]

    async def evaluate(
        self,
        entry: dict[str, Any],
        rubric_items: list[str],
        guidelines: str = "",
    ) -> list[dict[str, Any]]:
        """Evaluate rubric items against an entry using LLM.

        Returns list of issue dicts for failed items only. Raises
        ``EvaluationFailedError`` when the model did not answer (module
        docstring); ``[]`` with no items is the one empty that is not an answer
        and needs none.
        """
        if not rubric_items:
            return []

        entry_id = entry.get("id", "")
        if not self.is_available():
            raise EvaluationFailedError(entry_id, "no model is configured")

        system_prompt = self._build_system_prompt()
        user_prompt = self._build_user_prompt(entry, rubric_items, guidelines)

        try:
            response = await self._llm.complete(user_prompt, system=system_prompt, max_tokens=1024)
        except Exception as exc:
            logger.warning("LLM rubric evaluation failed", exc_info=True)
            raise EvaluationFailedError(
                entry_id, f"the model call failed: {type(exc).__name__}: {exc}"
            ) from exc

        return self._parse_response(response, entry, rubric_items)

    def _build_system_prompt(self) -> str:
        return (
            "You are a knowledge base quality evaluator. You assess entries against "
            "rubric criteria that require human-like judgment.\n\n"
            "For each rubric item, determine if the entry passes or fails.\n\n"
            "Respond with ONLY a JSON array. Each element must have:\n"
            '- "item": the rubric item text (exactly as given)\n'
            '- "pass": true or false\n'
            '- "confidence": float 0.0-1.0 (how confident you are)\n'
            '- "reasoning": brief explanation (1-2 sentences)\n\n'
            "Example:\n"
            '[\n  {"item": "Entry explains the why", "pass": false, '
            '"confidence": 0.8, "reasoning": "Body only lists facts without rationale"}\n]'
        )

    def _build_user_prompt(
        self,
        entry: dict[str, Any],
        rubric_items: list[str],
        guidelines: str,
    ) -> str:
        title = entry.get("title", "") or ""
        body = entry.get("body", "") or ""
        entry_type = entry.get("entry_type", "") or ""

        # Parse metadata
        meta_dict = parse_metadata(entry.get("metadata"))

        # Truncate body
        if len(body) > MAX_BODY_LENGTH:
            body = body[:MAX_BODY_LENGTH] + "\n[... truncated]"

        parts = [
            f"## Entry\n- Type: {entry_type}\n- Title: {title}\n\n### Body\n{body}",
        ]

        if meta_dict:
            meta_str = json.dumps(meta_dict, indent=2, default=str)
            parts.append(f"\n### Metadata\n{meta_str}")

        if guidelines:
            parts.append(f"\n## Type Guidelines\n{guidelines}")

        items_text = "\n".join(f"- {item}" for item in rubric_items)
        parts.append(f"\n## Rubric Items to Evaluate\n{items_text}")

        return "\n".join(parts)

    def _parse_response(
        self,
        response: str,
        entry: dict[str, Any],
        rubric_items: list[str],
    ) -> list[dict[str, Any]]:
        """Parse LLM response into issue dicts for failed items.

        Raises ``EvaluationFailedError`` unless the reply answers every item
        with a boolean ``pass``.
        """
        entry_id = entry.get("id", "")
        if not response or not response.strip():
            raise EvaluationFailedError(entry_id, "the model's reply was empty")

        # Extract JSON from response (handle markdown code fences)
        text = response.strip()
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if match:
            text = match.group(1).strip()

        try:
            results = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            logger.warning("LLM rubric response was not valid JSON: %s", text[:200])
            raise EvaluationFailedError(entry_id, "the model's reply was not JSON") from None

        if not isinstance(results, list):
            raise EvaluationFailedError(entry_id, "the model's reply was not a JSON list")

        # Build set of valid rubric items for validation
        valid_items = set(rubric_items)

        # Only an item the model judged pass or fail counts as answered; an
        # item it left out, or answered without a boolean, was not checked.
        # The item must be a string before the set lookup: a list or object
        # there is unhashable and would end the whole run in a TypeError.
        answered = {
            r["item"]
            for r in results
            if isinstance(r, dict)
            and isinstance(r.get("item"), str)
            and r["item"] in valid_items
            and isinstance(r.get("pass"), bool)
        }
        unanswered = [item for item in rubric_items if item not in answered]
        if unanswered:
            raise EvaluationFailedError(
                entry_id,
                f"the model did not answer {len(unanswered)} of {len(rubric_items)} rubric "
                f"item(s): {'; '.join(unanswered)}",
            )

        issues: list[dict[str, Any]] = []
        status = self._llm.status()
        evaluator = f"{status['provider']}/{status['model']}"

        for result in results:
            if not isinstance(result, dict):
                continue

            item_text = result.get("item")
            passed = result.get("pass")
            confidence = result.get("confidence", 0.5)
            reasoning = result.get("reasoning", "")

            # Only include items from our rubric list, judged fail
            if (
                not isinstance(item_text, str)
                or item_text not in valid_items
                or passed is not False
            ):
                continue
            if isinstance(confidence, bool) or not isinstance(confidence, int | float):
                raise EvaluationFailedError(
                    entry_id,
                    f"the model's confidence for '{item_text}' is not a number: {confidence!r}",
                )

            issues.append(
                {
                    "entry_id": entry.get("id", ""),
                    "kb_name": entry.get("kb_name", ""),
                    "rule": "llm_rubric_violation",
                    # A warning, so an entry the model failed is recorded
                    # `warn`, never `pass`, and fails `ci` at its default
                    # severity (#795). It was `info`, which no status counted.
                    "severity": "warning",
                    "field": "body",
                    "message": str(reasoning) if reasoning else f"Failed rubric: {item_text}",
                    "rubric_item": item_text,
                    "confidence": float(confidence),
                    "evaluator": evaluator,
                }
            )

        return issues
