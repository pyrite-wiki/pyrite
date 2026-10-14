"""The momentary lock and the compare (ADR-0042 decision 10, amendments A1-A5).

A1 (as amended in #748): the lock lives in ``<git-dir>/pyrite/locks`` of the
repository whose work tree holds the file. ``lock_dir_for`` is the one place
that answers "where is the lock"; the reference it must agree with is git
itself, so the location tests run ``git rev-parse`` (with every ``GIT_*``
variable removed, see ``_git_reference``) on each adversary layout and compare.

Process tests follow tests/test_task_claim_concurrency.py: spawned interpreters,
a start barrier, one group deadline. The merge here is the minimum a test
needs (set one JSON key); merging real entries is another PR's job.
"""

from __future__ import annotations

import errno
import functools
import hashlib
import json
import multiprocessing
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import unicodedata
from pathlib import Path

import pytest

from pyrite.utils import atomic_write as aw
from pyrite.utils.atomic_write import atomic_write_text

# Reached by name at run time so the file still collects without the change
# (verify-red then sees behavioural reds, not a collection error).
try:
    from pyrite.utils import file_lock as fl
except ImportError:  # pragma: no cover - only without the change under test
    fl = None


class _NeverRaised(Exception):  # noqa: N818 - never raised; a stand-in
    """Stands in for ``FileChanged`` without the change. Falling back to
    ``Exception`` made every retry loop catch the TypeError of ``expect=``
    and spin forever under verify-red (#752): a test whose guard is gone must
    fail, in bounded time."""


FileChanged = getattr(aw, "FileChanged", _NeverRaised)
ABSENT = getattr(aw, "ABSENT", None)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git as the reference")

_GROUP_DEADLINE = 180.0
_BARRIER_TIMEOUT = 150.0
# Each writer's retry loop gives up after this long, inside the group deadline,
# so a writer that never makes progress (the lock or compare removed, or a
# livelock) fails the test instead of spinning past it.
_WRITER_DEADLINE = 90.0


# --------------------------------------------------------------------------
# git: the reference, and repositories to test in
# --------------------------------------------------------------------------


def _clean_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _git(*args: str, cwd: Path) -> str:
    env = _clean_env() | {"GIT_CONFIG_NOSYSTEM": "1"}
    out = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return out.stdout.strip()


def _git_reference(file: Path) -> Path | None:
    """The git dir git itself names for ``file``: discovery from the file's
    real directory, environment overrides removed (the lock must not depend
    on a process's environment). ``None`` when git finds no work tree."""
    start = os.path.dirname(os.path.realpath(file))
    out = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree", "--absolute-git-dir"],
        cwd=start,
        env=_clean_env(),
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        return None
    inside, git_dir = out.stdout.splitlines()
    return Path(git_dir) if inside == "true" else None


def _repo(path: Path, *, commit: bool = False) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git("init", "-q", cwd=path)
    if commit:
        _git("commit", "-q", "--allow-empty", "-m", "init", cwd=path)
    return path


def _entry(directory: Path, name: str = "e.md", text: str = "x") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    f = directory / name
    f.write_text(text)
    return f


def _same(a: Path, b: Path) -> bool:
    return os.path.samefile(a, b)


def _assert_agrees(file: Path, expected_git_dir: Path) -> None:
    """Git and ``lock_dir_for`` both name ``expected_git_dir`` for ``file``."""
    reference = _git_reference(file)
    assert reference is not None, f"git finds no work tree for {file}"
    assert _same(reference, expected_git_dir), (reference, expected_git_dir)
    got = fl.lock_dir_for(file)
    assert got.parts[-2:] == ("pyrite", "locks")
    assert _same(got.parent.parent, reference), (got, reference)


def _assert_refused(file: Path, match: str) -> None:
    assert _git_reference(file) is None, "git finds a work tree here"
    with pytest.raises(fl.LockDirError, match=match):
        fl.lock_dir_for(file)


# --------------------------------------------------------------------------
# A1: where the lock is
# --------------------------------------------------------------------------


class TestWhereTheLockIs:
    """Each adversary class from the brief, against git."""

    def test_a_plain_repository(self, tmp_path):
        repo = _repo(tmp_path / "repo")
        _assert_agrees(_entry(repo / "kb" / "deep"), repo / ".git")
        _assert_agrees(_entry(repo), repo / ".git")

    def test_a_file_that_does_not_exist_yet(self, tmp_path):
        repo = _repo(tmp_path / "repo")
        (repo / "kb").mkdir()
        _assert_agrees(repo / "kb" / "new.md", repo / ".git")

    def test_a_linked_worktree_locks_in_its_own_git_dir(self, tmp_path):
        """Per-worktree, not the common dir: each worktree has its own files,
        and its index.lock lives here too."""
        repo = _repo(tmp_path / "repo", commit=True)
        _git("worktree", "add", "-q", str(tmp_path / "wt"), cwd=repo)
        _assert_agrees(_entry(tmp_path / "wt" / "kb"), repo / ".git" / "worktrees" / "wt")
        _assert_agrees(_entry(repo / "kb"), repo / ".git")

    def test_a_submodule_and_its_superproject(self, tmp_path):
        upstream = _repo(tmp_path / "upstream", commit=True)
        sup = _repo(tmp_path / "super", commit=True)
        _git(
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            "-q",
            str(upstream),
            "sub",
            cwd=sup,
        )
        assert (sup / "sub" / ".git").is_file()  # a gitfile, as git lays it out
        _assert_agrees(_entry(sup / "sub" / "kb"), sup / ".git" / "modules" / "sub")
        _assert_agrees(_entry(sup / "kb"), sup / ".git")

    def test_nested_repositories(self, tmp_path):
        outer = _repo(tmp_path / "outer")
        inner = _repo(outer / "vendor" / "inner")
        _assert_agrees(_entry(inner / "kb"), inner / ".git")
        _assert_agrees(_entry(outer / "kb"), outer / ".git")
        _assert_agrees(_entry(outer / "vendor"), outer / ".git")

    def test_a_symlinked_kb_path(self, tmp_path):
        repo = _repo(tmp_path / "repo")
        _entry(repo / "kb")
        os.symlink(repo / "kb", tmp_path / "kb-link")  # the link lives outside any repo
        _assert_agrees(tmp_path / "kb-link" / "e.md", repo / ".git")

    def test_a_symlinked_entry_locks_where_its_bytes_live(self, tmp_path):
        a, b = _repo(tmp_path / "a"), _repo(tmp_path / "b")
        target = _entry(b / "kb")
        (a / "kb").mkdir()
        os.symlink(target, a / "kb" / "e.md")
        _assert_agrees(a / "kb" / "e.md", b / ".git")

    def test_a_symlinked_dot_git(self, tmp_path):
        repo = _repo(tmp_path / "repo")
        store = tmp_path / "store"
        store.mkdir()
        os.rename(repo / ".git", store / "repo.git")
        os.symlink(store / "repo.git", repo / ".git")
        _assert_agrees(_entry(repo / "kb"), store / "repo.git")

    def test_a_gitfile_with_a_relative_path(self, tmp_path):
        repo = _repo(tmp_path / "repo")
        os.rename(repo / ".git", tmp_path / "elsewhere.git")
        (repo / ".git").write_text("gitdir: ../elsewhere.git\n")
        _assert_agrees(_entry(repo / "kb"), tmp_path / "elsewhere.git")

    def test_an_invalid_dot_git_directory_is_skipped_as_git_skips_it(self, tmp_path):
        outer = _repo(tmp_path / "outer")
        (outer / "kb" / ".git").mkdir(parents=True)  # not a repository: no HEAD
        _assert_agrees(_entry(outer / "kb"), outer / ".git")

    def test_a_repository_with_an_invalid_head_is_skipped_as_git_skips_it(self, tmp_path):
        outer = _repo(tmp_path / "outer")
        inner = _repo(outer / "inner")  # objects/ and refs/ present
        (inner / ".git" / "HEAD").write_text("garbage\n")
        _assert_agrees(_entry(inner / "kb"), outer / ".git")
        (inner / ".git" / "HEAD").write_text("0" * 40 + "\n")  # a detached HEAD is valid
        _assert_agrees(_entry(inner / "kb"), inner / ".git")

    def test_an_invalid_gitfile_is_refused_as_git_refuses_it(self, tmp_path):
        outer = _repo(tmp_path / "outer")
        _entry(outer / "kb")
        (outer / "kb" / ".git").write_text("not a gitfile\n")
        _assert_refused(outer / "kb" / "e.md", "git")
        (outer / "kb" / ".git").write_text("gitdir: /nonexistent/x.git\n")
        _assert_refused(outer / "kb" / "e.md", "git")

    def test_the_environment_does_not_move_the_lock(self, tmp_path, monkeypatch):
        """GIT_DIR in a process's environment made ``git rev-parse`` name
        another repository for the same file: two lock dirs, failing open.
        The lookup reads no environment variable."""
        repo, other = _repo(tmp_path / "repo"), _repo(tmp_path / "other")
        f = _entry(repo / "kb")
        before = fl.lock_dir_for(f)
        for name, value in {
            "GIT_DIR": str(other / ".git"),
            "GIT_WORK_TREE": str(other),
            "GIT_COMMON_DIR": str(other / ".git"),
            "GIT_CEILING_DIRECTORIES": str(repo),
            "GIT_DISCOVERY_ACROSS_FILESYSTEM": "1",
        }.items():
            monkeypatch.setenv(name, value)
        assert fl.lock_dir_for(f) == before
        _assert_agrees(f, repo / ".git")

    def test_a_case_different_spelling_names_the_same_directory(self, tmp_path):
        repo = _repo(tmp_path / "repo")
        f = _entry(repo / "kb", "Entry.md")
        upper = Path(str(f).replace("/repo/kb/", "/REPO/KB/"))
        if not upper.exists():
            pytest.skip("case-sensitive filesystem")
        assert _same(fl.lock_dir_for(upper).parent.parent, fl.lock_dir_for(f).parent.parent)
        assert fl.stripe_of(upper) == fl.stripe_of(f)

    def test_no_repository_is_refused_and_says_so(self, tmp_path):
        f = _entry(tmp_path / "plain" / "kb")
        _assert_refused(f, "not in a git repository")
        with pytest.raises(fl.NotInGitRepository, match="expect="):
            fl.lock_dir_for(f)

    def test_a_file_inside_the_git_dir_is_refused(self, tmp_path):
        repo = _repo(tmp_path / "repo")
        _assert_refused(_entry(repo / ".git" / "pyrite-test"), "inside a git directory")

    def test_a_bare_repository_is_refused(self, tmp_path):
        bare = tmp_path / "bare.git"
        bare.mkdir()
        _git("init", "-q", "--bare", cwd=bare)
        _assert_refused(_entry(bare / "kb"), "bare repository")

    def test_discovery_stops_at_a_filesystem_boundary(self, tmp_path, monkeypatch):
        """Git stops at a mount point unless GIT_DISCOVERY_ACROSS_FILESYSTEM is
        set (which the lookup ignores). Simulated: the KB directory reports
        another device. A real second mount is not available in the suite."""
        repo = _repo(tmp_path / "repo")
        f = _entry(repo / "kb" / "mnt")
        mount = os.path.realpath(repo / "kb" / "mnt")
        real_stat = os.stat

        def fake_stat(p, *a, **k):
            st = real_stat(p, *a, **k)
            if os.fspath(p) == mount and not k.get("dir_fd"):
                fields = list(st)
                fields[2] = st.st_dev + 1  # st_dev
                return os.stat_result(fields)
            return st

        monkeypatch.setattr(fl.os, "stat", fake_stat)
        with pytest.raises(fl.NotInGitRepository, match="filesystem"):
            fl.lock_dir_for(f)

    def test_a_move_across_two_repositories_is_refused(self, tmp_path):
        a, b = _repo(tmp_path / "a"), _repo(tmp_path / "b")
        with pytest.raises(fl.LockDirError, match="two git"):
            with fl.file_lock(_entry(a / "kb"), _entry(b / "kb")):
                pass

    def test_file_lock_asks_lock_dir_for_and_nothing_else(self, tmp_path, monkeypatch):
        """One predicate: file_lock takes no directory argument and reads no
        environment variable; it locks where lock_dir_for says."""
        repo = _repo(tmp_path / "repo")
        f = _entry(repo / "kb")
        asked = []
        real = fl.lock_dir_for
        monkeypatch.setattr(fl, "lock_dir_for", lambda p: (asked.append(p), real(p))[1])
        with fl.file_lock(f):
            pass
        assert asked == [f]
        assert len(list((repo / ".git" / "pyrite" / "locks").iterdir())) == 1
        with pytest.raises(TypeError):
            fl.file_lock(f, lock_dir_path=tmp_path).__enter__()
        assert not hasattr(fl, "LOCK_DIR_ENV") and not hasattr(fl, "default_lock_dir")


# --------------------------------------------------------------------------
# A1: the lock directory has the git dir's permissions, never wider
# --------------------------------------------------------------------------


def _mode(path) -> int:
    return os.stat(path, follow_symlinks=False).st_mode & 0o7777


def _lock_once(*entries, **kw):
    with fl.file_lock(*entries, **kw):
        pass


def _umask(mask):
    class _U:
        def __enter__(self):
            self.old = os.umask(mask)

        def __exit__(self, *a):
            os.umask(self.old)

    return _U()


not_root = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0, reason="root writes anything"
)


class TestPermissions:
    def test_a_private_git_dir_gives_a_private_lock_dir_whatever_the_umask(self, tmp_path):
        repo = _repo(tmp_path / "repo")
        os.chmod(repo / ".git", 0o755)
        for mask in (0o000, 0o002, 0o022):
            shutil.rmtree(repo / ".git" / "pyrite", ignore_errors=True)
            with _umask(mask):
                _lock_once(_entry(repo / "kb"))
            assert _mode(repo / ".git" / "pyrite") == 0o755, oct(mask)
            assert _mode(repo / ".git" / "pyrite" / "locks") == 0o755, oct(mask)
            (stripe,) = (repo / ".git" / "pyrite" / "locks").iterdir()
            assert _mode(stripe) == 0o600, oct(mask)  # only who can write the repo

    def test_a_group_shared_git_dir_gives_a_group_lock_dir_whatever_the_umask(self, tmp_path):
        repo = _repo(tmp_path / "repo")
        os.chmod(repo / ".git", 0o2775)
        for mask in (0o077, 0o022, 0o222):
            shutil.rmtree(repo / ".git" / "pyrite", ignore_errors=True)
            with _umask(mask):
                _lock_once(_entry(repo / "kb"))
            assert _mode(repo / ".git" / "pyrite" / "locks") == 0o2775, oct(mask)
            (stripe,) = (repo / ".git" / "pyrite" / "locks").iterdir()
            assert _mode(stripe) == 0o660, oct(mask)

    def test_a_lock_dir_wider_than_the_git_dir_is_refused(self, tmp_path):
        repo = _repo(tmp_path / "repo")
        os.chmod(repo / ".git", 0o755)
        f = _entry(repo / "kb")
        _lock_once(f)
        for name in ("pyrite", "pyrite/locks"):
            d = repo / ".git" / name
            for mode in (0o777, 0o775, 0o757):
                os.chmod(d, mode)
                with pytest.raises(fl.LockDirError, match="wider"):
                    _lock_once(f)
            os.chmod(d, 0o755)
        _lock_once(f)

    @not_root
    def test_a_read_only_git_dir_is_refused_and_nothing_created(self, tmp_path):
        repo = _repo(tmp_path / "repo")
        f = _entry(repo / "kb")
        os.chmod(repo / ".git", 0o555)
        try:
            with pytest.raises(fl.LockDirError, match="cannot write"):
                _lock_once(f)
            assert not (repo / ".git" / "pyrite").exists()
            os.chmod(repo / ".git", 0o755)
            _lock_once(f)  # the directories now exist
            os.chmod(repo / ".git", 0o555)
            with pytest.raises(fl.LockDirError, match="cannot write"):
                _lock_once(f)  # still refused: git could not write index.lock here
        finally:
            os.chmod(repo / ".git", 0o755)

    def test_a_symlinked_lock_dir_is_refused(self, tmp_path):
        repo = _repo(tmp_path / "repo")
        real = tmp_path / "real"
        real.mkdir(mode=0o755)
        (repo / ".git" / "pyrite").mkdir()
        os.symlink(real, repo / ".git" / "pyrite" / "locks")
        with pytest.raises(fl.LockDirError):
            _lock_once(_entry(repo / "kb"))
        assert not list(real.iterdir())

    def test_a_symlinked_pyrite_dir_is_refused(self, tmp_path):
        repo = _repo(tmp_path / "repo")
        real = tmp_path / "real"
        real.mkdir(mode=0o755)
        os.symlink(real, repo / ".git" / "pyrite")
        with pytest.raises(fl.LockDirError):
            _lock_once(_entry(repo / "kb"))
        assert not list(real.iterdir())

    def test_a_symlinked_stripe_is_refused_and_its_target_untouched(self, tmp_path):
        repo = _repo(tmp_path / "repo")
        entry = _entry(repo / "kb")
        _lock_once(entry)
        locks = repo / ".git" / "pyrite" / "locks"
        (stripe,) = locks.iterdir()
        stripe.unlink()
        victim = tmp_path / "victim.txt"
        victim.write_text("keep")
        os.chmod(victim, 0o600)
        os.symlink(victim, stripe)
        with pytest.raises(fl.LockDirError):
            _lock_once(entry)
        assert _mode(victim) == 0o600 and victim.read_text() == "keep"

    def test_a_dangling_symlinked_stripe_creates_nothing_elsewhere(self, tmp_path):
        repo = _repo(tmp_path / "repo")
        entry = _entry(repo / "kb")
        _lock_once(entry)
        (stripe,) = (repo / ".git" / "pyrite" / "locks").iterdir()
        stripe.unlink()
        elsewhere = tmp_path / "created_elsewhere"
        os.symlink(elsewhere, stripe)
        with pytest.raises(fl.LockDirError):
            _lock_once(entry)
        assert not elsewhere.exists()

    def test_an_existing_stripe_keeps_its_mode(self, tmp_path):
        repo = _repo(tmp_path / "repo")
        entry = _entry(repo / "kb")
        _lock_once(entry)
        (stripe,) = (repo / ".git" / "pyrite" / "locks").iterdir()
        os.chmod(stripe, 0o640)
        _lock_once(entry)
        assert _mode(stripe) == 0o640

    def test_a_stripe_that_is_not_a_regular_file_is_refused(self, tmp_path):
        repo = _repo(tmp_path / "repo")
        entry = _entry(repo / "kb")
        _lock_once(entry)
        (stripe,) = (repo / ".git" / "pyrite" / "locks").iterdir()
        stripe.unlink()
        os.mkfifo(stripe)  # opens fine, is not a file
        with pytest.raises(fl.LockDirError, match="regular"):
            _lock_once(entry)

    def test_a_stripe_opens_relative_to_the_verified_directory(self, tmp_path, monkeypatch):
        """A directory swapped after verification must not redirect the stripe."""
        repo = _repo(tmp_path / "repo")
        entry = _entry(repo / "kb")
        locks, moved = repo / ".git" / "pyrite" / "locks", repo / ".git" / "pyrite" / "moved"
        real = fl._open_lock_dir

        def swap_after_verify(lock_dir):
            dfd = real(lock_dir)
            os.rename(locks, moved)
            locks.mkdir(mode=0o755)  # the decoy
            return dfd

        monkeypatch.setattr(fl, "_open_lock_dir", swap_after_verify)
        _lock_once(entry)
        assert len(list(moved.iterdir())) == 1
        assert not list(locks.iterdir())


# --------------------------------------------------------------------------
# The lock itself (carried over from #733, now in the git dir)
# --------------------------------------------------------------------------


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


def _gave_up(deadline: float, what: str) -> None:
    if time.monotonic() > deadline:
        raise AssertionError(f"{what}: no write landed before the writer deadline")


def _set_key(path: str, key: str, value, deadline: float) -> tuple[object, int]:
    """Read, set one key, write with expect=what was read; retry on a change
    until ``deadline``. Returns (the value replaced, conflicts met)."""
    conflicts = 0
    while True:
        _gave_up(deadline, f"set {key}={value}")
        raw = Path(path).read_bytes()
        data = json.loads(raw)
        base = data.get(key)
        data[key] = value
        try:
            atomic_write_text(path, json.dumps(data, sort_keys=True), expect=raw)
            return base, conflicts
        except FileChanged:
            conflicts += 1


def _chain_worker(path, env, cwd, index, n, key, queue, barrier):
    os.environ.update(env)
    os.chdir(cwd)
    barrier.wait(timeout=_BARRIER_TIMEOUT)
    deadline = time.monotonic() + _WRITER_DEADLINE
    done = []
    for j in range(n):
        value = f"{index}-{j}"
        base, _ = _set_key(path, key, value, deadline)
        done.append((base, value))
    queue.put(done)


def _increment_worker(path, index, n, queue, barrier):
    barrier.wait(timeout=_BARRIER_TIMEOUT)
    deadline = time.monotonic() + _WRITER_DEADLINE
    for _ in range(n):
        while True:
            _gave_up(deadline, f"increment k{index}")
            raw = Path(path).read_bytes()
            data = json.loads(raw)
            data[f"k{index}"] = data.get(f"k{index}", 0) + 1
            try:
                atomic_write_text(path, json.dumps(data, sort_keys=True), expect=raw)
                break
            except FileChanged:
                pass
    queue.put(index)


def _holder(lock_target, ready, release):
    with fl.file_lock(lock_target):
        ready.set()
        release.wait(timeout=_BARRIER_TIMEOUT)


def _run_chain(paths, n_writes, *, envs=None, cwds=None):
    """One writer per entry of ``paths`` (each a spelling of one file)."""
    n = len(paths)
    ctx = multiprocessing.get_context("spawn")
    queue, barrier = ctx.Queue(), ctx.Barrier(n)
    procs = [
        ctx.Process(
            target=_chain_worker,
            args=(
                str(paths[i]),
                (envs or [{}] * n)[i],
                str((cwds or [paths[0].parent] * n)[i]),
                i,
                n_writes,
                "title",
                queue,
                barrier,
            ),
        )
        for i in range(n)
    ]
    for p in procs:
        p.start()
    _join_all(procs)
    return [w for _ in procs for w in queue.get(timeout=10)]


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
    assert json.loads(Path(target).read_text())["title"] == cur


class TestChains:
    def test_eight_processes_one_key_form_one_chain(self, tmp_path):
        target = _entry(_repo(tmp_path / "repo") / "kb", "entry.json", "{}")
        writes = _run_chain([target] * 8, 8)
        _assert_one_chain(target, writes, 64)
        assert len(list((tmp_path / "repo" / ".git" / "pyrite" / "locks").iterdir())) == 1

    def test_eight_processes_eight_keys_keep_all_changes(self, tmp_path):
        target = _entry(_repo(tmp_path / "repo") / "kb", "entry.json", "{}")
        ctx = multiprocessing.get_context("spawn")
        queue, barrier = ctx.Queue(), ctx.Barrier(8)
        procs = [
            ctx.Process(target=_increment_worker, args=(str(target), i, 10, queue, barrier))
            for i in range(8)
        ]
        for p in procs:
            p.start()
        _join_all(procs)
        assert json.loads(target.read_text()) == {f"k{i}": 10 for i in range(8)}

    def test_writers_reaching_one_file_by_different_paths_form_one_chain(self, tmp_path):
        """A direct path, a symlinked KB directory, a case-different spelling
        (where the filesystem folds case) and a process whose environment
        names another repository in GIT_DIR, with different working
        directories and config dirs: one file, one lock, one chain."""
        repo, other = _repo(tmp_path / "repo"), _repo(tmp_path / "other")
        target = _entry(repo / "kb", "entry.json", "{}")
        os.symlink(repo / "kb", tmp_path / "kb-link")
        upper = Path(str(target).replace("/repo/kb/", "/REPO/KB/"))
        paths = [target, tmp_path / "kb-link" / "entry.json", target]
        if upper.exists():
            paths.append(upper)
        envs = [
            {"PYRITE_CONFIG_DIR": str(tmp_path / f"cfg{i}"), "GIT_DIR": str(other / ".git")}
            if i == 2
            else {"PYRITE_CONFIG_DIR": str(tmp_path / f"cfg{i}")}
            for i in range(len(paths))
        ]
        cwds = [tmp_path, other, repo / "kb", tmp_path][: len(paths)]
        writes = _run_chain(paths, 10, envs=envs, cwds=cwds)
        _assert_one_chain(target, writes, 10 * len(paths))
        assert not (other / ".git" / "pyrite").exists(), "GIT_DIR moved a lock"


# --------------------------------------------------------------------------
# A2 (maintainer, 2026-10-07): one file, one lock, whatever path reaches it
# --------------------------------------------------------------------------


def _alias(kind: str, tmp_path: Path, repo: Path, target: Path) -> tuple[Path, dict]:
    """Another path to ``target`` of class ``kind``, and the environment the
    writer on it runs with. Skips when this filesystem cannot produce it."""
    env: dict = {}
    if kind == "symlinked directory":
        os.symlink(target.parent, tmp_path / "kb-link")
        alias = tmp_path / "kb-link" / target.name
    elif kind == "symlinked file":
        alias = tmp_path / "e-link.json"
        os.symlink(target, alias)
    elif kind == "dot-dot segments":
        alias = target.parent / ".." / target.parent.name / target.name
    elif kind == "case spelling":
        alias = target.parent / target.name.upper()
    elif kind == "unicode NFD spelling":
        alias = target.parent / unicodedata.normalize("NFD", target.name)
    elif kind == "firmlink /System/Volumes/Data":
        alias = Path("/System/Volumes/Data" + str(target))
    elif kind == "Greek dialytika-tonos spelling (U+0390 / U+0399 U+0308 U+0301)":
        alias = target.parent / target.name.replace("\u0390", "\u0399\u0308\u0301")
    elif kind == "GIT_DIR naming another repo":
        alias = target
        env = {"GIT_DIR": str(_repo(tmp_path / "other") / ".git")}
    else:  # pragma: no cover
        raise AssertionError(kind)
    if not alias.exists() or not os.path.samefile(alias, target):
        pytest.skip(f"this filesystem has no {kind} alias")
    return alias, env


ALIAS_CLASSES = [
    "symlinked directory",
    "symlinked file",
    "dot-dot segments",
    "case spelling",
    "unicode NFD spelling",
    "firmlink /System/Volumes/Data",
    "Greek dialytika-tonos spelling (U+0390 / U+0399 U+0308 U+0301)",
    "GIT_DIR naming another repo",
]


def _try_lock(path, env, queue):
    os.environ.update(env)
    try:
        with fl.file_lock(path, timeout=0.3):
            queue.put("acquired")
    except fl.LockTimeout:
        queue.put("timed out")


class TestOneFileOneLock:
    """Every alias class this machine can produce, through real processes.
    A hard link is not here: it is refused (TestCompare)."""

    @pytest.fixture
    def target(self, tmp_path):
        # é: NFC/NFD and case; ΐ (U+0390): its casefold is not NFC (#752 round 2)
        return _entry(_repo(tmp_path / "repo") / "kb", "caf\u00e9-\u0390.json", "{}")

    @pytest.mark.parametrize("kind", ALIAS_CLASSES)
    def test_the_lock_held_by_one_path_is_held_for_the_alias(self, kind, tmp_path, target):
        alias, env = _alias(kind, tmp_path, tmp_path / "repo", target)
        ctx = multiprocessing.get_context("spawn")
        ready, release, queue = ctx.Event(), ctx.Event(), ctx.Queue()
        holder = ctx.Process(target=_holder, args=(str(target), ready, release))
        holder.start()
        try:
            assert ready.wait(timeout=_BARRIER_TIMEOUT)
            tryer = ctx.Process(target=_try_lock, args=(str(alias), env, queue))
            tryer.start()
            _join_all([tryer], deadline=60)
            assert queue.get(timeout=10) == "timed out", f"{kind}: a second lock for one file"
        finally:
            release.set()
            _join_all([holder], deadline=60)

    @pytest.mark.parametrize("kind", ALIAS_CLASSES)
    def test_writers_on_a_path_and_its_alias_form_one_chain(self, kind, tmp_path, target):
        alias, env = _alias(kind, tmp_path, tmp_path / "repo", target)
        paths = [target, alias, target, alias]
        envs = [{}, env, {}, env]
        writes = _run_chain(paths, 10, envs=envs, cwds=[tmp_path] * 4)
        _assert_one_chain(target, writes, 40)


def _fold_not_nfc() -> list[int]:
    """Every code point whose casefold is not NFC, from this interpreter's
    ``unicodedata`` (not a hand list): there ``NFC(name).casefold()`` gave two
    keys for one APFS name (#752 round 2: 8 found by brute force, 12 here,
    U+1FB7's title case needing NFD before the fold)."""
    nfc = functools.partial(unicodedata.normalize, "NFC")
    return [
        cp
        for cp in range(sys.maxunicode + 1)
        if not 0xD800 <= cp <= 0xDFFF and nfc(nfc(chr(cp)).casefold()) != nfc(chr(cp)).casefold()
    ]


FOLD_NOT_NFC = _fold_not_nfc()


def _spellings(c: str) -> set[str]:
    nfd = functools.partial(unicodedata.normalize, "NFD")
    forms = {c, c.lower(), c.upper(), c.casefold(), c.title()}
    return forms | {nfd(f) for f in forms} | {unicodedata.normalize("NFC", f) for f in forms}


class TestFoldThenNormalise:
    def test_the_generated_set_is_not_empty(self):
        assert 0x390 in FOLD_NOT_NFC and len(FOLD_NOT_NFC) >= 8

    @pytest.mark.parametrize("cp", FOLD_NOT_NFC, ids=lambda cp: f"U+{cp:04X}")
    def test_every_spelling_the_filesystem_treats_as_one_keys_equally(self, cp, tmp_path):
        """Canonically equivalent spellings key equally everywhere. Case
        variants key equally too, and on a filesystem that folds (APFS) each
        one that opens the same file is asserted to."""
        c = chr(cp)
        name = f"x{c}.json"
        (tmp_path / name).write_text("{}")
        nfd = functools.partial(unicodedata.normalize, "NFD")
        assert fl.lock_key(tmp_path / nfd(name)) == fl.lock_key(tmp_path / name)
        for s in _spellings(c):
            other = tmp_path / f"x{s}.json"
            if other.exists() and os.path.samefile(other, tmp_path / name):
                assert fl.lock_key(other) == fl.lock_key(tmp_path / name), (
                    f"U+{cp:04X}: {[f'U+{ord(x):04X}' for x in s]} is the same file"
                )


class TestDirectoryReplaced:
    """The key names the parent directory by inode. A directory replaced
    between taking the key and holding the lock would leave the lock on the
    old key: the key is recomputed under the lock, and a change retries."""

    def _replace_parent(self, f: Path) -> None:
        """Put a new directory where ``f``'s parent was, with the same files
        (the writer's temp file included): the same paths, a new inode."""
        old = f.parent.with_name(f"{f.parent.name}-old-{time.monotonic_ns()}")
        os.rename(f.parent, old)
        f.parent.mkdir()
        for child in old.iterdir():
            os.rename(child, f.parent / child.name)

    def test_a_replaced_directory_retries_on_the_new_key(self, tmp_path, monkeypatch):
        f = _entry(_repo(tmp_path / "repo") / "kb", text="base")
        before = fl.stripe_of(f)
        taken = []
        real = fl._open_stripe

        def replace_once(d, s):
            taken.append(s)
            if len(taken) == 1:
                self._replace_parent(f)
            return real(d, s)

        monkeypatch.setattr(fl, "_open_stripe", replace_once)
        keys = []
        real_key = fl.lock_key
        monkeypatch.setattr(fl, "lock_key", lambda p: (keys.append(real_key(p)), keys[-1])[1])
        atomic_write_text(f, "mine", expect=b"base")
        assert f.read_text() == "mine"
        assert len(set(keys)) == 2, "the key was not recomputed after the directory changed"
        assert taken[0] == before and taken[-1] == fl.stripe_of(f)

    def test_a_directory_replaced_every_time_ends_in_lock_timeout(self, tmp_path, monkeypatch):
        f = _entry(_repo(tmp_path / "repo") / "kb", text="base")
        real = fl._open_stripe

        def replace_always(d, s):
            self._replace_parent(f)
            return real(d, s)

        monkeypatch.setattr(fl, "_open_stripe", replace_always)
        start = time.monotonic()
        with pytest.raises(fl.LockTimeout):
            atomic_write_text(f, "mine", expect=b"base", lock_timeout=0.5)
        assert time.monotonic() - start < 5
        assert f.read_text() == "base"


class TestInProcessLock:
    def test_threads_exclude_each_other_when_the_os_lock_does_not(self, tmp_path, monkeypatch):
        """Where flock degrades to process-scoped record locks (NFS) only the
        in-process lock separates threads. Simulate that: flock does nothing,
        and a slow rename widens the compare-to-replace gap."""
        monkeypatch.setattr(fl.fcntl, "flock", lambda *a, **k: None)
        real_replace = os.replace

        def slow_replace(a, b):
            time.sleep(0.003)
            real_replace(a, b)

        monkeypatch.setattr(os, "replace", slow_replace)
        target = _entry(_repo(tmp_path / "repo") / "kb", "entry.json", "{}")
        writes, lock = [], threading.Lock()
        errors = []

        def work(i):
            try:
                for j in range(5):
                    base, _ = _set_key(str(target), "title", f"{i}-{j}", deadline)
                    with lock:
                        writes.append((base, f"{i}-{j}"))
            except Exception as e:  # pragma: no cover
                errors.append(e)

        deadline = time.monotonic() + _WRITER_DEADLINE
        threads = [threading.Thread(target=work, args=(i,), daemon=True) for i in range(6)]
        for t in threads:
            t.start()
        end = time.monotonic() + _GROUP_DEADLINE  # one deadline for the group
        for t in threads:
            t.join(timeout=max(0.0, end - time.monotonic()))
        assert not any(t.is_alive() for t in threads), "a writer thread hung"
        assert not errors
        _assert_one_chain(target, writes, 30)


class TestKilledHolder:
    def test_sigkill_of_the_holder_leaves_a_lock_acquired_within_a_second(self, tmp_path):
        entry = _entry(_repo(tmp_path / "repo") / "kb")
        ctx = multiprocessing.get_context("spawn")
        ready, release = ctx.Event(), ctx.Event()
        p = ctx.Process(target=_holder, args=(str(entry), ready, release))
        p.start()
        try:
            assert ready.wait(timeout=_BARRIER_TIMEOUT)
            with pytest.raises(fl.LockTimeout):  # really held
                with fl.file_lock(entry, timeout=0.1):
                    pass
            os.kill(p.pid, signal.SIGKILL)
            p.join(timeout=10)
            start = time.monotonic()
            with fl.file_lock(entry, timeout=1.0):
                pass
            assert time.monotonic() - start < 1.0
        finally:
            if p.is_alive():
                p.kill()


class TestStripes:
    def test_lock_files_are_bounded(self, tmp_path, monkeypatch):
        repo = _repo(tmp_path / "repo")
        (repo / "kb").mkdir()
        real = fl.lock_dir_for
        by_dir = functools.cache(lambda d: real(Path(d) / "x"))  # same answer, asked once
        monkeypatch.setattr(fl, "lock_dir_for", lambda p: by_dir(os.path.dirname(p)))
        for i in range(fl.STRIPES + 100):
            _lock_once(repo / "kb" / f"entry-{i}.md")
        assert len(list((repo / ".git" / "pyrite" / "locks").iterdir())) <= fl.STRIPES

    def test_a_move_takes_both_stripes_once_in_ascending_order(self, tmp_path, monkeypatch):
        repo = _repo(tmp_path / "repo")
        taken = []
        real = fl._open_stripe
        monkeypatch.setattr(fl, "_open_stripe", lambda d, s: (taken.append(s), real(d, s))[1])
        a, b = _entry(repo / "kb", "a.md"), _entry(repo / "kb", "b.md")
        for pair in ((a, b), (b, a), (a, a)):
            taken.clear()
            _lock_once(*pair)
            assert taken == sorted(set(taken))
            assert len(taken) == len({fl.stripe_of(p) for p in pair})

    def test_stripes_fold_case_and_unicode_form(self, tmp_path):
        assert fl.stripe_of(tmp_path / "Entry.md") == fl.stripe_of(tmp_path / "entry.md")
        assert fl.stripe_of(tmp_path / "café.md") == fl.stripe_of(tmp_path / "café.md")

    def test_flock_is_called_by_module_attribute(self):
        src = Path(fl.__file__).read_text()
        assert "from fcntl import" not in src and "fcntl.flock(" in src


class TestLockFailures:
    @pytest.mark.parametrize(
        "timeout",
        [
            None,
            0,
            -1.0,
            float("inf"),
            float("nan"),
            1e10,
            10**400,
            threading.TIMEOUT_MAX * 2,
            "5",
            True,
        ],
        ids=repr,
    )
    def test_an_unbounded_wait_is_rejected(self, tmp_path, timeout):
        """A stopped (not dead) holder keeps its lock; only a bounded wait
        turns that into LockTimeout instead of a hung writer."""
        entry = _entry(_repo(tmp_path / "repo") / "kb")
        with pytest.raises(ValueError, match="timeout"):
            _lock_once(entry, timeout=timeout)
        with pytest.raises(ValueError, match="timeout"):
            atomic_write_text(entry, "y", expect=b"x", lock_timeout=timeout)
        assert entry.read_text() == "x"

    def test_windows_is_unsupported_and_says_so(self, tmp_path, monkeypatch):
        entry = _entry(_repo(tmp_path / "repo") / "kb")
        monkeypatch.setattr(fl, "fcntl", None)
        with pytest.raises(fl.LockDirError, match="Windows"):
            _lock_once(entry)

    @pytest.mark.parametrize("timeout", [5.0])
    def test_a_filesystem_without_flock_fails_at_once(self, tmp_path, monkeypatch, timeout):
        """Some network filesystems answer flock with ENOTSUP or ENOLCK. That
        is not "busy": fail with a message, never spin until the timeout."""
        entry = _entry(_repo(tmp_path / "repo") / "kb")

        def no_flock(fd, op):
            raise OSError(errno.ENOLCK, os.strerror(errno.ENOLCK))

        monkeypatch.setattr(fl.fcntl, "flock", no_flock)
        start = time.monotonic()
        with pytest.raises(fl.LockDirError, match="filesystem"):
            _lock_once(entry, timeout=timeout)
        assert time.monotonic() - start < 2.0


def _fork_child(entry):
    pid = os.fork()
    if pid == 0:
        code = 1
        try:
            time.sleep(0.3)
            with fl.file_lock(entry, timeout=2.0):
                code = 0
        except BaseException:
            pass
        os._exit(code)
    return pid


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs fork")
def test_a_forked_child_does_not_inherit_a_locked_process_lock(tmp_path):
    entry = _entry(_repo(tmp_path / "repo") / "kb")
    held, go = threading.Event(), threading.Event()

    def holder():
        with fl.file_lock(entry):
            held.set()
            go.wait(10)

    th = threading.Thread(target=holder, daemon=True)
    th.start()
    assert held.wait(10)
    pid = _fork_child(entry)  # forked while the thread holds the stripe
    go.set()
    th.join(10)
    assert not th.is_alive()
    end = time.monotonic() + 30.0
    while True:
        done, status = os.waitpid(pid, os.WNOHANG)
        if done:
            break
        if time.monotonic() > end:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
            raise AssertionError("the forked child never finished")
        time.sleep(0.01)
    assert os.WEXITSTATUS(status) == 0, "the child timed out on an inherited locked lock"


# --------------------------------------------------------------------------
# The compare (A5), in a repository
# --------------------------------------------------------------------------


def _others(directory: Path, *keep: str) -> list[str]:
    return sorted(p.name for p in directory.iterdir() if p.name not in keep)


class TestCompare:
    @pytest.fixture
    def kb(self, tmp_path):
        return _repo(tmp_path / "repo") / "kb"

    def test_match_writes(self, kb):
        f = _entry(kb, text="old")
        atomic_write_text(f, "new", expect=b"old")
        assert f.read_text() == "new"
        atomic_write_text(f, "newer", expect=hashlib.sha256(b"new").hexdigest())
        assert f.read_text() == "newer"

    def test_mismatch_writes_nothing_and_carries_what_is_on_disk(self, kb):
        f = _entry(kb, text="theirs")
        with pytest.raises(FileChanged) as ei:
            atomic_write_text(f, "mine", expect=b"base")
        assert ei.value.on_disk == b"theirs"
        assert f.read_bytes() == b"theirs"
        assert _others(kb) == ["e.md"]  # no temp file left

    def test_absent(self, kb):
        kb.mkdir()
        f = kb / "e.md"
        atomic_write_text(f, "created", expect=ABSENT)
        with pytest.raises(FileChanged):
            atomic_write_text(f, "again", expect=ABSENT)
        assert f.read_text() == "created"
        with pytest.raises(FileChanged) as ei:
            atomic_write_text(kb / "gone.md", "x", expect=b"was here")
        assert ei.value.on_disk is None

    def test_a_kb_outside_git_is_refused_for_expect_and_written_without(self, tmp_path):
        f = _entry(tmp_path / "plain", text="old")
        with pytest.raises(fl.NotInGitRepository, match="not in a git repository"):
            atomic_write_text(f, "new", expect=b"old")
        assert f.read_text() == "old"
        assert _others(f.parent) == ["e.md"]
        atomic_write_text(f, "new")  # no expect: unchanged behaviour
        assert f.read_text() == "new"

    def test_without_expect_nothing_is_locked_or_looked_up(self, kb, monkeypatch):
        monkeypatch.setattr(fl.fcntl, "flock", lambda *a, **k: pytest.fail("locked"))
        monkeypatch.setattr(fl, "lock_dir_for", lambda *a: pytest.fail("looked up"))
        f = _entry(kb, text="old")
        atomic_write_text(f, "new")
        assert f.read_text() == "new"
        assert not (kb.parent / ".git" / "pyrite").exists()

    def test_compare_runs_under_the_lock_immediately_before_the_rename(self, kb, monkeypatch):
        """A writer that lands between the temp write and the rename is seen."""
        f = _entry(kb, text="base")
        real_open = fl._open_stripe

        def intrude(d, s):  # runs as the lock is being taken, i.e. after the temp write
            f.write_bytes(b"theirs")
            return real_open(d, s)

        monkeypatch.setattr(fl, "_open_stripe", intrude)
        with pytest.raises(FileChanged):
            atomic_write_text(f, "mine", expect=b"base")
        assert f.read_bytes() == b"theirs"

    def test_an_expect_write_to_a_hard_linked_file_is_refused(self, kb):
        """Another name for the same inode would be another lock key (A2,
        2026-10-07): refused, with a reason, through either name."""
        f = _entry(kb, text="theirs")
        os.link(f, kb / "alias.md")
        for name in (f, kb / "alias.md"):
            for expect in (b"theirs", b"base"):
                with pytest.raises(aw.HardLinked, match="hard link"):
                    atomic_write_text(name, "mine", expect=expect)
        assert f.read_bytes() == b"theirs"
        assert _others(kb) == ["alias.md", "e.md"]
        atomic_write_text(f, "mine")  # without expect: as before, in place
        assert (kb / "alias.md").read_text() == "mine"

    def test_a_hard_link_made_after_the_first_check_is_still_refused(self, kb, monkeypatch):
        """The link count is read under the lock, before the compare."""
        f = _entry(kb, text="base")
        real_open = fl._open_stripe

        def link_then_lock(d, s):
            if not (kb / "late.md").exists():
                os.link(f, kb / "late.md")
            return real_open(d, s)

        monkeypatch.setattr(fl, "_open_stripe", link_then_lock)
        with pytest.raises(aw.HardLinked):
            atomic_write_text(f, "mine", expect=b"base")
        assert f.read_bytes() == b"base"

    def test_a_wait_for_the_lock_is_bounded(self, kb):
        f = _entry(kb, text="base")
        held, go = threading.Event(), threading.Event()

        def holder():
            with fl.file_lock(f):
                held.set()
                go.wait(10)

        th = threading.Thread(target=holder, daemon=True)
        th.start()
        assert held.wait(10)
        try:
            start = time.monotonic()
            with pytest.raises(fl.LockTimeout):
                atomic_write_text(f, "mine", expect=b"base", lock_timeout=0.2)
            assert time.monotonic() - start < 5
        finally:
            go.set()
            th.join(10)
        assert f.read_bytes() == b"base"
        assert _others(kb) == ["e.md"]  # no temp left
        assert aw.LOCK_TIMEOUT > 0

    def test_text_is_not_mistaken_for_a_digest(self, kb):
        f = _entry(kb, text="old")
        for bad in ("old", "abc", "z" * 64):
            with pytest.raises(ValueError):
                atomic_write_text(f, "new", expect=bad)
        for bad in (123, bytearray(b"old"), False):
            with pytest.raises(TypeError):
                atomic_write_text(f, "new", expect=bad)
        assert f.read_text() == "old"
        atomic_write_text(f, "new", expect=hashlib.sha256(b"old").hexdigest().upper())
        assert f.read_text() == "new"

    def test_the_unwritable_directory_path_compares_before_the_truncate(self, kb, monkeypatch):
        f = _entry(kb, text="theirs")
        real_open = os.open

        def no_temp(path, *a, **k):
            if str(path).endswith(".tmp"):
                raise PermissionError(13, "denied", str(path))
            return real_open(path, *a, **k)

        monkeypatch.setattr(os, "open", no_temp)
        with pytest.raises(FileChanged):
            atomic_write_text(f, "mine", expect=b"base")
        assert f.read_bytes() == b"theirs"
        atomic_write_text(f, "mine", expect=b"theirs")
        assert f.read_bytes() == b"mine"

    def test_the_owner_fallback_path_compares_before_the_truncate(self, kb, monkeypatch):
        monkeypatch.setattr(aw, "_owner_restored", lambda *a: False)
        f = _entry(kb, text="theirs")
        with pytest.raises(FileChanged):
            atomic_write_text(f, "mine", expect=b"base")
        assert f.read_bytes() == b"theirs"
        assert _others(kb) == ["e.md"]
        atomic_write_text(f, "mine", expect=b"theirs")
        assert f.read_bytes() == b"mine"
