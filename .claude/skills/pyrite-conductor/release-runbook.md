# Release & Deploy Runbook

Procedural reference for the `dev → main` release path and the deploy script. SKILL.md keeps the daily-development rules (branches, committing); this file holds the runbook steps that only fire when shipping.

> **Maintainer-only.** Cutting releases and running the deploy script need push
> rights to `main` and the gitignored `pyrite_deployments/` directory, which
> holds the maintainer's site configs. Contributors never need this file: open a
> PR against `dev` (see CONTRIBUTING.md).

## Releasing (dev → main)

Only release when the user explicitly asks to. The decision stays the
maintainer's; **executing it is one command**:

```bash
scripts/release.py X.Y.Z             # dry run (the DEFAULT): the rehearsal. Runs
                                     # every check, names each one that would
                                     # stop --execute, changes nothing; exit 1
                                     # if there is any
scripts/release.py X.Y.Z --execute   # cut it
```

**The rule the script enforces: the commit that gets tagged is a commit CI
already passed.** All release edits happen on `dev`; `main` only ever
fast-forwards. Never commit on `main` (a version bump there is an untested
commit, and it makes `main` diverge from `dev` so the next release needs a
real merge).

### Before you run it

Three things the script checks and cannot do for you. They belong on `dev`, in
one commit, pushed and green *before* you release:

- `pyproject.toml` `version` — the ONLY place it is written;
  `pyrite.__version__` reads it and `tests/test_version_consistency.py` checks
  it.
- `CHANGELOG.md` — open the section being released, `## [X.Y.Z] - YYYY-MM-DD`
  (today). Its **entries do not go here**: they are the fragments under
  `changelog.d/`, which the script assembles into this section in step (e).
  Write only the lede if the release has one ("Operational — see
  `kb/roadmap.md`"). A section that is empty *and* has no fragments to fill it
  is refused; `[Unreleased]` must still be empty (it always is — see
  `changelog.d/README.md`).
- `SECURITY.md` supported-versions table, if the minor changed.

```bash
git commit -m "release: prepare vX.Y.Z" -- pyproject.toml CHANGELOG.md
git push -u origin release-prep && gh pr create --base dev --fill   # dev takes PRs only
```

Read the notes before you cut: step (a) prints the assembled section (the lede
plus every fragment, in Keep a Changelog order) — that is the whole point of a
dry run, and it is the last place the notes can be corrected cheaply.

One-time prerequisite: the `release-blocker` label must exist. The script
refuses to release while an open PR carries it — and refuses to release at all
while the label is *missing*, because it cannot ask the question. It never
creates labels; it fails with the command to run:

```bash
gh label create release-blocker --repo pyrite-wiki/pyrite \
  --description 'Must not ship in the next release' --color B60205
```

### What each step checks

| Step | Checks | Irreversible |
|------|--------|--------------|
| a. preconditions | clean checkout, on `dev`, HEAD exactly `origin/dev`; `origin` resolves to `pyrite-wiki/pyrite` (the repo the `gh` calls name); `vX.Y.Z` exists neither locally, on `origin`, nor as a GitHub release; `origin/main` is an ancestor of the SHA, so step d's push can only fast-forward; `pyproject.toml` version == X.Y.Z; CHANGELOG section dated today with content *or* fragments under `changelog.d/` to fill it, every fragment naming a known section, and no stranded `[Unreleased]`; the `release-blocker` label exists and no open PR carries it. **Prints the assembled release notes** | no |
| b. CI | the commit is **verified**: the required checks for that exact SHA concluded `success` — `gate` by default, matched by exact name, from GitHub Actions only, `skipped` not counted, and green in every workflow run that ran it on the SHA (the newest within one, so a rerun to green counts). The rule is `scripts/verified_commit.py`, which the pre-push hook also uses. Never starts a run; `--wait-ci MINUTES` waits out a pending one (default 15). An answer it cannot read (no `gh` login, no network, more than 100 check runs) stops at once rather than waiting. **When one workflow run's `gate` is red or cancelled** and the others are green, nothing is wrong on `dev`: the refusal names that run by URL and prints `gh run rerun <id>`; re-run it and release when it is green. Fix on `dev` only when it is red for a real reason | no |
| b2. push rehearsal | what **this machine's** pre-push hook will do for the two pushes in step d, asked of the hook's own decision (`scripts/test-affected --preflight`) with the checkout's `.venv/bin/python`: it skips (the commit is verified, or its tree already passed here), or it will run the suite — and then the suite must import. A missing `.venv` or a suite that does not import fails here. A suite that would run and imports is a caution marked `!!`, not a failure. The hook runs the suite whenever it cannot itself establish that the commit is verified: with `PYRITE_PUSH_FORCE`, `PYRITE_PUSH_FULL` or `PYRITE_PUSH_EXPERIMENTAL` set; when its own lookup fails or takes more than 8 seconds (step b passing does not make the hook's request succeed); when step b was satisfied by `--require-check` naming something other than `gate`, which is the only check the hook asks about; or when `origin`'s push URL is not a github.com URL | no |
| c. release layer | what a *user* gets, **before the tag exists** (ADR-0032 §3a): install from the SHA into a throwaway `uv` venv, `pyrite --version` equals X.Y.Z, `scripts/run_tutorial.sh` (the Quick Start) run against that install, `docker build` when docker is present — a loud note when it is not | no |
| d. publish | fast-forward `main` to the SHA (`git push origin <sha>:refs/heads/main`; the ruleset allows only a fast-forward), tag `vX.Y.Z`, push the tag, `gh release create` with the CHANGELOG section, the assembled fragments, and the contributors line | **yes** |
| e. post-release | **consume the fragments** — write the assembled sections into `CHANGELOG.md` under `## [X.Y.Z]` and delete the files from `changelog.d/` — and reopen `## [Unreleased]`, in one commit on a fresh `release/reopen-unreleased-X.Y.Z` branch cut from the release commit (local `dev` is never committed on); print the `git push -u origin …` / `gh pr create --base dev --fill` lines. A no-op only when there are no fragments *and* `[Unreleased]` is already there | **yes** |
| f. handoff | prints what the release does *not* do and cannot: **pyrite.wiki** (below), the deploys the tag does not trigger, the `[Unreleased]` PR, the announcement. Changes nothing | no |

**pyrite.wiki is not automated and cannot be.** The marketing site lives
outside this repo, and it carries version-specific claims — the version it
names, install commands pinned to a tag, and the counts it quotes (MCP tools,
tests, ADRs) — that go stale silently the moment a release lands. The top
GitHub referrer for the repo is chatgpt.com, so those numbers are what gets
quoted to prospective users; `docs-counts-generated-or-asserted-from-code`
tracks fixing the drift at the source. Until then, step f reminds you and you
update the site by hand.

If step **a** says `origin/main` is not an ancestor of the SHA, `main` has
commits `dev` lacks: stop and find out why (a hotfix that was never merged
back?) before going further. It is checked there, not discovered in step d,
because step d moves `main` before it tags — a failure halfway through is the
one thing the ordering exists to prevent.

If something does fail after step d has started, the run says which commands
already ran and that their effects stand, instead of "nothing further was
attempted".

**Required checks, and why not "all green".** `gate` is the required check on
`dev` and `main`; it needs the jobs that must pass, so requiring it requires
them. Checks *not* named are advisory and reported but never blocking — `e2e`
runs on pushes to `main` and is deliberately outside `gate`'s needs (ADR-0032
§3a keeps breadth out of the merge gate), so a red or merely slow `e2e` must
not hold a tag. `--require-check NAME` (repeatable) changes the set.

### The dry run is the rehearsal

Run it **in the checkout you will release from**: several checks describe that
checkout and that machine (untracked files, the venv the hook uses). It does
not stop at the first check that fails. It runs steps a, b, b2 and c, marks
each failure where it finds it (`!! WOULD STOP --execute: ...`), repeats them
all at the end as `FAIL at <step>: ...`, plans no write, and exits 1. It names
untracked files one by one, says what date the CHANGELOG section has, and says
which branch it is on. When it ends `looks releasable`, `--execute` run then
and there stops on none of them. `--execute` itself still stops at the first
failed check, before anything is written.

On 0.25.7 four things failed only on release day — the section dated the day
it was prepared, three untracked files, the hook re-running the suite in a
venv with no `hypothesis`, and a cleanup PR — because the rehearsal had been
run on a branch that was not `dev` and stopped at that first check.

### Safety

`--dry-run` is the default; `--execute` is the only way anything is written.
Every irreversible command is printed verbatim first, and printed *instead of*
running without `--execute`. The script never passes `--no-verify`, never
force-pushes, never deletes a ref, and refuses a dirty checkout or a HEAD that
is not exactly `origin/dev`. `gh` reads answer the checks; `gh` writes are
printed.

Step c is minutes of real network, so a dry run prints it rather than running
it. `--install-check` performs it for real *without* `--execute` — that is how
to rehearse the release layer before release day. `--skip-install-check` drops
it, and then the clean-venv check is yours by hand:

```bash
python -m venv /tmp/relcheck && /tmp/relcheck/bin/pip install -q \
  "pyrite[all] @ git+https://github.com/pyrite-wiki/pyrite@vX.Y.Z"
/tmp/relcheck/bin/python -c "import pyrite; print(pyrite.__version__)"   # X.Y.Z
```

Contributors are credited automatically: every outside author of a PR merged
since the previous tag becomes `Thanks to @a, @b for their contributions.` at
the end of the notes. Contributors are why the project is not a solo project;
the notes say so.

Version numbers follow the roadmap, not the calendar: a minor (0.25) names a
milestone with a definition of done. Do not tag it until that is met; ship
fixes as patch releases of the current minor meanwhile.

**PyPI**: not reachable. The `pyrite` name is held by a locked pre-2FA account (ADR-0025, amended 2026-09-17), so `publish.yml` is `workflow_dispatch`-only and a GitHub release publishes nothing. Install path is `pip install git+https://github.com/pyrite-wiki/pyrite@<tag>`.

## Deploying

Use the deployment script at `pyrite_deployments/deploy.sh` (gitignored, local only):

```bash
# Check all sites
./pyrite_deployments/deploy.sh status

# Deploy latest dev to demo.pyrite.wiki
./pyrite_deployments/deploy.sh demo

# Deploy cascade (code only — fast, no re-index)
./pyrite_deployments/deploy.sh cascade

# Deploy cascade with full re-seed (re-copy KB data, rebuild index, re-export, re-render)
./pyrite_deployments/deploy.sh cascade --reseed

# Deploy specific tag to pyrite.ink
./pyrite_deployments/deploy.sh ink v0.21.0
```

**When to use `--reseed`:** when KB content changed (new events, fixed bodies), or when the export/render code changed (new fields in site cache, new source data in timeline.json).

## Hotfixes

For urgent fixes to a release:

Prefer a normal patch release (above): if `dev` is releasable, fast-forward it.
Only when `dev` carries work that must not ship yet:

1. Branch `hotfix/vX.Y.Z` from the release tag, cherry-pick the fix from `dev`,
   bump the patch version there and open the CHANGELOG section (the fix's own
   fragment comes across with the cherry-pick), push, and wait for CI.
2. Fast-forward `main` to the hotfix branch, tag, release, deploy.
3. Merge `main` back into `dev` immediately, so `main` is an ancestor of `dev`
   again and the next release can fast-forward.

## Site mapping (which branch lands where)

| Site | Source | Trigger |
|------|--------|---------|
| `demo.pyrite.wiki` | `dev` HEAD | Auto on CI pass |
| `capturecascade.org` | `main` tag | Manual via `deploy.sh cascade` |
| `pyrite.ink` | `main` tag | Manual via `deploy.sh ink <tag>` |
| PyPI | — | Unreachable (locked account); `publish.yml` is manual-only |
