---
id: followups-2026-09-19
title: Follow-ups from the 2026-09-19 reviews
type: note
tags:
- followups
- review
- filed
importance: 5
lifecycle: archived
---

Five follow-ups found during the 2026-09-19 reviews. **Filed 2026-09-20 with the maintainer's agreement:** #192, #193, #194, #195, #196; the `pyrite create -t note` → ADR bug hit while writing this note is #197. Each section below
is ready-to-file issue text: copy the body, use the stated title and label.

---

## 1. The CLI drops the identity pair under `--fields`

**Proposed title:** `pyrite search/get/list --fields` returns rows with no `id` or `kb_name`
**Proposed label:** `good first issue`, `bug`

**Reproduction (verbatim, reproduced 2026-09-19):**

```
$ pyrite search "field projection identity pair" -k pyrite --fields title --format json
{
  "query": "field projection identity pair",
  "count": 2,
  "results": [
    {
      "title": "Groom lane breakdown — 2026-09-18 tick 6 (0.24.2 remaining, pool, quality stock)"
    },
    {
      "title": "Conductor log 2026-W38"
    }
  ]
}
```

The rows are unusable: nothing in the output identifies which entry each title
belongs to, so a caller cannot fetch, update or link the result. The same flag
on REST is being fixed by outside PR #184 ("preserve identity fields in REST
projections"), which mirrors the rule MCP already applies in `_project_fields`
(`pyrite/server/mcp_server.py:97-106`). The CLI was not in that PR's scope.

**The rule to apply** (already the MCP/REST behaviour): `id` and `kb_name` are
always included in a projection, whether or not the caller named them.

**Acceptance:**
- `--fields title` on `pyrite search`, `pyrite get` and the `pyrite list` /
  browse commands returns rows that include `id` and `kb_name` in addition to
  the requested fields.
- Explicitly naming `--fields id,title` does not duplicate or reorder anything.
- A field the caller names that does not exist on the row is still silently
  skipped (current behaviour, do not change).
- Tests cover all four call sites, asserting the identity pair is present.
- Matches what #184 lands for REST — read that PR before starting so the two
  agree.

**Files:**
- `pyrite/cli/search_commands.py:209`
- `pyrite/cli/entry_commands.py:105`
- `pyrite/cli/browse_commands.py:69`, `:132`, `:302`
- reference implementation: `pyrite/server/mcp_server.py:97-106`

---

## 2. One shared `_project_fields` helper

**Proposed title:** Extract one field-projection helper — four spellings of the identity-pair rule
**Proposed label:** `refactor`, `tech-debt` (not a good first issue — it needs the three PRs in context)

Once #184 and #174 land, the identity-pair rule will exist in four places with
three different spellings: `pyrite/server/endpoints/entries.py` will carry two
(one from #174's batch-read parity work, one from #184's projection fix),
`pyrite/server/endpoints/search.py` a third, and
`pyrite/server/mcp_server.py:97-106` the original. Follow-up 1 above adds five
more in the CLI. A rule copied nine times is a rule that will drift — the
extension-drop bug groomed the same day (five hand-rolled copies of
`_base_kwargs`, each missing different fields) is the same failure one layer
down.

**Acceptance:**
- One helper, one definition, called by MCP, both REST endpoint modules and the
  CLI projection sites.
- Behaviour identical to what #184 lands — this is extraction, not a redesign;
  no test should change meaning.
- The helper is the seam for the planned read-shaping module: put it where that
  module will live, not in a `utils` grab bag.

**Files:** `pyrite/server/mcp_server.py`, `pyrite/server/endpoints/entries.py`,
`pyrite/server/endpoints/search.py`, `pyrite/cli/search_commands.py`,
`pyrite/cli/entry_commands.py`, `pyrite/cli/browse_commands.py`

**Blocked on:** #184 and #174 landing, plus follow-up 1. Filing now would
produce a PR that conflicts with both.

**Note:** I could not find a KB entry for the planned read-shaping module —
`pyrite search "read shaping" -k pyrite` surfaces only this week's conductor log
(`desk/notes/conductor-log-2026-W38.md` — moved to the gitignored
`pyrite-desk` KB on 2026-09-21). Before filing, confirm with the
maintainer where that module is meant to live; if the design is only in the log,
this issue should name it rather than assume it.

---

## 3. sqlite-vec filtered KNN recall is bounded by the 4096 `k` ceiling

**Proposed title:** Filtered semantic search can return fewer results than `limit` above 4096 embedded rows (sqlite-vec)
**Proposed label:** `bug`, `search` — **not a good first issue**

Found reviewing PR #145 (`fix/search-filters-every-leg`). The sqlite-vec KNN is
issued with a `k` ceiling of 4096 and filters are applied to the returned
candidates *afterwards*. Above 4096 embedded rows in a KB, a selective filter
(a rare `entry_type`, a narrow tag) can eliminate every candidate the KNN
returned, so a filtered semantic search returns fewer rows than `limit` — down
to zero — while matching rows exist in the KB. The failure is silent and
data-dependent: it appears only once a KB grows past the ceiling, and it looks
like "no results" rather than an error.

- Interim fix shipped with #145: a `warnings` entry on the response.
- Real fix: pre-filter *inside* the KNN so recall is exact — either sqlite-vec
  metadata/partition-key columns, or passing a candidate-id list into the KNN.
  Needs a spike on which sqlite-vec supports at the version pinned.
- Postgres has no equivalent ceiling; this is sqlite-only.

**Acceptance:**
- A filtered semantic search over a KB with >4096 embedded rows returns `limit`
  results whenever `limit` matching rows exist.
- A test builds a corpus past the ceiling (or injects the ceiling as a test
  constant so the corpus stays small — preferred, and say which in the PR).
- The interim `warnings` entry is removed when it no longer applies.
- Postgres behaviour unchanged.

**Files:** `pyrite/storage/backends/sqlite_backend.py` (see the `k` ceiling on
branch `fix/search-filters-every-leg`), `pyrite/services/search_service.py`

**Blocked on:** #145 landing.

---

## 4. Repo endpoint success bodies return absolute server paths

**Proposed title:** Repo endpoint success responses disclose absolute server paths
**Proposed label:** `security`, `bug` — **not a good first issue** (security-adjacent)

PR #161 fixed absolute-path and git-stderr disclosure in repo endpoint **400**
bodies (CodeQL #51 #52 #53). Success bodies were left alone deliberately, to
keep that PR to one theme. They still return filesystem paths from the server:
`RepoInfo.local_path`, and the `path` field in the `subscribe` response —
`pyrite/services/repo_service.py:145` and `:377`.

Same disclosure class as the one #161 closed, on the other branch of the same
handlers: a client learns the server's directory layout and usernames.

**Acceptance:**
- Repo endpoint success bodies no longer contain absolute server paths.
- Decide and state whether the field is dropped, made relative to the repo root,
  or replaced by an opaque handle — API consumers may depend on it, so check
  `web/` and `docs/json-contracts.md` before choosing.
- Tests assert no absolute path appears in success bodies, alongside #161's
  existing tests for the 400 bodies (`tests/test_repo_error_disclosure.py`).
- `docs/json-contracts.md` updated if the response shape changes.

**Files:** `pyrite/services/repo_service.py:145`, `:377`,
`pyrite/server/endpoints/repos.py`, `tests/test_repo_error_disclosure.py`,
`docs/json-contracts.md`

**Note:** this changes a public REST response shape — it needs a cold read and
probably a CHANGELOG breaking-change note.

---

## 5. `configure_logging` never runs, so library warnings hit the user's terminal raw

**Proposed title:** Pyrite never configures logging — `logger.warning` falls through to Python's last-resort handler
**Proposed label:** `bug`, `good first issue` *(see caveat below — confirm with the maintainer first)*

**Correction to the original report:** `configure_logging` *is* called — at
`pyrite/logging.py:112`, at module import time. The actual defect is that
**nothing imports `pyrite/logging.py`**. Confirmed:

```
$ grep -rn "from .logging import\|from ..logging import\|from pyrite.logging import\|import pyrite.logging" --include=*.py pyrite/ tests/
(no output)
```

Every module uses `logging.getLogger(__name__)` directly instead. So the module
never loads, its `configure_logging()` at line 112 never runs, and no handler is
ever installed. Confirmed at runtime:

```
handlers on pyrite logger: []
root handlers: []
```

The consequence — a warning from library code goes to Python's last-resort
handler, which writes an unformatted message plus a full traceback straight to
the user's stderr, in the middle of CLI output:

```
$ python -c "import pyrite.models.base as b; b.parse_datetime('not-a-date')"
Failed to parse datetime: not-a-date
Traceback (most recent call last):
  File ".../pyrite/models/base.py", line 344, in parse_datetime
    return datetime.fromisoformat(s)
           ~~~~~~~~~~~~~~~~~~~~~~^^^
ValueError: Invalid isoformat string: 'not-a-date'
```

No timestamp, no level, no logger name — none of `DEFAULT_FORMAT` from
`pyrite/logging.py:18`. For a KB with one malformed date this prints a traceback
per entry during an index sync, and under `--format json` it corrupts stdout
consumers that do not separate the streams.

**What the entry points should do:**
- The **CLI** (`pyrite/cli/__init__.py` or the Typer app's callback) should call
  `configure_logging()` once at startup, honouring an existing verbosity flag if
  there is one (`configure_quiet` / `configure_verbose` already exist at
  `pyrite/logging.py:93,98`), and send records to **stderr** so `--format json`
  on stdout stays clean.
- The **server** (`pyrite/server/api.py` factory) should configure logging at
  app creation, or explicitly defer to uvicorn's config — decide which and write
  it down; today it does neither.
- **Library imports must not configure logging.** Move the module-level
  `configure_logging()` call at `pyrite/logging.py:112` out of import side
  effect; a library that installs handlers on import is the bug the entry-point
  fix would otherwise re-create. Adding a `NullHandler` on the `pyrite` logger
  is the conventional replacement.

**Acceptance:**
- A `pyrite` CLI command that triggers a `logger.warning` prints a single
  formatted line to stderr, no traceback, with stdout unaffected.
- `--format json` output on stdout remains parseable when warnings fire.
- Importing `pyrite` as a library installs no handlers and changes no global
  logging state.
- A test asserts the `pyrite` logger has a handler after CLI startup and none
  after a bare `import pyrite`.

**Files:** `pyrite/logging.py` (lines 50, 93, 98, 112), `pyrite/cli/__init__.py`,
`pyrite/server/api.py`

**Caveat on `good first issue`:** the reproduction and the file list are
self-contained, but the fix requires a judgement call about the server entry
point and about stdout/stderr separation across every `--format json` command.
Either downgrade the label, or narrow the issue to the CLI entry point plus the
`NullHandler` change and file the server half separately.
