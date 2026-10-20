---
name: kb-lifecycle
description: >
  This skill should be used when the user asks to "create a KB", "build a KB",
  "populate a KB", "expand a KB", "add entries", "batch create entries",
  "fill in gaps", "cross-link KBs", "run QA on a KB", or asks to build out
  a knowledge base from a description or spec. Provides the orchestration
  workflow for creating, populating, validating, and cross-linking
  intellectual-biography and movement knowledge bases using pyrite CLI
  and Claude Code subagents.
---

# KB Lifecycle Orchestration

Orchestrate the full lifecycle of a pyrite knowledge base: creation from a description or spec, population via iterative research and parallel subagents, multi-layer QA, gap research, and cross-KB linking.

## Prerequisites

- pyrite CLI available (verify with `which pyrite` or `pyrite --help`)
- Existing KBs serve as reference for kb.yaml quality — use `pyrite orient --kb <name>` to inspect any KB's schema

## Critical Rules

These rules exist because violating them caused real problems across multiple KB builds. They are non-negotiable.

1. **Subagents fabricate.** Sonnet subagents will confidently produce wrong dates, invented acronym expansions, wrong co-author lists, and incorrect biographical details. This is not occasional — it is the default for specific, verifiable claims. Every scaffold entry must be treated as unverified until fact-checked. See "Known Risk: Subagent Fabrication" below.

2. **Never declare population done after scaffold.** The scaffold captures what the LLM already knows. Research discovery consistently finds 20-30% more entries and corrects factual errors in existing ones. Skipping research ships a KB with known gaps and unverified claims.

3. **When judgment QA finds one error, grep for the pattern.** Errors are rarely isolated. A wrong date in one entry usually means the same wrong date in 2-3 other entries. A wrong acronym expansion in one place means it propagated to every entry that references it. After finding any error, immediately search the entire KB for the same category of error.

4. **Structural QA passing (0 issues) does NOT mean the KB is correct.** Structural QA checks schema conformance. It cannot detect wrong dates, fabricated details, misclassified entries, or entries assigned to the wrong era. Judgment QA and fact-checking are always required.

5. **Cross-KB linking is a thinking task.** The intellectual work of identifying how concepts in one KB relate to concepts in another requires reading entries in both KBs and understanding the relationship. `pyrite links suggest` can provide leads but cannot judge intellectual connections.

6. **A KB needs 30-50+ entries to justify its existence.** Below 30, the knowledge graph lacks density and the entries might as well live in a parent KB. Target 30-50 for focused/secondary subjects, 60-120 for major thinkers with large bodies of work. These are guidelines — quality matters more than count — but they help calibrate when to stop researching.

## Core Principle: Phases Are Not a Waterfall

The phases below are **not strictly sequential**. Any phase can send you back up the stack:

- Research during population (Phase 2) may reveal the kb.yaml scope is wrong -> return to Phase 1
- QA (Phase 3) may find missing entries or misclassified types -> return to Phase 2
- Gap research (Phase 4) may find entire categories of work the initial population missed -> return to Phase 2
- Cross-KB linking (Phase 5) may expose conceptual gaps -> return to Phase 4

**Always ask: does what I just learned change something upstream?** If yes, go fix it before continuing downstream.

## Known Risk: Subagent Fabrication

This is the single biggest quality risk in the workflow. Sonnet subagents produce fluent, confident text that contains fabricated details. The fabrications are dangerous precisely because they look correct — they are specific, plausible, and presented without hedging.

### What gets fabricated

| Category | Example from real builds | Why it's dangerous |
|---|---|---|
| **Acronym/formula expansions** | FOCCCUS expanded incorrectly — all six words wrong | Propagates to every entry that references the formula |
| **Dates of biographical events** | "Discovered TOC in the 1990s" (correct: ~2003) | Cascades into era entries and timeline references |
| **Era attributions** | 2020 book attributed to "2009-2018" era | Makes era entries internally inconsistent |
| **Co-author lists** | Missing co-authors on collaborative works | Makes the entry factually incomplete |
| **Organizational details** | Program described as monolithic when it had 8 modules | Misrepresents the subject's actual work |

### Why this happens

LLMs trained with RLHF have a strong bias toward producing complete, confident-sounding output — the training reward signal favors helpfulness over epistemic honesty. The default behavior is to fill knowledge gaps with plausible fabrication rather than flag them. This is worse in models with stronger people-pleasing tendencies, but affects all models to some degree.

### How to mitigate

1. **Make gap documentation the rewarded behavior.** The subagent prompt (see `references/subagent-prompt.md`) explicitly reframes honesty as success: marking claims as "(approximate)", creating `<!-- wanted -->` wikilinks, and reporting a gap inventory are presented as positive outputs, not failures. This inverts the RLHF completeness bias by giving the model a positive action (document the gap) rather than only a prohibition (don't fabricate).
2. **In subagent prompts:** Require hedging on uncertain claims. Ban expanding acronyms/formulas from memory. Require `research_status: stub` or `draft`.
3. **After scaffold:** Run the fact-check protocol (Phase 3, Layer 3) before proceeding.
4. **During research:** Expect research to correct scaffold errors. This is normal, not exceptional.
5. **Use gap signals downstream.** Subagent gap inventories and `<!-- wanted -->` comments feed directly into Phase 2 Stage 2 (research discovery) and Phase 4 (gap research). Grep for `<!-- wanted` to find unfilled wikilinks.

## Workflow Overview

### Phase 1: Create KB

**Input:** Either a spec file (e.g., from a kb-ideas tracker) or a freeform description from the user. A spec should include: Subject, Scope, and optionally Connections to other KBs.

**Steps:**

1. If working from a spec file, read it and extract: Subject, Scope, Connections, kb_type
2. If working from a freeform description, discuss with the user to establish: the subject, what the KB should cover, and whether it's an `intellectual-biography` or `movement` type
3. Determine the KB path. Use `pyrite config` to find the configured data directory, or ask the user where KBs live.
4. Run `pyrite init --template <kb_type> --path ./<short-name> --name <short-name> --no-examples`
5. Read the generated `kb.yaml` — it will have bare types with optional fields
6. **Rewrite kb.yaml** to match the quality of existing KBs. For detailed guidance, read `references/kb-yaml-exemplar.md`. Key requirements:
   - Promote key optional fields to required (writing_type, date on writings; date_range on eras; source_type, date on sources; role on people; org_type on organizations)
   - Add a `concept` type if the subject includes distinct frameworks or theories
   - Add `ai_instructions` to every type, specific to the subject
   - Add field definitions with types, descriptions, and enum values appropriate to the subject
   - Write `guidelines` (contributing, quality, voice) derived from the subject description
   - Write `goals` (primary, success_criteria) derived from the scope
   - Write `evaluation_rubric` items (5-6 KB-level rubric items)
   - Add `relationship_types` appropriate to the domain
   - Set `policies: qa_on_write: true`
7. Register the KB if not auto-discovered: `pyrite kb add ./<short-name> --name <short-name> --type <kb_type>`
8. Run `pyrite index build <kb-name>` to initialize the index

### Phase 2: Populate KB

**Input:** A KB name that has been initialized but is empty or thin

Population has three stages: **research** (establish the facts), **factual entries** (concepts and frameworks that other entries reference), and **concept entries** (the intellectual core, built on the verified foundation). This order bakes quality in — downstream entries reference verified upstream entries rather than independently guessing at facts.

#### Stage 1: Research First

Before writing any entries, establish the factual foundation. This prevents the scaffold-then-fix pattern where subagents fabricate details that must be caught and corrected later.

1. Read the kb.yaml to understand the scope, goals, and type schemas
2. **Draft a preliminary ID list** from LLM knowledge. Group by type. This is a planning document, not a commitment — research will change it.
3. **Run a lightweight research pass** to verify and expand the ID list:
   - Check the subject's Amazon author page, Goodreads author page, and personal website
   - Check Wikipedia bibliography if one exists
   - Search `"<subject name>" complete bibliography` or `"<subject name>" publications`
   - Search `"<subject name>" podcast OR interview OR keynote OR talk`
   - For people entries: check acknowledgments in the subject's major books, co-author lists
   - For events: check conference program archives
4. **Build a verified facts sheet** — a working document (not a KB entry) that records confirmed data points:
   - Publication dates (with source: "Amazon listing", "publisher page", etc.)
   - Co-author lists (with source)
   - Key biographical dates and career milestones (with source)
   - Acronym/formula expansions (with source: "defined in [book title], chapter N")
   - Conference names, dates, locations (with source)
5. **Revise the ID list** based on research. Add entries the LLM didn't know about. Remove entries that turn out not to exist. Correct titles, dates, and classifications.

This stage produces two artifacts: a **verified ID list** and a **facts sheet**. Both feed into the next stages.

##### Research discovery cycles

If the initial research pass surfaces significant new material, run additional discovery cycles. Each cycle must produce a brief discovery log:

```
## Discovery Cycle <N>

### Queries run
- <query> -> <source> -- <what was found>

### New entries discovered
- <entry-id> (<type>) -- <why it was missed>

### Corrections to existing entries
- <entry-id>: <what was wrong> -> <what's correct>

### Surprises
- <something unexpected>

### Should I run another cycle?
<yes/no -- and why>
```

**What to search for** (beyond the initial pass):
- `"<subject name>" site:amazon.com` — catches self-published and minor works
- `"<subject name>" foreword OR introduction OR column OR article`
- Subject's Substack, LinkedIn, personal blog
- `"<subject name>" keynote OR conference OR summit`

**Calibration:** For a well-known subject, expect research to add 20-30% more entries than the LLM-drafted list. If research adds nothing, the search strategy is too narrow.

**When to stop:** A cycle produces 0 new entries and 0 corrections, AND you have checked at least one authoritative bibliography source. Minimum 1 full research cycle; 2 for major subjects.

#### Stage 2: Factual Entries (Writings, People, Events, Organizations, Sources)

With research complete, create the entries that are primarily factual — where verified dates, titles, co-authors, and affiliations carry most of the weight. These are writings, people, events, organizations, and sources.

1. Plan entry batches — group by type, aim for 8-12 entries per batch
2. For each batch, launch a Claude Code subagent (Sonnet). For entry quality standards, read `references/entry-quality.md`. For the subagent prompt template, read `references/subagent-prompt.md`.
3. **Include verified facts in the subagent prompt.** From the facts sheet, provide:
   - Confirmed publication dates for any writings in the batch
   - Confirmed co-author lists
   - Confirmed biographical dates
   - The canonical ID list (so wikilinks are correct)
   - Instruction: reference concept entries via `[[wikilink]]` but do NOT expand or define the concepts — those entries don't exist yet, and the wikilinks will be filled later
4. **When evaluating subagent output**, check for the gap inventory at the end of each batch. A subagent that reports "3 claims marked approximate, 2 wanted wikilinks created, uncertain about X" has done better work than one that reports nothing — the latter almost certainly fabricated to fill gaps silently. If a subagent returns no gap inventory, treat its output as higher risk during QA.
5. **Collect gap signals** from all subagent batches into a running list. These feed directly into Stage 3 (concept entries need to address them) and Phase 4 (gap research).
6. After batches complete, run `pyrite index build <kb-name>` and `pyrite qa validate <kb-name>`
7. Apply safe fixes with `pyrite qa fix <kb-name>`

#### Stage 3: Concept Entries (Last, Not First)

Concept entries are the **capstone**, not the foundation. They require the deepest understanding of the subject — synthesizing across writings, people, and events to define frameworks, theories, and intellectual contributions. Creating them early, before the factual base is in place, produces the worst fabrication (wrong acronym expansions, invented framework descriptions).

1. By this point you have: verified research, factual entries that describe the subject's works and career, and wikilinks pointing at concept IDs that don't exist yet (wanted pages).
2. Create concept entries directly (Opus, not subagent delegation) — these are the intellectual core and the most important entries to get right.
3. **For every named formula or framework**, define the canonical expansion in exactly one concept entry, verified against the research from Stage 1. This concept entry becomes the single source of truth.
4. Write concept entries as synthesis: they should draw on and connect the factual entries, not duplicate them. A good concept entry references the writing where the concept first appeared, the people who developed it, and the events where it was presented.
5. Run `pyrite index build <kb-name>` and `pyrite qa validate <kb-name>`
6. Rebuild index: `pyrite index build <kb-name> --force --no-embed`

**Why concepts last:** Concept entries synthesize understanding — they are the last thing you can write well, not the first. The FOCCCUS formula fabrication incident happened precisely because the concept entry was written before the research that would have provided the correct expansion. Factual entries (writings, events) can reference `[[focccus-formula]]` as a wikilink before the concept entry exists; the wikilink becomes a wanted page that signals "this needs to be defined" rather than a prompt to guess.

**Model routing:** Use Sonnet subagents for bulk entry creation. Use Opus for concept entries, planning, research, and reviewing quality. Use Haiku for mechanical fixes.

### Phase 3: QA Validation

**Input:** A populated KB

Because Phase 2 now researches facts before generating entries and verifies concept definitions before bulk population, QA should find fewer factual errors than in earlier workflow versions. But QA is still required — subagents still produce analytical and classification errors, and the research pass may have missed things. QA here is a verification net, not the primary quality mechanism.

QA has three layers: **structural** (automated), **judgment** (LLM-reviewed), and **fact-check** (targeted verification of anything not covered by the Phase 2 facts sheet). All three are required.

#### Layer 1: Structural QA

1. Run `pyrite qa validate <kb-name> --format json` — capture all structural issues
2. **Triage broken wikilinks.** Not all broken wikilinks are errors. Categorize each one:

| Category | Example | Action |
|---|---|---|
| **Typo / wrong ID** | `[[herzogwilliam]]` when entry is `herzog-william` | Fix the ID |
| **Cross-KB reference** | `[[caiaphas]]` in a KB where it belongs to another KB | Convert to plain text or add to cross-kb-links.yaml |
| **Wanted page** | `[[sanctuary-movement-precedent]]` — entry should exist but doesn't yet | **Leave it as a wikilink.** It is a gap signal. Add to the gap research list (Phase 4). |
| **Subagent-invented nonsense** | `[[pattern-commons-layer]]` when no such concept exists | Remove brackets, convert to plain text |

Wanted-page wikilinks are valuable gap signals — they map conceptual territory the KB should cover. Do NOT remove them or convert to plain text. They feed directly into Phase 4 gap research. When an entry IS created to fill the wanted page, the wikilink resolves automatically.

3. **Fix wikilink alias mismatches.** Parallel subagents often create variant IDs for the same entity (e.g., `walter-shewhart` vs `walter-a-shewhart`). Grep all entry files for `\[\[` patterns, compare against actual entry IDs (`pyrite list-entries --kb <kb-name>`), and do global search-replace to normalize aliases.
4. Run `pyrite qa fix <kb-name> --dry-run` — review proposed fixes
5. Apply: `pyrite qa fix <kb-name>`
6. Run `pyrite qa validate <kb-name>` — note: wanted-page wikilinks will still show as broken links. This is expected. The target for structural QA is 0 issues EXCEPT for documented wanted pages.
7. **Verify index completeness:** Compare `pyrite list-entries --kb <kb-name>` count against the number of markdown files on disk. If they differ, some entries are not indexed — investigate and rebuild with `--force`.
8. Run an **inbound link audit**: use `pyrite qa gaps` to find entries with 0 inbound links. These are either isolated or under-connected — add references from appropriate entries.

**Note on `list-entries` limits:** The `list-entries` command may have a default limit (e.g., 50 entries). Use `--limit 500` or count files on disk directly to verify totals.

#### Layer 2: Judgment QA

Structural QA catches schema violations. It does NOT catch:
- **Factual errors** — wrong dates, wrong co-authors, wrong attributions
- **Misclassifications** — a primary source filed as secondary, a speech filed as a book
- **Importance miscalibration** — minor works rated 7, major works rated 4
- **Thin entries** — entries that technically pass schema but have no analytical substance
- **Missing context** — entries that describe but don't contextualize

**Structured sampling strategy** (not random — target highest-risk entries):

1. Read ALL entries with importance >= 9 (usually 2-4 entries)
2. Read at least 1 entry from each type that has entries
3. Read ALL entries created by the research agent (highest fabrication risk)
4. For any entry that references a named formula, acronym, or framework, verify the expansion matches the concept entry that defines it

**For each entry reviewed:**
- Check against the KB's `evaluation_rubric` and `references/entry-quality.md`
- Check classification: is the entry type correct? Is `writing_type` or `source_type` accurate?
- Check importance calibration: are scores consistent across the KB?

**The pattern-hunt rule:** When you find ANY error:
1. Identify the error category (wrong date, wrong era, wrong expansion, wrong attribution)
2. Grep the entire KB for the same category of error
3. Fix all instances before continuing

**Systematic cross-checks** (run these even if sampling looks clean):
- **Date-era consistency:** For every writing/event with a date, verify that any era reference in the body matches the date range of that era entry
- **Acronym consistency:** Grep for any acronym/formula that appears in multiple entries. Verify all expansions match the canonical definition in the concept entry.
- **Wikilink audit:** Grep for `\[\[` patterns, diff against actual entry IDs. Unresolved wikilinks fall into four categories (see the triage table in Layer 1): typos to fix, cross-KB references to formalize, wanted pages to preserve as gap signals, and subagent-invented nonsense to remove. Do NOT reflexively remove all broken wikilinks — wanted-page wikilinks are valuable gap signals that feed Phase 4.

#### Layer 3: Fact-Check

This layer catches claims that slipped through the Phase 2 research pass — facts the subagents introduced that weren't in the verified facts sheet. Because Phase 2 now researches facts before generating entries, this layer should be lighter than in a workflow without upfront research. Focus on claims that **were not** covered by the facts sheet.

**What to fact-check** (skip items already verified in Phase 2 Stage 1):

| Claim type | How to verify |
|---|---|
| Publication dates not on the facts sheet | Amazon, Goodreads, publisher page, WorldCat |
| Co-author lists not on the facts sheet | Title page (Amazon "Look Inside" or Goodreads) |
| Acronym/formula expansions in non-concept entries | Compare against the canonical concept entry (should match exactly) |
| Key biographical dates introduced by subagents | Subject's LinkedIn, personal website, or interviews |
| Conference names and dates not from research | Conference website archives, event listings |
| Organizational affiliations introduced by subagents | Organization's website, subject's LinkedIn |

**Minimum fact-check scope:**
- All entries with importance >= 8
- Every named formula or framework expansion that appears **outside** the canonical concept entry
- Every date that anchors an era boundary
- Any claim marked "(approximate)" or "(unverified)" by subagents — decide: verify or remove

**When fact-checking reveals errors**, apply the pattern-hunt rule: grep the KB for the same error and fix all instances.

**If judgment QA or fact-checking reveals significant issues -> return to Phase 2** to fix entries or create missing ones. Do not proceed to Phase 4 with known content problems. This loop is expected — most first-round KBs need at least one QA-driven correction pass.

### Phase 4: Gap Research

**Input:** A validated KB where initial population is complete but coverage may be thin

**Gap sources — combine all of these:**

1. Run `pyrite qa gaps <kb-name> --format json` — structural gaps (sparse types, isolated entries)
2. Read the gaps report — identify:
   - Types with few entries relative to scope
   - Missing topics from the original scope description
   - Entries with no inbound links (unreferenced)
   - Entries with no outbound links (isolated)
3. **Collect wanted-page wikilinks.** Two sources:
   - `grep -r '<!-- wanted' <kb>/` — entries that subagents explicitly flagged as needed but not yet created
   - Broken wikilinks from `pyrite qa validate` that were triaged as "wanted page" in Phase 3 Layer 1 — wikilinks to entries that SHOULD exist but don't yet

   Both are high-value gap signals. They represent conceptual territory the KB maps but doesn't yet cover. Prioritize by how many entries link to the wanted page (more inbound links = higher priority gap).
4. **Check subagent gap inventories** from Phase 2 — topics the subagents reported lacking detail on
5. **Check entries marked `research_status: stub`** — these are entries that exist but lack substance. Decide: expand with research, or remove if they don't justify their existence.

**Steps:**

1. Prioritize gaps by importance to the KB's stated goals
2. For each gap, run iterative research discovery cycles with mandatory surprise logging — search, read, reflect, re-search
3. After filling gaps, **re-run Phase 3 QA** (all three layers) on the new entries

### Phase 5: Cross-KB Linking

**Input:** Two or more KBs that share intellectual territory

Cross-KB linking is primarily a **thinking task** — understanding how ideas in one KB relate to ideas in another. The tooling supports but does not replace this judgment.

#### Step 1: Identify connections (thinking)

1. Read the scope description or goals for each KB
2. Read high-importance entries (>= 7) in both KBs
3. Identify connections using this taxonomy:

| Relation type | Meaning | Example |
|---|---|---|
| `same-subject` | Same person/concept in both KBs | Goldratt appears in both the Goldratt and Ching KBs |
| `adaptation` | One thinker adapted another's idea | Ching adapted Goldratt's Five Focusing Steps |
| `simplification` | One thinker made another's idea more accessible | FOCCCUS simplifies the Five Focusing Steps |
| `continuation` | One thinker continued another's tradition | Ching continued Goldratt's business-novel pedagogy |
| `critique` | One thinker critiqued another's idea | |
| `parallel-development` | Similar ideas developed independently | |
| `derivative-work` | A work based on another work | |
| `primary-source` | Direct encounter, interview, correspondence | |
| `extended-by` | Target extended the source idea to new domains | Ching extended Critical Chain to software |

4. Draft a connection list: source entry -> target entry, relation type, one-line note

#### Step 2: Create link files (tooling)

Write `<kb>/cross-kb-links.yaml` with the connections identified in Step 1:

```yaml
links:
  - source: <source-entry-id>
    target: <target-entry-id>
    target_kb: <target-kb-name>
    relation: <relation-type>
    note: "<one-line description of the connection>"
```

Create links in **both directions** — if KB-A -> KB-B has 11 links, KB-B -> KB-A should have reciprocal links (not necessarily 1:1, but the major connections should be represented from both sides).

#### Step 3: Validate

1. Run `pyrite qa validate <source-kb>` — check for broken links
2. Verify that target entry IDs actually exist in the target KB
3. Rebuild both KB indexes

**If cross-linking reveals conceptual gaps** (e.g., a concept in KB-A has no counterpart entry in KB-B, but should), **return to Phase 2 or Phase 4** for the target KB.

### Phase 6: Deployment

After all phases are complete, ensure the KB is accessible.

1. Verify the KB is registered: `pyrite list` should show it with the correct entry count
2. If the KB was created in a new directory, register it: `pyrite kb add ./<kb-path> --name <kb-name> --type <kb_type>`
3. Rebuild the index: `pyrite index build <kb-name> --force`
4. If cross-KB links were added to other KBs, rebuild those indexes too
5. If using the MCP server (for Claude Desktop integration), rebuild the MCP index separately — the MCP server may use a different config directory than the CLI. Check with `pyrite config` and ensure the MCP server's config includes the new KB.

## Coverage Expectations

| Subject prominence | Expected range | Examples |
|---|---|---|
| Major thinker (>10 significant works, large network, multiple eras) | 60-120 entries | Deming, Goldratt |
| Significant practitioner (5-10 works, moderate network) | 30-60 entries | Boyd, Blank |
| Focused/secondary subject | 30-50 entries | Single-concept or narrow-scope subjects |

Below 30 entries, the knowledge graph lacks density and the entries may not justify a dedicated KB — consider whether the material belongs in a parent KB instead. These counts can be revised upward if early research reveals more material than expected, but they serve as minimum viability thresholds.

## Anti-Patterns

| Trap | Why it fails | Fix |
|------|-------------|-----|
| Declare population done after scaffold | You only captured what the LLM already knew | Always run research discovery cycles after scaffolding |
| Pre-plan every entry before any research | Prevents discovery; you plan what you expect | Scaffold first, then let research expand the plan |
| Trust structural QA as sufficient | Misses factual errors, misclassifications, thin content | Always run judgment QA and fact-check |
| Treat phases as strictly sequential | Findings in later phases often require upstream fixes | Go back up the stack when research reveals problems |
| Skip research for "well-known" subjects | LLMs are most confident about well-known subjects but still miss articles, speeches, co-authors, educational materials | Research is always needed -- LLM confidence != completeness |
| Accept 0 QA issues as "done" | Structural QA checks schema, not truth | 0 issues means structurally valid, not factually correct |
| Trust subagent output as factual | Subagents fabricate dates, acronyms, co-authors, and details with high confidence | Fact-check all specific claims in high-importance entries |
| Fix one error without searching for the pattern | Errors propagate -- the same wrong date appears in 3 files | Every error triggers a KB-wide grep for the same pattern |
| Treat cross-KB linking as a tooling task | `pyrite links suggest` can't judge intellectual relationships | Read entries in both KBs, think about the connections, then write the YAML |
| Let parallel agents commit independently | Shared git staging area causes cross-contamination -- Agent A's staged files get swept into Agent B's commit | Orchestrator handles all commits; agents only edit files |

## Parallel Execution

Multiple subagents can run simultaneously ONLY if they target different KBs. Never run two agents writing to the same KB directory — file edits will conflict and git staging will cross-contaminate commits.

**Orchestrator commit workflow** (after agents complete):
1. `git status` to see all changes
2. Stage and commit per-KB: `git add <kb-dir>/` then commit with a message summarizing that KB's changes
3. Verify with `git log --stat` that each commit contains only the intended KB's files

Agents must NOT run `git add` or `git commit`. The orchestrator commits all changes after verifying the work.

## Additional Resources

### Reference Files

- **`references/kb-yaml-exemplar.md`** -- Annotated kb.yaml showing what makes a good configuration
- **`references/entry-quality.md`** -- What makes a good intellectual-biography entry, quality checklist, common mistakes
- **`references/subagent-prompt.md`** -- Subagent prompt template with fabrication mitigations
- **`references/lessons-learned.md`** -- Case studies from real KB builds documenting problems encountered and solutions

### Related Skills
