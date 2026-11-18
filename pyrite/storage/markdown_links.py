"""Markdown links from an entry body to entry files in the same KB (#1723).

A wikilink names an entry by id; a Markdown link ``[text](other.md)`` names it
by path. This module finds the paths: the link rows keep the path
(``link.target_path``) and the backend's resolver gives each row the id of the
entry indexed at that path now (``BaseBackend._resolve_path_links``).

What is a path link here, and what is not, is the one rule below
(``markdown_link_targets``). The body it reads has its code regions blanked
already (``index._strip_code_regions``), so a link inside code is not seen.

A regular expression, not a full CommonMark parser, as the wikilink
extraction is. Shapes it does not read (known limits, none found in this
repo's ``kb/``):

* link text that spans a line break, or brackets nested deeper than one level
  (``[a [b [c]]](x.md)``);
* a destination with spaces or unbalanced parentheses outside ``<...>``, which
  CommonMark does not take as a link either;
* a reference definition inside a block quote or list item (``> [r]: x.md``);
* an HTML ``<a href="x.md">``;
* a link inside an *indented* code block: only fenced blocks and inline spans
  are blanked, the same limit wikilinks have.

A bare destination (``[t](other)``) is a link only when ``other.md`` is a file
when the source is read; a target created later appears when the source is
next read (groom question 2).

A broken link's ``target_id`` is its path text (``notes/a.md``, decision D1).
An entry whose id is literally that text would make the broken row read as
resolved to every reader. Ids made from titles cannot contain the dot, so only
a hand-set id can collide; it is not guarded.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import unquote

from ..utils.kb_files import KBFiles
from . import entry_paths

#: The link text: any run without brackets, or one level of nested brackets
#: (``[![img](x.png)](a.md)``), with backslash escapes.
_TEXT = r"\[((?:\\.|[^\[\]\\\n]|\[[^\[\]\n]*\])*)\]"

#: An inline destination: ``<...>`` or a run with balanced parentheses.
_DEST = r"(<[^<>\n]*>|(?:[^\s()<>\\]|\\.|\((?:[^\s()\\]|\\.)*\))+)"

#: An optional title: ``"..."``, ``'...'`` or ``(...)``.
_TITLE = r"""(?:[ \t]+(?:"[^"\n]*"|'[^'\n]*'|\([^()\n]*\)))?"""

#: ``[text](dest "title")``, not an image ``![..](..)`` and not the inside of
#: a wikilink ``[[x]]``.
_INLINE_RE = re.compile(r"(?<![!\[\\])" + _TEXT + r"\([ \t]*" + _DEST + _TITLE + r"[ \t]*\)")

#: A reference definition ``[label]: dest "title"`` on its own line.
_DEFINITION_RE = re.compile(
    r"^ {0,3}\[([^\[\]\n]+)\]:[ \t]*(<[^<>\n]*>|\S+)"
    r"""(?:[ \t]+(?:"[^"\n]*"|'[^'\n]*'|\([^()\n]*\)))?[ \t]*$""",
    re.MULTILINE,
)

#: A reference use: ``[text][label]``, ``[text][]`` or ``[label]``; never an
#: inline link (``(`` next) nor a definition (``:`` next).
_REFERENCE_RE = re.compile(r"(?<![!\[\\\]])\[([^\[\]\n]+)\](?:\[([^\[\]\n]*)\])?(?![\[(:])")

_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")
_ESCAPE_RE = re.compile(r"\\([!-/:-@\[-`{-~])")


def _label(text: str) -> str:
    return " ".join(text.split()).casefold()


def _destinations(body: str) -> list[str]:
    """Every link destination the body writes, in order: inline links, then
    reference links whose label is defined."""
    found = [m.group(2) for m in _INLINE_RE.finditer(body)]
    definitions: dict[str, str] = {}
    for m in _DEFINITION_RE.finditer(body):
        # The first definition of a label wins (CommonMark).
        definitions.setdefault(_label(m.group(1)), m.group(2))
    if definitions:
        rest = _DEFINITION_RE.sub("", body)
        for m in _REFERENCE_RE.finditer(rest):
            label = m.group(2) if m.group(2) else m.group(1)
            dest = definitions.get(_label(label))
            if dest is not None:
                found.append(dest)
    return found


def markdown_link_targets(body: str, root: Path, source: Path) -> list[tuple[str, str]]:
    """The in-KB entry-file paths ``body`` links to, as ``(path, note)``.

    ``path`` is KB-relative POSIX text, after symbolic links are followed
    (``KBFiles.resolved``, the #1262 alias rule); ``note`` is the fragment as
    ``#heading`` (as a wikilink's), or "". ``source`` is the file the body is
    read from, under ``root``.

    A path link is a relative destination that resolves inside the KB to a
    path the walk lists (``entry_paths.is_listed``) and ends in ``.md``; with
    nothing there it is still a link (a broken one, decision D1). A bare
    destination (no suffix) is a link only when ``<path>.md`` is a file now.
    Not a path link: a URL or any ``scheme:``, a fragment-only ``#x``, a
    root-relative ``/x.md`` (the site's root, not the KB's), a path that
    leaves the KB, a file the walk does not list (README, ``_templates/``, a
    hidden folder), another suffix, and the source itself.
    """
    if not body or "](" not in body and "]:" not in body:
        return []
    files = KBFiles(root)
    source_dir = Path(source).parent
    own = files.resolved(source)
    out: list[tuple[str, str]] = []
    for raw in _destinations(body):
        dest = raw[1:-1] if raw.startswith("<") and raw.endswith(">") else raw
        dest = _ESCAPE_RE.sub(r"\1", dest.strip())
        if not dest or dest.startswith(("#", "/", "\\")) or _SCHEME_RE.match(dest):
            continue
        path_part, _, fragment = dest.partition("#")
        path_part = path_part.partition("?")[0]
        path_text = unquote(path_part)
        if not path_text or path_text.endswith(("/", os.sep)):
            continue
        suffix = Path(path_text).suffix
        if suffix == "":
            candidate = files.resolved(source_dir / (path_text + ".md"))
            if candidate is None or not candidate.is_file():
                continue
        elif suffix.lower() == ".md":
            candidate = files.resolved(source_dir / path_text)
            if candidate is None:
                continue
        else:
            continue
        if not entry_paths.is_listed(root, candidate):
            continue
        if own is not None and candidate == own:
            continue
        rel = candidate.relative_to(files.root).as_posix()
        note = f"#{unquote(fragment)}" if fragment else ""
        out.append((rel, note))
    return out
