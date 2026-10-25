---
name: software-kb
description: "Use when working with a software-type Pyrite KB for development workflow: pulling work items, checking the board, claiming tasks, submitting for review, or managing backlog. Covers the full kanban cycle via sw_* MCP tools and CLI commands."
---

# Software KB Skill

Pull-based development workflow for software-type Pyrite knowledge bases. Covers the kanban cycle from backlog to done, using `sw_*` MCP tools (for agents) and `pyrite sw` CLI commands (for humans).

**Announce at start:** "I'm using the software-kb skill."

---

## The Development Loop

```
  Pull         Context       Claim         Work         Submit       Review
┌────────┐   ┌──────────┐  ┌──────────┐  ┌─────────┐  ┌──────────┐  ┌──────────┐
│ What's  │──>│ Gather   │──>│ Lock the │──>│ Do the  │──>│ Move to  │──>│ Approve  │
│ next?   │   │ context  │   │ item     │   │ work    │   │ review   │   │ or reject│
└────────┘   └──────────┘  └──────────┘  └─────────┘  └──────────┘  └──────────┘
sw_pull_next  sw_context    sw_claim       (you)        sw_submit     sw_review
              _for_item
```

### Status Flow

```
proposed → accepted → in_progress → review → done
   ↑          ↑           ↑                    │
   │          │           └── changes_requested─┘
   │          └── in_progress (unclaim)
   └── accepted (demote)
```

Backward transitions: `accepted → proposed`, `accepted → deferred`, `in_progress → accepted`.

Side exits: `proposed → wont_do`, `proposed → deferred → proposed`, `done → retired`.

---

## MCP Tools Quick Reference

### Read tier (safe, no side effects)

| Tool | Purpose | Key args |
|------|---------|----------|
| `sw_board` | Kanban board — all items grouped by lane | `kb_name` |
| `sw_pull_next` | Recommend highest-priority unblocked accepted item | `kb_name` |
| `sw_review_queue` | Items awaiting review, sorted by wait time | `kb_name` |
| `sw_context_for_item` | Full context bundle: linked ADRs, components, validations, conventions, reviews | `item_id`, `kb_name` |
| `sw_backlog` | List/filter backlog items | `kb_name`, `status`, `priority`, `kind` |
| `sw_adrs` | List ADRs | `kb_name`, `status` |
| `sw_milestones` | Milestones with completion stats | `kb_name` |
| `sw_component` | Find component docs | `kb_name`, `path`, `name` |
| `sw_validations` | Programmatic checks (pass/fail criteria) | `kb_name`, `category` |
| `sw_conventions` | Development conventions (judgment guidance) | `kb_name`, `category` |
| `sw_standards` | All standards (validations + conventions) | `kb_name`, `category` |
| `sw_check_ready` | Check DoR for a single item without claiming | `item_id`, `kb_name` |
| `sw_refine` | Scan backlog for DoR gaps, sorted by priority | `kb_name`, `status` |

### Write tier (mutates state)

| Tool | Purpose | Key args |
|------|---------|----------|
| `sw_claim` | Claim item: `accepted → in_progress` + set assignee (CAS) | `item_id`, `kb_name`, `assignee` |
| `sw_submit` | Submit for review: `in_progress → review` | `item_id`, `kb_name` |
| `sw_review` | Record review outcome: `review → done` or `review → in_progress` | `item_id`, `kb_name`, `outcome`, `reviewer`, `feedback` |
| `sw_create_adr` | Create auto-numbered ADR | `title`, `kb_name` |
| `sw_create_backlog_item` | Create backlog item | `title`, `kind` |
| `sw_transition` | Transition to any valid status (with gate check) | `item_id`, `kb_name`, `to_status`, `reason` |
| `sw_log` | Log work session: decisions, rejected approaches, open questions | `item_id`, `kb_name`, `summary` |

---

## CLI Equivalents

```bash
pyrite sw context-for-item <item-id> -k <kb>  # Full context bundle (ADRs, components, deps, reviews, work logs)
pyrite sw board -k <kb>                # Kanban board
pyrite sw backlog -k <kb>              # List backlog
pyrite sw backlog -k <kb> --status accepted  # Filtered
pyrite sw review-queue -k <kb>         # Review queue
pyrite sw claim <item-id> -k <kb> --assignee <name>
pyrite sw submit <item-id> -k <kb>     # Submit for review
pyrite sw transition <item-id> <status> -k <kb>  # Transition with gate check
pyrite sw check-ready <item-id> -k <kb>        # Check DoR without claiming
pyrite sw refine -k <kb>                       # Scan backlog for DoR gaps
pyrite sw adrs -k <kb>                 # List ADRs
pyrite sw new-adr "..." -k <kb>        # Create ADR (title is positional)
pyrite sw components -k <kb>           # Component docs
pyrite sw validations -k <kb>          # Validations
pyrite sw conventions -k <kb>          # Conventions
pyrite sw milestones -k <kb>           # Milestones
```

---

## Workflow Protocols

### Starting Work (Agent)

```
1. sw_pull_next(kb_name=...)           → get recommendation
2. sw_context_for_item(item_id, kb)    → read linked ADRs, validations, reviews
3. sw_claim(item_id, kb, assignee=...) → lock the item (CAS — fails if already claimed)
   ↳ Read the `gate` field in the response (Definition of Ready)
4. Address gate results:
   - checker failures → fix before proceeding (estimate effort, resolve blockers, decompose XL items)
   - judgment items → self-evaluate and fill gaps (add ## Acceptance Criteria, link to ADR, etc.)
   - If policy is "enforce" and checkers failed, the claim is rejected — fix and retry
5. Do the work
6. sw_submit(item_id, kb)              → move to review
```

If `sw_pull_next` returns `"reason": "WIP limit reached"`, finish existing in-progress work first.

If `sw_pull_next` returns blocked items, check if you can unblock them by completing their dependencies.

### Reviewing Work

```
1. sw_review_queue(kb_name=...)        → see what needs review
2. sw_context_for_item(item_id, kb)    → check prior reviews, linked validations
3. Evaluate the work against Definition of Done
4. sw_review(item_id, kb,
     outcome="approved"|"changes_requested",
     reviewer=...,
     feedback=...)                      → record outcome
```

- `approved` → transitions to `done` (triggers DoD gate evaluation — check `gate` in response)
- `changes_requested` → transitions back to `in_progress` (feedback required)
- Before approving, verify `agent_responsibility` items: tests passing, clean working tree, KB docs updated
- Prior review feedback is surfaced in `sw_context_for_item` so rework addresses specific issues
- `rework_count` in the review queue shows how many times an item has been sent back

### Logging Work Sessions

Record session context so the next agent doesn't repeat dead ends:

```
sw_log(item_id, kb_name, summary,
       decisions="...",       # what you chose and why
       rejected="...",        # what you tried and abandoned
       open_questions="...")   # what's unresolved for next session
```

The tool creates a `work_log` entry linked to the backlog item via `session_for` / `has_session`. These entries surface automatically in `sw_context_for_item`.

Log at the end of a work session, especially when:
- You made non-obvious design choices
- You tried an approach that didn't work
- You're handing off unfinished work

### Checking the Board

```
sw_board(kb_name=...)
```

Returns lanes with item counts and WIP limit status. Use this for situational awareness — where is work piling up?

### Quality Gates (DoR / DoD)

`sw_claim` and `sw_transition` return a `gate` field containing quality criteria evaluated at transition boundaries:

- **DoR (Definition of Ready)** fires at `sw_claim` / transition to `in_progress`
- **DoD (Definition of Done)** fires at transition to `done`

#### Criterion types

| Type | Meaning | How to handle |
|------|---------|---------------|
| `checker` | Programmatic check (pass/fail) | Fix the underlying issue — add missing fields, resolve blockers, estimate effort |
| `judgment` | Guidance for self-evaluation | Review the criterion and fill gaps yourself — add acceptance criteria, link to ADR, etc. |
| `agent_responsibility` | Agent must verify before proceeding | Confirm the condition holds — tests passing, clean working tree, docs updated |

#### Policy modes

- **`warn`** — gate results are advisory; transition proceeds even if checkers fail
- **`enforce`** — checker failures block the transition; fix issues and retry

Judgment and agent_responsibility criteria always pass structurally — they rely on the agent to self-evaluate honestly.

#### Example gate response

```json
{
  "gate": {
    "gate_type": "dor",
    "policy": "warn",
    "passed": false,
    "criteria": [
      {"name": "has_effort_estimate", "type": "checker", "passed": false, "message": "Missing effort estimate"},
      {"name": "has_acceptance_criteria", "type": "judgment", "passed": true, "message": "Item should have clear acceptance criteria"},
      {"name": "tests_passing", "type": "agent_responsibility", "passed": true, "message": "All tests should pass before starting"}
    ]
  }
}
```

#### The self-check loop

```
claim → read gate → fix checker failures → self-evaluate judgment items → proceed
```

If policy is `enforce` and a checker failed, the claim is rejected. Fix the issue (e.g., `pyrite update <id> -k <kb> -f effort=M`) and retry the claim.

### Backlog Refinement

Proactively prepare backlog items so they pass DoR before anyone claims them.

#### The refinement loop

```
1. pyrite sw refine -k <kb>              → scan for DoR gaps
2. For each item with gaps, highest priority first:
   a. Auto-fix what you can:
      - Add effort estimate based on scope analysis
      - Link to relevant ADRs/components
      - Add missing tags
      - Decompose XL items into subtasks
   b. Flag what needs human input:
      - Ambiguous requirements → add ## Open Questions to item body
      - Missing acceptance criteria needing product context → note it
      - Architectural questions → suggest creating an ADR
   c. pyrite sw check-ready <id> -k <kb>  → verify fixes
3. Transition ready items: proposed → accepted (if appropriate)
   - ALWAYS provide a descriptive reason that captures WHY the item is ready
   - Format: "Per refinement: <effort>, <risk>, <impact> (<rationale>)"
   - Example: "Per refinement: Small, low risk, high impact (unblocks backward transitions for blocked items)"
   - NEVER use generic reasons like "Ready per refinement" — the reason should help future readers understand the decision
```

#### What agents can fix autonomously

- Effort estimates (analyze scope, set S/M/L)
- Missing tags (infer from title/body)
- ADR/component links (search KB for related entries)
- Decomposition (create sub-items for XL work, link with blocked_by)

#### What needs human input

- Acceptance criteria requiring product/business context
- Priority decisions between competing items
- Architectural choices not covered by existing ADRs
- Scope questions ("should this include X?")

For these, add a `## Open Questions` section to the item body with specific asks.

---

## Backlog Item Fields

| Field | Values | Notes |
|-------|--------|-------|
| `kind` | `feature`, `bug`, `tech_debt`, `improvement`, `spike`, `epic`, `refactor`, `chore`, `process` | What type of work |
| `status` | `proposed`, `accepted`, `in_progress`, `review`, `done`, `wont_do` | Where in the flow |
| `priority` | `critical`, `high`, `medium`, `low` | Pull order |
| `effort` | `XS`, `S`, `M`, `L`, `XL` | T-shirt sizing |
| `assignee` | string | Set by `sw_claim` |

### Priority Semantics

- **critical** — blocking other work or production issue; pull immediately
- **high** — important for current milestone; pull before medium
- **medium** — default; pull in order
- **low** — nice-to-have; pull when nothing else is available

`sw_pull_next` ranks by priority then age (oldest first within same priority).

---

## Dependencies

Backlog items can have `blocked_by` / `blocks` relationships:

- `sw_pull_next` skips blocked items automatically
- `sw_claim` refuses to claim a blocked item
- `sw_context_for_item` shows dependency status (resolved or not)
- Blocked items appear in `sw_pull_next` response under `blocked_items`

To unblock work, complete the blocking item first.

---

## Linking to Context

Backlog items gain value from links to other entries:

| Link target | Why |
|-------------|-----|
| ADRs | Architectural decisions that constrain the work |
| Components | Code areas affected |
| Validations | Automated checks to run |
| Conventions | Style/approach guidance to follow |
| Milestones | Which milestone this contributes to |
| Work logs | Prior session decisions, rejected approaches, open questions |
| Other backlog items | `blocked_by` / `blocks` dependencies |

`sw_context_for_item` assembles all of these in one call. Use it before starting work — it's the single source of truth for what an item needs.

---

## Anti-patterns

| Don't | Do instead |
|-------|------------|
| Skip `sw_context_for_item` before working | Always gather context — prior reviews, linked validations, work logs, dependencies |
| Claim multiple items at once | Respect WIP limits; finish one before pulling another |
| Submit without running validations | Check `sw_context_for_item` for linked programmatic validations; run them |
| Review without feedback | Always provide `feedback`, even for approvals ("LGTM" is fine) |
| Ignore `rework_count` | Items sent back multiple times likely need a different approach |
| Create items without `kind` | Kind drives board grouping and reporting |
| Hand-edit YAML frontmatter for field changes | Use `pyrite update <id> -k <kb> -f field=value` — validates and syncs the index |
| End a session without logging context | Use `sw_log` to record decisions, rejected approaches, and open questions |
| Ignore `gate` results after claim/transition | Read every criterion; address checker failures and self-evaluate judgment items |
| Skip judgment criteria because they auto-pass | They're guidance — verify them yourself |
| Invent acceptance criteria without domain knowledge | Add `## Open Questions` asking the human for specifics |
| Rubber-stamp items as ready without checking | Run `pyrite sw check-ready` and address every gap |
| Use generic transition reasons ("Ready per refinement") | Write reasons that capture effort, risk, impact, and rationale for the decision |
