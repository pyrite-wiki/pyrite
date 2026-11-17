"""Which paths the index walk lists as entry files: one rule, one home (#1552).

The walk (``KBRepository.walk_files``) keeps a path below the KB root only
when this module says so, and every writer that places a new entry-file name
(a create through ``create_entry_text``, the templated move, software-kb's
``move_entry_text``, the reconcile's ``KBFiles.move``) asks it before
anything is written. So a file a write places as an entry is a file the next
``index sync`` lists (``kb/design.md`` principle 8).

The rule is lexical, on the path's parts below the root, with no I/O. A path
is skipped when:

- ``hidden``: a part starts with ``.`` (a dot-folder or a dot-file);
- ``templates``: a part is ``_templates`` (the template service's scaffolds);
- ``readme``: the file name is ``readme.md`` in any case (a README is the
  user's, assumed to carry no entry);
- ``not_markdown``: the file name does not end in ``.md``, case-sensitively,
  so ``x.MD`` is skipped on every OS, not only where ``rglob`` is
  case-sensitive.

The reserved-name test is built on the lowercased name, never on a probe of
the disk, so the answer is the same on a case-insensitive filesystem (macOS)
and a case-sensitive one. These are the files ``docs/pinning-entry-ids.md``
promises Pyrite does not index; a hand-kept ``README.md``, dot-folder or
``_templates/`` file stays the user's.

Leaf module: imports nothing from ``pyrite.storage``, so ``repository.py``,
``entry_writer.py`` and the services can all ask it.
"""

from __future__ import annotations

import os

from ..exceptions import ValidationError

#: The reasons a path below the root is not listed, as ``index health``
#: names them (``readme | hidden | templates``) plus the suffix rule.
README = "readme"
HIDDEN = "hidden"
TEMPLATES = "templates"
NOT_MARKDOWN = "not_markdown"
#: Not below the KB root at all: never listed, and not this rule's refusal
#: (``KBFiles`` answers ``OUTSIDE_KB_ROOT`` for it).
OUTSIDE = "outside"

_WHY = {
    README: "a file named README.md (in any case) is the user's, not an entry",
    HIDDEN: "a part starting with '.' is hidden",
    TEMPLATES: "`_templates/` holds the template scaffolds",
    NOT_MARKDOWN: "an entry file's name ends in `.md` (lower case)",
}


def parts_below(
    root: str | os.PathLike[str], path: str | os.PathLike[str]
) -> tuple[str, ...] | None:
    """``path``'s parts below ``root``, lexically, or None when it is not
    below it. A string prefix first: on a lookup's walk of a 2,000-file KB a
    ``relative_to`` was a third of the cost (#135)."""
    top = os.fspath(root)
    prefix = top if top.endswith(os.sep) else top + os.sep
    name = os.fspath(path)
    if name.startswith(prefix):
        rest = name[len(prefix) :]
        parts = tuple(rest.split(os.sep)) if rest else None
        # ``..`` is a way out, not a hidden part: collapsed below, so a path
        # that leaves the root is ``outside`` (KBFiles' refusal), never ``hidden``.
        if parts is None or ".." not in parts:
            return parts
    top_n = os.path.normpath(os.path.abspath(top))
    name_n = os.path.normpath(os.path.abspath(name))
    try:
        if os.path.commonpath([top_n, name_n]) != top_n or top_n == name_n:
            return None
    except ValueError:  # another drive on Windows
        return None
    return tuple(os.path.relpath(name_n, top_n).split(os.sep))


def reason_for_folders(parts: tuple[str, ...]) -> str | None:
    """The folder half of the rule: ``hidden`` or ``templates`` for the first
    of ``parts`` the walk would not enter (a file name is a part too: ``.x.md``
    is hidden), else None."""
    if any(part.startswith(".") for part in parts):
        return HIDDEN
    if "_templates" in parts:
        return TEMPLATES
    return None


def reason_for_parts(parts: tuple[str, ...]) -> str | None:
    """The rule on parts below the root: None when listed, else the reason."""
    folder = reason_for_folders(parts)
    if folder is not None:
        return folder
    name = parts[-1]
    if name.lower() == "readme.md":
        return README
    if not name.endswith(".md"):
        return NOT_MARKDOWN
    return None


def unlisted_reason(root: str | os.PathLike[str], path: str | os.PathLike[str]) -> str | None:
    """Why the walk would not list ``path`` as an entry file of the KB at
    ``root``: None when it would; ``readme``, ``hidden``, ``templates`` or
    ``not_markdown``; or ``outside`` for a path not below the root. Lexical:
    nothing on disk is read, and an in-KB link's alias rule is the walk's
    own (``KBRepository.alias_target``), not this one's."""
    parts = parts_below(root, path)
    if parts is None:
        return OUTSIDE
    return reason_for_parts(parts)


def is_listed(root: str | os.PathLike[str], path: str | os.PathLike[str]) -> bool:
    """Whether the walk lists ``path`` by this rule."""
    return unlisted_reason(root, path) is None


def why(reason: str) -> str:
    """The reason in words, for a message."""
    return _WHY.get(reason, reason)


def unlisted_message(rel: str, reason: str, source: str | None = None) -> str:
    """One sentence for a write refused because ``rel`` would not be listed:
    the KB-relative path, why, and what chose it."""
    chose = f" ({source})" if source else ""
    return (
        f"Refusing to write the entry at '{rel}'{chose}: the index walk does not list "
        f"that path ({why(reason)}), so the next `pyrite index sync` would drop the "
        "entry. Choose a folder and file name the walk lists."
    )


def kept_in_place(root: str | os.PathLike[str], target: str | os.PathLike[str]) -> str | None:
    """Why a move of an entry file to ``target`` does not happen, or None
    when the walk lists ``target`` (or it is not below the root, which
    ``KBFiles`` refuses on its own). The templated move's ``not_moved`` and
    the reconcile's ``errors`` say this; the file stays where it is."""
    reason = unlisted_reason(root, target)
    if reason is None or reason == OUTSIDE:
        return None
    rel = "/".join(parts_below(root, target) or ())
    return (
        f"the file keeps its place: the index walk does not list `{rel}` ({why(reason)}), "
        "so the next `pyrite index sync` would drop the entry"
    )


def require_listed(
    root: str | os.PathLike[str],
    path: str | os.PathLike[str],
    *,
    source: str | None = None,
    errors: list[dict] | None = None,
) -> None:
    """Raise ``ValidationError`` (``VALIDATION_FAILED``) when the walk would
    not list ``path``. A path not below the root is left to ``KBFiles``,
    whose ``OUTSIDE_KB_ROOT`` refusal is the answer for it."""
    reason = unlisted_reason(root, path)
    if reason is None or reason == OUTSIDE:
        return
    rel = "/".join(parts_below(root, path) or ())
    raise ValidationError(unlisted_message(rel, reason, source), errors=errors)
