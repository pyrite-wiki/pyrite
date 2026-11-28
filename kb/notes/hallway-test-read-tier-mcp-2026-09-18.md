---
id: hallway-test-read-tier-mcp-2026-09-18
title: "Hallway test: the read tier over MCP, cold start (2026-09-18)"
type: note
tags: [hallway-test, mcp, usability, read-tier, evidence]
created: "2026-09-18"
lifecycle: archived
---

A cold-start session (Claude Cowork, no prior Pyrite context) used the MCP
read tier against a 54-KB, 27k-entry corpus to explore a research KB, then
wrote up every detour, guess and workaround. Filed as issues #56–#68 the same
day; this is the full report so the grooming architect and the docs lane can
read the reasoning, the verbatim errors, and — as important — §2.12, what
worked and must not be touched. Part 1 of the original (what the corpus
itself contained) is research material and stays out of this repo.

Issue map: 2.1 → #56 · 2.2 → #57 · 2.3 → #58 · 2.4 → #59 · 2.5 → #60 ·
2.6 → #61 · 2.7 → #62 · 2.8 → #63 · 2.9 → #64 · 2.10 → #65 ·
2.11.1 + 2.11.5 + 2.13 → #66 · 2.11.6 → #67 · 2.11.2 → #68.

# Hallway test of the read tier (MCP, 2026-09-18)

Ordered roughly by severity. Every error message is verbatim.

## 2.1 🔴 BUG: `entry_type`, `tags`, `state`, and `fips` are silently ignored in the **default** search mode

This is the headline finding. `kb_search` defaults to `mode: "hybrid"`. In hybrid and semantic modes, four of the six filters are not applied. The tool does not error, does not warn, and returns a plausible-looking result set.

**The bogus-value test, run as instructed:**

| call | mode | result |
|---|---|---|
| `kb_search(query="accountability bypass", kb="cascade-research", entry_type="zzz-not-a-real-type")` | hybrid (default) | **8 results**, mixed `theme`/`mechanism` |
| same | `keyword` | **0 results** ✅ |
| same | `semantic` | **8 results**, mixed types |
| `kb_search(query="detention facility", state="ZZ")` | hybrid (default) | **5 results** |
| same | `keyword` | **0 results** ✅ |
| `kb_search(query="detention facility", fips="99999")` | hybrid (default) | **5 results** — byte-identical to the `state="ZZ"` set |
| `kb_search(query="detention facility", tags=["zzz-no-such-tag"])` | hybrid (default) | **5 results** |
| same | `keyword` | **0 results** ✅ |
| `kb_search(query="detention facility", status="zzz-bogus-status")` | hybrid (default) | **0 results** ✅ |

**Likely mechanism, from the valid-value case.** `kb_search(query="detention facility", state="MI")` in hybrid returns *three* MI facilities **plus** `basile-detention-center-evangeline-la` (Louisiana) and `warehouse-prison-camps-canonical-chapter-briefs` (book-drafts, no state). That's exactly what you'd expect if the filters are compiled into the FTS/SQL leg's `WHERE` clause and the vector leg is queried unfiltered, then the two result sets are fused. `status` is the odd one out and is presumably applied post-fusion in Python.

**Why this is worse than a normal filter bug.** The harm isn't only the bogus case — it's that a *valid* filter under-constrains. `entry_type="mechanism"` in hybrid returned 4 mechanisms and 4 themes. A researcher who asks for mechanisms and gets themes will either not notice, or will conclude the corpus's typing is a mess. The corpus's typing is fine; the search is lying.

**Suggested fix, in preference order:** (1) apply the filters to the vector leg too; (2) failing that, post-filter the fused set; (3) at minimum, when a filter cannot be honored in the selected mode, return it in a `warnings: []` field on the response. Silently dropping a constraint is the worst of the three.

**Related:** an unknown `entry_type` / `tag` / `state` value should probably be a `VALIDATION` error rather than an empty set even in keyword mode — with 187 distinct `entry_type` values in the corpus (see 2.4), "0 results" and "you typo'd the type" are indistinguishable to the caller.

## 2.2 🔴 BUG: `kb_batch_read` raises a raw `KeyError` when `fields` omits `kb_name`

```
call: kb_batch_read(entries=[{entry_id, kb_name} × 3],
                    body_limit=5000,
                    fields=["id","title","entry_type","tags","body","links"])
```
```json
{"error":"'kb_name'","error_code":"INTERNAL","retryable":true}
```

The message is the bare Python dict key. `retryable: true` is wrong — retrying the identical call fails identically; it is deterministic. Adding `"kb_name"` to `fields` fixes it, which is not discoverable from the error. Either default `kb_name` into the projection or say `fields must include 'kb_name'`.

## 2.3 🟠 `body_limit` is silently overridden by `fields` — asking for *less* returns *more*

```
call: kb_batch_read(entries=[2 entries], body_limit=6000, fields=[...,"body"])
→ 171,189 characters returned (bodies of 37,767 and 129,736 chars)
```

The tool description does say *"When specified, body chunking is skipped"* — so this is documented. But the interaction is backwards from the caller's intent: `fields` is a token-reduction parameter and `body_limit` is a token-reduction parameter, and using both makes the response 28× larger than the explicit cap. The response blew the tool-output ceiling and got spilled to a file:

```
Error: result (171,189 characters across 1 line) exceeds maximum allowed tokens.
```

A 129KB single-line JSON payload is also the worst possible spill format — the harness's own advice was *"this file's lines are too long for Read's offset/limit chunking."* I had to fall back to a Python slice.

**Suggestion:** an explicit `body_limit` should win over the chunking-skip. If that's not desired, error on the combination.

## 2.4 🟠 RESULT QUALITY: `entry_type` is a free-text field with 187 values and heavy drift

`kb_stats` reports 187 distinct `entry_type` values corpus-wide. The drift is systematic, not incidental:

- `research-note` (165) / `research_note` (15) / **`research note`** (2, with a space)
- `prospecting-note` (18) / `prospecting_note` (1)
- `substrate-pack` (4) / `substrate-package` (1) / `scene-substrate-pack` (1)
- `synthesis` (3) / `synthesis_note` (3) / `synthesis-note` (1)
- `work-log` (7) / `work_log` (1) · `news-digest` (11) / `news_digest` (1)
- `actor` (576) / `actor_profile` (3) · `research_task` (2) alongside `task` (2,515)

Consequence: `entry_type="research-note"` misses 17 entries that a human would call research notes. Combined with 2.1 this is compounding — the filter is both unreliable *and* pointed at an unreliable vocabulary. A normalization pass (`_` → `-`, space → `-`) plus a `kb_manage`-level alias map would recover most of it cheaply.

## 2.5 🟠 RESULT QUALITY: `cascade_capture_lanes` returns 92 lanes against a documented 22-value vocabulary

The schema declares a *"Canonical 22-value vocabulary (see `_CANONICAL-CAPTURE-LANES.md`)"*. The actual data has **92**. The schema's own field description already concedes it: *"off-list values are not yet enforced (field type is list, not select/multi-select) — see `issue-pyrite-schema-enums-inert-values-options-key-mismatch.md`."*

So the bug is known. What may not be visible from inside is how far it has gone. Alongside the 22 canonical lanes there are now:

- **Case/format variants of canonical lanes**: `detention-industrial-complex` vs `Detention Industrial Complex`; `legislative-capture` vs `Legislative Capture`; `Surveillance State` / `Surveillance-Industrial Complex` / `Surveillance Infrastructure`; `Intellectual Capture` / `intellectual-capture` / `Intellectual Infrastructure`
- **Free-text sentences used as enum values**: `"Election-capture — SAVE database / noncitizen-voter-roll verification pipeline"` (n=1), `"Trump 2 Pipeline 1 — AFPI/Heritage institutional pre-positioning"` (n=2), `"Data Colonialism / Techno-Feudalism (foreign-force-projection substrate)"` (n=1)
- **Tag-style values that aren't lanes at all**: `ideological`, `academic`, `political`, `Higher education`

**70 of the 92 lanes have count ≤ 5.** A facet with a 76% singleton tail is not a facet. Since `allow_other: true` is intentional, the fix is probably not enforcement but a **canonical/other split in the tool output** — return the 22 with counts, then an `other: [...]` bucket — so a caller can tell at a glance which values are the vocabulary and which are drift.

## 2.6 🟠 RESULT QUALITY: `investigation_search_all(correlate=true)` correlates nothing

`correlate` is documented as *"Group results by entity identity across KBs."* Run on `query="Palantir"`, **all 40 rows returned `kb_count: 1`.** The summary line even says *"Found 40 entities across 6 KBs"* — 40 entities, 40 groups, zero grouping.

The corpus makes this easy to falsify. These four rows are one fact:

- `2026-04-23--144-trump-appointees-palantir-disclosures` (cascade-timeline)
- `palantir-admin-financial-ties` (detention-industrial) — "142 Trump Administration Officials with Palantir Financial Ties"
- `palantir-142-trump-administration-financial-ties-propublica-disclosures` (surveillance-industrial-complex)
- plus the 142/144 discrepancy itself, which is a finding nobody surfaced

And these two are the same entity, same `entry_id`, different KBs — and still weren't grouped:

- `palantir-technologies` in `detention-industrial` (type `contractor`)
- `palantir-technologies` in `surveillance-industrial-complex` (type `firm`)

**Two same-ID entries in different KBs is the easiest possible correlation case and it failed.** Whatever the key is, it isn't `entry_id` and it isn't a normalized title. Suggested: correlate on `entry_id` first, then on normalized title, then optionally on embedding similarity above a threshold — and report the key used so the caller can judge.

## 2.7 🟠 RESULT QUALITY: staging content outranks canon, with no way to exclude it

`daily-capture-reports` is described in its own registry entry as *"STAGING KB for the capture-routines fleet — machine-generated news-capture stories (`status:unprocessed` proposals, **not canon**) awaiting human/conductor promotion."* It has 1,436 entries.

It is indistinguishable from canon in every cross-KB tool:

- `kb_timeline(date_from=2026-08-01, date_to=2026-08-10, min_importance=9, limit=10)` → **7 of 10 results from `daily-capture-reports`**, including three near-duplicate renderings of the same July-detention-record story (`2026-08-02--ice-detention-record-46k-july-deaths-pace`, `2026-08-02--ice-july-2026-record-46k-detentions`, and the cascade-timeline canonical `2026-08-02--ice-july-2026-record-46000-detentions-24-custody-deaths`) with conflicting figures (17 deaths vs 24+ deaths in entries dated one day apart).
- `investigation_search_all("Palantir")` → **12 of 40** from staging.

`kb_timeline` has **no `kb_name` parameter at all**, so there is no way to scope it. The workaround is to pass `kb_names` explicitly to `investigation_search_all` — which requires already knowing the registry. Since the staging entries carry `status: unprocessed`, and `status` is one of the two filters that actually works (2.1), the cheapest fix available today is to expose `status`/`exclude_status` on `kb_timeline`. The better fix is a `canon: true|false` or `lifecycle` flag in the registry that cross-KB tools respect by default.

## 2.8 🟡 `cascade_network` has no `limit` and no pagination

`cascade_network(entry_id="thiel-peter", kb_name="cascade-research")` returned **35 outlinks + ~130 backlinks in one unbounded response** (~18k tokens). `kb_backlinks` has `limit`/`offset`; `cascade_network` has neither. On a hub node it's a token bomb, and there's no way to ask for the top-N by importance. It's also the tool most likely to be called on a hub node, because hub nodes are what you want networks for.

## 2.9 🟡 Dangling wikilinks are returned as `title: null` with no flag

`kb_get` `outlinks` mixes resolved and unresolved targets with nothing distinguishing them but a null:

```json
{"id":"feedback_party_filing_not_authoritative_for_another_docket",
 "kb_name":"cascade-research","title":null,"entry_type":null,"relation":"wikilink"}
```

You have to infer "null title ⇒ target doesn't exist." In `cascade_network(thiel-peter)`, 5 of 35 outlinks were nulls. The corpus itself has a method entry on exactly this — `a-dangling-wikilink-is-a-cross-kb-gap-not-a-research-gap` ("12 of 13 'missing' entities already existed") — which suggests the ambiguity has already cost someone real time. An explicit `resolved: false` (and, per that method entry, a `resolved_in_kb: "<other-kb>"` when the target exists elsewhere) would close it.

## 2.10 🟡 `kb_discover_neighbors` doesn't do what its description says

Description: *"Find entries in **other KBs** that are semantically similar…"* In practice, **6 of 15** results for `accountability-bypass-mechanisms-...` came from `cascade-research` itself. Either the description or the behavior should change; the same-KB results were useful, so I'd change the description.

Also: 3 of the 15 were `draft-conductor:…:fact-checker:1`-style process-task stubs **with empty snippets**, ranked above substantive entries. Process/task entries competing with analysis in a discovery tool is noise — worth a default `entry_type` exclusion or a de-prioritization.

## 2.11 Things I got wrong, guessed at, or had to look up

Recording these because they stop feeling like friction ten minutes later.

1. **`kb_orient` can't actually be the first call.** The CLI help calls it "the recommended first call in any session," but `kb_name` is required and nothing tells you what KBs exist. The real first call is `kb_list`. Worth one line in the description: *"Call `kb_list` first if you don't know the KB name."*
2. **I couldn't tell which tools were read-only.** ~120 `pyrite__*` tools surfaced, with `kb_delete`, `kb_create`, `kb_push`, `kb_commit`, `investigation_bulk_edges` sitting in the same undifferentiated namespace as `kb_search`. There's a documented 3-tier structure (`mcp_server.py`, "3-tier tools") but nothing in the tool descriptions exposes the tier. A `[read]` / `[write]` prefix in the description's first four characters would be enough.
3. **I expected `kb_timeline` to take `kb_name`.** It doesn't. Every other cross-KB tool does. I assumed the omission was a mistake and wasted a call confirming it.
4. **I assumed `mode` defaulted to keyword.** It defaults to `hybrid`. Given 2.1, this default is the single most consequential undocumented-in-context fact in the whole tool surface.
5. **I assumed `kb_orient`'s error code would be `KB_NOT_FOUND`.** `kb_orient`'s own `operational_contracts.error_contract` gives `KB_NOT_FOUND` as its worked example of a machine-readable code. The actual response:
   ```json
   {"error":"KB 'no-such-kb' not found","error_code":"OPERATION_FAILED","retryable":false}
   ```
   `OPERATION_FAILED` is the generic bucket. The documented contract and the implementation disagree in the one example the contract itself chose. No `suggestion` either — a near-match list would be cheap here (54 KB names, all in memory).
6. **I got the FTS quoting wrong on the first try, exactly as documented, and the error still confused me.**
   ```
   call: kb_search(query="detention AND third-party-doctrine", mode="keyword")
   ```
   ```json
   {"error":"Query could not be parsed: no such column: party. If your query uses AND/OR/NOT or phrase quotes, quote any tokens containing - : . yourself (sanitization is skipped once you use operators or quotes).","error_code":"QUERY_SYNTAX","retryable":false,"suggestion":"quote tokens containing - : . yourself when your query uses AND/OR/NOT or phrase quotes"}
   ```
   The *guidance* in this error is excellent (see 2.12). But the leading clause — `no such column: party` — is raw SQLite and sent me looking for a schema problem for a few seconds before the rest of the sentence rescued it. Since the handler already knows enough to append the correct explanation, it could also identify the offending token and say `the token 'third-party-doctrine' was parsed as a column reference` instead of surfacing the fragment `party`.
7. **`kb_list_entries(fields=[...])` doesn't shrink the response much** — tag arrays come back in full, and some entries in this corpus carry 60+ tags (`officer-refusal-decisive-variable-6-...` has ~60). Not a bug, but `fields` didn't buy what I expected it to buy.

## 2.12 What worked — specifically

Naming these precisely, because the failure list above is longer and would give a misleading impression of the tool surface.

- **`kb_backlinks` is the best tool in the set.** Clean shape, `limit`/`offset` present and honored, returns `title` + `entry_type` + `kb_name` so you can triage without a second call. 40 backlinks on `captured-x-operational-signature-...` gave me the entire captured-X variant family in one call — every `*-captured-x-variant` theme, the actor profiles that instantiate them, and the research-packs behind them. I navigated the corpus's densest cluster almost entirely through this one tool. **Do not touch it.**

- **`kb_discover_neighbors`' `exclude_linked` is correct — I checked.** I assumed it was broken when it returned `administrative-engineering-of-accountability-evasion-2026` for a source entry that obviously *should* link there. I grepped the source body for all six returned IDs: **none appear**, and the `## Related entries` section lists seven entirely different targets. So `exclude_linked` did its job and simultaneously surfaced a genuine corpus gap. That's the tool working exactly as designed, and I'd have reported it as a bug if I hadn't verified. (Reporting this partly as a note on how easy it is to file a false bug against this tool.)

- **Semantic mode is genuinely strong on structural descriptions.** I searched, in plain English with no corpus vocabulary:
  > *"a rule is created then a legal form substitution routes around the rule without breaking it"*

  Across 54 KBs and 27k entries, the #1 hit was `legal-laundering` (ramm) — the exact concept, in a KB I had no reason to be looking at. Second query, *"absence of a record is itself the finding — no document exists where one should"*, returned `the-record-and-the-account-of-the-record-2026`, `false-absence-as-the-dominant-failure-mode-2026-08`, `fbi-302-missing-serials`, and `check-the-payload-not-the-status-code` in the top five. **Sections 1.2 and 1.4 of Part 1 exist because of these two calls.** Keyword search would not have found either cluster; I didn't know the words.

- **The `QUERY_SYNTAX` error taught me the rule in one shot.** It stated the constraint, gave the exact remediation, gave a concrete example, and set `retryable: false` so I didn't burn a retry. This is the model the other error paths should follow — compare `{"error":"'kb_name'","error_code":"INTERNAL","retryable":true}` in 2.2.

- **`kb_orient`'s `types` + `top_tags` block is the fastest orientation I've had in an unfamiliar corpus.** Type histogram plus the ten highest-count tags told me in one call that this was a cross-investigation corpus with a Palantir/Thiel/detention spine. That block alone is worth the call. (The schema dump that follows it is another matter — see below.)

- **`kb_get`'s `outlinks` carrying the link `note`** is a small thing that mattered a lot. `legal-laundering`'s links come with sentence-long explanations of *why* each relation holds. Most link models throw that away. It let me understand a concept's position in the graph without reading its neighbors.

- **`cascade_actors` filters are honored.** `capture_lane="Not A Real Lane"` → `{"count":0,"actors":[]}`; `capture_lane="Theological Legitimation", min_importance=8` → 33 actors. Both filters work. Given 2.1, this is worth stating explicitly: **the `cascade_*` tools are more trustworthy than `kb_search`'s filters.**

- **`status` is the one `kb_search` filter that holds in every mode.** Also worth stating, since it's currently the only reliable way to exclude the staging KB's `unprocessed` content (2.7).

## 2.13 One design note on `kb_orient`

`kb_orient(kb_name="cascade-research")` returned roughly 9,000 tokens, of which the parts I used were `total_entries`, `types`, `top_tags`, and `recent` — maybe 1,200 tokens. The remaining ~7,800 was the full write-side schema: `ai_instructions` for every type, `evaluation_rubric`s, `guidelines.contributing`, `guidelines.voice`, `goals.success_criteria`, and the complete 60-entry `relationship_types` map.

All of that is correct and useful **for an agent that is about to write**. For a read-only session it's paid-for and discarded, and it's the first call of every session by design. A `detail: "brief" | "full"` parameter, or simply moving the write-side material behind `kb_schema` (which exists and which I'd call anyway before writing), would cut the standard session's orientation cost by ~85%.

---

## Bug summary for triage

| # | Severity | Component | Issue |
|---|---|---|---|
| 2.1 | 🔴 | `kb_search` | `entry_type`/`tags`/`state`/`fips` silently dropped in hybrid + semantic modes; hybrid is the default |
| 2.2 | 🔴 | `kb_batch_read` | Raw `KeyError: 'kb_name'` when `fields` omits `kb_name`; `retryable: true` is wrong |
| 2.3 | 🟠 | `kb_batch_read` / `kb_get` | Explicit `body_limit` silently overridden by `fields`; blew the output ceiling |
| 2.4 | 🟠 | data / schema | 187 `entry_type` values with systematic `-`/`_`/space drift |
| 2.5 | 🟠 | `cascade_capture_lanes` | 92 lanes vs documented 22; 70 have count ≤ 5; no canonical/other split |
| 2.6 | 🟠 | `investigation_search_all` | `correlate=true` groups nothing — fails even on identical `entry_id` across KBs |
| 2.7 | 🟠 | `kb_timeline`, cross-KB tools | Non-canon staging KB outranks canon; `kb_timeline` has no `kb_name` |
| 2.8 | 🟡 | `cascade_network` | No `limit`/`offset`; unbounded on hub nodes |
| 2.9 | 🟡 | `kb_get`, `cascade_network` | Dangling wikilinks returned as `title: null` with no `resolved` flag |
| 2.10 | 🟡 | `kb_discover_neighbors` | Returns same-KB results despite "other KBs" description; process-task stubs rank high |
| 2.11.1 | 🟡 | `kb_orient` | Required `kb_name` contradicts "first call in any session" |
| 2.11.5 | 🟡 | error contract | `OPERATION_FAILED` returned where the documented contract's own example says `KB_NOT_FOUND`; no `suggestion` |
| 2.13 | 🟡 | `kb_orient` | ~85% of payload is write-side schema, returned on every read session's first call |

## Corpus actions suggested (Part 1)

| Priority | Action |
|---|---|
| High | Link `dialog-as-cross-lane-convergence-venue-...` ↔ `capture-is-architecture-not-conspiracy-of-operators` (§1.1) |
| High | Create a parent mechanism for the six legal-form-substitution instances; link `legal-laundering` (ramm) across the KB seam (§1.2) |
| High | Create the unnamed "record as object of capture" theme spanning Layers 8 + 13, the coroner cluster, the Epstein records entries, and `false-absence-as-the-dominant-failure-mode` (§1.4) |
| Medium | Resolve or document the `Theological Legitimation` lane's three incompatible senses (§1.3) |
| Medium | Connect `officer-refusal-decisive-variable-6-...` to the resistance-matrix themes; it's an orphan (§1.5) |
