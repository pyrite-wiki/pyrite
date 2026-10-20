---
id: packaged-web-ui-3-quick-start-leads-with-the-one-liner-and-the-release-checks-the-ui
title: 'Packaged web UI (3/3): Quick Start leads with the one-liner and the release checks the UI'
type: backlog_item
tags:
- packaging
- install
- docs
- release
importance: 5
kind: feature
status: proposed
priority: high
effort: S
rank: 0
---

Third of three themes split from [[installable-from-github-with-a-working-web-ui-package-the-built-frontend]]; closes the parent.

## Acceptance (from the parent, verbatim)

- "The release runbook's clean-venv step checks the UI, not just `pyrite.__version__`."
- "README Quick Start leads with the `uv tool install` one-liner."

1. `scripts/release.py`: before the version commit it runs `scripts/build_static.sh` and stages `pyrite/static/` (the "refresh only in the release commit" rule, made a step rather than a memory); its clean-venv step sets `PYRITE_REQUIRE_FRESH_STATIC=1`, starts `pyrite-server` from the installed package and asserts `/`, a `.woff2` and an `.svg` return 200. The dry run prints both steps.
2. `release-runbook.md` describes the same, and the post-tag workflow's `install` job (if it has landed) gains the same three requests.
3. README Quick Start leads with `uv tool install "pyrite[server,cli] @ git+https://github.com/markramm/pyrite@vX.Y.Z"`; the "no web UI from a git install" caveat added on 2026-09-17 is removed; `docs/getting-started.md` agrees.
4. The parent item is marked done and moved.

## Groom 2026-09-18 (serial)

**Regimes:** `npm` absent on the releaser's machine (release.py refuses before touching anything, naming the requirement); `build_static.sh` fails half-way (no partial `pyrite/static/` is staged — the #140 cold read's "aborted run" class); a rerun after an aborted release where `pyrite/static/` is already fresh (no-op, not an error); the UI check when the port is taken (free-port pattern from `tests/e2e/`); the README one-liner's tag — it must not hardcode a version the docs-facts test will then have to chase (use the placeholder form the parent uses, or derive it).

**Touches** — existing: `scripts/release.py`, `tests/test_release_script.py`, the maintainer's release runbook (outside this repo), `README.md`, `docs/getting-started.md`, `.github/workflows/post-tag.yml` (if present), the parent item.

**Sequence:** after theme 2; after #140 merges; after the changelog-fragments theme (both edit `scripts/release.py` and its test); before the docs-facts theme and the README reposition (all edit `README.md` — this one first because it changes the facts the others assert and frame).

**Model:** sonnet (#140's cold read already shaped release.py's step structure; this adds two steps in its idiom). **heavy:** no — release.py's tests are dry-run/fake-repo tests; the real build happens at the release. **Cold read:** yes — `scripts/release.py` moves `main`. **Size:** S–M, ~200 lines.

**Out of scope:** the README's opening and positioning (the reposition item); Railway/deploy docs (#20, reserved).
