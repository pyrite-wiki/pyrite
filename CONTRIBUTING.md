# Contributing to Pyrite

Thank you for considering contributing to Pyrite! This guide will help you get started.

If you find Pyrite valuable, please also [star the repository](https://github.com/pyrite-wiki/pyrite).
It costs nothing, and it is one of the signals people use to decide whether a
project is worth their time.

## Development Setup

### Prerequisites

- Python 3.11+ (3.13 recommended)
- Git
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

### Initial Setup

```bash
# Clone the repository
git clone https://github.com/pyrite-wiki/pyrite.git
cd pyrite

# Create virtual environment and install dependencies
uv venv
source .venv/bin/activate  # or `.venv\Scripts\activate` on Windows
# `.[all]` is the whole optional surface (CLI, server, MCP, AI) plus the test
# tooling. `.[dev]` alone is tooling only and cannot even collect the suite.
uv pip install -e ".[all]"

# Install all extensions (required for full test suite)
for ext in extensions/*/; do uv pip install -e "$ext"; done

# Install the git hooks (commit, commit-msg and pre-push in one go)
pre-commit install

# Verify installation
.venv/bin/pytest tests/ extensions/*/tests/ -q
```

Pytest prints the current collected and passed test counts in its summary;
they change as the project and extensions grow.

See [Setting Up the Development Environment](kb/runbooks/setting-up-dev-environment.md) for troubleshooting.

`scripts/setup-checkout.sh` does the venv, the extensions and a repo-local
`.pyrite/config.yaml` (so `pyrite -k pyrite` means this checkout's `kb/`) in
one step; `scripts/new-worktree.sh <branch>` creates a worktree and runs it.

### Developing in Claude Code on the web

A cloud session at [claude.ai/code](https://claude.ai/code) comes in two
halves: a **setup script** you paste into the environment once (the slow part,
cached as a snapshot), and a **SessionStart hook** in the checked-in
`.claude/settings.json` (the cheap part, every session).

**Create the environment** (environment settings at claude.ai/code): keep the
default "Trusted" network level, and paste this one line as the setup script:

```bash
curl -fsSL https://raw.githubusercontent.com/pyrite-wiki/pyrite/dev/scripts/cloud-env-setup.sh | bash
```

It builds `.venv` (pyrite and every in-repo extension), the pre-commit hook
environments and pulls the Postgres image, in parallel (9 s on a laptop with a
fast network), far inside Anthropic's five-minute cache limit. Skip it and sessions still work, only
slower: the hook then does the install itself.

**What the hook does** (`scripts/cloud-session-start.sh`; nothing outside a
cloud session): reinstalls only if a `pyproject.toml` changed since the
snapshot, writes `.pyrite/config.yaml` so `pyrite -k pyrite` means this
checkout's `kb/`, syncs the index, installs the git hooks, puts `.venv/bin` on
`PATH`, and starts Postgres.

- **Postgres tests run here.** The hook starts a `pgvector/pgvector:pg16`
  container (the image, user, password and database CI uses, so a cloud pass
  predicts a CI pass) and exports `PYRITE_TEST_PG_URL` once it accepts
  connections. If Docker or the container fails, the session starts anyway
  with one warning and those tests skip. Set `PYRITE_SETUP_POSTGRES=0` in the
  environment's variables to opt out. Details: `scripts/cloud-postgres.sh`.
- **Embeddings are off by default**: `sentence-transformers` pulls torch; the
  tests that need it skip. For them, set `PYRITE_SETUP_EXTRAS=all,postgres`
  and add `huggingface.co` to the environment's allowed domains.
- **The VM has 4 vCPUs and 16 GB RAM:** run one test suite at a time, with
  `-n 4` (the pre-push hook already does).
- **Frontend:** run `npm ci` in `web/` when your change touches it.
- **Branches:** the session's own branch is fine for a PR to `dev`; name it
  `fix/*` or `feature/*` if you create one yourself. One session is one
  checkout, so there is no worktree to make.

## Code Standards

### Style

We use [ruff](https://docs.astral.sh/ruff/) for linting and formatting:

```bash
# Check for issues
ruff check pyrite/

# Auto-fix issues
ruff check --fix pyrite/

# Format code
ruff format pyrite/
```

### Type Hints

- Use modern Python type hints (3.11+ syntax)
- `list[str]` not `List[str]`
- `str | None` not `Optional[str]`
- `dict[str, Any]` not `Dict[str, Any]`

### Imports

- Use absolute imports within the package
- Group imports: stdlib, third-party, local
- Let ruff sort imports automatically

## Architecture

### Layer Structure

```
pyrite/
├── models/          # Data models (Entry, core types, factory)
├── storage/         # File and database operations
│   ├── database.py  # PyriteDB: SQLAlchemy ORM + mixin-based modules
│   ├── repository.py # KBRepository: markdown files with YAML frontmatter
│   ├── index.py     # IndexManager: builds/syncs index from markdown
│   ├── migrations.py # MigrationManager: custom schema versioning
│   └── backends/    # SearchBackend protocol + implementations
│       ├── protocol.py        # SearchBackend structural protocol
│       ├── sqlite_backend.py  # SQLiteBackend (default: FTS5 + sqlite-vec)
│       └── postgres_backend.py # PostgresBackend (server: tsvector + pgvector)
├── services/        # Business logic (search, KB ops, QA, schema, etc.)
├── plugins/         # Plugin protocol, registry, context (DI)
├── cli/             # CLI commands (Typer sub-apps)
├── server/
│   ├── api.py       # FastAPI app factory
│   ├── endpoints/   # Per-feature REST endpoint modules
│   └── mcp_server.py # Three-tier MCP server
├── formats/         # Content negotiation (JSON, Markdown, CSV, YAML)
└── utils/           # Shared utilities (yaml, markdown)
```

### Entry Points

| Command | Module | Purpose |
|---------|--------|---------|
| `pyrite` | `pyrite.cli:main` | Full CLI (Typer) — primary interface for humans and agents |
| `pyrite-read` | `pyrite.read_cli:main` | Read-only CLI (safe for untrusted agents) |
| `pyrite-admin` | `pyrite.admin_cli:main` | Admin CLI (DB management, config) |
| `pyrite-server` | `pyrite.server.api:main` | REST API + web UI server |

### Key Principles

1. **Service Layer**: Business logic lives in `services/`, not in CLI/API/MCP handlers
2. **Repository Pattern**: File operations go through `KBRepository`
3. **Two-Tier Durability**: Markdown files (git) = source of truth, SQLite/Postgres = derived index
4. **Plugin Protocol**: Extensions use structural typing (Protocol) — no base class inheritance required
5. **SearchBackend Abstraction**: All search operations go through `SearchBackend` protocol (SQLite or Postgres)

## Running the tests

Pyrite is written largely by AI agents and runs on its users' machines, so
most tests are **medium**: agents build units that pass alone and do not work
together, and a user's machine is production, with no rollback. New
behaviour, every fix and every guard is tested through the real wiring, so a
guard's test fails when the guard is mis-wired, not only when its logic is
wrong.

| Size | What | For | Runs |
|---|---|---|---|
| Small | one process, no I/O | pure logic with a large input space (parsers, schema validation, query sanitizing, selection rules), where medium is too slow to cover it | editing, pre-push, PR CI |
| **Medium** (default) | one machine, the real wiring: SQLite, temp dirs, `TestClient`, the CLI via Typer, MCP dispatch, git in temp repos, subprocesses | new behaviour, fixes, guards | editing, pre-push, PR CI |
| Large | several processes or a browser | the release gate, standing in for the production monitoring a hosted service would have: live server + MCP smoke, the tutorial, Playwright, install from the tag, upgrade on a real KB | `dev` after merge, manual, release |

`scripts/test-affected` picks tests by what a change can reach, not by size.
Tests carry no size marker yet.

**Everything**

```bash
.venv/bin/pytest tests/ extensions/ -n 4       # backend, ~6,000 tests, ~10 min on a busy laptop
cd web && npm ci && npm run check && npm run test:unit && npm run build   # frontend
HF_HUB_OFFLINE=1 .venv/bin/pytest tests/e2e -m e2e -n 4 --dist loadfile   # large: real server and MCP processes
PATH="$PWD/.venv/bin:$PATH" bash scripts/run_tutorial.sh                  # large: docs/getting-started.md as a test
PATH="$PWD/.venv/bin:$PATH" bash scripts/run_tutorial.sh docs/tutorials/pyrite-in-20-minutes.md   # large: the 20-minute tutorial as a test (clones the demo KBs)
cd web && npx playwright install chromium && npm run test:e2e             # large: browser; manual-only in CI while non-deterministic
```

Use `-n 4`, not `-n auto`, on a laptop: `-n auto` starts a worker per core,
each with its own databases and temp trees, and several worktrees doing that
at once ran a 16 GB machine out of memory (#168) and filled its disk (#356).

**A subset**

```bash
.venv/bin/pytest tests/test_storage.py -n 4                         # one file
.venv/bin/pytest tests/test_storage.py -k search                    # matching tests
.venv/bin/pytest "tests/test_rest_api.py::TestKBEndpoints::test_list_kbs"   # one test
.venv/bin/pytest extensions/software-kb -n 4                        # one extension
.venv/bin/pytest tests/ extensions/ -m core -n 4                    # the core smoke set
scripts/test-affected --list          # what your branch affects, against origin/dev's merge base
scripts/test-affected --explain       # ... and why each test was chosen
scripts/test-affected --run           # run it: core + affected, -n 4 (-n N or -n auto)
```

`test-affected` diffs your working tree, uncommitted and untracked files
included, against the merge base with `--base` (default `origin/dev`;
`--committed` ignores uncommitted work). It selects every test that imports,
through any chain, a module you changed or uses a conftest fixture that does,
plus the tests that load plugins when an extension changes, plus the `core`
set; and it switches to the full suite when you touch `conftest.py`,
`pyproject.toml`, pytest or hook configuration, CI workflows or
`tests/**/fixtures/`. Its known limit is import-time reach: importing
`pkg.x` runs `pkg/__init__.py`, which it does not follow, so a change to
`pyrite/services/kb_service.py` selects 167 of the 281 test files that
execute it on import. CI catches the rest.

**Experimental tests** (#657). A red required check means the core broke.
Tests of the surfaces `kb/designs/alpha-supported-surface.md` calls
experimental (the extensions, tasks, REST and the web UI, `/site`, `/ws`, the
AI endpoints, MCP prompts and resources and the non-core tools, Postgres, the
overlay backend and worktrees, the `repo`/`auth`/`extension`/`export`/
`collections` CLI groups) carry the `experimental` marker. Nobody writes it
by hand: `tests/experimental_surface.py` maps paths and node ids to surfaces,
and the root `conftest.py` applies it. Security properties (authorization,
read scoping, containment of paths, credential handling, the characterization
oracle, escaping) are never experimental, whatever surface they go through;
the same file lists them, and a test checks the mapping against a real
collection. The gate decides, not a test's name: an experimental test whose
body touches that vocabulary (paths leaving a root, private or readable sets,
read-only, redaction, credentials, escaping, tiers) fails
`tests/test_experimental_surface.py` until it is listed as security
(`NEVER_EXPERIMENTAL`) or reviewed with a reason (`REVIEWED_EXPERIMENTAL`).

```bash
.venv/bin/pytest tests/ extensions/ -n 4 -m "not slow and not e2e and not experimental"   # the core, as CI's gating job runs it
.venv/bin/pytest tests/ extensions/ -n 4 -m "experimental and not slow and not e2e"       # the experimental set
scripts/test-affected --run --experimental      # the pre-push selection, experimental tests included
```

A plain `pytest` runs both. `scripts/test-affected --run`, and so the
pre-push hook, runs the core only (`PYRITE_PUSH_EXPERIMENTAL=1` or
`--experimental` includes the rest). In CI the `test` job, which `gate`
needs, runs the core; the `experimental` job runs the rest on every PR and on
`dev`, does not block a merge, and is red only for news: a failure in neither
`tests/experimental_known_failures.txt` nor an open `experimental-broken`
issue, a PR that adds to that list (it can only shrink), or a run that did
not complete. On `dev` each of those opens or updates an `experimental-broken`
issue. A file that fails to import stops the core run only if it holds a core
or security case; one whose tests are all experimental is a warning there and
a failure in the experimental job. `test-affected --run` says how many
experimental tests it left out. A new test of an experimental surface needs
no marker if its path is mapped; a new file for one needs a line in
`tests/experimental_surface.py`.

**Each tree is tested once.** A `--run` that passes on a clean tree (nothing
uncommitted in tracked files, no untracked `.py` file, and no untracked or
gitignored file under `pyrite/`, `tests/`, `extensions/`, `kb/` or `scripts/`
beyond `__pycache__`-style build output), using the worktree's own `.venv`,
records a stamp: the
tree's SHA, the Python major.minor and the tests it ran, under the git common
directory, so every worktree of the repository shares it. A later `--run` --
the pre-push hook's included, which looks up the tree of the ref being pushed
-- whose tests the stamp covers prints `already passed on tree <sha> (...);
skipping` and exits 0. A dirty tree is never stamped and never skips;
failures are never stamped, and neither is a run with `PYTEST_ADDOPTS` or
`PYTEST_PLUGINS` set; `--force` or `PYRITE_PUSH_FORCE=1` ignores stamps.
Stamps older than 14 days are ignored and pruned. A stamp is keyed on the
tree and the Python version, not the environment: a pass where the Postgres
tests skipped (`PYRITE_TEST_PG_URL` unset) covers a later run where it is
set. When what changed is the environment, use `--force`.

**After a failure that looks load-caused** (a timeout, a flaky port), re-run
only what failed rather than the whole selection:

```bash
scripts/test-affected --run -- --lf    # anything after -- goes to pytest
```

A `--lf` pass is not stamped, since only part of the selection ran; when it
is green, the next full `--run` (or the push) runs the selection once more
and stamps it.

**When to test what**

| When | Run |
|---|---|
| While editing | the test file you are changing; `scripts/test-affected --run` |
| Before a commit | nothing extra: the commit hooks run ruff and the fast checks in seconds |
| Before a push | the pre-push hook runs `scripts/test-affected --run` on `PYRITE_PUSH_WORKERS` (default 4) workers, unless your own `--run` already passed on the tree being pushed (see *Each tree is tested once* below). It already runs everything for conftest, fixtures, pyproject and config changes; set `PYRITE_PUSH_FULL=1` yourself for storage or migration changes and cross-cutting refactors |
| On the pull request | nothing: CI runs the backend suite on one interpreter (all three Pythons when test infrastructure changes), KB validation, the frontend job when `web/` changes, the advisory `verify-red` job (your new tests, without and with your change) and diff coverage; the merge queue runs the full three-Python matrix on the exact commit about to land (#400); the push to `dev` after the merge runs the e2e smoke and the tutorial, and the matrix and frontend again only if the queue did not already pass that SHA |
| A frontend change | `cd web && npm run check && npm run test:unit && npm run build` |
| A release | the large tests: `scripts/release.py` installs the release commit into a fresh venv and runs the tutorial against it; Playwright and the smoke layer per the release runbook |

**What CI guarantees:** no pull request merges until the full backend suite
has passed on top of current `dev`. Run the full suite locally to reproduce a
CI failure, not out of habit.

The suite does not load the embedding model unless a test is marked
`@pytest.mark.embeddings`; everything else runs with `auto_embed` off. A test
that passes alone but fails under `-n auto` is a bug in that test (shared
state, a fixed wall-clock timeout, an unclosed database), not a reason to run
serially — `tests/test_task_claim_concurrency.py` shows the pattern for
process-spawning tests.

**The runner itself is pinned.** `pytest`, `pytest-cov` and `pytest-xdist`
are exact `==` pins in the `dev` extra (#128) — not a floor like the rest of
the project's dependencies — so every worktree venv and every CI leg run the
identical version. An unbounded `pytest>=8.0.0` once resolved 9.1.1 on one
interpreter and 9.0.2 on another; a change that passed the PR gate under one
version broke `dev` under the other. `tests/test_dev_process_config.py`
asserts the pins stay exact, so loosening one is a visible diff, not a silent
drift on the next `uv pip install`. After changing a pin, refresh your
worktree's venv and confirm it took:

```bash
uv pip install --python .venv/bin/python -e ".[all,dev]"
.venv/bin/python -m pytest --version
```

### Writing Tests

- Tests live in `tests/`; extension tests in `extensions/<name>/tests/`
- Name test files `test_*.py`
- A `fix:` commit must include a test that fails without the fix (a hook checks
  that it touches `tests/`)
- Use pytest fixtures for common setup (`tmp_kb`, isolated plugin registry);
  close any `PyriteDB` you open
- Use `in` not `len` for registry assertions (see [testing standards](kb/standards/testing-standards.md))

## Branches, hooks and pull requests

**Branches** (ADR-0025 and ADR-0032):

- `dev` is the default branch and where work integrates. `main` is releases
  only and moves by fast-forward to a commit that already passed CI.
- Branch from `dev`, open the PR against `dev`. Rebase is the default merge
  method (it keeps your commits and their messages); squash is fine for a
  branch whose history is noise.
- A PR merges when its checks are green **on top of current `dev`** — one
  required check, `gate`, summarizes the Python suite, the frontend build and
  KB validation (whichever the change needs), and the branch must be up to
  date. If `dev` is broken, nothing merges until it is fixed.

**Git hooks** — `pre-commit install` installs all three:

| Stage | Runs | Takes |
|---|---|---|
| commit | ruff, formatting, file hygiene, import-cycle check, KB schema validation | seconds |
| commit-msg | a `fix:` commit must touch `tests/` | — |
| pre-push | `scripts/test-affected --run`: core + affected tests on `PYRITE_PUSH_WORKERS` (default 4) workers, only when the push touches code, tests, scripts or test config (a docs- or KB-only push skips it); `PYRITE_PUSH_FULL=1` runs the full suite | Seconds to minutes, depending on what changed |

CI runs the same checks plus the full Python matrix, Postgres, the frontend
build and Playwright, so the pre-push run does not have to be perfect. If it
fails in a test your change does not touch, re-run that test alone
(`scripts/test-affected --run -- --lf`); if it passes, push with `--no-verify`
and say so in the PR, and CI will decide. Name the test in the PR; if CI fails it too, it is a real failure your change caused, even in a test you did not touch. A failure in a test your change
touches is a real failure: fix it.

**Claiming an issue:** before you spend more than an hour on an issue, say so
on the issue: two or three lines on how you'll fix it and what test proves
it. A draft PR with that in the body is even better — we'll steer you there
before you write much, and the draft is visible to everyone else looking at
the issue. The plan scales with the change: for a `good first issue` one
sentence is enough ("I'll add `id`/`kb_name` to the three projections and a
test per command").

A claim with no PR after 5 days lapses. Two people on one issue is fine — the
first PR that meets the acceptance criteria merges, and we credit the other
in the changelog entry for the change.

A placeholder commit or file is not a claim, and we don't merge placeholders.

Push as you go. A fix that exists only on your machine cannot be reviewed,
and "it's in a local commit" has cost a round trip more than once.

**Pull requests:**

1. `git checkout -b fix/what-it-fixes dev` (or `feature/...`)
2. Write the failing test, then the fix
3. Push; the pre-push hook runs the affected tests (CI runs the full suite)
4. Open the PR against `dev` and fill in the template (`Fixes #N` for bugs)
5. Expect a first response **within 72 hours**. If you have heard nothing
   after that, comment on the PR — it is a lapse, not a verdict, and saying
   so is doing us a favour.

Commit messages use conventional commits (`feat:`, `fix:`, `docs:`, `test:`,
`refactor:`, `ci:`, `kb:`).

## Where work is tracked

Two places, one rule — an item lives in exactly one of them (ADR-0033):

- **Bugs and requests → [GitHub Issues](https://github.com/pyrite-wiki/pyrite/issues).**
  Anyone can file one; use the templates. Issues labelled
  [`good first issue`](https://github.com/pyrite-wiki/pyrite/labels/good%20first%20issue)
  are small, well-specified and a fine place to start.
- **The roadmap → `kb/`** in this repo: epics, planned work and architecture
  decisions, browsable with the tool itself:

  ```bash
  pyrite sw backlog        # planned work, by priority
  pyrite sw adrs           # architecture decision records
  pyrite sw components     # module documentation
  ```

  `kb/roadmap.md` is the plan for the next releases. A request we accept gets a
  roadmap item that links back to the issue.

**Contributing a fix.** The PRs that merge fastest here look like this — and
the ones that arrived on 2026-09-18 from four first-time contributors all did:

- One issue per PR, and the diff stays inside it. A stray hunk from another
  project (an editor's `.gitignore` additions, say) is the most common thing a
  review asks to remove.
- A test that fails without the fix. CI checks it for you: the advisory
  **`verify-red`** job checks out the merge base in a throwaway tree, adds
  your test files, runs the tests you added or edited, then adds the rest of
  your change and runs them again. Its summary line, e.g.
  `verify-red: 3 red · 0 import-only · 1 unexpected pass · 0 n/a`, is on the
  run's summary page, with a row for each test that is not *red*.
  *red* is what a review wants to see. *import-only* is weaker: the test
  fails only because a name your change adds is missing, which shows it needs
  your code, not that it checks what the code does. *unexpected pass* leaves
  a warning on the test: it does not exercise the change. If it is a
  deliberate "this still works" guard, mark it
  `@pytest.mark.control(reason="...")` and it counts as a control instead. *n/a* means no claim (it skipped, failed with
  the fix, or timed out). `scripts/verify-red.sh` runs the same check
  locally; it never modifies your checkout, and it includes uncommitted work.
- Diff coverage: on a pull request the 3.12 test run measures coverage, and
  `diff-cover` lists the lines you changed that no test runs (target 80%).
  Like `verify-red` it is advisory for now; see [docs/testing.md](docs/testing.md).
- The affected tests green locally (`scripts/test-affected --run`), plus
  `ruff check` and `ruff format --check`. CI runs the full suite on the PR.
- A changelog fragment: a **new file** `changelog.d/<slug>.<section>.md`
  containing the bullet as it should read in the release notes. Do **not** edit
  `CHANGELOG.md` — it is the one file every pull request used to conflict on,
  and a fragment has a name nobody else picks, so two branches in flight cannot
  collide. `changelog.d/README.md` lists the sections and shows an example; the
  release script assembles the fragments when the release is cut.

  If your change touches `pyrite/` or `extensions/` and adds no fragment, CI
  leaves a **warning** on the pull request. It is a reminder, not a gate —
  nothing is blocked, and no one has to argue with a script about whether a
  change is user-visible. When it genuinely isn't one (a refactor with no
  behaviour change), say so in the pull-request description and the warning
  goes away:

  ```
  Changelog: none
  ```
- AI-assisted contributions are welcome here. If an AI coding agent wrote or
  co-wrote your change, declare it with a `Co-authored-by:` trailer on the
  commit (most agent tools add this automatically) — it is machine-readable,
  it survives a squash-merge, and it is the form we'll look for first. A
  mention in the PR body is welcome too, and helps the reviewer know what to
  look at first, but it's optional on top of the trailer, not instead of it.

What happens next: an outside PR is reviewed ahead of the maintainer's own
work, and the review is posted as a comment with a plain recommendation (merge
as is, merge after listed changes, or which of two competing PRs and what to
credit from the other). In practice that has often been within the hour, but
the hour is a happy accident of when you caught us — this is one maintainer
in one timezone who sleeps. **72 hours is the limit**, and it is borrowed from
the Apache Way: there, a proposal that draws no objection in 72 hours is taken
as having consent, on the principle that silence must have a deadline or it
becomes a veto. Here the same clock runs the other direction — it bounds how
long *you* should have to wait for an answer, rather than how long we may
wait for one. Past 72 hours, ping the PR.

First-time contributors' CI runs wait for a maintainer to approve them; that
is a GitHub safety default, not a judgement. A maintainer may
rebase your branch or push one small, credited fixup commit to it with a
comment saying what changed — your commits and authorship stay yours. The
merge itself is the maintainer's click.

**Security issues:** never in a public issue — see [SECURITY.md](SECURITY.md).

**Private material:** this repository and its KB are public. Placeholders, not
names, for anything that is not yours to publish; no absolute home paths.

## Project Configuration

- KB config: `kb.yaml` in each KB directory
- Claude Code skill: `.claude/skills/pyrite-dev/SKILL.md`
- Plugin developer guide: `kb/notes/plugin-developer-guide.md`
- Architecture docs: `kb/components/` and `kb/adrs/`

## Getting Help

- A bug or a feature request: open an issue
- A question about how something works: open an issue with the `question` label
- Something you found by deploying it: that is the most valuable report there
  is — see the v0.24.1 notes for the three that shaped that release

## Contributors

Maintainer: Mark Ramm (BDFL; see ADR-0032). Contributors are credited by
name in the release notes of the release their work ships in (the runbook
lists every outside author of a merged PR), in the changelog entry for the
change, and in the README.

## License

By contributing, you agree that your contributions will be licensed under the same license as the project.
