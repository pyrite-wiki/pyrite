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
from ruamel.yaml.comments import merge_attrib
from ruamel.yaml.error import YAMLError
from ruamel.yaml.scalarstring import DoubleQuotedScalarString
from yaml.resolver import Resolver as _PyYAMLResolver

from ..exceptions import FrontmatterError, YamlLimitError, outside_text

#: Fixed bounds on a YAML document, checked before anything is built:
#:
#: - ``MAX_YAML_ALIASES``: alias references in one document. KB files use a
#:   handful (a shared ``&defaults`` merged into a few records); 1,000 is two
#:   orders of magnitude above that.
#: - ``MAX_YAML_EXPANDED_NODES``: scalars, sequences and mappings counted as if
#:   every alias were copied out. A 10 MB file of plain data is about 1M nodes,
#:   so a document past this is larger than any file Pyrite is meant to hold.
MAX_YAML_ALIASES = 1000
MAX_YAML_EXPANDED_NODES = 1_000_000


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

    Two deliberate differences from #568's acceptance list, both because
    this stays driven by PyYAML's actual resolver tables rather than a
    hand-typed list, so it can't drift from what `safe_load` really does:

    - Bare ``y``/``Y``/``n``/``N`` are NOT treated as ambiguous. The full
      YAML 1.1 spec treats them as booleans, but PyYAML's own bool resolver
      does not (``yaml.safe_load("y")`` is the string ``'y'``), and quoting
      them regressed an existing single-letter list item
      (``TestBlockSequenceIndentRoundTrip``). A stricter YAML 1.1 reader
      than PyYAML -- Go's ``yaml.v2``, for one -- does read bare ``y``/``n``
      as booleans; a Pyrite string with exactly that value still round-trips
      through PyYAML, just not through every YAML 1.1 implementation.
    - The empty string is excluded here even though PyYAML's null resolver
      matches it: ruamel's own representer already quotes an empty string
      (``''``) on its own account, unconditionally, for an unrelated reason
      (an empty bare scalar is otherwise ambiguous with "key with no
      value"). Matching it here too would just change `''` to `""` for no
      behavioural gain -- a formatting change with no reader it fixes.
    """
    return value != "" and any(rgx.fullmatch(value) for rgx in _PYYAML11_NONSTRING_REGEXES)


def _own_items(node: MutableMapping) -> list[tuple[Any, Any]]:
    """``node``'s own key/value pairs, excluding any merged in via a YAML
    merge key (``<<: *anchor``).

    A ``CommentedMap`` with a merge exposes the anchor's keys through the
    ordinary mapping interface (``items()``, ``__getitem__``,
    ``__setitem__``) as if they were the map's own -- so reading OR writing
    through that interface, including a plain ``node[k] = v``, silently
    turns a merged-in key into a real, explicit one on the child (#571
    round 2). ``non_merged_items()`` is what ruamel's own representer calls
    to decide what a mapping with a merge should literally write
    (``RoundTripRepresenter.represent_mapping``); used the same way here,
    nothing merged in is ever touched. There is nothing to quote in it
    that recursing into the anchor's own definition -- wherever it sits in
    the document -- does not already reach.
    """
    if getattr(node, merge_attrib, None) and hasattr(node, "non_merged_items"):
        return list(node.non_merged_items())
    return list(node.items())


def _quote_yaml11_ambiguous_strings(node: Any) -> None:
    """Recursively wrap plain string scalars a YAML 1.1 reader would misread.

    Mutates ``node`` in place, at any depth and inside list items, and on
    mapping KEYS as well as values -- an undeclared frontmatter key is
    stored as the caller wrote it (#407), so ``on: x`` is exactly as
    reader-ambiguous as a value would be, and two keys that differ only in
    a form PyYAML conflates (``yes`` and ``on``, both the bool ``True``)
    collide, losing an entry. Only a mapping's own keys are ever visited --
    see ``_own_items``.

    A bare Python ``str`` (``type(value) is str`` -- not a quoted/styled
    ruamel scalar subclass, and not a real bool/int/float) that
    ``_is_yaml11_ambiguous`` matches is replaced with a
    ``DoubleQuotedScalarString``, so the emitter writes explicit quotes. A
    value already quoted in the source (loaded with ``preserve_quotes``, so
    it is already a ``ScalarString`` subclass, not plain ``str``) is left
    exactly as it is -- its existing quote style is kept, not changed to
    double quotes. A value-only change is assigned in place
    (``node[key] = ...``); the mapping itself is not rebuilt.

    Renaming a key DOES rebuild the mapping (clear, then re-set every own
    item in its original order) rather than deleting and re-adding just
    that one key, which would move it to the end. A ``DoubleQuotedScalarString``
    compares equal to (and hashes the same as) the plain ``str`` it wraps,
    so a comment ruamel attached to the original key (keyed by equality, in
    ``CommentedMap.ca``, untouched by ``clear()``) still finds it.
    """
    if isinstance(node, MutableMapping):
        renamed: dict[Any, Any] = {}
        for key, value in _own_items(node):
            if type(value) is str and _is_yaml11_ambiguous(value):
                node[key] = DoubleQuotedScalarString(value)
            else:
                _quote_yaml11_ambiguous_strings(value)
            if type(key) is str and _is_yaml11_ambiguous(key):
                renamed[key] = DoubleQuotedScalarString(key)
        if renamed:
            current = _own_items(node)  # re-read: values above may have changed
            node.clear()
            for k, v in current:
                node[renamed.get(k, k)] = v
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


def _measure_events(events: Any, ev: Any) -> None:
    """Refuse a document whose events pass the bounds. ``ev`` is the events
    module of the parser that made them (ruamel's or PyYAML's): the two have
    the same event classes, and a bound holds only when it is measured on
    the events of the parser that does the load.

    Each node's expanded size comes from its events (an alias counts as the
    size of its anchor, capped so the arithmetic stays small), so a bomb
    nine levels deep is measured in a few hundred events, never expanded.
    """
    cap = MAX_YAML_EXPANDED_NODES
    sizes: dict[str, int] = {}
    stack: list[list[Any]] = []  # [anchor, size so far] per open collection
    root = 0
    aliases = 0

    def add(n: int) -> None:
        nonlocal root
        if stack:
            stack[-1][1] = min(cap + 1, stack[-1][1] + n)
            total = stack[-1][1]
        else:
            root = min(cap + 1, root + n)
            total = root
        if total > cap:
            raise YamlLimitError(
                f"YAML document refused: its aliases expand to more than {cap:,} nodes"
            )

    for event in events:
        if isinstance(event, ev.AliasEvent):
            aliases += 1
            if aliases > MAX_YAML_ALIASES:
                raise YamlLimitError(
                    f"YAML document refused: more than {MAX_YAML_ALIASES:,} aliases"
                )
            add(sizes.get(event.anchor, 1))
        elif isinstance(event, ev.ScalarEvent):
            if event.anchor:
                sizes[event.anchor] = 1
            add(1)
        elif isinstance(event, ev.CollectionStartEvent):
            stack.append([event.anchor, 1])
        elif isinstance(event, ev.CollectionEndEvent) and stack:
            anchor, size = stack.pop()
            if anchor:
                sizes[anchor] = size
            add(size)


def _check(text: str, events_of: Any, ev: Any, parse_error: type[Exception]) -> None:
    """The bound on ``text``, measured by one parser (``events_of``, its
    event stream; ``parse_error``, its own error for malformed YAML).

    Malformed YAML is that same parser's error, and the load that follows
    stops at the same event with it, so nothing unmeasured is built. Any
    other exception (ruamel raises ``AssertionError`` on a ``%YAML 1.3``
    directive) means the document cannot be measured: it is refused,
    ``YamlLimitError``, never passed on to a load.

    Skipped for text that can hold no alias (no ``&`` or no ``*``) and no
    directive (no ``%``).
    """
    if ("*" not in text or "&" not in text) and "%" not in text:
        return
    try:
        _measure_events(events_of(text), ev)
    except YamlLimitError:
        raise
    except parse_error:
        return  # the same parser's load stops at the same event and reports it
    except Exception as e:
        raise YamlLimitError(
            f"YAML document refused: it could not be measured ({type(e).__name__})"
        ) from e


def check_yaml_limits(text: str) -> None:
    """Refuse a document whose aliases pass the bounds, without building it,
    measured on ruamel's parse events: for the loads ruamel makes
    (``load_yaml``, ``load_yaml_document``, ``load_yaml_file`` and the
    ``compose`` of ``storage.file_operations``). A PyYAML load is measured by
    ``safe_load_bounded`` on PyYAML's own events instead: two parsers do not
    agree on what text they can read (PyYAML reads U+2028 as a line break,
    ruamel refuses it), so a bound measured by one does not hold for the
    other.

    Raises ``YamlLimitError``, also for a document ruamel cannot measure
    without a parse error (``%YAML 1.3`` stops it with an ``AssertionError``);
    malformed YAML is left for the load to report.
    """
    from ruamel.yaml import events

    _check(text, _get_yaml().parse, events, YAMLError)


def safe_load_bounded(text: str) -> Any:
    """PyYAML ``safe_load`` (YAML 1.1 reading) of ``text`` within the alias bounds.

    For the readers that want plain Python values rather than ruamel's
    round-trip types (templates, alias lists, bulk spec files). The bound is
    measured on PyYAML's own event stream, the parser that then loads, so
    text ruamel would refuse to parse is measured too. Raises
    ``YamlLimitError`` before anything is built when the bounds are passed or
    the text cannot be measured, and ``yaml.YAMLError`` for malformed input.
    """
    import yaml
    from yaml import events

    _check(text, lambda t: yaml.parse(t, Loader=yaml.SafeLoader), events, yaml.YAMLError)
    return yaml.safe_load(text)


def load_yaml_document(text: str) -> Any:
    """Parse a YAML document, including bulk-import sequences and empty input.

    Entry frontmatter callers must use ``load_yaml`` for its mapping contract.
    Both paths use the same parser, scalar rules and public parse error.
    """
    check_yaml_limits(text)
    try:
        return _get_yaml().load(text)
    except YAMLError as e:
        # The parser's text quotes the file's own lines: the operator's, not
        # a network caller's (ADR-0052 N3); the CLI still shows it.
        raise FrontmatterError(f"Invalid YAML frontmatter: {outside_text(e)}") from e


def load_yaml(text: str) -> dict[str, Any]:
    """Load YAML from a string.

    Returns a CommentedMap (dict-compatible MutableMapping) that preserves
    comments and quoting when later passed to ``dump_yaml``.  Returns an
    empty dict for blank / ``None`` input.

    Raises ``FrontmatterError`` if the text is not valid YAML or does not
    parse to a mapping, so a single malformed entry surfaces a clear error
    instead of leaking a raw ruamel traceback to callers.
    """
    result = load_yaml_document(text)
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


def load_yaml_file(path: str | Path, *, kb_root: str | Path | None = None) -> dict[str, Any]:
    """Load YAML from a file path.

    Returns a CommentedMap (dict-compatible) or empty dict.

    ``kb_root``: the file is a KB's (``kb.yaml``, ``board.yaml``) and is read
    only inside that root (``KBFiles.read_bytes``): a link out of it, or a
    file with more than one hard link, raises ``KBFileReadOutsideRootError``
    (``OUTSIDE_KB_ROOT``) and nothing is read. ``FileNotFoundError`` when it
    is absent. Every reader of a KB's own YAML whose values reach a caller
    passes it.
    """
    p = Path(path)
    y = _get_yaml()
    if kb_root is not None:
        from .kb_files import KBFiles

        raw = KBFiles(kb_root).read_bytes(p)
        if raw is None:
            raise FileNotFoundError(str(p))
        text = raw.decode("utf-8")
    else:
        with open(p) as f:
            text = f.read()
    check_yaml_limits(text)
    result = y.load(text)
    return result if result is not None else {}


def dump_yaml_text(data: Any) -> str:
    """The text ``dump_yaml_file`` writes for ``data``, for a caller that
    publishes it itself (a KB's kb.yaml goes through ``KBFiles``)."""
    _quote_yaml11_ambiguous_strings(data)
    stream = StringIO()
    _dumper_for(data).dump(data, stream)
    return stream.getvalue()


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
