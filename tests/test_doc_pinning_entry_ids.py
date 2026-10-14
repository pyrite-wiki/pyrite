"""docs/pinning-entry-ids.md, run as a test (#700).

The upgrade steps an operator follows are the acceptance test: every
```bash block in the doc runs, in order, in the KB's folder, through the
installed ``pyrite`` entry point, against a hand-authored KB shaped like the
doc's example (a KB named ``notes`` with one collision). Each block's exit
code is the one the doc states in the ``<!-- expect-exit: N -->`` comment
above it. A doc edit that breaks a command, an exit code or the example
fails here.

After the last block, the property the doc promises: each id-less file
gained exactly one line (``git show --numstat`` of the commit the doc makes),
and nothing else changed.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
DOC = REPO / "docs" / "pinning-entry-ids.md"
BLOCK = re.compile(r"(?:<!-- expect-exit: (\d+) -->\n)?```bash\n(.*?)^```", re.S | re.M)

# The KB the doc's example describes: hand-written, not Pyrite's output.
FILES: dict[str, bytes] = {
    "meeting-notes.md": b"---\ntitle: Meeting Notes\ntype: note\n---\nThis one is linked.\n",
    "archive/meeting-notes.md": (
        b"---\n# imported from the old wiki\ntitle: Meeting Notes\ntags: [old, wiki]\n---\n"
        b"An older copy, shadowed today.\n"
    ),
    "people/ada.md": b"---\r\ntitle: Ada Lovelace\r\ntype: person\r\n---\r\nCRLF file.\r\n",
    "stated.md": b"---\nid: stated\ntitle: Already Has One\n---\nUntouched.\n",
    "README.md": b"# Not an entry\n",
}
PINNED = {"meeting-notes.md", "archive/meeting-notes.md", "people/ada.md"}


def _blocks() -> list[tuple[int, str]]:
    text = DOC.read_text(encoding="utf-8")
    return [(int(code or 0), body) for code, body in BLOCK.findall(text)]


def test_the_doc_has_the_upgrade_steps():
    commands = "\n".join(body for _, body in _blocks())
    for step in (
        "pyrite ids missing -k notes",
        "pyrite ids pin -k notes --dry-run",
        "pyrite ids pin -k notes --format text\n",
        "--rename archive/meeting-notes.md=",
        "git diff --stat",
        "pyrite index build",
    ):
        assert step in commands + "\n", step


def test_following_the_doc_pins_every_file_with_one_added_line(tmp_path):
    kb = tmp_path / "notes"
    for rel, content in FILES.items():
        p = kb / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "config.yaml").write_text(
        "knowledge_bases:\n"
        f"- name: notes\n  path: {kb}\n  kb_type: generic\n"
        f"settings:\n  index_path: {tmp_path / 'index.db'}\n  auto_embed: false\n"
    )
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "PYRITE_CONFIG_DIR": str(cfg),
        "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ.get('PATH', '')}",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
    }
    env.pop("PYRITE_DATA_DIR", None)

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=kb, env=env, check=True, capture_output=True
        ).stdout.decode()

    git("init", "-q")
    git("config", "core.autocrlf", "false")
    git("add", "-A")
    git("commit", "-qm", "before")

    blocks = _blocks()
    assert len(blocks) >= 5
    for expected, body in blocks:
        run = subprocess.run(
            ["bash", "-e", "-c", body], cwd=kb, env=env, capture_output=True, text=True
        )
        assert run.returncode == expected, (body, run.stdout, run.stderr)

    # One commit after "before"; each id-less file gained one line, nothing else.
    numstat = git("show", "--numstat", "--format=", "HEAD")
    rows = {line.split("\t")[2]: line.split("\t")[:2] for line in numstat.split("\n") if line}
    assert rows == {rel: ["1", "0"] for rel in PINNED}
    for rel in PINNED:
        after = (kb / rel).read_bytes()
        before = FILES[rel]
        added = [
            line
            for line in git("show", "-U0", "--format=", "HEAD", "--", rel).split("\n")
            if line.startswith("+") and not line.startswith("+++")
        ]
        assert len(added) == 1 and added[0].startswith("+id: "), added
        # a CRLF file's added line keeps its "\r" (split on "\n" only)
        line = added[0][1:].encode() + b"\n"
        assert after.replace(line, b"", 1) == before, rel
    assert (kb / "meeting-notes.md").read_text().count("id: meeting-notes\n") == 1
    assert "id: meeting-notes-2019" in (kb / "archive/meeting-notes.md").read_text()
