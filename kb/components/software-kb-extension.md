---
id: software-kb-extension
title: Software KB Extension
type: component
tags:
- extension
- extension:software-kb
- software
kind: package
path: extensions/software-kb/
owner: markr
---

Pyrite extension for software project knowledge management. Provides entry types, MCP tools, CLI commands, validators, and workflows for managing the full development lifecycle.

## Entry Types

- **adr** — Architecture Decision Records with status lifecycle
- **design_doc** — Design documents and specifications
- **standard** — Coding standards and conventions
- **programmatic_validation** — Automated checks with pass/fail criteria
- **development_convention** — Judgment-based guidance for work sessions
- **component** — Module/service documentation with paths and dependencies
- **backlog_item** — Feature/bug/tech-debt tracking with kanban status flow
- **runbook** — How-to guides and operational procedures
- **milestone** — Project milestones for grouping backlog items
- **work_log** — Structured work session notes linked to backlog items

## MCP Tools (sw_*)

Read tier (also in write and admin): sw_adrs, sw_component, sw_standards, sw_validations, sw_conventions, sw_milestones, sw_backlog, sw_board, sw_pull_next, sw_review_queue, sw_context_for_item, sw_epics, sw_epic_detail, sw_check_ready, sw_refine

Write tier (also in admin): sw_claim, sw_submit, sw_review, sw_transition, sw_prioritize, sw_log, sw_create_adr, sw_create_backlog_item

## Workflows

- ADR lifecycle: proposed → accepted → deprecated/superseded
- Backlog kanban: proposed → accepted → in_progress → review → done
- Review loop: changes_requested cycles back to in_progress

## Relationships

implements/implemented_by, supersedes/superseded_by, documents/documented_by, depends_on/depended_on_by, tracks/tracked_by, blocks/blocked_by, session_for/has_session
