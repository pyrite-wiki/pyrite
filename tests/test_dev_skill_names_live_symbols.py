"""Guard the skills against naming symbols a refactor removed (#1033).

The 0.26 write-path sprint (#1028, #955) deleted the model emitter.  The
``pyrite-dev`` skill is the method a coding session follows, so a stale name
there sends a contributor looking for code that no longer exists.

This is an explicit list, not a general scanner: a symbol may be named in a
skill for a deliberate reason, and each removal should add one line here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SKILLS = REPO / ".claude" / "skills"

REMOVED_SYMBOLS = {
    "to_markdown": re.compile(r"(?<![A-Za-z0-9_])to_markdown\b"),
    "SiteCacheService": re.compile(r"\bSiteCacheService\b"),
    "extensions/cascade": re.compile(r"(?<![A-Za-z0-9_])extensions/cascade(?=/|\b)"),
    "get_kb_by_shortname": re.compile(r"\bget_kb_by_shortname\b"),
    "kb_name_for": re.compile(r"\bkb_name_for\b"),
}


def stale_symbols(line: str) -> list[str]:
    """Return removed names, without matching live names that contain them."""
    return [symbol for symbol, pattern in REMOVED_SYMBOLS.items() if pattern.search(line)]


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("Entry.to_markdown", ["to_markdown"]),
        ("entry.to_markdown()", ["to_markdown"]),
        ("to_markdown(", ["to_markdown"]),
        ("_entry_to_markdown", []),
        ("_entry_list_to_markdown", []),
        ("_search_results_to_markdown", []),
        ("_tags_to_markdown", []),
        ("_timeline_to_markdown", []),
        ("_kbs_to_markdown", []),
        ("SiteCacheService", ["SiteCacheService"]),
        ("get_kb_by_shortname", ["get_kb_by_shortname"]),
        ("kb_name_for(", ["kb_name_for"]),
        ("extensions/cascade/x", ["extensions/cascade"]),
        ("SiteCacheServiceV2 get_kb_by_shortname_v2 kb_name_format", []),
        ("extensions/cascaded/x", []),
        ("Use `SiteCacheService` and `entry.to_markdown()`.", ["to_markdown", "SiteCacheService"]),
        ("A skill line with no stale names.", []),
    ],
)
def test_stale_symbols(line: str, expected: list[str]) -> None:
    assert stale_symbols(line) == expected


def test_the_skills_name_no_removed_symbol() -> None:
    assert SKILLS.is_dir(), f"missing skills directory: {SKILLS}"

    offenders: list[str] = []
    for path in sorted(SKILLS.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), start=1):
            for symbol in stale_symbols(line):
                relative = path.relative_to(REPO).as_posix()
                offenders.append(f"{relative}:{line_number}: {symbol}: {line.strip()}")

    assert not offenders, (
        "The skills name symbols removed by the 0.26 write-path refactor; "
        "update the skill or the explicit list:\n" + "\n".join(offenders)
    )
