#!/usr/bin/env python3
"""Run docs/getting-started.md as a test: docs-as-tests.

The 2026-07-02 docs audit found the docs excellent wherever an agent loop
exercises them daily and fictional wherever nobody has executed them since
writing (`pip install pyrite` DOA, a `--tier` flag that did not exist, a
git-init claim that was false). One-time fixes re-rot. The durable fix is to
put the tutorial under the same discipline as the code: extract its fenced
bash blocks, run them in order against the installed package, and fail on the
first non-zero exit.

What this deliberately does:

- runs the blocks **in one shell session**, in order, so `cd my-research` in
  one block is still in effect for the next. Running each block in a fresh
  shell would pass while the tutorial as a human follows it is broken.
- runs in a **temp HOME**, so `pyrite init`, `~/.pyrite/config.yaml` and
  `pyrite mcp-setup` (which writes `~/.claude/claude_desktop_config.json`)
  touch nothing of the caller's.
- asserts the doc's own keyword `pyrite search` examples **return at least
  one result**, which forces the example queries to actually match the
  example data -- an exit-0 assertion alone would pass on an empty result
  set. `--mode semantic`/`--mode hybrid` are exit-code-only for now; see
  EMBEDDING_MODE_RE and issue #43.
- finishes with `pyrite index health` and fails on `unhealthy`, printing a
  `warning` rather than failing on it (issue #44). The audit found a fresh
  tutorial KB failing the tool's own health check; it still does, and this
  is where that stays visible.

What it skips, and why (each skip is a line the tutorial shows but a CI run
must not execute, not a line that is allowed to be wrong):

- the install block: CI has already installed the package, and it would
  clone the repo into the temp HOME.
- `pyrite serve`: a long-running server with no terminating condition.

Usage:  python scripts/run_tutorial.py [path/to/getting-started.md]
Exit:   0 if every block ran and every assertion held; 1 otherwise.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_DOC = REPO / "docs" / "getting-started.md"

FENCE = re.compile(r"^```(\w*)[^\n]*\n(.*?)^```", re.S | re.M)

# A block is skipped when any of these appears in it. Kept as substrings of
# the command rather than block indexes so inserting a paragraph in the doc
# does not silently change which blocks run.
SKIP_MARKERS = (
    "git clone",  # install block: CI already installed the package
    "pip install",  # ditto
    "docker compose",  # not this job's surface
    "pyrite serve",  # long-running server, no terminating condition
)

# Commands whose output shape is asserted, not just their exit code.
SEARCH_RE = re.compile(r"^\s*pyrite search\b", re.M)

# Semantic and hybrid search need a vector index, which needs the embedding
# model. Their exit code is still asserted -- a documented flag that does not
# exist is exactly the `--tier` class of bug this runner exists to prevent --
# but requiring a non-empty result set would either force a ~90 MB model
# download in CI or fail for a reason that has nothing to do with the doc.
EMBEDDING_MODE_RE = re.compile(r"--mode\s+(semantic|hybrid)")


class TutorialError(Exception):
    pass


def extract_blocks(doc: Path) -> list[str]:
    """Fenced ```bash blocks, in document order."""
    return [body for lang, body in FENCE.findall(doc.read_text()) if lang == "bash"]


def should_skip(block: str) -> str | None:
    for marker in SKIP_MARKERS:
        if marker in block:
            return marker
    return None


def run_block(block: str, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    """Run one block in bash with `set -e`, from `cwd`.

    Returns the completed process; the caller decides what to do with it. The
    block's own `cd` is honoured by returning the resulting directory through
    a marker line, so the next block starts where this one ended.
    """
    script = f"set -euo pipefail\ncd {cwd!s}\n{block}\nprintf '%s\\n' \"__CWD__$PWD\"\n"
    return subprocess.run(
        ["bash", "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )


def resulting_cwd(stdout: str, fallback: Path) -> Path:
    for line in reversed(stdout.splitlines()):
        if line.startswith("__CWD__"):
            candidate = Path(line[len("__CWD__") :])
            return candidate if candidate.is_dir() else fallback
    return fallback


def strip_marker(stdout: str) -> str:
    return "\n".join(line for line in stdout.splitlines() if not line.startswith("__CWD__"))


def assert_search_returned_results(block: str, stdout: str) -> None:
    """A documented search must match the documented data.

    `pyrite search` exits 0 on an empty result set, so an exit-code check
    alone would let the doc drift until none of its example queries matched
    any of its example entries -- which is exactly the failure the audit
    found elsewhere in these docs.

    Exception: `--mode semantic` and `--mode hybrid` (see EMBEDDING_MODE_RE).
    Their exit code is still asserted. Tighten this to a full result
    assertion when issue #43 lands -- today a fresh tutorial KB has no
    embeddings at all, so requiring results here would fail on the product
    bug rather than on doc drift.
    """
    if not SEARCH_RE.search(block) or EMBEDDING_MODE_RE.search(block):
        return
    text = strip_marker(stdout)
    # The CLI's default output is JSON; count either a results array or the
    # rich table's rows. Both shapes are checked so a formatter change is a
    # visible failure here rather than a silently weakened assertion.
    import json

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        if not text.strip():
            raise TutorialError(
                f"`pyrite search` in the tutorial printed nothing:\n{block}"
            ) from None
        return
    results = payload.get("results", payload if isinstance(payload, list) else [])
    if len(results) < 1:
        raise TutorialError(
            f"a documented search returned 0 results -- the tutorial's example "
            f"query no longer matches its example data:\n{block}\n{text}"
        )


def check_index_health(kb_name: str, cwd: Path, env: dict[str, str]) -> None:
    """`pyrite index health` must not report `unhealthy` for the tutorial KB.

    There is no `-k` filter on this command yet (issue #18), so the JSON is
    filtered here by KB: an unrelated KB in the caller's config must not fail
    the tutorial, and a problem in the tutorial's own KB must not be hidden by
    a global "ok".

    `warning` is printed, not failed on -- yet. A KB built exactly as the
    tutorial says currently warns `subdirectory_mismatches` on every entry
    because the declared `people/` is compared to the actual `people` without
    stripping the slash (issue #44). Failing on `warning` today would make
    this job permanently red for a bug that has nothing to do with the doc.
    Tighten to `in ("unhealthy", "warning")` when #44 lands -- that is the
    assertion `ci-run-getting-started-tutorial` actually asked for.
    """
    import json

    proc = subprocess.run(
        ["pyrite", "index", "health"],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if proc.returncode != 0:
        raise TutorialError(
            f"`pyrite index health` exited {proc.returncode}\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise TutorialError(f"`pyrite index health` did not print JSON:\n{proc.stdout}") from None

    # `checks` values are mostly lists of {kb, path, id} rows, but not all:
    # `broken_links` is a bare count. Only the row-shaped ones can be
    # attributed to a KB, which is the whole reason for filtering here.
    problems: dict[str, list] = {}
    for check, rows in (report.get("checks") or {}).items():
        if not isinstance(rows, list):
            continue
        ours = [row for row in rows if isinstance(row, dict) and row.get("kb") == kb_name]
        if ours:
            problems[check] = ours

    status = report.get("status")
    counts = {check: len(rows) for check, rows in problems.items()}
    if status == "unhealthy" and problems:
        raise TutorialError(
            f"`pyrite index health` is unhealthy and the tutorial KB {kb_name!r} is "
            f"implicated: {counts}\n{json.dumps(problems, indent=2)[:4000]}"
        )
    if problems:
        # Visible, not fatal: see the docstring. A silent pass here is how the
        # audit's finding stayed invisible in the first place.
        print(f"  index health: {status}; {kb_name} rows in {counts} (see issue #44)")
    else:
        print(f"  index health: {status} (no {kb_name} rows in any failing check)")


def main(argv: list[str]) -> int:
    doc = Path(argv[1]).resolve() if len(argv) > 1 else DEFAULT_DOC
    if not doc.exists():
        print(f"no such doc: {doc}", file=sys.stderr)
        return 1

    if not shutil.which("pyrite"):
        print(
            "`pyrite` is not on PATH. The tutorial runner drives the INSTALLED "
            "package, the way a reader of the doc would: install it first "
            '(`pip install -e ".[all]"`).',
            file=sys.stderr,
        )
        return 1

    blocks = extract_blocks(doc)
    if not blocks:
        print(f"no bash blocks found in {doc}", file=sys.stderr)
        return 1

    home = Path(tempfile.mkdtemp(prefix="pyrite-tutorial-home-"))
    env = dict(os.environ)
    env.update(
        {
            "HOME": str(home),
            # The doc's own contract: ~/.pyrite holds the config and the index.
            # Pinning it to the temp HOME keeps the run off the caller's config
            # without changing what the doc says.
            "PYRITE_DATA_DIR": str(home / ".pyrite"),
            "PYRITE_CONFIG_DIR": str(home / ".pyrite"),
            # No model download in CI. Semantic/hybrid blocks fall back or fail
            # visibly rather than pulling ~90 MB.
            "PYRITE_AUTO_EMBED": "0",
            "HF_HUB_OFFLINE": "1",
            "NO_COLOR": "1",
        }
    )
    # `pyrite mcp-setup` registers the server with the MCP clients it finds and
    # exits 1 when it finds none (#582), and a CI runner has none. A stub
    # `claude` stands in for Claude Code, first on PATH so a caller's real one
    # is never called; it records each call in the temp HOME.
    stub_bin = home / ".tutorial-bin"
    stub_bin.mkdir()
    stub = stub_bin / "claude"
    stub.write_text('#!/bin/sh\necho "$*" >> "$HOME/.tutorial-claude-calls"\n')
    stub.chmod(0o755)
    env["PATH"] = f"{stub_bin}{os.pathsep}{env.get('PATH', '')}"
    # Git identity: the tutorial's git block commits, and a CI runner has no
    # global identity configured.
    env.setdefault("GIT_AUTHOR_NAME", "Pyrite Tutorial")
    env.setdefault("GIT_AUTHOR_EMAIL", "tutorial@example.invalid")
    env.setdefault("GIT_COMMITTER_NAME", "Pyrite Tutorial")
    env.setdefault("GIT_COMMITTER_EMAIL", "tutorial@example.invalid")

    print(f"running {doc.relative_to(REPO) if doc.is_relative_to(REPO) else doc}")
    print(f"  HOME={home}")

    cwd = home
    ran = 0
    try:
        for number, block in enumerate(blocks, start=1):
            marker = should_skip(block)
            if marker:
                print(f"  [{number}] skipped ({marker})")
                continue

            first = block.strip().splitlines()[0]
            print(f"  [{number}] {first}")
            proc = run_block(block, cwd, env)
            if proc.returncode != 0:
                raise TutorialError(
                    f"block {number} of {doc.name} exited {proc.returncode}:\n"
                    f"{block}\n--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
                )
            assert_search_returned_results(block, proc.stdout)
            cwd = resulting_cwd(proc.stdout, cwd)
            ran += 1

        check_index_health("my-research", cwd, env)
    except TutorialError as failure:
        print(f"\nFAIL: {failure}", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(home, ignore_errors=True)

    print(f"\nOK: {ran} block(s) from {doc.name} ran clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
