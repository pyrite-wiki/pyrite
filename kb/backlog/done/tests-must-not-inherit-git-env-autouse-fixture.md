---
id: tests-must-not-inherit-git-env-autouse-fixture
title: 'Tests must not inherit git env: autouse fixture'
type: backlog_item
tags:
- testing
- git
- ci
- contributor-experience
- programmatic-validation
links:
- target: mcp-tool-dispatch-smoke-test-every-registered-tool
  relation: related
  kb: pyrite
- target: full-suite-only-flaky-tests-state-leak-across-test-files
  relation: related
  kb: pyrite
importance: 5
kind: tech_debt
status: done
priority: high
effort: S
rank: 0
---

## Problem

The pyrite repo's own `.git/config` has been overwritten with
`user.name = Test`, `user.email = test@test.com`. 35 commits on
`origin/dev` (2026-07-03 onward) are authored that way.

Mechanism (code read and confirmed; end-to-end reproduction still owed —
see Acceptance): `tests/test_version_service.py:19-23` runs `git init` then
`git config user.email test@test.com` / `user.name Test` via
`subprocess.run(..., cwd=tmp)` with no `env=` and no `check=True`. When the
suite runs inside the pre-commit `pytest` hook it is a child of
`git commit`, which exports `GIT_DIR` (and `GIT_INDEX_FILE`) pointing at the
real repo. `GIT_DIR` beats `cwd`, so the config write lands in
`<repo>/.git/config`. The values match exactly, and the first
`Test` commit follows the hook's installation by hours.

Same unscrubbed pattern: `tests/test_kb_commit.py:29-37`,
`tests/test_kb_git_ops.py:46-47`, `tests/test_pending_changes.py:30-31`,
`tests/test_schema_validate_changed_scope.py:35-36`, and
`tests/test_admin_cli.py`. The hazard is already known —
`tests/test_worktree_service.py:15` (`_clean_git_env`) guards against it,
and [[full-suite-only-flaky-tests-state-leak-across-test-files]] documents
`GIT_DIR` being set under the hook — but the guard was applied per-file, to
two files, rather than structurally. A leaked `test/alice` branch and a
prunable worktree at `/private/tmp/worktree-repro/wt` are from the same
cause.

Production code has the same gap: `pyrite/services/version_service.py:57`,
`pyrite/storage/document_manager.py:114,121` and
`pyrite/services/kb_service.py:1386` invoke git without `_git_env()`. A user
who runs any pyrite command from inside a git hook gets the same
cross-repo writes.

## Proposed validation

1. **Autouse, session-wide fixture in `tests/conftest.py`** (and the shared
   conftest used by `extensions/*/tests`) that deletes every `GIT_*`
   variable from `os.environ` — `GIT_DIR`, `GIT_WORK_TREE`,
   `GIT_INDEX_FILE`, `GIT_OBJECT_DIRECTORY`, `GIT_COMMON_DIR`,
   `GIT_PREFIX`, `GIT_CONFIG_PARAMETERS`, `GIT_CONFIG_COUNT`/`_KEY_n`/
   `_VALUE_n`, `GIT_AUTHOR_*`, `GIT_COMMITTER_*`, … — before any test runs,
   and sets
   `GIT_CONFIG_GLOBAL`/`GIT_CONFIG_SYSTEM` to empty temp files so tests
   neither read nor write the developer's real config.
2. **A canary test**: set `GIT_DIR` to a throwaway repo, run the git-using
   fixtures, assert that repo's config and refs are untouched. This is the
   regression test the per-file fix never had.
3. **Route every production git subprocess through `_git_env()`**, with a
   grep-style test that fails on a bare `subprocess.run(["git", …])` under
   `pyrite/` outside `git_service.py`.
4. Add `check=True` to the test helpers so a failed `git init` cannot
   silently fall through to configuring whatever repo is ambient.

**Second confirmed instance (2026-09-17, reproduced in isolation).**
Committing with `git -c user.name=… -c user.email=… commit` exports
`GIT_CONFIG_PARAMETERS` to the hook. `-c` outranks repo-local config, so
`tests/test_collaboration_integration.py::TestIndexWithAttribution::
test_index_with_attribution_populates_created_by` sees the committer's
override instead of the `Test User` it configured in its temp repo and fails
(`assert 'markramm' == 'Test User'`). Reproduce without a commit:
`GIT_CONFIG_PARAMETERS="'user.name=x'" pytest tests/test_collaboration_integration.py`.
So the leak runs both ways: tests write into the ambient repo, and the
ambient commit's settings reach into the tests.

**Third confirmed instance (2026-09-17, reproduced in isolation).**
`git commit -- <paths>` (the path-enumerated form CLAUDE.md's multi-session
rule steers people toward) builds a temporary index and exports its
absolute path as `GIT_INDEX_FILE`. `tests/test_kb_commit.py::
TestGitServiceStatus::test_status_clean` then runs `git status` in its temp
repo against pyrite's index and is not clean. A plain `git commit` exports
the relative `.git/index`, which resolves inside the temp repo, so it
passes — which is why this only bites the careful form. Reproduce:
`GIT_INDEX_FILE=/abs/copy/of/.git/index pytest tests/test_kb_commit.py -k test_status_clean`.

## One-time cleanup (do with this ticket)

- `git config --local --unset user.name` and `user.email` in the repo.
- Add a `.mailmap` mapping `Test <test@test.com>` to the maintainer, rather
  than rewriting 35 pushed commits.
- `git worktree prune`; delete the leaked `test/alice` branch.

## Acceptance

- [ ] Reproduced first: in a throwaway clone,
      `GIT_DIR=$PWD/.git pytest tests/test_version_service.py` rewrites the
      clone's identity on current `dev`. (Confirms the mechanism before the
      fix — root cause, not correlation.)
- [ ] With the fixture, the same command leaves the clone's config
      untouched; the canary test pins it.
- [ ] No bare git subprocess under `pyrite/` outside the sanctioned helper.
- [ ] A commit made through the pre-commit hook after the fix is authored
      with the developer's real identity.

## Notes

Filed from the 2026-09-17 whole-project review; one of five structural
checks (see [[mcp-tool-dispatch-smoke-test-every-registered-tool]] for the
set). This one matters most for outside contributors: running the documented
`pre-commit install` currently risks rewriting *their* clone's identity.

## Status 2026-09-17

**Done** (`88da503`): repo-root `conftest.py` drops every `GIT_*` variable before
collection and isolates global/system git config; `tests/test_git_env_isolation.py`
is the end-to-end reproduction this ticket owed (it failed for both git-using
test files before the guard); `.mailmap` remaps the 40 pushed `Test` commits;
local identity unset, dead worktree pruned, `test/alice` deleted.

**Worse than the identity clobber, same cause:** at 16:44 local on 2026-09-17 the
real `.git/index` was replaced by a 137-byte index holding one fixture
`README.md`, so git showed all ~1,500 tracked files as staged deletions. A plain
`git commit` in any session at that moment would have committed the deletion of
the repo. Repaired with a mixed `git reset`. The conftest guard closes the test
side of this.

**Still open — item 3:** production git subprocess calls that skip `_git_env()`:
`pyrite/services/version_service.py:57`, `pyrite/storage/document_manager.py:114,121`,
`pyrite/services/kb_service.py:1386`. Route them through `_git_env()` and add a
test that fails on a new bare `subprocess` git call outside `GitService`. Also:
the commit stage no longer runs pytest at all
([[fast-commit-hooks-full-suite-at-pre-push-ci-is-the-gate]]), which removes the
most common trigger, but pre-push still runs the suite as a child of git.

## Closed 2026-09-20

Verified shipped during the 0.24.2 pre-release review: conftest.py:56 has the autouse fixture; its docstring documents the GIT_DIR-beats-cwd problem it solves.
