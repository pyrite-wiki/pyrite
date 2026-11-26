---
id: scrub-private-material-and-machine-paths-from-tracked-files
title: Scrub private material and machine paths from tracked files
type: backlog_item
tags:
- hygiene
- privacy
- public-repo
importance: 5
kind: task
status: done
priority: high
effort: S
rank: 0
---

## Problem

The secrets scan is clean (no credentials in tracked files or history), but the
owner's private work and machine layout leak into a public repo. This ticket
deliberately names **files, not contents**.

- Named subjects of private investigative work and request strategy:
  `kb/backlog/add-blocked-on-optional-field-to-task-schema.md` (lines ~22-36).
  `FEEDBACK.md`'s own rule says such subjects must be placeholders.
- A named private individual in
  `kb/backlog/done/review-flow-web-ui-shelved-superseded-by-google-drive-workflow.md`
  and `kb/backlog/done/full-suite-only-flaky-tests-state-leak-across-test-files.md`.
- Absolute home-directory paths in ~16 tracked files, including `FEEDBACK.md`
  (which also reveals private KB counts), `.claude/skills/pyrite-dev/SKILL.md`,
  `.claude/skills/extension-builder/SKILL.md`, two `kb/notes/` files, and about
  ten `kb/backlog/` files that reference private repositories and the owner's
  local agent-memory directory.
- `.claude/skills/pyrite-conductor/release-runbook.md` depends on a gitignored deploy
  script and the owner's sites: mark it maintainer-only.
- `.claude-plugin/plugin.json`: `"author": "markr"`; `plugin.md` still pitches
  "for citizen journalists", which no longer matches the README.

## Fix

Replace subjects and people with placeholders, home paths with `~/…` or
`<repo>`. This cleans the tip only; the content stays in git history. Whether to
rewrite history is the owner's call — decide it explicitly and record the
decision here.

## Acceptance

- [ ] `git grep -n "/Users/"` returns nothing outside fixtures.
- [ ] A grep test (or pre-commit hook) fails on a new absolute home path in a
      tracked file, so this does not recur.

Source: 2026-09-17 project review (three read-only audits: docs/contributor, public-repo, code-health).

## Status 2026-09-17: tip scrubbed; history rewrite evaluated and not recommended

**Scrubbed at the tip (18 files):** every absolute home path (now `~/…` or
`<repo>`); the named individual (now "the editor"); the investigation subjects,
request identifiers and outreach strategy in
`add-blocked-on-optional-field-to-task-schema.md` (now placeholders). `git grep`
for each returns nothing. `release-runbook.md` is marked maintainer-only.

**Deliberately left:** private repository *names* (names only, no contents);
the KB counts in `FEEDBACK.md` (they reveal nothing); `plugin.json` author.

**History rewrite — evaluated, not recommended:**
- The only material that is more than cosmetic is one passage in one file, added
  in one commit (`b446ee0`, 2026-04-23). It never reached `main`.
- It has been public for about five months, and one fork exists
  (the outside contributor's), which a rewrite cannot reach. GitHub also keeps
  unreachable commits addressable by SHA until support purges them.
- A rewrite changes every commit SHA after April: it breaks the fork's PR base,
  the SHAs cited throughout `kb/` and CHANGELOG, and any clone.
- What the passage exposes is *which records requests were planned* for subjects
  whose coverage is itself published. That is stale strategy, not a source, a
  credential, or a private person's data.

Revisit only if something of a different class turns up (a source's identity,
unpublished documents, credentials) — then rewrite and ask GitHub support to
purge cached views.

**Still open:** the recurrence guard (a test or hook that fails on a new absolute
home path in a tracked file).

## Closed 2026-09-20

Verified shipped during the 0.24.2 pre-release review: grep for machine home paths across pyrite/ returns nothing.
