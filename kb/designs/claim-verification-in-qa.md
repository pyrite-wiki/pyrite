---
id: claim-verification-in-qa
type: design_doc
title: "Claim verification in QA: claims live in the file, Pyrite checks what code can settle, verifiers are callers"
status: review
author: pyrite-architect (Claude Opus 5.5), for markr
date: '2026-10-08'
reviewers: [markr]
tags: [qa, quality, design, claims, fact-check, verification, cost]
links:
- target: adr-0047
  relation: related
  kb: pyrite
- target: claim-verification-in-qa-claims-protocol-deterministic-checks-verifier-contract
  relation: related
  kb: pyrite
- target: qa-loop-for-published-kbs
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
---

# Claim verification in QA

> **Status: proposed** (the `design_doc` enum has no `proposed`; `review` is
> its nearest value). The public shapes it needs are drafted as ADR-0047
> (proposed). The plan is the backlog item
> [[claim-verification-in-qa-claims-protocol-deterministic-checks-verifier-contract]].

The maintainer asked for this on 2026-10-08: "we should store what we learned in
a design for a cheaper fact check process. We can also just make this a part of
pyrite's Quality system. Adversarial checks are definitely worth supporting."

## The two models

**The user's model.** "Every factual claim in this entry is tied to the words
in a source that support it. When someone checks a claim, the result is written
next to the claim. When a check finds an error, the fix and the reason are on
the page. An entry says `confirmed` only when its claims bear that out. Someone
other than the finder must try to knock a finding down before it changes the
page. I bring the agents and pay for them."

**The implementation model.**

- A claim is content, in the entry's own file. It goes in a `claims:` list, in
  the shape the daily-capture pipeline already writes.
- A correction is content too, in a `corrections:` list on the same entry.
- Pyrite does the deterministic part:
  - checks each claim against its own span, and against the cached source
    text;
  - ranks and samples entries by risk;
  - hands each entry to a verifier as one bounded packet;
  - validates the verdicts that come back;
  - applies them as ADR-0042 operations, one entry at a time, all or nothing
    per entry, idempotently.
- An entry's verification level (`confirmed`, `reported`, `disputed`) is
  **derived** from its claim outcomes. It is never written as a side effect.
  When the file's `status` claims more than its claims support, QA reports
  it. An explicit repair command may change it.
- Pyrite runs no model in this contract. The finder, the verifier and the
  model tiering belong to the caller. To Pyrite, a built-in verifier would be
  just another caller.

**Where they differ today.**

- Pyrite has no idea of a claim inside an entry. The claim-and-span gate lives
  in a private skill script (`check_sourcing.py --claims --gate`).
- `qa fix` rewrites whole entries from the index's reading.
- Factual verification is marked done in the KB, but no code does it.

## Evidence: a real run, 2026-10-08

A fact-check workflow ran over 100 capturecascade.org timeline events. They
were the highest-risk events, ranked by how many figures they contain and how
important they are.

**Pipeline.**

1. 10 Sonnet checkers took 10 events each. They bound every claim to a span of
   a fetched source, in the daily-capture claims format (`claims: [{id, type,
   value, qualifier, span, url, read, outcome, checked}]`), and ran
   `check_sourcing.py --claims --gate`.
2. One Opus verifier per finding was prompted to refute that finding.
3. One writer applied the confirmed corrections.

| Measure | Value |
|---|---|
| Findings | 469 (4.7 per event) |
| Confirmed / refuted by the adversarial verifier | 343 / 126 (27% refuted) |
| Corrections landed | 334 in 99 events |
| Kinds of correction | about 55% figure or date, about 45% wording or overstated scope |
| Status after the run | every event `status: reported` until all its claims verify |
| Correction record | a `corrections:` entry each: date, was, now, why, found_by |
| Cost | about 30M subagent tokens, 480 agents, about 1.9 hours |

**What drove the cost, and what failed.**

- **One Opus agent per finding.** 469 of the 480 agents were verifiers. Each
  one re-read its event and re-fetched its sources.
- **The checkers and the verifiers fetched the same URLs again.** No stage
  reused the spans an earlier stage had fetched.
- **No deterministic pass.** "Does the span contain the value?" can be
  settled by code, but a model judged it.
- **The single writer overflowed its context** and was restarted mid-run. The
  writes turned out idempotent, but nothing made them so. Nothing in the files
  could tell an applied correction from one that had not been applied.

**The adversarial step is worth keeping.** It removed about one false finding
in four. Without it, 126 correct statements would have been "corrected" into
errors on a public site.

**The cheaper pipeline the run points to** (target: about 1/5 of the cost):

- one verifier per **entry**, judging all of its findings;
- tiered models: a cheap model first, the strong model only for overstatement,
  attribution or disputed verdicts;
- a span cache, so the verifier reuses the checker's text and fetches again
  only to refute;
- a deterministic pass first, so models see only what code cannot settle;
- one writer per batch, with idempotent writes;
- selection ranked by risk, with low-risk tiers sampled and a full pass only
  where the sample's error rate exceeds a threshold.

A rough count, which no run has measured yet: 99 per-entry verifiers in place
of 469, with cached spans in their packets and the code-settled claims removed,
is about a fifth of the verifier agents. The verifier agents were nearly the
whole bill. The 1/5 target is plausible. The first sampled run must measure it.

The published-KB loop item [[qa-loop-for-published-kbs]] adds evidence from
2026-10-01. A hand sample of 240 source URLs from `confirmed` entries found 16%
that block scripted fetches. A cache that only Pyrite's own HTTP client can
fill would miss those pages. The agent that read the page must be able to put
its text in.

## Contracts that apply

- [[design]] **P1, P5.** Claim outcomes and corrections are statements about
  the entry. They live in its file. The span cache can be deleted without
  losing any record.
- **P3 and [[adr-0042]] decisions 1, 3, 10, 12.** A verdict is applied as
  operations: set `claims[i].outcome`, append to `corrections`, replace the
  body. A set to the value already in the file writes nothing. `qa fix` is on
  decision 12's list of writers that go through this path.
- **P4 and [[adr-0045]] decisions 2 and 3.** The verification level is derived
  through the index and labelled derived. It is never written into the file
  and never accepted back.
- **P6 and [[adr-0045]] decision 5.** A type opts in by satisfying a protocol,
  checked structurally against its schema. Code does not keep a list of
  types.
- **P8.** Each claim rule lives in one function. `qa validate`, `qa
  verify-claims` and the verdict validator all call it.
- **[[adr-0030]] section 4.** A rule that must hold is enforced in the write
  path, never in the harness. Separation of duties and required evidence are
  checked when verdicts are applied, whichever agent produced them.
- **[[adr-0007]] section 3.** AI is opt-in, bring your own key. Nothing here
  needs a key.
- **[[adr-0046]].** New commands return an `Outcome`. Applying verdicts to
  some entries but not all is `partial` (exit 3).
- **[[adr-0034]].** A verifier packet is a bounded read. A truncated body is
  never writable.
- **ADR-0039 decision 7.** The span cache is derived machinery outside the KB,
  not state and not content.
- Standard `pyrite-is-a-guest-in-state-it-does-not-own`. The claims format is
  already on disk in KBs Pyrite did not shape. Pyrite adopts its keys and does
  not respell them.

## What the code does today (dev at 85160605), surprises first

1. **The KB says factual verification is done, and no code does it.**
   - `kb/backlog/done/qa-phase-4-tier-3-factual-verification.md` and
     `source-content-verification-via-llm.md` are `status: done`.
   - The QA epic's progress note says "Phases 1-4: COMPLETE".
   - There is no `qa verify` or `verify-sources` command. The ten `qa`
     commands are at `pyrite/cli/qa_commands.py:24-814`.
   - Nothing in `pyrite/` reads a claim.
2. **`qa assess --tier 2` from the CLI can never call a model, and records a
   pass anyway.**
   - Every `qa_commands.py` site constructs `QAService(config, db)` with no
     `llm_service` (:43, :134, :223, :437, :555, :642, :694).
   - `_evaluate_llm_rubric` then logs and returns `[]` (`qa_service.py:1271-1273`).
   - The assessment is written with `tier: 2` and `qa_status: pass`
     (:277-306).
   - `LLMRubricEvaluator.evaluate` also returns `[]` when the call fails
     (`llm_rubric_evaluator.py:56-60`).
   - So an unevaluated judgment reads as a passed one. The verifier contract
     below makes "not judged" a value of its own for this reason. This was
     read, not run.
3. **`qa fix` does not follow ADR-0042.**
   - It writes through `kb_svc.update_entry(**updates)` (`qa_fix_service.py:162`),
     the re-serialising update path.
   - For a metadata fix it reads the entry's current metadata **from the index
     row**, merges into it and writes the whole map back (:150-160). That is
     the index acting as tiebreaker (P5).
   - #788: a failed write is reported as `manual`, with exit 0.
4. **`qa check-urls` checks five hard-coded types.**
   - `timeline_event`, `solidarity_event`, `scene`, `investigation_event`,
     `note` (`url_checker.py:76-82`).
   - `sources` is on the base `Entry` (`models/base.py:363`), so every other
     type's sources are skipped, including core `event`.
   - It reads sources from the index (`db.get_entry`, :96).
   - An exception for a type means `continue` (:89-90; #611 and PR #746).
5. **Four verification vocabularies already exist, and none of them is
   derived from evidence.**
   - Core `EventStatus` (`schema/enums.py:16-27`) defaults to `confirmed` when
     a file has no `status:` (`models/core_types.py:148, :183`). An unchecked
     event therefore reads as confirmed.
   - `VerificationStatus` (`enums.py:6-13`).
   - journalism-investigation's `ClaimEntry.claim_status` and its events'
     `verification_status`.
   - `Source.verified` / `confidence` / `key_facts_confirmed`
     (`schema/provenance.py:11-34`).
6. **A rubric checker returns one issue for a whole entry.** The signature is
   `(entry, schema, params) -> dict | None` (`rubric_checkers.py:22-25`), and
   checkers read the index row's `metadata` JSON. A claim checker needs one
   result per claim, read from the file. Rubric checkers cannot be the
   extension point for claims. `_check_file_values` (`qa_service.py:896-937`)
   already loads each file once for checks that need the file's own values.
   Claim checks belong there.
7. **The value-level write path exists but nothing uses it.**
   `pyrite/storage/file_operations.py` (`apply(text, ops, span)`: `Set`,
   `Append`, `Remove`, `Unset`, `ReplaceBody`, `AddSubkey`) landed in B6 P1
   (#732). Its docstring says it "is not wired to any write path yet (B6 phase
   P3 does that)". No module imports it. Any slice that writes waits for that
   wiring (#730).
8. **`qa_assessment` writes one new file per entry per run**, named
   `qa-<id>-<ms>`, through `create_entry` (`qa_service.py:286-306`). At 4.7
   findings an event, that shape is too fine-grained to record verdicts.

## The design, question by question

### Q1. How do claims become first-class?

**Recommendation: a protocol, `verifiable`, whose data contract is the claims
format already on disk.** ADR-0047 decision 1.

- **Data.**
  - `claims` is a list of mappings. Each item has `id`, `type`, `value`, and
    usually `qualifier`, `span`, `url`, `read`, `outcome`, `checked`.
  - `corrections` (optional) is a list of mappings: `date`, `was`, `now`,
    `why`, `found_by`, plus `claim` and `finding` when known.
  - The protocol adopts the daily-capture keys exactly as written. Before the
    vocabularies are fixed, a spike reads the real files (open question 1).
- **Satisfaction** is structural (ADR-0045 decision 5). A type whose
  `kb.yaml` schema declares `claims` satisfies the protocol. So does a type
  that names `verifiable` in its protocols. A plugin type can satisfy it the
  same way. Every QA path that ranges over claims asks the registry which
  types satisfy it. None keeps a list of types, as `url_checker.py:76-82`
  does.
- **Derived (ADR-0045 decision 3).** `verification` is one of `confirmed`,
  `reported` or `disputed` (Q5). Claim counts by outcome come with it, as
  does the oldest `checked` date.
- **Explicit operations.** `qa verify-claims` is the check. Applying verdicts
  is the write (Q3).
- **Refusals.** A write that leaves a claim's `outcome` outside the
  vocabulary is refused (ADR-0042 decision 8, scoped to what the write
  touches).

| Option | For | Against |
|---|---|---|
| **A. Protocol `verifiable` (recommended)** | P6's own mechanism; satisfaction is checked against the schema, not a type list; carries the derived value and the operations | A new protocol is part of the alpha plugin contract (ADR-0040), so it needs an ADR |
| B. New field type `claims` in `FieldSchema.VALID_TYPES` (`field_schema.py:75-87`) | Validation in one place | A field type cannot carry derived values or operations; the status rule would end up elsewhere |
| C. Convention only: a `list` field named `claims` | No code | Nothing fails when the shape drifts; the checker keys on a field name; same gap as today |

`list` with object `items` would help `sources` and `links` too. That is
worth a general ticket, but this design does not need it. The protocol
validates its own item shape.

**How it relates to what exists.**

- `qa check-urls` answers "does the URL answer?". `verify-claims` answers
  "does the source say this?". The two share the URL set, and the span cache
  (Q4) replaces check-urls' ad-hoc JSON cache (`--cache`, `url_checker.py:193-210`).
- `qa assess` records one assessment per entry. Claim outcomes are recorded on
  the claims themselves, not in assessments.
- `qa validate` gains the claim rules through `_check_file_values`. Its MCP
  and REST twins (`kb_qa_validate`, `GET /qa/validate`) get them with no
  code of their own.
- journalism-investigation's `claim` entry is a different, compatible shape:
  one claim per entry, linked to evidence entries. Its `claim_status` maps
  onto the outcome vocabulary in ADR-0047's table. That plugin can satisfy
  `verifiable` later. This design does not change it.

### Q2. Adversarial verification as a QA primitive

**Recommendation: a verifier is a caller that answers a packet with verdicts.
Pyrite defines the packet, the verdict and the rules a verdict must meet.
ADR-0047 decision 4.**

**In: a packet, one per entry**, bounded under ADR-0034:

- the entry (`kb`, `id`, path, `content_hash` as base);
- its claims, each with the deterministic results (Q4);
- the findings against it, from any finder: `id`, `claim`, `kind` (`figure`,
  `date`, `wording`, `scope`, `attribution`), the proposed `was`/`now`, `why`,
  `found_by`;
- the cached source text each finding cites, by cache key, `fetched_at` and
  hash;
- a `needs` hint per finding, derived from its `kind` and the deterministic
  result (`code-settled`, `cheap`, `strong`). The caller chooses the models.

**Out: verdicts**, one per finding:

- `verdict`: `holds`, `refuted`, `amended` (the finding stands with a
  different correction), or `undecided`;
- the final `now` text for `holds` and `amended`;
- `evidence`: `url`, `span`, `fetched_at`;
- `why`;
- `by`: an opaque verifier id. A model name may be part of it; Pyrite does not
  interpret it.

Claim outcomes come back with the verdicts (`claim`, `outcome`, `checked`).

**The rules Pyrite enforces when it applies verdicts** (the write path, so
they hold whichever harness ran the verifier):

1. **Separation of duties.** If a verdict's `by` equals the finding's
   `found_by`, the verdict is refused. This is the one structural property
   that makes the check adversarial. Pyrite cannot see the prompt, but it can
   see who signed.
2. **Evidence.** A `holds` or `amended` verdict must carry a span. Where the
   source is in the cache, the span must be found in the cached text, and the
   new value must be found in the span (the same deterministic functions as
   Q4). A `refuted` verdict must carry either a span or a `why`.
3. **Undecided is not a pass.** An `undecided` or missing verdict leaves the
   claim's outcome as it was. It is reported. The entry's derived level cannot
   rise because of it. This is surprise 2 above, applied.
4. **Base.** Verdicts carry the packet's base hash. Under ADR-0042 decision 10
   a change to another key merges. A change to `claims` or to the body text a
   correction touches is a conflict for that entry. The entry is reported
   conflicted and the other entries proceed.

**Audit trail.**

- A confirmed correction is its own record: a `corrections` item with
  `found_by` and `verified_by`.
- A verified claim keeps `outcome` and `checked`.
- A refuted finding changes nothing in the file. It is returned in the apply
  report, and stays in the verdict file the caller keeps.
- Whether refuted findings must also be recorded in the KB is decision D3.

### Q3. Where findings and corrections live, and how they are applied

**Recommendation:**

- **Findings are work in flight.** They live in the caller's packet and
  verdict files, or in an ephemeral KB (ADR-0029) if a team wants a shared
  queue. They are not entries in the published KB.
- **Outcomes and corrections are content** on the entry.
- **One explicit command applies verdicts as operations.** ADR-0047
  decision 5.

The command is `qa apply-verdicts <file>`. For each entry, against the file as
it is at write time:

1. For each verdict that `holds` or is `amended`:
   - `Set claims[i].value`, `Set claims[i].outcome` = `corrected`, `Set
     claims[i].checked`;
   - append a `corrections` item, **only if no item with that `finding`
     already exists** (an operation with a precondition, ADR-0042 §10a A3);
   - change the body text: compute `ReplaceBody` from the file's current
     body, replacing `was` with `now`. This is refused when `was` occurs zero
     times or more than once. When `was` is absent and `now` is present, the
     result is `unchanged`.
2. For each claim outcome: `Set claims[i].outcome`, `Set claims[i].checked`.
3. All of one entry's operations go in one write. If any is refused, nothing
   is written for that entry and the reason is reported.
4. The result is `Outcome.from_counts(applied, failed)`.

Why this is idempotent by construction:

- a set to the file's own value writes nothing (ADR-0042 decision 3);
- a correction already appended is skipped by its key;
- a body already corrected is `unchanged`.

A restarted writer re-runs the whole file. Every entry it already finished
reports `unchanged`. The new check `correction_not_applied` (Q4) finds an
entry that is half done.

**Finding:** ADR-0042 has no operation for "replace one span of the body". A
correction to prose is a whole `ReplaceBody`, computed from the current file
and guarded by a unique match. If a correction must survive a concurrent edit
elsewhere in the body, that needs a body-span operation. Question 4.

`qa fix` keeps its own rules (dates, missing defaults, links, tag case).
ADR-0042 decision 12 already moves those onto operations as part of B6.
`apply-verdicts` is a separate command, not a `--fix-rule`. A verdict file
carries outside evidence, and `qa fix` repairs only what validation itself
found. `qa fix` does gain one rule, `status_exceeds_claims` (Q5), because
validation finds that one.

| Option | For | Against |
|---|---|---|
| **Findings in caller files, outcomes and corrections in the entry (recommended)** | The page carries its record (Hugo can render `corrections`); no KB noise from in-flight work; P5 | Refuted findings are not in the KB unless D3 says so |
| `qa_assessment` entry per finding | Queryable | 469 files for one run; `qa-<id>-<ms>` names; the assessment is about the entry but not in it |
| Task per finding (`--create-tasks`) | Fits the board | A finding is not work a person claims; the queue is the caller's |

### Q4. Cost control: what is Pyrite's, what is the caller's

| Lever | Owner | Why |
|---|---|---|
| Deterministic pass | **Pyrite**, slice 1 | It is code over the file, a rule in one place (P8), and every caller benefits |
| Span cache | **Pyrite** stores and serves it, slice 2; **Pyrite or the caller** fills it | Derived machinery. Pyrite's fetch fails on about 16% of pages, so the agent that read a page must be able to put the text in (`qa spans put`) |
| Risk ranking and sampling | **Pyrite**, slice 2 | Ranking is a derived value from the files (figure density, `importance`, never checked, age of `checked`). A seeded sample is reproducible |
| Error rate per stratum | **Pyrite** computes it from recorded outcomes | Derived |
| Threshold that triggers a full pass | **Caller** | A policy about money and risk; Pyrite reports the rate and its sample size |
| Batching (one packet per entry, N entries a batch) | **Pyrite** shapes the packets; **caller** chooses N | Packet size is bounded by ADR-0034 |
| Model tiering | **Caller** | Pyrite emits `needs`; it never picks a model |
| Token and money budgets | **Caller** | Pyrite runs no model, so it cannot see tokens |
| Idempotent writer | **Pyrite**, slice 3 | Q3: by construction, not by luck |

**The deterministic pass.** Each rule is one function in
`pyrite/services/claim_checks.py`, and each result is per claim:

- `claim_missing_span`, `claim_missing_url`: an empty `span` or `url`.
- `claim_url_not_in_sources`: the claim's URL is not among the entry's
  `sources` (info).
- `claim_value_not_in_span`: the value is absent from the span after
  normalisation.
  - Numbers: `$1.2 billion` is the same as `1,200,000,000`, and `12%` as
    `12 percent`.
  - Dates: `March 3, 2025`, `2025-03-03` and `3 March 2025` are the same.
  - Text: whitespace, case and Unicode are folded.
  - The results are `exact`, `normalised` or `absent`.
- `claim_qualifier_missing`: the claim has a `qualifier` ("about", "at least",
  "alleged") and the span does not.
- `claim_value_not_in_entry`: the claim's value is not in the entry's title or
  body, which means the claims list has drifted from the prose.
- `claim_unchecked` / `claim_check_stale`: `outcome` is absent, or `checked`
  is older than the KB's `max_age`.
- `correction_not_applied`: a correction's `now` is not in the entry, or its
  `was` still is. This would have caught the restarted writer.
- `status_exceeds_claims`: the file's `status` asserts more than the derived
  level (Q5).
- Slice 2 adds `span_not_in_source`: the span is not found in the cached
  source text. The result is `found`, `absent` or `unfetched`.

**What code may settle.** Code may *fail* a claim (`absent`) or *narrow* what
a model must judge. Recommendation: code never writes `outcome: verified` by
itself. An exact value in a span that is also found in the fetched source is
the strongest code-only evidence. Even then the figure can belong to something
else, which is part of the 45% of findings about wording and scope. Claims
settled by code go to the low-risk stratum and are sampled. If the sample's
error rate stays low, they never cost a model call. Decision D4 has the
counter-case.

### Q5. Status semantics

**Recommendation:** `verification` is derived. A rule compares it with the
file's `status`. Pyrite never writes `status` as a side effect.

Derived `verification`, per entry:

- `disputed` if any claim's outcome is `disputed`;
- else `confirmed` if the entry has at least one claim, and every claim's
  outcome is `verified` or `corrected` within `max_age`;
- else `reported`. This covers unchecked, `unsupported` and stale claims,
  and an entry with no claims at all.

The file's own `status` is the person's statement. A rule judges it against
the derived level, using a strength table in ADR-0047. Every value of core
`EventStatus` is classified:

- **Assert verification:** `confirmed`, `proven`.
- **Make no verification claim:** `reported`, `alleged`, `rumored`,
  `developing`, `approximate`, `draft`.
- **Disputed:** `disputed`.

`status_exceeds_claims` is a warning when the file asserts verification and
the derived level is lower. It is an error on a KB whose rubric binds the
rule. `qa fix --fix-rule status_exceeds_claims` sets `status` to the derived
level as an explicit repair (ADR-0042 decision 7). That is the downgrade the
2026-10-08 run did by hand. Raising a status is never automatic. A `reported`
entry whose claims all verify gets an `info` suggesting `confirmed`, and a
person decides.

**Finding:** core `EventStatus` defaults a file with no `status:` to
`confirmed`. Under ADR-0042 decision 9 that default comes back only under a
derived key. Even so, the derived verification level must never take its
input from it. The rule reads the file's own `status`, and absent means
"asserts nothing".

**The claim outcome vocabulary**, recommended, pending the spike over the real
files:

| Outcome | Meaning |
|---|---|
| absent | unchecked |
| `verified` | a source span supports the claim as written |
| `corrected` | it was wrong; the entry now says what the source supports, and a `corrections` item records it |
| `unsupported` | no source found that supports it |
| `disputed` | sources conflict |

The finding verdicts (`holds`, `refuted`, `amended`, `undecided`) are a
separate vocabulary. In the run, "refuted" described findings, not claims.
A refuted finding leaves its claim `verified`.

### Q6. What Pyrite must not do

**Recommendation: in the verification contract, Pyrite runs no model and
fetches nothing on a verifier's behalf. Agents are callers. Pyrite is the
record, the deterministic rules and the write path.** ADR-0047 decision 4.

The line:

- **Pyrite does:**
  - parse and validate claims;
  - run the deterministic rules;
  - rank, sample and shape packets;
  - store and serve cached source text;
  - validate verdicts (separation, evidence, base);
  - apply them as operations;
  - derive the verification level and the per-stratum error rates;
  - report.
- **Pyrite does not:**
  - choose, prompt or call a model to find or judge claims;
  - hold a token budget;
  - decide the error threshold;
  - schedule agents;
  - write a verdict it did not receive.

Why:

- ADR-0030 section 4 puts enforcement in the write path because harnesses
  change. Here the harness is whatever runs the verifiers.
- Cost and model choice belong to whoever pays (ADR-0007 section 3).
- Surprise 2 shows what a built-in model path does when it is not configured:
  it records a pass.
- The thesis is "agents write, humans verify". A model judging a model is the
  caller's method, and its methodology belongs in the intent layer and skills,
  not in core.

A built-in verifier later, through `LLMService`, is not excluded. If one is
built, it is a caller of `apply-verdicts` like any other, held to the same
rules, and never wired into `qa assess`. `qa assess --tier 2` keeps working as
it does. Making its unavailable path honest is a separate bug (surprise 2).

Fetching: Pyrite already makes HTTP requests for `check-urls`. Slice 2's
`qa spans fetch` is a convenience that fills the cache. It is not a step a
verifier depends on.

## The sets this design ranges over (every X names its list)

- **QA commands (10):** `validate`, `assess`, `status`, `checkers`, `gaps`,
  `fix`, `stale`, `compact`, `check-urls`, `coverage`.
  - Changed: `validate` (claim rules through `_check_file_values`), `fix` (one
    new rule), `status` and `coverage` (claim coverage, slice 4), and
    `check-urls` (shares the span cache, slice 2).
  - Unchanged: `assess` (outcomes are not assessments), `checkers` (claim
    rules are not rubric checkers, surprise 6), `gaps`, `stale`, `compact`.
  - New: `verify-claims`, `apply-verdicts`, `spans` (put, fetch, show).
- **QA MCP tools (3):** `kb_qa_validate` gains the claim rules through the
  service. `kb_qa_status` and `kb_qa_assess` are unchanged. No new MCP tool
  until slice 3 proves the contract on the CLI; the QA MCP tools are
  experimental (`alpha-supported-surface`).
- **QA REST routes (4):** `GET /qa/status`, `/qa/validate/{id}`,
  `/qa/validate`, `/qa/coverage`. The two validate routes gain the rules
  through the service. No new route.
- **Core rubric checkers (12, `rubric_checkers.py:356-369`):** none changes.
- **Types:** every type that satisfies `verifiable` structurally, found
  through the registry. Not the five types `url_checker.py:76-82` hard-codes.
- **Status vocabularies (6):**
  - core `EventStatus` (9 values, classified in Q5);
  - `VerificationStatus`;
  - journalism-investigation's `claim_status` and `verification_status`;
  - `Source.verified` / `confidence`;
  - `qa_status`.

  ADR-0047 maps the first and third. The rest are untouched and listed there.
- **Writers of an entry file used here:** only `apply-verdicts` and `qa fix
  --fix-rule status_exceeds_claims`, both through the ADR-0042 path.

## Open questions

1. **The real format.** What do `read` and `outcome` hold in the
   daily-capture files, and how many files carry `claims:` today? This design
   could not find them on the maintainer's machine. The spike in slice 1 reads
   them before the vocabularies are fixed. If the files already use other
   outcome words, the files win.
2. **Per-claim history or last result only?** A claim keeps its last
   `outcome` and `checked`, and git keeps the history. A user who wants
   "checked three times by whom" in the file would want a `checks:` list. The
   recommendation is no, and to keep the file small. Overturn it with a real
   reader who needs the history.
3. **Normalisation edges.** Ranges ("$1-2 billion"), rounding ("nearly $1.2
   billion" against 1.18), currencies, and relative dates ("last Tuesday").
   The slice 1 worker should list what the 469 findings contained, if that
   data can be obtained.
4. **A body-span operation.** Is a whole-body replace with a unique-match
   guard enough, or must a correction merge with a concurrent edit elsewhere in
   the body? This is an ADR-0042 question, not this design's.
5. **Is a failed gate a refusal?** `verify-claims --gate` fails CI. Under
   ADR-0046 a failure that is not an effect is a raised `PyriteError`. The
   recommendation is `ClaimsGateFailed`, code `CLAIMS_GATE_FAILED`, exit 1.

## Checked versus assumed

- **Checked by reading** (dev at 85160605):
  - the ten QA commands, `qa_service.py`, `qa_fix_service.py`,
    `url_checker.py`, `rubric_checkers.py`, `llm_rubric_evaluator.py`,
    `file_operations.py`, `models/protocols.py`, `schema/enums.py`,
    `field_schema.py`;
  - ADR-0042, ADR-0045, ADR-0046, ADR-0030 section 4;
  - the QA and AI topic maps;
  - the done QA backlog items, and the open issues #730, #788, #675 and #611;
  - the open PRs that touch `qa_commands.py` / `qa_service.py` (#746, #784)
    and the CLI outcome (#793).
- **Not run:** no command, test, server or browser. Surprise 2 was read, not
  reproduced.
- **Assumed:** the run's numbers and the claims format, from the maintainer's
  brief. The format's real values were not inspected. The 1/5 cost estimate is
  arithmetic, not measured.
- **This design is wrong if** the daily-capture claims are not kept in entry
  frontmatter (for example in a sidecar or a separate report). If so, the
  protocol's data contract needs a different home, and P5 needs a different
  argument.
