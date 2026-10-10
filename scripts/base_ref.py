"""One integration-base policy for contributor scripts (#665)."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

HINT = (
    "git remote add upstream https://github.com/pyrite-wiki/pyrite.git "
    "&& git fetch upstream dev; or supply --base REF"
)


class BaseRefError(ValueError):
    pass


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)


def resolve_base(root: Path, explicit: str | None = None, *, fetch: bool = False) -> str:
    """Explicit overrides never fall back; only worktree creation fetches."""
    override = explicit if explicit is not None else os.environ.get("PYRITE_BASE")
    candidates = [override] if override is not None else ["upstream/dev", "origin/dev", "dev"]
    remotes = _git(root, "remote").stdout.splitlines() if fetch and override is None else []
    for ref in candidates:
        remote = ref.split("/", 1)[0]
        if remote in remotes:
            fetched = _git(root, "fetch", "-q", remote, "dev")
            if fetched.returncode:
                print(
                    f"note: could not fetch {remote}/dev; trying the existing local ref",
                    file=sys.stderr,
                )
        if (
            ref
            and _git(
                root, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{ref}^{{commit}}"
            ).returncode
            == 0
        ):
            return ref
    description = (
        f"base {override!r} is not a commit"
        if override is not None
        else "no integration base exists"
    )
    hint = (
        "git fetch upstream dev; or supply --base REF"
        if "upstream" in _git(root, "remote").stdout.splitlines()
        else HINT
    )
    raise BaseRefError(f"{description}; {hint}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base")
    parser.add_argument("--fetch", action="store_true")
    args = parser.parse_args()
    try:
        print(resolve_base(Path.cwd(), args.base, fetch=args.fetch))
    except BaseRefError as exc:
        print(f"base-ref: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
