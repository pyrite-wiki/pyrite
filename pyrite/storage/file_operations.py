"""Operations on an entry file's text: ADR-0042 decisions 1, 2, 3 and 6.

``apply(text, ops, span)`` applies field operations (set, append, remove,
unset, replace the body, add a sub-key) to the file's own text and returns the
new text and a report. It is a pure function. It touches no file and is not
wired to any write path yet (B6 phase P3 does that).

**The model: a value-level patch.** Pyrite patches a file the way git does,
but at the level of the values it knows the types of. The result of ``apply``
differs from its input only inside the *spans* of the values the operations
name. Every other byte is identical: comments, blank lines, other keys, the
delimiters, the BOM and the body.

**Who owns which bytes** (the span rule). For a value named by a set, an
append, a remove or an add-sub-key:

=====================================================  ==========================
bytes                                                  owner
=====================================================  ==========================
key, ``:`` and the indentation of the key line         the pair (unset only)
the separator after ``:`` and an inline value          the value
(scalar or flow), with the spaces/tabs after it
a comment after an inline value, or on a key line      the pair: kept by a set,
before a block value, or on a block scalar's header    removed by an unset
a block scalar's indicators and its content lines      the value
(a ``|+`` scalar's trailing blank lines are content)
a block collection's lines, from the end of its key    the value
line to its last content line, comments between its
items included
blank and comment-only lines after a value's last      outside every span
content line, before the next key or item
lines before the first key, after the last key         outside every span
delimiters, BOM; the body                              outside; the body is
                                                       ReplaceBody's
=====================================================  ==========================

An unset owns its pair's or item's own lines: from the start of the key (or
dash) line to the end of the last content line, comments on those lines
included. A pair that shares a list item's dash line (``- target: x``) also
owns the indentation of the next key's line when nothing but spaces lies
between them. Unsetting the only key of a mapping, or the only item of a list,
is a set of that collection to ``{}`` or ``[]``, so it owns that collection's
span. A new top-level key is written at the end of the frontmatter; a new
nested key at the end of its mapping's span.

**How an edit is made.** Each operation is a splice inside those spans.
Spans come from ruamel.yaml's composer, the YAML 1.2 parser that reads use
(amendment A4). Existing bytes are never re-emitted through a serializer. Only
the bytes of a new value are written, by the small emitter below. A new list
item copies its neighbour's indent and quoting; a replaced scalar keeps its
quote style; a trailing comment keeps its bytes, and its column when the new
value fits before it; new lines use the file's line ending. A string a YAML
1.1 reader would misread is quoted.

**The post-check (decision 2) runs after every operation.** It computes the
allowed spans *by its own rule* (``_named_spans``: a node's ``end_mark``
walked back over blank and comment-only lines), not with the code that made
the edit (``_end``, which recurses into the last child). It then checks that
the bytes outside those spans are identical, before against after, and that
the result parses to exactly the operation applied. A narrow edit that fails
is widened item-wise, rebuilding the named top-level value and copying each
unchanged item's bytes. If that fails too, the call raises
``OperationRefusedError``. The whole call is checked once more at the end,
through the reader's own split (``load_frontmatter``).

**Keys.** A path segment names the key whose loaded value it equals, the
segment read as a YAML scalar: ``"true"`` names ``true:`` (a bool) and
``"true":`` (a string); both in one mapping is refused as ambiguous. A new key
is always written as a string. ``_key_matches`` is the one rule; ``_walk``
(the nodes) and ``_lookup`` (the values) both use it.

**Where the frontmatter is** comes from the caller, as a ``FrontmatterSpan``.
This module has no frontmatter splitter: ``split_frontmatter``
(``pyrite.utils.frontmatter``, #744) supplies the span, and the final check
reads the result with ``load_frontmatter``.

**Limits, refused or restyled, each pinned by a test** (the ``test_limit_*``
tests in ``tests/test_file_operations.py``):

- A block scalar without keep chomping (``|``, ``|-``) set to a value with two
  or more trailing newlines is written double-quoted: switching it to ``|+``
  would make any blank lines after it content
  (``test_limit_keep_chomping_on_a_clip_scalar_is_written_double_quoted``).
- A folded (``>``) value with a line that starts with a space is written in
  literal (``|``) style
  (``test_limit_a_folded_value_with_an_indented_line_is_written_literal``); a
  block-scalar value with a control character is written double-quoted.
- A list item laid out as ``- - x`` (a list directly in a list item) is not
  edited in place. Refused
  (``test_limit_a_list_written_directly_in_a_list_item_is_refused``).
- A line inside a scalar that looks like a comment (``# x`` in a literal
  block scalar, or on a continuation line of a multi-line quoted scalar),
  when it is the last line of a collection, ends that collection's span early
  for the check: an insertion after it is refused, never wrongly accepted
  (``test_limit_a_comment_looking_last_line_refuses_an_insertion_after_it``).
- L3: operations on different top-level keys in one call are spliced against
  one parse, so newly emitted lines follow the file's indentation conventions
  as they were before the call; applied one call at a time, a later
  operation sees the conventions the earlier one left. The values are the
  same; the indentation of new lines may differ
  (``test_limit_a_batch_indents_new_lines_by_the_conventions_before_it``).
- L6 (cosmetic): unsetting the first pair on a dash line (``- target: x``)
  whose next line is a comment leaves ``-   # comment``: the comment line,
  indentation included, is outside the unset's span
  (``test_limit_unset_of_a_dash_line_pair_before_a_comment_leaves_the_dash_alone``).
- When a widen runs, the top-level value is rebuilt item-wise: every
  unchanged pair or item keeps its bytes, and so do the comment lines between
  them. Only a *changed* pair (a map) or item (a list) is written fresh, so
  the comments inside it go with it and its indentation can change
  (``test_the_widen_of_a_map_keeps_the_files_order_and_its_comments``,
  ``test_the_widen_of_a_nested_operation_rebuilds_its_top_level_value``). No
  natural input reached a widen in the #732 cold reads; it is a backstop.
- NaN (#749): ``.nan`` equals ``.nan`` here (``_scalar_eq``), as a key, a
  value or a list item, so a file holding one stays editable, an unchanged one
  is "unchanged", and one can be written (``.nan``). ruamel's NaN is never
  equal to itself; that is the loader's, not a property Pyrite relies on.
- Tagged keys (#749): a key with an explicit tag (``!foo a: 1``,
  ``!!str a: 1``, ``!!float 1: a``, ``!!binary aGk=: a``) anywhere in the
  frontmatter refuses every operation, ``ReplaceBody`` included, with the
  reason "tagged key ... Pyrite cannot model, so it edits nothing in this
  file". ruamel loads it as a ``TaggedScalar`` (or as a different type than
  the key reads plain), which no path names. Two guards, each with a case the
  other cannot see: ``_has_tagged_key`` (the node's explicit tag against its
  plain reading: ``!!float 1``) and ``_loaded_tagged_key`` (what ruamel
  loaded: ``!!str x``). Every container a key can be loaded into is walked and
  has a case in ``_KEY_CONTAINERS``: a mapping (``CommentedMap``, ``!!omap``),
  a set (``CommentedSet``, an ``abc.Set`` not a ``set``; its members are the
  keys), the ``(key, value)`` tuples of ``!!pairs``, a tagged collection, and
  any of them nested in lists or each other (each case is checked against
  ruamel's loader by ``test_every_key_container_case_loads_a_tagged_scalar_key``).
  The tagged check runs before the duplicate-key refusal, so ``!!str a: 1``
  beside ``a: 2`` is refused as tagged. Limit (#769): a tag inside the
  *value* of a ``!!set`` member (``s: !!set {? a: {!!str k: 1}}``) is
  discarded by ruamel, so nothing loaded shows it and it is not refused; an
  edit elsewhere keeps those bytes
  (``test_limit_a_tag_inside_a_set_members_value_is_not_seen``). Body-only writes on a file whose
  frontmatter Pyrite cannot model are refused too; allowing them is a
  separate change.
- Unicode normalisation (#749): no normalisation. A path segment or new
  sub-key that differs from an existing *key* only by NFC canonical
  equivalence (NFD against NFC) is refused (``_lookup``): ruamel keeps the
  file's spelling, and an added look-alike would sit beside it. Not caught:
  compatibility look-alikes (full-width, ligatures), case, other scripts
  (Cyrillic ``a``), zero-width characters, and any list *value* (a
  ``Remove``/``Append`` compares strings exactly). The file's own spelling,
  and a file holding both spellings, are edited exactly
  (``test_a_key_in_another_unicode_normalisation_is_refused_not_added_beside_it``).
- Unsetting a list's last item keeps the comment lines between its items.
  #749 reported otherwise; it did not reproduce on ~16000 generated layouts
  and is pinned
  (``test_unsetting_a_lists_last_item_keeps_the_comment_lines_between_its_items``).

Fixed in round 2, each with a test: a second operation on the same key in
one call (L1: each operation is checked against the text it applied to); a
value shared within one call, ``[d, d]`` (L2: ``_unshare``); a tagged value
anywhere (L4: ``_plain`` and ``values_equal`` compare it); a new key spelled
like a document marker (L5: quoted); a complex key (refused with a reason).

Trap, measured on ruamel 0.19.1 (#732): a node's ``end_mark`` is not where its
bytes end. A block sequence or mapping ends after the comment line that
follows it; a block scalar ends after the blank lines that follow it. An alias
composes to the *same node object* as its anchor, so its span is the anchor's
bytes. The anchor refusal covers aliases.
"""

from __future__ import annotations

import functools
import math
import threading
import unicodedata
from collections.abc import Mapping
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError
from ruamel.yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from ..utils.frontmatter import Malformed, Unsupported, Unterminated, describe, split_frontmatter
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
    segments: strings for keys, ints for list indexes). A missing key is
    added; missing parent mappings are created."""

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
    for op in ops:
        _check_op(op)
    original = _parse(text, span)
    current = original
    results: list[OpResult] = []
    widened: list[str] = []
    expected = original.data
    for op in ops:
        expected = _model_apply(expected, op)
    i = 0
    while i < len(ops):
        batch = _batch(current, ops, i)
        if len(batch) > 1:
            done = _apply_batch(current, batch)
            if done is not None:
                current, outcomes = done
                results += [OpResult(op, out) for op, out in zip(batch, outcomes, strict=True)]
                i += len(batch)
                continue
        # each operation is checked against the text it applies to, so a
        # second operation on the same key is checked where the first left it
        current, outcome, was_widened = _apply_one(current, ops[i])
        results.append(OpResult(ops[i], outcome))
        if was_widened:
            widened.append(str(_top_key(ops[i])))
        i += 1
    report = Report(tuple(results), tuple(widened))
    if current.text == text:
        return text, report
    problem = _final_check(current, expected)
    if problem:
        raise OperationRefusedError(f"the result failed the post-check: {problem}")
    return current.text, report


# --- decision 3: equality ---------------------------------------------------


def values_equal(a: Any, b: Any) -> bool:
    """ADR-0042 decision 3. A string equals a date or timestamp when it is
    that value's ISO form or the same instant; int and float compare by value;
    a bool never equals an int; mappings compare unordered; lists in order."""
    if type(a) is type(b) and type(a) in (str, int):
        return a == b
    a, b = _plain(a), _plain(b)
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, int | float) and isinstance(b, int | float):
        return _scalar_eq(a, b)
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
        for k, v in a.items():
            other = _exact(b, k)
            if other is _MISSING or not values_equal(v, b[other]):
                return False
        return True
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(values_equal(x, y) for x, y in zip(a, b, strict=True))
    if type(a) is not type(b):
        return False
    if isinstance(a, frozenset):  # a !!set: its members compare as values (NaN too)
        return len(a) == len(b) and all(any(values_equal(x, y) for y in b) for x in a)
    return a == b  # bytes


def _scalar_eq(a: Any, b: Any) -> bool:
    """``a == b``, except that NaN equals NaN. ruamel loads ``.nan`` as a float
    that is never equal to itself, so a file holding one would fail the
    post-check on every edit, including of other keys (#749)."""
    return a == b or (isinstance(a, float) and isinstance(b, float) and a != a and b != b)


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
    """ruamel's round-trip types as plain Python values. A value with a
    custom tag (``!custom 1``) becomes ``("!tag", tag, value)`` and a
    ``!!set`` a frozenset, so an untouched one compares equal to itself after
    a reparse (round 2: they made every edit of the file refuse)."""
    if type(value).__name__ == "TaggedScalar":
        tag = getattr(value.tag, "value", value.tag)
        return ("!tag", str(tag), _plain(value.value))
    if isinstance(value, set | frozenset):
        return frozenset(_plain(v) for v in value)
    if isinstance(value, Mapping):
        return {_plain(k): _plain(v) for k, v in value.items()}
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
    return _same_keys(got, expected)


def _same_keys(got: Mapping, expected: Mapping) -> bool:
    """The two mappings' keys are equal one by one, in order (NaN equals NaN)."""
    a, b = [_plain(k) for k in got], [_plain(k) for k in expected]
    return len(a) == len(b) and all(_scalar_eq(x, y) for x, y in zip(a, b, strict=True))


# --- keys: the one lookup rule ------------------------------------------------

_MISSING = object()
_LOCAL = threading.local()


def _yaml() -> YAML:
    """One YAML instance per thread, reused: building one per parse was a
    third of the cost of 500 operations (#732 round 1)."""
    y = getattr(_LOCAL, "yaml", None)
    if y is None:
        y = _LOCAL.yaml = YAML()
    return y


@functools.lru_cache(maxsize=4096)
def _scalar_of(text: str) -> Any:
    """What ``text`` reads as when written bare as a YAML 1.2 scalar key."""
    try:
        value = YAML().load(f"{text}: 0") if text else None
    except YAMLError:
        return text
    if isinstance(value, Mapping) and len(value) == 1:
        return _plain(next(iter(value)))
    return text


def _key_matches(seg: Any, key: Any) -> bool:
    """THE rule for which key a path segment names. A string segment names a
    string key spelled the same, or a non-string key (``true``, ``null``,
    ``1``, a date) that the segment reads as. An int segment never names a
    mapping key; it is a list index."""
    if isinstance(seg, str) and isinstance(key, str):
        return seg == key
    if not isinstance(seg, str) or isinstance(key, str):
        return False
    target = _scalar_of(seg)
    if isinstance(target, str):
        return False
    key = _plain(key)
    return type(target) is type(key) and _scalar_eq(target, key)


def _lookup(mapping: Mapping, seg: Any) -> Any:
    """The key of ``mapping`` that path segment ``seg`` names (``_key_matches``),
    or ``_MISSING``. Two keys named by one segment: refused as ambiguous."""
    hits = [k for k in mapping if _key_matches(seg, k)]
    if len(hits) > 1:
        raise OperationRefusedError(f"{seg!r} names two keys here ({hits!r}); edit it by hand")
    if not hits and isinstance(seg, str):
        # Choice (#749): no Unicode normalisation. ruamel keeps the file's
        # spelling, so a key in NFD is not the NFC string it looks like, and
        # adding the NFC one would put a look-alike beside it. Refuse; edit it
        # by hand, or use the file's own spelling.
        form = unicodedata.normalize("NFC", seg)
        for k in mapping:
            if isinstance(k, str) and k != seg and unicodedata.normalize("NFC", k) == form:
                raise OperationRefusedError(
                    f"{seg!r} is spelled in a different Unicode normalisation than the "
                    f"key {k!r} here; use the file's spelling, or edit it by hand"
                )
    return hits[0] if hits else _MISSING


def _exact(mapping: Mapping, key: Any) -> Any:
    """The key of ``mapping`` equal to ``key`` with the same type (``True`` is
    not ``1``), or ``_MISSING``."""
    if type(key) is str:
        return key if key in mapping else _MISSING
    plain = _plain(key)
    for k in mapping:
        if type(k) is not str and type(_plain(k)) is type(plain) and _scalar_eq(_plain(k), plain):
            return k
    return _MISSING


def _node_key(node: Node) -> Any:
    """A key node's loaded value: a string, or what its plain text reads as."""
    if not isinstance(node, ScalarNode):
        return _MISSING  # a complex key (``? [a, b]``): no path names it
    if node.tag.endswith(":str"):
        return node.value
    return _scalar_of(node.value)


# --- the parsed document ----------------------------------------------------


@dataclass
class _Doc:
    text: str
    span: FrontmatterSpan
    root: MappingNode | None
    data: dict
    eol: str
    # key nodes of top-level pairs that define or use an anchor, found before
    # construction: constructing a merge (``<<: *b``) removes that pair from
    # the node, so it cannot be found afterwards
    anchored: frozenset = frozenset()

    def s(self, node: Node) -> int:
        return node.start_mark.index + self.span.start

    def e(self, node: Node) -> int:
        return node.end_mark.index + self.span.start

    @property
    def body(self) -> str:
        return self.text[self.span.body :]


def _why_no_frontmatter(text: str) -> str:
    result = split_frontmatter(text)
    if isinstance(result, (Malformed, Unterminated)):
        return describe(result)
    if isinstance(result, Unsupported):
        marker = "+++" if result.format == "toml" else "{"
        return (
            f"{result.format.upper()} frontmatter ({marker}) is not edited: "
            "Pyrite edits YAML frontmatter only"
        )
    return "the file has no YAML frontmatter"


def _parse(text: str, span: FrontmatterSpan) -> _Doc:
    """Compose once and construct the values from the same nodes."""
    if not (0 <= span.start <= span.end <= span.body <= len(text)):
        raise OperationRefusedError(f"the frontmatter span {span} does not fit the text")
    yaml_text = text[span.start : span.end]
    y = _yaml()
    try:
        root = y.compose(yaml_text)
    except YAMLError as e:
        raise OperationRefusedError(f"the frontmatter does not parse: {e}{_BODY_TOO}") from e
    if root is not None and not isinstance(root, MappingNode):
        raise OperationRefusedError(f"the frontmatter is not a mapping{_BODY_TOO}")
    if root is not None and root.flow_style:
        raise OperationRefusedError(
            f"the frontmatter is one flow-style mapping ({{...}}); not edited{_BODY_TOO}"
        )
    if root is not None and _has_complex_key(root, set()):
        raise OperationRefusedError(
            f"the frontmatter has a complex key (? [a, b] or ? {{a: 1}}); not edited{_BODY_TOO}"
        )
    if root is not None and _has_tagged_key(root, set(), y):
        raise OperationRefusedError(_TAGGED_KEY)
    duplicate = _duplicate_key(root, set()) if root is not None else None
    anchored = frozenset(
        id(k)
        for k, v in (root.value if root is not None else [])
        if _has_anchor(k, set()) or _has_anchor(v, set())
    )
    try:
        loaded = y.constructor.construct_document(root) if root is not None else {}
    except YAMLError as e:
        if duplicate is not None:
            raise OperationRefusedError(_DUPLICATE.format(duplicate)) from e
        raise OperationRefusedError(f"the frontmatter does not parse: {e}{_BODY_TOO}") from e
    # before the duplicate refusal: a tagged key beside its plain twin
    # (``!!str a: 1`` / ``a: 2``) is a duplicate to the node walk but, first,
    # a key Pyrite cannot name (#769)
    if _loaded_tagged_key(loaded, set()):
        raise OperationRefusedError(_TAGGED_KEY)
    if duplicate is not None:
        raise OperationRefusedError(_DUPLICATE.format(duplicate))
    eol = "\r\n" if text[max(0, span.start - 2) : span.start] == "\r\n" else "\n"
    return _Doc(text, span, root, _plain(loaded or {}), eol, anchored)


def _has_complex_key(node: Node, seen: set[int]) -> bool:
    """A mapping key that is itself a list or a mapping: Python cannot hold
    it as a dict key, and no path can name it."""
    if id(node) in seen:
        return False
    seen.add(id(node))
    if isinstance(node, MappingNode):
        return any(
            not isinstance(k, ScalarNode) or _has_complex_key(v, seen) for k, v in node.value
        )
    if isinstance(node, SequenceNode):
        return any(_has_complex_key(item, seen) for item in node.value)
    return False


# Every frontmatter-shape refusal also refuses ``ReplaceBody``: a body-only
# write on a file whose frontmatter Pyrite cannot model is refused (#760 may
# allow it), so each reason says so (#769).
_BODY_TOO = "; the body is not written either"
_DUPLICATE = "the frontmatter has a duplicate key {!r}; not edited" + _BODY_TOO

_TAGGED_KEY = (
    "the frontmatter has a tagged key (!foo a: 1, !!str a: 1) that Pyrite cannot model, "
    "so it edits nothing in this file, body included"
)


def _has_tagged_key(node: Node, seen: set[int], y: YAML) -> bool:
    """A mapping key with an explicit tag whose reading differs from the same
    key written plain (or quoted): ``!!float 1: a`` (ruamel: float 1.0; plain:
    int 1), ``!!int "1"``, ``!foo a``, ``!!binary aGk=``. ``!!str x`` reads as
    ``x`` at node level, so ``_loaded_tagged_key`` finds it."""
    if id(node) in seen:
        return False
    seen.add(id(node))
    if isinstance(node, MappingNode):
        for k, v in node.value:
            if isinstance(k, ScalarNode):
                implicit = (True, False) if k.style is None else (False, True)
                if k.tag != y.resolver.resolve(ScalarNode, k.value, implicit):
                    return True
            if _has_tagged_key(v, seen, y):
                return True
    elif isinstance(node, SequenceNode):
        return any(_has_tagged_key(item, seen, y) for item in node.value)
    return False


def _loaded_tagged_key(value: Any, seen: set[int]) -> bool:
    """What ruamel loaded holds a key it wraps as a ``TaggedScalar``. Neither
    ``_plain`` nor a path can handle one: it is not a string, and it is not
    hashable once ``_plain`` has made it a tuple (#749).

    Every container ruamel loads a key into: a ``Mapping`` (``CommentedMap``,
    ``!!omap``), a set (``CommentedSet``, ``collections.abc.Set``: its members
    are the keys), and the ``(key, value)`` tuples of a ``!!pairs`` list.
    A list or tuple is walked for what it holds."""
    if id(value) in seen:
        return False
    if isinstance(value, Mapping):
        seen.add(id(value))
        return any(
            type(k).__name__ == "TaggedScalar" or _loaded_tagged_key(v, seen)
            for k, v in value.items()
        )
    if isinstance(value, AbstractSet):
        seen.add(id(value))
        return any(type(m).__name__ == "TaggedScalar" for m in value)
    if isinstance(value, tuple):  # a !!pairs item: (key, value)
        seen.add(id(value))
        if not value:  # ruamel refuses an empty !!pairs item first; refuse, don't crash
            raise OperationRefusedError(
                f"the frontmatter holds a (key, value) item with no key; not edited{_BODY_TOO}"
            )
        key, *rest = value
        return type(key).__name__ == "TaggedScalar" or any(
            _loaded_tagged_key(v, seen) for v in rest
        )
    if isinstance(value, list):
        seen.add(id(value))
        return any(_loaded_tagged_key(v, seen) for v in value)
    return False


def _duplicate_key(node: Node, seen: set[int]) -> str | None:
    """The first key that appears twice in one mapping, by loaded value (as
    the loader compares them: ``true`` and ``1`` collide), or None."""
    if id(node) in seen:
        return None
    seen.add(id(node))
    if isinstance(node, MappingNode):
        keys: dict[Any, str] = {}
        for k, v in node.value:
            value = _node_key(k)
            if value is not _MISSING and not (isinstance(k, ScalarNode) and k.value == "<<"):
                try:
                    if value in keys:
                        return k.value
                    keys[value] = k.value
                except TypeError:
                    pass
            found = _duplicate_key(v, seen)
            if found is not None:
                return found
    elif isinstance(node, SequenceNode):
        for item in node.value:
            found = _duplicate_key(item, seen)
            if found is not None:
                return found
    return None


def _refuse_anchors(doc: _Doc, top: Any, op: Any) -> None:
    """Decision 6: a key that defines or uses an anchor is refused for change.

    An alias composes to the anchor's own node object, so checking the touched
    subtree for any node with an anchor catches the anchor, every alias to it
    and a merge key (``<<: *b``)."""
    if doc.root is None:
        return
    idx = _key_index(doc.root, top)
    if idx is None:
        return
    if id(doc.root.value[idx][0]) in doc.anchored:
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


def _skip_blanks(text: str, pos: int) -> int:
    while pos < len(text) and text[pos] in " \t":
        pos += 1
    return pos


def _col(doc: _Doc, node: Node) -> int:
    at = doc.s(node)
    return at - _line_start(doc.text, at)


def _is_empty(node: Node) -> bool:
    return isinstance(node, ScalarNode) and node.start_mark.index == node.end_mark.index


def _is_null(node: Node) -> bool:
    return isinstance(node, ScalarNode) and node.tag.endswith(":null")


def _is_block(node: Node) -> bool:
    return isinstance(node, MappingNode | SequenceNode) and not node.flow_style


def _is_block_scalar(node: Node) -> bool:
    return isinstance(node, ScalarNode) and node.style in ("|", ">")


def _header_end(doc: _Doc, node: ScalarNode) -> int:
    """Past a block scalar's indicators (``|``, ``>-``, ``|2+``)."""
    pos = doc.s(node) + 1
    while pos < len(doc.text) and doc.text[pos] in "+-0123456789":
        pos += 1
    return pos


def _header(doc: _Doc, node: ScalarNode) -> str:
    return doc.text[doc.s(node) : _header_end(doc, node)]


def _keeps(doc: _Doc, node: Node) -> bool:
    """A block scalar with keep chomping: its trailing blank lines are content."""
    return _is_block_scalar(node) and "+" in _header(doc, node)


def _colon_end(doc: _Doc, key: Node) -> int:
    return doc.text.index(":", doc.e(key)) + 1


def _end(doc: _Doc, node: Node) -> int:
    """For the edits: just past the last character of ``node``'s own text.
    Not ``end_mark``: see the module docstring. A keep (``|+``) scalar ends
    after its blank lines, which are its content."""
    if isinstance(node, ScalarNode):
        start, end = doc.s(node), doc.e(node)
        if _is_block_scalar(node) and not _keeps(doc, node):
            # back over the blank lines after it; a literal's trailing spaces
            # on its last line are content and stay inside
            while end > start + 1:
                line = _line_start(doc.text, end - 1)
                if line <= start or doc.text[line:end].strip():
                    break
                end = line
            end = _content_end_of_line(doc.text, end - 1)
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
        pos = _skip_blanks(doc.text, _colon_end(doc, key))
        while pos < len(doc.text) and doc.text[pos] not in " \t\r\n,]}":
            pos += 1
        return pos
    return _end(doc, value)


def _own_end(doc: _Doc, end: int) -> int:
    """Past the line ending of the line holding the character before ``end``."""
    return _line_end(doc.text, end - 1)


def _dash(doc: _Doc, seq: SequenceNode, i: int) -> int:
    """The position of item ``i``'s ``-``: the first line at or after the end of
    the previous item whose first non-space character is a ``-``."""
    text = doc.text
    pos = doc.s(seq) if i == 0 else _own_end(doc, _end(doc, seq.value[i - 1]))
    pos = _line_start(text, pos) if i == 0 else pos
    while pos < doc.s(seq.value[i]):
        first = _skip_blanks(text, pos)
        if text[first : first + 1] == "-":
            return first
        pos = _line_end(text, pos)
    raise OperationRefusedError("cannot find this list item's dash")


def _item_range(doc: _Doc, seq: SequenceNode, i: int) -> tuple[int, int]:
    """A block sequence item's own lines, from its dash line."""
    item = seq.value[i]
    dash = _dash(doc, seq, i)
    if (
        isinstance(item, SequenceNode)
        and not item.flow_style
        and doc.s(item) < _line_end(doc.text, dash)
    ):
        raise OperationRefusedError("a list written directly in a list item (- - x) is not edited")
    return _line_start(doc.text, dash), _own_end(doc, _end(doc, item))


def _item_cols(doc: _Doc, seq: SequenceNode, i: int) -> tuple[int, int]:
    dash = _dash(doc, seq, i)
    dash_col = dash - _line_start(doc.text, dash)
    return dash_col, max(_col(doc, seq.value[i]), dash_col + 2)


# --- paths ------------------------------------------------------------------


def _segments(path: Any, op: Any = None) -> list[str | int]:
    """A path as segments: strings name keys, ints index lists. Refused when
    it cannot be read: an empty path or segment (``a..b``), a bracket that is
    not an index, a segment that is neither."""
    if isinstance(path, tuple | list):
        segs = list(path)
        for seg in segs:
            if isinstance(seg, bool) or not isinstance(seg, str | int):
                raise OperationRefusedError(
                    f"a path segment is a string or an int, not {seg!r}", op
                )
    elif isinstance(path, str):
        segs = []
        for part in path.split("."):
            name, bracket, rest = part.partition("[")
            if not name and not (bracket and segs):
                raise OperationRefusedError(f"the path {path!r} has an empty segment", op)
            if "]" in name:
                raise OperationRefusedError(f"cannot read the path {path!r}", op)
            if name:
                segs.append(name)
            rest = bracket + rest
            while rest:
                index, closed, rest = rest[1:].partition("]")
                if not rest.startswith("[") and rest:
                    raise OperationRefusedError(f"cannot read the path {path!r}", op)
                try:
                    number = int(index)
                except ValueError as e:
                    raise OperationRefusedError(f"cannot read the path {path!r}", op) from e
                if not closed:
                    raise OperationRefusedError(f"cannot read the path {path!r}", op)
                segs.append(number)
    else:
        raise OperationRefusedError(f"a path is a string or a tuple, not {type(path).__name__}", op)
    if not segs and not isinstance(op, AddSubkey):
        raise OperationRefusedError(f"the path {path!r} is empty", op)
    if segs and isinstance(segs[0], int):
        raise OperationRefusedError(f"the path {path!r} starts with a list index", op)
    return segs


def _show(segs: list) -> str:
    out = ""
    for seg in segs:
        out += f"[{seg}]" if isinstance(seg, int) else (f".{seg}" if out else str(seg))
    return out or "(the frontmatter)"


def _top_key(op: Any) -> Any:
    if isinstance(op, ReplaceBody):
        return None
    segs = _segments(op.path, op)
    if not segs and isinstance(op, AddSubkey):
        return op.key
    return segs[0]


def _key_index(node: MappingNode, seg: Any) -> int | None:
    hits = [i for i, (k, _) in enumerate(node.value) if _key_matches_node(seg, k)]
    if len(hits) > 1:
        raise OperationRefusedError(f"{seg!r} names two keys here; edit it by hand")
    return hits[0] if hits else None


def _key_matches_node(seg: Any, key: Node) -> bool:
    value = _node_key(key)
    return value is not _MISSING and _key_matches(seg, value)


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
            data = data[_lookup(data, seg)]
        else:
            data = data[seg]
    return data


# --- the model: what the operation means on the parsed value ------------------


def _model_apply(data: dict, op: Any) -> dict:
    """The operation applied to the parsed value: what the post-check expects.
    Copies only the touched top-level value."""
    if isinstance(op, ReplaceBody):
        return data
    segs = _segments(op.path, op)
    if isinstance(op, AddSubkey):
        segs = [*segs, op.key]
    result = dict(data)
    top = _lookup(result, segs[0])
    if top is not _MISSING:
        # the one copy: every later operation in the call edits this, so a
        # value shared within it ([d, d], an alias) must not stay shared
        result[top] = _unshare(result[top])
    where = _show(segs)
    parent: Any = result
    for depth, seg in enumerate(segs[:-1]):
        if isinstance(parent, Mapping):
            found = _lookup(parent, seg)
            if found is _MISSING or parent[found] is None:
                if isinstance(op, Unset | Remove):
                    return data
                if isinstance(segs[depth + 1], int):
                    raise OperationRefusedError(
                        f"{where}: there is no list at {_show(segs[: depth + 1])}", op
                    )
                found = seg if found is _MISSING else found
                parent[found] = {}
            parent = parent[found]
        elif (
            isinstance(parent, list) and isinstance(seg, int) and -len(parent) <= seg < len(parent)
        ):
            parent = parent[seg]
        else:
            if isinstance(op, Unset | Remove):
                return data
            if isinstance(parent, list) and isinstance(seg, int):
                raise OperationRefusedError(
                    f"{where}: index {seg} is out of range ({_show(segs[:depth])} has {len(parent)} items)",
                    op,
                )
            if isinstance(parent, list):
                raise OperationRefusedError(
                    f"{where}: {_show(segs[:depth])} is a list; name an item by index", op
                )
            raise OperationRefusedError(
                f"{where}: {_show(segs[:depth])} is not a mapping or list", op
            )
    last = segs[-1]
    if isinstance(parent, list):
        if not isinstance(last, int):
            if isinstance(op, Unset | Remove):
                return data
            raise OperationRefusedError(
                f"{where}: {_show(segs[:-1])} is a list; name an item by index", op
            )
        if not -len(parent) <= last < len(parent):
            if isinstance(op, Unset | Remove):
                return data
            raise OperationRefusedError(
                f"{where}: index {last} is out of range (the list has {len(parent)} items)", op
            )
        if isinstance(op, Unset):
            del parent[last]
            return result
        if isinstance(op, Set):
            if values_equal(parent[last], op.value):
                return data
            parent[last] = op.value
            return result
        target, holder, key = parent[last], parent, last
    elif isinstance(parent, Mapping):
        if isinstance(last, int):
            if isinstance(op, Unset | Remove):
                return data
            raise OperationRefusedError(f"{where}: {_show(segs[:-1])} is a mapping, not a list", op)
        key = _lookup(parent, last)
        target, holder = (None if key is _MISSING else parent[key]), parent
    else:
        if isinstance(op, Unset | Remove):
            return data
        raise OperationRefusedError(f"{where}: {_show(segs[:-1])} is not a mapping or list", op)
    if isinstance(op, Set | AddSubkey):
        if key is not _MISSING and values_equal(target, op.value):
            return data
        if isinstance(op, AddSubkey) and key is not _MISSING:
            raise OperationRefusedError(
                f"{_show(segs[:-1])} already has {op.key!r}; use set to change it", op
            )
        holder[last if key is _MISSING else key] = op.value
    elif isinstance(op, Unset):
        if key is _MISSING:
            return data
        del holder[key]
    elif isinstance(op, Append):
        if key is _MISSING or target is None:
            holder[last if key is _MISSING else key] = [op.value]
        elif isinstance(target, list):
            target.append(op.value)
        else:
            raise OperationRefusedError(f"{where} is not a list; cannot append to it", op)
    elif isinstance(op, Remove):
        if target is None:
            return data
        if not isinstance(target, list):
            raise OperationRefusedError(f"{where} is not a list; cannot remove from it", op)
        for i, item in enumerate(target):
            if values_equal(item, op.value):
                del target[i]
                return result
        return data
    return result


def _unshare(value: Any) -> Any:
    """A copy that shares no list or mapping with anything: ``deepcopy``
    keeps the sharing in ``[d, d]`` (and an alias's), so a change to one item
    would change both in the model, and not in the file."""
    if isinstance(value, Mapping):
        return {k: _unshare(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_unshare(v) for v in value]
    return value


def _expected_body(doc: _Doc, op: Any) -> str:
    if isinstance(op, ReplaceBody):
        written = _written_body(doc, op.body)
        if written is not None:
            return written
    return doc.body


# --- a batch: operations on different top-level keys, one parse ---------------


def _top_identity(doc: _Doc, op: Any) -> Any:
    """Which top-level value ``op`` touches: the body, a key of the file (by
    position), or a key it does not have yet (by name)."""
    if isinstance(op, ReplaceBody):
        return "body"
    top = _top_key(op)
    idx = _key_index(doc.root, top) if doc.root is not None else None
    return ("key", idx) if idx is not None else ("new", top)


def _batch(doc: _Doc, ops: list, start: int) -> list:
    """The longest run from ``start`` whose operations touch different
    top-level values: their edits cannot interact, so they are spliced
    against one parse and checked once (500 sets on 500 keys: one parse, not
    500; #732 round 1)."""
    seen: set = set()
    batch = []
    for op in ops[start:]:
        identity = _top_identity(doc, op)
        if identity in seen:
            break
        seen.add(identity)
        batch.append(op)
    return batch


def _apply_batch(doc: _Doc, batch: list) -> tuple[_Doc, list[str]] | None:
    """The batch spliced at once and checked as one patch: the bytes outside
    every operation's spans, the values, the body. None when any step fails,
    and the caller goes one operation at a time (where a failed narrow edit
    can widen)."""
    expected = doc.data
    expected_body = doc.body
    outcomes: list[str] = []
    edits: list = []
    spans: list = []
    try:
        for op in batch:
            top = _top_key(op)
            if top is not None:
                _refuse_anchors(doc, top, op)
            after = _model_apply(expected, op)
            body = _expected_body(doc, op)
            if after is expected and body == expected_body:
                outcomes.append("unchanged")
                continue
            expected, expected_body = after, body
            outcomes.append("changed")
            spans += _named_spans(doc, op)
            edits += _narrow_edits(doc, op)
    except OperationRefusedError:
        return None
    if not edits:
        return (doc, outcomes) if "changed" not in outcomes else None
    new_doc, _ = _try(doc, edits, spans, expected, expected_body)
    return None if new_doc is None else (new_doc, outcomes)


# --- one operation: narrow, verify, widen -------------------------------------


def _apply_one(doc: _Doc, op: Any) -> tuple[_Doc, str, bool]:
    top = _top_key(op)
    if top is not None:
        _refuse_anchors(doc, top, op)
    expected = _model_apply(doc.data, op)
    expected_body = _expected_body(doc, op)
    if expected is doc.data and expected_body == doc.body:
        return doc, "unchanged", False  # decision 3: asked for what is there
    allowed = _named_spans(doc, op)
    edits = _narrow_edits(doc, op)
    problem = "the narrow edit changed nothing"
    if edits:
        after, problem = _try(doc, edits, allowed, expected, expected_body)
        if after is not None:
            return after, "changed", False
    edits = _widen_edits(doc, op, expected)
    if edits is None:
        raise OperationRefusedError(f"{problem}, and this operation cannot be widened", op)
    # decision 2: a widen owns the whole top-level value it rebuilds
    wide = _slot_spans(doc, *_walk(doc, [top]))
    after, second = _try(doc, edits, wide, expected, expected_body)
    if after is None:
        raise OperationRefusedError(f"{problem}; widened item-wise, {second}", op)
    return after, "changed", True


def _try(doc, edits, allowed, expected, expected_body) -> tuple[_Doc | None, str | None]:
    """Splice, parse and check; the new doc, or None and why not."""
    try:
        text, span = _splice(doc, edits)
    except OperationRefusedError as e:
        return None, e.reason
    problem = _outside_changed(doc.text, text, allowed)
    if problem:
        return None, problem
    try:
        after = _parse(text, span)
    except OperationRefusedError as e:
        return None, f"the result does not parse ({e.reason})"
    if not _same(after.data, expected):
        return None, "the result does not parse to the operation applied"
    if after.body != expected_body:
        return None, "the body is not what was asked"
    return after, None


_SCALARS = (str, int, float, bool, date, type(None))


def _check_op(op: Any) -> None:
    if not isinstance(op, Set | Append | Remove | Unset | ReplaceBody | AddSubkey):
        raise OperationRefusedError(f"not an operation: {op!r}", op)
    if isinstance(op, ReplaceBody):
        if not isinstance(op.body, str):
            raise OperationRefusedError("a body is a string", op)
        return
    _segments(op.path, op)
    if isinstance(op, AddSubkey) and (not isinstance(op.key, str) or not op.key):
        raise OperationRefusedError(f"a key to add is a non-empty string, not {op.key!r}", op)
    if hasattr(op, "value"):
        _check_value(op.value, op)


def _check_value(value: Any, op: Any) -> None:
    if isinstance(value, Mapping):
        for k, v in value.items():
            if isinstance(k, Mapping | list | tuple):
                raise OperationRefusedError(f"cannot write a {type(k).__name__} as a key", op)
            _check_value(k, op)
            _check_value(v, op)
    elif isinstance(value, list | tuple):
        for v in value:
            _check_value(v, op)
    elif not isinstance(value, _SCALARS):
        raise OperationRefusedError(f"cannot write a {type(value).__name__} value", op)


def _splice(doc: _Doc, edits: list[tuple[int, int, str]]) -> tuple[str, FrontmatterSpan]:
    """Apply (start, end, replacement) edits given in ``doc.text``'s offsets.

    At one offset, a range is replaced before an insertion is made, and
    insertions keep the order they were listed in."""
    order = sorted(
        enumerate(edits),
        key=lambda ie: (ie[1][0], 1 if ie[1][1] > ie[1][0] else 0, ie[0]),
        reverse=True,
    )
    pieces: list[str] = []
    bound = len(doc.text)
    delta = 0
    for _, (start, end, new) in order:
        if end > bound or start > end:
            raise OperationRefusedError("internal: overlapping edits")
        pieces.append(doc.text[end:bound])
        pieces.append(new)
        bound = start
        if start >= doc.span.start and end <= doc.span.end:
            delta += len(new) - (end - start)
    pieces.append(doc.text[:bound])
    text = "".join(reversed(pieces))
    span = FrontmatterSpan(doc.span.start, doc.span.end + delta, doc.span.body + delta)
    return text, span


# --- the post-check: the bytes outside the named spans -------------------------


def _outside_changed(before: str, after: str, spans: list[tuple[int, int]]) -> str | None:
    """None when ``after`` is ``before`` with only the bytes inside ``spans``
    changed: the pieces between the spans appear in ``after``, in order, the
    first as a prefix and the last as a suffix."""
    merged: list[list[int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    pieces, at = [], 0
    for start, end in merged:
        pieces.append(before[at:start])
        at = end
    pieces.append(before[at:])
    if len(pieces) == 1:
        return None if after == before else "bytes changed with no span to change"
    first, *middle, last = pieces
    if not after.startswith(first):
        return f"bytes before the edited span changed (near offset {len(first)})"
    pos = len(first)
    for piece in middle:
        found = after.find(piece, pos)
        if found < 0:
            return f"bytes between edited spans changed: {piece[:60]!r}"
        pos = found + len(piece)
    if len(after) - len(last) < pos or not after.endswith(last):
        return f"bytes after the edited span changed: {last[:60]!r}"
    return None


def _check_last_line_end(doc: _Doc, node: Node) -> int:
    """For the check: past the line ending of ``node``'s last content line.
    Its own rule, not ``_end``'s: ``end_mark`` walked back over blank lines
    and, for a collection, comment-only lines; a keep (``|+``) scalar at the
    very end owns its blank lines."""
    text = doc.text
    if isinstance(node, ScalarNode):
        end = doc.e(node)
        if _is_block_scalar(node):
            if _keeps(doc, node):
                return end
            lines = text[doc.s(node) : end].splitlines(keepends=True)
            while len(lines) > 1 and not lines[-1].strip():
                end -= len(lines.pop())
        return _line_end(text, end - 1)
    if node.flow_style:
        return _line_end(text, doc.e(node) - 1)
    last = node
    while isinstance(last, MappingNode | SequenceNode) and not last.flow_style and last.value:
        last = last.value[-1][1] if isinstance(last, MappingNode) else last.value[-1]
    pos = doc.e(node)
    while pos > doc.s(node):
        start = _line_start(text, pos - 1)
        line = text[start:pos].strip()
        if line and not line.startswith("#"):
            break
        pos = start
    if _keeps(doc, last):  # its blank lines are content
        pos = max(pos, doc.e(last))
    return pos


def _value_spans(doc: _Doc, container: Node | None, idx: int, node: Node) -> list[tuple[int, int]]:
    """The span a value owns (see the table in the module docstring)."""
    text = doc.text
    if container is None:  # the root mapping: new keys go at the end
        return [(doc.span.end, doc.span.end)]
    if container.flow_style:
        return [(doc.s(node), doc.e(node))]
    in_map = isinstance(container, MappingNode)
    start = _colon_end(doc, container.value[idx][0]) if in_map else doc.s(node)
    if _is_empty(node):
        return [
            (start, start),
            (_content_end_of_line(text, start), _content_end_of_line(text, start)),
        ]
    if _is_block(node):
        if in_map:
            return [
                (start, start),
                (_content_end_of_line(text, start), _check_last_line_end(doc, node)),
            ]
        return [(start, _check_last_line_end(doc, node))]
    if _is_block_scalar(node):
        last = _check_last_line_end(doc, node)
        last = _content_end_of_line(text, last - 1) if last > 0 else last
        return [(start, _header_end(doc, node)), (_content_end_of_line(text, doc.s(node)), last)]
    end = _skip_blanks(text, doc.e(node))
    line_end = _content_end_of_line(text, doc.e(node))
    return [(start, end), (line_end, line_end)]


def _slot_spans(doc: _Doc, trail: list, depth: int) -> list[tuple[int, int]]:
    """The value spans of the node ``trail`` reaches at ``depth`` (0: root)."""
    if depth == 0 or doc.root is None:
        return _value_spans(doc, None, 0, doc.root)
    container, idx, node = trail[depth - 1]
    return _value_spans(doc, container, idx, node)


def _named_spans(doc: _Doc, op: Any) -> list[tuple[int, int]]:
    """Where ``op`` may change bytes, by the span rule alone."""
    if isinstance(op, ReplaceBody):
        return [(doc.span.body, len(doc.text))]
    segs = _segments(op.path, op)
    trail, found = _walk(doc, segs)
    if isinstance(op, Unset):
        return _unset_spans(doc, trail) if found == len(segs) else []
    return _slot_spans(doc, trail, found)


def _unset_spans(doc: _Doc, trail: list) -> list[tuple[int, int]]:
    text = doc.text
    container, idx, node = trail[-1]
    parent_depth = len(trail) - 1
    if container.flow_style:
        items = container.value
        start = doc.s(items[idx][0] if isinstance(container, MappingNode) else items[idx])
        end = doc.e(items[idx][1] if isinstance(container, MappingNode) else items[idx])
        if idx + 1 < len(items):
            nxt = items[idx + 1]
            return [(start, doc.s(nxt[0] if isinstance(container, MappingNode) else nxt))]
        if idx > 0:
            prev = items[idx - 1]
            return [(doc.e(prev[1] if isinstance(container, MappingNode) else prev), end)]
        return [(start, end)]
    if len(container.value) == 1 and container is not doc.root:
        return _slot_spans(doc, trail, parent_depth)
    if isinstance(container, SequenceNode):
        dash_line = _line_start(text, doc.s(node))
        while text[_skip_blanks(text, dash_line) : _skip_blanks(text, dash_line) + 1] != "-":
            dash_line = _line_start(text, dash_line - 1)
        return [(dash_line, _check_last_line_end(doc, node))]
    key = container.value[idx][0]
    if _is_empty(node):
        last = _line_end(text, _colon_end(doc, key))
    elif doc.s(node) < doc.e(key):  # an alias
        last = _line_end(text, _colon_end(doc, key))
    else:
        last = _check_last_line_end(doc, node)
    line = _line_start(text, doc.s(key))
    if not text[line : doc.s(key)].strip():
        return [(line, last)]
    # the first pair on a list item's dash line
    end = last
    if idx + 1 < len(container.value):
        nxt = doc.s(container.value[idx + 1][0])
        if not text[last:nxt].strip(" \t"):
            end = nxt
    return [(doc.s(key), end)]


def _final_check(current: _Doc, expected: dict) -> str | None:
    """Once per call, a second line behind the per-operation check: the
    values and the body through the reader's own split (``load_frontmatter``),
    compared here without ``_same``. The bytes were checked per operation,
    each against the text it applied to."""
    from ..utils.frontmatter import load_frontmatter

    try:
        parsed = load_frontmatter(current.text)
    except Exception as e:  # FrontmatterError: the edit broke the YAML
        return f"the reader cannot parse the result ({e})"
    if parsed is None:
        return "the reader finds no frontmatter in the result"
    meta, body = parsed
    got = _plain(meta)
    if not values_equal(got, expected) or not _same_keys(got, expected):
        return "the reader does not read the operations applied"
    if body != current.body.strip():
        return "the reader's body is not what was asked"
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
            try:
                seq_offset = _item_cols(doc, v, 0)[0] - _col(doc, k)
            except OperationRefusedError:
                continue
        if map_indent is not None and seq_offset is not None:
            break
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
    segs = _segments(op.path, op)
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
        raise OperationRefusedError(
            f"{_show(segs)}: no list item at {_show(segs[: found + 1])}", op
        )
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
    raise OperationRefusedError(f"{_show(segs)}: {_show(segs[:found])} is not a mapping", op)


def _add_subkey(doc: _Doc, segs: list, op: AddSubkey, conv: _Conv) -> list:
    if not segs:
        return _set_path(doc, [op.key], op.value, conv, op)
    trail, found = _walk(doc, segs)
    if found < len(segs) or not isinstance(trail[-1][2], MappingNode):
        raise OperationRefusedError(f"no mapping at {_show(segs)} to add {op.key!r} to", op)
    current = _data_at(doc.data, segs)
    if _lookup(current, op.key) is not _MISSING:
        return []  # equal: the model said unchanged; different: the model refused
    return _add_pairs(doc, trail[-1][2], [(op.key, op.value)], conv)


def _append(doc: _Doc, segs: list, op: Append, conv: _Conv) -> list:
    trail, found = _walk(doc, segs)
    if found < len(segs):
        return _set_path(doc, segs, [op.value], conv, op)
    container, idx, node = trail[-1]
    if _is_null(node):
        return _set_node(doc, container, idx, node, None, [op.value], conv)
    if not isinstance(node, SequenceNode):
        raise OperationRefusedError(f"{_show(segs)} is not a list; cannot append to it", op)
    items = node.value
    if node.flow_style:
        hint = _style(items[-1]) if items else None
        new = _emit_flow(op.value, hint)
        if not items:
            return [(doc.s(node) + 1, doc.s(node) + 1, new)]
        end = _end(doc, items[-1])
        return [(end, end, ", " + new)]
    last = len(items) - 1
    dash_col, content_col = _item_cols(doc, node, last)
    lines = _emit_item_lines(op.value, dash_col, content_col, _style(items[last]), conv)
    pos = _item_range(doc, node, last)[1]
    return [(pos, pos, "".join(line + doc.eol for line in lines))]


def _remove(doc: _Doc, segs: list, op: Remove, conv: _Conv) -> list:
    trail, found = _walk(doc, segs)
    if found < len(segs):
        return []
    container, idx, node = trail[-1]
    if not isinstance(node, SequenceNode):
        return []  # the model refused a non-list or found nothing to remove
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
    start, end = _item_range(doc, seq, i)
    return [(start, end, "")]


def _unset(doc: _Doc, segs: list, op: Unset, conv: _Conv) -> list:
    trail, found = _walk(doc, segs)
    if found < len(segs):
        return []
    container, idx, _ = trail[-1]
    if isinstance(container, SequenceNode):
        parent, pidx, _ = trail[-2]
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
    """Replace an inline scalar's text. A trailing comment keeps its bytes; it
    keeps its column when the new value fits before it, and otherwise its gap."""
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


def _block_body_range(doc: _Doc, node: ScalarNode) -> tuple[int, int]:
    """A block scalar's content: from the end of its header line (the header's
    line ending included) to the end of its last content line (a keep
    scalar's last blank line), before that line's ending."""
    text = doc.text
    start = _content_end_of_line(text, doc.s(node))
    end = _end(doc, node)
    if _keeps(doc, node):
        end = _content_end_of_line(text, end - 1) if end > start else start
    return start, max(start, end)


def _replace_scalar(doc: _Doc, node: ScalarNode, new: Any, flow: bool) -> list:
    if not _is_block_scalar(node):
        return _scalar_edit(doc, node, _emit_scalar(new, node.style, node.tag, flow), flow)
    body_start, body_end = _block_body_range(doc, node)
    header = (doc.s(node), _header_end(doc, node))
    if isinstance(new, str):
        indent, parent = _block_indents(doc, node)
        explicit = any(ch.isdigit() for ch in _header(doc, node))
        for style in (node.style, "|"):
            block = _emit_block_scalar(
                new, style, indent, parent, explicit, doc.eol, _keeps(doc, node)
            )
            if block is not None:
                head, body = block
                return [(header[0], header[1], head), (body_start, body_end, body)]
    return [
        (header[0], header[1], _emit_scalar(new, None, None, flow)),
        (body_start, body_end, ""),
    ]


def _block_indents(doc: _Doc, node: ScalarNode) -> tuple[int, int]:
    """(content indent, parent indent) of a block scalar."""
    text = doc.text
    line = _line_start(text, doc.s(node))
    head = text[line : doc.s(node)]
    parent = len(head) - len(head.lstrip(" "))
    if head.strip().startswith("-"):  # an item: the content sits past the dash
        parent = head.index("-")
    digits = "".join(ch for ch in _header(doc, node) if ch.isdigit())
    if digits:
        return parent + int(digits), parent
    pos = _line_end(text, doc.s(node))
    end = doc.e(node)
    while pos < end:
        line_end = _line_end(text, pos)
        content = text[pos:line_end]
        if content.strip():
            return len(content) - len(content.lstrip(" ")), parent
        pos = line_end
    return parent + 2, parent


def _edit_map(doc, container, idx, node: MappingNode, cur: Mapping, new: Mapping, conv) -> list:
    """Set a mapping to a new mapping key by key: the file's keys the new
    value keeps are set in place, the others are deleted, new keys are added
    at the end. A key of the new value names the file's key it equals with
    the same type (a string ``"true"`` is not the bool ``true``)."""
    edits: list = []
    deleted: set[int] = set()
    kept: dict = {}
    for i, (k, v) in enumerate(node.value):
        key = _node_key(k)
        hit = _MISSING if key is _MISSING else _exact(new, key)
        if hit is _MISSING:
            deleted.add(i)
            continue
        kept[hit] = None
        edits += _set_node(doc, node, i, v, cur[_exact(cur, key)], new[hit], conv)
    if len(deleted) == len(node.value):
        if node.flow_style:
            return [(doc.s(node), doc.e(node), _emit_flow(new, None))]
        return _replace_fresh(doc, container, idx, node, new, conv)
    if deleted:
        if node.flow_style:
            edits += _flow_delete(doc, node.value, sorted(deleted), lambda d, p: _pair_end(d, *p))
        else:
            edits += _delete_pairs(doc, node, deleted)
    # a new key is one the file does not have, compared by value under the
    # key rule; never by identity (round 2: only one-character strings are
    # interned, so ``id`` made every longer key look new and duplicated it)
    added = [(k, v) for k, v in new.items() if _exact(kept, k) is _MISSING]
    if added:
        edits += _add_pairs(doc, node, added, conv)
    return edits


def _delete_pairs(doc: _Doc, node: MappingNode, deleted: set[int]) -> list:
    """Each deleted pair's own lines. A first pair that shares its line with a
    list dash (``- target: x``) is cut from its key to the end of its last line,
    and through the next key's indentation when only spaces lie between, so
    that a comment line between it and the next key stays."""
    text = doc.text
    ranges = []
    pairs = node.value
    for i in sorted(deleted):
        k, v = pairs[i]
        line = _line_start(text, doc.s(k))
        end = _own_end(doc, _pair_end(doc, k, v))
        if text[line : doc.s(k)].strip():
            start = doc.s(k)
            if i + 1 < len(pairs) and not text[end : doc.s(pairs[i + 1][0])].strip(" \t"):
                end = doc.s(pairs[i + 1][0])
        else:
            start = line
        ranges.append([start, end])
    merged: list[list[int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end, "") for start, end in merged]


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
    kept byte for byte (an LCS under decision 3 equality, after the common
    head and tail); paired items of the same kind are set in place; the rest
    are deleted or inserted."""
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
        start, end = _item_range(doc, node, i)
        edits.append((start, end, ""))
    for anchor, js in inserts:
        ref = first_kept if anchor is None else anchor
        start, end = _item_range(doc, node, ref)
        dash_col, content_col = _item_cols(doc, node, ref)
        lines = [
            line
            for j in js
            for line in _emit_item_lines(new[j], dash_col, content_col, _style(items[ref]), conv)
        ]
        pos = start if anchor is None else end
        edits.append((pos, pos, "".join(line + doc.eol for line in lines)))
    return edits


def _plan(old: list, new: list):
    """(replaces {old: new}, deletes [old], inserts [(anchor old or None, [new])],
    first kept old index), or None when no old item is kept."""
    n, m = len(old), len(new)
    head = 0
    while head < min(n, m) and values_equal(old[head], new[head]):
        head += 1
    tail = 0
    while tail < min(n, m) - head and values_equal(old[n - 1 - tail], new[m - 1 - tail]):
        tail += 1
    mid_old, mid_new = old[head : n - tail], new[head : m - tail]
    matches = [(i, i) for i in range(head)]
    matches += [(head + i, head + j) for i, j in _lcs(mid_old, mid_new)]
    matches += [(n - tail + i, m - tail + i) for i in range(tail)]
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


def _lcs(old: list, new: list) -> list[tuple[int, int]]:
    n, m = len(old), len(new)
    if not n or not m:
        return []
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
    return matches


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
    becomes empty) afresh at the node's place. Only the value's own bytes
    change: its key line, and a comment on it, stay."""
    text, eol = doc.text, doc.eol
    if container.flow_style:
        return [(doc.s(node), _end(doc, node), _emit_flow(new, None))]
    inline = _is_inline(new)
    old_block = _is_block(node)
    if isinstance(container, MappingNode):
        key = container.value[idx][0]
        colon = _colon_end(doc, key)
        key_line_end = _content_end_of_line(text, colon)
        step = conv.map_indent if isinstance(new, Mapping) else conv.seq_offset
        if _is_block_scalar(node):
            body_start, body_end = _block_body_range(doc, node)
            head = (doc.s(node), _header_end(doc, node))
            if inline:
                return [(head[0], head[1], _emit_inline(new, None)), (body_start, body_end, "")]
            lines = _emit_block(new, _col(doc, key) + step, conv)
            return [(colon, head[1], ""), (body_start, body_end, eol + eol.join(lines))]
        if inline:
            value = _emit_inline(new, _style(node))
            if _is_empty(node):
                return [(colon, colon, " " + value)]
            if old_block:
                # the new value goes after the colon; the key line, and a
                # comment on it, stay; the old lines go
                return [
                    (colon, colon, " " + value),
                    (_line_end(text, colon), _own_end(doc, _end(doc, node)), ""),
                ]
            return _scalar_edit(doc, node, value, False)
        lines = _emit_block(new, _col(doc, key) + step, conv)
        if old_block:
            first = _line_start(text, doc.s(node))
            return [(first, _own_end(doc, _end(doc, node)), "".join(x + eol for x in lines))]
        if _is_empty(node):
            return [(key_line_end, key_line_end, eol + eol.join(lines))]
        end = _end(doc, node)
        at = _content_end_of_line(text, end)
        return [(colon, end, ""), (at, at, eol + eol.join(lines))]
    # an item of a block sequence
    col = _col(doc, node)
    if _is_block_scalar(node):
        body_start, body_end = _block_body_range(doc, node)
        head = (doc.s(node), _header_end(doc, node))
        if inline:
            return [(head[0], head[1], _emit_inline(new, None)), (body_start, body_end, "")]
        lines = _emit_block(new, col, conv)
        first = lines[0][col:] + "".join(eol + x for x in lines[1:])
        return [(head[0], head[1], first), (body_start, body_end, "")]
    end = _content_end_of_line(text, _end(doc, node) - 1) if old_block else _end(doc, node)
    if inline:
        return [(doc.s(node), end, _emit_inline(new, _style(node)))]
    lines = _emit_block(new, col, conv)
    return [(doc.s(node), end, lines[0][col:] + "".join(eol + x for x in lines[1:]))]


# --- the widen (decision 2): rebuild one top-level key item-wise ---------------


def _widen_edits(doc: _Doc, op: Any, expected: dict) -> list | None:
    """Rebuild the touched top-level value, copying every unchanged item's
    bytes from the file and emitting only what changed. None when there is no
    item-wise form (a new key, an unset key, a scalar, the body)."""
    top = _top_key(op)
    if top is None or doc.root is None:
        return None
    idx = _key_index(doc.root, top)
    new_key = _lookup(expected, top)
    old_key = _lookup(doc.data, top)
    if idx is None or new_key is _MISSING or old_key is _MISSING:
        return None
    _, node = doc.root.value[idx]
    if not isinstance(node, MappingNode | SequenceNode):
        return None
    old, new = doc.data[old_key], expected[new_key]
    conv = _conventions(doc)
    text = doc.text
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
    if isinstance(node, SequenceNode) and isinstance(new, list):
        return _widen_seq(doc, node, old, new, conv)
    if isinstance(node, MappingNode) and isinstance(new, Mapping):
        return _widen_map(doc, node, old, new, conv)
    return None


def _widen_map(doc: _Doc, node: MappingNode, old: Mapping, new: Mapping, conv) -> list:
    """The mapping rebuilt in the file's order: each kept pair's bytes, and
    the lines between pairs, copied; a changed pair emitted; new keys last."""
    text, eol = doc.text, doc.eol
    col = _col(doc, node.value[0][0])
    region_start = _line_start(text, doc.s(node.value[0][0]))
    chunks: list[str] = []
    kept: dict = {}
    prev = region_start
    for k, v in node.value:
        start = _line_start(text, doc.s(k))
        end = _own_end(doc, _pair_end(doc, k, v))
        chunks.append(text[prev:start])  # the lines between pairs stay
        prev = end
        key = _node_key(k)
        hit = _MISSING if key is _MISSING else _exact(new, key)
        if hit is _MISSING:
            continue  # deleted
        kept[hit] = None
        if values_equal(old[_exact(old, key)], new[hit]):
            chunks.append(text[start:end])
        else:
            chunks.append("".join(x + eol for x in _emit_pair_lines(hit, new[hit], col, conv)))
    for k, v in new.items():
        if _exact(kept, k) is _MISSING:
            chunks.append("".join(x + eol for x in _emit_pair_lines(k, v, col, conv)))
    return [(region_start, prev, "".join(chunks))]


def _widen_seq(doc: _Doc, node: SequenceNode, old: list, new: list, conv) -> list:
    """The list rebuilt in the file's order: each kept item's bytes, and the
    lines between items, copied; a changed or new item emitted where the
    alignment of old and new (``_plan``) puts it."""
    text, eol = doc.text, doc.eol
    items = node.value
    region_start = _item_range(doc, node, 0)[0]
    dash_col, content_col = _item_cols(doc, node, 0)

    def emit(values: list) -> str:
        return "".join(
            x + eol for v in values for x in _emit_item_lines(v, dash_col, content_col, None, conv)
        )

    plan = _plan(old, new)
    if plan is None:
        end = _item_range(doc, node, len(items) - 1)[1]
        return [(region_start, end, emit(new))]
    replaces, deletes, inserts, first_kept = plan
    after = dict(inserts)
    chunks: list[str] = []
    prev = region_start
    for i in range(len(items)):
        start, end = _item_range(doc, node, i)
        chunks.append(text[prev:start])  # the lines between items stay
        prev = end
        if i == first_kept and None in after:
            chunks.append(emit([new[j] for j in after[None]]))
        if i in replaces:
            chunks.append(emit([new[replaces[i]]]))
        elif i not in deletes:
            chunks.append(text[start:end])
        if i in after:
            chunks.append(emit([new[j] for j in after[i]]))
    return [(region_start, prev, "".join(chunks))]


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
    """A key as written. A string key is always written so that it loads as
    that string: ``true``, ``null`` or ``1`` as a new key is quoted."""
    if not isinstance(key, str):
        return _emit_scalar(key, None, None, flow)
    # a key at column 0 spelled like a document marker would end the
    # frontmatter for a tool that splits on a line starting with --- or ...
    marker = key.startswith(("---", "..."))
    if (
        key
        and not marker
        and key == key.strip()
        and _printable(key)
        and not _is_yaml11_ambiguous(key)
    ):
        probe = f"{{{key}: 0}}" if flow else f"{key}: 0"
        try:
            loaded = _yaml().load(probe)
        except YAMLError:
            loaded = None
        if isinstance(loaded, Mapping) and len(loaded) == 1:
            got = next(iter(loaded))
            if type(got) is str and got == key:
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
        return _emit_float(value)
    if isinstance(value, date):
        return value.isoformat()
    if style == "'" and _single_ok(value):
        return "'" + value.replace("'", "''") + "'"
    if style == '"' or style == "'":
        return _double(value)
    if _plain_ok(value, flow, timestamp=bool(tag and tag.endswith(":timestamp"))):
        return value
    return _double(value)


def _emit_float(value: float) -> str:
    """A float both YAML 1.2 and YAML 1.1 read as a float: 1.1 needs a dot
    (``1e+20`` is a string there), so ``1.0e+20``."""
    if math.isnan(value):
        return ".nan"  # NaN equals NaN here (_scalar_eq), so it can be written (#749)
    if math.isinf(value):
        return ".inf" if value > 0 else "-.inf"
    text = repr(float(value))
    if "e" in text and "." not in text:
        mantissa, exponent = text.split("e")
        text = f"{mantissa}.0e{exponent}"
    return text


def _printable(value: str) -> bool:
    """No character a YAML reader would not take bare: control characters,
    NEL, the line and paragraph separators, a BOM."""
    return not any(ord(ch) < 0x20 or 0x7F <= ord(ch) <= 0x9F or ch in "  ﻿" for ch in value)


def _plain_ok(value: str, flow: bool, timestamp: bool) -> bool:
    """Does ``value`` written bare read back as itself under YAML 1.2, and not
    as anything else under YAML 1.1? A string written over a date may stay
    bare when it reads back as that same date (decision 3)."""
    if not value or value != value.strip() or not _printable(value):
        return False
    if _is_yaml11_ambiguous(value):
        return False
    try:
        loaded = _yaml().load(f"k: [{value}]" if flow else f"k: {value}")
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
    return _printable(value)


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


def _emit_block_scalar(
    value: str, style: str, indent: int, parent: int, explicit: bool, eol: str, was_keep: bool
) -> tuple[str, str] | None:
    """``value`` as a literal (``|``) or folded (``>``) block scalar whose
    content lines sit at ``indent``: (the header, the body that replaces the
    old content). None when it needs a form this does not write: a control
    character, a folded line that starts with a space, keep chomping where the
    old scalar did not keep (blank lines after it would become content)."""
    if not _printable(value.replace("\n", "").replace("\t", "")):
        return None
    stripped = value.rstrip("\n")
    trailing = len(value) - len(stripped)
    if trailing > 1 and not was_keep:
        return None
    chomp = "-" if trailing == 0 else ("" if trailing == 1 else "+")
    if not stripped:
        return None
    parts = stripped.split("\n")
    needs_indicator = parts[0][:1] in (" ", "\t")
    if parts[0][:1] == "\t":
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
    if was_keep and trailing <= 1:
        pass  # the old trailing blank lines are inside the replaced range
    parts = parts + [""] * max(0, trailing - 1)
    indicator = ""
    if needs_indicator or explicit:
        step = indent - parent
        if not 1 <= step <= 9:
            return None
        indicator = str(step)
    body = eol + eol.join((" " * indent + p) if p else "" for p in parts)
    return f"{style}{indicator}{chomp}", body


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
