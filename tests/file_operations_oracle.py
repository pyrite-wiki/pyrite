"""The byte oracle for ``apply()``: where one operation may change bytes.

ADR-0042 as a value-level patch (#732 round 1): the result may differ from the
input only inside the spans of the values the operation names. This module
computes those spans from the input text alone, by its own mechanism, so that
it cannot share a blind spot with the code it checks (Andon #745):

- the module's edits find where a value ends by recursing into its last child;
- the module's post-check walks a node's ``end_mark`` back over blank and
  comment lines;
- this oracle takes from the composer only where a key or value *starts*, and
  where an inline scalar or a flow collection *ends* (those marks are exact).
  Where a block value ends, it finds by **scanning the lines by indentation**.

The rule it implements is the ownership table in the module docstring of
``pyrite.storage.file_operations``, restated from the property:

- a set owns the value: the separator after the ``:``, an inline value and the
  spaces after it, the end of its line (an insertion point, for a value that
  becomes a block); a block scalar's indicators and its content lines; a
  block collection's lines up to its last content line;
- a comment after an inline value, on a key line, or on a block scalar's
  header, is not the value's: a set keeps it;
- an unset owns the pair's or item's own lines, from the start of the key
  (dash) line to the end of its last content line; the first pair on a dash
  line owns the indentation of the next key when only spaces lie between;
- unsetting the only key of a mapping or the only item of a list owns that
  collection's span (it becomes ``{}`` or ``[]``);
- blank and comment-only lines after a value's last content line belong to
  no value.

``outside_identical`` compares bytes, before against after, by a regex
fullmatch of the pieces between the spans: a different mechanism again from
the module's left-to-right search.
"""

from __future__ import annotations

import re
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.nodes import MappingNode, ScalarNode, SequenceNode

from pyrite.storage.file_operations import (
    AddSubkey,
    Append,
    FrontmatterSpan,
    Remove,
    ReplaceBody,
    Set,
    Unset,
)

_HEADER = re.compile(r"[|>][0-9+-]*")
_KEEP_HEADER = re.compile(r"[|>](?:[0-9]?\+[0-9]?)\s*(?:#.*)?$")


class _Lines:
    """The frontmatter's lines, with absolute offsets."""

    def __init__(self, text: str, start: int, end: int):
        self.text = text
        self.starts: list[int] = []
        self.ends: list[int] = []  # past the line ending
        pos = start
        while pos < end:
            nl = text.find("\n", pos)
            stop = end if nl < 0 or nl >= end else nl + 1
            self.starts.append(pos)
            self.ends.append(stop)
            pos = stop

    def index(self, pos: int) -> int:
        for i in range(len(self.starts) - 1, -1, -1):
            if self.starts[i] <= pos:
                return i
        return 0

    def line(self, i: int) -> str:
        return self.text[self.starts[i] : self.ends[i]]

    def content_end(self, i: int) -> int:
        line = self.line(i)
        return self.starts[i] + len(line.rstrip("\r\n"))

    def indent(self, i: int) -> int:
        line = self.line(i)
        return len(line) - len(line.lstrip(" "))

    def blank(self, i: int) -> bool:
        return not self.line(i).strip()

    def comment(self, i: int) -> bool:
        return self.line(i).strip().startswith("#")


def _last_line(lines: _Lines, owner: int, col: int, dash_at_col: bool, scalar: bool) -> int:
    """The index of the last content line of a block value whose owner (key or
    dash) line is ``owner`` and whose parent sits at column ``col``.

    A line belongs while it is blank, a comment, indented past ``col``, or
    (a list under a key) a dash at ``col``. Of those, the last *content* line
    is the answer: comment lines never are, except inside a block scalar;
    blank lines are content only inside a keep (``|+``) scalar."""
    last = owner
    keep_indent = None  # inside a keep scalar whose header line has this indent
    header = lines.line(owner).split(" #")[0].rstrip()
    if scalar and _KEEP_HEADER.search(header):
        keep_indent = lines.indent(owner) if not dash_at_col else col
    i = owner + 1
    while i < len(lines.starts):
        if lines.blank(i):
            if keep_indent is not None:
                last = i
            i += 1
            continue
        indent = lines.indent(i)
        stripped = lines.line(i).strip()
        is_dash = indent == col and dash_at_col and (stripped == "-" or stripped.startswith("- "))
        if indent <= col and not is_dash:
            if lines.comment(i) and not scalar:
                i += 1
                continue
            break
        if keep_indent is not None and indent <= keep_indent:
            keep_indent = None
        if scalar or not lines.comment(i):
            last = i
            body = stripped.split(" #")[0].rstrip()
            if not scalar and _KEEP_HEADER.search(body):
                keep_indent = indent
        i += 1
    return last


def _walk(root: Any, segs: list) -> tuple[list, int]:
    """(container, index, node) per segment found; a key matches by its text."""
    trail = []
    node = root
    for n, seg in enumerate(segs):
        if isinstance(node, MappingNode) and isinstance(seg, str):
            hits = [
                i
                for i, (k, _) in enumerate(node.value)
                if isinstance(k, ScalarNode) and k.value == seg
            ]
            if not hits:
                return trail, n
            idx = hits[0]
            child = node.value[idx][1]
        elif isinstance(node, SequenceNode) and isinstance(seg, int):
            idx = seg + len(node.value) if seg < 0 else seg
            if not 0 <= idx < len(node.value):
                return trail, n
            child = node.value[idx]
        else:
            return trail, n
        trail.append((node, idx, child))
        node = child
    return trail, len(segs)


def _segs(op: Any) -> list:
    path = op.path
    if isinstance(path, tuple | list):
        return list(path)
    out: list = []
    for part in path.split(".") if path else []:
        name, _, rest = part.partition("[")
        if name:
            out.append(name)
        while rest:
            index, _, rest = rest.partition("]")
            out.append(int(index))
            rest = rest[1:] if rest.startswith("[") else rest
    return out


class Oracle:
    def __init__(self, text: str, fm: FrontmatterSpan):
        self.text = text
        self.fm = fm
        self.off = fm.start
        self.root = YAML().compose(text[fm.start : fm.end])
        self.lines = _Lines(text, fm.start, fm.end)

    def s(self, node) -> int:
        return node.start_mark.index + self.off

    def e(self, node) -> int:
        return node.end_mark.index + self.off

    def colon(self, key) -> int:
        return self.text.index(":", self.e(key)) + 1

    # -- what a value owns ----------------------------------------------

    def value_spans(self, container, idx, node) -> list[tuple[int, int]]:
        lines = self.lines
        if container is None:
            return [(self.fm.end, self.fm.end)]
        if container.flow_style:
            return [(self.s(node), self.e(node))]
        in_map = isinstance(container, MappingNode)
        if in_map:
            key = container.value[idx][0]
            start = self.colon(key)
            owner = lines.index(self.s(key))
            col = self.s(key) - lines.starts[owner]  # the key's column, on a dash line too
        else:
            start = self.s(node)
            owner = self.dash_line(node)
            col = self.text.index("-", lines.starts[owner]) - lines.starts[owner]
        if isinstance(node, ScalarNode) and self.s(node) == self.e(node):
            end = lines.content_end(owner)
            return [(start, start), (end, end)]
        if isinstance(node, MappingNode | SequenceNode) and not node.flow_style:
            last = _last_line(lines, owner, col, in_map and isinstance(node, SequenceNode), False)
            if in_map:
                return [(start, start), (lines.content_end(owner), lines.ends[last])]
            return [(start, lines.ends[last])]
        if isinstance(node, ScalarNode) and node.style in ("|", ">"):
            header = _HEADER.match(self.text, self.s(node)).end()
            head_line = lines.index(self.s(node))
            last = _last_line(lines, head_line, col, False, True)
            return [(start, header), (lines.content_end(head_line), lines.content_end(last))]
        end = self.e(node)
        stop = end
        while stop < len(self.text) and self.text[stop] in " \t":
            stop += 1
        line_end = lines.content_end(lines.index(end - 1 if end > 0 else end))
        return [(start, stop), (line_end, line_end)]

    def dash_line(self, node) -> int:
        """The line of the ``-`` that starts a list item: upward from the
        item's first line."""
        i = self.lines.index(self.s(node))
        while not self.lines.line(i).lstrip().startswith("-"):
            i -= 1
        return i

    def last_line_of(self, container, idx, node) -> int:
        """The last content line of a pair's or item's value."""
        lines = self.lines
        if isinstance(container, MappingNode):
            key = container.value[idx][0]
            owner = lines.index(self.s(key))
            col = self.s(key) - lines.starts[owner]
        else:
            owner = self.dash_line(node)
            col = self.text.index("-", lines.starts[owner]) - lines.starts[owner]
        if isinstance(node, ScalarNode) and self.s(node) == self.e(node):
            return owner
        if isinstance(node, MappingNode | SequenceNode) and not node.flow_style:
            dash = isinstance(container, MappingNode) and isinstance(node, SequenceNode)
            return _last_line(lines, owner, col, dash, False)
        if isinstance(node, ScalarNode) and node.style in ("|", ">"):
            return _last_line(lines, lines.index(self.s(node)), col, False, True)
        return lines.index(self.e(node) - 1)

    # -- an operation's spans --------------------------------------------

    def spans(self, op: Any) -> list[tuple[int, int]]:
        if isinstance(op, ReplaceBody):
            return [(self.fm.body, len(self.text))]
        segs = _segs(op)
        trail, found = _walk(self.root, segs)
        if isinstance(op, Unset):
            return self.unset_spans(trail) if found == len(segs) else []
        if isinstance(op, Remove) and found == len(segs):
            return self.remove_spans(trail, op)
        if isinstance(op, Append) and found == len(segs):
            return self.append_spans(trail)
        return self.slot(trail, found)

    def slot(self, trail, depth) -> list[tuple[int, int]]:
        if depth == 0 or self.root is None:
            return [(self.fm.end, self.fm.end)]
        return self.value_spans(*trail[depth - 1])

    def append_spans(self, trail) -> list[tuple[int, int]]:
        container, idx, node = trail[-1]
        if not isinstance(node, SequenceNode):
            return self.value_spans(container, idx, node)
        if node.flow_style:
            if not node.value:
                return [(self.s(node) + 1, self.s(node) + 1)]
            end = self.e(node.value[-1])
            return [(end, end)]
        last = len(node.value) - 1
        stop = self.lines.ends[self.last_line_of(node, last, node.value[last])]
        return [(stop, stop)]

    def remove_spans(self, trail, op) -> list[tuple[int, int]]:
        from tests.test_file_operations import _eq, _plain

        container, idx, node = trail[-1]
        if not isinstance(node, SequenceNode):
            return []
        values = _plain(YAML().load(self.text[self.fm.start : self.fm.end]))
        for seg in _segs(op):
            values = values[seg]
        for i, item in enumerate(values):
            if _eq(item, op.value):
                return self.unset_spans([*trail, (node, i, node.value[i])])
        return []

    def unset_spans(self, trail) -> list[tuple[int, int]]:
        lines = self.lines
        container, idx, node = trail[-1]
        if container.flow_style:
            items = container.value
            mapping = isinstance(container, MappingNode)

            def first(i):
                return self.s(items[i][0] if mapping else items[i])

            def last(i):
                return self.e(items[i][1] if mapping else items[i])

            if idx + 1 < len(items):
                return [(first(idx), first(idx + 1))]
            if idx > 0:
                return [(last(idx - 1), last(idx))]
            return [(first(idx), last(idx))]
        if len(container.value) == 1 and container is not self.root:
            return self.slot(trail, len(trail) - 1)
        stop = lines.ends[self.last_line_of(container, idx, node)]
        if isinstance(container, SequenceNode):
            return [(lines.starts[self.dash_line(node)], stop)]
        key = container.value[idx][0]
        line = lines.index(self.s(key))
        if not self.text[lines.starts[line] : self.s(key)].strip():
            return [(lines.starts[line], stop)]
        if idx + 1 < len(container.value):
            nxt = self.s(container.value[idx + 1][0])
            if not self.text[stop:nxt].strip(" \t"):
                stop = nxt
        return [(self.s(key), stop)]


def op_spans(text: str, fm: FrontmatterSpan, op: Any) -> list[tuple[int, int]]:
    """Where ``op`` may change ``text``'s bytes."""
    return Oracle(text, fm).spans(op)


def outside_identical(before: str, after: str, spans: list[tuple[int, int]]) -> bool:
    """Every byte of ``before`` outside ``spans`` is in ``after``, in order,
    with only the spans' contents replaced: a regex fullmatch of the pieces
    between the spans, joined by "anything"."""
    cuts = sorted(spans)
    pieces, at = [], 0
    for start, end in cuts:
        if start < at:
            start = at
        pieces.append(before[at:start])
        at = max(at, end)
    pieces.append(before[at:])
    if len(pieces) == 1:
        return after == before
    pattern = "(?s)" + "(?:.*?)".join(re.escape(p) for p in pieces)
    return re.fullmatch(pattern, after) is not None
