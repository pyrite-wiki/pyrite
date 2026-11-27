---
id: contributor-docs-pass-contributing-security-pr-template-credits
title: 'Contributor docs pass: CONTRIBUTING, SECURITY, PR template, credits'
type: backlog_item
tags:
- docs
- contributor-experience
importance: 5
kind: improvement
status: done
priority: high
effort: S
rank: 0
---

## Problem

Pyrite has an outside contributor and real users; the contributor-facing docs
still describe a solo project.

**CONTRIBUTING.md**
- Line 23 says `uv pip install -e ".[dev]"`. That is test tooling only (no
  typer, bcrypt, fastapi) — the same install that failed 70 test modules at
  collection in CI. Should be `.[all]`, plus the extensions loop.
- Never explains the branch model (branch from `dev`, PR against `dev`, `main`
  is releases only — ADR-0025). PRs land correctly only because `dev` is the
  GitHub default.
- Says only `pre-commit install`. Document the stages (commit = fast checks,
  commit-msg = `fix:` needs tests, pre-push = full suite, CI = the gate); see
  [[fast-commit-hooks-full-suite-at-pre-push-ci-is-the-gate]]. The `--no-verify`
  guidance currently lives only in CLAUDE.md.
- "Check existing issues" — there are none. Explain `pyrite sw backlog` and
  `kb/backlog/`, and mirror 5-10 starter items as GitHub issues labelled
  `good first issue`.

**SECURITY.md / CODE_OF_CONDUCT.md**
- SECURITY.md sends reporters to GitHub private vulnerability reporting, which
  is **disabled** on the repo (`gh api …/private-vulnerability-reporting` →
  `enabled:false`), and gives no email. Enable it (repo setting — owner action)
  and/or add a contact address.
- Supported versions say 0.20.x; should be 0.24.x.
- CODE_OF_CONDUCT line 38: "reported to the project maintainers", no contact.

**PR template** says `pytest`; add the extensions command and the
fix-needs-a-test rule. All three outside PRs were fixes with no tests.

**Credit** AsyncLegs in README/CONTRIBUTING (already in CHANGELOG).

**Process:** the three outside PRs sat 7-8 days with one comment between them.
Decide a response-time intent and write it down. `dev` has no branch
protection; `main` requires only `test (3.12)`.

**README facts not covered by [[docs-counts-generated-or-asserted-from-code]]:**
line 171 lifecycle hooks are registered through `get_hooks()` (and
`before_index` is a fifth); line 210 `schema.py` is now the `pyrite/schema/`
package; line 222 services list is ~40, not the handful shown; line 226 lists a
`task` extension that does not exist and omits `journalism-investigation`.

**Quick Start friction** (the walk itself works): first `--mode=semantic` run
silently downloads the embedding model — say so; the MCP JSON uses
`"command": "pyrite"`, which fails when the venv is not on Claude Desktop's
PATH — suggest an absolute path.

## Acceptance

- [ ] A fresh clone following CONTRIBUTING verbatim gets a green `pytest`.
- [ ] SECURITY.md names a channel that is actually switched on.

Source: 2026-09-17 project review (three read-only audits: docs/contributor, public-repo, code-health).

## Status 2026-09-17

**Done:** CONTRIBUTING (install, branch flow per ADR-0032, hook stages, PR
steps, one-week first-response intent, where work is tracked per ADR-0033,
private-material rule, credits); PR template (`Fixes #N`, failing-test rule,
extensions command); issue templates + `config.yml` (version, install path,
interface; security routed to the private form); CODE_OF_CONDUCT contact;
SECURITY.md versions; README facts listed above, install section (git-tag
install + no-web-UI caveat), MCP absolute path, model-download note,
contributors; `docs/configuration.md` (new: every `PYRITE_*` variable);
docs/getting-started.md model-download note and MCP path. Seven
`good first issue` items exist (#12, #16–#21).

**Still open, owner action:** enable GitHub private vulnerability reporting
(repo Settings → Security). Until then the security links in SECURITY.md and
the issue-template `config.yml` point at a form that returns 404. This is the
only unmet acceptance criterion.

## Closed 2026-09-20

Verified shipped during the 0.24.2 pre-release review: CONTRIBUTING, SECURITY.md, the PR template and the credits section all exist; PR #214 and #216 closed the last drift.
