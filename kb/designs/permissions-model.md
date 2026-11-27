---
id: permissions-model
title: "Design Sketch: Three-Layer Permissions Model"
type: design_doc
status: draft
tags:
- permissions
- security
- corporate
- design-sketch
links:
- launch-plan
- roadmap
- bhag-self-configuring-knowledge-infrastructure
lifecycle: archived
---

> Superseded by [[adr-0043]] and [[who-can-do-what]].

# Design Sketch: Three-Layer Permissions Model

Status: **brainstorm** — not an ADR yet. Captures the thinking so far for future design work.

## Context

Corporate KB adoption requires fine-grained permissions: "who can edit runbooks" and "who can approve ADRs." Pyrite's git-native philosophy means git is the collaboration and permission layer. The question is where git's model ends and where Pyrite needs to add application-level controls.

## The Three Layers

### Layer 1: Git Permissions (today, working)

Git provides the foundation. This is the layer that gates changes to the **source of truth** — the markdown files in the repository.

| Mechanism | What It Controls | Enforcement |
|-----------|-----------------|-------------|
| Repository access | Who can see the KB at all | GitHub/GitLab repo permissions |
| Branch protection | Who can push to main (the canonical branch) | GitHub branch protection rules |
| CODEOWNERS | Who must review changes to specific paths/folders | GitHub PR review requirements |
| PR review | Who can approve proposed changes | GitHub review + merge permissions |

**Key insight:** PRs are the easy way for anyone to suggest edits to the source of truth. You don't need write access to contribute — you fork, edit, submit a PR. The review process is where trust is established. This is a feature, not a limitation.

**Folder-level permissions via CODEOWNERS example:**
```
# Only the architecture team can modify ADRs
kb/adrs/ @myorg/architecture-team

# Runbooks require SRE review
kb/runbooks/ @myorg/sre-team

# Anyone can modify their own team's docs
kb/teams/frontend/ @myorg/frontend-team
kb/teams/backend/ @myorg/backend-team
```

This already covers many corporate permission needs without any Pyrite-specific code.

### Layer 2: MCP/API Tier Permissions (today, working)

Pyrite's three-tier permission model controls what actions are available through programmatic interfaces (MCP, REST API, CLI with API keys).

| Tier | Access | Use Case |
|------|--------|----------|
| **read** | Search, browse, retrieve, schema discovery | Untrusted agents, research assistants, public demo site |
| **write** | Create, update, delete, link entries | Trusted agents, team contributors |
| **admin** | Index management, KB provisioning, schema changes, git operations | Orchestrators, human operators |

This gates who can do what **through Pyrite**, but doesn't affect anyone who edits files directly in git (which is fine — that's what layer 1 is for).

### Layer 3: Application-Layer Fine-Grained Permissions (future)

This is the layer that doesn't exist yet. It would enforce type-level, field-level, or workflow-level permissions within Pyrite's interfaces.

**Important constraint:** Layer 3 only works for people and agents going through Pyrite (CLI, MCP, REST API, Web UI). Anyone who edits markdown directly in git bypasses it. This is the same class of problem as someone pushing to main and bypassing CI — the answer is branch protection (layer 1), not making the application layer unbypassable.

## Layer 3: Design Directions (brainstorm, not decided)

### Direction A: Schema-Driven Type Permissions

Extend `kb.yaml` to define who can do what to which entry types:

```yaml
# Hypothetical — not designed yet
permissions:
  roles:
    contributor:
      can_create: [note, clipping, bookmark]
      can_edit: [note, clipping, bookmark]
    architect:
      can_create: [adr, component, standard]
      can_edit: [adr, component, standard]
      can_approve: [adr]
    qa_agent:
      can_create: [qa_assessment]
      can_edit: [qa_assessment]
      can_validate: all
```

**Pros:** Knowledge-as-Code — permissions are versioned in git alongside the schema. Easy to understand. Natural extension of the existing schema-as-config philosophy.

**Cons:** Role management is a whole problem (how do you map users/agents to roles?). Doesn't handle field-level granularity.

### Direction B: Workflow-Layer Enforcement

Instead of static permissions on types, the workflow state machine gates transitions:

```yaml
# Hypothetical — not designed yet
workflows:
  adr_lifecycle:
    states: [draft, proposed, review, accepted, deprecated]
    transitions:
      draft -> proposed:
        allowed_roles: [architect, contributor]
      proposed -> review:
        allowed_roles: [architect]
      review -> accepted:
        allowed_roles: [architecture_lead]
        requires: [two_approvals]
```

**Pros:** Matches how real teams work — permissions are about what you can do, not what you can see. The task/coordination plugin already has a workflow state machine. Natural extension of existing patterns.

**Cons:** More complex to configure. Not all permission needs are workflow-shaped (sometimes you just want "interns can't delete things").

### Direction C: MCP Tier Scoping

Extend the existing tier model with scopes:

```yaml
# Hypothetical — not designed yet
api_keys:
  research_agent:
    tier: write
    scope:
      kbs: [research-kb]
      types: [note, source, finding]
  qa_agent:
    tier: write
    scope:
      kbs: all
      types: [qa_assessment]
      actions: [create, validate]
  human_operator:
    tier: admin
    scope: all
```

**Pros:** Incremental extension of existing tier model. No new abstractions. Scoped API keys are a well-understood pattern.

**Cons:** Doesn't handle workflow-level transitions. Scopes can get complex quickly.

### Direction D: Git Branch Model (most git-native)

Everyone works on branches. Publishing requires a PR. The PR process is where permissions live:

- Draft entries live on feature branches (anyone can create)
- Publishing = PR to main (requires review from appropriate CODEOWNERS)
- QA agent runs as a PR check (like CI)
- Schema validation runs as a PR check
- Merge requires passing checks + reviewer approval

**Pros:** No new permission system needed — uses git's model entirely. Full audit trail. Familiar to developers.

**Cons:** Requires everyone to understand git branching. Non-developers (corporate KB users) may struggle. High friction for simple edits.

## Likely Approach

Probably a combination. The layers are complementary, not competing:

- **Layer 1 (git)** handles coarse access: who can see the KB, who can merge to main, who reviews what paths
- **Layer 2 (MCP tiers)** handles programmatic access: untrusted agents get read, trusted agents get write, orchestrators get admin
- **Layer 3 (future)** probably combines **Direction A** (type permissions in schema) with **Direction B** (workflow gating) — schema defines who can create/edit which types, workflow state machine gates transitions like approval

The API-key scoping from Direction C is probably a quick win that could ship earlier than the full type/workflow permission system.

## Enforcement Points

Wherever Layer 3 is enforced, it needs to be consistent across all three portals:

| Portal | Enforcement Point |
|--------|------------------|
| CLI | Service layer (before storage write) |
| MCP | MCP tool handler (before calling service) |
| REST API | Endpoint handler (before calling service) |
| Web UI | API call (enforced by REST API) |

Best approach: enforce in the **service layer** so all portals get the same behavior. The service layer already enforces schema validation — permission checks would sit alongside it.

## Relationship to Layer 1

Layer 3 is a **convenience layer**, not a security boundary. The security boundary is git (layer 1). Layer 3 prevents mistakes and enforces workflow — it's closer to a linter than a firewall. If someone bypasses Pyrite and edits files directly:

- Branch protection prevents unauthorized merges (layer 1)
- CODEOWNERS requires appropriate reviewers (layer 1)
- QA validation catches structural issues in PR checks (layer 1 + Pyrite CI integration)
- Schema validation catches type/field violations in PR checks (layer 1 + Pyrite CI integration)

The right framing for corporate teams: "Git is your access control. Pyrite is your quality control. Together they enforce both who can change what and what changes are valid."

## Open Questions

- How do users/agents map to roles? API key → role? Git identity → role? Separate role config?
- Should permissions be per-KB or global? (Probably per-KB, defined in `kb.yaml`)
- How does this interact with the coordination/task plugin? (Tasks probably have their own permission layer — who can claim, who can approve)
- What's the minimum viable layer 3 that unblocks corporate adoption? (Probably: type-level create/edit permissions + workflow transition gating)
- Should Pyrite ship a `pyrite ci` command that runs as a GitHub Action / PR check? (This would enforce schema + QA validation at the git layer without needing layer 3)

## Timeline

**Layer 2 extensions** are now concretely scheduled: OAuth providers (#110) in 0.14, per-KB ACL with ephemeral sandboxes (#112) in 0.14, personal KB repo backing with fork/PR workflow (#113) in 0.16. These implement the per-KB permission resolution chain and the demo site user funnel described above.

**Layer 3** (application-layer fine-grained permissions) remains unscheduled — probably post-launch alongside other agent swarm infrastructure. The launch story for corporate teams is: "Git permissions today, per-KB ACL today, application-level type/workflow permissions on the roadmap."

A quick win shipping in 0.16: **`pyrite ci` command** (#86) — a single CLI command that validates schema, runs QA checks, and exits non-zero on failure. Teams add it to their GitHub Actions. This gives corporate teams enforcement at the git layer (PR checks) without building a full application-level permission system.
