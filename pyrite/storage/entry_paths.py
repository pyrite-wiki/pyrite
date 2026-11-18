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
- ``templates``: a part is ``_templates`` in any letter case, on every disk
  (the template service's scaffolds), or another spelling that folds to it;
- ``readme``: the file name folds to ``readme.md`` (a README is the user's,
  assumed to carry no entry);
- ``not_markdown``: the file name does not end in ``.md``, case-sensitively,
  so ``x.MD`` is skipped on every OS, not only where ``rglob`` is
  case-sensitive.

"Folds" is ``fold``: the spelling a filesystem may treat as the same name.
Case (APFS and NTFS are case-insensitive by default), Unicode case folding
(U+017F folds to ``s``, CaseFolding.txt), canonical normalisation (APFS treats
NFC and NFD as one name), and the trailing dots and spaces NTFS drops through
the Win32 API. The two reserved names are tested on the folded name, never on
a probe of the disk, so a writer's spelling and the disk's spelling of the
same folder get the same answer whatever the filesystem's rules (#1552 round
1: ``subdirectory: memos/_TeMpLaTeS`` was written into ``memos/_templates/``
on APFS). The rule: the walk skips a folder named ``_templates`` in any
letter case, on every disk (maintainer, 2026-11-17), and the other spellings
that fold to it, a trailing dot or space (``_templates.``) or a case-fold
variant (``_templateſ``). A disk keeps the spelling a folder was made with, so
a ``_Templates`` folder is skipped on APFS and NTFS as on a case-sensitive
disk. At the next sync the
rows of the entries in such a folder are retired, and ``index health`` names
each of its files that states an ``id:`` under ``unlisted_entry_files``, with
reason ``templates``; to keep them, rename the folder or move the entries out.
These are the files ``docs/pinning-entry-ids.md`` promises
Pyrite does not index; a hand-kept ``README.md``, dot-folder or
``_templates/`` file stays the user's.

Not folded: an NTFS 8.3 short name (``_TEMPL~1``), which no lexical rule can
see; a writer would have to name one.

Leaf module: imports nothing from ``pyrite.storage``, so ``repository.py``,
``entry_writer.py`` and the services can all ask it.
"""

from __future__ import annotations

import os
import unicodedata

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

#: The reasons ``index health`` reports a skipped file under
#: ``unlisted_entry_files`` (#1552). ``not_markdown`` is not among them: the
#: walk's ``rglob("*.md")`` meets no such file on POSIX; on Windows it matches
#: ``x.MD``, which this rule then skips, unreported (a name no writer places).
HEALTH_REASONS = (README, HIDDEN, TEMPLATES)

_WHY = {
    README: "a file named README.md (in any case) is the user's, not an entry",
    HIDDEN: "a part starting with '.' is hidden",
    TEMPLATES: "`_templates/`, in any case, holds the template scaffolds",
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


def fold(part: str) -> str:
    """The name a filesystem may take ``part`` to be: case-folded,
    canonically normalised, and without the trailing dots and spaces NTFS
    drops. Two spellings that fold alike may be one name on disk, so a rule
    about a reserved name is tested on this."""
    if part.isascii():
        return part.lower().rstrip(". ")
    folded = unicodedata.normalize("NFD", part).casefold()
    return unicodedata.normalize("NFC", folded).rstrip(". ")


def fold_parts(parts: tuple[str, ...]) -> tuple[str, ...]:
    """Fold each part of ``parts`` with ``fold``."""
    return tuple(fold(p) for p in parts)


def folded_path_key(root: str | os.PathLike[str], path: str | os.PathLike[str]) -> tuple[str, ...]:
    """Folded parts of ``path`` below ``root``; falls back to folded parts
    of the path itself if outside or relative."""
    parts = parts_below(root, path)
    if parts is not None:
        return fold_parts(parts)
    return fold_parts(tuple(p for p in os.fspath(path).replace("\\", "/").split("/") if p))


def reason_for_folders(parts: tuple[str, ...]) -> str | None:
    """The folder half of the rule: ``hidden`` or ``templates`` for the first
    of ``parts`` the walk would not enter (a file name is a part too: ``.x.md``
    is hidden), else None."""
    if any(part.startswith(".") for part in parts):
        return HIDDEN
    if any(fold(part) == "_templates" for part in parts):
        return TEMPLATES
    return None


def reason_for_parts(parts: tuple[str, ...]) -> str | None:
    """The rule on parts below the root: None when listed, else the reason."""
    folder = reason_for_folders(parts)
    if folder is not None:
        return folder
    name = parts[-1]
    if fold(name) == "readme.md":
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


def unlisted_message(
    rel: str, reason: str, source: str | None = None, remedy: str | None = None
) -> str:
    """One sentence for a write refused because ``rel`` would not be listed:
    the KB-relative path, why, what chose it, and what the caller can change
    (``remedy``; by default, the folder and file name)."""
    chose = f" ({source})" if source else ""
    return (
        f"Refusing to write the entry at '{rel}'{chose}: the index walk does not list "
        f"that path ({why(reason)}), so the next `pyrite index sync` would drop the "
        f"entry. {remedy or 'Choose a folder and file name the walk lists.'}"
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
    remedy: str | None = None,
    errors: list[dict] | None = None,
) -> None:
    """Raise ``ValidationError`` (``VALIDATION_FAILED``) when the walk would
    not list ``path``. A path not below the root is left to ``KBFiles``,
    whose ``OUTSIDE_KB_ROOT`` refusal is the answer for it."""
    reason = unlisted_reason(root, path)
    if reason is None or reason == OUTSIDE:
        return
    rel = "/".join(parts_below(root, path) or ())
    raise ValidationError(unlisted_message(rel, reason, source, remedy), errors=errors)
