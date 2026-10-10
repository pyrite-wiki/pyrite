"""Read/write the golden JSON files under `tests/characterization/goldens/`.

One file per surface (`rest.json`, `mcp.json`, `error_bodies.json`), each a
JSON object keyed by a stable, sorted string key so a diff is one line per
changed case, not a reshuffled file -- EXCEPT that a surface whose full
golden would exceed the repo's 500KB `check-added-large-files` pre-commit
limit is sharded into `{name}.{shard}.json` files instead (`load`/`save`
below do this transparently: callers still pass one `name` and get one
merged `dict` back). `rest`/`mcp` shard by principal (one of `world.py`'s
seven principal names is always the middle segment of every key here, so
this needs no new grouping logic); `error_bodies` (12KB) never shards.
Regeneration (`PYRITE_CHARACTERIZATION_REGENERATE=1 pytest
tests/characterization/ -n 0`, documented in each test module's docstring)
writes these files; every other run only reads and compares. **Never gated
to run in CI** -- the regenerate path is opt-in per the environment
variable, off by default, and nothing in `scripts/test-affected`, the
pre-push hook or CI sets it.

**`save` replaces the file's keys in its scope, under a lock.**
`test_rest_matrix.py` and `test_mcp_matrix.py` parametrize one test per
principal. Regeneration must run serially: the root pytest hook refuses an
opted-in run with xdist workers before collection or writes. Normal golden
comparison remains safe to run in parallel. The scoped read/merge/write
below still preserves each principal's previously recorded cases.
A blind merge-only overwrite is a lost-update race across workers (worker
B's save must not erase worker A's already-written keys for A's own
principal) AND leaves a route/tool that dropped out of the LIVE run set
(a renamed scoping dependency, say) sitting in the file forever, looking
exactly like still-covered ground (#476 blocker 1). So `save` takes a
`principal_scope` and, within that scope only, keeps exactly the keys this
run reproduced -- a key nothing produced this run is deleted, not left
behind -- while every OTHER principal's shard/keys are untouched. `save`
re-reads the file immediately before writing, inside a cross-process
advisory lock (`fcntl.flock`, POSIX-only -- matching this repo's dev
platforms), as an additional safeguard for independent processes.
"""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
from typing import Any

GOLDEN_DIR = Path(__file__).parent / "goldens"
REGENERATE_ENV = "PYRITE_CHARACTERIZATION_REGENERATE"

# Surfaces whose one-file golden would exceed the repo's 500KB
# check-added-large-files pre-commit limit -- sharded by principal instead
# (the middle " | "-delimited segment of every key in these two surfaces is
# always one of world.py's seven principal names).
_SHARDED_SURFACES = {"rest", "mcp"}


def regenerating() -> bool:
    return os.environ.get(REGENERATE_ENV) == "1"


def _shard_key(key: str) -> str:
    """The principal name segment of a sharded surface's key
    (``"{route/tool} | {principal} | {kb_state}"``) -- the shard a key
    belongs to."""
    parts = key.split(" | ")
    return parts[1] if len(parts) >= 2 else "_other"


def _shard_paths(name: str) -> list[Path]:
    return sorted(GOLDEN_DIR.glob(f"{name}.*.json"))


def load(name: str) -> dict[str, Any]:
    if name in _SHARDED_SURFACES:
        merged: dict[str, Any] = {}
        for path in _shard_paths(name):
            merged.update(json.loads(path.read_text()))
        return merged
    path = GOLDEN_DIR / f"{name}.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def save(name: str, data: dict[str, Any], *, principal_scope: str | None = None) -> None:
    """Replace `{name}.json` (or, for a sharded surface, each
    `{name}.{shard}.json`) with `data`'s keys, under a lock.

    A merge that only adds/updates (what this used to do) can never catch a
    dropped route: a migration that renames the dependency `surfaces.py`
    filters on makes the live run set shrink, so `data` simply never
    contains that route's keys again -- and a pure `on_disk.update(data)`
    leaves the OLD key sitting in the file forever, looking exactly like a
    still-covered case (found in cold review, #476 blocker 1). So a
    regenerate run now DELETES every on-disk key in its own scope that
    `data` did not also produce.

    `principal_scope`, when given, is a principal name: only keys whose
    " | "-delimited middle segment equals it are eligible to be deleted (a
    single principal's parametrized regenerate run must not delete another
    principal's keys just because it did not touch them -- `test_rest_matrix
    .py`/`test_mcp_matrix.py` each pass their own `principal_name` here).
    Without it (the unsharded `error_bodies` surface, which regenerates in
    one pass with every key in scope), every on-disk key is eligible.
    """
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    if name in _SHARDED_SURFACES:
        by_shard: dict[str, dict[str, Any]] = {}
        for key, value in data.items():
            by_shard.setdefault(_shard_key(key), {})[key] = value
        # A shard that produced no keys this run (e.g. a principal whose
        # test didn't run in this invocation) is not visited at all -- only
        # shards this run actually touched get their stale keys pruned.
        for shard, shard_data in by_shard.items():
            _save_one(GOLDEN_DIR / f"{name}.{shard}.json", shard_data, scope_key=principal_scope)
        return
    _save_one(GOLDEN_DIR / f"{name}.json", data, scope_key=principal_scope)


def _in_scope(key: str, scope_key: str | None) -> bool:
    if scope_key is None:
        return True
    return _shard_key(key) == scope_key


def _save_one(path: Path, data: dict[str, Any], *, scope_key: str | None) -> None:
    lock_path = path.with_suffix(path.suffix + ".lock")
    with open(lock_path, "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            on_disk: dict[str, Any] = {}
            if path.exists():
                on_disk = json.loads(path.read_text())
            # Drop every on-disk key in scope that this run did not
            # reproduce -- a stale key (the route/tool this run no longer
            # sees) must disappear, not linger looking covered.
            kept = {k: v for k, v in on_disk.items() if not _in_scope(k, scope_key) or k in data}
            kept.update(data)
            path.write_text(json.dumps(kept, indent=2, sort_keys=True) + "\n")
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def assert_matches(name: str, key: str, actual: Any, golden: dict[str, Any]) -> None:
    """Compare `actual` against `golden[key]`. In regenerate mode, records
    instead of comparing (the caller writes the whole dict once at the end
    of the module via `save`)."""
    if regenerating():
        golden[key] = actual
        return
    assert key in golden, (
        f"{key!r} has no golden recorded. Run with "
        f"{REGENERATE_ENV}=1 to (re)generate tests/characterization/goldens/{name}.json, "
        f"review the diff, and commit it as its own reviewed change."
    )
    assert actual == golden[key], (
        f"golden mismatch for {key!r}:\n  golden:  {golden[key]}\n  actual:  {actual}"
    )


class MismatchCollector:
    """Collect every golden mismatch in a run instead of raising on the
    first one (#476 blocker 7): a policy change that affects many routes
    should show every key it changed in one failure, not just the
    alphabetically-first one a bare `assert` would stop at."""

    def __init__(self) -> None:
        self.failures: list[str] = []

    def check(self, name: str, key: str, actual: Any, golden: dict[str, Any]) -> None:
        if regenerating():
            golden[key] = actual
            return
        if key not in golden:
            self.failures.append(
                f"{key!r} has no golden recorded. Run with {REGENERATE_ENV}=1 to "
                f"(re)generate tests/characterization/goldens/{name}.json."
            )
            return
        if actual != golden[key]:
            self.failures.append(
                f"golden mismatch for {key!r}:\n  golden:  {golden[key]}\n  actual:  {actual}"
            )

    def assert_clean(self) -> None:
        if self.failures:
            raise AssertionError(
                f"{len(self.failures)} golden mismatch(es):\n\n" + "\n\n".join(self.failures)
            )
