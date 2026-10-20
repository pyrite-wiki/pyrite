---
id: post-tag-workflow-install-from-the-tag-run-the-quick-start-build-the-docker-image
title: 'Post-tag workflow: install from the tag, run the Quick Start, build the Docker image'
type: backlog_item
tags:
- release
- ci
- process
importance: 5
kind: task
status: proposed
priority: medium
effort: S
rank: 0
---

## Problem

The roadmap's 0.24.2 Workstream 1 lists "Post-tag workflow: install from the tag, run the Quick Start, build the Docker image (unverifiable locally on 2026-09-17)". No such workflow exists: `.github/workflows/` holds `ci.yml` and a manual-only `publish.yml`. `scripts/release.py` (#140) installs from the tag into a clean venv on the releaser's machine; nothing checks the same from a clean runner, and nothing has ever built the `Dockerfile` in CI.

## Acceptance

1. `.github/workflows/post-tag.yml` runs on a pushed tag matching `v*` and on `workflow_dispatch` with a `tag` input.
2. Job `install`: a clean venv, `pip install "pyrite[server,cli] @ git+https://github.com/markramm/pyrite@<tag>"`, `pyrite --version` equals the tag, then `scripts/run_tutorial.sh` (the Quick Start as tests) against the installed package.
3. Job `docker`: `docker build .` from the tagged tree and `docker run … pyrite --version`; no push to a registry.
4. Every job declares least-privilege `permissions:`; `tests/test_dev_process_config.py` extends the every-job-declares-permissions pin to this workflow and pins the trigger (tags `v*` + dispatch, nothing else).
5. A failure does not delete or move anything: the workflow only reports. The release runbook says what to do when it is red.

## Groom 2026-09-18 (serial)

**Regimes:** a tag that is not a version (`vtest`) — runs and fails on the version check, harmlessly; `workflow_dispatch` against `v0.24.1` (the only evidence available before 0.24.2 is cut — and v0.24.1's tutorial predates #43/#44's looser assertions, so say which result is expected); the tutorial's semantic-search step on a runner with no HF cache (#13/#43 — the tutorial already tolerates it; do not tighten here); a Docker build on a runner with no layer cache (timeout-minutes set).

**Touches** — existing: `tests/test_dev_process_config.py`, the maintainer's release runbook (outside this repo) (one paragraph). New: `.github/workflows/post-tag.yml`.

**Sequence:** after #140 (`scripts/release.py`) merges — the runbook paragraph sits beside its steps; after package H (same test file, same pins). Independent of the packaged-UI themes (when they land, the `install` job gains the `.woff2`/`.svg` check — one line, in that theme).

**Model:** sonnet. **heavy:** no (runs on GitHub). **Cold read:** no. **Size:** S, ~150 lines.

**Out of scope:** publishing anything (PyPI is blocked, ADR-0025; no registry push); the release-attached wheel (the packaged-UI item's step 2); changing `publish.yml`.
