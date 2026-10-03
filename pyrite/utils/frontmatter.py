"""Where a file's frontmatter starts and ends: the one rule (design.md, principles 1 and 8).

The reference is the convention other tools follow -- Hugo's frontmatter
reader and YAML itself -- not Pyrite's old loader. Every place Pyrite departs
from it is listed under "Departures" below, pinned by a row in
``tests/test_frontmatter_splitter.py`` (``ORACLE``, with Hugo's recorded
answer beside it), and refuses visibly: a typed result that readers turn into
a ``FrontmatterError``, never a quiet change to what the file means. Nothing
else under ``pyrite/`` or ``extensions/`` may carry its own pattern for it
(the same test fails if one does).

The rule:

* A leading UTF-8 BOM and leading blank lines are ignored (Hugo does the same).
* The frontmatter opens with a ``---`` line: ``---``, then spaces or tabs, then
  the line end (``\\n`` or ``\\r\\n``). ``--- # comment`` also opens it.
* It closes at the first later line that starts with ``---`` in column 0, and
  that line must be a delimiter of the same shape (blanks, or blanks and a
  ``# comment``, then the line end or the end of the file). An indented
  ``---`` (inside a block scalar) or a ``---`` inside a quoted value is not a
  delimiter. ``...`` does not close it.
* The body is everything after the closing line, stripped.

Departures from Hugo (each refused or stated, never silent):

* ``----``, ``---x``, ``---`` + a non-blank such as NBSP, in column 0 before the
  closing line: Hugo closes on the ``---`` prefix and reads the rest as body.
  Here that is ``Malformed``: the file is refused, with its line number.
* ``+++`` (TOML) and a leading ``{`` (JSON) frontmatter: Hugo reads them.
  Here that is ``Unsupported``: refused with a message naming the format.
* The ``# comment`` on a delimiter line belongs to the delimiter; Hugo leaves it
  at the start of the body.
* ``---`` + a tab on the opening line: Hugo refuses; YAML and this rule accept.

The result carries the span so a writer can edit in place without splitting
again. Offsets index the text exactly as given (BOM and CRLF included)::

    text[open_start:yaml_start]   the opening ``---`` line, with its line end
    text[yaml_start:close_start]  the YAML (== ``.text``)
    text[close_start:close_end]   the closing line: ``---``, any tail, line end
    text[close_end:]              the body, unstripped (``.body`` is this, stripped)

``text[:open_start]`` is the BOM and blank lines, if any. An empty frontmatter
has ``yaml_start == close_start``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..exceptions import FrontmatterError

_BOM = "﻿"
_LEADING = re.compile(r"(?:[ \t\r]*\n)*")
# What may follow `---` on a delimiter line: blanks, or blanks and a comment.
_TAIL = r"(?:[ \t]*|[ \t]+#[^\r\n]*)\r?"
_OPENING = re.compile(r"---" + _TAIL + r"\n")
_DELIMITER = re.compile(r"---" + _TAIL + r"(?:\n|\Z)")
_FENCE_START = re.compile(r"^---", re.MULTILINE)
_TOML_OPENING = re.compile(r"\+\+\+[ \t]*\r?\n")


@dataclass(frozen=True)
class Frontmatter:
    """A file with frontmatter: its YAML text, body and the span (see the module doc)."""

    text: str
    body: str
    open_start: int
    yaml_start: int
    close_start: int
    close_end: int


@dataclass(frozen=True)
class NoFrontmatter:
    """The file does not open with a ``---`` line: it has no frontmatter."""


@dataclass(frozen=True)
class Unterminated:
    """The file opens with ``---`` but no later line closes it."""


@dataclass(frozen=True)
class Malformed:
    """A line before the closing delimiter starts with ``---`` but is not a delimiter."""

    line: int  # 1-based, in the text as given


@dataclass(frozen=True)
class Unsupported:
    """Frontmatter in a format Hugo reads and Pyrite does not (``toml`` or ``json``)."""

    format: str


SplitResult = Frontmatter | NoFrontmatter | Unterminated | Malformed | Unsupported


def next_delimiter(text: str, pos: int = 0) -> tuple[int, int] | None:
    """(start, end) of the first delimiter line at or after ``pos`` (a line start).
    Lines that start with ``---`` but are not delimiters are skipped."""
    for m in _FENCE_START.finditer(text, pos):
        d = _DELIMITER.match(text, m.start())
        if d:
            return d.start(), d.end()
    return None


def split_frontmatter(text: str) -> SplitResult:
    """Split ``text`` into frontmatter and body by the rule in the module doc."""
    start = 1 if text.startswith(_BOM) else 0
    start = _LEADING.match(text, start).end()
    opening = _OPENING.match(text, start)
    if not opening:
        if _TOML_OPENING.match(text, start):
            return Unsupported("toml")
        if text.startswith("{", start):
            return Unsupported("json")
        return NoFrontmatter()
    fence = _FENCE_START.search(text, opening.end())
    if fence is None:
        return Unterminated()
    closing = _DELIMITER.match(text, fence.start())
    if closing is None:
        return Malformed(line=text.count("\n", 0, fence.start()) + 1)
    return Frontmatter(
        text=text[opening.end() : closing.start()],
        body=text[closing.end() :].strip(),
        open_start=start,
        yaml_start=opening.end(),
        close_start=closing.start(),
        close_end=closing.end(),
    )


def describe(result: NoFrontmatter | Unterminated | Malformed | Unsupported) -> str:
    """Why a file is not read as having frontmatter, for an error message."""
    if isinstance(result, Unterminated):
        return "missing YAML frontmatter: the opening --- has no closing --- line"
    if isinstance(result, Malformed):
        return (
            f"line {result.line} starts with --- but is not a delimiter "
            "(a closing line is --- alone, or --- then a # comment)"
        )
    if isinstance(result, Unsupported):
        marker = "+++" if result.format == "toml" else "{"
        return (
            f"{result.format.upper()} frontmatter ({marker}) is not supported: "
            "Pyrite reads YAML between --- lines"
        )
    return "missing YAML frontmatter"


def require_frontmatter(text: str) -> Frontmatter:
    """The split of ``text``; raises ``FrontmatterError`` for every other result."""
    split = split_frontmatter(text)
    if not isinstance(split, Frontmatter):
        raise FrontmatterError(f"Invalid entry format: {describe(split)}")
    return split


def load_frontmatter(text: str) -> tuple[dict[str, Any], str] | None:
    """(metadata, body) of an entry file's text; None when it has no frontmatter
    or none that closes. A file that is refused (``Malformed``, ``Unsupported``),
    YAML that does not parse and YAML that is not a mapping raise
    ``FrontmatterError``."""
    from .yaml import load_yaml

    split = split_frontmatter(text)
    if isinstance(split, (Malformed, Unsupported)):
        raise FrontmatterError(f"Invalid entry format: {describe(split)}")
    if not isinstance(split, Frontmatter):
        return None
    return load_yaml(split.text), split.body
