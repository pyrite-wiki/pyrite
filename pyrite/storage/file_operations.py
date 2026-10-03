"""Operations on an entry file's text: ADR-0042 decisions 1, 2, 3 and 6.

``apply(text, ops, span)`` applies field operations (set, append, remove,
unset, replace the body, add a sub-key) to the file's own text and returns the
new text and a report. It is a pure function. It touches no file and is not
wired to any write path yet (B6 phase P3 does that).

**How an edit is made.** Each operation is a splice at the span of the value
it names. Spans come from ruamel.yaml's composer, the YAML 1.2 parser that
reads use (amendment A4). Existing bytes are never re-emitted through a
serializer. Only the bytes of the new value are written, by the small
emitter below. A new key goes last, a new list item copies its neighbour's
indent and quoting, a replaced scalar keeps its quote style and its trailing
comment, and new lines use the file's line ending.

**The post-check (decision 2) runs on every call.** After each operation the
result is parsed by the reader's own split (``_frontmatter_of``). It must
parse to exactly the operation applied, every top-level key the operation does
not name must keep its bytes, and the BOM, the delimiters and the body must
be unchanged. A narrow edit that fails this is widened item-wise: the touched
top-level key is rebuilt, copying each unchanged item's bytes from the file.
If that fails too, the call raises ``OperationRefusedError``.

**Where the frontmatter is** comes from the caller, as a ``FrontmatterSpan``.
This module has no frontmatter splitter of its own. The backlog item
"one-frontmatter-splitter" will supply the one function that computes it.
Until then the post-check calls ``_frontmatter_of`` read-only, so the result
is verified through the split every read uses.

Trap, measured on ruamel 0.19.1 (#732): a node's ``end_mark`` is not where its
bytes end. A block sequence or block mapping ends after the comment line that
follows it. A block scalar ends after the blank lines that follow it. An
alias composes to the *same node object* as its anchor, so its span is the
anchor's bytes. ``_end`` computes the real end, and the anchor refusal covers
aliases.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError
from ruamel.yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from ..utils.yaml import _is_yaml11_ambiguous

# --- the public surface -----------------------------------------------------


class OperationRefusedError(Exception):
    """The operation cannot be applied as asked, and nothing was changed.

    ``reason`` says why, in words a caller can show to a person.
    """

    def __init__(self, reason: str, op: Any = None):
        super().__init__(reason)
        self.reason = reason
        self.op = op


@dataclass(frozen=True)
class FrontmatterSpan:
    """Offsets into a file's text: ``start`` is the first character of the
    YAML (after the opening ``---`` line), ``end`` the first character of the
    closing ``---`` line, and ``body`` the first character after that line."""

    start: int
    end: int
    body: int


@dataclass(frozen=True)
class Set:
    """Set a key to a value. ``path`` names a top-level key (``"title"``) or a
    nested one (``"links[0].relation"``, ``"params.social.x"``, or a tuple of
    segments). A missing key is added; missing parents are created."""

    path: Any
    value: Any


@dataclass(frozen=True)
class Append:
    """Append one item to the list at ``path`` (created if absent)."""

    path: Any
    value: Any


@dataclass(frozen=True)
class Remove:
    """Remove the first item equal (decision 3) to ``value`` from a list."""

    path: Any
    value: Any


@dataclass(frozen=True)
class Unset:
    """Remove the key (or list item) at ``path``, with its own lines."""

    path: Any


@dataclass(frozen=True)
class ReplaceBody:
    """Replace the Markdown body. The file's leading blank lines and trailing
    whitespace are kept, and a body equal to the old one after ``strip()``
    (the form a read returns) changes nothing."""

    body: str


@dataclass(frozen=True)
class AddSubkey:
    """Add ``key: value`` to the mapping at ``path`` (``links[0]``): one line
    inserted at the item's indent. Already there with an equal value: no
    change. Already there with another value: refused (use ``Set``)."""

    path: Any
    key: str
    value: Any


@dataclass(frozen=True)
class OpResult:
    op: Any
    outcome: str  # "changed" | "unchanged"


@dataclass(frozen=True)
class Report:
    results: tuple[OpResult, ...] = ()
    # top-level keys whose narrow edit failed the post-check and were rebuilt
    widened: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return any(r.outcome == "changed" for r in self.results)


def apply(text: str, ops: list[Any], span: FrontmatterSpan | None) -> tuple[str, Report]:
    """Apply ``ops`` in order to ``text``; return the new text and a report.

    A call that changes nothing returns ``text`` itself. Raises
    ``OperationRefusedError`` (and changes nothing) when an operation cannot be
    applied as asked.
    """
    ops = list(ops)
    if not ops:
        return text, Report()
    if span is None:
        raise OperationRefusedError(_why_no_frontmatter(text))
    original = _parse(text, span)
    current = original
    results: list[OpResult] = []
    widened: list[str] = []
    for op in ops:
        new_doc, outcome, was_widened = _apply_one(current, op)
        results.append(OpResult(op, outcome))
        if was_widened:
            widened.append(str(_top_key(op)))
        current = new_doc
    if current.text == text:
        return text, Report(tuple(results), tuple(widened))
    # the whole call, against the original: untouched keys, values, order
    expected = original.data
    for op in ops:
        expected = _model_apply(expected, op)
    named = {_top_key(op) for op in ops}
    problem = _verify(original, current.text, current.span, expected, current.body, named)
    if problem:
        raise OperationRefusedError(f"the result failed the post-check: {problem}")
    return current.text, Report(tuple(results), tuple(widened))


# --- decision 3: equality ---------------------------------------------------


def values_equal(a: Any, b: Any) -> bool:
    """ADR-0042 decision 3. A string equals a date or timestamp when it is
    that value's ISO form or the same instant; int and float compare by value;
    a bool never equals an int; mappings compare unordered; lists in order."""
    a, b = _plain(a), _plain(b)
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, int | float) and isinstance(b, int | float):
        return a == b
    if isinstance(a, str) and isinstance(b, date):
        a, b = b, a
    if isinstance(a, date):
        if isinstance(b, str):
            return _temporal_equals_text(a, b)
        if isinstance(b, date):
            if isinstance(a, datetime) != isinstance(b, datetime):
                return False
            if isinstance(a, datetime) and (a.tzinfo is None) != (b.tzinfo is None):
                return False
            return a == b
        return False
    if isinstance(a, str) and isinstance(b, str):
        return a == b
    if isinstance(a, Mapping) and isinstance(b, Mapping):
        if len(a) != len(b):
            return False
        bk = {str(k): v for k, v in b.items()}
        return all(str(k) in bk and values_equal(v, bk[str(k)]) for k, v in a.items())
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(values_equal(x, y) for x, y in zip(a, b, strict=True))
    return False


def _temporal_equals_text(value: date, text: str) -> bool:
    if value.isoformat() == text:
        return True
    if not isinstance(value, datetime):
        return False
    if value.isoformat(" ") == text:
        return True
    try:
        other = datetime.fromisoformat(text)
    except ValueError:
        return False
    if (value.tzinfo is None) != (other.tzinfo is None):
        return False
    return value == other


def _plain(value: Any) -> Any:
    """ruamel's round-trip types as plain Python values."""
    if isinstance(value, Mapping):
        return {(str(k) if isinstance(k, str) else k): _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    if type(value).__name__ == "ScalarBoolean":
        return bool(value)
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, str):
        return str(value)
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    if isinstance(value, datetime) and type(value) is not datetime:
        # ruamel's TimeStamp drops its tzinfo under copy.deepcopy (0.19.1):
        # 09:30-05:00 would come back as a naive 09:30. A plain datetime.
        return datetime(
            value.year,
            value.month,
            value.day,
            value.hour,
            value.minute,
            value.second,
            value.microsecond,
            tzinfo=value.tzinfo,
        )
    return value


def _same(got: Any, expected: Any) -> bool:
    """Equal under decision 3, with the top-level key order kept."""
    if not values_equal(got, expected):
        return False
    return [str(k) for k in got] == [str(k) for k in expected]


# --- the parsed document ----------------------------------------------------


@dataclass
class _Doc:
    text: str
    span: FrontmatterSpan
    root: MappingNode | None
    data: dict
    eol: str

    def s(self, node: Node) -> int:
        return node.start_mark.index + self.span.start

    def e(self, node: Node) -> int:
        return node.end_mark.index + self.span.start

    @property
    def body(self) -> str:
        return self.text[self.span.body :]


def _why_no_frontmatter(text: str) -> str:
    head = text.lstrip("﻿")
    if head.startswith("+++"):
        return "TOML frontmatter (+++) is not edited: Pyrite edits YAML frontmatter only"
    if head.startswith("{"):
        return "JSON frontmatter is not edited: Pyrite edits YAML frontmatter only"
    return "the file has no YAML frontmatter"


def _parse(text: str, span: FrontmatterSpan) -> _Doc:
    yaml_text = text[span.start : span.end]
    try:
        root = YAML().compose(yaml_text)
    except YAMLError as e:
        raise OperationRefusedError(f"the frontmatter does not parse: {e}") from e
    if root is not None and not isinstance(root, MappingNode):
        raise OperationRefusedError("the frontmatter is not a mapping")
    if root is not None and root.flow_style:
        raise OperationRefusedError("the frontmatter is one flow-style mapping ({...}); not edited")
    if root is not None:
        duplicate = _duplicate_key(root, set())
        if duplicate is not None:
            raise OperationRefusedError(
                f"the frontmatter has a duplicate key {duplicate!r}; not edited"
            )
    try:
        loaded = YAML().load(yaml_text)
    except YAMLError as e:
        raise OperationRefusedError(f"the frontmatter does not parse: {e}") from e
    eol = "\r\n" if text[max(0, span.start - 2) : span.start] == "\r\n" else "\n"
    return _Doc(text, span, root, _plain(loaded or {}), eol)


def _duplicate_key(node: Node, seen: set[int]) -> str | None:
    if id(node) in seen:
        return None
    seen.add(id(node))
    if isinstance(node, MappingNode):
        keys: set[str] = set()
        for k, v in node.value:
            name = k.value if isinstance(k, ScalarNode) else None
            if name is not None and name != "<<":
                if name in keys:
                    return name
                keys.add(name)
            found = _duplicate_key(v, seen)
            if found is not None:
                return found
    elif isinstance(node, SequenceNode):
        for item in node.value:
            found = _duplicate_key(item, seen)
            if found is not None:
                return found
    return None


def _refuse_anchors(doc: _Doc, top: str, op: Any) -> None:
    """Decision 6: a key that defines or uses an anchor is refused for change.

    An alias composes to the anchor's own node object, so checking the touched
    subtree for any node with an anchor catches the anchor, every alias to it
    and a merge key (``<<: *b``)."""
    if doc.root is None:
        return
    for k, v in doc.root.value:
        if isinstance(k, ScalarNode) and k.value == top:
            if _has_anchor(k, set()) or _has_anchor(v, set()):
                raise OperationRefusedError(
                    f"{top!r} defines or uses a YAML anchor or alias; edit it by hand", op
                )


def _has_anchor(node: Node, seen: set[int]) -> bool:
    if id(node) in seen:
        return True  # reached twice: an alias
    seen.add(id(node))
    if getattr(node, "anchor", None):
        return True
    if isinstance(node, MappingNode):
        return any(_has_anchor(k, seen) or _has_anchor(v, seen) for k, v in node.value)
    if isinstance(node, SequenceNode):
        return any(_has_anchor(item, seen) for item in node.value)
    return False


# --- text positions ---------------------------------------------------------


def _line_start(text: str, pos: int) -> int:
    return text.rfind("\n", 0, pos) + 1


def _line_end(text: str, pos: int) -> int:
    """Just past the line ending of the line holding ``pos``."""
    nl = text.find("\n", pos)
    return len(text) if nl < 0 else nl + 1


def _content_end_of_line(text: str, pos: int) -> int:
    """The end of the line holding ``pos``, before its line ending."""
    nl = text.find("\n", pos)
    if nl < 0:
        return len(text)
    return nl - 1 if nl > 0 and text[nl - 1] == "\r" else nl


def _col(doc: _Doc, node: Node) -> int:
    at = doc.s(node)
    return at - _line_start(doc.text, at)


def _is_empty(node: Node) -> bool:
    return isinstance(node, ScalarNode) and node.start_mark.index == node.end_mark.index


def _is_null(node: Node) -> bool:
    return isinstance(node, ScalarNode) and node.tag.endswith(":null")


def _is_block(node: Node) -> bool:
    return isinstance(node, MappingNode | SequenceNode) and not node.flow_style


def _colon_end(doc: _Doc, key: Node) -> int:
    return doc.text.index(":", doc.e(key)) + 1


def _end(doc: _Doc, node: Node) -> int:
    """Just past the last character of ``node``'s own text. Not ``end_mark``:
    see the module docstring."""
    if isinstance(node, ScalarNode):
        start, end = doc.s(node), doc.e(node)
        if node.style in ("|", ">"):
            while end > start + 1 and doc.text[end - 1] in " \t\r\n":
                end -= 1
        return end
    if node.flow_style or not node.value:
        return doc.e(node)
    if isinstance(node, MappingNode):
        k, v = node.value[-1]
        return _pair_end(doc, k, v)
    return _end(doc, node.value[-1])


def _pair_end(doc: _Doc, key: Node, value: Node) -> int:
    if _is_empty(value):
        return _colon_end(doc, key)
    if doc.s(value) < doc.e(key):  # an alias: its own text is `*name`
        pos = _colon_end(doc, key)
        while pos < len(doc.text) and doc.text[pos] in " \t":
            pos += 1
        while pos < len(doc.text) and doc.text[pos] not in " \t\r\n,]}":
            pos += 1
        return pos
    return _end(doc, value)


def _own_end(doc: _Doc, end: int) -> int:
    """Past the line ending of the line holding the character before ``end``."""
    return _line_end(doc.text, end - 1)


def _item_range(doc: _Doc, item: Node) -> tuple[int, int]:
    """A block sequence item's own lines, from its dash line."""
    start = _line_start(doc.text, doc.s(item))
    if doc.text[start : doc.s(item)].strip() != "-":
        raise OperationRefusedError("a list item laid out on more than its dash line is not edited")
    return start, _own_end(doc, _end(doc, item))


def _item_cols(doc: _Doc, item: Node) -> tuple[int, int]:
    start = _line_start(doc.text, doc.s(item))
    dash = doc.text.index("-", start)
    return dash - start, doc.s(item) - start


# --- paths ------------------------------------------------------------------


def _segments(path: Any) -> list[str | int]:
    if isinstance(path, tuple | list):
        segs = list(path)
    elif isinstance(path, str):
        segs = []
        for part in path.split("."):
            name, bracket, rest = part.partition("[")
            if name:
                segs.append(name)
            rest = bracket + rest
            while rest:
                if not rest.startswith("[") or "]" not in rest:
                    raise OperationRefusedError(f"cannot read the path {path!r}")
                index, _, rest = rest[1:].partition("]")
                try:
                    segs.append(int(index))
                except ValueError as e:
                    raise OperationRefusedError(f"cannot read the path {path!r}") from e
    else:
        raise OperationRefusedError(f"a path is a string or a tuple, not {type(path).__name__}")
    if not segs and path != "":
        raise OperationRefusedError(f"the path {path!r} is empty")
    return segs


def _top_key(op: Any) -> str | None:
    if isinstance(op, ReplaceBody):
        return None
    segs = _segments(op.path)
    if not segs and isinstance(op, AddSubkey):
        return str(op.key)
    return str(segs[0])


def _key_index(node: MappingNode, seg: Any) -> int | None:
    for i, (k, _) in enumerate(node.value):
        if isinstance(k, ScalarNode) and k.value == str(seg):
            return i
    return None


def _walk(doc: _Doc, segs: list) -> tuple[list[tuple[Node, int, Node]], int]:
    """(container, index, child) for each segment the file has; and how many."""
    trail: list[tuple[Node, int, Node]] = []
    node: Node | None = doc.root
    for i, seg in enumerate(segs):
        if isinstance(node, MappingNode) and isinstance(seg, str):
            idx = _key_index(node, seg)
            if idx is None:
                return trail, i
            child = node.value[idx][1]
        elif isinstance(node, SequenceNode) and isinstance(seg, int):
            j = seg + len(node.value) if seg < 0 else seg
            if not 0 <= j < len(node.value):
                return trail, i
            idx, child = j, node.value[j]
        else:
            return trail, i
        trail.append((node, idx, child))
        node = child
    return trail, len(segs)


def _data_at(data: Any, segs: list) -> Any:
    for seg in segs:
        if isinstance(data, Mapping):
            data = {str(k): v for k, v in data.items()}[str(seg)]
        else:
            data = data[seg]
    return data


def _find_key(data: Mapping, key: Any) -> Any:
    for k in data:
        if str(k) == str(key):
            return k
    return None


# --- the model: what the operation means on the parsed value ------------------


def _model_apply(data: dict, op: Any) -> dict:
    """The operation applied to the parsed value: what the post-check expects."""
    result = copy.deepcopy(data)
    if isinstance(op, ReplaceBody):
        return result
    segs = _segments(op.path)
    if isinstance(op, AddSubkey):
        segs = [*segs, op.key]
    parent: Any = result
    for seg in segs[:-1]:
        if isinstance(parent, Mapping):
            found = _find_key(parent, seg)
            if found is None or parent[found] is None:
                if isinstance(op, Unset | Remove):
                    return result
                parent[seg if found is None else found] = {}
                found = seg if found is None else found
            parent = parent[found]
        elif (
            isinstance(parent, list) and isinstance(seg, int) and -len(parent) <= seg < len(parent)
        ):
            parent = parent[seg]
        else:
            if isinstance(op, Unset | Remove):
                return result
            raise OperationRefusedError(f"{'.'.join(map(str, segs))}: no such mapping", op)
    last = segs[-1]
    if isinstance(parent, list):
        if isinstance(op, Unset) and isinstance(last, int) and -len(parent) <= last < len(parent):
            del parent[last]
            return result
        if isinstance(op, Set) and isinstance(last, int) and -len(parent) <= last < len(parent):
            if not values_equal(parent[last], op.value):
                parent[last] = op.value
            return result
        if isinstance(op, Unset):
            return result
        raise OperationRefusedError("no such list item", op)
    key = _find_key(parent, last)
    if isinstance(op, Set | AddSubkey):
        if key is not None and values_equal(parent[key], op.value):
            return result
        parent[last if key is None else key] = op.value
    elif isinstance(op, Unset):
        if key is not None:
            del parent[key]
    elif isinstance(op, Append):
        if key is None or parent[key] is None:
            parent[last if key is None else key] = [op.value]
        elif isinstance(parent[key], list):
            parent[key].append(op.value)
        else:
            raise OperationRefusedError(f"{op.path!r} is not a list; cannot append to it", op)
    elif isinstance(op, Remove):
        items = parent.get(key) if key is not None else None
        if items is not None and not isinstance(items, list):
            raise OperationRefusedError(f"{op.path!r} is not a list; cannot remove from it", op)
        for i, item in enumerate(items or []):
            if values_equal(item, op.value):
                del items[i]
                break
    return result


def _expected_body(doc: _Doc, op: Any) -> str:
    if isinstance(op, ReplaceBody):
        written = _written_body(doc, op.body)
        if written is not None:
            return written
    return doc.body


# --- one operation: narrow, verify, widen -------------------------------------


def _apply_one(doc: _Doc, op: Any) -> tuple[_Doc, str, bool]:
    _check_op(op)
    top = _top_key(op)
    if top is not None:
        _refuse_anchors(doc, top, op)
    expected = _model_apply(doc.data, op)
    expected_body = _expected_body(doc, op)
    if _same(expected, doc.data) and expected_body == doc.body:
        return doc, "unchanged", False  # decision 3: asked for what is there
    named = {top}
    edits = _narrow_edits(doc, op)
    problem = "the narrow edit changed nothing"
    if edits:
        text, span = _splice(doc, edits)
        problem = _verify(doc, text, span, expected, expected_body, named)
        if problem is None:
            return _parse(text, span), "changed", False
    edits = _widen_edits(doc, op, expected)
    if edits is None:
        raise OperationRefusedError(f"{problem}, and this operation cannot be widened", op)
    text, span = _splice(doc, edits)
    second = _verify(doc, text, span, expected, expected_body, named)
    if second is not None:
        raise OperationRefusedError(f"{problem}; widened item-wise, {second}", op)
    return _parse(text, span), "changed", True


_SCALARS = (str, int, float, bool, date, type(None))


def _check_op(op: Any) -> None:
    if not isinstance(op, Set | Append | Remove | Unset | ReplaceBody | AddSubkey):
        raise OperationRefusedError(f"not an operation: {op!r}", op)
    if isinstance(op, ReplaceBody):
        if not isinstance(op.body, str):
            raise OperationRefusedError("a body is a string", op)
        return
    if hasattr(op, "value"):
        _check_value(op.value, op)


def _check_value(value: Any, op: Any) -> None:
    if isinstance(value, Mapping):
        for k, v in value.items():
            _check_value(k, op)
            _check_value(v, op)
    elif isinstance(value, list | tuple):
        for v in value:
            _check_value(v, op)
    elif not isinstance(value, _SCALARS):
        raise OperationRefusedError(f"cannot write a {type(value).__name__} value", op)
    elif isinstance(value, float) and math.isnan(value):
        raise OperationRefusedError("cannot write NaN: it never equals itself", op)


def _splice(doc: _Doc, edits: list[tuple[int, int, str]]) -> tuple[str, FrontmatterSpan]:
    """Apply (start, end, replacement) edits given in ``doc.text``'s offsets.

    At one offset, a range is replaced before an insertion is made, and
    insertions keep the order they were listed in."""
    order = sorted(
        enumerate(edits),
        key=lambda ie: (ie[1][0], 1 if ie[1][1] > ie[1][0] else 0, ie[0]),
        reverse=True,
    )
    text = doc.text
    bound = len(text)
    delta = 0
    for _, (start, end, new) in order:
        if end > bound or start > end:
            raise OperationRefusedError("internal: overlapping edits")
        text = text[:start] + new + text[end:]
        bound = start
        if start >= doc.span.start and end <= doc.span.end:
            delta += len(new) - (end - start)
    span = FrontmatterSpan(doc.span.start, doc.span.end + delta, doc.span.body + delta)
    return text, span


def _top_level_blocks(text: str, span: FrontmatterSpan) -> dict[str, str]:
    """Each top-level key's lines, found by a line scan (independent of the
    composer spans the edits use): from its key line to the next key line,
    less trailing blank and comment-only lines."""
    blocks: dict[str, list[str]] = {}
    current = None
    for line in text[span.start : span.end].splitlines(keepends=True):
        head = line[:1]
        if head and head not in " \t#-\r\n" and ":" in line:
            current = line.split(":", 1)[0].strip().strip("'\"")
            blocks[current] = [line]
        elif current is not None:
            blocks[current].append(line)
    out = {}
    for key, lines in blocks.items():
        while len(lines) > 1 and (not lines[-1].strip() or lines[-1].lstrip().startswith("#")):
            lines = lines[:-1]
        out[key] = "".join(lines)
    return out


def _verify(
    before: _Doc,
    text: str,
    span: FrontmatterSpan,
    expected: dict,
    expected_body: str,
    named: set,
) -> str | None:
    """Decision 2's check: None when the result is exactly what was asked."""
    from ..models.core_types import _frontmatter_of

    try:
        parsed = _frontmatter_of(text)
    except Exception as e:  # FrontmatterError: the edit broke the YAML
        return f"the result does not parse ({e})"
    if parsed is None:
        return "the reader finds no frontmatter in the result"
    meta, body = parsed
    if not _same(_plain(meta), expected):
        return "the result does not parse to the operation applied"
    if text[: span.start] != before.text[: before.span.start]:
        return "the bytes before the frontmatter changed"
    if text[span.end : span.body] != before.text[before.span.end : before.span.body]:
        return "the closing delimiter changed"
    if text[span.body :] != expected_body or body != expected_body.strip():
        return "the body is not what was asked"
    old_blocks = _top_level_blocks(before.text, before.span)
    new_blocks = _top_level_blocks(text, span)
    for key, block in old_blocks.items():
        if key not in named and new_blocks.get(key) != block:
            return f"the untouched key {key!r} changed"
    return None


# --- the narrow edit ----------------------------------------------------------


@dataclass(frozen=True)
class _Conv:
    """How this file indents what Pyrite adds: a nested mapping's indent and a
    block list's dash offset, both relative to the parent key."""

    map_indent: int = 2
    seq_offset: int = 0


def _conventions(doc: _Doc) -> _Conv:
    map_indent = seq_offset = None
    for k, v in doc.root.value if doc.root is not None else []:
        if not _is_block(v) or not v.value or doc.s(v) < doc.e(k):
            continue
        if isinstance(v, MappingNode) and map_indent is None:
            map_indent = _col(doc, v.value[0][0]) - _col(doc, k)
        if isinstance(v, SequenceNode) and seq_offset is None:
            seq_offset = _item_cols(doc, v.value[0])[0] - _col(doc, k)
    return _Conv(
        map_indent if map_indent and map_indent > 0 else 2,
        seq_offset if seq_offset is not None and seq_offset >= 0 else 0,
    )


def _narrow_edits(doc: _Doc, op: Any) -> list[tuple[int, int, str]]:
    """The smallest splice that applies ``op``: offsets into ``doc.text``."""
    if isinstance(op, ReplaceBody):
        written = _written_body(doc, op.body)
        return [] if written is None else [(doc.span.body, len(doc.text), written)]
    conv = _conventions(doc)
    segs = _segments(op.path)
    if isinstance(op, Set):
        return _set_path(doc, segs, op.value, conv, op)
    if isinstance(op, AddSubkey):
        return _add_subkey(doc, segs, op, conv)
    if isinstance(op, Append):
        return _append(doc, segs, op, conv)
    if isinstance(op, Remove):
        return _remove(doc, segs, op, conv)
    return _unset(doc, segs, op, conv)


def _set_path(doc: _Doc, segs: list, value: Any, conv: _Conv, op: Any) -> list:
    trail, found = _walk(doc, segs)
    if found == len(segs):
        container, idx, node = trail[-1]
        return _set_node(doc, container, idx, node, _data_at(doc.data, segs), value, conv)
    rest = segs[found:]
    if any(isinstance(seg, int) for seg in rest):
        raise OperationRefusedError(f"no list item at {'.'.join(map(str, segs))}", op)
    nested = value
    for seg in reversed(rest[1:]):
        nested = {seg: nested}
    if found == 0:
        return _add_root_key(doc, rest[0], nested, conv)
    container, idx, parent = trail[found - 1]
    if isinstance(parent, MappingNode):
        return _add_pairs(doc, parent, [(rest[0], nested)], conv)
    if _is_null(parent):
        return _set_node(doc, container, idx, parent, None, {rest[0]: nested}, conv)
    raise OperationRefusedError(f"{'.'.join(map(str, segs[:found]))} is not a mapping", op)


def _add_subkey(doc: _Doc, segs: list, op: AddSubkey, conv: _Conv) -> list:
    if not segs:
        return _set_path(doc, [op.key], op.value, conv, op)
    trail, found = _walk(doc, segs)
    if found < len(segs) or not isinstance(trail[-1][2], MappingNode):
        raise OperationRefusedError(f"no mapping at {op.path!r} to add {op.key!r} to", op)
    current = _data_at(doc.data, segs)
    if _find_key(current, op.key) is not None:
        if values_equal(current[_find_key(current, op.key)], op.value):
            return []
        raise OperationRefusedError(f"{op.path!r} already has {op.key!r}; use set to change it", op)
    return _add_pairs(doc, trail[-1][2], [(op.key, op.value)], conv)


def _append(doc: _Doc, segs: list, op: Append, conv: _Conv) -> list:
    trail, found = _walk(doc, segs)
    if found < len(segs):
        return _set_path(doc, segs, [op.value], conv, op)
    container, idx, node = trail[-1]
    if _is_null(node):
        return _set_node(doc, container, idx, node, None, [op.value], conv)
    if not isinstance(node, SequenceNode):
        raise OperationRefusedError(f"{op.path!r} is not a list; cannot append to it", op)
    items = node.value
    if node.flow_style:
        hint = _style(items[-1]) if items else None
        new = _emit_flow(op.value, hint)
        if not items:
            return [(doc.s(node) + 1, doc.s(node) + 1, new)]
        end = _end(doc, items[-1])
        return [(end, end, ", " + new)]
    last = items[-1]
    dash_col, content_col = _item_cols(doc, last)
    lines = _emit_item_lines(op.value, dash_col, content_col, _style(last), conv)
    pos = _item_range(doc, last)[1]
    return [(pos, pos, "".join(line + doc.eol for line in lines))]


def _remove(doc: _Doc, segs: list, op: Remove, conv: _Conv) -> list:
    trail, found = _walk(doc, segs)
    if found < len(segs):
        return []
    container, idx, node = trail[-1]
    if _is_null(node):
        return []
    if not isinstance(node, SequenceNode):
        raise OperationRefusedError(f"{op.path!r} is not a list; cannot remove from it", op)
    current = _data_at(doc.data, segs)
    for i, item in enumerate(current):
        if values_equal(item, op.value):
            return _remove_index(doc, container, idx, node, i, conv)
    return []


def _remove_index(doc: _Doc, container: Node, idx: int, seq: SequenceNode, i: int, conv) -> list:
    if seq.flow_style:
        return _flow_delete(doc, seq.value, [i], _end)
    if len(seq.value) == 1:
        return _replace_fresh(doc, container, idx, seq, [], conv)
    start, end = _item_range(doc, seq.value[i])
    return [(start, end, "")]


def _unset(doc: _Doc, segs: list, op: Unset, conv: _Conv) -> list:
    trail, found = _walk(doc, segs)
    if found < len(segs):
        return []
    container, idx, _ = trail[-1]
    if isinstance(container, SequenceNode):
        parent, pidx, _ = trail[-2] if len(trail) > 1 else (doc.root, 0, container)
        return _remove_index(doc, parent, pidx, container, idx, conv)
    if container is doc.root:
        k, v = container.value[idx]
        start = _line_start(doc.text, doc.s(k))
        return [(start, _own_end(doc, _pair_end(doc, k, v)), "")]
    if container.flow_style:
        return _flow_delete(doc, container.value, [idx], lambda d, p: _pair_end(d, *p))
    if len(container.value) == 1:
        parent, pidx, _ = trail[-2]
        return _replace_fresh(doc, parent, pidx, container, {}, conv)
    return _delete_pairs(doc, container, {idx})


# --- setting a node to a value ------------------------------------------------


def _set_node(doc: _Doc, container: Node, idx: int, node: Node, cur: Any, new: Any, conv) -> list:
    if values_equal(cur, new):
        return []
    if isinstance(new, tuple):
        new = list(new)
    collection = isinstance(new, Mapping | list)
    if isinstance(node, ScalarNode) and not collection and not _is_empty(node):
        return _replace_scalar(doc, node, new, bool(container.flow_style))
    if isinstance(node, MappingNode) and isinstance(new, Mapping) and new:
        return _edit_map(doc, container, idx, node, cur, new, conv)
    if isinstance(node, SequenceNode) and isinstance(new, list) and new:
        return _edit_seq(doc, container, idx, node, cur, new, conv)
    if isinstance(node, MappingNode | SequenceNode) and node.flow_style and collection:
        return [(doc.s(node), doc.e(node), _emit_flow(new, None))]
    return _replace_fresh(doc, container, idx, node, new, conv)


def _style(node: Node) -> str | None:
    return node.style if isinstance(node, ScalarNode) else None


def _scalar_edit(doc: _Doc, node: Node, new_text: str, flow: bool) -> list:
    """Replace a scalar's text. A trailing comment keeps its bytes; it keeps
    its column when the new value fits before it, and otherwise its gap."""
    start, end = doc.s(node), _end(doc, node)
    text = doc.text
    if not flow and "\n" not in text[start:end] and "\n" not in new_text:
        rest = text[end : _content_end_of_line(text, end)]
        comment = rest.lstrip(" ")
        if comment.startswith("#") and len(rest) > len(comment):
            gap = len(rest) - len(comment)
            line = _line_start(text, start)
            comment_col = end - line + gap
            new_end_col = start - line + len(new_text)
            new_gap = comment_col - new_end_col if new_end_col + 1 <= comment_col else gap
            return [(start, end + gap, new_text + " " * new_gap)]
    return [(start, end, new_text)]


def _replace_scalar(doc: _Doc, node: ScalarNode, new: Any, flow: bool) -> list:
    if node.style in ("|", ">") and isinstance(new, str):
        indent = _block_indent(doc, node)
        block = _emit_block_scalar(new, node.style, indent, doc.eol)
        if block is None and node.style == ">":
            block = _emit_block_scalar(new, "|", indent, doc.eol)
        if block is not None:
            return [(doc.s(node), _end(doc, node), block)]
    return _scalar_edit(doc, node, _emit_scalar(new, node.style, node.tag, flow), flow)


def _block_indent(doc: _Doc, node: ScalarNode) -> int:
    text = doc.text
    pos = _line_end(text, doc.s(node))
    end = _end(doc, node)
    while pos < end:
        line_end = _line_end(text, pos)
        line = text[pos:line_end]
        if line.strip():
            return len(line) - len(line.lstrip(" "))
        pos = line_end
    start = _line_start(text, doc.s(node))
    head = text[start : doc.s(node)]
    return len(head) - len(head.lstrip(" ")) + 2


def _edit_map(doc, container, idx, node: MappingNode, cur: Mapping, new: Mapping, conv) -> list:
    curs = {str(k): v for k, v in cur.items()}
    news = {str(k): (k, v) for k, v in new.items()}
    edits: list = []
    deleted: set[int] = set()
    for i, (k, v) in enumerate(node.value):
        if k.value in news:
            edits += _set_node(doc, node, i, v, curs.get(k.value), news[k.value][1], conv)
        else:
            deleted.add(i)
    if len(deleted) == len(node.value):
        if node.flow_style:
            return [(doc.s(node), doc.e(node), _emit_flow(new, None))]
        return _replace_fresh(doc, container, idx, node, new, conv)
    if deleted:
        if node.flow_style:
            edits += _flow_delete(doc, node.value, sorted(deleted), lambda d, p: _pair_end(d, *p))
        else:
            edits += _delete_pairs(doc, node, deleted)
    added = [
        (k, v) for name, (k, v) in news.items() if name not in {p[0].value for p in node.value}
    ]
    if added:
        edits += _add_pairs(doc, node, added, conv)
    return edits


def _delete_pairs(doc: _Doc, node: MappingNode, deleted: set[int]) -> list:
    """Each deleted pair's own lines. A first pair that shares its line with a
    list dash (``- target: x``) is cut up to the next kept key instead."""
    edits = []
    pairs = node.value
    skip: set[int] = set()
    first_k = pairs[0][0]
    if 0 in deleted and doc.text[_line_start(doc.text, doc.s(first_k)) : doc.s(first_k)].strip():
        kept = next(i for i in range(len(pairs)) if i not in deleted)
        edits.append((doc.s(first_k), doc.s(pairs[kept][0]), ""))
        skip = set(range(kept))
    for i in sorted(deleted - skip):
        k, v = pairs[i]
        start = _line_start(doc.text, doc.s(k))
        edits.append((start, _own_end(doc, _pair_end(doc, k, v)), ""))
    return edits


def _add_pairs(doc: _Doc, node: MappingNode, pairs: list, conv: _Conv) -> list:
    if node is doc.root:
        return [e for key, value in pairs for e in _add_root_key(doc, key, value, conv)]
    if node.flow_style:
        new = ", ".join(f"{_emit_key(k, True)}: {_emit_flow(v, None)}" for k, v in pairs)
        if not node.value:
            return [(doc.s(node) + 1, doc.s(node) + 1, new)]
        end = _pair_end(doc, *node.value[-1])
        return [(end, end, ", " + new)]
    col = _col(doc, node.value[0][0])
    pos = _own_end(doc, _pair_end(doc, *node.value[-1]))
    lines = [line for k, v in pairs for line in _emit_pair_lines(k, v, col, conv)]
    return [(pos, pos, "".join(line + doc.eol for line in lines))]


def _add_root_key(doc: _Doc, key: Any, value: Any, conv: _Conv) -> list:
    """A new top-level key goes on the last line of the frontmatter."""
    pos = doc.span.end
    lines = _emit_pair_lines(key, value, 0, conv)
    return [(pos, pos, "".join(line + doc.eol for line in lines))]


def _edit_seq(doc, container, idx, node: SequenceNode, cur: list, new: list, conv) -> list:
    """Reduce a set of a whole list to item edits: items equal in order are
    kept byte for byte (an LCS under decision 3 equality); paired items of the
    same kind are set in place; the rest are deleted or inserted."""
    items = node.value
    plan = _plan(cur, new)
    if plan is None:  # nothing kept: write the list afresh, in its own style
        if node.flow_style:
            hint = _style(items[0]) if items else None
            return [(doc.s(node), doc.e(node), _emit_flow(new, hint))]
        return _replace_fresh(doc, container, idx, node, new, conv)
    replaces, deletes, inserts, first_kept = plan
    edits: list = []
    for i, j in replaces.items():
        edits += _set_node(doc, node, i, items[i], cur[i], new[j], conv)
    if node.flow_style:
        edits += _flow_delete(doc, items, deletes, _end)
        for anchor, js in inserts:
            ref = items[first_kept if anchor is None else anchor]
            text = ", ".join(_emit_flow(new[j], _style(ref)) for j in js)
            if anchor is None:
                edits.append((doc.s(ref), doc.s(ref), text + ", "))
            else:
                end = _end(doc, ref)
                edits.append((end, end, ", " + text))
        return edits
    for i in deletes:
        start, end = _item_range(doc, items[i])
        edits.append((start, end, ""))
    for anchor, js in inserts:
        ref = items[first_kept if anchor is None else anchor]
        start, end = _item_range(doc, ref)
        dash_col, content_col = _item_cols(doc, ref)
        lines = [
            line
            for j in js
            for line in _emit_item_lines(new[j], dash_col, content_col, _style(ref), conv)
        ]
        pos = start if anchor is None else end
        edits.append((pos, pos, "".join(line + doc.eol for line in lines)))
    return edits


def _plan(old: list, new: list):
    """(replaces {old: new}, deletes [old], inserts [(anchor old or None, [new])],
    first kept old index), or None when no old item is kept."""
    n, m = len(old), len(new)
    lcs = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            if values_equal(old[i], new[j]):
                lcs[i][j] = lcs[i + 1][j + 1] + 1
            else:
                lcs[i][j] = max(lcs[i + 1][j], lcs[i][j + 1])
    matches = []
    i = j = 0
    while i < n and j < m:
        if values_equal(old[i], new[j]) and lcs[i][j] == lcs[i + 1][j + 1] + 1:
            matches.append((i, j))
            i, j = i + 1, j + 1
        elif lcs[i + 1][j] >= lcs[i][j + 1]:
            i += 1
        else:
            j += 1
    replaces: dict[int, int] = {}
    deletes: list[int] = []
    inserts: list[tuple[int | None, list[int]]] = []
    last_kept: int | None = None
    pi = pj = -1
    for mi, mj in [*matches, (n, m)]:
        olds, news = list(range(pi + 1, mi)), list(range(pj + 1, mj))
        k = 0
        while k < min(len(olds), len(news)) and _same_kind(old[olds[k]], new[news[k]]):
            replaces[olds[k]] = news[k]
            last_kept = olds[k]
            k += 1
        deletes += olds[k:]
        if news[k:]:
            inserts.append((last_kept, news[k:]))
        if mi < n:
            last_kept = mi
        pi, pj = mi, mj
    kept = sorted(set(replaces) | {mi for mi, _ in matches})
    if not kept:
        return None
    return replaces, deletes, inserts, kept[0]


def _same_kind(a: Any, b: Any) -> bool:
    if isinstance(a, Mapping) or isinstance(b, Mapping):
        return isinstance(a, Mapping) and isinstance(b, Mapping)
    if isinstance(a, list) or isinstance(b, list):
        return False
    return True


def _flow_delete(doc: _Doc, items: list, deletes: list[int], end_of) -> list:
    """Delete runs of items from a flow collection with one separator each."""
    edits = []
    n = len(items)

    def start_of(item):
        return doc.s(item[0]) if isinstance(item, tuple) else doc.s(item)

    def stop_of(item):
        return end_of(doc, item)

    runs: list[list[int]] = []
    for i in sorted(deletes):
        if runs and runs[-1][-1] == i - 1:
            runs[-1].append(i)
        else:
            runs.append([i])
    for run in runs:
        a, b = run[0], run[-1]
        if b + 1 < n:
            edits.append((start_of(items[a]), start_of(items[b + 1]), ""))
        elif a > 0:
            edits.append((stop_of(items[a - 1]), stop_of(items[b]), ""))
        else:
            edits.append((start_of(items[a]), stop_of(items[b]), ""))
    return edits


def _replace_fresh(doc: _Doc, container: Node, idx: int, node: Node, new: Any, conv) -> list:
    """Write a value whose shape changed (a scalar becomes a list, a list
    becomes empty) afresh at the node's place. Only the touched value's bytes
    are emitted; its key line, and a comment on it, stay."""
    text, eol = doc.text, doc.eol
    if container.flow_style:
        return [(doc.s(node), _end(doc, node), _emit_flow(new, None))]
    inline = _is_inline(new)
    old_block = _is_block(node)
    if isinstance(container, MappingNode):
        key = container.value[idx][0]
        colon = _colon_end(doc, key)
        if inline:
            value = _emit_inline(new, _style(node))
            if _is_empty(node):
                return [(colon, colon, " " + value)]
            if old_block:
                return [(colon, _content_end_of_line(text, _end(doc, node) - 1), " " + value)]
            return _scalar_edit(doc, node, value, False)
        step = conv.map_indent if isinstance(new, Mapping) else conv.seq_offset
        lines = _emit_block(new, _col(doc, key) + step, conv)
        if old_block:
            first = _line_start(text, doc.s(node))
            return [(first, _own_end(doc, _end(doc, node)), "".join(x + eol for x in lines))]
        if _is_empty(node):
            at = _content_end_of_line(text, colon)
            return [(at, at, eol + eol.join(lines))]
        end = _end(doc, node)
        at = _content_end_of_line(text, end - 1)
        return [(colon, end, ""), (at, at, eol + eol.join(lines))]
    # an item of a block sequence
    col = _col(doc, node)
    end = _content_end_of_line(text, _end(doc, node) - 1) if old_block else _end(doc, node)
    if inline:
        return [(doc.s(node), end, _emit_inline(new, _style(node)))]
    lines = _emit_block(new, col, conv)
    return [(doc.s(node), end, lines[0][col:] + "".join(eol + x for x in lines[1:]))]


# --- the widen (decision 2): rebuild one top-level key item-wise ---------------


def _widen_edits(doc: _Doc, op: Any, expected: dict) -> list | None:
    """Rebuild the touched top-level key's value, copying every unchanged
    item's bytes from the file and emitting only what changed. None when there
    is no item-wise form (a new key, an unset, a scalar, the body)."""
    top = _top_key(op)
    if top is None or doc.root is None:
        return None
    idx = _key_index(doc.root, top)
    new_key = _find_key(expected, top)
    if idx is None or new_key is None:
        return None
    key, node = doc.root.value[idx]
    old, new = doc.data[_find_key(doc.data, top)], expected[new_key]
    conv = _conventions(doc)
    text, eol = doc.text, doc.eol
    if node.flow_style and isinstance(node, SequenceNode) and isinstance(new, list):
        pieces, used = [], 0
        for item in new:
            copied = None
            for i in range(used, len(node.value)):
                if values_equal(old[i], item):
                    copied = text[doc.s(node.value[i]) : _end(doc, node.value[i])]
                    used = i + 1
                    break
            pieces.append(copied if copied is not None else _emit_flow(item, None))
        return [(doc.s(node), doc.e(node), "[" + ", ".join(pieces) + "]")]
    if not _is_block(node) or not new or not isinstance(new, Mapping | list):
        return None
    region = (_line_start(text, doc.s(node)), _own_end(doc, _end(doc, node)))
    chunks: list[str] = []
    if isinstance(node, SequenceNode) and isinstance(new, list):
        dash_col, content_col = _item_cols(doc, node.value[0])
        used = 0
        for item in new:
            for i in range(used, len(node.value)):
                if values_equal(old[i], item):
                    start, end = _item_range(doc, node.value[i])
                    chunks.append(text[start:end])
                    used = i + 1
                    break
            else:
                lines = _emit_item_lines(item, dash_col, content_col, None, conv)
                chunks.append("".join(x + eol for x in lines))
    elif isinstance(node, MappingNode) and isinstance(new, Mapping):
        col = _col(doc, node.value[0][0])
        olds = {str(k): v for k, v in old.items()}
        for name, value in new.items():
            i = _key_index(node, name)
            if i is not None and values_equal(olds.get(str(name)), value):
                k, v = node.value[i]
                start = _line_start(text, doc.s(k))
                chunks.append(text[start : _own_end(doc, _pair_end(doc, k, v))])
            else:
                chunks.append("".join(x + eol for x in _emit_pair_lines(name, value, col, conv)))
    else:
        return None
    return [(region[0], region[1], "".join(chunks))]


# --- the emitter: bytes for new values only -----------------------------------


def _is_inline(value: Any) -> bool:
    return not isinstance(value, Mapping | list | tuple) or not value


def _emit_inline(value: Any, style: str | None) -> str:
    if isinstance(value, Mapping):
        return "{}"
    if isinstance(value, list | tuple):
        return "[]"
    return _emit_scalar(value, style, None, False)


def _emit_pair_lines(key: Any, value: Any, col: int, conv: _Conv) -> list[str]:
    pad = " " * col
    name = _emit_key(key, False)
    if _is_inline(value):
        return [f"{pad}{name}: {_emit_inline(value, None)}"]
    step = conv.map_indent if isinstance(value, Mapping) else conv.seq_offset
    return [f"{pad}{name}:", *_emit_block(value, col + step, conv)]


def _emit_block(value: Any, col: int, conv: _Conv) -> list[str]:
    """A non-empty mapping or list in block style, its first line at ``col``."""
    if isinstance(value, Mapping):
        return [line for k, v in value.items() for line in _emit_pair_lines(k, v, col, conv)]
    return [line for item in value for line in _emit_item_lines(item, col, col + 2, None, conv)]


def _emit_item_lines(item: Any, dash_col: int, content_col: int, style, conv) -> list[str]:
    lead = " " * dash_col + "-" + " " * max(1, content_col - dash_col - 1)
    if _is_inline(item):
        return [lead + _emit_inline(item, style)]
    lines = _emit_block(item, content_col, conv)
    return [lead + lines[0][content_col:], *lines[1:]]


def _emit_flow(value: Any, style: str | None) -> str:
    if isinstance(value, Mapping):
        inner = ", ".join(f"{_emit_key(k, True)}: {_emit_flow(v, None)}" for k, v in value.items())
        return "{" + inner + "}"
    if isinstance(value, list | tuple):
        return "[" + ", ".join(_emit_flow(v, style) for v in value) + "]"
    return _emit_scalar(value, style, None, True)


def _emit_key(key: Any, flow: bool) -> str:
    if not isinstance(key, str):
        return _emit_scalar(key, None, None, flow)
    if key and key == key.strip() and "\n" not in key and not _is_yaml11_ambiguous(key):
        probe = f"{{{key}: 0}}" if flow else f"{key}: 0"
        try:
            loaded = YAML().load(probe)
        except YAMLError:
            loaded = None
        if isinstance(loaded, Mapping) and list(loaded) == [key] and type(str(key)) is str:
            return key
    return _double(key)


def _emit_scalar(value: Any, style: str | None, tag: str | None, flow: bool) -> str:
    """One scalar, in ``style`` where it can be. A string a YAML 1.1 reader
    would misread is never written bare (decision 6)."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(int(value))
    if isinstance(value, float):
        if math.isinf(value):
            return ".inf" if value > 0 else "-.inf"
        return repr(float(value))
    if isinstance(value, date):
        return value.isoformat()
    if style == "'" and _single_ok(value):
        return "'" + value.replace("'", "''") + "'"
    if style == '"' or style == "'":
        return _double(value)
    if _plain_ok(value, flow, timestamp=bool(tag and tag.endswith(":timestamp"))):
        return value
    return _double(value)


def _plain_ok(value: str, flow: bool, timestamp: bool) -> bool:
    """Does ``value`` written bare read back as itself under YAML 1.2, and not
    as anything else under YAML 1.1? A string written over a date may stay
    bare when it reads back as that same date (decision 3)."""
    if not value or value != value.strip() or "\n" in value or "\r" in value:
        return False
    if _is_yaml11_ambiguous(value):
        return False
    try:
        loaded = YAML().load(f"k: [{value}]" if flow else f"k: {value}")
    except YAMLError:
        return False
    if not isinstance(loaded, Mapping) or list(loaded) != ["k"]:
        return False
    got = loaded["k"]
    if flow:
        if not isinstance(got, list) or len(got) != 1:
            return False
        got = got[0]
    if isinstance(got, str):
        return type(got) is str and got == value
    return timestamp and isinstance(got, date) and values_equal(got, value)


def _single_ok(value: str) -> bool:
    return not any(ord(ch) < 0x20 or 0x7F <= ord(ch) <= 0x9F or ch in "  ﻿" for ch in value)


_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\t": "\\t", "\r": "\\r", "\0": "\\0"}


def _double(value: str) -> str:
    out = []
    for ch in value:
        if ch in _ESCAPES:
            out.append(_ESCAPES[ch])
        elif ord(ch) < 0x20 or 0x7F <= ord(ch) <= 0x9F or ch in "  ﻿":
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _emit_block_scalar(value: str, style: str, indent: int, eol: str) -> str | None:
    """``value`` as a literal (``|``) or folded (``>``) block scalar whose
    content lines sit at ``indent``. None when the value needs a form this
    does not write (keep chomping, a first line starting with a space, a
    folded line that would be read as more-indented)."""
    stripped = value.rstrip("\n")
    trailing = len(value) - len(stripped)
    if trailing > 1 or not stripped or "\r" in value:
        return None
    chomp = "-" if trailing == 0 else ""
    parts = stripped.split("\n")
    if parts[0][:1] in (" ", "\t"):
        return None
    if style == ">":
        if any(p[:1] in (" ", "\t") for p in parts) or not parts[0]:
            return None
        lines = [parts[0]]
        empties = 0
        for part in parts[1:]:
            if not part:
                empties += 1
                continue
            lines += [""] * (empties + 1) + [part]
            empties = 0
        parts = lines
    body = eol.join((" " * indent + p) if p else "" for p in parts)
    return f"{style}{chomp}{eol}{body}"


def _written_body(doc: _Doc, new: str) -> str | None:
    """The body text a ReplaceBody writes, or None when it changes nothing."""
    old = doc.body
    new_n = new.replace("\r\n", "\n")
    if new_n.strip() == old.replace("\r\n", "\n").strip():
        return None
    pos = 0
    while True:
        nl = old.find("\n", pos)
        if nl < 0 or old[pos:nl].strip():
            break
        pos = nl + 1
    lead = old[:pos]
    lines = new_n.split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    core = "\n".join(lines).rstrip()
    if doc.eol == "\r\n":
        core = core.replace("\n", "\r\n")
    trail = old[len(old.rstrip()) :] if old.strip() else doc.eol
    return lead + core + trail
