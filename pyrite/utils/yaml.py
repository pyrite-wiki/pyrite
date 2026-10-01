"""Round-trip safe YAML utilities using ruamel.yaml.

Preserves comments, quoting style, and key ordering — producing minimal
git diffs when only a single field changes.
"""

import re
from collections.abc import Iterable, Mapping, MutableMapping, MutableSequence
from io import StringIO
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError
from ruamel.yaml.scalarstring import DoubleQuotedScalarString
from yaml.resolver import Resolver as _PyYAMLResolver

from ..exceptions import FrontmatterError


def _pyyaml_regexes(tag: str) -> list[re.Pattern[str]]:
    """The compiled regex(es) PyYAML's own resolver uses to recognize `tag`.

    Pulled directly from ``yaml.resolver.Resolver`` (PyYAML) rather than
    retyped, so this can't drift from what a real PyYAML ``safe_load``
    actually resolves a bare plain scalar to (#568).
    """
    seen_ids: set[int] = set()
    regexes: list[re.Pattern[str]] = []
    for resolvers in _PyYAMLResolver.yaml_implicit_resolvers.values():
        for candidate_tag, regex in resolvers:
            if candidate_tag == tag and id(regex) not in seen_ids:
                seen_ids.add(id(regex))
                regexes.append(regex)
    return regexes


#: What PyYAML's `safe_load` (YAML 1.1) resolves a bare plain scalar to
#: besides a string: bool, null, int, float. A Pyrite *string* matching one
#: of these must be quoted on write, or a PyYAML reader sees something other
#: than the string Pyrite was given.
_PYYAML11_NONSTRING_REGEXES = [
    rgx
    for tag in (
        "tag:yaml.org,2002:bool",
        "tag:yaml.org,2002:null",
        "tag:yaml.org,2002:int",
        "tag:yaml.org,2002:float",
    )
    for rgx in _pyyaml_regexes(tag)
]


def _is_yaml11_ambiguous(value: str) -> bool:
    """Would PyYAML's `safe_load` (YAML 1.1) resolve this *string* scalar to
    something other than a string, written bare?

    #568's acceptance list also names bare ``y``/``Y``/``n``/``N`` (the full
    YAML 1.1 spec treats them as booleans), but PyYAML's own bool resolver
    does not -- ``yaml.safe_load("y")`` is the string ``'y'`` -- and quoting
    them regressed an existing single-letter list item
    (``TestBlockSequenceIndentRoundTrip``). This stays driven by PyYAML's
    actual resolver tables so it matches what `safe_load` really does and
    can't drift from it; see the pyrite-dev report's "Unsure" for the
    tradeoff.
    """
    return any(rgx.fullmatch(value) for rgx in _PYYAML11_NONSTRING_REGEXES)


def _quote_yaml11_ambiguous_strings(node: Any) -> None:
    """Recursively wrap plain string scalars a YAML 1.1 reader would misread.

    Mutates ``node`` in place, at any depth and inside list items: a bare
    Python ``str`` (``type(value) is str`` -- not a quoted/styled ruamel
    scalar subclass, and not a real bool/int/float) that
    ``_is_yaml11_ambiguous`` matches is replaced with a
    ``DoubleQuotedScalarString``, so the emitter writes explicit quotes. A
    value already quoted in the source (loaded with ``preserve_quotes``,
    so it is already a ``ScalarString`` subclass, not plain ``str``) is left
    exactly as it is -- its existing quote style is kept, not changed to
    double quotes.
    """
    if isinstance(node, MutableMapping):
        items: Iterable[Any] = list(node.keys())
        for key in items:
            value = node[key]
            if type(value) is str and _is_yaml11_ambiguous(value):
                node[key] = DoubleQuotedScalarString(value)
            else:
                _quote_yaml11_ambiguous_strings(value)
    elif isinstance(node, MutableSequence):
        for i, value in enumerate(node):
            if type(value) is str and _is_yaml11_ambiguous(value):
                node[i] = DoubleQuotedScalarString(value)
            else:
                _quote_yaml11_ambiguous_strings(value)


def _get_yaml() -> YAML:
    """Get a configured YAML instance for round-trip processing."""
    y = YAML()
    y.preserve_quotes = True
    y.default_flow_style = False
    y.width = 4096  # prevent line wrapping
    return y


def _node_column(node: Any) -> int | None:
    """The column a parsed node started at, or None for a node built here."""
    lc = getattr(node, "lc", None)
    return None if lc is None else lc.col


def _is_block_sequence(node: Any) -> bool:
    """Is this a sequence written in block style (not `[a, b]`)?"""
    from ruamel.yaml.comments import CommentedSeq

    return isinstance(node, CommentedSeq) and not node.fa.flow_style()


def _detected_sequence_indent(data: Any) -> tuple[int, int] | None:
    """The `(sequence, offset)` the source document was written with.

    ruamel's emitter takes `sequence` and `offset` for the whole document --
    there is no per-node setting -- so a round-trip write cannot ask one
    sequence how it sat in the file it came from. What the parsed tree does
    carry is each node's original line and column, and for a document written
    in one style that is enough:

    ```
    links:
      - target: "adr-0018"      # the `-` at column 2  -> offset
        relation: "implements"  # the item at column 4  -> sequence
    ```

    Both numbers are read against the column of the mapping that holds the
    sequence: the document root is column 0 by definition, and every nested
    mapping carries its own column in `lc`. Returns `None` when there is no
    block sequence to read -- a freshly built entry has no source style to
    preserve, and the emitter's defaults stay as they are (#148).
    """

    def _search(node: Any, parent_col: int) -> tuple[int, int] | None:
        if isinstance(node, Mapping):
            column = _node_column(node)
            column = parent_col if column is None else column
            for value in node.values():
                found = _search(value, column)
                if found is not None:
                    return found
            return None

        if isinstance(node, str | bytes) or not isinstance(node, Iterable):
            return None

        if _is_block_sequence(node):
            seq_col = _node_column(node)
            # An empty sequence carries no layout of its own: its `lc` points
            # at something arbitrary -- one real file's empty `dependencies:`
            # reported column 14, the column of an unrelated line -- and
            # believing it re-indented every other sequence in the document.
            # Skipped rather than guessed at.
            if seq_col is not None and len(node):
                offset = seq_col - parent_col
                # A sequence of scalars carries no per-item column (`str` has
                # no `lc`), so the content column is the dash's plus the
                # mapping indent -- the same relation `y.indent()` encodes
                # between `offset` and `sequence`.
                item_col = _node_column(node[0]) if len(node) else None
                sequence = offset + 2 if item_col is None else item_col - parent_col
                if 0 <= offset < sequence:
                    return (sequence, offset)

        for item in node:
            found = _search(item, parent_col)
            if found is not None:
                return found
        return None

    return _search(data, 0)


def _dumper_for(data: Any) -> YAML:
    """A YAML dumper whose block-sequence indent matches the source document.

    Nothing else about the emitter changes: with no readable source style this
    is exactly `_get_yaml()`.
    """
    y = _get_yaml()
    indent = _detected_sequence_indent(data)
    if indent is not None:
        y.indent(mapping=2, sequence=indent[0], offset=indent[1])
    return y


def load_yaml(text: str) -> dict[str, Any]:
    """Load YAML from a string.

    Returns a CommentedMap (dict-compatible MutableMapping) that preserves
    comments and quoting when later passed to ``dump_yaml``.  Returns an
    empty dict for blank / ``None`` input.

    Raises ``FrontmatterError`` if the text is not valid YAML or does not
    parse to a mapping, so a single malformed entry surfaces a clear error
    instead of leaking a raw ruamel traceback to callers.
    """
    y = _get_yaml()
    try:
        result = y.load(text)
    except YAMLError as e:
        raise FrontmatterError(f"Invalid YAML frontmatter: {e}") from e
    if result is None:
        return {}
    if not isinstance(result, Mapping):
        raise FrontmatterError(f"Frontmatter must be a mapping, got {type(result).__name__}")
    return result


def dump_yaml(data: Any) -> str:
    """Dump a mapping to a YAML string, preserving style.

    The returned string has no trailing newline so it can be embedded
    directly inside YAML frontmatter fences.

    Any string scalar a YAML 1.1 reader (PyYAML's ``safe_load`` -- the most
    common Python YAML reader) would resolve to a bool/null/number is
    quoted first (#568), so every reader -- not only ruamel, which Pyrite
    itself reads with -- gets back the string that was written.
    """
    _quote_yaml11_ambiguous_strings(data)
    y = _dumper_for(data)
    stream = StringIO()
    y.dump(data, stream)
    return stream.getvalue().rstrip("\n")


def load_yaml_file(path: str | Path) -> dict[str, Any]:
    """Load YAML from a file path.

    Returns a CommentedMap (dict-compatible) or empty dict.
    """
    p = Path(path)
    y = _get_yaml()
    with open(p) as f:
        result = y.load(f)
    return result if result is not None else {}


def dump_yaml_file(data: Any, path: str | Path, *, atomic: bool = False) -> None:
    """Write a mapping to a YAML file, preserving style.

    ``atomic=True`` replaces the file crash-safely, keeping its mode, owner,
    hard links and symlink (see ``pyrite.utils.atomic_write``, #405).
    """
    p = Path(path)
    _quote_yaml11_ambiguous_strings(data)
    y = _dumper_for(data)
    if atomic:
        from .atomic_write import atomic_write_text

        stream = StringIO()
        y.dump(data, stream)
        atomic_write_text(p, stream.getvalue())
        return
    with open(p, "w") as f:
        y.dump(data, f)
