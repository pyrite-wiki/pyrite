# Pyrite Gotchas and Known Pitfalls

Things that look right but will bite you. Read this before your first extension or before debugging a confusing issue.

## Hooks: DB Access via PluginContext

**Status:** Resolved via PluginContext dependency injection.

Plugins receive a `PluginContext` via `set_context(ctx)` at startup. The context provides `ctx.db`, `ctx.config`, and `ctx.services`. Hooks can access the DB through the plugin instance's stored context:

```python
class MyPlugin:
    def set_context(self, ctx):
        self._ctx = ctx

    def get_hooks(self):
        return {"after_save": [self._after_save]}

    def _after_save(self, entry, context):
        self._ctx.db.execute_sql("UPDATE ...")  # Works
```

**Gotcha:** Hooks defined as standalone functions (not methods) still don't have DB access. Always use instance methods that can reach the plugin's stored context.

## generate_entry_id Is Title-Based

`generate_entry_id("My Great Note")` returns `"my-great-note"`. This means:

- Two entries with the same title get the same ID
- Changing a title changes the ID
- Empty title → empty ID (which can cause silent issues)

**Always check** `meta.get("id", "")` first. Only call `generate_entry_id()` as fallback:

```python
entry_id = meta.get("id", "")
if not entry_id:
    entry_id = generate_entry_id(meta.get("title", ""))
```

## Validator Signature: 3 Arguments

Validators receive `(entry_type, data, context)` — not an Entry object, raw dicts.

```python
# Correct
def validate_my_type(entry_type: str, data: dict, context: dict) -> list[dict]:
    ...

# Wrong — will crash at runtime
def validate_my_type(entry: Entry) -> list[dict]:
    ...
```

The `context` dict contains: `kb_name`, `kb_schema`, `user`, `existing_entry` (for updates).

## Plugin Validators Always Run

In `pyrite/schema.py`, plugin validators run for **all entries** during schema validation, not just entries of the plugin's declared types. This is by design — it allows cross-type validation rules.

**Consequence:** Your validator MUST check `entry_type` and return `[]` for types it doesn't handle:

```python
def validate_my_type(entry_type, data, context):
    if entry_type != "my_type":
        return []  # CRITICAL: don't validate other types
    # ... actual validation
```

If you forget this check, your validator will generate errors for every entry in the KB.

## DB Table Names Can Collide

Plugin tables all live in the same SQLite database. If two plugins define a table with the same name, one will silently override the other.

**Rule:** Prefix table names with your extension name:

```python
# Good
{"name": "encyclopedia_review", ...}

# Bad — might collide
{"name": "review", ...}
```

## CLI Commands: Lazy Import

Plugin CLI commands should use lazy imports to avoid circular dependencies and slow startup:

```python
def get_cli_commands(self):
    from .cli import my_app  # Lazy import
    return [("my-cmd", my_app)]
```

If you import at module level, the CLI module may try to import pyrite components that aren't ready yet during plugin discovery.

## Two-Tier Durability

Pyrite has two data tiers. Mixing them up leads to data loss or bloated repos.

| Tier | Storage | Git-tracked? | Examples |
|------|---------|-------------|----------|
| **Content** | Markdown + YAML frontmatter | Yes | Entries, links, tags, sources |
| **Engagement** | SQLite (custom DB tables) | No | Votes, reviews, view counts, reputation |

**Content tier:** Knowledge that should survive across clones and be version-controlled. This is the entry's markdown file.

**Engagement tier:** Operational data local to this install. Created via `get_db_tables()`. Lost if the SQLite DB is deleted.

**Rule of thumb:** If a user clones the repo fresh, will they need this data? Yes → content tier. No → engagement tier.

**Common mistake:** Putting engagement data in entry metadata. This bloats the git history with frequent small changes (vote counts, view counts).

## Entry Subclass from_frontmatter

When subclassing an entry type (e.g., `ZettelEntry(NoteEntry)`), your `from_frontmatter` must set **all** base class fields explicitly. You can't rely on `super().from_frontmatter()` because `from_frontmatter` is a `@classmethod` that constructs the object directly.

```python
@classmethod
def from_frontmatter(cls, meta, body):
    # Must include ALL base class fields
    return cls(
        id=...,
        title=...,
        body=body,
        summary=...,
        tags=...,
        sources=parse_sources(meta.get("sources")),
        links=parse_links(meta.get("links")),
        provenance=...,
        metadata=...,
        created_at=parse_datetime(meta.get("created_at")),
        updated_at=parse_datetime(meta.get("updated_at")),
        # Plus your custom fields
        my_field=meta.get("my_field", ""),
    )
```

If you forget a base field, it silently gets the dataclass default (often empty string or None), and the data appears to "vanish" on roundtrip.

## MCP Tool Handlers: Import Inside Handler

MCP tool handlers run in a separate context. Import pyrite modules inside the handler, not at the top of the plugin file:

```python
def _mcp_inbox(self, args):
    # Import here, not at module level
    from pyrite.config import load_config
    from pyrite.storage.database import PyriteDB

    config = load_config()
    db = PyriteDB(config.settings.index_path)
    try:
        # ... use db
        return result
    finally:
        db.close()  # Always close
```

## Pydantic Schemas vs DB Nulls

API response schemas (Pydantic models in `pyrite/server/schemas.py`) must account for NULL values in the database. If a field is typed as `str` but the DB row has `NULL`, Pydantic will raise a validation error.

```python
# Will crash on NULL file_path
class EntryResponse(BaseModel):
    file_path: str

# Correct — handles NULL
class EntryResponse(BaseModel):
    file_path: str | None = None
```

## SPA Fallback Catches API Routes

The static file serving uses SPA fallback: any non-file request returns `index.html`. If an API route isn't mounted with the `/api` prefix, the SPA fallback will catch it and return HTML instead of JSON.

**Symptom:** API endpoint returns `<!DOCTYPE html>` instead of JSON.
**Fix:** Ensure all API routes use the `/api` prefix via `APIRouter(prefix="/api")`.

## Relationship Types Need Inverse Pairs

When defining relationship types in `get_relationship_types()`, always define both the relationship and its inverse:

```python
# Good — both sides defined
{
    "elaborates": {"inverse": "elaborated_by", "description": "..."},
    "elaborated_by": {"inverse": "elaborates", "description": "..."},
}

# Bad — inverse references a type that doesn't exist
{
    "elaborates": {"inverse": "elaborated_by", "description": "..."},
    # Missing elaborated_by definition
}
```

`get_inverse_relation()` in `schema.py` will look up the inverse, and it must exist in the merged relationship types.

## _resolve_entry_type Silently Maps Core Types to Plugin Subtypes

When you call `kb_create(type="event")`, the entry type gets silently resolved to a plugin subtype (e.g., `cascade_event`) if a plugin provides one. This happens in `kb_service.py:_resolve_entry_type()`.

**Consequences:**
- `type(entry).__name__` won't be `Event` — it'll be `CascadeEvent`
- The entry's dataclass fields come from the plugin class, not the core class
- `isinstance(entry, Event)` still works (inheritance), but exact type checks fail

**When this bites you:** Writing tests that construct entries directly vs going through `build_entry()`. Direct construction uses the core class; the factory uses the resolved plugin class. Behavior differs if the plugin overrides `from_frontmatter` or adds custom fields.

## build_entry: Unknown kwargs Go to metadata, Not Top-Level Frontmatter

The `build_entry()` factory in `factory.py` introspects the resolved entry class's dataclass fields. Any kwargs that aren't recognized dataclass fields get collected into the `metadata` dict.

**Consequence:** If you add a new field to a plugin entry class's dataclass but forget to handle it in `from_frontmatter()`, the field's value silently ends up in `metadata` instead of the proper field. The entry appears to save correctly but the field is "invisible" to code that reads the dataclass attribute.

**How to verify:** After creating an entry, check both `entry.my_field` and `entry.metadata.get("my_field")`. If the value is in metadata but not the field, your `from_frontmatter()` isn't extracting it.

## Connection Management: session vs _raw_conn

`PyriteDB` has **two separate** database connections that don't share transaction state:

- `db.session` — SQLAlchemy ORM session (used by `execute_sql()`, ORM models)
- `db._raw_conn` — Raw `sqlite3` connection (used by `conn` property, legacy code)

**The gotcha:** ORM writes via `session` are NOT visible to `_raw_conn` until committed. They use different connections from different engines.

```python
# This will NOT see uncommitted ORM writes:
db._raw_conn.execute("SELECT * FROM entry WHERE id = ?", (entry_id,))

# This WILL see ORM writes (goes through session):
db.execute_sql("SELECT * FROM entry WHERE id = ?", (entry_id,))
```

**Rules:**
- Use `db.execute_sql()` for reads that need to see ORM writes
- `db.conn` property returns `_raw_conn` — treat it as deprecated
- In tests, if you must use `_raw_conn`, ensure prior ORM operations are committed first

## Hybrid Search Pagination: Both Legs Must Over-Fetch

`_hybrid_search()` in `search_service.py` uses Reciprocal Rank Fusion to combine keyword and semantic results. Both legs fetch `max(limit * 2, offset + limit)` candidates because:

1. RRF needs full ranked lists from both legs to compute scores correctly
2. The fused result set is sliced at `[offset : offset + limit]` after scoring
3. If either leg fetches too few candidates, pagination at high offsets returns empty

**When this bites you:** Adding a new search mode or modifying the hybrid pipeline. Always ensure both legs fetch enough to cover `offset + limit`.

## `list_files()` Skips Files with "template" in the Filename

`KBRepository.list_files()` (repository.py:244) filters out any `.md` file whose name contains the substring `"template"`. This is a broad substring check, not a prefix/suffix match.

**When this bites you:** Creating KB entries with filenames like `templated-foo.md`, `template-bar.md`, or `path-templates.md`. They silently won't be indexed. Rename to avoid the word entirely (e.g., `dynamic-foo.md`).

## Closing a Backlog Item: Use `status=done` (and the History of the `kb/notes/` Misplacement)

Two traps when closing a `backlog_item`, both load-bearing:

**Trap 1 — the status value.** Use `status=done`, **never** `status=completed`.
`completed` is off-enum for backlog items (the type declares `proposed`/`planned`/
`in_progress`/`done`; the board maps both `done` and `completed` to the Done column,
which masks the problem). It passes silently but drifts the board — this exact mistake
once stranded 75 items on an undetected `completed` status (see `index.py:703`). As of
this writing 380 items use `done` and 0 use `completed`. `done` is canonical.

**Trap 2 — file placement.** Largely **fixed** (commit on the
`update-relocates-entry-to-type-default-subdir` ticket): `pyrite update` now preserves an
entry's existing subdirectory instead of relocating it to the type default. A
`backlog_item` already in `kb/backlog/` or `kb/backlog/done/` **stays put** on
`status=done` — no more manual `mv` after updates.

**Create-side placement — fixed 2026-09-17.** `pyrite create -t backlog_item` used to
land new items in `kb/notes/`. The earlier explanation here ("`backlog_item` isn't
declared in `kb.yaml`") was wrong: it *is* declared, just without a `subdirectory`, and
`KBRepository._infer_subdir` then walked the MRO (`BacklogItemEntry` → `NoteEntry` →
`notes/`) without ever asking the plugin that owns the type. It now consults the plugin
KB presets first (`PluginRegistry.get_type_default_subdirectory`), so new items land in
`kb/backlog/`. Precedence: kb.yaml `subdirectory` → core type → plugin preset → MRO parent.

When closing an item you still `git mv` it to `kb/backlog/done/` yourself — that is a
convention, not something the type's subdirectory encodes.

**Trap 3 — `-f` is not a substitute for a dedicated flag on list-typed fields (#231).**
`-f status=done` is correct and is the pattern CLAUDE.md shows, so the hand reaches for
`-f tags=a,b` next. It does not work, and it does not tell you:

```
$ pyrite update <id> -k pyrite -f tags=mcp,agent-ux
{"updated": true, ...}                 # looks fine
$ pyrite get <id> -k pyrite --format json | jq .tags
["m","c","p",",","a","g","e","n","t","-","u","x"]     # the CHARACTERS
```

`-f` stores the raw string (`tags: mcp,agent-ux`, not a YAML list), and the reader then
iterates it as a sequence. The entry drops out of `pyrite tags`, out of tag-filtered
search and out of every `sw` view keyed on a tag, silently. Use `--tags "a,b"`, which
parses properly. Assume the same for any other list-typed field reachable through `-f`
(`participants`, `aliases`, `actors`). Repair by re-running with `--tags`.

**Always read the entry back** after a CLI update that touched a list field:
`pyrite get <id> -k pyrite --format json` shows what the index will actually serve, which
is not always what the file looks like at a glance.

## `pyrite sw new-adr` Takes a Positional TITLE and Misfiles Without `-k`

Two traps in one command:

1. **`TITLE` is a positional argument, not `--title`.** `pyrite sw new-adr --title "X"`
   fails. Correct: `pyrite sw new-adr "X" -k pyrite --status accepted`. (Note this differs
   from `pyrite create`, which *does* take `--title` — the inconsistency is real.)
2. **Without `-k`, the file is written to `./adrs/` relative to your current directory**,
   not the KB's `kb/adrs/`. The command resolves the KB path only when `--kb` is passed;
   otherwise it falls back to `Path(".")` (`cli.py:135-141` in the software-kb extension).
   It reports `Created ADR-NNNN` and exits 0, so the misplacement is silent — the file is
   never indexed and `pyrite sw adrs` never shows it. The next-number lookup *does* default
   the KB, so you get a correctly-numbered ADR in the wrong place.

**Always pass `-k pyrite`.** If you forget, `mv ./adrs/<file> kb/adrs/`, remove the stray
`./adrs/`, then `pyrite index sync`.

## A `from ... import X` Anywhere Inside a Function Makes `X` Local for the Whole Function

Hit while fixing `search-query-syntax-error-contract`: added `from ..utils.errors import
cli_error` at module level in `search_commands.py`, then added a new `except` branch that
called `cli_error(...)`. Got `UnboundLocalError: cannot access local variable 'cli_error'
where it is not associated with a value` — even though the module-level import should have
made it a global.

Root cause: the `search()` function already had a *local* `from ..utils.errors import
cli_error` inside an earlier `if` branch (a deliberate lazy-import pattern used elsewhere in
this file). Python's scoping is lexical and whole-function: any assignment (including an
`import`) to a name anywhere in a function body makes that name local for the **entire**
function, from its first line — even before the local import statement executes. The
module-level import at the top of the file is shadowed for the whole `search()` function
body, not just after the local import line.

**Fix:** either import at module level only (remove all local imports of that name in the
function), or match the existing lazy-import pattern and add a local import at your new call
site too. Don't mix module-level and local imports of the same name within one function.

**How to catch this:** if you add a module-level import and a function that already does
local (lazy) imports starts raising `UnboundLocalError` on a name you just imported, search
the whole function body (not just nearby) for another `import` of that name.

**Status:** ticketed — backlog item `new-adr-writes-to-cwd-without-kb-flag`.

## `pre-commit run --all-files` Is Repo-Wide, Not Scoped to a Backlog Item's Files

Hit while fixing `ci-make-green-and-load-bearing`: CI's lint step only checks `ruff check
pyrite/ tests/` and `ruff format --check pyrite/ tests/` — so I fixed formatting drift there,
confirmed CI's exact lint step passes, and committed. Then, separately, ran `pre-commit run
--all-files` to verify the newly-installed hooks work. That command has **no directory scope**
— `.pre-commit-config.yaml`'s hooks apply to the whole repo by default (`extensions/`,
`scripts/`, `benchmarks/`, `deploy/`, `kb/*.md`, `ui_streamlit.py`, ...), not just what CI
lints or what the current ticket touches. It auto-fixed 76 ruff errors (some behavioral —
unused-variable removal, not just formatting) and reformatted 66 more files across the repo,
producing an 85-file, 3000+-line diff far outside the ticket's scope, mixed with real (if
minor) behavioral changes I hadn't reviewed.

**Fix:** `git checkout -- <files>` to revert everything the stray run touched, keep only the
intentional change (`pyproject.toml`'s new dependency).

**How to avoid:** when verifying a newly-installed pre-commit hook works, either scope it to
specific files (`pre-commit run --files <paths>`) or just trust `pre-commit install` succeeding
— don't run `--all-files` unless the task is explicitly "clean up the whole repo's pre-commit
compliance." A repo-wide hook run is a different, larger task than a CI-lint-scoped fix.

## Never `pre-commit install` from a worktree's venv

The hook scripts in the shared `.git/hooks/` embed `INSTALL_PYTHON=<path of the
python that ran pre-commit install>`. Run it from a worktree's `.venv` and every
checkout's hooks point at that venv; remove the worktree and every commit and
push in the repo fails with "`pre-commit` not found. Did you forget to activate
your virtualenv?" (2026-09-17, while testing `scripts/new-worktree.sh`).
Always install from the main checkout: `cd /path/to/pyrite && .venv/bin/pre-commit
install`. The script does this for you. If hooks are broken, that same command
repairs them.

## `git commit --allow-empty` is not an empty commit if anything is staged

It commits the index like any other commit. Used as a "hook probe" with work
staged, followed by `git reset --hard HEAD~1`, it destroyed the working-tree
edits made after staging (2026-09-17; recovered from the reflog, three edits
re-done). Probe hooks with `pre-commit run --all-files` instead, or on a
throwaway branch with nothing staged. And never `reset --hard` with a dirty
tree.

## `pyrite -k pyrite` in a worktree wrote to the main checkout

The `pyrite` KB is registered in `~/.pyrite/config.yaml` with the main
checkout's path. Run `pyrite update <ticket>` from a worktree and the file
under `/Users/markr/pyrite/kb/` changes, not the worktree's -- three stray
modifications on 2026-09-18, caught only by `git status` in the main tree.
Fixed by repo-local config: `resolve_config_dir()` finds `.pyrite/config.yaml`
upward from the cwd (explicit `PYRITE_CONFIG_DIR` still wins), and
`scripts/new-worktree.sh` writes one per worktree. Verify with `pyrite kb
list` before the first KB command in any worktree.

## What you put in the frontmatter dict is what gets written back (#46, fixed)

On 2026-09-18 `update <backlog-item> --tags a,b` wrote `body:` (the whole body
as a YAML string), `file_path:`, `importance: 5` and `rank: 0` into the file
and reordered every other key. The file still parses, so nothing complains.

The mechanism is worth remembering because it will recur. `extra_frontmatter`
preserves "keys this entry's class did not declare", and it decides what those
are **empirically**: whatever is in the `meta` dict but not in
`to_frontmatter()`. `KBRepository._load_entry` was setting `fm["body"]` and
`fm["file_path"]` on that same dict before handing it over, so two model
attributes were classified as unknown frontmatter and faithfully preserved
into the file. The protection was working; it was being fed non-frontmatter.

So: **never put an Entry attribute into a dict that is going to
`from_frontmatter` / `capture_extra_frontmatter`.** `body` arrives as the
positional argument and `file_path` is set by the caller. `_BASE_CONSUMED_KEYS`
now lists the model internals as a backstop.

Two related rules the fix established:

- A field serialized even at its default (`importance: 5`, `rank: 0`) must not
  be written onto a file that never carried the key. Entries built in memory
  still emit it — commit 7783335 fixed the loss of an explicit `importance: 5`
  — but a load records which always-written defaults the source lacked, so a
  round trip does not invent them.
- **That suppression lives in exactly one place: `Entry._frontmatter_for_file`,
  called only from `to_markdown`. Do not guard it per call site.** The first
  attempt guarded `importance` and `rank` by hand and missed `priority` three
  lines above `rank` in the same method — and 31 other types besides, every one
  of which emits some field unconditionally at its default. A per-site guard
  also misses every plugin type written afterwards.
- **`to_frontmatter` and the file deliberately disagree, and that is the point.**
  `to_frontmatter` always reports the entry's real values, because
  `storage/index.py` builds the index's metadata column from it and `sw backlog`
  filters on the `status` and `priority` it finds there. An entry loaded from a
  file with no `status:` genuinely *is* `proposed`. Suppression is a property of
  the **file**, not of the entry, so it happens at the file boundary and nowhere
  else. If you ever feel like moving the filter "up" into `to_frontmatter` for
  tidiness, that is the bug you would be creating.
- A write re-emits unchanged keys from the ruamel mapping the file was parsed
  from, so key order, quoting and `tags: [a, b]` flow style survive. Diff noise
  is not cosmetic: the real corruption above stayed invisible in review because
  it was buried in a 20-line reformat of a one-word change.

Still open: an entry whose on-disk value is already off-enum (`kind: refactor`)
cannot be updated at all until hand-repaired (#47).

## The suite silently disables write-time embedding, so embedding tests can pass vacuously

The **root** `conftest.py` (repo root, not `tests/conftest.py`) has an autouse
fixture `_no_auto_embed_unless_marked`. For every test that does **not** carry
`@pytest.mark.embeddings` it does two things:

```python
monkeypatch.setattr(KBService, "_get_embedding_svc", _no_model)  # returns None
monkeypatch.setenv("PYRITE_AUTO_EMBED", "0")
```

That is correct and load-bearing — it is the 3m37s → 45 s suite win.

**History, because the shape of the trap generalises even though this
instance is closed.** While #13's fix was being written, the write path still
went through `_get_embedding_svc`, so the stub sat directly on it. A test
asserting "`create_entry` imports no torch" therefore **passed on the unfixed
code** — the stub had already removed the very service the bug ran through.
The same code as a plain script took 9.9 s and imported 1277
`torch`/`sentence_transformers` modules. Green test, live bug, nothing in the
output to tell them apart.

**As of ADR-0035 that particular masking is gone:** `_auto_embed` enqueues and
never calls `_get_embedding_svc` at all, so the stub cannot hide a write-path
model load any more, and an in-process assertion about the *write* is now
honest. The fixture still governs everything that reaches the embedding
service another way, so before trusting a green test in this area, check
whether the code under test is one the autouse fixture has a hand on.

Note the env var and the stub cover *different* things: the stub reaches any
`KBService`, while `PYRITE_AUTO_EMBED=0` is applied by `_apply_env_overrides`
during `load_config()` only — a test that constructs `Settings(auto_embed=
True)` by hand is not covered by it at all.

Two ways to get an honest answer, both used by
`tests/test_writes_never_block_on_embedding.py`:

- **Run the write in a subprocess.** A cold interpreter has none of the
  suite's stubs, and its `sys.modules` is a clean measurement. It remains the
  only way to ask "did *this one write* import torch" even now the stub is out
  of the way: once any test in a worker imports torch, the in-process answer
  is permanently yes, so a module-delta assertion in a shared worker can only
  ever be measuring history.
- **Mark the test `@pytest.mark.embeddings`** when you genuinely want the real
  embedding path in-process — but then you own the ~10 s model load for that
  worker.

And to simulate a fresh install without downloading 90 MB: point `HF_HOME`
(plus `HUGGINGFACE_HUB_CACHE`, `TRANSFORMERS_CACHE`) at an empty directory and
set `HF_HUB_OFFLINE=1` / `TRANSFORMERS_OFFLINE=1`. The model load then fails
the way it would on a machine that has never seen it, in about 7 s instead of
a minute. **A timing bound alone is never enough** — every developer machine
has the model cached, which is precisely why no test ever caught #13.

## A probe script outside pytest writes the real `~/.pyrite` (#377)

The suite is isolated: the repo-root `conftest.py` exports `PYRITE_CONFIG_DIR`
(a session temp dir) and clears `PYRITE_DATA_DIR`; child processes inherit both.
A `python -c` / heredoc probe you run by hand is **not**: anything that calls
`save_config` -- `EphemeralKBService` create/expire, `RepoService`, the admin
CLI -- writes `current_config_file()`, which from a directory with no
`.pyrite/config.yaml` is `~/.pyrite/config.yaml`. On 2026-09-23 a probe of
ephemeral expiry (`ws/ephemeral/ln -> other/`, index `…/tmpXXXX/i.db`) did
exactly that and emptied a ~50-KB registry through a symlink. `save_config`
now refuses any save that drops a KB it was not told to remove, but sandbox
probes anyway -- HOME and both directories in temp dirs:

```bash
T=$(mktemp -d); HOME=$T/home PYRITE_CONFIG_DIR=$T/cfg PYRITE_DATA_DIR=$T/cfg \
  .venv/bin/python -c '...'
```

Point both at the **same** directory. `PYRITE_DATA_DIR` wins when both are set,
so with two directories `config.yaml` is read from the data dir and a seed file
put in the config dir is silently ignored -- a probe that "passes" against a
file the code never read (the #387 delta read lost its first repro this way).

`PYRITE_CONFIG_DIR` alone now moves the index too (it defaults beside
`config.yaml`); before #377 it did not.

## "Submit, then assert still active" races the background thread's own completion (#88)

A test that calls `IndexWorker.submit_sync()`/`submit_rebuild()` and, on the
*next line*, asserts the job is still `pending`/`running` (or that a second
submit's dedup check found the first job still active) is betting the
background thread hasn't finished yet. On an empty fixture KB that sync can
complete in well under a millisecond, so under CPU contention (`-n auto`
co-scheduled with another CPU-heavy file) the bet loses roughly 1 run in 5-8:
`tests/test_index_worker.py::TestGetActiveJobs::test_active_jobs_filters`
(`assert 0 >= 1`) and `TestConcurrency::test_duplicate_sync_returns_same_id`
(job ids differ because dedup didn't fire) both did, on dev's post-merge CI.
Any assertion whose truth depends on "the thread hasn't gotten there yet" is
this shape, not just these two -- `test_different_kbs_get_different_jobs` has
the identical "submit right after submit" call shape but only asserts id
*inequality*, true regardless of completion order, which is why it never
flaked.

Fix: don't race it, gate it. Patch `pyrite.services.index_worker.IndexManager`
so `sync_incremental` blocks on a `threading.Event` the test controls (see the
`gated_sync` fixture in `tests/test_index_worker.py`) -- the job is
deterministically still `running` for as long as the gate is closed, no sleep,
no timing assumption, and the test still proves dedup/filtering against a real
in-flight job rather than a synthetic one.

## A surface reaching the DB fails `test_layer_boundaries.py` (#380)

REST endpoints, MCP handlers, the CLIs and `ui/data.py` reach storage only
through a service. `tests/test_layer_boundaries.py` counts, per function and
rule, every place a DB handle *enters* a surface: `_raw_conn`, any `.db` read
(so `d = svc.db` and `getattr(svc, "db")` count), `db.<anything>` but
`close`, `get_db` under any name and `AuthService(` outside `api.py`,
`PyriteDB`, `sqlite3`, and any import or use of a `pyrite.storage` class.
Composition roots and the allowlist both pin a count per (function, rule)
and a list size, so one more reach anywhere fails. The fix is a service
method plus a provider (`api.py` for REST, `cli/context.py` for the CLI),
never a new entry: `ALLOWLIST_SIZE` only goes down.

Two traps met on the way:
- `import pyrite.cli.context` imports the whole `pyrite.cli` package (the
  Typer app and every command module): `import pyrite.admin_cli` went from
  0.05 s to 0.22 s when it did so at module level. `admin_cli.py` imports it
  inside each command instead.
- Newer FastAPI wraps `include_router` in `_IncludedRouter`, so
  `app.routes` shows 10 `APIRoute`s, not ~140. Walk the wrappers
  (`tests/_surface_inventory.rest_operations`) rather than `app.routes`.

## A model that reads a key lossily must not write it back (#557)

`Entry._base_kwargs` and every `from_frontmatter` parse some keys into
Pyrite's own shapes: `provenance` into `Provenance` (ten known sub-keys),
`sources`/`links` into dataclasses, `importance` through `safe_int`, plugin
fields through their coercions. A file whose value does not fit (a KB's own
`provenance:` block, `importance: high`) used to lose it on the next save of
any kind. `capture_extra_frontmatter` now records such keys in
`_unrepresented_keys` with the value the model serialized at load, and
`_frontmatter_for_file` puts the file's node back while the model still
serializes that value. To change such a field, **assign it** (`entry.x = ...`
clears the key in `__setattr__`) or change it in place so its serialization
differs; a writer that sets the attribute via `object.__setattr__` bypasses
the first rule and keeps the file's value if the new one serializes the same.
