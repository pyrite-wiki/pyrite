#!/usr/bin/env python3
"""Refuse a pull request that adds a row to a ratchet list (#1608).

A ratchet is a hand-written list that may only shrink. Removing rows needs no
other edit, so two pull requests that remove different rows never touch the
same line. Adding a row is a reviewed decision: it fails this check unless a
commit in BASE..HEAD carries a trailer in its final paragraph:

    Ratchet-raise: <list name> <reason>

Usage: ``check_ratchets_only_shrink.py --range BASE HEAD`` (exit 0 pass, 1 a
row was added without the trailer or a list could not be read, 2 usage).

For each ``(path, name)`` in REGISTRY the script reads the file at BASE and at
HEAD with ``git show``, parses it with ``ast`` (it never imports the file),
finds the module-level assignment to ``name`` and reads its rows with
``ast.literal_eval``: a set, list or tuple gives its elements, a dict its keys.
A list absent at BASE counts as empty there. A file or name missing at HEAD,
or a value that is not a literal, FAILS: the check never passes by not being
able to look. Stdlib only.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

# (path, name) of every ratchet list guarded by the range check. Slice 2 of
# #1608 registers the others.
REGISTRY: list[tuple[str, str]] = [
    ("tests/test_cli_numeric_options_declare_a_range.py", "NOT_YET"),
]

TRAILER_KEY = "Ratchet-raise"


class RatchetReadError(Exception):
    """The list cannot be read as a literal; the message says why."""


def read_rows(source: str, name: str) -> set:
    """The rows of the module-level literal ``name`` in ``source``.

    Raises RatchetReadError when the name is not assigned at module level or
    its value is not a literal set, list, tuple or dict.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise RatchetReadError(f"the file does not parse ({exc.msg}, line {exc.lineno})") from exc
    value = None
    for node in tree.body:  # the last module-level assignment wins, as at import
        if isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
                value = node.value
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == name and node.value:
                value = node.value
    if value is None:
        raise RatchetReadError(f"no module-level assignment to {name}")
    try:
        literal = ast.literal_eval(value)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError) as exc:
        raise RatchetReadError(
            f"the value of {name} is not a literal (a set, list, tuple or dict of literals)"
        ) from exc
    if isinstance(literal, dict):
        return set(literal.keys())
    if isinstance(literal, (set, frozenset, list, tuple)):
        return set(literal)
    raise RatchetReadError(f"{name} is a {type(literal).__name__}, not a set, list, tuple or dict")


def _git(args: list[str], cwd) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def _show(rev: str, path: str, cwd) -> str | None:
    """File contents at ``rev``, None when the path does not exist there.

    An unknown revision raises CalledProcessError (git's own error).
    """
    _git_ok(["rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"], cwd)
    result = _git(["cat-file", "-e", f"{rev}:{path}"], cwd)
    if result.returncode != 0:
        return None
    return _git_ok(["show", f"{rev}:{path}"], cwd).stdout


def _git_ok(args: list[str], cwd) -> subprocess.CompletedProcess:
    result = _git(args, cwd)
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, ["git", *args], output=result.stdout, stderr=result.stderr
        )
    return result


def raised_lists(base: str, head: str, cwd=None) -> set[str]:
    """Names with a non-empty-reason ``Ratchet-raise`` trailer in base..head."""
    out = _git_ok(
        [
            "log",
            f"--format=%x01%(trailers:key={TRAILER_KEY},valueonly,unfold)",
            f"{base}..{head}",
        ],
        cwd,
    ).stdout
    names: set[str] = set()
    for line in out.replace("\x01", "\n").splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and parts[1].strip():
            names.add(parts[0])
    return names


def check_range(base: str, head: str, registry=None, cwd=None) -> int:
    """0 if no registered list gained a row without its trailer, else 1."""
    registry = REGISTRY if registry is None else registry
    raised = raised_lists(base, head, cwd)
    failed = False
    for path, name in registry:
        head_src = _show(head, path, cwd)
        if head_src is None:
            print(
                f"ERROR: {path} does not exist at {head}, so ratchet list {name} cannot be read.\n"
                "  Renaming or moving a ratchet needs its registry entry in "
                "scripts/check_ratchets_only_shrink.py updated in the same change.",
                file=sys.stderr,
            )
            failed = True
            continue
        try:
            head_rows = read_rows(head_src, name)
        except RatchetReadError as exc:
            print(
                f"ERROR: cannot read ratchet list {name} in {path} at {head}: {exc}.\n"
                "  Keep it a literal, or update scripts/check_ratchets_only_shrink.py.",
                file=sys.stderr,
            )
            failed = True
            continue
        base_src = _show(base, path, cwd)
        base_rows: set = set()
        if base_src is not None:
            try:
                base_rows = read_rows(base_src, name)
            except RatchetReadError:
                base_rows = set()  # not a readable list at BASE: counts as empty there
        added = sorted(head_rows - base_rows, key=repr)
        if not added or name in raised:
            continue
        failed = True
        print(
            f"ERROR: ratchet list {name} in {path} gained {len(added)} row(s) "
            f"between {base} and {head}:",
            file=sys.stderr,
        )
        for row in added:
            print(f"  {row!r}", file=sys.stderr)
        print(
            "  A ratchet list only shrinks. Remove the rows, or if adding them is deliberate, "
            "put this line in the final paragraph of a commit message in the range:\n"
            f"    {TRAILER_KEY}: {name} <reason>",
            file=sys.stderr,
        )
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 3 or argv[0] != "--range":
        print("usage: check_ratchets_only_shrink.py --range BASE HEAD", file=sys.stderr)
        return 2
    try:
        return check_range(argv[1], argv[2], cwd=Path.cwd())
    except subprocess.CalledProcessError as exc:
        print(f"ERROR: {' '.join(exc.cmd)} failed:\n{exc.stderr}", file=sys.stderr)
        return exc.returncode or 1


if __name__ == "__main__":
    sys.exit(main())
