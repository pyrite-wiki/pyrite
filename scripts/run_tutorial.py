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
  `pyrite mcp-setup` (which asks a stub `claude` to add the server, and
  writes Claude Desktop's config when its directory exists) touch nothing of
  the caller's.
- asserts the doc's own keyword `pyrite search` examples **return at least
  one result**, which forces the example queries to actually match the
  example data -- an exit-0 assertion alone would pass on an empty result
  set. Each search sits in its own block, so each is asserted on its own.
  A `--mode semantic`/`--mode hybrid` search with no results passes only when
  it said why: the `pyrite index embed` warning (#43).
- refuses a **`&&` list** in a block it runs. Under `set -e` a failing command
  that is not the last in an `a && b` list does not stop the shell, so a
  failed `cd` passed a block that did nothing. Write one command per line.
- finishes with `pyrite index health` and fails on `unhealthy`, printing a
  `warning` rather than failing on it (issue #44). The audit found a fresh
  tutorial KB failing the tool's own health check; it still does, and this
  is where that stays visible.

- reads **runner directives**, HTML comments a reader never sees, for the few
  things a document needs from the runner that fenced commands cannot say:

  `<!-- runner: expect-ids ID [KB:ID ...] -->`  after a ```bash block: the
      block's JSON output must list every one of these entries. A search that
      "returns something" passes on the wrong entries; this names the right ones.
  `<!-- runner: expect-exit N -->`  before a ```bash block: the block must
      exit N (not 0). For the commands a tutorial shows being refused.
  `<!-- runner: expect-text WORD -->`  after a ```bash block: WORD appears in
      its output (stdout or stderr). Repeat the line for more words.
  `<!-- runner: seed FIXTURE_DIR TARGET_DIR -->`  stands in for a step only a
      person with an agent can do (docs/tutorials/pyrite-in-20-minutes.md's
      "ask Claude" steps): copies the fixture's files into TARGET_DIR (under the
      current directory) and runs `pyrite index sync`, which is what the agent's
      write would have done. The commands that CHECK the result still run.
  `<!-- runner: health-kb NAME -->`  the KB whose `pyrite index health` rows
      must be clean at the end (default `my-research`, getting-started's).

What it skips, and why (each skip is a line the tutorial shows but a CI run
must not execute, not a line that is allowed to be wrong):

- the install block: CI has already installed the package, and it would
  clone the repo into the temp HOME. The checkout under test is linked in as
  `pyrite/` where the clone would have put it, so later steps that read
  `pyrite/kb/` read the KB of the code being tested. (Cloning any other
  repository, such as the demo KBs, is run: that is the document's claim.)
- `pyrite serve`: a long-running server with no terminating condition.
- `npm install` / `npm run build`: the web UI build, which needs Node and a clone.

Usage:  python scripts/run_tutorial.py [path/to/doc.md]   (default: docs/getting-started.md)
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
DIRECTIVE = re.compile(r"<!--\s*runner:\s*(.*?)\s*-->", re.S)
_ITEM = re.compile(FENCE.pattern + "|" + DIRECTIVE.pattern, re.S | re.M)

# A block is skipped when any of these appears in it. Kept as substrings of
# the command rather than block indexes so inserting a paragraph in the doc
# does not silently change which blocks run.
SKIP_MARKERS = (
    "pip install",  # install block: CI already installed the package
    "docker compose",  # not this job's surface
    "pyrite serve",  # long-running server, no terminating condition
    "npm install",  # the web UI build: a clone's web/, not a CLI step
)

# Commands whose output shape is asserted, not just their exit code.
SEARCH_RE = re.compile(r"^\s*pyrite search\b", re.M)

# Semantic and hybrid search need a vector index, which needs the embedding
# model. The runner is offline (HF_HUB_OFFLINE), so such a search finds
# nothing; what it must do is say why (#43). An empty result is accepted only
# with this text in the output.
MODE_RE = re.compile(r"--mode\s+(semantic|hybrid)")
NO_EMBEDDINGS_WARNING = "pyrite index embed"

_QUOTED = re.compile(r"\"(?:\\.|[^\"\\])*\"|'[^']*'")


class TutorialError(Exception):
    pass


#: Where the stub `claude` records its calls, relative to the temp HOME.
CLAUDE_CALLS = ".tutorial-claude-calls.jsonl"

STUB_CLAUDE = """#!{python}
import json, os, sys
with open(os.path.join(os.environ["HOME"], "{log}"), "a") as log:
    log.write(json.dumps(sys.argv[1:]) + "\\n")
"""


def check_claude_calls(home: Path, ran_mcp_setup: bool) -> None:
    """Read back every `claude mcp add` the doc caused (#582): the command a
    client will start must be an absolute path that exists, with an explicit
    --tier. An exit code alone passed a setup that wrote a bare `pyrite`."""
    import json

    log = home / CLAUDE_CALLS
    calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    adds = [call for call in calls if call[:2] == ["mcp", "add"]]
    if ran_mcp_setup and not adds:
        raise TutorialError("`pyrite mcp-setup` ran but asked Claude Code to add nothing")
    for call in adds:
        if "--" not in call or call.index("--") == len(call) - 1:
            raise TutorialError(f"`claude mcp add` was given no command to run: {call}")
        command, *args = call[call.index("--") + 1 :]
        if not os.path.isabs(command) or not os.path.isfile(command):
            raise TutorialError(
                f"`claude mcp add` registered {command!r}, which is not an absolute path that "
                "exists, so a client could not start it"
            )
        if "--tier" not in args:
            raise TutorialError(f"`claude mcp add` registered no explicit --tier: {call}")


def extract_blocks(doc: Path) -> list[str]:
    """Fenced ```bash blocks, in document order."""
    return [body for kind, body in extract_items(doc) if kind == "block"]


def extract_items(doc: Path) -> list[tuple[str, str]]:
    """("block", bash source) and ("directive", text) in document order."""
    items: list[tuple[str, str]] = []
    for match in _ITEM.finditer(doc.read_text()):
        lang, body, directive = match.groups()
        if directive is not None:
            items.append(("directive", directive))
        elif lang == "bash":
            items.append(("block", body))
    return items


def stand_in_for_clone(target: Path) -> None:
    """What `git clone` + `python3 -m venv pyrite/.venv` would have left behind.

    The checkout under test, as what a tutorial reads: its KB and skills as a
    COPY: with the software extension installed (CI has it) Pyrite creates
    `kb/_templates/` in a software KB it loads, and a run must leave the
    checkout, and so a release's clean-tree check, as it found it. The rest
    are links. And an `activate` that
    does nothing: the runner's own `pyrite` is already on PATH, and a tutorial
    tells the reader to `source` it again in a new terminal.
    """
    target.mkdir()
    for name in ("kb", ".claude"):
        if (REPO / name).is_dir():
            shutil.copytree(REPO / name, target / name, symlinks=True)
    for name in ("docs", "CLAUDE.md", "README.md"):
        if (REPO / name).exists():
            (target / name).symlink_to(REPO / name)
    activate = target / ".venv" / "bin" / "activate"
    activate.parent.mkdir(parents=True)
    activate.write_text("# stand-in: the runner's pyrite is already on PATH\n")


def assert_expected_ids(block: str, stdout: str, wanted: list[str]) -> None:
    """Every `expect-ids` entry must be in the block's JSON results.

    An item is `ID` (any KB) or `KB:ID`. Results are read from the `results`
    list of the CLI's JSON; a block that did not print JSON cannot satisfy it.
    """
    import json

    text = strip_marker(stdout)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        raise TutorialError(f"expect-ids needs JSON output, got:\n{block}\n{text[:500]}") from None
    rows = payload.get("results", payload if isinstance(payload, list) else [])
    have = {(row.get("kb_name"), row.get("id")) for row in rows if isinstance(row, dict)}
    missing = []
    for want in wanted:
        kb, _, entry_id = want.rpartition(":")
        if not any(i == entry_id and (not kb or k == kb) for k, i in have):
            missing.append(want)
    if missing:
        listed = ", ".join(f"{k}:{i}" for k, i in sorted(have, key=str))
        raise TutorialError(
            f"the tutorial says this command finds {', '.join(missing)}, and it does not:\n"
            f"{block}\nit found: {listed}"
        )


def seed_fixture(fixture: str, target: str, cwd: Path, env: dict[str, str]) -> None:
    """Copy a fixture tree into `cwd/target` and index it (the `seed` directive)."""
    source = REPO / fixture
    if not source.is_dir():
        raise TutorialError(f"runner: seed fixture {fixture!r} is not a directory")
    destination = cwd / target
    shutil.copytree(source, destination, dirs_exist_ok=True)
    proc = subprocess.run(
        ["pyrite", "index", "sync"],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if proc.returncode != 0:
        raise TutorialError(f"`pyrite index sync` after seeding {target} failed:\n{proc.stderr}")


# The clone of Pyrite itself (not of any other repository) is the install step.
CLONE_PYRITE_RE = re.compile(r"git clone\s+\S*/pyrite(?:\.git)?(?=\s|$)")


def should_skip(block: str) -> str | None:
    if CLONE_PYRITE_RE.search(block):
        return "git clone of pyrite"
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


def assert_no_and_lists(block: str) -> None:
    """Refuse `a && b`: errexit does not stop the shell when `a` fails (a
    failed `cd X && git init` exits 0), so the block would pass having done
    nothing. One command per line is asserted; a chain is not."""
    for line in block.splitlines():
        if "&&" in _QUOTED.sub('""', line.split("#", 1)[0]):
            raise TutorialError(
                "a tutorial block chains commands with `&&`, which `set -e` does not "
                f"enforce (a failing first command still exits 0); one command per line:\n{line}"
            )


def assert_search_returned_results(block: str, stdout: str, stderr: str = "") -> None:
    """A documented search must match the documented data.

    `pyrite search` exits 0 on an empty result set, so an exit-code check
    alone would let the doc drift until none of its example queries matched
    any of its example entries -- which is exactly the failure the audit
    found elsewhere in these docs.

    A block holds one search, so a zero-result search cannot hide behind a
    sibling that found something. A semantic or hybrid search with no results
    passes only when its output carries the NO_EMBEDDINGS_WARNING.
    """
    searches = SEARCH_RE.findall(block)
    if not searches:
        return
    if len(searches) > 1:
        raise TutorialError(
            f"a block holds {len(searches)} `pyrite search` commands; put each in its own "
            f"block so each is asserted on its own:\n{block}"
        )
    text = strip_marker(stdout)
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
    if len(results) >= 1:
        return
    if MODE_RE.search(block) and NO_EMBEDDINGS_WARNING in text + "\n" + stderr:
        return
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
    # `index health` exits 1 when ANY registered KB is unhealthy -- the demo
    # KBs declare fewer types than they use -- so a nonzero exit with a report
    # is not by itself this tutorial's failure; the report is read per KB below.
    # No report at all is.
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise TutorialError(
            f"`pyrite index health` exited {proc.returncode} without a JSON report\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        ) from None

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

    items = extract_items(doc)
    blocks = [text for kind, text in items if kind == "block"]
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
    # is never called; it records each call, and check_claude_calls reads back
    # what the doc asked it to add.
    stub_bin = home / ".tutorial-bin"
    stub_bin.mkdir()
    stub = stub_bin / "claude"
    stub.write_text(STUB_CLAUDE.format(python=sys.executable, log=CLAUDE_CALLS))
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
    health_kb = "my-research"
    last: tuple[str, str] | None = None  # (block, stdout) of the block that ran last
    last_output = ""  # stdout + stderr of that block
    exit_wanted = 0  # set by `expect-exit` for the next block
    try:
        for number, (kind, text) in enumerate(items, start=1):
            if kind == "directive":
                words = text.split()
                if words[:1] == ["expect-ids"] and len(words) > 1:
                    if last is None:
                        raise TutorialError(f"`{text}` follows no command that ran")
                    print(f"  [{number}] expect-ids {' '.join(words[1:])}")
                    assert_expected_ids(last[0], last[1], words[1:])
                elif words[:1] == ["expect-exit"] and len(words) == 2 and words[1].isdigit():
                    exit_wanted = int(words[1])
                elif words[:1] == ["expect-text"] and len(words) > 1:
                    if last is None:
                        raise TutorialError(f"`{text}` follows no command that ran")
                    print(f"  [{number}] expect-text {' '.join(words[1:])}")
                    for word in words[1:]:
                        if word not in last_output:
                            raise TutorialError(
                                f"the tutorial says this output contains {word!r}:\n"
                                f"{last[0]}\n--- output ---\n{last_output[:2000]}"
                            )
                elif words[:1] == ["seed"] and len(words) == 3:
                    print(f"  [{number}] seed {words[1]} -> {words[2]}")
                    seed_fixture(words[1], words[2], cwd, env)
                elif words[:1] == ["health-kb"] and len(words) == 2:
                    health_kb = words[1]
                else:
                    raise TutorialError(f"unknown runner directive: {text!r}")
                continue

            block = text
            marker = should_skip(block)
            if marker:
                print(f"  [{number}] skipped ({marker})")
                if marker == "git clone of pyrite" and not (cwd / "pyrite").exists():
                    stand_in_for_clone(cwd / "pyrite")
                continue

            first = block.strip().splitlines()[0]
            print(f"  [{number}] {first}")
            assert_no_and_lists(block)
            proc = run_block(block, cwd, env)
            if proc.returncode != exit_wanted:
                raise TutorialError(
                    f"block {number} of {doc.name} exited {proc.returncode}, "
                    f"the document says {exit_wanted}:\n"
                    f"{block}\n--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
                )
            exit_wanted = 0
            if proc.returncode == 0:
                assert_search_returned_results(block, proc.stdout, proc.stderr)
            last = (block, proc.stdout)
            last_output = proc.stdout + "\n" + proc.stderr
            cwd = resulting_cwd(proc.stdout, cwd)
            ran += 1

        check_claude_calls(home, any("pyrite mcp-setup" in block for block in blocks))
        check_index_health(health_kb, cwd, env)
    except TutorialError as failure:
        print(f"\nFAIL: {failure}", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(home, ignore_errors=True)

    print(f"\nOK: {ran} block(s) from {doc.name} ran clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
