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
import os
from pathlib import Path
from typing import Any

from ..exceptions import FrontmatterError
from ..models.base import _ABSENT as ABSENT
from ..models.base import DEFAULT_IMPORTANCE
from ..schema.validators import importance_value
from ..utils.parse import safe_int

logger = logging.getLogger(__name__)


def refused_importance_message(given: Any) -> str | None:
    """The sentence for a scalar file value the importance rule refuses, else ``None``.

    A missing key (``ABSENT``, the model's own sentinel), a null or blank value,
    a value the rule accepts, and a mapping or list (reported by
    ``reserved_key_collision`` with its rename advice) say nothing."""
    if given is None or given is ABSENT or (isinstance(given, str) and not given.strip()):
        return None
    if isinstance(given, (dict, list, tuple, set)):
        return None
    if importance_value(given) is not None:
        return None
    shown = safe_int(given, DEFAULT_IMPORTANCE)
    return f"importance is a whole number 1-10; the file holds {given!r}, readers show {shown}"


def file_importance(root: str | Path | None, path: str | Path | None) -> Any:
    """The ``importance`` the file at ``path`` holds, as parsed; ``ABSENT`` when
    there is no file, no frontmatter or no such key, and for a path that is
    not the KB's (a link out of ``root``): nothing outside the KB is read.
    The bytes come through ``review_service._read_entry_file`` (``KBFiles.read_bytes``,
    the one classified read of an entry file), like every read whose answer reaches a caller. Never raises."""
    if not root or not path:
        return ABSENT
    try:
        # Local: review_service loads sqlalchemy, which the CLI entry points
        # must not load at import (tests/test_cli_import_performance.py).
        from ..utils.frontmatter import load_frontmatter
        from .review_service import _read_entry_file

        raw = _read_entry_file(Path(root), path)
        if raw is None:
            return ABSENT
        loaded = load_frontmatter(raw.decode("utf-8"))
    except (OSError, ValueError, FrontmatterError):
        logger.debug("importance reading: cannot read %s", path, exc_info=True)
        return ABSENT
    if loaded is None:
        return ABSENT
    return loaded[0].get("importance", ABSENT)


def _is_lexically_inside(root: str | Path, path: str | Path) -> bool:
    """``path`` stays under ``root`` once ``..`` is collapsed (lexical only)."""
    top = os.path.normpath(os.path.abspath(root))
    target = os.path.normpath(os.path.abspath(path))
    try:
        return os.path.commonpath([top, target]) == top
    except ValueError:
        return False


def _non_empty_root(value: Any) -> Path | None:
    """Convert a non-empty root string/Path to ``Path``, rejecting ``None`` and blank strings.

    In Python ``Path("")`` becomes ``Path(".")`` (CWD), which is truthy and
    would silently grant the process working directory as a KB root.
    """
    if not value:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return Path(value)


def _kb_roots(db: Any, kb_name: str, roots: dict[str, Any]) -> tuple[Path, ...]:
    """The owning KB's configured root plus any overlay roots ``db`` grants for
    it (``WorktreeDB.kb_roots``), cached once per ``kb_name`` in ``roots``."""
    if kb_name not in roots:
        rows = db.execute_sql("SELECT path FROM kb WHERE name = :name", {"name": kb_name})
        main_root = _non_empty_root(rows[0]["path"]) if rows else None
        extra = (getattr(db, "kb_roots", None) or {}).get(kb_name, ()) or ()
        allowed: list[Path] = []
        if main_root is not None:
            allowed.append(main_root)
        for candidate in extra:
            root_path = _non_empty_root(candidate)
            if root_path is not None and root_path not in allowed:
                allowed.append(root_path)
        roots[kb_name] = tuple(allowed)
    cached = roots[kb_name]
    if not cached:
        return ()
    if isinstance(cached, (str, Path)):
        root_path = _non_empty_root(cached)
        return (root_path,) if root_path is not None else ()
    return tuple(p for r in cached if (p := _non_empty_root(r)) is not None)


def _owning_root(allowed_roots: tuple[Path, ...], path: str | Path) -> Path | None:
    """The root in ``allowed_roots`` that lexically contains ``path``, or ``None``.

    A relative path belongs to the configured (first) root. An absolute path
    must stay lexically inside one of the KB's granted roots; choosing the
    deepest matching root keeps any nested overlay root strictly contained
    before ``KBFiles.read_bytes`` enforces symlink and hard-link containment.
    """
    if not allowed_roots:
        return None
    target = Path(path)
    if not target.is_absolute():
        return allowed_roots[0]
    matching = [root for root in allowed_roots if _is_lexically_inside(root, target)]
    if not matching:
        return None
    return max(matching, key=lambda root: len(os.path.normpath(os.path.abspath(root))))


def row_importance_warning(
    db: Any, kb_name: str | None, path: str | Path | None, roots: dict[str, Any] | None = None
) -> str | None:
    """`refused_importance_message` for the file of an index row of ``kb_name``,
    read through that KB's own root (or a granted overlay root on ``WorktreeDB``);
    None when the KB is unknown or the path lies outside every granted root.

    ``roots``: a dict the caller keeps for one pass, so the KB root is looked up
    once per KB, not once per row. The file read stays one per row (a stated
    limit: a page of N rows opens N files)."""
    if not kb_name or not path:
        return None
    roots = roots if roots is not None else {}
    owning = _owning_root(_kb_roots(db, kb_name, roots), path)
    if owning is None:
        return None
    return refused_importance_message(file_importance(owning, path))


def attach_importance_warnings(db: Any, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach ``entry_warnings`` to rows whose file holds a refused ``importance``.

    Looks up each KB's root(s) once per pass and reads each file through the
    owning KB's ``KBFiles`` (including granted overlay roots on ``WorktreeDB``).
    Rows with valid or absent importance are left untouched.
    """
    from .read_shaping import ENTRY_WARNINGS_KEY

    kb_roots: dict[str, Any] = {}
    for row in rows:
        if not isinstance(row, dict) or not row.get("id") or not row.get("kb_name"):
            continue
        path = row.get("file_path") or (db.get_entry(row["id"], row["kb_name"]) or {}).get(
            "file_path"
        )
        if said := row_importance_warning(db, row["kb_name"], path, kb_roots):
            row[ENTRY_WARNINGS_KEY] = [said]
    return rows
