"""Markdown format importer -- parse markdown files with YAML frontmatter."""

import logging
from typing import Any

from pyrite.exceptions import FrontmatterError
from pyrite.utils.frontmatter import (
    Frontmatter,
    NoFrontmatter,
    describe,
    next_delimiter,
    split_frontmatter,
)

logger = logging.getLogger(__name__)


def import_markdown(data: str | bytes) -> list[dict[str, Any]]:
    """Parse one or more markdown entries with YAML frontmatter.

    Supports:
    - Single entry: one frontmatter block + body
    - Multiple entries: separated by ``---`` on its own line (after the first block)
    """
    if isinstance(data, bytes):
        data = data.decode("utf-8")

    entries = []
    # Split on document separator (triple dash on own line, not frontmatter)
    # Strategy: parse first entry, then check for more
    remaining = data.strip()

    first = True
    while remaining:
        split = split_frontmatter(remaining)
        if first and not isinstance(split, (Frontmatter, NoFrontmatter)):
            # Refused visibly: reading the YAML as the body would change what the text means.
            raise FrontmatterError(f"Invalid entry format: {describe(split)}")
        first = False
        entry = _parse_single_md(remaining)
        if entry:
            entries.append(entry)
        if not isinstance(split, Frontmatter):
            break
        # After the first frontmatter+body, look for the next --- line. It
        # starts another entry only when what follows reads as frontmatter;
        # otherwise it is a horizontal rule in the body (Hugo reads one page).
        next_sep = next_delimiter(remaining[split.close_end :])
        if not next_sep:
            break
        candidate = remaining[split.close_end :][next_sep[1] :]
        if not isinstance(split_frontmatter(candidate), Frontmatter):
            candidate = "---\n" + candidate
        if not isinstance(split_frontmatter(candidate), Frontmatter):
            break
        remaining = candidate

    return entries


def _parse_single_md(text: str) -> dict[str, Any] | None:
    """Parse a single markdown entry with frontmatter."""
    from pyrite.utils.yaml import load_yaml

    split = split_frontmatter(text)
    if not isinstance(split, Frontmatter):
        # No frontmatter -- treat entire text as body
        if text.strip():
            # Try to extract title from first heading
            lines = text.strip().split("\n")
            title = "Untitled"
            body = text.strip()
            if lines[0].startswith("# "):
                title = lines[0][2:].strip()
                body = "\n".join(lines[1:]).strip()
            return {
                "id": "",
                "title": title,
                "body": body,
                "entry_type": "note",
                "tags": [],
            }
        return None

    frontmatter_text = split.text
    body = split.body

    # YAML that does not parse raises FrontmatterError: an entry with its frontmatter
    # dropped is a different entry, so the import refuses instead of carrying on.
    fm = load_yaml(frontmatter_text)

    if not isinstance(fm, dict):
        fm = {}

    return {
        "id": fm.get("id", ""),
        "title": fm.get("title", "Untitled"),
        "body": body,
        "entry_type": fm.get("type", fm.get("entry_type", "note")),
        "tags": fm.get("tags", []),
        **{
            k: v
            for k, v in fm.items()
            if k not in ("id", "title", "type", "entry_type", "tags", "body")
        },
    }
