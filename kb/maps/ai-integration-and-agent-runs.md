---
id: map-ai-integration-and-agent-runs
title: "Which LLM providers, and who enforces a rule when another agent runs the work? Topic map: AI integration and agent runs"
type: note
tags:
- map
- design
- ai
- llm
- agents
- byok
- mcp
- acp
- runs
---

# Which LLM providers, and who enforces a rule when another agent runs the work?

Answers "which LLM providers, and where do keys live?", "how does an agent learn
what a type is for?", "who enforces a rule when a different agent runs the
work?". Layer 2 under [[design]] (P8, P9).

## The design today

1. The CLI, the MCP server and the web UI share one backend; an LLM abstraction
   serves all three. **decided** [[adr-0007]].
2. Providers: the Anthropic and OpenAI SDKs; OpenRouter, Ollama and any
   OpenAI-compatible endpoint through `base_url`; a stub for tests and offline.
   No LiteLLM (a 100 MB dependency). **decided** [[adr-0007]] section 2.
3. Bring your own key: AI is opt-in; with no key the features are hidden or say
   how to configure; keys stay on the server and never go to the browser;
   per-request token and cost are reported. **decided** [[adr-0007]] section 3.
4. A type carries `ai_instructions` and field descriptions; they flow into
   `kb_create` and `kb_schema` responses and the skills, and `kb.yaml` overrides
   a plugin's. **decided** [[adr-0009]] sections 1 to 4.
5. MCP exposes prompts (`research_topic`, `summarize_entry`, `find_connections`,
   `daily_briefing`) and resources. **decided** [[adr-0007]] section 5.
6. Agents work from a pull-based board: they pull when they have capacity;
   context is assembled at pull time; limits sit at the human review stage.
   **decided** [[adr-0019]]. See [[map-work-coordination-and-agent-teams]].
7. Agent reads are bounded and a truncated body is never writable. **decided**
   [[adr-0034]]. Embedding never blocks a write. **decided** [[adr-0035]].
8. The agent running the work is replaceable; Pyrite is an ACP client; run
   control is a backend service (`RunService`); agent tools reach Pyrite only
   through MCP, scoped per session; enforcement lives in the write path, never
   in the harness; methodology lives in the intent layer. **proposed** (open)
   [[adr-0030]]; Phase 0, a spike to verify adapter claims, has not started.
9. An MCP client over HTTP is a user or the operator, and over stdio the
   operator; the acting identity comes from the session. **proposed** ADR-0043
   decisions 2 and 7.
10. The thesis the roadmap states: agents write, humans verify; review attention
    is the constraint. [[adr-0019]]; roadmap.

## Invariants a test could check

- With no provider configured, every `/api/ai/*` endpoint answers 503 and no
  key reaches a browser response ([[adr-0007]]).
- Type metadata resolves kb.yaml first, then the plugin, then introspection,
  then core defaults ([[adr-0009]] section 3).
- Any gate must hold when a different harness runs the same work: it is a write
  path check, tested without the harness ([[adr-0030]] section 4).
- `auto_embed: false` touches no embedding stack ([[adr-0035]]).

## ADRs in reading order

[[adr-0007]], [[adr-0009]], [[adr-0019]], [[adr-0034]], [[adr-0035]];
[[adr-0030]] (proposed; read its Phase 0 and open questions); [[adr-0031]]
(draft; run execution as a capability). History only: [[adr-0003]]'s note that
AI-generated content is content-tier.

## Where the code starts

Components [[llm-service]], [[query-expansion-service]],
[[llm-rubric-evaluator]], [[mcp-server]], [[settings-service]]. Paths:
`pyrite/services/llm_service.py`, `pyrite/server/endpoints/ai_ep.py`,
`.claude-plugin/plugin.json`, `.claude/skills/`.

## Tests that pin it

`tests/test_llm_service.py`, `tests/test_ai_endpoints.py`,
`tests/test_mcp_prompts.py`, `tests/test_mcp_resources_session.py`,
`tests/test_llm_rubric_evaluator.py`.

## Known gaps

- [[adr-0007]] section 6 lists eight endpoints; four exist (`summarize`,
  `auto-tag`, `suggest-links`, `chat`), `status` lives elsewhere, and
  `/generate`, `/assist`, `/expand-query` were not built. Its skills tree is
  `.claude/skills/`, not `skills/`.
- No `RunService` or ACP code exists; the go or no-go on [[adr-0030]] is open.
- [[adr-0031]] is a draft; its capabilities idea (run execution as a grant)
  conflicts with ADR-0043, which makes egress the operator's (proposed).
- The alpha supported-surface entry (proposed) marks the AI endpoints and the
  Claude Code plugin experimental.
- Not decided: the `status:` name used by the [[adr-0028]] query operator, by
  [[adr-0020]]'s lanes and by a milestone's own status; and whether
  `task_checkpoint` moves to the read tier or every run needs the write tier,
  which blocks a phase gate ([[adr-0030]] open questions 1 and 6).
