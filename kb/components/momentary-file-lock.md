---
id: momentary-file-lock
title: Momentary file lock
type: component
tags:
- core
- storage
- concurrency
importance: 5
kind: module
path: pyrite/utils/file_lock.py
owner: core
---

The momentary lock that `atomic_write_text(expect=...)` (`pyrite/utils/atomic_write.py`) takes for the instant of compare-and-replace (ADR-0042 decision 10; §10a A1, A2, A5). Nothing calls `expect=` yet; B6 P3 wires the writers.

## Where the lock is

`lock_dir_for(path)` is the one answer: `<git-dir>/pyrite/locks` of the repository whose **work tree** holds `realpath(path)`, beside git's own `index.lock`. Nothing overrides it (no environment variable, no argument). It follows git's discovery rules without running git, and reads no environment: with `GIT_DIR` exported, `git rev-parse` names another repository for the same file, and two writers of one file must never resolve two lock dirs. A linked worktree locks in its own git dir (`.git/worktrees/<name>`). Refused with `NotInGitRepository`: a file outside any work tree, inside a git dir, or in a bare repository. Also refused (`LockDirError`): a git dir this user cannot write, a move between two repositories, and Windows. `tests/test_file_lock.py::TestWhereTheLockIs` checks agreement with `git rev-parse` on each layout.

## Permissions

`pyrite/` and `locks/` get the git dir's own mode bits (setgid kept), whatever the umask. An existing one that is wider than the git dir is refused. Stripe files are read-write for exactly the classes that can write the git dir.

## One file, one lock (A2, maintainer 2026-10-07)

The lock key is the `(st_dev, st_ino)` of the entry's parent directory plus its name folded as `NFD(casefold(NFD(name)))`, Unicode's canonical caseless match (`lock_key`). The fold matched APFS for every code point to U+2FFFF; `NFC` before the fold did not (U+0390, U+1FB7). The key is recomputed under the lock, and a parent directory replaced meanwhile releases and retakes the lock on the new key. Every path that reaches the entry gives the same key: a symlinked directory or file, `..` segments, case and Unicode spelling, the macOS firmlink `/System/Volumes/Data`, and a process with `GIT_DIR` naming another repository. The atomic rename keeps the key. `tests/test_file_lock.py::TestOneFileOneLock` drives each alias class through real processes. An `expect=` write to a file with more than one hard link is refused (`HardLinked`), because another name for the inode would be another key.

## Stripes

1024 stripe files, `sha256(lock_key) mod 1024`, never deleted. A move takes both stripes in ascending order. Each stripe has an OS `flock` and an in-process `threading.Lock`, reinitialised after fork. Every wait is bounded: `timeout`/`lock_timeout` default to `LOCK_TIMEOUT` (10 s), and `None`, zero, negative, infinite, NaN, a value above `threading.TIMEOUT_MAX` or one that cannot be converted to a float is a `ValueError`. An `flock` that is unsupported (ENOLCK/ENOTSUP on some network filesystems) fails at once.

## Limits

- **A stopped holder.** A holder that is stopped rather than dead (`SIGSTOP`, a debugger, a suspended job) keeps its lock, and writers wait until `LockTimeout`. The kernel frees the lock of a holder that dies.
- **`core.worktree` or `GIT_DIR`.** A KB reachable only through `core.worktree` or `GIT_DIR` (its git dir is not found by walking up from the work tree) is refused as not in a git repository.
- **The entry's directory moved away during a write**: the rename path fails closed (`FileNotFoundError`, nothing written; the temp file may be left in the moved directory, safe to delete). On the in-place fallback, a directory swapped after the key recheck can let two writers hold the lock (#774).
- **Hosts and mounts.** The lock is per host only. Some NFS/SMB mounts do not support `flock`. A second mount of one filesystem that reports another `st_dev` gives other keys (by reasoning; not run).
- See the module docstring for the rest.
