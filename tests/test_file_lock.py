"""The momentary lock and the compare (ADR-0042 decision 10, amendments A1-A5).

Process tests follow tests/test_task_claim_concurrency.py: spawned interpreters,
a start barrier, one group deadline. The merge here is the minimum a test
needs (set one JSON key); merging real entries is another PR's job.
"""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import signal
import threading
import time
from pathlib import Path

import pytest

from pyrite.config import default_data_dir
from pyrite.utils import atomic_write as aw
from pyrite.utils.atomic_write import atomic_write_text

# Reached by name at run time so the file still collects without the change
# (verify-red then sees behavioural reds, not a collection error).
try:
    from pyrite.utils import file_lock as fl
except ImportError:  # pragma: no cover - only without the change under test
    fl = None
FileChanged = getattr(aw, "FileChanged", Exception)
ABSENT = getattr(aw, "ABSENT", None)

_GROUP_DEADLINE = 180.0
_BARRIER_TIMEOUT = 150.0


def _join_all(processes, deadline=_GROUP_DEADLINE):
    end = time.monotonic() + deadline
    for p in processes:
        p.join(timeout=max(0.0, end - time.monotonic()))
    still = [p.pid for p in processes if p.exitcode is None]
    for p in processes:
        if p.exitcode is None:
            p.kill()
    assert not still, f"{len(still)} worker(s) still running after {deadline:.0f}s: {still}"
    crashed = [(p.pid, p.exitcode) for p in processes if p.exitcode != 0]
    assert not crashed, f"worker(s) crashed: {crashed}"


def _set_key(path: str, key: str, value) -> tuple[object, int]:
    """Read, set one key, write with expect=what was read; retry on a change.
    Returns (the value replaced, conflicts met)."""
    conflicts = 0
    while True:
        raw = Path(path).read_bytes()
        data = json.loads(raw)
        base = data.get(key)
        data[key] = value
        try:
            atomic_write_text(path, json.dumps(data, sort_keys=True), expect=raw)
            return base, conflicts
        except FileChanged:
            conflicts += 1


def _chain_worker(path, lock_dir, env, index, n, key, queue, barrier):
    if lock_dir:
        os.environ[fl.LOCK_DIR_ENV] = lock_dir
    os.environ.update(env)
    barrier.wait(timeout=_BARRIER_TIMEOUT)
    done = []
    for j in range(n):
        value = f"{index}-{j}"
        base, _ = _set_key(path, key, value)
        done.append((base, value))
    queue.put(done)


def _increment_worker(path, lock_dir, index, n, queue, barrier):
    os.environ[fl.LOCK_DIR_ENV] = lock_dir
    barrier.wait(timeout=_BARRIER_TIMEOUT)
    for _ in range(n):
        while True:
            raw = Path(path).read_bytes()
            data = json.loads(raw)
            data[f"k{index}"] = data.get(f"k{index}", 0) + 1
            try:
                atomic_write_text(path, json.dumps(data, sort_keys=True), expect=raw)
                break
            except FileChanged:
                pass
    queue.put(index)


def _holder(lock_target, lock_dir, ready, release):
    os.environ[fl.LOCK_DIR_ENV] = lock_dir
    with fl.file_lock(lock_target):
        ready.set()
        release.wait(timeout=_BARRIER_TIMEOUT)


def _run_chain(tmp_path, n_procs, n_writes, *, lock_dir, envs=None):
    target = tmp_path / "entry.json"
    target.write_text("{}")
    ctx = multiprocessing.get_context("spawn")
    queue, barrier = ctx.Queue(), ctx.Barrier(n_procs)
    procs = [
        ctx.Process(
            target=_chain_worker,
            args=(
                str(target),
                lock_dir,
                (envs or [{}] * n_procs)[i],
                i,
                n_writes,
                "title",
                queue,
                barrier,
            ),
        )
        for i in range(n_procs)
    ]
    for p in procs:
        p.start()
    _join_all(procs)
    writes = [w for _ in procs for w in queue.get(timeout=10)]
    return target, writes


def _assert_one_chain(target, writes, total):
    """Every write replaced the value the previous write left: no stale base."""
    assert len(writes) == total
    by_base = {}
    for base, value in writes:
        assert base not in by_base, f"two writes replaced the same value {base!r}: a lost update"
        by_base[base] = value
    cur, length = None, 0
    while cur in by_base:
        cur, length = by_base[cur], length + 1
    assert length == total, f"chain has {length} of {total} writes"
    assert json.loads(target.read_text())["title"] == cur


class TestChains:
    def test_eight_processes_one_key_form_one_chain(self, tmp_path):
        target, writes = _run_chain(tmp_path, 8, 8, lock_dir=str(tmp_path / "locks"))
        _assert_one_chain(target, writes, 64)

    def test_eight_processes_eight_keys_keep_all_changes(self, tmp_path):
        target = tmp_path / "entry.json"
        target.write_text("{}")
        ctx = multiprocessing.get_context("spawn")
        queue, barrier = ctx.Queue(), ctx.Barrier(8)
        procs = [
            ctx.Process(
                target=_increment_worker,
                args=(str(target), str(tmp_path / "locks"), i, 10, queue, barrier),
            )
            for i in range(8)
        ]
        for p in procs:
            p.start()
        _join_all(procs)
        assert json.loads(target.read_text()) == {f"k{i}": 10 for i in range(8)}

    def test_configs_resolving_one_lock_dir_exclude_each_other(self, tmp_path, monkeypatch):
        """Four writers, each with its own config dir and working directory,
        share the lock dir the platform default names (A1)."""
        runtime = tmp_path / "runtime"
        runtime.mkdir()
        monkeypatch.delenv(fl.LOCK_DIR_ENV, raising=False)
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
        monkeypatch.setenv("TMPDIR", str(runtime))
        envs = [{"PYRITE_CONFIG_DIR": str(tmp_path / f"cfg{i}")} for i in range(4)]
        target, writes = _run_chain(tmp_path, 4, 10, lock_dir=None, envs=envs)
        _assert_one_chain(target, writes, 40)
        assert any(runtime.rglob("*.lock")), "the default lock dir was not used"


class TestInProcessLock:
    def test_threads_exclude_each_other_when_the_os_lock_does_not(self, tmp_path, monkeypatch):
        """Where flock degrades to process-scoped record locks (NFS) only the
        in-process lock separates threads. Simulate that: flock does nothing,
        and a slow rename widens the compare-to-replace gap."""
        monkeypatch.setenv(fl.LOCK_DIR_ENV, str(tmp_path / "locks"))
        monkeypatch.setattr(fl.fcntl, "flock", lambda *a, **k: None)
        real_replace = os.replace

        def slow_replace(a, b):
            time.sleep(0.003)
            real_replace(a, b)

        monkeypatch.setattr(os, "replace", slow_replace)
        target = tmp_path / "entry.json"
        target.write_text("{}")
        writes, lock = [], threading.Lock()
        errors = []

        def work(i):
            try:
                for j in range(5):
                    base, _ = _set_key(str(target), "title", f"{i}-{j}")
                    with lock:
                        writes.append((base, f"{i}-{j}"))
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=work, args=(i,)) for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        assert not errors
        _assert_one_chain(target, writes, 30)


class TestKilledHolder:
    def test_sigkill_of_the_holder_leaves_a_lock_acquired_within_a_second(self, tmp_path):
        locks = str(tmp_path / "locks")
        entry = tmp_path / "e.md"
        entry.write_text("x")
        ctx = multiprocessing.get_context("spawn")
        ready, release = ctx.Event(), ctx.Event()
        p = ctx.Process(target=_holder, args=(str(entry), locks, ready, release))
        p.start()
        try:
            assert ready.wait(timeout=_BARRIER_TIMEOUT)
            with pytest.raises(fl.LockTimeout):  # really held
                with fl.file_lock(entry, lock_dir_path=locks, timeout=0.1):
                    pass
            os.kill(p.pid, signal.SIGKILL)
            p.join(timeout=10)
            start = time.monotonic()
            with fl.file_lock(entry, lock_dir_path=locks, timeout=1.0):
                pass
            assert time.monotonic() - start < 1.0
        finally:
            if p.is_alive():
                p.kill()


class TestStripes:
    def test_lock_files_are_bounded(self, tmp_path):
        locks = tmp_path / "locks"
        for i in range(fl.STRIPES + 100):
            with fl.file_lock(tmp_path / f"entry-{i}.md", lock_dir_path=locks):
                pass
        assert len(list(locks.iterdir())) <= fl.STRIPES

    def test_a_move_takes_both_stripes_once_in_ascending_order(self, tmp_path, monkeypatch):
        taken = []
        real = fl._open_stripe
        monkeypatch.setattr(fl, "_open_stripe", lambda d, s: (taken.append(s), real(d, s))[1])
        a, b = tmp_path / "a.md", tmp_path / "b.md"
        for pair in ((a, b), (b, a), (a, a)):
            taken.clear()
            with fl.file_lock(*pair, lock_dir_path=tmp_path / "locks"):
                pass
            assert taken == sorted(set(taken))
            assert len(taken) == len({fl.stripe_of(p) for p in pair})


class TestLockDirResolution:
    def test_platform_defaults(self):
        env = {
            "XDG_RUNTIME_DIR": "/run/user/7",
            "TMPDIR": "/var/folders/x/T/",
            "LOCALAPPDATA": "C:/L",
        }
        assert fl.default_lock_dir("linux", env) == Path("/run/user/7/pyrite/locks")
        assert fl.default_lock_dir("darwin", env) == Path("/var/folders/x/T/pyrite-locks")
        assert fl.default_lock_dir("win32", env) == Path("C:/L/pyrite/locks")

    def test_override_wins_and_default_ignores_config_and_kb(self, tmp_path, monkeypatch):
        monkeypatch.delenv(fl.LOCK_DIR_ENV, raising=False)
        monkeypatch.setenv("PYRITE_CONFIG_DIR", str(tmp_path / "cfg"))
        monkeypatch.chdir(tmp_path)
        kb = tmp_path / "kb"
        kb.mkdir()
        resolved = fl.lock_dir()
        assert resolved == fl.default_lock_dir()
        assert default_data_dir() not in (resolved, *resolved.parents)
        assert kb not in (resolved, *resolved.parents) and tmp_path / "cfg" not in resolved.parents
        monkeypatch.setenv(fl.LOCK_DIR_ENV, str(tmp_path / "env"))
        assert fl.lock_dir() == tmp_path / "env"
        assert fl.lock_dir(tmp_path / "arg") == tmp_path / "arg"

    def test_flock_is_called_by_module_attribute(self):
        src = Path(fl.__file__).read_text()
        assert "from fcntl import" not in src and "fcntl.flock(" in src


class TestCompare:
    @pytest.fixture(autouse=True)
    def _locks(self, tmp_path, monkeypatch):
        monkeypatch.setenv(fl.LOCK_DIR_ENV, str(tmp_path / "locks"))

    def test_match_writes(self, tmp_path):
        f = tmp_path / "e.md"
        f.write_bytes(b"old")
        atomic_write_text(f, "new", expect=b"old")
        assert f.read_text() == "new"
        atomic_write_text(f, "newer", expect=hashlib.sha256(b"new").hexdigest())
        assert f.read_text() == "newer"

    def test_mismatch_writes_nothing_and_carries_what_is_on_disk(self, tmp_path):
        f = tmp_path / "e.md"
        f.write_bytes(b"theirs")
        with pytest.raises(FileChanged) as ei:
            atomic_write_text(f, "mine", expect=b"base")
        assert ei.value.on_disk == b"theirs"
        assert f.read_bytes() == b"theirs"
        assert [p.name for p in tmp_path.iterdir() if p.name != "locks"] == ["e.md"]  # no temp

    def test_absent(self, tmp_path):
        f = tmp_path / "e.md"
        atomic_write_text(f, "created", expect=ABSENT)
        with pytest.raises(FileChanged):
            atomic_write_text(f, "again", expect=ABSENT)
        assert f.read_text() == "created"
        with pytest.raises(FileChanged) as ei:
            atomic_write_text(tmp_path / "gone.md", "x", expect=b"was here")
        assert ei.value.on_disk is None

    def test_without_expect_nothing_is_locked_or_compared(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fl.fcntl, "flock", lambda *a, **k: pytest.fail("locked"))
        f = tmp_path / "e.md"
        f.write_text("old")
        atomic_write_text(f, "new")
        assert f.read_text() == "new"

    def test_compare_runs_under_the_lock_immediately_before_the_rename(self, tmp_path, monkeypatch):
        """A writer that lands between the temp write and the rename is seen."""
        f = tmp_path / "e.md"
        f.write_bytes(b"base")
        real_open = fl._open_stripe

        def intrude(d, s):  # runs as the lock is being taken, i.e. after the temp write
            f.write_bytes(b"theirs")
            return real_open(d, s)

        monkeypatch.setattr(fl, "_open_stripe", intrude)
        with pytest.raises(FileChanged):
            atomic_write_text(f, "mine", expect=b"base")
        assert f.read_bytes() == b"theirs"

    def test_in_place_paths_compare_before_the_truncate(self, tmp_path):
        f = tmp_path / "e.md"
        f.write_bytes(b"theirs")
        os.link(f, tmp_path / "alias.md")  # a hard link: written in place
        with pytest.raises(FileChanged):
            atomic_write_text(f, "mine", expect=b"base")
        assert f.read_bytes() == b"theirs"
        atomic_write_text(f, "mine", expect=b"theirs")
        assert (tmp_path / "alias.md").read_text() == "mine"
