---
id: single-source-of-truth-for-the-version-asserted-by-a-test
title: Single source of truth for the version, asserted by a test
type: backlog_item
tags:
- release
- packaging
- programmatic-validation
importance: 5
kind: improvement
status: done
priority: high
effort: S
rank: 0
---

## Problem

Versions disagree across the repo (2026-09-17):

| Where | Says |
|---|---|
| `pyproject.toml` | 0.24.1 |
| `pyrite/__init__.py` `__version__` | 0.12.0 |
| `pyrite-mcp/pyproject.toml` | 0.20.0 (requires `pyrite>=0.20.0`) |
| `web/package.json` | 0.20.0 |
| `.claude-plugin/plugin.json` | 0.1.0 |
| latest tag | v0.24.0 |

`CHANGELOG.md` has a `[0.24.1] - 2026-09-17` section that says "this tag", but
no `v0.24.1` tag or GitHub release exists, and there is no `[Unreleased]`
heading, so new entries are being appended to a version that claims to be cut.
`main` is 226 commits behind `dev`. Minor: tag `v0.6.0` has no changelog entry;
0.12.0 / 0.2.0 / 0.1.0 have entries but no tags; 0.21-0.24 share one heading.

## Fix

- `__version__` reads from package metadata (`importlib.metadata.version`) so it
  cannot drift.
- A test asserts `pyproject.toml`, `web/package.json` and (if kept)
  `pyrite-mcp` agree, and that the top CHANGELOG version is either
  `Unreleased` or has a matching git tag.
- Add `## [Unreleased]`; the release runbook moves it on tag.

## Acceptance

- [ ] `python -c "import pyrite; print(pyrite.__version__)"` matches pyproject.
- [ ] The test fails on today's tree, passes after the sync.

Source: 2026-09-17 project review (three read-only audits: docs/contributor, public-repo, code-health). Same family as [[docs-counts-generated-or-asserted-from-code]].

## Status 2026-09-17

**Done:** `pyrite.__version__` reads `pyproject.toml` in a source checkout and
installed metadata otherwise (`tests/test_version_consistency.py`). Metadata
alone was not enough: an editable install's metadata stays at the old version
after a bump until `pip install -e .` is re-run (it said 0.24.0 while
pyproject said 0.24.1).

**Still open:** `web/package.json` and `pyrite-mcp` agreement (both wait on the
ADR-0031 packaging question), `.claude-plugin/plugin.json`, the
`## [Unreleased]` heading plus the top-CHANGELOG-version-has-a-tag assertion.

## Closed 2026-09-20

Verified shipped during the 0.24.2 pre-release review: tests/test_version_consistency.py exists with 3 tests; pyproject.toml is the only place the version is written.
