"""Markdown format importer -- parse markdown files with YAML frontmatter."""

import logging
import re
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


# The first line after a separator that marks it as the start of another entry.
_ENTRY_START = re.compile(r"\s*(?:title|type|id)\s*:")


def import_markdown(data: str | bytes, *, stream: bool = False) -> list[dict[str, Any]]:
    """Parse one or more markdown entries with YAML frontmatter.

    Supports:
    - Single entry: one frontmatter block + body
    - One entry per file (the default): the frontmatter and the whole body,
      whatever ``---`` lines the body holds (Hugo reads one page).
    - A stream of entries, only when asked for (``stream=True``; ``pyrite import
      --stream``): after an entry's own frontmatter, a ``---`` line directly
      followed by a line that starts ``title:``, ``type:`` or ``id:`` begins the
      next entry, whose block must then close and parse to a mapping. A block that
      begins that way and cannot be read is refused (``FrontmatterError``) for the
      whole file, never folded into the previous entry's body. Limits of the
      stream rule: an entry must start with one of those three keys, and a ``---``
      inside a code block is not understood (a fenced example of frontmatter in an
      entry's body is read as the start of another entry, or refuses the file), so
      use it only on files you know are streams.

    A file this importer cannot read is refused whole: unlike ``pyrite import``'s
    JSON and YAML records, a markdown stream has no per-record boundary until it
    has been parsed.
    """
    if isinstance(data, bytes):
        data = data.decode("utf-8")

    entries = []
    remaining = data.strip()

    first = True
    while remaining:
        split = split_frontmatter(remaining)
        if first and not isinstance(split, (Frontmatter, NoFrontmatter)):
            # Refused visibly: reading the YAML as the body would change what the text means.
            raise FrontmatterError(f"Invalid entry format: {describe(split)}")
        first = False
        following = (
            _next_entry(remaining, split.close_end)
            if stream and isinstance(split, Frontmatter)
            else None
        )
        entry = _parse_single_md(remaining if following is None else remaining[: following[0]])
        if entry:
            entries.append(entry)
        if following is None:
            break
        remaining = following[1]

    return entries


def _next_entry(text: str, pos: int) -> tuple[int, str] | None:
    """(where the current entry ends, the next entry's text) for the first separator
    at or after ``pos`` that starts another entry; None when none does."""
    from pyrite.utils.yaml import load_yaml

    while (sep := next_delimiter(text, pos)) is not None:
        start, end = sep
        if _ENTRY_START.match(text, end):
            block = "---\n" + text[end:]
            nxt = split_frontmatter(block)
            if not isinstance(nxt, Frontmatter):
                raise FrontmatterError(
                    f"Invalid entry format: the entry after the separator on line "
                    f"{text.count(chr(10), 0, start) + 1}: {describe(nxt)}"
                )
            load_yaml(nxt.text)  # raises FrontmatterError unless it is a mapping
            return start, block
        pos = end
    return None


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
