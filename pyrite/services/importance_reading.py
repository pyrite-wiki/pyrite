"""A refused ``importance`` is never shown silently (#1761, maintainer decision C).

A file's ``importance`` the one rule (`importance_value`) refuses -- ``7.5``,
``0``, ``11``, ``"high"``, ``.nan`` -- keeps its legacy reading for readers
(``Entry._base_kwargs``: the index, ``get``, ``search`` show a whole number
while the file keeps its own text). This module names that disagreement, from
the file's value, so ``qa validate`` and every reader say the same sentence.
An accepted spelling (``5.0``, ``"05"``) and an absent or blank value say
nothing.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..exceptions import FrontmatterError
from ..models.base import DEFAULT_IMPORTANCE
from ..schema.validators import importance_value
from ..utils.kb_files import KBFileOutsideRootError, KBFiles
from ..utils.parse import safe_int

logger = logging.getLogger(__name__)

ABSENT = object()


def refused_importance_message(given: Any) -> str | None:
    """The sentence for a file value the importance rule refuses, else ``None``."""
    if given is None or given is ABSENT or (isinstance(given, str) and not given.strip()):
        return None
    if importance_value(given) is not None:
        return None
    shown = safe_int(given, DEFAULT_IMPORTANCE)
    return f"importance is a whole number 1-10; the file holds {given!r}, readers show {shown}"


def file_importance(root: str | Path | None, path: str | Path | None) -> Any:
    """The ``importance`` the file at ``path`` holds, as parsed; ``ABSENT`` when
    there is no file, no frontmatter or no such key, and for a path that is
    not the KB's (a link out of ``root``): nothing outside the KB is read.
    The bytes come through ``KBFiles.read_bytes``, like every read whose
    answer reaches a caller. Never raises."""
    if not root or not path:
        return ABSENT
    try:
        from ..utils.frontmatter import load_frontmatter

        raw = KBFiles(root).read_bytes(Path(path))
        if raw is None:
            return ABSENT
        loaded = load_frontmatter(raw.decode("utf-8"))
    except (OSError, ValueError, FrontmatterError, KBFileOutsideRootError):
        logger.debug("importance reading: cannot read %s", path, exc_info=True)
        return ABSENT
    if loaded is None:
        return ABSENT
    return loaded[0].get("importance", ABSENT)


def row_importance_warning(db: Any, kb_name: str | None, path: str | Path | None) -> str | None:
    """`refused_importance_message` for the file of an index row of ``kb_name``,
    read through that KB's own root; None when the KB is unknown."""
    if not kb_name or not path:
        return None
    rows = db.execute_sql("SELECT path FROM kb WHERE name = :name", {"name": kb_name})
    if not rows:
        return None
    return refused_importance_message(file_importance(rows[0]["path"], path))
