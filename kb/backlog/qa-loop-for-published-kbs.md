---
id: qa-loop-for-published-kbs
type: backlog_item
title: "A QA loop for published KBs: exercise the qa tools on a schedule and track one quality number"
kind: improvement
status: proposed
priority: high
effort: L
tags: [process, quality, qa, published-kb, dogfooding]
---

## Problem

Pyrite has ten `qa` commands (validate, assess, status, checkers, gaps, fix, stale, compact,
check-urls, coverage) and an assessment entry type. On a published, 6,107-entry timeline KB they
had never been run as a loop. Exercising them on 2026-10-01 showed:

- **Assessment coverage is 0%.** `qa status` reports 0 of 6,107 entries assessed.
- **The signal is buried.** 10,638 issues, of which about 5,050 are "no outbound link" rubric
  violations and 4,092 are "orphan entry". Those rules fit a wiki, not dated events. The 1,490
  broken internal links are real and sit underneath them.
- **The only rubric is the generic four** (title, body, tags, outbound links). Nothing checks what
  a published factual KB needs: sources resolve, claims are bound to their source, status matches
  verification.
- **`qa check-urls` crashes on start** (#574). Its test replaces the code path that breaks.
- **The subcommands disagree on how the KB is named** (positional vs `-k`).
- **The strongest check lives outside Pyrite**: a claim-and-span gate in a private skill script.

A hand sample of 240 source URLs from entries marked `confirmed` found 72% resolving, 8% dead
(404), 16% blocking scripted fetches, 4% other. One entry marked `confirmed` had five dead source
links and was found by accident.

Root cause: quality is checked when someone happens to look. There is no rubric for what a
published KB promises, no scheduled run, and no number that moves.

## Proposal

1. **A rubric per published KB.** KB-level rubric items for a factual, sourced KB: sources
   resolve; claims are bound to a source span; the date is the event date; status matches
   verification. A KB can switch off system rubric items that do not fit its entry types.
2. **Make the tools run.** Fix #574; one convention for naming the KB across `qa` subcommands;
   let a KB or plugin register an external checker (the claim gate) so `qa assess` covers it.
3. **A bounded scheduled pass.** Validate, check URLs (cached), and run the claim checker on a
   fixed-size batch of the oldest unverified entries. Output: assessment entries and a one-screen
   scorecard.
4. **A capped fix queue.** Failures become tasks (`--create-tasks`), a set number per run, each a
   re-source-or-retire decision.
5. **One number.** Share of published entries that are source-verified, trended across runs.

## Acceptance

- `qa status <kb>` on a published KB reports nonzero assessment coverage after one scheduled pass.
- Rubric violations reported for the KB are ones its own rubric defines; switching off a system
  item removes its violations from the count.
- `qa check-urls` runs end to end against a real KB, covered by a test that does not replace the
  CLI context.
- A run with `--create-tasks` creates at most the configured number of tasks.
- The scorecard shows the source-verified share and its change since the previous run.

## Notes

Raised by the maintainer on 2026-10-01: "pyrite has qa tools for things like the timeline, but
they need to be exercised and organized so that we can do continuous improvement on the quality
of the core published KBs." Split into themes at grooming; items 1 and 2 are prerequisites for 3.
