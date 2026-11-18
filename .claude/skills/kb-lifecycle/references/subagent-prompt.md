# Subagent Prompt Template

Use this template when launching Sonnet subagents to create entry batches during Phase 2 population.

## Template

```
Create entries for the <kb-name> KB.
Working directory: <path-to-kb-parent-directory>

KB description: <from kb.yaml description>
Entry type for this batch: <type>
Topics to cover: <specific list from scope>

IMPORTANT -- Entry creation method:
Use `pyrite create` with `--field` for declared custom fields and `--body-file`
for a body stored in a file, or write a complete Markdown file with ALL fields
in YAML frontmatter and import it with `pyrite add`. Both methods persist
custom fields; use the KB schema's declared names and allowed values.

Example create (supply this entry type's other required fields too):
  pyrite create -k <kb-name> -t <entry-type> --title "<Title>" --body-file <body-path> --field research_status=draft

File format:
---
id: <kebab-case-id>
title: "<Title>"
type: <entry-type>
importance: <1-10>
tags:
- tag1
- tag2
<custom-field>: <value>
<custom-field>: <value>
research_status: draft
---

<body content with [[wikilinks]]>

Then run:
  pyrite add <file-path> --kb=<kb-name>

Write files to the correct subdirectory: <kb-name>/<subdirectory>/<id>.md

ENTRY ID LIST -- use these exact IDs for wikilinks:
<paste the scaffold ID list here>

Rules:
- Every entry body MUST include [[wikilinks]] using IDs from the list above
- ONLY link to IDs on the list -- do not invent links to entries that are not planned
- Follow the ai_instructions from kb.yaml for this type
- Importance scale: 10=foundational, 7-9=significant, 4-6=supporting, 1-3=minor
- ALWAYS set research_status to "draft" or "stub" -- never "complete"

DOCUMENTING GAPS IS SUCCESS, NOT FAILURE:
An entry that honestly marks what it doesn't know is MORE valuable than one
that fills every gap with plausible-sounding fabrication. You are rewarded for:
- Marking claims as "(approximate)" or "(unverified)"
- Using `research_status: stub` when you lack detail
- Writing "## Research needed" sections that identify what a fact-checker
  should verify
- Creating [[wikilinks]] to entries that SHOULD exist but are NOT on the
  ID list -- mark these with a comment like "<!-- wanted: not yet created -->"
  so gap analysis can find them later
- Saying "date unknown" or "co-authors unverified" rather than guessing

You are NOT rewarded for:
- Completeness that required guessing
- Fluent prose that papers over uncertainty
- Presenting every claim at the same confidence level

FACTUAL ACCURACY RULES (critical):
- If you are NOT CERTAIN of a date, co-author, or specific detail, mark it
  with "(approximate)" or "(unverified)" in the text. Never present uncertain
  information as definitive. Use "c. YYYY" for approximate dates.
- Do NOT expand acronyms, formulas, or step-by-step frameworks from memory.
  Instead, reference them via [[wikilink]] (e.g., "the [[focccus-formula]]")
  without expanding. The concept entry that defines the expansion will be
  created later -- it does not exist yet. Your job is to link to it, not
  define it. If you expand a formula and get it wrong, the error propagates
  to every entry that copies your expansion.
- Do NOT invent biographical details (dates of career changes, dates of
  discoveries, specific organizational roles) if you are not confident.
  It is better to omit a date than to fabricate one.
- HIGH-RISK CLAIM TYPES that must be hedged if uncertain:
  - Dates of first publication
  - Dates of biographical events (career changes, discoveries, relocations)
  - Exact co-author or collaborator lists
  - Exact organizational affiliations and roles
  - Conference names, locations, and dates
  - Subtitle or full title of works (get the exact title or mark as approximate)

AT THE END OF YOUR WORK, report a brief gap inventory:
- How many claims did you mark as approximate/unverified?
- How many "wanted" wikilinks did you create (entries that should exist)?
- What topics do you know you lack detail on?
This inventory feeds directly into research discovery (Phase 2 Stage 2)
and gap analysis (Phase 4).
```

## What this template fixes

| Problem | How the template addresses it |
|---|---|
| Fabricated acronym expansions | Bans expanding from memory; references concept entry instead |
| Wrong dates presented as definitive | Requires hedging with "(approximate)" or "c. YYYY" |
| Missing `research_status` | Makes `draft` or `stub` mandatory |
| Invented biographical details | Explicitly says "better to omit than fabricate" |
| Aspirational wikilinks to nonexistent entries | Constrains to pre-planned ID list only |
| RLHF-driven completeness bias | Explicitly rewards gap documentation over gap-filling; reframes honesty as success |
| Silent knowledge gaps | Gap inventory at end of work surfaces what subagent doesn't know, feeding downstream phases |
| Wanted-page discovery | "Wanted" wikilink comments create machine-readable gap signals for Phase 4 |

## Notes

- The template is intentionally restrictive about factual claims. This produces entries that are less fluent but more honest. The fluency can be improved during judgment QA; fabricated facts are much harder to catch and fix.
- Subagent entries should be treated as first drafts that require verification, not finished products.
- **Why gap documentation matters:** LLMs trained with RLHF have a strong bias toward producing complete, confident-sounding output. The "documenting gaps is success" framing explicitly inverts this incentive: the subagent is told that marking uncertainty is the rewarded behavior. This is more effective than simply saying "be accurate" because it gives the model a positive action (document the gap) rather than a prohibition (don't fabricate).
- **Wanted wikilinks as gap signals:** When a subagent creates a `<!-- wanted: not yet created -->` comment next to a wikilink, this becomes a machine-readable signal that can be grepped during Phase 4 gap analysis. The wikilink itself maps the conceptual territory the KB should cover; the comment flags that it's unfilled.
