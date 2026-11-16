"""Public text names no issue of the private fixes repository.

Security fixes are made in a private repository and then land on the public
one. Code, tests, docs, changelog fragments and scripts describe the property
that holds, never a private issue: a number there means nothing to a public
reader and points at work that may not be disclosed. This scans the tracked
files for the two spellings such a reference takes.

`kb/` is not scanned: its ADRs and roadmap are the maintainer's record, and
any reference there is the maintainer's to amend.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

#: Two forms: the word "private", a space and a hash followed by digits; and
#: the private repository's name, a hash and digits. Spelled in pieces, and
#: described here with no digit after a hash, so this file does not match
#: itself.
PATTERN = re.compile("private" + r" #\d+|pyrite-" + r"security#\d+", re.IGNORECASE)

SCANNED = ("pyrite", "tests", "extensions", "scripts", "docs", "changelog.d", ".claude")
SUFFIXES = {".py", ".md", ".txt", ".yaml", ".yml", ".toml", ".json", ".sh", ".ts", ".svelte"}


def _tracked() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "--", *SCANNED, "CONTRIBUTING.md", "README.md", "AGENTS.md"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    ).stdout
    return [REPO / line for line in out.splitlines() if Path(line).suffix in SUFFIXES]


def test_no_tracked_public_file_names_a_private_issue():
    found = []
    for path in _tracked():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if PATTERN.search(line):
                found.append(f"{path.relative_to(REPO)}:{n}: {line.strip()[:100]}")
    assert not found, "describe the property instead:\n" + "\n".join(found)
