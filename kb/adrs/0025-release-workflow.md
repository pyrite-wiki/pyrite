---
id: adr-0025
type: adr
title: "Release Workflow: Dev Branch, Tagged Releases, and Deployment Tiers"
adr_number: 25
status: accepted
deciders: ["markr"]
date: "2026-04-01"
tags: [process, releases, deployment, git, ci]
links:
- target: adr-0032
  relation: amended_by
  kb: pyrite
---

> **Amended by [[adr-0032]] (2026-09-17).** The branch roles below stand. "All
> work happens on `dev`" does not: work happens on feature branches and reaches
> `dev` by pull request with checks green on top of current `dev`; `main` moves
> only by fast-forward to a CI-verified commit. Where this document and
> ADR-0032 differ, ADR-0032 wins.

# ADR-0025: Release Workflow: Dev Branch, Tagged Releases, and Deployment Tiers

## Context

Pyrite has been developed on `main` with direct pushes. This worked during solo development but is now untenable:

- **Multiple live deployments** (demo.pyrite.wiki, and at the time capturecascade.org) pull from `main` HEAD.
- **External users** are installing from source and GitHub. Breaking `main` breaks them.
- **Development velocity is high** — 20+ changes in a single session is common. Any of these could break a deployment.
- **The cascade Dockerfile** clones `pyrite.git` at build time with `--depth=1`, getting whatever is on `main`.
- **Releases are untagged** — there is no systematic tagging (pyproject.toml says 0.20.0, git has only a v0.6.0 tag).

> **Amended 2026-09-17 — capturecascade.org no longer deploys pyrite.** It is
> now a Hugo build of the `cascade-timeline-kb` repo: `generate_content.py`
> reads the KB markdown directly and Hugo renders it, with GitHub Actions
> deploying to a VPS. It consumes KB *content*, not a pyrite release, so it
> is no longer a consumer of this workflow. The same pattern now serves
> detention-pipeline. Site deployments that pin a pyrite tag still track
> `main`; capturecascade is simply not one of them.

Full gitflow is unnecessary — there are only a handful of users and no formal QA process. But the minimum viable discipline is: don't deploy untested code to live sites.

## Decision

### Branching Model

**Two long-lived branches:**

- **`main`** — stable releases only. Every commit on `main` is a tagged release (or a merge preparing one). Early adopters and any deployment pinning a tag track this.
- **`dev`** — daily development. All feature work, bug fixes, and experiments land here. CI runs on every push. demo.pyrite.wiki tracks this (dogfooding).

**Short-lived feature branches** (optional): for large multi-day changes that would destabilize `dev`. Branch from `dev`, merge back to `dev`. Named `feature/<slug>` or `fix/<slug>`.

### Three Deployment Tiers

| Tier | Branch | Who | Stability | Updates |
|------|--------|-----|-----------|---------|
| **Development** | `dev` | demo.pyrite.wiki | Latest, may break | Auto-deploy on CI pass |
| **Stable** | `main` (tags) | early adopters, pinned deployments | Tested, release-gated | Manual deploy of tagged release |
| **Published** | Tags → GitHub release | source installers | Formally released | Manual release creation |

> **Amended 2026-09-17 — PyPI is not reachable.** The `pyrite` name on PyPI
> is held by an account predating PyPI's 2FA requirement, which is locked;
> `publish.yml` has never fired, and PyPI carries only a `0.1` placeholder
> from before essentially all of this work. The install path is source or
> `pip install git+https://github.com/markramm/pyrite@<tag>`. Recovering the
> account, or publishing under a new name from a new 2FA-enabled account, is
> unresolved — see the "Published" tier above, which now means *a GitHub
> release exists to pin to*, nothing more.

### Release Process

1. Work accumulates on `dev` until a release is warranted.
2. CI must be green on `dev` — this is a hard gate for merge to `main`.
3. Merge `dev` → `main` (fast-forward or merge commit, no squash — preserve history).
4. Tag `main` with a semver tag: `v0.21.0`, `v0.21.1`, etc.
5. Bump `pyproject.toml` version on `dev` to the next dev version.
6. Create a GitHub release from the tag. **This publishes nothing
   automatically** — it exists so there is a pinnable artifact and durable
   release notes.
7. Deploy stable sites by updating their version reference and rebuilding.

### CI and Gating

**On push to `dev`:**
- Run full test suite (backend + frontend + extensions)
- Run linting (ruff)
- On pass: auto-deploy demo.pyrite.wiki (optional, can be triggered manually)

**Merge `dev` → `main` gated on:**
- All CI checks passing on `dev`
- Branch protection rule on `main`: require status checks to pass

**On push to `main`:**
- Run full test suite (confirmation)
- On tag: nothing automatic — see the PyPI amendment above

**GitHub branch protection on `main`** (verified present 2026-09-17):
- Require status checks to pass before merging — **currently `test (3.12)`
  only**, of six CI jobs
- No force pushes ✓
- No deletions ✓
- `enforce_admins: false` — the owner can push past the gate

> **Gap worth closing.** Requiring a single Python version is thin
> protection: the 2026-09-17 type-resolution bug manifested *only* because
> plugin discovery order differs between hosts, and a matrix exists
> precisely to catch environment-dependent failures. `test (3.11)`,
> `test (3.13)`, `test-optional-deps` and `frontend` should also be
> required. `e2e` should not be, while it remains non-deterministic and
> non-blocking. With `enforce_admins: false` the gate is convention for the
> owner rather than enforcement — acceptable for a solo maintainer, worth
> revisiting before the shared-instance pilot adds contributors.

### Version Numbering

Continue from the current pyproject.toml version (0.20.0). Current release is **v0.21.0**.

- **Minor** (0.X.0): features, non-breaking changes, accumulated bug fixes.
- **Patch** (0.X.Y): urgent fixes to a release (cherry-pick to `main`, tag, release).
- **Major** (1.0.0): public announcement / API stability commitment. Not yet.

Dev versions use PEP 440 dev suffix: `0.22.0.dev0`.

### Deployment References

Deploy scripts and Dockerfiles reference specific tags, not branch HEAD:

```dockerfile
# Cascade and stable deployments pin to a release tag:
ARG PYRITE_VERSION=v0.21.0
RUN git clone --branch $PYRITE_VERSION --depth=1 https://github.com/markramm/pyrite.git /tmp/pyrite
```

```bash
# Demo tracks dev branch:
git checkout dev && git pull
```

### What This Does NOT Include

- **No PR reviews** — solo developer + AI agents, PRs add ceremony without value right now.
- **No release branches** — unnecessary at current scale. If a patch is needed, cherry-pick to `main` and tag.
- **No changelog generation** — keep it manual for now. Consider `git-cliff` later.

## Consequences

### Positive
- Live stable sites no longer break from untested development pushes
- Demo site dogfoods latest development for fast feedback
- Users and stable deployments can pin to a known-good version
- GitHub releases align with git tags (PyPI does not — see the amendment above)
- Rollback is trivial: deploy the previous tag
- CI gating provides a quality floor for releases

### Negative
- Small overhead: merge `dev` → `main` when releasing
- Must remember to bump version after tagging
- Deploy scripts need the version arg updated for each upgrade

### Migration (completed 2026-04-01)

1. Tagged `main` as `v0.21.0` ✓
2. Created `dev` branch from `main` ✓
3. Updated cascade Dockerfile to pin `PYRITE_VERSION=v0.21.0` ✓
4. Updated demo `update.sh` to accept optional tag arg ✓
5. Set GitHub default branch to `dev` ✓
6. CI extended to run on both `main` and `dev` ✓
7. Bumped pyproject.toml on `dev` to `0.22.0.dev0` ✓
8. Branch protection on `main` — ✓ present, verified 2026-09-17, but requires
   only `test (3.12)` of six jobs and does not enforce against admins (see
   the gap note under CI and Gating)
9. Auto-deploy demo on dev CI pass — pending

### First application of this process (2026-09-17)

v0.24.1 is the first release cut under this ADR — and the first GitHub
release the project has ever had. Notes from running it:

- Step 3's merge is a clean fast-forward: `main...dev` was `0 225`, with
  `main` an ancestor of `dev`. `main` had not moved since 2026-04-10.
- Step 5 is easy to miss. The version was bumped to `0.24.1` on `dev` as
  release prep; under this process the tag carries that and `dev` must then
  move *past* it, or both report the same version indefinitely. That drift
  is exactly why `web/package.json` sat at `0.20.0` while `pyproject.toml`
  said `0.24.0`.
- Step 2's gate held: the release was staged behind a green CI run rather
  than tagged on assumption.
