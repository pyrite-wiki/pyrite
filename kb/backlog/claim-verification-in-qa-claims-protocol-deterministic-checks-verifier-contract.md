---
id: claim-verification-in-qa-claims-protocol-deterministic-checks-verifier-contract
title: 'Claim verification in QA: a claims protocol, deterministic checks, and a verifier contract'
type: backlog_item
tags:
- quality
- qa
- claims
- fact-check
links:
- target: claim-verification-in-qa
  relation: tracks
  kb: pyrite
- target: adr-0047
  relation: tracks
  kb: pyrite
- target: qa-loop-for-published-kbs
  relation: related
  kb: pyrite
importance: 5
kind: feature
status: proposed
priority: medium
effort: XL
---

The maintainer, 2026-10-08: "store what we learned in a design for a cheaper
fact check process … make this a part of pyrite's Quality system. Adversarial
checks are definitely worth supporting."

- **Design:** [[claim-verification-in-qa]], which holds the evidence from the
  2026-10-08 run (469 findings, 27% refuted, about 30M tokens).
- **Shapes:** ADR-0047 (proposed).
- **Related:** [[qa-loop-for-published-kbs]]. It asks for "the claim gate" as
  a checker; slices 1 and 2 are that checker.

## Groom 2026-10-08

### Contracts that apply

- [[design]] P5: outcomes and corrections live in the entry's file. P4: the
  verification level is derived. P8: each claim rule is one function.
- ADR-0047 (proposed), decisions 1 to 7. Slices 1 and 2 need D1. Slice 3
  needs D3. Slice 4's sampling needs D4.
- ADR-0042, decisions 1, 3, 10 and 12, and §10a A3: verdicts are applied as
  operations with a precondition.
- ADR-0045, decisions 3 and 5: the protocol is satisfied structurally, and
  derived values sit under a derived key.
- ADR-0046: new commands return an `Outcome`, and `apply-verdicts` is
  `from_counts`.
- ADR-0034: a packet is a bounded read.
- ADR-0030 §4: separation of duties and evidence are checked in the write
  path.
- Standard `pyrite-is-a-guest-in-state-it-does-not-own`: the daily-capture
  claims format is already on disk, and Pyrite adopts its keys.

### What the code does today

See the design, "What the code does today". Its surprises, briefly:

- The factual-verification phase is marked done, but no code exists.
- The CLI's `qa assess --tier 2` records `pass` without calling a model
  (`qa_commands.py:134`, `qa_service.py:1271-1273`).
- `qa fix` merges metadata from the index row and re-serialises the entry
  (`qa_fix_service.py:150-162`).
- `check-urls` hard-codes five types (`url_checker.py:76-82`).
- `file_operations.apply` is not wired to any write path.
- `PROTOCOL_FIELDS` doubles as the list of keys promoted to database columns
  (`models/protocols.py:186-201`). `verifiable` must register its fields
  without becoming columns.

Nothing was reproduced by a run. This is a design ticket. Slice 1's spike is
the first check against real inputs.

### Invariant and surfaces

**Invariant.** Every claim rule is decided by one function, on the file's own
values. Every entry whose type satisfies `verifiable` is judged by it. An
entry's level is derived and never written as a side effect. A verdict
changes a file only through ADR-0042 operations, after the rules in ADR-0047
decision 5.

- **CLI.**
  - New: `qa verify-claims` (S1), `qa spans` (S2), `qa apply-verdicts` (S3).
  - Changed: `qa validate` (S1), `qa fix` (S4), `qa status` and `qa
    coverage` (S4), `qa check-urls` (S2).
- **MCP.** `kb_qa_validate` gains the rules through the service (S1). There
  is no new tool. The QA MCP tools are experimental (`alpha-supported-surface`).
- **REST.** `GET /qa/validate` and `GET /qa/validate/{id}` gain the rules
  through the service (S1). There is no new route.
- **Plugins.** A plugin type satisfies `verifiable` through its schema. The
  journalism-investigation `claim` type is not changed. Its status mapping is
  in ADR-0047's Consequences.
- **Files on disk.**
  - Read: `claims` and `corrections` in entries.
  - Written only by `apply-verdicts` and `qa fix --fix-rule
    status_exceeds_claims`.
  - The span cache lives outside the KB (S2).
- **Shipped schemas and templates.** None declares `claims` today. The S1
  doc example adds a `kb.yaml` fragment, as documentation and not as a
  shipped preset.

### Options

See the design, Q1 to Q6. Recommended:

- the protocol, over a field type or a convention;
- findings in the caller's files, with outcomes and corrections in the entry;
- Pyrite runs no model;
- code never writes `verified`.

### Open questions

These are the design's open questions 1 to 5, plus where the span cache lives.

- Recommended for the cache: `<git-dir>/pyrite/span-cache/`, beside the locks
  (ADR-0042 §10a A1), so it follows the repository and is not in the KB.
- Counter-case: a non-git KB has no git dir. `check-urls` today takes a
  caller-named path.

### Checked versus assumed

- **Checked:** the design's list.
- **This plan is wrong if** the daily-capture claims are not in entry
  frontmatter, or if `check_sourcing.py --gate` enforces rules that need the
  fetched page and not the span. Slice 1's spike settles both before code.
- **Spike first, for S1:** the claims format and the gate rules have only been
  read in the maintainer's brief.
  - Read the real files that carry `claims:` (count them; list the values of
    `type`, `read` and `outcome`; note how `span` is quoted; record how
    `url` relates to `sources`).
  - Read `check_sourcing.py`'s gate rules.
  - Output: the rule list and the vocabularies, written into ADR-0047 before
    it is accepted.
  - About 1 hour, Sonnet.
- **Spike first, for S2:** run text-extraction candidates (plain HTML to text;
  a readability-style extractor) over the run's URLs. Measure the share where
  the checker's span is found verbatim, and the share that block scripted
  fetches. That decides whether `qa spans fetch` is worth building or `qa
  spans put` alone is enough.

## Slices

### S1: the `verifiable` data contract and a deterministic `qa verify-claims`

```
Acceptance:   - A KB with an `event` type whose kb.yaml declares `claims` runs
                `qa verify-claims <kb>` and gets one result per claim, from the
                file's own values: missing span or url, url not among the
                entry's sources, value not in span after normalisation
                (exact | normalised | absent), qualifier missing, value not in
                the entry's title or body, unchecked or stale,
                correction_not_applied.
              - The same rules appear in `qa validate` for that entry, through
                `_check_file_values`, so `kb_qa_validate` and
                `GET /qa/validate` show them with no code of their own.
              - A type that does not satisfy `verifiable` produces no claim
                results, and no type list exists in code.
              - The command returns an Outcome: `done` when every requested
                entry was read. `--gate` raises ClaimsGateFailed
                (CLAIMS_GATE_FAILED, exit 1) when any claim fails.
              - The test that fails today: `qa verify-claims` does not exist.
                A doc page, `docs/claim-checks.md`, runs as its test over a
                fixture KB written by hand in the daily-capture format.
Regimes:      - An entry with `claims: []`, with no `claims` key, with a claim
                that is not a mapping, or with claims missing `id`: each is
                reported, never a crash.
              - 10,000 entries: the file is read once per entry, in the same
                pass as `_check_file_values`.
              - A file outside the contract (anchors, unparseable) is reported
                and skipped.
Touches:      existing: pyrite/models/protocols.py, pyrite/services/qa_service.py,
                        pyrite/cli/qa_commands.py
              new: pyrite/services/claim_checks.py, tests/test_claim_checks.py,
                   tests/test_qa_verify_claims_cli.py, docs/claim-checks.md,
                   tests/test_doc_claim_checks.py, kb/components/claim-checks.md
              predicted: 9 files, about 1,000 lines, half of them tests
Sequence:     after the S1 spike and decision D1; after #793 (Outcome), #746
              and #784, which change qa_commands.py and qa_service.py
Model:        opus (a public shape; number and date normalisation)
heavy:        yes
Cold read:    yes (a public shape, a protocol in the plugin contract)
Out of scope: - Fetching or caching sources (S2): the rules here need only
                the file.
              - Writing anything: the command is read-only, and
                `_check_file_values` already loads files read-only.
              - `qa assess` and rubric checkers: their one-issue signature
                cannot carry per-claim results (rubric_checkers.py:22-25), and
                no rubric item refers to claims today.
Decision:     D1, protocol versus field type versus convention. Recommended:
              protocol.
```

### S2: the span cache, source checks, and risk-ranked sampling

```
Acceptance:   - `qa spans put <url> --file text.txt` and `qa spans fetch`
                fill a cache outside the KB.
              - `qa verify-claims` adds span_not_in_source
                (found | absent | unfetched).
              - `--rank` orders entries by a derived risk: claims of type
                figure or date, `importance`, never checked, age of `checked`.
              - `--sample N --seed S` is reproducible, and reports the error
                rate per stratum from recorded outcomes, with its sample size.
              - `qa check-urls` reads its URLs from every type, through the
                files, and shares the cache.
              - The test that fails today: `check-urls` skips the `sources`
                of an `event` entry.
Regimes:      - A page that blocks fetches: `unfetched`, never `absent`.
              - A cache entry older than `max_age`: reported stale.
              - Deleting the cache loses no record.
              - Two processes writing the same URL: last write wins, keyed by
                SHA-256.
Touches:      existing: pyrite/services/url_checker.py, pyrite/services/claim_checks.py,
                        pyrite/cli/qa_commands.py
              new: pyrite/services/span_cache.py, tests/test_span_cache.py,
                   tests/test_claim_sampling.py
              predicted: 6 files, about 800 lines
Sequence:     after S1 (claim_checks.py, qa_commands.py); after the S2 spike
Model:        opus
heavy:        yes
Cold read:    yes (new state outside the KB; network)
Out of scope: - Thresholds and model tiering: the caller's (ADR-0047 decision
                4).
              - Removing check-urls' `--cache`: it is kept as an alias until a
                release note retires it.
Decision:     The cache location: the git dir (recommended) or a configured
              path.
```

### S3: the verifier contract, `--packets` out and `apply-verdicts` in

```
Acceptance:   - `qa verify-claims --packets DIR --findings F.json` writes one
                bounded packet per entry, with the base hash, the claims and
                deterministic results, the findings, the cached text, and a
                `needs` hint.
              - `qa apply-verdicts V.json` applies each entry's verdicts as one
                ADR-0042 write.
              - It refuses a verdict signed by the finding's `found_by`, a
                `holds` without a span, and a span absent from cached text.
              - `undecided` changes nothing.
              - It returns `Outcome.from_counts`.
              - Running it twice reports every entry `unchanged` the second
                time.
              - The test that fails today: there is no command. A doc page,
                `docs/verifier-contract.md`, runs a two-entry round trip in
                which one finding is refuted and one holds.
Regimes:      - The file changed since its packet: per-key merge, or a conflict
                for that entry only; the others apply.
              - `was` found 0 or 2 times in the body: refused for that entry.
              - A correction with the same `finding` already present: skipped.
              - A verdict file of 500 entries is applied one entry at a time,
                so a crash leaves whole entries.
Touches:      existing: pyrite/services/claim_checks.py, pyrite/cli/qa_commands.py
              new: pyrite/services/verdict_service.py, docs/verifier-contract.md,
                   tests/test_verdict_service.py, tests/test_doc_verifier_contract.py,
                   docs/schemas/claims-packet.schema.json, docs/schemas/verdicts.schema.json
              predicted: 8 files, about 1,100 lines
Sequence:     after S1 and S2; after B6 P3 wires file_operations.apply into the
              update path (#730)
Model:        opus
heavy:        yes
Cold read:    yes (writes entry files; a public shape)
Out of scope: - An MCP tool: not added until the CLI contract has run on a real
                batch.
              - A built-in verifier: ADR-0047 decision 4.
Decision:     D3, whether refuted findings are recorded in the KB.
              Recommended: no.
```

### S4: the derived verification level, `status_exceeds_claims`, and coverage

```
Acceptance:   - A read of a `verifiable` entry carries `verification` and
                claim counts under the derived key.
              - `qa validate` reports status_exceeds_claims for a
                `confirmed` or `proven` file whose derived level is lower.
              - `qa fix --fix-rule status_exceeds_claims` lowers `status` by
                one ADR-0042 set, and never raises it.
              - `qa status` and `qa coverage` show the share of entries
                `confirmed` by derivation. This is the "one number" in
                qa-loop-for-published-kbs.
Regimes:      - An entry with no claims is `reported`.
              - A file with no `status:` asserts nothing; the EventStatus
                default is never used.
Touches:      existing: pyrite/services/qa_fix_service.py, pyrite/services/qa_analytics_service.py,
                        pyrite/services/claim_checks.py, the read path's derived key
              predicted: 6 files, about 600 lines
Sequence:     after S1; after the derived-key read of ADR-0042 decision 9 and
              ADR-0045 decision 3 exists (no such key today)
Model:        sonnet, once the derived key exists
heavy:        no
Cold read:    yes (it writes status)
Out of scope: Raising a status: a person's decision (ADR-0047 decision 3).
```

### S5: a caller's recipe, documented

The cheaper pipeline as a public doc, `docs/fact-check-with-pyrite.md`:

1. finders produce findings;
2. `verify-claims --rank --sample` chooses the entries;
3. `--packets` hands them out;
4. one verifier per entry, a cheap model first and the strong model only on a
   `strong` hint;
5. `apply-verdicts`, one batch per writer.

It is run as a test with a stub verifier. Sonnet, after S3. Mark's own
workflow stays in tcp-skills.

## Not in this item, found while grooming

- **A bug.** CLI `qa assess --tier 2` records `tier: 2, qa_status: pass` with
  no model call. An LLM failure also reads as no issues
  (`llm_rubric_evaluator.py:56-60`). File it as its own issue.
- **A KB correction.** `qa-phase-4-tier-3-factual-verification`,
  `source-content-verification-via-llm` and the QA epic's "Phases 1-4
  COMPLETE" claim work that does not exist. This is a docs task.
