---
id: community-feature-request-system-via-pyrite-kb
title: Community Feature Request System via Pyrite KB
type: backlog_item
tags:
- community
- dogfooding
- feature
kind: feature
priority: low
effort: M
status: deferred
---

## Problem

Feature requests currently live in GitHub issues — flat, unstructured, no deduplication, no relationship tracking. Users can't see how their request relates to existing backlog items, ADRs, or design docs. Maintainers spend time triaging duplicates and asking for clarification. The request format varies wildly.

## Vision

A `pyrite-feature-ideas` KB that dogfoods Pyrite for community feature requests. Integrated alongside the demo site's KB viewer, it provides an AI-assisted workflow for submitting, researching, and developing feature requests — then contributes them via the fork/PR model.

## How it works

### User experience

1. User browses the demo site, explores curated KBs, reads docs
2. Clicks \"Request a Feature\" — opens the BYOK AI chat sidebar with a feature request prompt workflow
3. The AI agent searches existing KB entries (backlog items, ADRs, design docs, other feature requests) for related context and potential duplicates
4. Helps the user develop a well-structured request: problem statement, proposed solution, relationship to existing features, use cases
5. Produces a typed `feature_request` entry in the user's ephemeral sandbox KB
6. User reviews, refines, then clicks \"Submit\" — Pyrite forks the feature-ideas repo, commits the entry, opens a PR

### Maintainer experience

1. PR arrives in `pyrite-feature-ideas` repo with a properly structured entry
2. `pyrite ci` validates schema compliance
3. Maintainer reviews, may use AI chat to compare against existing backlog
4. Merge → entry appears in the public feature-ideas KB on the demo site
5. Accepted requests get linked to backlog items when work begins

### Entry type

```yaml
# In pyrite-feature-ideas kb.yaml
types:
  feature_request:
    description: \"Community feature request\"
    fields:
      problem:
        type: text
        required: true
        description: \"What problem does this solve?\"
      proposed_solution:
        type: text
        required: false
        description: \"How should it work? (optional — problem is more important)\"
      use_cases:
        type: list
        required: true
        description: \"Who benefits and how?\"
      related_entries:
        type: multi-ref
        required: false
        description: \"Links to existing backlog items, ADRs, or other requests\"
      status:
        type: select
        values: [submitted, under-review, accepted, planned, declined, duplicate]
      submitter:
        type: text
        required: false
        description: \"GitHub username\"
    ai_instructions: >
      Help the user articulate the problem clearly. Search existing backlog
      items, ADRs, and feature requests for duplicates or related work.
      Encourage problem-first thinking over solution-first. Link to relevant
      existing entries.
```

### Prompt workflow

A skill file that guides the AI through a collaborative research and development process:

1. **Landscape research**: Search the feature-ideas KB, pyrite backlog, ADRs, and design docs for related entries. Present the user with a map of the territory — "here's what already exists in this area"
2. **Deduplication and convergence**: If similar requests exist, show them with context. Ask "Is this the same thing, or different?" If similar, suggest the user comment on or refine the existing request rather than creating a duplicate. Surface areas of agreement and disagreement across related requests.
3. **Cross-pollination**: When multiple feature requests touch the same area, the AI highlights connections — "Three people have asked for variations of this. Here's where the ideas agree, here's where they differ." This helps users build on each other's thinking rather than starting from scratch.
4. **Development phase**: Help the user write the problem statement, use cases, and optionally a proposed solution. Encourage problem-first thinking — solutions are optional, problems are required.
5. **Linking phase**: Suggest links to related backlog items, ADRs, design docs, and other feature requests. Build the relationship graph as part of submission.
6. **Engagement routing**: When appropriate, suggest the user add a comment to an existing request instead of creating a new one. "This request from @user covers similar ground — would you like to add your use case as a comment there instead?"
7. **Validation**: Run QA validation on the draft entry before submission
8. **Submission**: Fork, commit, PR via [[personal-kb-repo-backing]] Flow B

### Comment and discussion model

Feature requests aren't just static entries — they accumulate community input:

- Users can add comments to existing requests (also via fork/PR — comments are linked entries of type `feature_comment` with a `parent_request` ref)
- The AI workflow surfaces comment threads when showing related requests
- Maintainers can see which requests have the most community engagement (comment count, unique commenters)
- When requests converge, the maintainer can merge them into a consolidated request that links back to the originals

This turns the feature-ideas KB into a living discussion space, not just a suggestion box.

## Why this matters

- **Dogfooding**: The feature request system IS a Pyrite KB — proves the platform works for structured community input
- **Quality**: AI-assisted research produces better requests than blank GitHub issue templates — users understand the landscape before contributing
- **Convergence**: Instead of 10 similar requests, the AI helps users find and build on each other's ideas
- **Discoverability**: Feature requests are a browsable, searchable, graph-connected KB — not a flat issue list
- **Contribution model**: Uses the same fork/PR workflow as any other curated KB contribution
- **Community signal**: The graph structure reveals which areas have the most community energy — maintainers can see clusters of related requests, not just individual items
- **Social proof**: The feature-ideas KB accumulates GitHub forks from contributors

## Prerequisites

- [[personal-kb-repo-backing]] — fork/PR contribution model (0.16)
- Demo site with BYOK AI chat (0.15)
- [[per-kb-permissions]] — ephemeral sandbox for drafting (0.14)
- A published `pyrite-feature-ideas` repo with the feature_request type schema

## Effort estimate

M — the infrastructure (fork/PR, BYOK AI, ephemeral KBs, QA validation) all ship by 0.16. The new work is the entry type schema, the prompt workflow skill file, and the UI integration (\"Request a Feature\" button + workflow).

## Related

- [[personal-kb-repo-backing]] — fork/PR contribution model
- [[demo-site-deployment]] — where this lives
- [[launch-web-presence]] — the three-layer web architecture
- [[pyrite-ci-command]] — validates submitted requests
