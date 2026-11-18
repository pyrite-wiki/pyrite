# Lessons Learned from KB Builds

Case studies from real knowledge base builds documenting problems encountered, root causes, and solutions. These lessons shaped the current workflow. Understanding *why* each rule exists helps you apply it correctly in new situations.

## Case Study 1: Scaffold-Only Population Misses Significant Works

**KB:** Goldratt (Theory of Constraints)
**Phase:** Population (Phase 2)

The initial workflow pre-planned all entries from LLM knowledge before creating any. This produced 10 writing entries covering the well-known novels and major books. Research-driven discovery (web search for bibliographies, publisher catalogs, Goodreads) found 7 additional works the LLM missed:

- "Cost Accounting: Public Enemy #1" (1983 APICS speech -- pre-dates The Goal)
- "Essays on the Theory of Constraints" (1987-90 journal, compiled 1998)
- "Late Night Discussions" (1998, Industry Week columns)
- "The Goal Movie" (2002 video, IMDB-listed)
- "Production the TOC Way" (2003 workbook + simulator)
- "Beyond the Goal" (2005 audio lectures)
- Reclassification of "Standing on the Shoulders of Giants" from source to writing

**Lesson:** LLMs recall novels and famous books but systematically miss articles, speeches, columns, educational materials, and audio/video. Research is always needed, especially for writings. Expect research to add 20-30% more entries than the LLM-drafted list.

## Case Study 2: Structural QA Passes but Judgment QA Fails

**KB:** Goldratt
**Phase:** QA (Phase 3)

After scaffolding, `qa validate` reported 0 issues. But manual review found:
- "Isn't It Obvious?" was missing co-authors (Ilan Eshkoli, Joe Brownleer)
- "Standing on the Shoulders of Giants" was misclassified as a source (it's a Goldratt-authored writing)
- "Goldratt Satellite Program" described as monolithic when it actually had 8 distinct modules

**Lesson:** Structural QA checks schema conformance. It cannot catch wrong co-authors, misclassified types, or incomplete descriptions. Judgment QA (reading entries and spot-checking facts) is always required. This led to the three-layer QA model (structural + judgment + fact-check).

## Case Study 3: Subagent Fabrication Is the Default, Not the Exception

**KB:** Ching (TOC for software)
**Phase:** Population (Phase 2) and QA (Phase 3)

Three distinct fabrication incidents in a single KB build:

1. **FOCCCUS formula expanded incorrectly** -- all six words wrong. The subagent produced a plausible-sounding expansion that was completely fabricated. This propagated to both the concept entry AND the era entry.
2. **Framework step description wrong** -- "U = Upshift if needed" when the correct meaning was entirely different
3. **TOC discovery date wrong** -- placed "in the 1990s" (correct: ~2003) -- propagated to 3 files
4. **Era misattribution** -- CorkScrew Solutions (2020) attributed to the "writing-and-consulting-period" (2009-2018)

**Root cause:** LLMs trained with RLHF have a strong bias toward producing complete, confident-sounding output. The most dangerous pattern is "confident specificity": the subagent doesn't hedge, so fabricated details look identical to correct ones.

**Solution:** The subagent prompt template now explicitly bans expanding acronyms/formulas from memory, requires hedging on uncertain claims, and reframes gap documentation as success rather than failure. Concept entries are now created last (after research and factual entries), not first.

## Case Study 4: Parallel Agents Contaminate Git Commits

**KB:** Multiple (Boyd, Deming, TPS, Goldratt batch QA)
**Phase:** QA (Phase 3)

When 4 judgment QA agents ran simultaneously, each editing files in their own KB directory, git operations interfered:
- TPS agent committed first but its `git add` swept in already-staged goldratt files
- Deming agent committed second but swept in already-staged boyd files
- No data was lost, but commit messages were misleading

**Root cause:** `git add <directory>` only adds files from that directory, but files already staged by another agent's `git add` persist and get included in the next `git commit`.

**Solution:** Agents must not run `git add` or `git commit`. The orchestrator handles all commits after agents complete, staging and committing per-KB in clean separate commits.

## Case Study 5: The Pattern-Hunt Rule

**KB:** Multiple (observed across 12+ KB builds)
**Phase:** QA (Phase 3)

Across 12 KBs, every single one had factual errors -- typically 3-6 per KB. The most common categories:
- Wrong dates (off by 1-2 years): founding dates, publication dates, biographical dates
- Missing co-authors on collaborative works
- Organizational history confusion (conflating related but distinct entities)
- Misattribution (crediting the wrong person for a concept or work)
- Numeric errors (sales figures, page counts, level counts)

**Key observation:** Errors are rarely isolated. A wrong date in one entry usually means the same wrong date in 2-3 other entries, because subagents working from the same LLM knowledge reproduce the same error independently.

**Solution:** The pattern-hunt rule: when you find ANY error, identify the error category, grep the entire KB for the same pattern, and fix all instances before continuing.

## Case Study 6: Research Discovery Was Underspecified

**KB:** Ching
**Phase:** Population (Phase 2)

The workflow said "search broadly" with 4 bullet points and "minimum 2 cycles." In practice, a single research agent launched one wave of searches. The "minimum 2 cycles" requirement was easy to ignore because it was buried in text with no enforcement mechanism.

**Solution:** Research cycles now require a structured discovery log (checkpoint format with queries run, entries discovered, corrections, surprises, and explicit "should I run another cycle?" decision). Concrete query templates were added for different entry types, along with calibration guidance (expect 3-8 new entries in first cycle; 0 suggests wrong strategy).

## Case Study 7: Cross-KB Linking Is Thinking, Not Tooling

**KB:** Ching <-> Goldratt cross-links
**Phase:** Cross-KB Linking (Phase 5)

The workflow suggested running `pyrite links suggest` on high-importance entries. In practice, the suggest command was never used -- links were identified by reading entries in both KBs and reasoning about intellectual connections, then writing YAML by hand.

**Solution:** Phase 5 was rewritten as thinking-first, tooling-second. A connection taxonomy was added (same-subject, adaptation, simplification, continuation, critique, etc.). `pyrite links suggest` was demoted from primary workflow to optional supplement.

## Known Open Issues

These are pyrite core issues that affect the workflow but haven't been fixed yet:

1. **`list-entries` may have a default limit** that silently drops entries. Use `--limit 500` or count files on disk directly to verify totals.
2. **Resolved: custom-field persistence.** `pyrite create` persists declared custom fields supplied with `--field`; use schema-valid names and values. Importing a complete Markdown file with `pyrite add` remains an alternative.
3. **`qa gaps` may report core types instead of custom types** defined in kb.yaml, making the gaps report confusing.
