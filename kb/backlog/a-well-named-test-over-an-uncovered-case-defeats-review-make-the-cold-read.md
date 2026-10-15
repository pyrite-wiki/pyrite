---
id: a-well-named-test-over-an-uncovered-case-defeats-review-make-the-cold-read
title: "A well-named test over an uncovered case defeats review: make the cold read mandatory when a worker's Unsure names a design decision"
type: backlog_item
tags:
- process
importance: 5
status: proposed
priority: medium
rank: 0
---

Filed by the 05:50Z session as evidence for the retro. This is the *mechanism* behind the near-miss on PR #69, separate from the outcome.

**What happened.** PR #69's worker added a test class `TestAlwaysWrittenDefaultsStillWork`, with a docstring explaining that it guards commit 7783335's data-loss fix against the narrowing the branch introduced. Its three cases covered an entry built in memory, another built in memory, and a file that *already had* the key. The case that actually loses data — file lacks the key, user explicitly sets it to the default — was absent.

The conductor read that class during review and was reassured **by its name**. The cold read found the regression: `--importance 5` silently writes nothing while reporting `{"updated": true}`, reachable from CLI, REST `PUT`/`PATCH`, MCP `entry_update` and the software-kb reorder.

**The generalisable point.** A well-named test over an uncovered case is worse than no test at all. It converts an unexamined risk into an *apparently examined* one, and it specifically defeats a reviewer who is checking whether a risk was **considered** rather than whether it was **covered**. A missing test class prompts "where is the test for this?"; a present one closes the question.

**Which trigger actually earned its keep.** review.md lists five cold-read triggers. The one that fired usefully here was not "touches `pyrite/storage/`" (true, but true of many benign changes) — it was **"the worker's Unsure is non-empty"**. The worker had explicitly written that its `_absent_default_keys` design was "the decision most likely to have gone another way". That is the cheapest and most specific of the five, and the easiest to wave through when a branch otherwise looks clean and its suite is green.

**Suggested change to review.md:**

1. When a worker's Unsure names a **design decision** (not a style choice or a naming preference), the cold read is **mandatory**, not discretionary.
2. The reviewer's brief should quote that Unsure **verbatim as its first question**, rather than paraphrasing it into the conductor's own framing — the worker's own words are the highest-signal input available and the conductor's paraphrase is where the anchoring creeps in.

Data point for the retro's "cold-read findings that changed a PR / cold reads dispatched" row: **1 of 1 changed the disposition**, from *flip to ready* to *do not merge*.

## Triage 2026-09-20 (from GitHub)

**State: partly addressed — leave open for one specific, one-line change.** (`process` issue; verified, not rewritten.)

Checked against the maintainer's review procedure (outside this repo; it was `review.md:115-118` at the time):

> "Dispatch `pyrite-reviewer` when the diff touches `pyrite/server/`, `pyrite/storage/`, `pyrite/schema/`, the auth code, or a public shape, when it deletes or weakens a test, **when the worker's 'Unsure' is non-empty**, or whenever your own reading felt too familiar to be critical."

So "Unsure non-empty" is a listed trigger — but it is listed *among* five discretionary ones ("Dispatch … when"), which is exactly the framing this issue says is too easy to wave through on an otherwise clean branch. **Change 1 of this issue (make it mandatory when the Unsure names a design decision, not merely discretionary) is not in the text.**

**Change 2 is also not in the text.** The reviewer prompt in the same procedure (`review.md:124-131`) gives the reviewer "the diff and nothing else — no ticket, no report, no conversation". That is right for the cold read's independence and is deliberately not what this issue asked for; this issue asked for the worker's Unsure to be quoted **verbatim as the reviewer's first question** rather than paraphrased by the conductor. Those two are in tension and the tension is real: quoting the Unsure gives the reviewer context the cold read is designed to withhold.

**That tension is the only open question on this issue**, and it is a one-paragraph decision, not a theme: *when a worker's Unsure names a design decision, does the cold reader receive that sentence verbatim (losing some independence, gaining the highest-signal pointer), or does the conductor keep the cold read blind and instead refuse to discharge the Unsure itself?* #133 answers the neighbouring case in the second direction ("a cold-read finding that says 'I could not confirm X' is not dischargeable as a trade-off"), which suggests the second option here too.

Leaving open with that question stated. The data point this issue records — **1 of 1 cold reads changed the disposition** — is the retro's, and stays.


_Migrated from GitHub issue #115 on 2026-09-20 (maintainer: process findings live in the KB)._
