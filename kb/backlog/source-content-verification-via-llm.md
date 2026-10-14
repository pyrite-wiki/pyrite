---
id: source-content-verification-via-llm
title: Source content verification via LLM (Phase 2)
type: backlog_item
tags:
- qa
- sources
- fact-checking
- llm
- cascade
links:
- target: epic-migrate-kleptocracy-timeline-to-pyrite-managed-kb
  relation: subtask_of
  kb: pyrite
- target: source-url-validation-and-content-verification-for-qa
  relation: depends_on
  kb: pyrite
kind: feature
status: proposed
priority: medium
effort: L
---

## Problem

Even when source URLs are live, there's no verification that the source content actually supports the claims in the KB entry. AI research agents may accurately cite real URLs but misrepresent or hallucinate the content of those sources.

## Context

This is Phase 2 of the source verification pipeline. Phase 1 (URL liveness checking) must be complete first. This phase requires LLM integration and has associated API costs.

## Scope

- Fetch source URL content and extract key claims from the entry body
- Use LLM to compare: "Does this source support these claims?"
- Flag entries where sources don't appear to support key claims
- Store verification results as QA assessment entries linked to the original entry
- Support batch processing with cost controls

## Acceptance Criteria

- `pyrite qa verify-sources --kb=timeline --sample=50` verifies a random sample
- Each entry gets a verification score (0-1) based on source-claim alignment
- Low-scoring entries are flagged for human review
- Cost-controlled: respects `--max-cost` budget parameter
- Results stored as QA assessment entries with evidence links
- Incremental: skips entries already verified unless `--force`

## Open Questions

- Which LLM provider to use? Should respect Pyrite's existing LLMService config (BYOK)
- What content extraction strategy for web pages? (readability, trafilatura, etc.)
- How to handle paywalled sources? (skip with warning? use cached content?)

## Status note (2026-10-08)

Marked `done` in error; reset to `proposed`. Not built: there is no `qa verify-sources` command (`pyrite/cli/qa_commands.py` has only `check-urls`, which checks HTTP liveness, not content), no `--max-cost` or `--sample` verification, no verification score and no stored verification assessments. The Phase 1 item it depends on, [[source-url-validation-and-content-verification-for-qa]], delivered liveness checking only. Direction now lives in ADR-0047 (claims are content; Pyrite checks them deterministically; verifiers are callers), the design `kb/designs/claim-verification-in-qa.md`, and the backlog item [[claim-verification-in-qa-claims-protocol-deterministic-checks-verifier-contract]] (PR #794). Under ADR-0047 a model-based content check is a caller of Pyrite, not a Pyrite command.
