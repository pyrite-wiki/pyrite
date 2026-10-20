"""THE parser of a frontmatter field path: ``a.b[0].c`` -> ``["a", "b", 0, "c"]``.

One reading of a path for every layer: the request boundary
(``KBService._request_operations`` and ``_item_operations``), the field
resolution (``schema.field_aliases.path_segments`` / ``field_at``) and the
write (``storage.file_operations.apply``) all call this, so the path a
refusal is decided on is the path that is written (#1001 review; Andon
#994). It lives here, below every layer, because ``schema`` may not import
``storage`` and ``storage`` must not import a surface.

A path is parsed from a string exactly once. A tuple or list is already
parsed: its string segments are literal keys and are never read again.
"""

from __future__ import annotations

from typing import Any


def parse_field_path(path: str) -> list[str | int]:
    """Segments of a dotted path with ``[i]`` indexes; strings name keys,
    ints index lists. Raises ``ValueError`` when the path cannot be read:
    an empty segment (``a..b``), a bracket that is not an index, a segment
    that is neither, a path that starts with a list index."""
    segs: list[str | int] = []
    for part in path.split("."):
        name, bracket, rest = part.partition("[")
        if not name and not (bracket and segs):
            raise ValueError(f"the path {path!r} has an empty segment")
        if "]" in name:
            raise ValueError(f"cannot read the path {path!r}")
        if name:
            segs.append(name)
        rest = bracket + rest
        while rest:
            index, closed, rest = rest[1:].partition("]")
            if not rest.startswith("[") and rest:
                raise ValueError(f"cannot read the path {path!r}")
            try:
                number = int(index)
            except ValueError as e:
                raise ValueError(f"cannot read the path {path!r}") from e
            if not closed:
                raise ValueError(f"cannot read the path {path!r}")
            segs.append(number)
    if segs and isinstance(segs[0], int):
        raise ValueError(f"the path {path!r} starts with a list index")
    return segs


def check_segments(segs: Any) -> list[str | int]:
    """An already-parsed path (a tuple or list): every segment a string or
    an int (never a bool), returned as a list. Raises ``ValueError``."""
    out = list(segs)
    for seg in out:
        if isinstance(seg, bool) or not isinstance(seg, str | int):
            raise ValueError(f"a path segment is a string or an int, not {seg!r}")
    return out
