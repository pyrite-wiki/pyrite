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

from ..models.base import DEFAULT_IMPORTANCE
from ..schema.validators import importance_value
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


def file_importance(path: str | Path | None) -> Any:
    """The ``importance`` the file at ``path`` holds, as parsed; ``ABSENT`` when
    there is no file, no frontmatter or no such key. Never raises."""
    if not path:
        return ABSENT
    try:
        from ..utils.frontmatter import load_frontmatter

        loaded = load_frontmatter(Path(path).read_text(encoding="utf-8"))
    except Exception:
        logger.debug("importance reading: cannot read %s", path, exc_info=True)
        return ABSENT
    if loaded is None:
        return ABSENT
    return loaded[0].get("importance", ABSENT)


def file_importance_warning(path: str | Path | None) -> str | None:
    """`refused_importance_message` for the file at ``path``."""
    return refused_importance_message(file_importance(path))
