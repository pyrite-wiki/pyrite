---
id: clarify-metadata-vs-top-level-field-mapping-in-mcp-create-update-tools
title: Clarify metadata vs top-level field mapping in create/update (CLI + MCP + REST)
type: backlog_item
tags:
- mcp
- agents
- dx
- schema
kind: improvement
status: done
priority: medium
effort: m
milestone: "0.13"
---

## Problem

The `kb_create` and `kb_update` tools (MCP) and `pyrite create` (CLI) accept some fields as top-level parameters (title, body, tags, importance, date, participants) and others via an arbitrary `metadata` dict (status, priority, effort, milestone, kind, etc.). The schema tool describes what fields a type has, but doesn't indicate which parameter they map to in the create/update interfaces.

An agent that reads the schema sees `status` as a field on `backlog_item`, but has to infer that it goes in `metadata` rather than as a top-level parameter. Agents without prior Pyrite experience (Claude Desktop, Codex, Gemini CLI, etc.) will get this wrong.

## Proposal

Options (not mutually exclusive):

1. **Schema tool/command enhancement**: Include a `parameter_mapping` in schema output that says which tool parameter each field maps to.
2. **Tool/command description enhancement**: Document in the MCP tool descriptions and CLI help text that type-specific fields go in `metadata`.
3. **Smart field routing** (recommended): Accept any known schema field as a top-level parameter in create/update and route it internally — so `kb_create(status="proposed")` and `pyrite create --status proposed` work the same as putting it in metadata. Unknown fields still go to metadata dict. Applied consistently across CLI, MCP, and REST API.

Option 3 is the most agent-friendly. It means any agent that reads the schema and passes fields by name will succeed, regardless of whether it understands the top-level vs. metadata distinction.

## Impact

Reduces failed or malformed create/update attempts. Especially important for agents without prior Pyrite experience — which is every agent the first time it encounters a Pyrite KB.
