---
id: pypi-trusted-publisher
title: "Configure PyPI trusted publisher for automated releases"
type: backlog_item
tags:
- ops
- packaging
- ci
links:
- target: pypi-publish
  relation: related
kind: task
status: retired
priority: high
effort: S
lifecycle: archived
---

## Blocker

Waiting on PyPI support team to resolve project access ticket for the `pyrite` project on PyPI. Cannot configure trusted publisher until access is granted. (2026-03-09)

## Problem

The GitHub Actions publish workflow (`.github/workflows/publish.yml`) uses PyPI trusted publishing (OIDC) to publish without API tokens. Before the first automated release can succeed, both PyPI projects (`pyrite` and `pyrite-mcp`) need their trusted publisher configured in PyPI project settings.

## Steps

1. Go to https://pypi.org/manage/project/pyrite/settings/publishing/ and add a trusted publisher:
   - Owner: `markramm`
   - Repository: `pyrite`
   - Workflow name: `publish.yml`
   - Environment: `pypi`
2. Repeat for `pyrite-mcp` at https://pypi.org/manage/project/pyrite-mcp/settings/publishing/
3. Create a GitHub environment named `pypi` in the repo settings (Settings > Environments)
4. Test by creating a GitHub release with tag `v0.12.0`

## Related

- [[pypi-publish]] — Added the publish workflow
- [[container-deployment]] — Unblocked by working PyPI publish
