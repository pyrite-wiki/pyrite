---
type: design
id: test-architecture-review-2026-10
title: "Test architecture review, 2026-10: findings, the layered target model, principles for test code"
status: accepted
date: '2026-10-14'
author: 'pyrite-architect (Claude Fable 5.1), for markr'
tags: [testing, quality, ci, process, design]
links:
- target: adr-0032
  relation: related
  kb: pyrite
- target: testing-standards
  relation: related
  kb: pyrite
- target: tests-t4-a-world-builder-in-tests-config-dir-kbs-db-and-entries-once-surfaces
  relation: related
  kb: pyrite
---

The maintainer asked (2026-10-14) for "clean readable tests, that are fast,
locate the source of an error quickly, ... a layered testing system with
additional verifications as we move towards release and a solid testing
process." This is what the test tree and the gates look like today, measured
on `dev` at a41f1dde, and what to change. Every number below came from a
command named in "How this was measured"; nothing ran the suite.

Settled positions this review does not reopen: sizes S/M/L with medium (real
wiring) as the default and large as the release gate; layered gates where a
local run need not be perfect; confirm-by-failing-test (`verify-red`); a test
that passes alone and fails under `-n` is a bug in the test. The previous
review (2026-09-25) produced `tests-t1` to `tests-t8`; of those only T1
(#770, the 600 s ceiling) has landed. This review does not restate them; it
names where they fit.

## The five findings

**1. Tests mock the service behind the entry point they drive, so a refactor
inside the service breaks tests that never asserted on it.** 195 of 428 test
files (46%) use `patch`, `Mock` or `monkeypatch.setattr`: 435 `patch(` sites,
303 `Mock(`, 413 `monkeypatch.setattr`. `load_config` alone is patched 147
times in 67 files. The worst shape stacks seven patches around one `CliRunner`
call: `tests/test_fields_acceptors.py:161-168` and `:263-268` patch
`cli_context`, `open_index_db`, `IndexManager`, `_warn_if_stale`,
`SearchService` and `_search_files` to make `pyrite search` run without a KB;
`tests/test_fields_every_format.py:159-164,184` is the same. On 2026-10-13,
a41f1dde (#847) changed which `SearchService` method the CLI calls and had to
edit `test_fields_acceptors.py`, `test_fields_every_format.py`,
`test_projected_rich_width.py`, `test_search_filters_across_modes.py` and
rewrite 257 lines of `test_plugin_semantic_search.py`, none of which were
testing search ranking. The same commit added `tests/search_verdict_seed.py`,
a real seeded KB that makes the mocks unnecessary. The rule that fixes the
class is one lint (principle 8 applied to tests), not another migration.

**2. The pre-push selection is the full suite in disguise for half of the
commits.** `scripts/test-affected` selects by import reach. For the last ten
`dev` commits it selected 391-392 of 428 files for four of them (a41f1dde,
7c2dd71b, 05063a0b: three to forty-six changed files, same result), the full
suite for one (c15f910f touched `tests/conftest.py` to add a fixture), about
200-227 for three, and 13-22 for two. The script's own docstring names the
cause: `pyrite/services/kb_service.py` reaches 167 of 281 files through
`__init__` imports. So the "feedback, not the gate" layer costs the gate's
price. The fix is selection by execution reach (a coverage context map), not
a better import graph.

**3. A red line in CI is four steps from its cause, and the first step is
scrolling past 700 lines of mypy.** `mypy` runs `continue-on-error` inside
the `test` job and prints 702-708 `error:` lines before pytest starts (jobs
115564565902, 115475155888, 115205058559). Of 17,440 `assert` lines, 2,117
(12%) carry a message. Three recent failures, worked:

- Run 38475336398 (PR #849): `test_projected_rich_width.py:88` failed on
  `assert ("┏" in result.stdout) == (columns == 80)`. The message shows a
  glyph and a table; the cause was Rich reading `COLUMNS` once at import, in
  the runner's environment, not in the diff. Fixed by the `console_width`
  fixture (`tests/conftest.py:418`). Steps from red line to cause: find the
  FAILED line under the mypy output, open the test, understand Rich, know
  the runner's terminal. Nothing in the test named the property.
- Run 38449104863 (merge queue, #844 consumed changelog fragments):
  `test_orient_is_true_as_the_first_call.py:750` raised `FileNotFoundError:
  changelog.d/66-orient-true-as-first-call.changed.md`. A test read a
  repository file outside `tests/` that another PR deleted by design. The
  message names a path, not a promise; the fix was a 15-line helper that
  looks in both places (`:731-745`).
- Run 38370201767 (PR #826): `test_mcp_sse_session.py:442`
  `TestSessionLifetime::test_session_expiry_ends_the_stream` raised
  `anyio.EndOfStream` through a 40-line `ExceptionGroup` traceback across
  anyio portals; it passed on re-run and was unrelated to the PR. The earlier
  run of the same PR (38116987922) failed in
  `test_cli_help_assertions_are_plain`, a test that lints tests, whose
  message was `tests/test_mcp_setup_reads_back.py:3134: use
  tests.cli_help.plain() before checking a CLI flag`. One step. That is the
  shape every guard should have.

**4. The same tree is tested three times and nothing records how long any
test took.** For the commits in finding 2, pre-push, the PR's `test (3.12)`
and the queue's 3.12 leg run the same ~10,500 tests on the same code. The
gating job took 632-1005 s of pytest (10.5-16.8 min) for 10,288-10,524 tests
at `-n auto` on a 4-vCPU runner; the queue's three legs finished 13-21 min
after start (run 38505895947: 3.11 in 13:15, 3.13 in 19:24, 3.12 in 20:38,
same commit), so the spread between legs is as large as the suite itself.
No job passes `--durations` or writes a junit file for the core, so "which
test is slow" has no answer today without a local run. Collection is 6.2 s
for 14,255 items; it is not the problem. The in-suite costs the previous
review measured are still there: bcrypt at cost 12
(`pyrite/services/auth_service.py:801`, `tests-t2a` not landed; 433 tests in
`test_private_kb_read_scoping.py`), 145 subprocess spawns in 47 files, 25
`time.sleep` calls in 12 files, 2 `@pytest.mark.timeout` marks under the 600
s default.

**5. The tree is big and front-loaded.** 428 files, 152,492 lines, median
244 lines, p90 707; 20 files over 1,000 lines
(`extensions/software-kb/tests/test_software_kb.py` 4,741,
`tests/test_mcp_setup_reads_back.py` 3,604, `tests/test_storage.py` 2,460).
14,255 collected items from 7,828 test functions; 6,401 are parametrised
instances and 1,782 of them belong to one function,
`tests/test_cli_outcome.py::test_the_root_is_the_only_way_out_of_a_command_on_the_contract`
(the walk over every command of three CLIs; 2,075 items in that file). 573
fixture definitions in 305 files, 19 autouse (3 in the root conftest), 56
module- or session-scoped. Setup is copied, not shared: `PyriteConfig(` is
built inline 421 times in 228 files, `PyriteDB(` 512 times, `create_app(` 167
times, and 30 files define their own `env` fixture, 27 their own `db`. The
root conftest's chain (`tmp_kb_dir` to `rest_api_env`, six deep) is used by
8-11 files per fixture. `tests-t4` (the world builder) is the answer and has
not started.

## Shape, in numbers

| Measure | Value |
|---|---|
| Test files | 428 (383 under `tests/`, 45 under `extensions/*/tests`) |
| Collected / default run | 14,255 / 14,229 (26 `slow`+`e2e` deselected) |
| Of which `experimental` (non-gating) | 2,178 |
| `core` (always run by test-affected) | 163 |
| `e2e` (dev push only) | 19, in `tests/e2e` |
| Playwright specs | 12 under `web/e2e`, manual-only in CI |
| Test functions / parametrised instances | 7,828 / 6,401 |
| Files that drive a surface | `CliRunner` 86, `TestClient` 96, MCP dispatch 70 |
| Files with mocks | 195 (46%) |
| Asserts / with a message | 17,440 / 2,117 |
| `assert x is not None` as the only check on its line | 231 |
| Lines ending in `exit_code == 0` | 159 |
| Test names over 45 characters (sentence style) | 3,001 of 8,068 |
| Generic names (`test_basic`, `test_works`, ...) | 23 |

Organisation is by behaviour in the newer files (`test_search_verdict_on_every_surface.py`,
`test_write_surface_parity.py`, `test_effective_status_everywhere.py`,
`test_default_output_is_the_result.py`) and by module in the older ones
(`test_storage.py`, `test_services.py`, `test_mcp_server.py`). Each
cross-surface file hand-rolls its own CLI, REST and MCP drivers; two
registries already exist that could feed them, `tests/outcome_walk.py`
(every command declaring `--format`, from the Typer registry) and
`tests/characterization/surfaces.py` (REST routes and MCP tools for the
goldens). Nothing joins them, so a new cross-surface property costs three
drivers and a new file every time.

## The gates today

| Gate | Runs | Time | Meant to catch | Overlap / gap |
|---|---|---|---|---|
| commit | ruff, format, hygiene, import cycles, `schema validate --changed` | seconds | malformed change | none |
| commit-msg | `fix:` touches `tests/` | instant | fix without a test | a test that does not fail without the fix (verify-red covers that later) |
| pre-push | `test-affected --run -n 4`: core + import-reach, `not experimental`; stamped per tree | unmeasured; the full-ish selection is the 9-11 min the maintainer reports | my change on my interpreter | >90% of files for half of commits (finding 2) |
| PR CI `test (3.12)` | the core suite, coverage, diff-cover advisory; mypy advisory inline; verify-red advisory; `experimental` job non-blocking | 10.5-16.8 min pytest; ~19 min job | the change on current `dev` | re-runs what pre-push ran; mypy noise in the log; no durations |
| merge queue | full matrix 3.11/3.12/3.13 + Postgres + frontend on the exact landing commit | 13-21 min wall | interpreter and backend breadth | re-runs the 3.12 leg a third time |
| dev push | `smoke` (19 live-process tests, two tutorials, 3m15s) + `experimental` ratchet (5m10s); `test`/`frontend` skipped when the queue passed the SHA | ~5 min | the assembled thing; experimental drift files an issue | nothing browser-shaped |
| main | `test` + `frontend` again; Playwright `e2e` manual-only, `continue-on-error` | | | Playwright catches nothing today |
| release (`scripts/release.py` step c) | install from SHA into a clean venv, `--version`, the getting-started tutorial, `docker build` | minutes | what a user gets | no upgrade-on-a-real-KB, no Playwright |

Flakiness has no home. The rate-limiter and index-worker classes were fixed
at the root (`tests/conftest.py:41-65`, `make_client` teardown); what remains
is handled by a re-run (`gh run rerun`, `--no-verify` locally per CLAUDE.md)
and nothing records that it happened. A same-SHA red-then-green in the queue
is the one signal that is both cheap to detect and certain to be a flake,
and no job looks for it. The `experimental` ratchet shows the mechanism
(`scripts/experimental_ratchet.py`: a list that can only shrink, an issue
filed on `dev`).

What only the release layer can catch: a wheel that misses a data file, a
console script that is not registered, a tutorial step whose output
changed. What nothing catches: a browser regression (Playwright is off), a
slow test getting slower, a golden regenerated without anyone reading the
diff (#858 shows the regenerate harness can also silently record half),
a test that reads a repository file outside `tests/`.

## How tests are written in this loop, and where it leaks

A worker writes under `pyrite-dev`: a test red on the base, then the change;
`verify-red` checks the claim on the PR (recent verdicts: `220 red / 159
import-only / 0 unexpected pass`, `51 red / 113 import-only / 23 unexpected
pass / 44 n/a`); a reviewer reads the diff and the CI; a cold read for risky
shapes. Where it produces weak tests:

- "import-only" red: a test that fails on the base because a name does not
  exist yet counts as red, though it asserts nothing about behaviour.
  `verify-red` already classifies it; nothing acts on the count.
- A test of the mock: the seven-patch shape in finding 1 passes whatever the
  service does.
- A well-named test over an uncovered case (the `a-well-named-test-...`
  item): the name says the property, the body does not check it.
- Goldens: 17 JSON files under `tests/characterization/goldens`, regenerated
  by an environment variable; review is "read the JSON diff", and #858 shows
  the harness under `-n4` writes a subset and reports success.
- Repo-file coupling: `test_orient_is_true_as_the_first_call.py` and
  `test_changelog_fragments.py` read `CHANGELOG.md`/`changelog.d` (finding 3).

What catches these mechanically is the pattern the tree already has in four
places (`test_cli_help_assertions_are_plain.py`, `test_experimental_surface.py`,
`test_dev_process_config.py::TestEveryTestIsBounded`, `test_changelog_fragments.py`):
a test that walks `tests/` and names the offending `file:line` in its message.

## The target model

One sentence: every layer runs once per tree, costs what it buys, and a red
line names the property that failed and the file that broke it.

| Layer | Runs | Budget | Must catch |
|---|---|---|---|
| editing | the file you touch; `test-affected --run` selecting by execution reach | under 2 min median for a one-module change | my own mistake |
| pre-push | same selection, stamped | under 4 min median; full suite only for infra | what my change reaches |
| PR | core suite once on 3.12, `--durations=25`, junit kept; mypy in its own advisory job; verify-red; experimental ratchet | under 10 min pytest | the change on current `dev`; a slow test getting slower |
| queue | 3.11 and 3.13 plus Postgres and frontend (3.12 already proven on this merge base when the PR is up to date) | under 15 min wall | breadth |
| dev push | smoke + tutorials + experimental ratchet + flake detector (same-SHA rerun pass files an issue) | under 6 min | the assembled thing; drift |
| main / release | install from tag, tutorials, Playwright once deterministic, upgrade on a real KB | minutes, once | what a user gets |

Each budget is a target to measure against, not a claim; the first theme
below makes the measurement exist.

## Principles for test code

1. **Drive the surface, seed the world.** A test of a CLI command, a REST
   route or an MCP tool patches nothing under `pyrite.services` or
   `pyrite.storage`; it seeds a KB (`tests/search_verdict_seed.py`,
   `tests/auth_seed.py`, the T4 builder when it lands) and reads the result.
   A patch is for the edge of the process: the network, the clock, a model
   download, another program.
2. **A failure names the property.** Cross-surface and parametrised tests
   assert with a message that says what was promised and on which surface;
   a guard test that walks `tests/` reports `file:line` and the rule.
3. **One registry, many surfaces.** A property that holds on CLI, REST and
   MCP is written once and parametrised over a registry row; the registry
   has a completeness test, so a new command or tool with no row fails.
4. **No test reads the repository.** Fixtures live under `tests/**/fixtures`;
   a test that needs a doc or a changelog as input copies it there or
   generates it. Docs-as-tests (`scripts/run_tutorial.sh`,
   `tests/test_doc_write_as_patch.py`) are the exception and say so.
5. **Every wait has a deadline and every deadline names itself.** Barriers
   and `wait_for_idle(timeout)` that report expiry, never `sleep`; T1 and
   T7 own this.
6. **Rules about tests are tests.** A convention is a test under
   `tests/test_tests_*.py` with an allowlist that can only shrink, or it is
   not a rule yet.
7. **A file is one behaviour and fits in a reviewer's head.** New files stay
   under 800 lines; a parametrised matrix that outgrows that moves its
   cases to data.

## The plan, in order

1. `tests-q1-no-test-mocks-the-service-behind-the-entry-point-it-drives` (sonnet, first: it removes the class that broke three files on 2026-10-13 and costs a day)
2. `tests-q2-the-red-line-is-the-first-thing-in-the-log-and-every-run-leaves-a-timing-record` (sonnet)
3. `tests-q3-a-weak-test-fails-a-lint-assert-free-is-not-none-only-sleep-and-repo-file-reads` (sonnet)
4. `tests-t2a-...` (existing, S, the single largest CPU win still unclaimed)
5. `tests-q4-test-affected-selects-by-execution-reach-not-import-reach` (opus)
6. `tests-t4-...` (existing, L, the world builder; Q5 depends on it)
7. `tests-q5-one-surface-registry-feeds-every-cross-surface-test` (opus, after T4)
8. `tests-q6-a-flake-is-recorded-not-re-run` (sonnet)
9. `tests-q7-a-golden-changes-only-with-its-diff-in-the-review` (sonnet, fixes #858)
10. `tests-q8-a-test-file-fits-in-a-head-800-line-budget-and-the-three-largest-split` (sonnet, last)

## How this was measured

- Counts: `pytest --collect-only -qq tests/ extensions/` (6.2 s; `-m` for
  `experimental`, `core`, `e2e`); `wc -l` over `test_*.py`; `grep -c` for
  the patterns quoted above, on `dev` a41f1dde.
- Selection: `scripts/test-affected --list --files <changed files of each
  commit>` for the last ten `dev` commits.
- CI: `gh run view --json jobs` for runs 38505895947 (queue), 38497137005
  (dev push), 38475336398, 38449104863, 38370201767, 38116987922 (failures);
  `gh run view --job <id> --log` for the tracebacks and the mypy line counts.
- Not measured, and the command that would: local suite wall time and the
  slowest 50 tests (`pytest tests/ extensions/ -n 4 --durations=50`); pre-push
  median (`scripts/test-affected --run` timed over ten pushes); fixture
  setup cost (`pytest --setup-show` on one file); the queue's per-test
  durations (no job writes them; Q2 adds it).

This review is wrong if the 2026-10-13 breakage was caused by the seeded KB
rather than the mocks (check: `git show a41f1dde -- tests/test_fields_acceptors.py`
removes `patch("pyrite.services.search_service.SearchService")` lines), or
if `test-affected` reach is already dominated by the `core` set rather than
`kb_service` (check: `--explain` on 7c2dd71b).
