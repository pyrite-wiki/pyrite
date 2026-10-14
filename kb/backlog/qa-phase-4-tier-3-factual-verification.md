---
id: qa-phase-4-tier-3-factual-verification
title: 'QA Phase 4: Tier 3 Factual Verification'
type: backlog_item
tags:
- feature
- quality
- ai
kind: feature
status: proposed
priority: medium
effort: L
---

## Parent

Subtask of [[qa-agent-workflows]] — Phase 4.

## Problem

Even with structural and consistency checks, factual accuracy remains unverified. Claims may not match cited sources, dates may be wrong, quotes misattributed, and the KB may contain internal contradictions.

## Solution

A research agent with web search capability that verifies specific claims against cited sources, checks historical accuracy, and detects cross-KB contradictions. Produces confidence-scored factual assessments with source chains.

## Acceptance Criteria

- Research agent with web search for claim verification
- Cross-KB contradiction detection
- Source chain verification (do cited sources actually support the claims?)
- Confidence-scored factual assessments with source provenance
- Checks: claim-source alignment, date accuracy, quote attribution, causal defensibility, statistic verifiability
- CLI command: `pyrite qa verify [--kb <name>] [--entry <id>]`
- Plugin architecture: domain-agnostic core with pluggable evaluation rubrics (legal, scientific, investigative)

## Dependencies

- Phase 3 (qa-phase-3-tier-2-llm-assisted-consistency-checks) — builds on Tier 2 infrastructure
- Web search capability

## Files Likely Affected

- Modified: `pyrite/services/qa_service.py` (Tier 3 verification logic)
- New: verification agent module with web search integration
- Modified: `pyrite/server/mcp_server.py` (qa verify tool)
- Modified: `pyrite/cli/__init__.py` (qa verify command)

## Status note (2026-10-08)

Marked `done` in error; reset to `proposed`. The claim-verification groom found that none of this was built: there is no `pyrite qa verify` command (`pyrite/cli/qa_commands.py` registers no `verify`), no Tier 3 logic in `pyrite/services/qa_service.py`, no web-search or verification agent module, and no `qa verify` MCP tool. `grep -rn 'qa verify' pyrite extensions` finds nothing. The shape of this work is now decided in ADR-0047 (claims are content; Pyrite checks them deterministically; verifiers are callers), the design `kb/designs/claim-verification-in-qa.md`, and the backlog item [[claim-verification-in-qa-claims-protocol-deterministic-checks-verifier-contract]] (PR #794). Under ADR-0047 Pyrite checks claims deterministically and verifiers are callers, so the research agent described above is not Pyrite's to ship. That item replaces this one when it is accepted.
