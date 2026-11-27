---
id: repo-endpoints-leak-git-stderr-and-absolute-server-paths-in-400-bodies-codeql
title: 'Repo endpoints leak git stderr and absolute server paths in 400 bodies (CodeQL #51 #52 #53)'
type: backlog_item
tags:
- security
- bug
- codeql
importance: 5
kind: bug
status: done
priority: high
assignee: agent:pyrite-worker
effort: M
rank: 0
---

## Problem

CodeQL `py/stack-trace-exposure` alerts #51, #52, #53 on `dev`. Reproduced by the
triage spike (2026-09-18): `POST /api/repos/subscribe` on a nonexistent repo returns
`400 {"message": "Clone failed: Cloning into '/Users/<user>/.pyrite/repos/…'…"}` —
raw git stderr and the server's absolute filesystem paths, to a **write**-tier
caller. `/api/repos/fork` (#52) and `/api/repos/{name}/pr` (#53) have the same
shape at lower yield. The token is already stripped by `_sanitize_output`; the path
disclosure is the leak.

## Groom 2026-09-18 (spike: codeql-triage, Theme B)

Model: opus (it decides what an error may say; getting it wrong either leaks or
makes the feature undiagnosable). Sequence: now — `endpoints/repos.py` and
`services/git_service.py` are quiet; no open PR touches either. Cold read: yes.
heavy: no.

Touches (existing): `pyrite/services/git_service.py` (a new sanitiser beside the
existing `_sanitize_output`), `pyrite/server/endpoints/repos.py`,
`pyrite/services/repo_service.py`, `CHANGELOG.md`. Touches (new):
`tests/test_repo_error_disclosure.py`.

## Acceptance

1. `GitService` gains a path-redacting step applied to every message returned to a
   caller: any absolute filesystem path in git stderr is replaced with a stable
   placeholder (e.g. `<workspace>/owner/repo`). Keep the existing token redaction in
   `_sanitize_output`; this is additive, not a replacement.
2. `POST /api/repos/subscribe` against a non-existent repo returns a 400 whose
   `detail.message` contains **no** substring of the server's `$HOME` or workspace
   root, and no `Cloning into '...'` line. The reproduced string above is the exact
   regression case; assert on it.
3. The message still distinguishes the three failures a write-tier caller must act
   on: repository not found / authentication required / branch not found. A test
   asserts each maps to a distinct, stable `code` in the detail body. Collapsing all
   three into "Clone failed" is a regression, not a fix.
4. The full stderr is still `logger.warning`'d server-side, so the operator loses
   nothing. A test asserts the log record contains what the response body omits.
5. `POST /api/repos/fork` (#52) and `POST /api/repos/{name}/pr` (#53) route their
   `result["error"]` through the same sanitiser.
6. Existing tests in `tests/test_rest_api.py` and any repo-service tests still pass
   unchanged, or their changed assertions are justified in the commit message.

Out of scope, named here and not chased: the private-repo existence oracle noted
under #51 — a write-tier caller learns whether a private repo exists through the
operator's token. Inherent to the feature; needs a design decision (maintainer),
not a message change.

Closes CodeQL alerts #51, #52, #53 (the conductor confirms on the code-scanning
page after merge). Part of
[[codeql-triage-and-close-the-56-open-code-scanning-alerts-on-dev]].
