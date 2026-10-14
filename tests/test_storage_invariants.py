"""Storage invariants of ADR-0038 (proposed): entry identity and the file lifecycle.

A Hypothesis ``RuleBasedStateMachine`` drives a REAL KB on disk -- a real
``PyriteDB``, ``KBService``, ``IndexManager`` and ``KBRepository`` -- through
Pyrite operations (create, with and without a ``file_pattern`` type; update;
rename; delete; incremental sync; full index; reindex) interleaved with
external edits (a file written by hand, a duplicate id, a hand-changed id, a
file with no ``id:`` line, an edit that keeps the old mtime, ``mv``/``git mv``,
``rm``). After each step it checks the ADR's invariants.

Two layers:

- **One hand-written test per known violation**, replaying the sequence
  Hypothesis shrank it to. Each is a strict xfail tied to ONE issue that must
  fail with an ``AssertionError`` starting ``I<n>:``; fixing the issue flips
  exactly that test (plus, for #483, the other tests of the same bug).
- **An exploratory run** of the machine with a pinned ``@seed``, checking only
  the invariants with no known violation (I5, I9, minus the ambiguous-id shape
  that has its own test). It is what catches a new regression.

I10 (history) needs git and a handful of commits, so it is three plain tests at
the end rather than a rule of the machine.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

hypothesis = pytest.importorskip("hypothesis")

from hypothesis import HealthCheck, Phase, seed, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402
from hypothesis.stateful import (  # noqa: E402
    RuleBasedStateMachine,
    invariant,
    rule,
    run_state_machine_as_test,
)

from pyrite.config import KBConfig, PyriteConfig, Settings  # noqa: E402
from pyrite.exceptions import PyriteError  # noqa: E402
from pyrite.services.kb_service import KBService  # noqa: E402
from pyrite.storage.database import PyriteDB  # noqa: E402
from pyrite.storage.index import IndexManager  # noqa: E402
from pyrite.storage.repository import KBRepository  # noqa: E402

KB = "inv"

# `decision` has a file_pattern whose filename comes from FIELDS, not the id
# (the software-kb `adr` shape): two ids can resolve to one path, and a rename
# keeps the filename.
KB_YAML = """\
name: inv
types:
  decision:
    subdirectory: decisions
    file_pattern: "{number:04d}-{title}.md"
"""

# Small pools, so collisions (same title, same id, same filename) are common.
TITLES = ["Alpha", "Beta", "Gamma"]
IDS = ["alpha", "beta", "gamma", "dec-1", "dec-2", "hand-1", "renamed-1", "renamed-2"]
FILENAMES = ["alpha.md", "beta.md", "hand-1.md", "renamed-1.md", "notes/alpha.md", "x.md"]

ALL = frozenset({"I1", "I2", "I3", "I4", "I5", "I6", "I7", "I8", "I9"})


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class StorageMachine(RuleBasedStateMachine):
    """One throwaway KB per example; ``ENABLED`` names the invariants checked."""

    ENABLED: frozenset[str] = ALL
    SKIP_KNOWN: bool = False

    def __init__(self) -> None:
        super().__init__()
        self.tmp = Path(tempfile.mkdtemp(prefix="pyrite-inv-")).resolve()
        self.kb_path = self.tmp / KB
        self.kb_path.mkdir()
        (self.kb_path / "kb.yaml").write_text(KB_YAML)
        self.config = PyriteConfig(
            knowledge_bases=[KBConfig(name=KB, path=self.kb_path, kb_type="generic")],
            settings=Settings(index_path=self.tmp / "index.db", auto_embed=False),
        )
        self.db = PyriteDB(self.tmp / "index.db")
        self.svc = KBService(self.config, self.db)
        self.idx = IndexManager(self.db, self.config)
        self.kb_config = self.config.get_kb(KB)
        self.repo = KBRepository(self.kb_config)
        self.counter = 0

    def teardown(self) -> None:
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- observation ---------------------------------------------------

    def disk(self) -> dict[Path, str | None]:
        """Every entry file -> the id loading it gives (None: unparseable)."""
        out: dict[Path, str | None] = {}
        for f in sorted(self.repo.list_all_files()):
            try:
                out[f] = self.repo.load_entry_from_file(f).id
            except Exception:
                out[f] = None
        return out

    def claims(self, disk: dict[Path, str | None]) -> dict[str, list[Path]]:
        by_id: dict[str, list[Path]] = {}
        for f, i in disk.items():
            if i:
                by_id.setdefault(i, []).append(f)
        return by_id

    def rows(self) -> dict[str, dict]:
        return self.idx._load_indexed_state(KB)

    def snapshot(self) -> dict[Path, bytes]:
        return {f: f.read_bytes() for f in self.repo.list_all_files()}

    def on(self, name: str) -> bool:
        return name in self.ENABLED

    # -- the Pyrite operations, wrapped with their per-operation checks --

    def _pyrite_op(self, kind: str, fn, **args):
        before_bytes = self.snapshot()
        before = self.disk()
        before_claims = self.claims(before)
        # Ids whose lookup is already wrong before this op, by a known bug:
        # a filename hit holding another id (#483), an id held only by a file
        # find_file cannot find (#484), an id held by two files (#494).
        ambiguous = set()
        for i in {args.get("id"), args.get("old"), args.get("new")} - {None}:
            found = self.repo.find_file(i)
            held = before_claims.get(i, [])
            if len(held) > 1 or (found is None and held) or (found and before.get(found) != i):
                ambiguous.add(i)
        try:
            result, err = fn(), None
        except PyriteError as e:  # a typed refusal is a legitimate outcome
            result, err = None, e
        after = self.disk()
        after_claims = self.claims(after)
        ctx = f"{kind}({args}) -> {'refused: ' + type(err).__name__ if err else 'ok'}"

        if self.on("I1"):
            # No Pyrite operation raises the number of files claiming an id above one.
            for i, paths in after_claims.items():
                assert len(paths) <= max(1, len(before_claims.get(i, []))), (
                    f"I1: {ctx} left {len(paths)} files claiming {i!r}: {paths}"
                )

        if self.on("I4"):
            # Only delete removes content: every id on disk before is still on
            # disk after, except the old id of a successful rename.
            gone = set(before_claims) - set(after_claims)
            if kind == "rename" and err is None:
                gone.discard(args["old"])
            if kind == "delete" and err is None:
                gone.discard(args["id"])
            assert not gone, f"I4: {ctx} removed {sorted(gone)} from disk"

        if self.on("I5") and kind.startswith("create"):
            # A create never changes a byte of a file that already existed.
            for f, b in before_bytes.items():
                assert f.exists() and f.read_bytes() == b, f"I5: {ctx} overwrote or removed {f}"

        if self.on("I6") and err is None:
            if kind == "update":
                i = args["id"]
                if len(before_claims.get(i, [])) == 1 and len(after_claims.get(i, [])) == 1:
                    assert before_claims[i] == after_claims[i], (
                        f"I6: {ctx} moved {i!r}: {before_claims[i]} -> {after_claims[i]}"
                    )
            if kind == "rename" and result and result.get("renamed"):
                old, new = args["old"], args["new"]
                if len(before_claims.get(old, [])) == 1 and after_claims.get(new):
                    # Proposed rule: a rename changes the id, never the path --
                    # for every type, not only file_pattern ones.
                    src = before_claims[old][0]
                    assert after_claims[new] == [src], (
                        f"I6: {ctx} put {new!r} at {after_claims[new]}, expected {src}"
                    )

        if self.on("I7") and kind == "delete":
            # delete(X) removes only files whose id was X.
            removed = set(before) - set(after)
            wrong = {f for f in removed if before[f] != args["id"]}
            assert not wrong, f"I7: {ctx} removed files of other ids: {wrong}"

        # The known I9 violations have their own tests below; the exploratory
        # machine skips exactly those shapes -- by the state that causes them,
        # not by seed -- so it stays green and can still catch anything else
        # (e.g. update not writing the index).
        known_i9 = self.SKIP_KNOWN and (
            (kind == "rename" and args.get("old") == args.get("new"))  # #493
            or bool(ambiguous)  # #483, #484, #494
        )
        if self.on("I9") and err is None and not known_i9:
            # Write-through: the index agrees with disk for the ids the write
            # touched, with no sync in between.
            rows = self.rows()
            touched = {
                "rename": [args.get("old"), args.get("new")],
                "delete": [args.get("id")],
                "update": [args.get("id")],
            }.get(kind, [getattr(result, "entry", None) and result.entry.id])
            for i in filter(None, touched):
                paths = after_claims.get(i, [])
                if len(paths) == 1:
                    assert i in rows, f"I9: {ctx} left no index row for {i!r}"
                    assert Path(rows[i]["file_path"]) == paths[0], (
                        f"I9: {ctx} row {i!r} -> {rows[i]['file_path']}, file at {paths[0]}"
                    )
                    assert rows[i]["content_hash"] == _sha(paths[0]), (
                        f"I9: {ctx} row {i!r} has a stale content hash"
                    )
                elif not paths and kind in ("delete", "rename"):
                    assert i not in rows, f"I9: {ctx} left row {i!r} with no file"
        return result

    @rule(title=st.sampled_from(TITLES), id=st.sampled_from([None, *IDS[:3]]))
    def create_note(self, title, id):
        spec = {"entry_type": "note", "title": title, "body": "b", "id": id}
        self._pyrite_op("create", lambda: self.svc.create(KB, spec), title=title, id=id)

    @rule(
        title=st.sampled_from(TITLES), id=st.sampled_from(["dec-1", "dec-2"]), n=st.integers(1, 2)
    )
    def create_decision(self, title, id, n):
        spec = {"entry_type": "decision", "id": id, "title": title, "number": n, "body": "d"}
        self._pyrite_op("create_decision", lambda: self.svc.create(KB, spec), id=id, n=n)

    @rule(id=st.sampled_from(IDS), what=st.sampled_from(["title", "body", "tags"]))
    def update(self, id, what):
        self.counter += 1
        value = {"title": f"T{self.counter}", "body": f"body {self.counter}", "tags": ["t"]}[what]
        self._pyrite_op("update", lambda: self.svc.update(id, KB, {what: value}), id=id)

    @rule(old=st.sampled_from(IDS), new=st.sampled_from(IDS))
    def rename(self, old, new):
        self._pyrite_op("rename", lambda: self.svc.rename_entry(old, new, KB), old=old, new=new)

    @rule(id=st.sampled_from(IDS))
    def delete(self, id):
        self._pyrite_op("delete", lambda: self.svc.delete_entry(id, KB), id=id)

    # -- index operations ------------------------------------------------

    def _check_index_matches_disk(self, how: str) -> None:
        if not self.on("I2"):
            return
        claims = self.claims(self.disk())
        rows = self.rows()
        for i, paths in claims.items():
            assert i in rows, f"I2: after {how}, {i!r} is on disk at {paths} but not indexed"
            row_path = Path(rows[i]["file_path"])
            assert row_path in paths, f"I2: after {how}, row {i!r} -> {row_path}, files {paths}"
            if len(paths) == 1:
                assert rows[i]["content_hash"] == _sha(paths[0]), (
                    f"I2: after {how}, row {i!r} is stale (content changed, hash did not)"
                )
        extra = set(rows) - set(claims)
        assert not extra, f"I2: after {how}, rows {sorted(extra)} have no file"

    def _index_op(self, how: str, fn) -> None:
        before = self.snapshot()
        fn()
        if self.on("I4"):
            assert self.snapshot() == before, f"I4: {how} changed files on disk"
        self._check_index_matches_disk(how)

    @rule()
    def sync_incremental(self):
        self._index_op("sync_incremental", lambda: self.idx.sync_incremental(KB))
        if self.on("I3"):
            rows = self.rows()
            again = self.idx.sync_incremental(KB)
            changed = {k: again[k] for k in ("added", "updated", "removed") if again[k]}
            assert not changed, f"I3: a second sync with no file change reported {changed}"
            paths = {i: r["file_path"] for i, r in rows.items()}
            assert paths == {i: r["file_path"] for i, r in self.rows().items()}, (
                "I3: a second sync moved index rows"
            )

    @rule()
    def full_index(self):
        self._index_op("index_kb (pyrite index build)", lambda: self.idx.index_kb(KB))

    @rule()
    def reindex(self):
        self._index_op("sync_kb (pyrite kb reindex)", lambda: self.idx.sync_kb(self.kb_config))

    # -- external edits (a person, an editor, git) -----------------------

    def _pick(self, k: int) -> Path | None:
        files = sorted(self.repo.list_files())
        return files[k % len(files)] if files else None

    @rule(
        name=st.sampled_from(FILENAMES), id=st.sampled_from([None, *IDS]), t=st.sampled_from(TITLES)
    )
    def ext_write(self, name, id, t):
        """Write a file by hand: possibly a duplicate id, possibly no `id:`."""
        path = self.kb_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        id_line = f"id: {id}\n" if id else ""
        path.write_text(f"---\n{id_line}type: note\ntitle: {t}\n---\n\nby hand\n")

    @rule(k=st.integers(0, 20), keep_mtime=st.booleans())
    def ext_edit_body(self, k, keep_mtime):
        """Edit a body; `keep_mtime` models rsync -a / cp -p / tar extract."""
        path = self._pick(k)
        if path is None:
            return
        st_ = path.stat()
        self.counter += 1
        path.write_text(path.read_text() + f"\nedit {self.counter}\n")
        if keep_mtime:
            os.utime(path, ns=(st_.st_atime_ns, st_.st_mtime_ns))

    @rule(k=st.integers(0, 20), new=st.sampled_from(IDS))
    def ext_set_id(self, k, new):
        """Change a file's `id:` by hand, in place."""
        path = self._pick(k)
        if path is None:
            return
        lines = path.read_text().splitlines(keepends=True)
        out, done = [], False
        for line in lines[1:]:
            if not done and (line.startswith("id:") or line.startswith("---")):
                out.append(f"id: {new}\n")
                if line.startswith("---"):
                    out.append(line)
                done = True
            else:
                out.append(line)
        path.write_text(lines[0] + "".join(out))

    @rule(k=st.integers(0, 20), name=st.sampled_from(FILENAMES))
    def ext_mv(self, k, name):
        """`git mv` / `mv`: the same bytes at a new path, mtime unchanged."""
        path = self._pick(k)
        target = self.kb_path / name
        if path is None or target.exists():
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        os.rename(path, target)

    @rule(k=st.integers(0, 20))
    def ext_rm(self, k):
        path = self._pick(k)
        if path is not None:
            path.unlink()

    # -- state invariant, after every step -------------------------------

    @invariant()
    def lookup_by_id(self):
        if not self.on("I8"):
            return
        disk = self.disk()
        claims = self.claims(disk)
        for i in sorted(set(IDS) | set(claims)):
            found = self.repo.find_file(i)
            if found is not None:
                assert disk.get(found) == i, (
                    f"I8: find_file({i!r}) returned {found.name}, whose id is {disk.get(found)!r}"
                )
            if len(claims.get(i, [])) == 1:
                assert found is not None, f"I8: {i!r} is on disk at {claims[i]} but not findable"


# ---------------------------------------------------------------------------
# The exploratory run: invariants with no known violation, pinned seed.
# ---------------------------------------------------------------------------

# Invariants whose checks the random run keeps. The others each have known
# violations, pinned below as one hand-written test per bug.
CLEAN = frozenset({"I5", "I9"})


@seed(20260925)
class ExploratoryStorageMachine(StorageMachine):
    """Random sequences over every rule, checking only CLEAN invariants.

    The seed is pinned on the class (``@seed``), so the examples do not depend
    on the class or test name (``derandomize`` hashes the test's identity).
    """

    ENABLED = CLEAN
    SKIP_KNOWN = True


EXPLORE = settings(
    max_examples=int(os.environ.get("PYRITE_INVARIANT_EXAMPLES", "150")),
    stateful_step_count=20,
    database=None,
    deadline=None,
    # Shrinking is for a human reading a new failure; set
    # PYRITE_INVARIANT_SHRINK=1 to get the minimal sequence.
    phases=[Phase.explicit, Phase.generate]
    + ([Phase.shrink] if os.environ.get("PYRITE_INVARIANT_SHRINK") else []),
    suppress_health_check=list(HealthCheck),
)


def test_exploratory_storage_machine():
    run_state_machine_as_test(ExploratoryStorageMachine, settings=EXPLORE)


# ---------------------------------------------------------------------------
# One test per known violation: the shrunk sequence, one issue, one invariant.
# Each is a strict xfail that must fail with an AssertionError whose message
# starts "I<n>:" -- any other failure (a crash, another invariant) is turned
# into a non-AssertionError and so fails the xfail instead of satisfying it.
# When the issue is fixed the test XPASSes, which fails the suite: delete the
# marker then.
# ---------------------------------------------------------------------------


def _violates(inv: str, issue: str, what: str):
    return pytest.mark.xfail(strict=True, raises=AssertionError, reason=f"{issue}: {inv} {what}")


def _run(inv: str, steps: list[tuple[str, dict]], *, check_lookup: bool = False) -> None:
    m = StorageMachine()
    m.ENABLED = frozenset({inv})
    try:
        for name, kwargs in steps:
            getattr(m, name)(**kwargs)
        if check_lookup:
            m.lookup_by_id()
    except AssertionError as e:
        if not str(e).startswith(f"{inv}:"):
            raise RuntimeError(f"expected an {inv}: failure, got: {e}") from e
        raise
    finally:
        m.teardown()


NO_ID_BETA_AT_ALPHA = ("ext_write", {"name": "alpha.md", "id": None, "t": "Beta"})


def test_i1_create_duplicates_a_derived_id():
    _run("I1", [NO_ID_BETA_AT_ALPHA, ("create_note", {"title": "Beta", "id": None})])


def test_i2_index_build_keeps_a_deleted_files_row():
    steps = [("create_decision", {"id": "dec-1", "n": 1, "title": "Alpha"})]
    _run("I2", [*steps, ("ext_rm", {"k": 0}), ("full_index", {})])


def test_i2_reindex_keeps_a_moved_files_old_path():
    steps = [("create_note", {"title": "Alpha", "id": None})]
    _run("I2", [*steps, ("ext_mv", {"k": 0, "name": "x.md"}), ("reindex", {})])


def test_i2_edit_keeping_mtime_is_not_synced():
    steps = [("create_note", {"title": "Alpha", "id": None})]
    edit = ("ext_edit_body", {"k": 0, "keep_mtime": True})
    _run("I2", [*steps, edit, ("sync_incremental", {})])


def test_i3_duplicate_id_flips_on_every_sync():
    steps = [
        ("create_note", {"title": "Alpha", "id": None}),
        ("ext_write", {"name": "alpha.md", "id": None, "t": "Alpha"}),
    ]
    _run("I3", [*steps, ("sync_incremental", {})])


def test_i4_delete_by_filename_removes_another_entry():
    _run("I4", [NO_ID_BETA_AT_ALPHA, ("delete", {"id": "alpha"})])


@_violates("I6", "#488", "update moves a KB-root file into the type's folder")
def test_i6_update_moves_a_root_file():
    steps = [("ext_write", {"name": "alpha.md", "id": "gamma", "t": "Alpha"})]
    _run("I6", [*steps, ("update", {"id": "gamma", "what": "title"})])


@_violates("I6", "#489", "rename moves an id-named file")
def test_i6_rename_moves_an_id_named_file():
    steps = [("create_note", {"title": "Alpha", "id": None})]
    _run("I6", [*steps, ("rename", {"old": "alpha", "new": "renamed-1"})])


def test_i7_delete_removes_a_file_holding_another_id():
    steps = [("ext_write", {"name": "alpha.md", "id": "beta", "t": "Alpha"})]
    _run("I7", [*steps, ("delete", {"id": "alpha"})])


def test_i8_find_file_trusts_the_filename():
    _run("I8", [NO_ID_BETA_AT_ALPHA], check_lookup=True)


def test_i8_derived_id_is_not_findable():
    _run("I8", [("ext_write", {"name": "beta.md", "id": None, "t": "Alpha"})], check_lookup=True)


def test_i9_rename_to_same_id_is_refused():
    steps = [("ext_write", {"name": "alpha.md", "id": None, "t": "Alpha"})]
    _run("I9", [*steps, ("rename", {"old": "alpha", "new": "alpha"})])


def test_i9_delete_of_a_duplicated_id_orphans_a_file():
    steps = [
        ("create_note", {"title": "Gamma", "id": None}),
        ("ext_write", {"name": "x.md", "id": "gamma", "t": "Alpha"}),
    ]
    _run("I9", [*steps, ("delete", {"id": "gamma"})])


# ---------------------------------------------------------------------------
# I10: history. A Pyrite rename keeps the entry's pre-rename history, whether
# or not its type names files by id -- and history never includes commits in
# which the file held some other id that Pyrite did not rename from.
# ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str) -> None:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com"}
    env |= {"GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
    # No user or system git config: hooks, signing or a template must not
    # change what these tests see.
    env |= {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=env)


@pytest.fixture
def git_kb(tmp_path):
    kb_path = tmp_path / KB
    kb_path.mkdir()
    (kb_path / "kb.yaml").write_text(KB_YAML)
    _git(kb_path, "init", "-q", "-b", "main")
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name=KB, path=kb_path, kb_type="generic")],
        settings=Settings(index_path=tmp_path / "index.db", auto_embed=False),
    )
    db = PyriteDB(tmp_path / "index.db")
    yield kb_path, config, db, KBService(config, db)
    db.close()


def _history_after_rename(git_kb, spec: dict, new_id: str) -> list[str]:
    from pyrite.services.git_service import GitService

    kb_path, config, db, svc = git_kb
    old_id = svc.create(KB, spec).entry.id
    _git(kb_path, "add", "-A")
    _git(kb_path, "commit", "-qm", "create")
    svc.update(old_id, KB, {"body": "edited"})
    _git(kb_path, "add", "-A")
    _git(kb_path, "commit", "-qm", "edit")
    svc.rename_entry(old_id, new_id, KB)
    _git(kb_path, "add", "-A")
    _git(kb_path, "commit", "-qm", "rename")
    IndexManager(db, config).index_with_attribution(KB, GitService())
    return [v["message"] for v in db.get_entry_versions(new_id, KB)]


@_violates("I10", "#489", "rename of an id-named file loses history")
def test_i10_history_survives_rename_of_an_id_named_file(git_kb):
    msgs = _history_after_rename(git_kb, {"entry_type": "note", "title": "Alpha"}, "omega")
    assert sorted(msgs) == ["create", "edit", "rename"], f"I10: history of 'omega' is {msgs}"


def test_i10_history_survives_rename_of_a_file_pattern_file(git_kb):
    spec = {"entry_type": "decision", "id": "dec-1", "title": "Alpha", "number": 1}
    msgs = _history_after_rename(git_kb, spec, "dec-9")
    assert sorted(msgs) == ["create", "edit", "rename"], f"I10: history of 'dec-9' is {msgs}"


@_violates("I10", "#490", "an in-place id change inherits history")
def test_i10_hand_changed_id_does_not_inherit_the_old_entrys_history(git_kb):
    from pyrite.services.git_service import GitService

    kb_path, config, db, svc = git_kb
    svc.create(KB, {"entry_type": "note", "title": "Alpha"})
    _git(kb_path, "add", "-A")
    _git(kb_path, "commit", "-qm", "alpha")
    f = kb_path / "notes" / "alpha.md"
    f.write_text(f.read_text().replace("id: alpha", "id: zeta"))
    _git(kb_path, "add", "-A")
    _git(kb_path, "commit", "-qm", "zeta")
    IndexManager(db, config).index_with_attribution(KB, GitService())
    msgs = [v["message"] for v in db.get_entry_versions("zeta", KB)]
    assert msgs == ["zeta"], f"I10: 'zeta' was never renamed from 'alpha' but inherits {msgs}"
