---
id: plugin-repo-extraction
title: "Extract Extensions to Separate Repositories"
type: backlog_item
tags:
- plugins
- distribution
- feature
- launch
- extensions
links:
- target: pypi-publish
  relation: related
- target: extension-registry
  relation: related
- target: roadmap
  relation: related
kind: feature
status: retired
effort: M
lifecycle: archived
---

## Problem

The five shipped extensions (software-kb, zettelkasten, encyclopedia, social, cascade) live in `extensions/` inside the monorepo. This creates problems for launch:

- Users can't install a single plugin without cloning the entire repo
- `pip install pyrite` either bundles all extensions (bloated) or none (confusing)
- Plugin versioning is coupled to core versioning
- Contributors can't develop a plugin independently
- Contradicts the "ecosystem" story — plugins should feel like community packages, not monorepo subdirectories

## Solution

### Phase 1: Extract to separate repos

Move each extension to its own repository under the `markramm` (or `pyrite-kb`) GitHub org:

- `pyrite-software-kb` → `pip install pyrite-software-kb`
- `pyrite-zettelkasten` → `pip install pyrite-zettelkasten`
- `pyrite-encyclopedia` → `pip install pyrite-encyclopedia`
- `pyrite-social` → `pip install pyrite-social`
- `pyrite-cascade` → `pip install pyrite-cascade`

Each repo gets:
- Its own `pyproject.toml` with `pyrite` as a dependency
- Entry point registration (already exists, just needs the right package name)
- README with install instructions and usage
- Tests (moved from monorepo)
- CI via GitHub Actions

### Phase 2: Awesome Plugins page

Add a curated plugin listing to the Pyrite repo (see [[awesome-plugins-page]]). This bridges the gap before the full extension registry (#84) lands in 0.13.

### Ordering

PyPI publish (#74) should land first so `pyrite` core is on PyPI and plugins can declare it as a dependency. Then extract plugins. Can be done in parallel if coordinated.

## Prerequisites

- PyPI publish (#74) — core must be installable before plugins can depend on it

## Success Criteria

- All 5 extensions installable via `pip install pyrite-<name>` from PyPI
- `extensions/` directory removed from monorepo (or left as symlinks for dev)
- Each plugin repo has CI, tests, and a README
- Existing `pyrite init --preset` still works with installed plugins
