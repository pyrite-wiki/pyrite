---
id: adr-0047
title: "Claims are content: Pyrite checks them deterministically and applies verdicts; verifiers are callers"
type: adr
importance: 5
adr_number: 47
status: proposed
date: '2026-10-08'
tags: [architecture, qa, claims, verification, protocols, write-path]
links:
- target: claim-verification-in-qa
  relation: related
  kb: pyrite
- target: adr-0042
  relation: related
  kb: pyrite
- target: adr-0045
  relation: related
  kb: pyrite
- target: adr-0046
  relation: related
  kb: pyrite
- target: adr-0030
  relation: related
  kb: pyrite
- target: adr-0021
  relation: related
  kb: pyrite
---

# ADR-0047: Claims are content; Pyrite checks them deterministically and applies verdicts; verifiers are callers

> **Proposed** (2026-10-08). The maintainer accepts or rejects it. The design
> and the evidence are in [[claim-verification-in-qa]].

## Context

The capturecascade.org fact-check run on 2026-10-08 bound every claim in 100
events to a span of a fetched source, in a `claims:` list in each event's
frontmatter. It then had each finding attacked by a separate verifier. 27% of
469 findings were refuted, and 334 corrections landed, each recorded in a
`corrections:` list. The run cost about 30M tokens, mostly one strong-model
agent per finding. A writer overflowed its context mid-run; its writes turned
out idempotent, but nothing made them so.

Pyrite today:

- has no idea of a claim inside an entry;
- has four verification vocabularies, none derived from evidence;
- has a `qa fix` that rewrites entries from the index;
- has a "factual verification" phase marked done with no code behind it.

The claims format is already on disk in KBs Pyrite did not shape.

## Decision

1. **`verifiable` is a protocol (ADR-0045).**
   - Its data contract is `claims`, a list of mappings, each with `id`,
     `type` and `value`, and optionally `qualifier`, `span`, `url`, `read`,
     `outcome` and `checked`.
   - Optionally it also has `corrections`, a list of mappings with `date`,
     `was`, `now`, `why`, `found_by`, and optionally `verified_by`, `claim`
     and `finding`.
   - The keys are the daily-capture format's, unchanged.
   - Satisfaction is structural, against the type's schema. Code that ranges
     over claims asks the registry which types satisfy `verifiable` and keeps
     no list of types.
2. **Two vocabularies, kept apart.**
   - A claim's `outcome` is absent (unchecked), `verified`, `corrected`,
     `unsupported` or `disputed`.
   - A finding's verdict is `holds`, `refuted`, `amended` or `undecided`.
   - Verdicts are not stored on claims.
   - If the files on disk use other outcome words, the files win and this
     table is amended before acceptance.
3. **Verification level is derived.**
   - It is `disputed` if any claim is disputed.
   - It is `confirmed` if there is at least one claim and every claim is
     `verified` or `corrected` within the KB's `max_age`.
   - Otherwise it is `reported`.
   - It is computed through the index, exposed under a derived key, and never
     written or accepted back.
   - A QA rule, `status_exceeds_claims`, reports a file whose own `status`
     asserts more than the derived level. The strength table:

     | Strength | Values |
     |---|---|
     | Asserts verification | `confirmed`, `proven` |
     | Asserts nothing | `reported`, `alleged`, `rumored`, `developing`, `approximate`, `draft`, absent |
     | Disputed | `disputed` |

   - Only `qa fix --fix-rule status_exceeds_claims` changes `status`, and only
     downward.
   - A type default (core `EventStatus` reads an absent status as
     `confirmed`) is never an input.
4. **Pyrite runs no model in this contract.**
   - It emits one bounded packet per entry: the entry and its base hash, the
     claims with the deterministic results, the findings, the cached source
     text, and a `needs` hint per finding.
   - It accepts verdicts and applies them.
   - Finders, verifiers, model choice, budgets and thresholds are the
     caller's.
   - A built-in verifier, if one is ever built, is a caller under the same
     rules.
5. **Verdicts are applied in the write path, under four rules.**
   - (a) A verdict signed by its finding's `found_by` is refused.
   - (b) `holds` and `amended` carry a span. Where the source is cached, the
     span must be in the source and the new value must be in the span.
   - (c) `undecided` or a missing verdict changes nothing and cannot raise the
     level.
   - (d) Each entry's operations (ADR-0042 decision 1: set the claim's keys,
     append a correction unless one with that `finding` exists, and replace
     the body computed from the current file with `was` matched exactly once)
     are one write against the verdict's base. They are all or nothing for
     that entry.

   The command returns `Outcome.from_counts` (ADR-0046). Re-applying a
   verdict file changes nothing that was already applied.
6. **Cached source text is derived machinery.**
   - It lives outside the KB, keyed by URL, with `fetched_at` and a SHA-256.
   - It can be deleted without losing any record. The `span` in the file is
     the evidence of record.
   - It may be filled by Pyrite's fetch or by the caller (`qa spans put`),
     because a scripted fetch fails on pages an agent can read.
7. **Each claim rule is one function** in one module. `qa validate` (through
   its file-value pass), `qa verify-claims` and the verdict validator call it.
   A test enumerates the rules and fails when a caller has its own copy.

## Questions for the maintainer

1. **D1:** Is the `verifiable` protocol the right home, rather than a new
   field type or a convention? Recommended: protocol.
2. **D3:** Are refuted findings recorded only in the caller's verdict file and
   the apply report, or also in the KB? Recommended: not in the KB. Git and
   the verdict file are the trail, and the page carries what changed.
3. **D4:** May code alone set `outcome: verified` when the value is exactly
   in the span and the span is exactly in the fetched source? Recommended: no.
   Such claims go to a sampled low-risk stratum.
   - Counter-case: figures and dates were about 55% of corrections. Letting
     code confirm exact matches could cut verifier work further.
   - The cost of that: a figure that belongs to something else on the same
     page passes unseen.

## Alternatives considered

- **A `claims` field type** (`FieldSchema.VALID_TYPES`). It gives one place to
  validate the shape. But it cannot carry the derived level or the
  operations, so the status rule would live somewhere else.
- **A convention only.** It needs no code. But nothing fails when the shape
  drifts, and checkers key on a field name.
- **A `qa_assessment` entry or a task per finding.** These are queryable, but
  one run would write 469 files, and the record would not be in the entry it
  describes.
- **Pyrite runs the verifier** through `LLMService`. It is convenient, but it
  makes Pyrite the holder of model choice and budget. Its unconfigured path
  already records a pass for a judgment that was never made
  (`qa_commands.py:134`, `qa_service.py:1271-1273`).
- **Code confirms exact matches.** It is cheaper, but it cannot see a figure
  that belongs to something else on the page (D4).

## Consequences

- `qa check-urls` stops hard-coding five types and shares the cache.
- `qa fix` gains one rule, and moves to the operation path with B6
  (ADR-0042 decision 12).
- Slices that write wait for B6 P3, which wires `file_operations.apply` into
  the update path (#730).
- journalism-investigation's `claim` entry type is untouched. Its
  `claim_status` maps to the outcomes as follows: `unverified` to absent,
  `partially_verified` to absent, `corroborated` to `verified`, `disputed` to
  `disputed`, `retracted` to `unsupported`. The plugin may adopt the protocol
  later.
- The QA epic's "Phases 1-4 complete" and the two done factual-verification
  items are corrected to say what was built.
