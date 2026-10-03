---
id: adr-0030
type: adr
title: "Agent-Agnostic Run Execution — ACP as the Harness Boundary"
adr_number: 30
status: proposed
deciders: ["markr"]
date: "2026-09-17"
tags: [architecture, agents, acp, mcp, workflow, runs]
links:
- target: adr-0007
  relation: refines
  kb: pyrite
- target: adr-0006
  relation: refines
  kb: pyrite
- target: adr-0029
  relation: related
  kb: pyrite
- target: adr-0019
  relation: related
  kb: pyrite
- target: adr-0021
  relation: related
  kb: pyrite
- target: intent-layer-guidelines-and-goals
  relation: related
  kb: pyrite
- target: notifications-condition-ledger
  relation: related
  kb: pyrite
---

# ADR-0030: Agent-Agnostic Run Execution — ACP as the Harness Boundary

> **Deferred by the maintainer, 2026-10-03.** Agent run execution is in no release. Phase 0 (the spike) has not
> started and no `RunService` or ACP adapter exists. The status stays `proposed`:
> `deferred` is not an allowed ADR status, and `proposed` is the nearest value
> (not decided to build, not rejected). Revisit when run execution is scheduled.

## Context

Pipeline work today runs inside Claude Code. The investigation-conductor,
draft-conductor, and capture-routines fleet are skills; session control is
the Claude Agent SDK or the CLI; and the natural place to put deterministic
gates — `PreToolUse` hooks, an in-process `can_use_tool` callback, in-process
Python `@tool` servers — is Claude-specific machinery.

Three pressures converge:

1. **Other agents.** Operators may run Gemini CLI or Codex, by preference, by
   cost, or because a subscription they already hold is the only economics
   that works for them. The BHAG
   ([[bhag-self-configuring-knowledge-infrastructure]]) already names
   "OpenClaw, Claude Code, Codex, custom orchestrators" as the agent runtimes
   Pyrite serves. Nothing in the current execution path honours that.

2. **A non-terminal surface.** Running, observing, interrupting, and
   reviewing pipeline work from a local UI requires run control to exist
   somewhere other than a terminal session — which, under ADR-0007, means it
   must exist as a backend service rather than as UI code.

3. **Gate placement.** Any rule enforced in a harness hook is unenforceable
   the moment a different harness runs the same work. A gate that Gemini can
   walk around is not a gate; it is documentation with an exit code.

### Problem

Three concerns are currently conflated:

- **Who executes** a run (which vendor's agent loop).
- **Where the methodology lives** (currently: Claude Code skills, commands,
  and hooks).
- **Where correctness is enforced** (currently: undecided, and drifting
  toward the harness because that is where the ergonomics are best).

Collapsing these means every vendor change is an enforcement change, and a
local UI that manages its own sessions becomes a second orchestrator running
beside the conductors — the dual-registry failure of ADR-0029 rebuilt at the
execution layer.

### Alternatives considered

1. **Claude Agent SDK as the interface.** Best ergonomics by a wide margin:
   hooks at every lifecycle point, in-process Python tools with no subprocess
   or serialization cost, a `can_use_tool` callback that can drive arbitrary
   approval logic, resumable sessions. Rejected *as the interface* because
   all of that is vendor-specific: gates implemented there bind only Claude,
   and making the harness the enforcement layer is precisely the coupling
   this ADR exists to prevent. Retained as one adapter behind the boundary,
   and as the right tool for Claude-specific experiments.

2. **Per-vendor CLI subprocess adapters over a Pyrite-defined protocol.**
   Rejected: we would write, version, and maintain three adapters plus a
   protocol only we speak, against three CLIs whose flags and event shapes
   change independently. ACP already occupies this seam.

3. **A third-party unifying wrapper SDK** (the `headless-coder-sdk` family and
   similar). Rejected: Node/TypeScript in a Python backend, an uncontrolled
   dependency between us and three vendors, and it abstracts the *agent loop*
   rather than the *client/agent boundary* — the wrong seam for a system that
   already owns its orchestration in the task graph.

4. **Status quo, Claude Code only.** Rejected: it forecloses the BHAG's
   stated agent-runtime plurality, and it puts the UI's run control in a
   place no other surface can reach.

### Why ACP, specifically

ACP is JSON-RPC 2.0 over stdio: the client spawns the agent as a subprocess
and they exchange newline-delimited messages. Adapters for the three agents
we care about are maintained by parties other than us
(`@zed-industries/claude-code-acp`, built on the official Claude Agent SDK;
`@zed-industries/codex-acp`; native ACP support in Gemini CLI), and each
supports the features this decision depends on: tool calls with permission
requests, edit review, and **client-provided MCP servers**.

The LSP analogy holds and is the reason to prefer it over anything we would
write: implement the client once, and every future agent works without
integration code.

**Verification owed before Phase 1 starts.** The claims above — protocol
version, adapter maturity, and in particular whether each adapter honours
client-provided MCP server configs at `session/new` — are external and were
not verified against running software when this ADR was written. §3 and §8
both depend on that specific feature working uniformly across all three
adapters. Phase 0 exists to check it.

## Decision

**ACP is the harness boundary. Pyrite is an ACP client. The agent is
replaceable; the gates are not in it.**

### 1. Run execution is a backend service

A `RunService` lives in `pyrite/services/`, alongside `task_service` and
`search_service`. It is reachable from `pyrite run` (CLI), `/api/runs/*`
(REST), and — in a later phase — MCP. Per ADR-0007, no surface implements run
execution itself; the local pipeline UI is a client of this service, not a
second runtime.

### 2. Agents attach over ACP, and their invocations are configuration

Agent profiles are declared in library settings, not in code:

```yaml
# ~/.pyrite/libraries/journalism.yaml
agents:
  claude:
    command: [npx, "@zed-industries/claude-code-acp"]
    env: [ANTHROPIC_API_KEY]
  codex:
    command: [codex-acp]
    env: [OPENAI_API_KEY]
  gemini:
    command: [...]        # adapter-version-specific; see runbook
```

Adapter invocations, auth modes, and flags change on the vendors' release
cadence, not ours. They belong in config with a runbook, never compiled in.

Client-side methods Pyrite must implement: `fs/read_text_file`,
`fs/write_text_file`, `terminal/create`, `session/request_permission`, and
consumption of the `session/update` stream.

Routing agent filesystem access through our client (rather than letting the
agent touch disk directly) is deliberate: it is what makes edit review a
uniform surface across vendors, and it is where the review-queue integration
of ADR-0019 attaches.

### 3. Tools reach Pyrite only through MCP, scoped per session

ACP passes client-provided MCP server configs at `session/new`. Every run
therefore spawns a Pyrite MCP server configured for that run:

- **Tier** (ADR-0006) is selected **per session**, not per deployment. A
  research stage gets read tier; a drafting stage gets write tier; an
  editorial stage gets write tier against a different library scope.
- **Library** (ADR-0029) is passed per session. A run structurally cannot see
  KBs outside its library, before tier is consulted.

**Implementation note — per-session tier means per-session process.** Tier is
currently baked into server identity at construction (`mcp_server.py:150`,
`sdk = Server(f"pyrite-{self.tier}")`) and servers are cached per tier
(`mcp_routes.py:154`). There is no way to vary tier on an existing server, so
each run spawns its own MCP server process. That is compatible with ACP's
model but it compounds the subprocess cost recorded in Consequences: one
adapter process *and* one MCP server process per concurrent run. Library
scoping is cheaper — `PyriteMCPServer(config=...)` already takes a config, so
a per-session library-scoped KB list is a natural fit.

**Corollary:** in-process SDK MCP servers are not used for pipeline runs.
They remain available for Claude-specific tooling experiments, but no
pipeline capability may exist only there.

### 4. Enforcement lives in the write path, never in the harness

**No gate may be implemented as an agent hook.** Any rule that must hold is
implemented in the write path.

**What exists today, and can carry a gate now:**

- schema validation on the entry type,
- MCP tier and library scoping,
- `after_save` hooks (`kb_service.py:296`), which run for every write that
  goes through `KBService`.

**What this ADR's §4 claim depends on but does NOT yet exist:**

- **`sw_validate`** — named in ADR-0019 as the automated gate that must clear
  before human review. It is prose in that ADR and was never implemented;
  twelve `sw_*` tools ship, and this is not one of them. Until it exists,
  "run all relevant `programmatic_validations`" is not a mechanism.
- **QA assessment on write** — `QAService.assess_entry` exists but is not
  wired into the save path; nothing in `document_manager` invokes it. QA is
  currently something a caller chooses to run, not something a write passes
  through.
- **The validated promote path** for ephemeral→durable moves (ADR-0029 §3).
  ADR-0029 is accepted, but no `promote` / `graduate` / `reap` implementation
  exists in `pyrite/`.

This matters because §4 is the load-bearing clause: it is what makes the gates
vendor-independent *by construction*. Three of the five mechanisms it names
are designs, not code. **Phase 2 cannot close until at least `sw_validate`
and write-path QA exist**, because before then "the write path enforces it"
is an aspiration and the honest enforcement surface is schema validation plus
tier scoping.

The ADR-0019 `programmatic_validation` / `development_convention` split
remains the intended boundary: validations are enforced identically for every
vendor; conventions are carried as context at pull time and judged by a human
at review. Human review capacity remains the system constraint, unchanged.

### 5. Methodology moves to the intent layer

Claude Code skills do not port. The methodology currently encoded in
`research-flow`, `investigation`, and the conductor skills moves to
agent-neutral carriers:

- **KB and type-level guidelines, goals, and evaluation rubrics**
  ([[intent-layer-guidelines-and-goals]]).
- **MCP prompts** (ADR-0007 §5) — available to any MCP-capable client.
- **Context assembly at pull time** (`sw_context_for_item`, ADR-0019).

**Status check:** the intent layer is a `draft` design doc, not shipped
machinery. `sw_context_for_item` exists; guidelines/goals/rubrics as a
first-class, `kb_orient`-surfaced layer do not. §5 is therefore a migration
onto a substrate that must be built first — which is why it is Phase 4 and
why Consequences calls it the real cost of this ADR.

`.claude-plugin/` retains only Claude-specific ergonomics (slash commands,
hook-based conveniences, anything genuinely about the harness).

**Acceptance test for this clause:** the same task, run by Claude, Codex, and
Gemini, produces entries that pass the same QA assessment at comparable
rates. If it does not, the methodology has not finished moving.

### 6. Checkpoints are the progress spine; the ACP stream is display

`task checkpoint` is an MCP tool call, so every agent can emit it, and under
ADR-0029 it doubles as the lease heartbeat. Run progress is read from
checkpoints: durable, cross-surface, restart-safe, already carrying
confidence and evidence (`task_service.checkpoint_task`, with `message`,
`confidence`, `partial_evidence`).

**Unresolved: `task_checkpoint` and `task_claim` are both WRITE tier.** A
read-tier research run — which §3 gives as the motivating example of
per-session tier — structurally cannot claim a task or emit a checkpoint. The
progress spine and the tier model contradict each other as specified. Three
ways out, none chosen here:

- move `task_checkpoint` (and possibly `task_claim`) to read tier, on the
  grounds that they write task *machinery*, not KB content;
- add a fourth tier, or a capability flag orthogonal to tier;
- accept that every run needs write tier, and drop the read-tier run as a
  concept — which would remove much of §3's motivation.

**This must be decided before Phase 2**, since Phase 2's gate is "a read-tier
run demonstrably cannot write."

The ACP `session/update` stream (message chunks, tool calls, plans, token
usage) is **ephemeral display detail**, delivered to the UI over WebSocket
and never persisted as truth.

Run status surfaces through the condition ledger as `kind: task`
([[notifications-condition-ledger]]) — itself a `draft` design with no
implementation, so Phase 2's ledger wiring depends on that being built.
Per that design's explicit instruction, no second job-tracking system is
built. The anti-drift rule applies unchanged: derive, coalesce, self-resolve.

### 7. Run identity, memory, and session state

- A **run is a claimed task**. Claiming is the existing atomic CAS; the run
  does not introduce a parallel queue.
- **Run memory is an ephemeral KB** leased to that task (ADR-0029 §3, `eph-`
  prefix), with the three declared exits: reap, promote, graduate. A crashed
  run's memory stays inspectable until the lease reaper takes it. *(Depends
  on ADR-0029's lifecycle being implemented — see §4.)*
- The **ACP session id** is runtime state (row-shaped, in a declared state
  table per ADR-0029 §4), not KB content, and not a file. It exists to
  support resume.
- **ACP plans and TODO lists are display-only.** They are within-session
  scratch. Mirroring them into task entries would create a second task graph
  with none of the claim, lease, or rollup semantics.

### 8. Permissions

`session/request_permission` surfaces to the operator. Approval is per-action
and per-session and is never generalized to later actions.

Vendor approval and sandbox models differ (Codex exposes sandbox and
approval-policy modes; Claude Code exposes permission modes) and are **not**
unified into a single Pyrite setting — they are exposed as per-agent
configuration. The boundary that actually holds is the write path (§4), which
is identical across vendors.

**Unattended runs must be configured such that no permission grant an agent
could request would authorize something the write path would reject.** If a
run needs an approval the write path would not grant, it belongs in the
review queue, not in an autonomous tick.

Note that this clause inherits §4's gap: it is only as strong as the write
path is, and the write path is currently thinner than §4 describes.

### Out of scope

- **The local pipeline UI.** Surface 3 work under ADR-0007; needs no ADR of
  its own once this service exists.
- **Pyrite as an ACP *agent*** (driving Pyrite research from inside Zed or
  JetBrains). Interesting, but a fourth surface requiring its own decision
  against ADR-0007.
- **Which model each pipeline should use.** Operator configuration, not
  architecture.

## Phasing

| Phase | Deliverable | Gate to next |
|-------|-------------|--------------|
| 0 | **Spike:** one ACP adapter driven end-to-end against a throwaway client; confirm client-provided MCP servers, permission requests, and `fs/*` round-trip as documented. Timeboxed. | The protocol behaves as §2/§3 assume, or this ADR is revised before any service is written. |
| 1 | `RunService` + ACP client + Claude adapter only. `pyrite run <task-id>`. No UI. | A conductor tick runs end-to-end through the service with parity to the current skill path. |
| 2 | Per-session MCP tier + library scoping. §6 tier contradiction resolved. `sw_validate` + write-path QA exist. Checkpoints wired to the condition ledger. | A read-tier run demonstrably cannot write; a run cannot see out-of-library KBs; a validation failure blocks a write identically for every adapter. |
| 3 | Codex and Gemini adapters + parity harness: same task, three agents, compared on QA pass rate and validation failures. | Parity harness runs on a schedule (not per-PR — see below). |
| 4 | Intent layer built; conductor methodology migrated onto it; `.claude-plugin/` reduced to ergonomics. | §5 acceptance test passes. |
| 5 | Surface 3: local pipeline UI against `/api/runs/*`. | — |

Phases 0–2 are worth doing even if we never ship a second adapter: they move
run control out of the harness and make tier scoping real.

**Phase 3's harness must not run per-PR.** It spends money at three vendors
per run and its failures are quality judgments rather than pass/fail.
Nightly, or on adapter-version bumps. A recent precedent: two CI failures
this month were environmental (plugin discovery order differing between
machines), not quality regressions — a parity harness has the same hazard
class, and parity failures need triage into *vendor capability* vs
*environment* before anyone concludes a model is worse.

## Consequences

### Positive

- One integration serves every current and future ACP agent; vendors maintain
  their own adapters.
- Gates become vendor-independent by construction — *once §4's mechanisms
  exist*. The question "can Gemini bypass this?" gets a structural answer
  instead of a per-rule one.
- Per-session tier and library close the gap where a single deployment-wide
  write-tier server served every agent regardless of stage.
- Run control becomes available to CLI, REST, MCP, and UI simultaneously,
  rather than being a property of whoever holds the terminal.
- The methodology migration is valuable on its own terms: a rubric in the
  schema binds every agent, including future ones, and is reviewable as a
  diff.

### Negative

- **This ADR has more prerequisites than deliverables.** `sw_validate`,
  write-path QA, ADR-0029's promote/reap lifecycle, the intent layer, and
  the condition ledger are all designs rather than code, and §§4–7 lean on
  all five. The honest cost is those five plus the ACP client, not the
  client alone.
- **We lose the Claude SDK's best affordances for pipeline runs**: lifecycle
  hooks, in-process Python tools, programmable `can_use_tool`. Fine-grained
  per-tool telemetry is no longer uniform; the checkpoint spine is coarser
  than hook spans would be.
- **Subprocess sprawl**: an ACP adapter process plus an MCP server process
  per concurrent run, and the adapters are Node/Rust binaries in an otherwise
  Python deployment. Node becomes a runtime dependency for the Claude and
  Codex paths.
- **The methodology migration (§5) is the real cost**, larger than the client
  implementation, and it determines output quality rather than plumbing
  correctness.
- **Three vendors' release cadences** now affect us at the config and runbook
  layer. Adapter feature parity is not guaranteed and will drift.
- A vendor's agent may produce lower-quality entries; the parity harness makes
  this visible but does not fix it.

### Neutral

- The Claude Agent SDK remains in the dependency tree via the Claude ACP
  adapter; we simply stop programming against it directly for runs.
- Existing conductor skills keep working during Phases 0–3; this is additive
  until Phase 4 removes their methodology content.

### Migration

- No data migration. Runs are claimed tasks; the task model, claim CAS, and
  lease semantics are unchanged.
- ADR-0006's per-deployment tier default remains for interactive Claude Code
  use; only run execution moves to per-session tier.
- Conductor skills migrate methodology to the intent layer incrementally, one
  skill at a time, each validated by the §5 acceptance test before the skill's
  copy is removed.

## Open questions for reviewers

1. **`status:` collision — needs deciding before the UI, not after.** The
   ADR-0028 DSL exposes `status:` as the entry status field; ADR-0020 makes
   lanes a *mapping over* task statuses (`lanes: [{statuses: [proposed,
   planned]}, ...]`); `milestone` also has a `status` (open/closed). That is
   one word doing at least three jobs, one layer apart, about to be surfaced
   side by side in a pipeline UI. Cheapest fix is probably renaming the
   ADR-0028 query operator, as the newest with the fewest dependents.
2. **Where does run telemetry live?** §6 says checkpoints plus the condition
   ledger. Is anything beyond that (per-tool timings, token spend per stage)
   worth a declared state table, or does it stay derived-and-discarded?
3. **Library scoping vs. a multi-pipeline dashboard.** ADR-0029 makes a
   library self-contained and closed, and a deployment serve one. A dashboard
   spanning research, drafting, and editorial either crosses that line or
   forces those pipelines into one contention domain. Which?
4. **Is `fs/*` routed through the client, or do we let agents touch disk?**
   §2 says routed, for uniform edit review. Real latency and complexity cost
   on large repos. Recommend keeping it routed and revisiting only on
   measurement.
5. **Ephemeral KB per run vs. per parent task.** ADR-0029 allows both. For
   multi-agent pipeline stages, which is the default?
6. **Does the §6 tier contradiction get fixed by moving `task_checkpoint` to
   read tier, or by admitting every run needs write tier?** This is the one
   open question that blocks a phase gate rather than a design detail.
