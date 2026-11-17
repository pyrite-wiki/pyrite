"""
KB Repository - File-based Storage

Handles reading and writing entries to/from markdown files.
Each KB is a directory of markdown files with YAML frontmatter.
"""

import io
import logging
import os
import re
import stat
from collections.abc import Callable, Iterator, Mapping, Set
from dataclasses import dataclass
from pathlib import Path

from ..config import KBConfig
from ..exceptions import (
    ConflictError,
    EntryExistsError,
    EntryLoadError,
    EntryNotFoundError,
    FrontmatterError,
    ValidationError,
    kb_origin,
    outside_text,
)
from ..migrations import get_migration_registry, load_plugin_migrations
from ..models import Entry, EventEntry
from ..models.collection import CollectionEntry
from ..models.core_types import (
    could_hold_id,
    entry_from_frontmatter,
    explicit_entry_id,
    read_entry_id,
)
from ..schema import CORE_TYPES, expand_subdirectory_template
from ..utils.atomic_write import HardLinked
from ..utils.frontmatter import Frontmatter, describe, split_frontmatter
from ..utils.kb_files import KBFileReadOutsideRootError, KBFiles
from ..utils.yaml import load_yaml
from . import entry_paths
from .entry_writer import (
    FileChanged,
    create_entry_text,
    publish_entry_text,
    raw_scalar,
    remove_entry_file,
    updated_at_stamp,
)

logger = logging.getLogger(__name__)

#: A ``{field}`` placeholder of a templated subdirectory (``st/{phase}``),
#: as ``expand_subdirectory_template`` reads it.
_TEMPLATE_FIELD_RE = re.compile(r"\{(\w+)\}")

#: How many times a rename re-reads a linking file whose bytes changed under
#: it before giving up (the same bound as an update's, ADR-0042 decision 10).
_REWRITE_ATTEMPTS = 6


@dataclass(frozen=True)
class _Rewrite:
    """A linking file's wikilink rewrite: the new text, how many wikilinks it
    renames, and ``partial``, the reason when the frontmatter could not be
    patched as values and wikilinks there still name the old id (None when
    every wikilink in the file is rewritten)."""

    text: str
    count: int
    partial: str | None = None


@dataclass(frozen=True)
class _Published:
    """What publishing one linking file's rewrite did: the wikilinks written,
    the ``partial`` reason carried over (or found on a re-read), or the
    reason the file was skipped."""

    count: int
    partial: str | None
    skipped: str | None


def _rewrite_wikilinks(text: str, old_id: str, new_id: str) -> _Rewrite:
    """``text`` with every ``[[old_id]]`` and ``[[old_id|alias]]`` naming
    ``new_id``, and how many were rewritten, as operations on the file's own
    bytes (#955, B6 P4): a ``ReplaceBody`` where the body holds one, a ``Set``
    of each frontmatter value that holds one; every other byte, a comment
    included, stays. A file with no frontmatter is rewritten as text. A
    frontmatter that cannot be patched as values (it does not parse, or
    ``apply`` refuses it) keeps its bytes while the body is rewritten, and the
    result says so when a wikilink there stays (#978 delta review, round 1:
    a partly rewritten file is reported, never counted as done)."""
    import re

    bare = re.compile(rf"\[\[{re.escape(old_id)}\]\]")
    aliased = re.compile(rf"\[\[{re.escape(old_id)}\|([^\]]*)\]\]")

    def substitute(value: str) -> tuple[str, int]:
        count = len(bare.findall(value)) + len(aliased.findall(value))
        if not count:
            return value, 0
        value = bare.sub(f"[[{new_id}]]", value)
        value = aliased.sub(lambda m: f"[[{new_id}|{m.group(1)}]]", value)
        return value, count

    split = split_frontmatter(text)
    if not isinstance(split, Frontmatter):
        new_text, count = substitute(text)
        return _Rewrite(new_text, count)
    try:
        new_text, count = _rewrite_as_values(text, split, substitute)
        return _Rewrite(new_text, count)
    except Exception as e:  # noqa: BLE001 - the fallback below is the point
        # The frontmatter cannot be patched as values: the wikilinks in the
        # body alone are rewritten, as text after the closing fence, and the
        # frontmatter's bytes stay. A linking file never fails the rename of
        # the entry it links to (#978 review, item 1); one whose frontmatter
        # still names the old id is reported as partial.
        tail, count = substitute(text[split.close_end :])
        _same, left = substitute(split.text)
        partial = None
        if left:
            partial = (
                "the frontmatter could not be patched as values "
                f"({type(e).__name__}: {outside_text(e)}); "
                f"{left} wikilink(s) there still name '{old_id}'"
            )
        return _Rewrite(text[: split.close_end] + tail, count, partial)


def _hard_link_fault(path: Path) -> str | None:
    """Why a compare-and-replace of ``path`` would be refused before any
    linker is written: a file with more than one hard link is refused, in a
    KB under git or not (``HardLinked``: another name for the same inode may
    be outside the KB, and under git would take another lock). None
    when the file can be published."""
    try:
        # lstat: the name's own link count. Called after KBFiles read the
        # file, but a stat that followed a link out would report the outside
        # inode's count.
        links = path.lstat().st_nlink
    except OSError:
        return None
    if links <= 1:
        return None
    return _hard_linked(links)


def _hard_linked(links: int) -> str:
    return (
        f"it has {links} hard links; another name for the same file may be outside "
        "the KB, and would take another lock"
    )


def _fault_reason(e: BaseException) -> str:
    from pyrite.utils.atomic_write import HardLinked
    from pyrite.utils.kb_files import KBFileOutsideRootError

    if isinstance(e, HardLinked):
        return _hard_linked(e.links)
    if isinstance(e, KBFileOutsideRootError):
        return "it resolves outside the KB root, through a symbolic link"
    return f"{type(e).__name__}: {outside_text(e)}"


def _read_fault_reason(path: Path, e: BaseException) -> str:
    """``_fault_reason`` for a read inside the KB (``_read_inside``), whose one
    refusal covers a link out and a hard link: a hard link is named as one."""
    from pyrite.utils.kb_files import KBFileOutsideRootError

    if isinstance(e, KBFileOutsideRootError):
        return _hard_link_fault(path) or _fault_reason(e)
    return _fault_reason(e)


def _rewrite_as_values(
    text: str, split: Frontmatter, substitute: Callable[[str], tuple[str, int]]
) -> tuple[str, int]:
    """The wikilink rewrite as operations: a ``ReplaceBody`` where the body
    holds one, a ``Set`` of each frontmatter value that holds one."""
    from .file_operations import FrontmatterSpan, ReplaceBody, Set, apply

    ops: list[object] = []
    total = 0
    new_body, count = substitute(split.body)
    if count:
        ops.append(ReplaceBody(new_body))
        total += count

    def walk(value: object, path: tuple[object, ...]) -> None:
        nonlocal total
        if isinstance(value, str):
            new_value, n = substitute(value)
            if n:
                ops.append(Set(path, new_value))
                total += n
        elif isinstance(value, dict):
            for k, v in value.items():
                walk(v, (*path, k))
        elif isinstance(value, list):
            for i, v in enumerate(value):
                walk(v, (*path, i))

    values = load_yaml(split.text)
    if isinstance(values, dict):
        for key, value in values.items():
            walk(value, (key,))
    if not ops:
        return text, 0
    span = FrontmatterSpan(split.yaml_start, split.close_start, split.close_end)
    new_text, _report = apply(text, ops, span)
    return new_text, total


def _is_regular_file(path: Path) -> bool:
    """``path`` (symlinks followed) is a regular file that can be opened
    without blocking; False for a FIFO, a directory, a socket or a path that
    cannot be stat'ed."""
    try:
        return stat.S_ISREG(path.stat().st_mode)
    except OSError:
        return False


def _text_of(raw: bytes) -> str:
    """``raw`` decoded as ``Path.read_text(encoding="utf-8")`` decodes a file:
    strict UTF-8, universal newlines. The one decoding for a reader that has
    the bytes (``_inside_text``, ``load_entry_from_bytes``), so what it
    parses is what a read of the file would give."""
    return io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8").read()


def new_entry_text(entry: Entry) -> str:
    """The text of a new entry file: ``---\nid: <id>\n---\n`` and the body, then
    one ``Set`` per key of the type's file frontmatter, in order, through
    ``file_operations.apply`` (#955, B6 C2). ``id`` is the first key; the
    rest follow ``_frontmatter_for_file`` (``to_frontmatter`` less the
    default-valued keys a loaded source lacked, #46). No YAML emitter sees
    the document: apply's own emitter spells each value.
    """
    from .file_operations import FrontmatterSpan, Set, apply

    meta = entry._frontmatter_for_file()
    raw_id = meta.get("id", entry.id)
    entry_id = raw_id if isinstance(raw_id, str) else str(raw_id)
    body = (entry.body or "").rstrip("\n")
    skeleton = f"---\nid: {_id_text(entry_id)}\n---\n" + (f"\n{body}\n" if body else "")
    split = split_frontmatter(skeleton)
    assert isinstance(split, Frontmatter), skeleton
    span = FrontmatterSpan(split.yaml_start, split.close_start, split.close_end)
    ops = [Set(key, _plain_for_file(value)) for key, value in meta.items() if key != "id"]
    if _id_text(entry_id) != entry_id:
        # The skeleton holds the placeholder: the id is written by apply's
        # emitter, which quotes a scalar YAML would misread (`yes`, `123`,
        # `2024-01-01`), so the file reads back the id asked for (#974 cold
        # read: the corrective Set was gated on a condition no string met,
        # and three such ids all became `id: placeholder`).
        ops.insert(0, Set("id", _id_value(entry_id)))
    text, _report = apply(skeleton, ops, span)
    return text


def _renamed_text(text: str, split: Frontmatter, new_id: str) -> str:
    """``text`` with ``id: <new_id>`` set (added as the last frontmatter line
    where the file had none) and ``updated_at`` stamped where the file
    carries it, through ``apply`` (#955, B6 P4)."""
    from .file_operations import FrontmatterSpan, Set, apply

    span = FrontmatterSpan(split.yaml_start, split.close_start, split.close_end)
    ops: list[object] = [Set("id", _id_value(new_id))]
    values = load_yaml(split.text)
    if isinstance(values, dict) and "updated_at" in values:
        stamp = updated_at_stamp(values["updated_at"], raw_scalar(split.text, "updated_at"))
        if stamp is not None:
            ops.append(Set("updated_at", stamp))
    new_text, _report = apply(text, ops, span)
    return new_text


#: Booleans to a YAML 1.1 reader by the spec, which PyYAML's own tables let
#: through (so apply's plain rule does too, `utils.yaml._is_yaml11_ambiguous`
#: says why): an id spelled so is written quoted, by a create and a rename as
#: `ids pin` already quotes it (#974 ride-along, the delta review of #978).
_YAML11_BOOL_LETTERS = frozenset({"y", "Y", "n", "N"})


def _id_value(entry_id: str) -> str:
    """The id as the value a ``Set("id", ...)`` writes: quoted whatever the
    plain rule says for the four letters above, the string itself otherwise
    (apply's emitter quotes what YAML would misread)."""
    from .file_operations import QuotedText

    return QuotedText(entry_id) if entry_id in _YAML11_BOOL_LETTERS else entry_id


def _id_text(entry_id: str) -> str:
    """``id`` on the skeleton as a plain scalar, when the string written bare
    reads back as itself under YAML 1.2 and 1.1; otherwise the placeholder
    ``new_entry_text`` replaces through apply."""
    from .file_operations import _plain_ok  # the one spelling rule for scalars

    if entry_id in _YAML11_BOOL_LETTERS:
        return "placeholder"
    if _plain_ok(entry_id, False, False) and entry_id == entry_id.strip():
        return entry_id
    return "placeholder"


def _plain_for_file(value: object) -> object:
    """A type's value as plain data: an Enum its value, a Link or Source its
    mapping, containers recursively (what ``to_frontmatter`` already does for
    most, kept here for a class that emits objects)."""
    from enum import Enum

    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _plain_for_file(value.to_dict())
    if isinstance(value, dict):
        return {k: _plain_for_file(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_plain_for_file(v) for v in value]
    return value


def load_problem(field_path: str, error: str) -> dict[str, str]:
    """One refused field of a single-entry read (``load_problems``): the file
    key, ``rule: load``, a severity and the loader's message."""
    return {
        "field": field_path,
        "rule": "load",
        "severity": "warning",
        "message": f"{field_path}: {error}",
    }


def _loader_refuses(fm: dict) -> str | None:
    """The loader's message when the type ``fm`` declares refuses it, else None."""
    try:
        entry_from_frontmatter(fm, "")
    except Exception as e:  # noqa: BLE001 - a probe of the type's own loader
        # A loader's text (a plugin's, a library's) reaches the refusal's
        # message and its fields: a network caller gets Pyrite's own text
        # (ADR-0052 N3, `outside_text`); the CLI and stdio MCP get the loader's.
        return outside_text(e, already_logged=True) or type(e).__name__
    return None


def _without(fm: dict, refused: Mapping[str, str]) -> dict:
    """``fm`` without the refused keys (``rank``, or ``metadata.rank`` inside
    a ``metadata:`` mapping)."""
    kept = dict(fm)
    nested = dict(kept["metadata"]) if isinstance(kept.get("metadata"), dict) else None
    for key in refused:
        head, _, sub = key.partition(".")
        if sub and head == "metadata" and nested is not None:
            nested.pop(sub, None)
        else:
            kept.pop(key, None)
    if nested is not None and "metadata" in kept:
        kept["metadata"] = nested
    return kept


def _refused_fields(fm: dict, body: str) -> dict[str, str]:
    """``{file key: loader message}`` for each key the declared type's loader
    refuses on its own, or ``{}`` when the refusal is not isolated to fields.

    A key is refused when the loader raises on the base keys (``type``,
    ``id``, ``title``) plus that key alone; a ``metadata:`` mapping is probed
    key by key, so ``metadata.rank`` is named as such. Isolated means the base
    keys load alone and the file loads without the refused keys; otherwise
    (a base field, a cross-field rule, a key the loader requires) it is
    ``{}``. Runs only for a file that already failed to load.
    """
    base = {k: fm[k] for k in ("type", "id", "title") if k in fm}
    if _loader_refuses(base) is not None:
        return {}
    refused: dict[str, str] = {}
    for key, value in fm.items():
        if key in base:
            continue
        whole = _loader_refuses({**base, key: value})
        if whole is None:
            continue
        if key == "metadata" and isinstance(value, dict):
            subs = {
                f"metadata.{sub}": msg
                for sub, sub_value in value.items()
                if (msg := _loader_refuses({**base, "metadata": {sub: sub_value}})) is not None
            }
            if subs:
                refused.update(subs)
                continue
        refused[str(key)] = whole
    if not refused:
        return {}
    try:
        entry_from_frontmatter(_without(fm, refused), body)
    except Exception:  # noqa: BLE001 - the rest still does not load: not isolated
        return {}
    return refused


class KBRepository:
    """
    File-based repository for a single knowledge base.

    Handles:
    - Loading entries from markdown files
    - Saving entries to markdown files
    - Listing/iterating over entries
    - File path management
    """

    def __init__(self, kb_config: KBConfig):
        self.config = kb_config
        self.path = kb_config.path
        self.kb_type = kb_config.kb_type
        self.name = kb_config.name

    def _get_entry_class(self) -> type:
        """Get the default entry class for loading files."""
        # All files are loaded by parsing frontmatter and dispatching
        # to the correct type based on entry_type field
        return Entry

    def _read_inside(self, file_path: Path) -> bytes:
        """The bytes of ``file_path``, read only where it resolves inside this
        KB's root and has one name (``KBFiles.read_bytes``). Every load of an
        entry reads through here, so neither the index nor a by-id load holds
        bytes from outside the KB: a link out of it, or a hard link, raises
        ``KBFileReadOutsideRootError`` (``OUTSIDE_KB_ROOT``) and nothing is
        read. ``FileNotFoundError`` when the file is gone."""
        raw = self._inside_bytes(file_path)
        if raw is None:
            raise FileNotFoundError(str(file_path))
        return raw

    def _inside_bytes(self, file_path: Path) -> bytes | None:
        """``KBFiles.read_bytes`` for this KB: the bytes, or None when nothing
        is there to read (absent, or not a regular file: a FIFO or a device,
        opened without blocking and never read). A link out of the KB, or a
        hard link, raises ``KBFileReadOutsideRootError`` whatever it points
        at, so no answer built on this depends on what lies outside."""
        return KBFiles(self.path).read_bytes(file_path)

    def _load_entry(self, file_path: Path, text: str | None = None) -> Entry:
        """Load an entry from a file as the type its frontmatter declares: THE
        strict load, the only kind a writer can call (ADR-0042 decision 8a).

        ``text``: the file's text, already read (``load_entry_from_bytes``);
        read here when None.

        Returns an entry of the declared type, fully loaded, or raises. A read
        error (``OSError``, ``UnicodeError``) propagates as itself, so the
        reconcile can tell an unreadable file from one with bad content.
        Frontmatter that is missing, does not parse or is not a mapping raises
        ``FrontmatterError``; a type loader that raises is ``EntryLoadError``
        (``ENTRY_UNREADABLE``) naming the file, the declared type and, when the
        refusal is isolated to fields, those fields. No path substitutes
        another type: this used to fall back to ``EventEntry`` of the same
        file, which indexed a ``backlog_item`` with a bad ``rank`` as an
        ``event`` and dropped it from its typed views with nothing reported
        (#1261). Nor does it return a model of the declared type that holds
        defaults for the refused values: a guard would judge a value the file
        does not hold (#1311 rounds 1-2). The single-entry read's partial view
        is ``load_for_read``, never a writer's.
        """
        # Read once, inside the KB, before any parsing (``_read_inside``): a
        # link out of the KB or a hard link is refused before a byte is read.
        if text is None:
            text = _text_of(self._read_inside(file_path))
        try:
            fm, body = self._frontmatter_of(text)
            try:
                entry = entry_from_frontmatter(fm, body)
            except FrontmatterError:
                raise
            except Exception as e:
                raise self._load_error(file_path, fm, body, e) from e
        except FrontmatterError as e:
            # One line, never a traceback: per-file tracebacks drowned bulk
            # sync output (Tier A 1080); the reconcile reports the file under
            # `malformed` with this message.
            logger.warning("Entry load failed for %s: %s", file_path, e)
            raise
        return self._with_references(entry, fm)

    def _frontmatter_of(self, text: str) -> tuple[dict, str]:
        """``(frontmatter, body)`` of an entry file's text, migrated, as the
        loader reads it; ``FrontmatterError`` when there is no mapping."""
        split = split_frontmatter(text)
        if not isinstance(split, Frontmatter):
            raise FrontmatterError(f"Invalid entry format: {describe(split)}")
        # This KB's bytes: a caller holding a read grant on it is shown the
        # parser's quote of them (D4); anyone else, the operator's message.
        fm = load_yaml(split.text, origin=kb_origin(self.name))
        if fm is None:
            fm = {}  # `---\n---` and `null`: an empty mapping, as `read_entry_id` reads it
        if not isinstance(fm, dict):
            raise FrontmatterError("Invalid entry format: the frontmatter is not a mapping")
        # `fm` is the entry's frontmatter and nothing else. It used to get
        # `body` and `file_path` injected here, but no from_frontmatter reads
        # either (body arrives as the positional argument, file_path is set by
        # the caller), while capture_extra_frontmatter reads this same dict to
        # decide which keys the class did not declare -- so the two internals
        # were recorded as "unknown frontmatter" and written back into the
        # file on the next save (#46).
        return self._maybe_migrate(fm), split.body

    @staticmethod
    def _with_references(entry: Entry, fm: dict) -> Entry:
        # Preserve references in metadata if present in frontmatter
        # (typed entries drop unknown fields during from_frontmatter)
        if "references" in fm and fm["references"]:
            if not hasattr(entry, "metadata") or not entry.metadata:
                entry.metadata = {}
            if "references" not in entry.metadata:
                entry.metadata["references"] = fm["references"]
        return entry

    def _load_error(self, file_path: Path, fm: dict, body: str, cause: Exception) -> EntryLoadError:
        """The refusal for a file its declared type's loader raised on: the
        KB-relative path, the declared type, the refused fields (when isolated)
        and the loader's message, in one message every surface shows."""
        declared = fm.get("type")
        entry_type = str(declared) if declared else None
        # As `_loader_refuses`: the loader's text only for the local process.
        # Logged here when withheld, so "the server log has the details" holds.
        loader_message = outside_text(cause) or type(cause).__name__
        fields = _refused_fields(fm, body)
        rel = self._rel(file_path)
        named = ", ".join(fields) if fields else "not isolated to a field"
        message = (
            f"{rel} does not load as its type `{entry_type or 'note'}` "
            f"(refused: {named}; {loader_message}). Pyrite reads it but writes "
            "nothing to it until the file is fixed by hand."
        )
        return EntryLoadError(
            message,
            path=file_path,
            entry_type=entry_type,
            rel_path=rel,
            fields=fields,
            loader_message=loader_message,
        )

    def load_for_read(self, file_path: Path, raw: bytes) -> tuple[Entry, list[dict[str, str]]]:
        """The single-entry read's view of a file: ``(entry, load_problems)``.

        ONLY ``KBService._entry_file`` calls this
        (``tests/test_refused_fields_stay_absent.py`` fails otherwise), and its
        entry never leaves that seam: callers get the read's dict. A file that
        loads gives ``(entry, [])``. A file its type's loader refuses in some
        fields (ADR-0042 decision 8a, point 5) gives an entry of the DECLARED
        type built without those keys, and one ``load_problem`` per refused
        key. That model holds the type's default where the file's value was
        refused, so the read leaves each named key out
        (``IndexManager.read_fields(refused=)``); it is for presenting the
        accepted fields, never for a write, a validator or a hook. A refusal
        no field explains (a base field, a cross-field rule, a missing key the
        loader requires) raises the ``EntryLoadError`` as the strict load does.
        """
        try:
            return self.load_entry_from_bytes(file_path, raw), []
        except EntryLoadError as error:
            if not error.fields:
                raise
            fm, body = self._frontmatter_of(_text_of(raw))
            try:
                entry = entry_from_frontmatter(_without(fm, error.fields), body)
            except Exception:
                raise error from None
            entry = self._with_references(entry, fm)
            entry.kb_name = self.name
            entry.file_path = file_path
            return entry, [load_problem(k, m) for k, m in error.fields.items()]

    def _maybe_migrate(self, fm: dict) -> dict:
        """Apply pending schema migrations to frontmatter if needed."""
        if not getattr(self.config, "kb_yaml_present", False):
            return fm

        entry_type = fm.get("type", "")
        if not entry_type:
            return fm

        type_schema = self.config.kb_schema.get_type_schema(entry_type)
        if not type_schema or type_schema.version == 0:
            return fm

        entry_sv = int(fm.get("_schema_version", 0))
        if entry_sv >= type_schema.version:
            return fm

        load_plugin_migrations()
        registry = get_migration_registry()
        if not registry.has_migrations(entry_type):
            return fm

        try:
            fm = registry.apply(entry_type, fm, entry_sv, type_schema.version)
        except ValueError:
            logger.warning(
                "Migration failed for %s (v%d -> v%d)",
                entry_type,
                entry_sv,
                type_schema.version,
                exc_info=True,
            )

        return fm

    @staticmethod
    def _validate_entry_id(entry_id: str) -> None:
        """Refuse ids that are not a plain filename stem.

        An id becomes `<id>.md`, and ids arrive from callers: the REST import
        endpoint reads `id` out of the uploaded file, `pyrite rename` takes it
        from argv. `../../x` must not be able to name a file outside the KB.
        """
        if (
            not isinstance(entry_id, str)
            or not entry_id.strip()
            or entry_id.startswith(".")
            or any(c in entry_id for c in ("/", "\\", "\x00"))
        ):
            raise ValidationError(
                f"Invalid entry id {entry_id!r}: an id must be a plain filename "
                "(no path separators, no leading '.', not empty)"
            )

    def _contained(self, file_path: Path) -> Path:
        """Return file_path, or raise if it resolves outside the KB root.

        Defence in depth behind _validate_entry_id: subdirectory templates and
        `file_pattern` filenames are also built from entry data. The decision
        is ``KBFiles``' (``KBFileOutsideRootError``, ``OUTSIDE_KB_ROOT``), the
        same one the entry writer makes again at the write.
        """
        KBFiles(self.path).require(file_path)
        return file_path

    def _get_file_path(self, entry_id: str, subdir: str | None = None) -> Path:
        """Get file path for an entry."""
        self._validate_entry_id(entry_id)
        if subdir:
            return self._contained(self.path / subdir / f"{entry_id}.md")
        return self._contained(self.path / f"{entry_id}.md")

    def _resolve_file_path(
        self,
        entry: Entry,
        subdir: str | None,
        *,
        taken: Set[Path] = frozenset(),
        taken_keys: Set[tuple[str, ...]] = frozenset(),
    ) -> Path:
        """Resolve the file path for an entry, respecting file_pattern if set.

        ``taken``: paths an earlier item of the same batch has written (or
        would write in a dry run: ``KBService.bulk_create_entries``). The
        default name treats each as held by another id, as the real batch
        finds the file there, so a dry run picks the ``-N`` the real write
        picks (#1552 round 1). The paths in ``taken`` are compared with
        ``entry_paths.fold`` on every disk (#1623, maintainer decision
        2026-11-17), so on a case-insensitive and case-sensitive disk alike
        two items whose names differ only in case step the second to ``-2``
        (or refuse a ``file_pattern`` name), and dry-run and real writes agree.
        """
        schema = self.config.kb_schema
        type_schema = schema.get_type_schema(entry.entry_type)
        if type_schema:
            custom_name = type_schema.resolve_filename(entry)
            if custom_name:
                # file_pattern provides the full filename (with .md)
                if subdir:
                    path = self._contained(self.path / subdir / custom_name)
                else:
                    path = self._contained(self.path / custom_name)
                self._require_listed(path, entry, subdir, from_pattern=True)
                return path
        path = self._free_default_path(entry.id, subdir, taken=taken, taken_keys=taken_keys)
        self._require_listed(path, entry, subdir, from_pattern=False)
        return path

    def _require_listed(
        self, path: Path, entry: Entry, subdir: str | None, *, from_pattern: bool
    ) -> None:
        """Refuse a new entry path the index walk would not list (#1552), with
        ``VALIDATION_FAILED`` and before anything is written: the operator's
        ``kb.yaml`` (a static ``subdirectory``, a ``file_pattern``) or the
        entry's own field value (a templated ``subdirectory``) chose it, and
        neither is Pyrite's to rename. Where Pyrite chose the name (``<id>.md``)
        ``_free_default_path`` already chose a listed one. The message names
        the reserved part's source; a field value that caused it also gets an
        ``errors`` row ``{field, rule: "entry_path", got, message}`` (#1284).

        ``_prepare`` (so a dry run) and ``save`` both reach this through
        ``_resolve_file_path``, so the two answer alike."""
        reason = entry_paths.unlisted_reason(self.path, path)
        if reason is None or reason == entry_paths.OUTSIDE:
            return
        parts = entry_paths.parts_below(self.path, path) or ()
        rel = "/".join(parts)
        etype = entry.entry_type
        type_schema = self.config.kb_schema.get_type_schema(etype)
        declared = getattr(type_schema, "subdirectory", None) or ""
        folder_reason = entry_paths.reason_for_folders(parts[:-1]) is not None
        source: str | None = None
        remedy: str | None = None
        rows: list[dict] | None = None
        if folder_reason and declared and "{" in declared:
            for name in _TEMPLATE_FIELD_RE.findall(declared):
                value = expand_subdirectory_template("{" + name + "}", entry)
                if entry_paths.reason_for_folders((value,)) is not None:
                    got = getattr(entry, name, None)
                    if got is None:
                        got = (getattr(entry, "metadata", None) or {}).get(name)
                    source = f"field `{name}` is {got!r}, used by the subdirectory `{declared}` of type '{etype}'"
                    remedy = f"Give `{name}` another value."
                    message = entry_paths.unlisted_message(rel, reason, source, remedy)
                    rows = [{"field": name, "rule": "entry_path", "got": got, "message": message}]
                    break
        if source is None:
            if folder_reason and declared:
                source = f"kb.yaml `subdirectory: {declared}` of type '{etype}'"
                remedy = "Change the type's `subdirectory` in kb.yaml."
            elif folder_reason and subdir:
                source = f"the folder `{subdir}` of type '{etype}'"
            elif from_pattern and type_schema is not None:
                pattern = type_schema.file_pattern
                source = f"kb.yaml `file_pattern: {pattern}` of type '{etype}'"
                # The caller chose neither the folder nor the name: say what
                # they can change. A pattern whose own text the walk skips
                # (`.{id}.md`, `{id}.txt`) places no entry at all.
                literal = _TEMPLATE_FIELD_RE.sub("x", re.sub(r"\{(\w+):[^}]*\}", "x", pattern))
                if entry_paths.reason_for_parts((literal,)) is not None:
                    remedy = (
                        "The type's `file_pattern` cannot place any entry; change it in kb.yaml."
                    )
                else:
                    remedy = (
                        "The type's `file_pattern` cannot place this entry: change the "
                        "field values it uses (such as the title), or the `file_pattern` "
                        "in kb.yaml."
                    )
        entry_paths.require_listed(self.path, path, source=source, remedy=remedy, errors=rows)

    def _free_default_path(
        self,
        entry_id: str,
        subdir: str | None,
        *,
        taken: Set[Path] = frozenset(),
        taken_keys: Set[tuple[str, ...]] = frozenset(),
    ) -> Path:
        """``<id>.md`` for a new entry, or the first ``<id>-N.md`` when that
        path is held by a *different* id.

        A file's name is a location fixed at creation, never evidence of the
        id it holds (ADR-0038 decision 1, I6): after ``rename(a, b)`` the file
        ``a.md`` holds ``b``, and a hand edit of ``id:`` does the same. The id
        ``a`` is free, so a create of it must not be refused for that file. A
        path whose holder cannot be read stays taken (the create refuses), and
        so does one the KB would not read (a link out, a hard link): what such
        a file holds decides nothing."""
        path = self._get_file_path(entry_id, subdir)
        # A name the index walk skips (``readme.md`` in any case, #1552) is
        # taken as if another id held it: Pyrite chose this name, so it
        # chooses a listed one, and the id stays what the caller named. The
        # test is on the folded name (``entry_paths.fold``), never a probe of
        # the disk, so a case-insensitive filesystem answers the same.
        # A path in ``taken`` is held by an earlier item of the same
        # batch, never by this id (the batch refuses a repeated id first).
        # Compared by ``entry_paths.fold`` on every disk (#1623).
        if not taken_keys and taken:
            taken_keys = {entry_paths.folded_path_key(self.path, p) for p in taken}
        if (
            entry_paths.is_listed(self.path, path)
            and entry_paths.folded_path_key(self.path, path) not in taken_keys
        ):
            holder = self.read_id(path)[0] if self.present(path) else None
            if holder is None or holder == entry_id:
                return path
        for n in range(2, 1000):
            candidate = path.with_name(f"{entry_id}-{n}.md")
            if not entry_paths.is_listed(self.path, candidate):
                break  # the folder itself is skipped: no name in it helps
            if entry_paths.folded_path_key(self.path, candidate) in taken_keys:
                continue
            # A candidate that already holds this id is the entry's own file:
            # a caller that recomputes the path after the create (a plugin
            # printing it) gets the file written, not the next ``-N``.
            if not self.present(candidate) or self.read_id(candidate)[0] == entry_id:
                return candidate
        return path

    def holder_of_path(self, path: Path) -> str | None:
        """The id the file at ``path`` holds, for a message; None if unreadable
        or not the KB's to read (a link out, a hard link)."""
        return self.read_id(path)[0] if self.present(path) else None

    def present(self, path: Path) -> bool:
        """Whether ``path`` is taken in this KB, as ``KBFiles`` answers it
        (``KBFiles.present``): a name that leads out of the KB is taken
        whatever it points at, or whether its target exists."""
        return KBFiles(self.path).present(path)

    def _infer_subdir(self, entry: Entry) -> str | None:
        """Infer subdirectory for entries based on type.

        Checks (in order):
        1. KB schema (kb.yaml types with subdirectory)
        2. Core types (built-in type → subdirectory mapping)
        3. Plugin KB presets (the owning plugin's default for the type)
        4. Plugin subtypes (walk MRO to find parent core type)
        """
        entry_type = entry.entry_type

        # Check KB schema first — covers kb.yaml-defined types with subdirectory
        # Note: subdirectory="" means "KB root" (override core type default),
        # while no subdirectory key at all means "use default"
        schema = self.config.kb_schema
        type_schema = schema.get_type_schema(entry_type)
        if type_schema and type_schema.subdirectory is not None:
            if type_schema.subdirectory == "":
                return None  # Explicit override: place in KB root
            return type_schema.resolve_subdirectory(entry)

        # Check core types for subdirectory mapping
        if entry_type in CORE_TYPES:
            return CORE_TYPES[entry_type].get("subdirectory")

        # Plugin type: the owning plugin's preset knows where it belongs
        # (e.g. backlog_item -> backlog/). Ask before falling back to the
        # parent core type, or every NoteEntry subclass lands in notes/.
        from ..plugins import get_registry

        plugin_subdir = get_registry().get_type_default_subdirectory(
            entry_type, self.config.kb_type
        )
        if plugin_subdir:
            return plugin_subdir

        # Plugin subtype: walk MRO to find the parent core type's subdirectory
        from ..models.core_types import ENTRY_TYPE_REGISTRY

        for core_name, core_cls in ENTRY_TYPE_REGISTRY.items():
            if isinstance(entry, core_cls) and core_name in CORE_TYPES:
                return CORE_TYPES[core_name].get("subdirectory")
        return None

    def exists(self, entry_id: str) -> bool:
        """Whether a file the KB would read holds ``entry_id``.

        An existence answer (create's "already exists") counts only files
        ``KBFiles`` would read: inside the root, with one name. A file that is
        a link out of the KB, or has a second hard link, is not the KB's, so
        whether it holds the id is not the KB's to say (``find_file``)."""
        return self.find_file(entry_id) is not None

    def _lexically_inside(self, path: Path) -> bool:
        """``path`` stays under the KB root once ``..`` is collapsed.

        Lexical, not ``resolve()``: it refuses ``<kb>/../x`` (a ``collection-..``
        lookup reached one level up through ``rglob("..")``). Which copy of a
        KB a path names; a link out of it is refused by the read itself
        (``_read_inside``).
        """
        return self._is_lexically_inside(self.path, path)

    @staticmethod
    def _is_lexically_inside(root: str | os.PathLike[str], path: str | os.PathLike[str]) -> bool:
        """``path`` stays under ``root`` once ``..`` is collapsed (lexical)."""
        top = os.path.normpath(os.path.abspath(root))
        target = os.path.normpath(os.path.abspath(path))
        return os.path.commonpath([top, target]) == top

    def _filename_candidates(self, entry_id: str) -> Iterator[Path]:
        """Files whose NAME matches ``entry_id``; ``entry_id`` must already be
        a plain stem. A name is a hint for a fast lookup, never evidence of
        the id the file holds: ``find_file`` verifies each one."""
        root_path = self.path / f"{entry_id}.md"
        if self.present(root_path) and self._listed(root_path):
            yield root_path

        # By name, never as a pattern (`*` must not match, and so let
        # `kb_delete` remove, whichever entry globs first), and never by
        # whether a link's target exists (``_named``).
        for match in self._named(f"{entry_id}.md"):
            if match != root_path and self._listed(match):
                yield match

        # Collection entries (collection-<folder_name>)
        if entry_id.startswith("collection-"):
            folder_name = entry_id[len("collection-") :]
            if folder_name in ("", ".", ".."):
                return
            for subdir in self._named(folder_name):
                yaml_path = subdir / "__collection.yaml"
                if self.present(yaml_path):
                    yield yaml_path

    def _named(self, name: str) -> Iterator[Path]:
        """Every path under the KB whose last component is ``name``, found
        by the name alone: a link (to a file, to a folder, or dangling) is
        listed as the name it is, never followed or checked, and a folder
        that is a link is not walked into (``os.walk``). On Python 3.11
        ``Path.rglob`` with a literal name lists a link only when its target
        exists, so a listing would differ by what lies outside the KB; 3.12
        and later list it either way. One rule on every Python."""
        for dirpath, dirnames, filenames in os.walk(self.path):
            if name in filenames or name in dirnames:
                yield Path(dirpath) / name

    def id_of_file(self, file_path: Path) -> str | None:
        """The id ``file_path`` holds, by the loader's rule; None when it is
        not a readable entry.

        Markdown goes through ``read_entry_id`` -- the one function that
        answers this (ADR-0038 decision 1) -- with this KB's schema
        migrations applied first, as ``_load_entry`` applies them.
        """
        return self.read_id(file_path)[0]

    def read_id(self, file_path: Path, text: str | None = None) -> tuple[str | None, str | None]:
        """``id_of_file`` with the reason: ``(id, None)``, or ``(None, why)``
        where ``why`` is the loader's own message (``title must be a string,
        got a list``), or None when the file simply holds no entry.
        ``text``: the markdown file's text, when the caller has read it
        inside the KB.

        One read path (ADR-0052 N1): the file is read only as ``KBFiles``
        reads it (``_inside_bytes``). A file it refuses -- a link out of the
        KB, a file with a second hard link -- holds no id here, and the reason
        is the refusal whatever the link points at; a path with nothing to
        read (absent, a FIFO, a device) is "not a regular file", and is never
        opened in a way that blocks. So no id, existence or hint depends on a
        file outside the KB."""
        try:
            if text is None:
                raw = self._inside_bytes(file_path)
                if raw is None:
                    return None, "not a regular file"
                text = _text_of(raw)
            if file_path.name == "__collection.yaml":
                return self._collection_of(file_path, text).id or None, None
            return read_entry_id(text, migrate=self._maybe_migrate), None
        except Exception as e:
            logger.warning("Skipping unreadable file during id lookup: %s (%s)", file_path, e)
            # One line, KB-relative: a ruamel message spans lines and may name
            # an absolute path, and this text is shown to the user.
            why = (outside_text(e, already_logged=True).splitlines() or [""])[0].replace(
                f"{self.path}/", ""
            )
            return None, why or type(e).__name__

    def find_file(self, entry_id: str) -> Path | None:
        """The file that holds ``entry_id``, or None (ADR-0038 I8).

        A file named like the id is tried first and returned only if it holds
        the id (#483: a filename hit used to be returned unread, so
        ``delete('alpha')`` removed a file holding ``beta``). Otherwise every
        file ``list_files`` walks -- the same skip rules -- is read with
        ``id_of_file``, so an id derived from the title is found too (#484).
        When two files hold the id, the first found is returned; ``find_files``
        returns them all.

        An id that is not a plain filename stem (a path separator, a leading
        '.', NUL, empty) is never turned into a path: writes were already
        guarded by ``_validate_entry_id``, lookups were not, so ``delete`` and
        ``load`` accepted ``../x`` and reached outside the KB. Such an id can
        still match a file's *frontmatter* id below -- that scan only returns
        files it found inside the KB.

        Only files ``KBFiles`` would read count (``read_id``). A file that is
        a link out of the KB, or has a second hard link, never matches an id,
        so a get, update, delete or rename by id answers not-found whatever
        such a file holds (maintainer decision 2026-11-10): no answer differs
        by what a file outside the KB holds. There is no raw mode.
        """
        if not isinstance(entry_id, str) or not entry_id:
            return None
        try:
            self._validate_entry_id(entry_id)
            plain = True
        except ValidationError:
            plain = False

        tried: set[Path] = set()
        if plain:
            for candidate in self._filename_candidates(entry_id):
                tried.add(candidate)
                if self._lexically_inside(candidate) and self.read_id(candidate)[0] == entry_id:
                    return candidate

        return next(self._files_holding(entry_id, skip=tried), None)

    def _files_holding(
        self,
        entry_id: str,
        skip: Set[Path] = frozenset(),
        only: Set[Path] | None = None,
    ) -> Iterator[Path]:
        """Every file ``list_files`` walks that holds ``entry_id``, in walk
        order: the whole-KB scan behind ``find_file``, ``files_to_delete`` and
        a ``get`` miss.

        ``could_hold_id`` rules out from the raw text the files that state
        another id, so only the rest are parsed (#135: a miss parsed every
        file, about 1 ms each). Not in a KB with schema migrations to apply:
        one may set the id the text does not show, so there every file is
        parsed, as before.

        ``only`` narrows the walk to those files: a caller that knows which
        files the index could be wrong about (``KBService`` on a miss) passes
        them, and no other file is opened.

        Each file is read as ``KBFiles`` reads it, and one it refuses is
        skipped (``find_file``).
        """
        prefilter = not self._migrations_apply()
        for md_file in self.list_files():
            if md_file in skip or (only is not None and md_file not in only):
                continue
            text = self._inside_text(md_file)
            if text is None:
                continue
            # The prefilter only where no migration can set the id the raw
            # text does not show.
            if prefilter and not could_hold_id(text, entry_id):
                continue
            if self.read_id(md_file, text)[0] == entry_id:
                yield md_file

    def _inside_text(self, file_path: Path) -> str | None:
        """``file_path``'s text read as ``KBFiles`` reads it, or None when it
        refuses the file (a link out, a hard link), it is not a regular file,
        it is gone, or it is not UTF-8."""
        try:
            raw = self._inside_bytes(file_path)
            return None if raw is None else _text_of(raw)
        except (OSError, UnicodeDecodeError, KBFileReadOutsideRootError):
            return None

    def _migrations_apply(self) -> bool:
        """Whether ``_maybe_migrate`` can change any file's frontmatter here:
        a type ``kb.yaml`` gives a version, with migrations registered."""
        if not getattr(self.config, "kb_yaml_present", False):
            return False
        versioned = [name for name, t in self.config.kb_schema.types.items() if t.version]
        if not versioned:
            return False
        load_plugin_migrations()
        registry = get_migration_registry()
        return any(registry.has_migrations(name) for name in versioned)

    def _holds(self, file_path: Path, entry_id: str) -> str | None:
        """How ``file_path`` holds ``entry_id``: ``"explicit"`` (its ``id:``
        says so), ``"derived"`` (the id comes from its title), or None.

        Read only as ``KBFiles`` reads: a file it refuses (a link out, a hard
        link) holds nothing, and nothing in it is read, so a delete by id never
        removes or refuses it for what it holds."""
        if file_path.name == "__collection.yaml":
            # named by its folder, or its own ``id:``; read inside the KB
            held = self.read_id(file_path)[0]
            return "explicit" if held == entry_id else None
        text = self._inside_text(file_path)
        if text is None or self.read_id(file_path, text)[0] != entry_id:
            return None
        try:
            stated = explicit_entry_id(text)
        except Exception:
            return None
        return "explicit" if stated == entry_id else "derived"

    def files_to_delete(
        self,
        entry_id: str,
        indexed_path: Path | None = None,
        holders: list[Path] | None = None,
    ) -> list[Path]:
        """The files ``delete(entry_id)`` removes: only files certain to BE
        the entry (ADR-0038 I7).

        First the cheap candidates -- the index row's file and the files named
        like the id -- each verified. If one of them states the id in its
        ``id:``, every candidate stating it is removed, and nothing else is
        read: a copy elsewhere in the KB is reported by the reconcile (step 2,
        #494), not found by walking the KB on every delete.

        Otherwise the id is derived from a title (or not found cheaply), so
        the whole KB is walked. One file holding it is removed. Several files
        holding it, any of them by derivation (two notes titled "Draft", or
        untitled notes that all derive one id), cannot be told apart: the
        delete is refused with their paths, and an ``id:`` line settles it.

        ``holders`` are the files the reconcile found holding the id
        (``IndexManager.plan_reconcile``, #494). When there are several, the
        same rule applies to them before anything else: all stating it are
        removed, one deriving it refuses the delete.
        """
        if not isinstance(entry_id, str) or not entry_id:
            return []
        if holders and len(holders) > 1:
            held = {f: self._holds(f, entry_id) for f in holders if self._lexically_inside(f)}
            held = {f: how for f, how in held.items() if how}
            if len(held) > 1:
                self._refuse_if_derived(entry_id, held)
                return list(held)
        candidates: list[Path] = []
        # The index row's path is a candidate only where the walk would list
        # it: the whole of ``_listed``, the rule ``_filename_candidates``
        # applies. That excludes an in-KB link to another walked file (an
        # alias; dev's sync wrote such a row when the link sorted first, and
        # any row can be stale: read through, it would count as an explicit
        # holder, and the delete would remove the user's link and keep the
        # entry, #1262). It also excludes a path the walk skips: a README, a
        # path with a dot-prefixed part, one under ``_templates``, a name not
        # ending in ``.md``. No write places an entry there any more: every
        # writer asks ``entry_paths`` first, so a create titled "README"
        # writes ``readme-2.md`` and a path kb.yaml or a field chose is
        # refused (#1552). Only a file an earlier version wrote can still be
        # such a holder; it is not found by id (``index health`` names it).
        if (
            indexed_path is not None
            and self._lexically_inside(indexed_path)
            and self._listed(indexed_path)
        ):
            candidates.append(indexed_path)
        try:
            self._validate_entry_id(entry_id)
            candidates.extend(self._filename_candidates(entry_id))
        except ValidationError:
            pass
        held = {c: self._holds(c, entry_id) for c in dict.fromkeys(candidates)}
        explicit = [c for c, how in held.items() if how == "explicit"]
        if explicit:
            return explicit

        held = {f: self._holds(f, entry_id) for f in self._files_holding(entry_id)}
        held = {f: how for f, how in held.items() if how}
        if len(held) > 1:
            self._refuse_if_derived(entry_id, held)
        return list(held)

    def _refuse_if_derived(self, entry_id: str, held: dict[Path, str]) -> None:
        """Several files hold ``entry_id``; refuse unless every one states it."""
        if "derived" not in held.values():
            return
        paths = ", ".join(str(f.relative_to(self.path)) for f in sorted(held))
        raise ValidationError(
            f"Refusing to delete '{entry_id}': {len(held)} files hold it and at least "
            f"one derives it from its title, so which is the entry is not certain "
            f"({paths}). Add an `id:` line to the one to delete, or remove it by hand."
        )

    def not_found_hint(self, entry_id: str) -> str:
        """Why a lookup by a filename found nothing: the file named like the
        id holds another id. Empty when there is no such file."""
        try:
            self._validate_entry_id(entry_id)
        except ValidationError:
            return ""
        for candidate in self._filename_candidates(entry_id):
            # Only a file the KB would read: the hint quotes the id it holds.
            held = self.read_id(candidate)[0]
            if held and held != entry_id:
                rel = candidate.relative_to(self.path)
                return (
                    f"; no file holds id '{entry_id}', but {rel} holds '{held}' (its id "
                    f"comes from its title unless it has an `id:` line). Use '{held}', "
                    f"or add `id: {entry_id}` to the file"
                )
        return ""

    def read_for_write(self, entry_id: str) -> tuple[Path, bytes] | None:
        """``(path, bytes)`` of the file holding ``entry_id``, or None.

        The base of an update (ADR-0042 decision 10, #955): the bytes the
        operations are applied to and the bytes the publish compares the file
        with, read once. The entry's model is parsed from the same bytes
        (``load_entry_from_bytes``), so the type's reading and the text it
        validates are one read of the file.
        """
        file_path = self.find_file(entry_id)
        if not file_path:
            return None
        try:
            return file_path, self._read_inside(file_path)
        except KBFileReadOutsideRootError:
            # A hard-linked file keeps the write rule's own refusal (ADR-0042
            # A2, ``STORAGE_ERROR``) on every update surface; a link out of
            # the KB is ``OUTSIDE_KB_ROOT`` as before.
            links = file_path.stat(follow_symlinks=False).st_nlink
            if links > 1:
                raise HardLinked(os.path.relpath(file_path, self.path), links) from None
            raise

    def load(self, entry_id: str) -> Entry | None:
        """Load an entry by ID, strictly: None when no file holds the id.

        A file its declared type cannot load raises ``EntryLoadError``
        (``ENTRY_UNREADABLE``), never None: every caller that loads by id to
        write (delete, link, the task and qa services) is then refused before
        any hook runs, instead of reading None as "not found" or as "no entry
        to hand the hooks" (ADR-0042 decision 8a; ``delete_entry`` ran
        ``before_delete`` only ``if entry``). The file's other faults (YAML,
        bytes, permissions) are still None until #777 gives them the same code.
        """
        file_path = self.find_file(entry_id)
        if not file_path:
            return None

        try:
            entry = self._load_entry(file_path)
            entry.kb_name = self.name
            entry.file_path = file_path
            return entry
        except EntryLoadError:
            raise
        except Exception as e:
            logger.warning("Could not load %s: %s", file_path, e)
            return None

    def save(
        self,
        entry: Entry,
        subdir: str | None = None,
        *,
        touch_updated_at: bool = True,
        keep_filename: bool = False,
        exclusive: bool = False,
        taken: Set[Path] = frozenset(),
        taken_keys: Set[tuple[str, ...]] = frozenset(),
    ) -> Path:
        """
        Save an entry to file.

        Args:
            entry: The entry to save
            subdir: None infers the type folder; "" explicitly selects the KB root.
            touch_updated_at: Refresh ``updated_at`` as bookkeeping before
                writing. Callers that already stamped it -- or that write a
                caller-supplied ``updated_at`` and must not overwrite it
                (#151) -- pass ``False``.
            keep_filename: A file's name is fixed at creation (#391 cold
                read). ``True`` (an update) keeps ``entry.file_path``'s
                existing filename instead of re-running
                ``resolve_filename``/``file_pattern`` -- a title or field
                edit must not rename the file every time. ``False`` (a
                create, or a caller with no on-disk path yet) resolves a
                fresh filename as before. Ignored -- falls back to a fresh
                resolution -- when ``entry.file_path`` is unset, since there
                is no existing filename to keep.
            exclusive: Kept for callers that pass it; every save is
                exclusive now. A save is a create: a target that already
                exists is refused (``EntryExistsError``, the file as it
                was), never overwritten (the maintainer on #978, decision
                4). An existing entry is changed through
                ``KBService.apply_operations``.

        Returns:
            Path to the saved file
        """
        self.config.refuse_writes()

        # No mkdir here: the create below makes the file's folders beneath
        # the root (KBFiles), so a folder that is a link out is the write's
        # refusal, never an OSError that depends on what lies outside.
        file_path = self._target_path(
            entry, subdir, keep_filename, taken=taken, taken_keys=taken_keys
        )

        # Stamp current schema version on save
        if getattr(self.config, "kb_yaml_present", False):
            type_schema = self.config.kb_schema.get_type_schema(entry.entry_type)
            if type_schema and type_schema.version > 0:
                entry._schema_version = type_schema.version

        if touch_updated_at:
            entry.touch_updated_at()
        # The type's values written onto a skeleton by apply() (ADR-0042
        # decision 13; #955, B6 C2): to_frontmatter() decides which values the
        # new file gets, never its bytes. A save is a create: it publishes
        # only if nothing is at the path. The model's whole-file text reaches
        # no existing entry (the maintainer on #978, decision 4): an entry is
        # changed through KBService.apply_operations alone, so a save over
        # one is refused with the file as it was, whatever `exclusive` says.
        text = new_entry_text(entry)
        try:
            create_entry_text(file_path, text, root=self.path)
        except FileExistsError:
            raise EntryExistsError(
                f"'{entry.id}' already exists in KB '{self.name}' at "
                f"{self._rel(file_path)}: a save writes a new entry only; an existing "
                "entry is changed through KBService.apply_operations."
            ) from None
        entry.kb_name = self.name
        entry.file_path = file_path

        return file_path

    def _target_path(
        self,
        entry: Entry,
        subdir: str | None,
        keep_filename: bool,
        *,
        taken: Set[Path] = frozenset(),
        taken_keys: Set[tuple[str, ...]] = frozenset(),
    ) -> Path:
        """Where ``entry`` lives: ``subdir`` (inferred from the type when
        None) and either its existing filename (``keep_filename``, an update
        or a rename: a file's name is fixed at creation, #391) or a fresh one
        from the type's ``file_pattern`` or its id."""
        if subdir is None:
            subdir = self._infer_subdir(entry)
        if keep_filename and entry.file_path is not None:
            filename = entry.file_path.name
            if subdir:
                path = self._contained(self.path / subdir / filename)
            else:
                path = self._contained(self.path / filename)
            self._require_listed(path, entry, subdir, from_pattern=False)
            return path
        # Check for custom file_pattern in the type schema
        return self._resolve_file_path(entry, subdir, taken=taken, taken_keys=taken_keys)

    def delete(
        self,
        entry_id: str,
        *,
        indexed_path: Path | None = None,
        holders: list[Path] | None = None,
    ) -> bool:
        """Delete the files that are certainly ``entry_id`` (``files_to_delete``;
        refuses when that is not certain). Returns True if any was deleted.
        ``indexed_path`` is the index row's file, a candidate checked first;
        ``holders`` the files the reconcile found holding the id."""
        self.config.refuse_writes()

        files = self.files_to_delete(entry_id, indexed_path, holders)
        # Every removal decided first: one that would leave the root refuses
        # the delete before any file goes.
        for file_path in files:
            KBFiles(self.path).require_removable(file_path)
        # Removing a file is a write to it (ADR-0042 decision 8a): the service
        # loaded one holder of the id, and this removes every holder, so each
        # is judged here, before the first goes. A file that cannot be parsed
        # at all is not this rule's cause (#777) and is removed as before.
        for file_path in files:
            if file_path.suffix == ".md":
                try:
                    self._load_entry(file_path)
                except EntryLoadError:
                    raise
                except Exception:  # noqa: BLE001 - not this rule's cause
                    pass
        for file_path in files:
            remove_entry_file(file_path, root=self.path)
        return bool(files)

    # ---------------------------------------------------------------
    # Rename — Tier A r1700. The smallest useful slice: same-KB id
    # rename (the file stays where it is) + frontmatter id rewrite +
    # wikilink rewrite. Cross-KB rewrite, redirect-stub creation, and
    # the `move` (subdir-change) variant are filed as r1700 follow-ups.
    # ---------------------------------------------------------------

    def rename(
        self,
        old_id: str,
        new_id: str,
        *,
        update_links: bool = True,
        dry_run: bool = False,
    ) -> dict:
        """Rename an entry at its existing path: rewrite frontmatter id and
        all in-KB wikilinks.

        Args:
            old_id: Current entry id.
            new_id: Target entry id. Must not already exist.
            update_links: When True (default), rewrite ``[[<old_id>]]``
                and ``[[<old_id>|alias]]`` wikilinks in every entry
                body in this KB. When False, only the renamed file
                changes — references stay dangling (rarely useful,
                kept for parity with the ticket spec).
            dry_run: When True, return what would happen without
                touching the filesystem.

        Returns:
            Plan/result dict with keys ``renamed`` (bool), ``old_id``,
            ``new_id``, ``files_rewritten`` (linking files written),
            ``links_rewritten``, ``files_skipped`` (linking files the rename
            could not patch, each ``{"path", "reason"}``), ``files_partial``
            (linking files whose body was rewritten while a wikilink in a
            frontmatter that could not be patched as values still names the
            old id, each ``{"path", "reason"}``), ``dry_run``.

        A rename either completes, with every linker it could not patch
        listed, or changes nothing (#978 delta review, round 1): the entry
        is rewritten first with compare-before-replace, so a source that
        changed leaves every file as it was; the linking files are written
        after it, and a fault in any of them
        (hard-linked, undecodable, unparseable, refused by ``apply``, the
        lock directory) is listed, never fatal. No linker is written before
        every linker's rewrite is computed and its hard links checked.

        Raises:
            EntryNotFoundError: if ``old_id`` doesn't exist in this KB.
            ValidationError: if the new ID equals the old ID or already exists.
            ConflictError: if the entry changed between the read and the write.
            KBReadOnlyError: if the KB is read-only.
        """
        if not dry_run:
            self.config.refuse_writes()

        # A rename must change the id; otherwise callers can silently skip index repair.
        self._validate_entry_id(new_id)

        if old_id == new_id:
            raise ValidationError(f"Cannot rename '{old_id}': new id equals old id")

        src = self.find_file(old_id)
        if not src or not src.exists():
            raise EntryNotFoundError(
                f"Entry '{old_id}' not found in KB '{self.name}'{self.not_found_hint(old_id)}"
            )

        # An existence answer: only a file the KB would read takes the id.
        if self.find_file(new_id):
            raise ValidationError(
                f"Cannot rename '{old_id}' to '{new_id}': target already exists in KB '{self.name}'"
            )

        # A source its declared type cannot load is written not at all
        # (ADR-0042 decision 8a). The rename no longer loads the model (it
        # rewrites the id in place, ADR-0038 I6), so the strict load is made
        # here, before the dry-run return too: a request that would change
        # nothing is refused the same way. The source is read once, inside
        # the KB (a link out of it, or a hard link, is refused here,
        # OUTSIDE_KB_ROOT, before anything is parsed), and these bytes are
        # what is loaded, rewritten and compared at the publish.
        raw = self._read_inside(src)
        self._load_entry(src, _text_of(raw))

        # Plan the link rewrite. We scan every body once and substitute
        # `[[<old_id>]]` and `[[<old_id>|...]]`. Substring matches at
        # arbitrary positions are NOT touched (the spec calls for exact
        # wikilink-id match only). Every linker's rewrite is computed, and
        # its hard links checked, before anything is written: a linking file
        # the rename cannot read or patch is listed, never fatal (#978 review,
        # item 1; the delta review: no linker is written before every
        # linker's fault is known, and a partly rewritten one is reported).
        rewrites: list[tuple[Path, bytes, _Rewrite]] = []  # (path, bytes read, rewrite)
        skipped: dict[str, str] = {}  # relative path -> reason
        partial: dict[str, str] = {}

        if update_links:
            for md_file in self.list_files():
                # The source file itself gets its own write below; don't
                # pre-count its body or double-write.
                if md_file.resolve() == src.resolve():
                    continue
                rel = self._rel(md_file)
                try:
                    # A dry run's counts too. Nothing to read (gone, a FIFO,
                    # which is never opened to block, #990) is passed over.
                    linker = self._inside_bytes(md_file)
                    if linker is None:
                        continue
                    rewrite = _rewrite_wikilinks(linker.decode("utf-8"), old_id, new_id)
                except Exception as e:  # noqa: BLE001 - listed, not fatal
                    skipped[rel] = _read_fault_reason(md_file, e)
                    continue
                if rewrite.count == 0 and rewrite.partial is None:
                    continue
                fault = _hard_link_fault(md_file) or self._write_refusal(md_file, linker)
                if fault is not None:
                    skipped[rel] = fault
                    continue
                if rewrite.partial is not None:
                    partial[rel] = rewrite.partial
                if rewrite.count:
                    rewrites.append((md_file, linker, rewrite))

        if dry_run:
            return self._rename_result(
                old_id,
                new_id,
                files_rewritten=len(rewrites),
                links_rewritten=sum(r.count for _p, _raw, r in rewrites),
                skipped=skipped,
                partial=partial,
                dry_run=True,
            )

        # Execute: the entry first, so a rename that cannot write it changes
        # nothing (the delta review's "half-applied rename": the linkers used
        # to be written first, and a refused write left them naming an id that
        # did not exist). The source: one `Set("id", new_id)` on its own
        # bytes (and the `updated_at` bookkeeping where the file carries it),
        # published in place against the bytes read.
        # The bytes read inside the KB above: no answer (not even "no
        # frontmatter") depends on a file outside it.
        text = raw.decode("utf-8")
        split = split_frontmatter(text)
        if not isinstance(split, Frontmatter):
            raise ValidationError(f"Cannot rename '{old_id}': the file has no frontmatter")
        new_text = _renamed_text(text, split, new_id)

        # A rename changes identity, never a file's placement: the file stays
        # at its path (#488, #489), so there is no second path to move to and
        # no target to find taken. A file that is a link inside the KB is
        # written through, as an update writes it.
        try:
            publish_entry_text(src, new_text, expect=raw, root=self.path)
        except FileChanged as e:
            raise ConflictError(
                f"'{old_id}' in KB '{self.name}' changed while the rename ran; "
                "nothing was written. Read it again and retry."
            ) from e

        # The linking files, each published against the bytes it was computed
        # on; one that changed meanwhile is re-read and rewritten again, one
        # whose publish is refused is listed with the reason (#955, B6 P4;
        # #978 delta review: any per-linker fault is caught the same way).
        files_rewritten = 0
        links_rewritten = 0
        for path, raw, rewrite in rewrites:
            rel = self._rel(path)
            done = self._publish_rewrite(path, raw, rewrite, old_id, new_id)
            if done.skipped is not None:
                skipped[rel] = done.skipped
                partial.pop(rel, None)
                continue
            if done.count:
                files_rewritten += 1
                links_rewritten += done.count
            if done.partial is not None:
                partial[rel] = done.partial
            else:
                partial.pop(rel, None)

        return self._rename_result(
            old_id,
            new_id,
            files_rewritten=files_rewritten,
            links_rewritten=links_rewritten,
            skipped=skipped,
            partial=partial,
            dry_run=False,
        )

    @staticmethod
    def _rename_result(
        old_id: str,
        new_id: str,
        *,
        files_rewritten: int,
        links_rewritten: int,
        skipped: dict[str, str],
        partial: dict[str, str],
        dry_run: bool,
    ) -> dict:
        return {
            "renamed": True,
            "old_id": old_id,
            "new_id": new_id,
            "files_rewritten": files_rewritten,
            "links_rewritten": links_rewritten,
            "files_skipped": [{"path": p, "reason": r} for p, r in skipped.items()],
            "files_partial": [{"path": p, "reason": r} for p, r in partial.items()],
            "dry_run": dry_run,
        }

    def _rel(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.path))
        except ValueError:
            return path.name

    def _write_refusal(self, path: Path, raw: bytes) -> str | None:
        """Why no write may land on ``path`` (ADR-0042 decision 8a), or None:
        a linking file its declared type cannot load keeps its bytes, and the
        rename lists it with this reason. Only that cause: a file with no
        frontmatter or one that does not parse is rewritten as before (#777
        owns those)."""
        try:
            self.load_entry_from_bytes(path, raw)
        except EntryLoadError as e:
            return str(e)
        except Exception:  # noqa: BLE001 - not this rule's cause
            return None
        return None

    def _publish_rewrite(
        self, path: Path, raw: bytes, rewrite: _Rewrite, old_id: str, new_id: str
    ) -> _Published:
        """Publish a linking file's rewrite against the bytes it was computed
        on; if the file changed meanwhile, re-read and rewrite again, up to
        ``_REWRITE_ATTEMPTS`` times. Any fault (the file hard-linked, the lock
        directory, bytes that no longer decode or patch, a file that kept
        changing) is the reason the file was skipped, never a failure of the
        rename (#978 review, item 1; the delta review)."""
        for _attempt in range(_REWRITE_ATTEMPTS):
            try:
                publish_entry_text(path, rewrite.text, expect=raw, root=self.path)
                return _Published(rewrite.count, rewrite.partial, None)
            except FileChanged:
                try:
                    raw = self._read_inside(path)
                    rewrite = _rewrite_wikilinks(raw.decode("utf-8"), old_id, new_id)
                except Exception as e:  # noqa: BLE001 - listed, not fatal
                    return _Published(0, None, _read_fault_reason(path, e))
                if rewrite.count == 0:
                    return _Published(0, rewrite.partial, None)
                refusal = self._write_refusal(path, raw)
                if refusal is not None:
                    return _Published(0, None, refusal)
            except Exception as e:  # noqa: BLE001 - listed, not fatal
                return _Published(0, None, _fault_reason(e))
        return _Published(
            0,
            None,
            f"it kept changing while the rename of '{old_id}' rewrote its links "
            f"({_REWRITE_ATTEMPTS} attempts); nothing more was written to it",
        )

    def list_files(self) -> Iterator[Path]:
        """Iterate over all markdown entry files in the KB: not an alias
        (``alias_target``), so each real file is listed once."""
        for md_file, target in self.walk_files():
            if target is None:
                yield md_file

    def walk_files(
        self, unlisted: list[tuple[Path, str]] | None = None
    ) -> Iterator[tuple[Path, Path | None]]:
        """Every markdown path the walk's lexical rules keep, with the walked
        file it is an alias of (``alias_target``), or None for an entry file.
        ``list_files`` keeps the Nones; the reconcile's plan reports the rest
        as aliases from the same pass, so there is one walk and one rule.

        ``unlisted``, when given, receives ``(path, reason)`` for each path
        the same pass meets and the rule skips (``entry_paths.HEALTH_REASONS``:
        a README, a hidden part, ``_templates``), unread: ``index health``
        asks which of them state an ``id:`` (``unlisted_entry_rows``, #1552)."""
        for md_file in self.path.rglob("*.md"):
            reason = entry_paths.unlisted_reason(self.path, md_file)
            if reason is None:
                yield md_file, self.alias_target(md_file)
            elif unlisted is not None and reason in entry_paths.HEALTH_REASONS:
                unlisted.append((md_file, reason))

    def unlisted_entry_rows(self, unlisted: list[tuple[Path, str]]) -> list[dict[str, str]]:
        """``{kb, path, reason}`` for each skipped path (from ``walk_files``)
        whose frontmatter states an ``id:``: an entry file an earlier version
        wrote where the walk does not list it (#1552). Reported, never
        indexed, never repaired.

        Only these paths are read, each through the KB's contained read
        (``_inside_text``): a name that is a symbolic link (an in-KB link to
        an entry, or one out of the KB) is not read, nor is a file
        ``KBFiles`` refuses (a second hard link); a file that cannot be read
        or decoded is not this report's. A file with no ``id:`` line -- a
        hand-kept README, a template scaffold -- is the user's and is not
        named."""
        rows: list[dict[str, str]] = []
        for path, reason in unlisted:
            if os.path.islink(path):
                continue
            text = self._inside_text(path)
            if text is None:
                continue
            try:
                stated = explicit_entry_id(text)
            except Exception:  # noqa: BLE001 - frontmatter that does not parse states nothing
                continue
            if isinstance(stated, str) and stated.strip():
                rel = path.relative_to(self.path).as_posix()
                rows.append({"kb": self.name, "path": rel, "reason": reason})
        rows.sort(key=lambda r: r["path"])
        return rows

    def alias_target(self, md_file: Path) -> Path | None:
        """The walked entry file ``md_file`` is another name for, or None.

        An alias (#1262) is a path that is itself a symbolic link whose fully
        resolved target is a regular ``*.md`` file inside the KB's real root
        that the walk keeps by its own rules: one entry per real file, so the
        target is the entry and this path is not. The target is returned as a
        path under ``self.path`` (the spelling the walk lists it by). A chain
        of links ends at its last target.

        Not an alias, and walked as before: a path that is not a link; a link
        that leaves the KB (the reader refuses it); a dangling link
        (``dangling_target``) or a looping one (malformed); a link to a file
        the walk skips (a hidden folder, ``_templates``, a README), which is
        then the only walked name. A hard link is not a symbolic link: no name
        is the real one, and the reconcile skips both (nlink > 1).

        Where the link leads, and whether that is out of the KB, is
        ``KBFiles``'s answer (``_in_kb_link``). Nothing is read; the target's
        kind is a ``stat``. The entry's bytes are read later, at the target,
        through ``KBFiles.read_bytes``. Nothing is written: the link is the
        user's file.
        """
        target = self._in_kb_link(md_file)
        if target is None or (
            not entry_paths.is_listed(self.path, target) or not _is_regular_file(target)
        ):
            return None
        return target

    def dangling_target(self, md_file: Path) -> Path | None:
        """The KB path ``md_file`` names, when it is a link inside the KB with
        nothing there (#1262): a hand-made link to a missing file, or one a
        delete left, which keeps the user's link. It holds no entry, so the
        reconcile reports it as a dangling link, not as an unreadable file.
        None for anything else: a link out of the KB, a loop (its target is
        there, and unreadable), a path that is not a link. ``lexists`` is a
        probe of a name ``KBFiles`` placed inside the root; nothing is read."""
        target = self._in_kb_link(md_file)
        if target is None or os.path.lexists(target):
            return None
        return target

    def _in_kb_link(self, md_file: Path) -> Path | None:
        """Where ``md_file`` leads, spelled under ``self.path``, when it is a
        symbolic link under the root that does not lead out of the KB; None
        otherwise. Where it leads is ``KBFiles.resolved`` (the rule behind
        ``KBFiles.contains`` and every read and write), never a second rule.

        The name is decided first, before any probe: ``_filename_candidates``
        asks about names built from a caller's id, and a name not under the
        root is no walked name, so it is never ``lstat``-ed. That check is
        lexical and covers the name, not its folders: a folder on the way
        that is a link is followed by the probe. No caller passes one: the
        walk (``rglob``) does not enter a linked folder, and a lookup's name
        is the root plus a plain stem."""
        top = os.path.normpath(os.path.abspath(self.path))
        name = os.path.normpath(os.path.abspath(md_file))
        if not name.startswith(top + os.sep):
            return None
        # One lstat of the name itself, never followed.
        if not os.path.islink(name):
            return None
        return KBFiles(self.path).resolved(name)

    def aliases_by_target(self) -> dict[Path, list[Path]]:
        """Every alias in the KB, by the walked file it names (one walk):
        what a write that would move or remove a file asks first, so it does
        not leave the user's link pointing at nothing without saying so."""
        found: dict[Path, list[Path]] = {}
        for md_file, target in self.walk_files():
            if target is not None:
                found.setdefault(target, []).append(md_file)
        return found

    def _listed(self, md_file: Path) -> bool:
        """Whether ``list_files`` walks ``md_file``. ``find_file`` and
        ``files_to_delete`` use the same rule, so they find only what sync
        would index; a read by id (``KBService._entry_file``) tries the index
        row's path first whatever this says. An alias of another walked file
        is not listed (``alias_target``)."""
        return self._lexically_listed(md_file) and self.alias_target(md_file) is None

    def _lexically_listed(self, md_file: Path) -> bool:
        """The walk's rule on the path's spelling alone, which is
        ``entry_paths``' (#1552): no hidden part, no ``_templates`` part, not a
        README, a ``.md`` name. No I/O. Every writer of a new entry-file name
        asks the same predicate, so what a write places the walk lists.

        A ``__collection.yaml`` is not a ``*.md`` entry file, but a delete's
        index-row candidate (``files_to_delete``) may be one: it is held to
        the folder rules alone, as it was before the suffix joined the rule."""
        reason = entry_paths.unlisted_reason(self.path, md_file)
        if reason == entry_paths.NOT_MARKDOWN and md_file.name == "__collection.yaml":
            return True
        return reason is None

    def list_all_files(self) -> Iterator[Path]:
        """Iterate over all entry file paths (md + collection yaml) without parsing."""
        yield from self.list_files()
        yield from self.list_collection_files()

    def list_collection_files(self) -> Iterator[Path]:
        """Every ``__collection.yaml`` the walk keeps (not under a hidden part)."""
        for yaml_file in self._named("__collection.yaml"):
            if any(part.startswith(".") for part in yaml_file.parts):
                continue
            yield yaml_file

    def load_entry_from_bytes(self, file_path: Path, raw: bytes) -> Entry:
        """``load_entry_from_file`` of ``raw``, the file's bytes as already read.

        For a reader that must name the bytes it parsed (a single-entry read
        and its ``content_hash``, ADR-0042 decision 9): the text is decoded as
        ``read_text`` decodes it -- UTF-8, universal newlines -- so the entry
        is the one a read of the file would give. Raises on parse failure.
        """
        text = _text_of(raw)
        if file_path.name == "__collection.yaml":
            data = load_yaml(text)
            folder_path = str(file_path.parent.relative_to(self.path))
            entry: Entry = CollectionEntry.from_collection_yaml(data, folder_path)
        else:
            entry = self._load_entry(file_path, text)
        entry.kb_name = self.name
        entry.file_path = file_path
        return entry

    def load_entry_from_file(self, file_path: Path) -> Entry:
        """Load and parse a single entry from a file path.

        Handles both markdown entries and __collection.yaml files.
        Raises on parse failure.
        """
        if file_path.name == "__collection.yaml":
            entry = self._load_collection(file_path)
        else:
            entry = self._load_entry(file_path)
        entry.kb_name = self.name
        entry.file_path = file_path
        return entry

    def list_entries(self) -> Iterator[tuple[Entry, Path]]:
        """Iterate over all entries in the KB. What is not a regular file (a
        FIFO, whose open blocks for a writer that never comes; a directory
        named ``*.md``) is skipped, as the reconcile skips it (#972 round 1)."""
        for file_path in self.list_files():
            if not _is_regular_file(file_path):
                logger.warning("Skipping %s: not a regular file", file_path)
                continue
            try:
                entry = self._load_entry(file_path)
                entry.kb_name = self.name
                entry.file_path = file_path
                yield entry, file_path
            except Exception as e:
                logger.warning("Could not parse %s: %s", file_path, e)
                continue

        # Discover __collection.yaml files
        for yaml_file in self._named("__collection.yaml"):
            if any(part.startswith(".") for part in yaml_file.parts):
                continue
            try:
                entry = self._load_collection(yaml_file)
                entry.kb_name = self.name
                entry.file_path = yaml_file
                yield entry, yaml_file
            except Exception as e:
                logger.warning("Could not parse collection %s: %s", yaml_file, e)
                continue

    def _load_collection(self, yaml_file: Path) -> CollectionEntry:
        """Load a CollectionEntry from a __collection.yaml file, read inside
        the KB."""
        return self._collection_of(yaml_file, _text_of(self._read_inside(yaml_file)))

    def _collection_of(self, yaml_file: Path, text: str) -> CollectionEntry:
        """The CollectionEntry ``text`` (``yaml_file``'s, read inside) holds."""
        data = load_yaml(text) or {}
        folder_path = str(yaml_file.parent.relative_to(self.path))
        return CollectionEntry.from_collection_yaml(data, folder_path)

    def count(self) -> int:
        """Count total entries in the KB."""
        md_count = sum(1 for _ in self.list_files())
        yaml_count = sum(
            1
            for f in self._named("__collection.yaml")
            if not any(part.startswith(".") for part in f.parts)
        )
        return md_count + yaml_count

    def search_files(self, query: str) -> Iterator[tuple[Entry, Path]]:
        """
        Simple file-based search (fallback when DB not indexed).

        For production use, prefer PyriteDB.search() which uses FTS5.
        """
        query_lower = query.lower()
        for file_path in self.list_files():
            if not _is_regular_file(file_path):
                continue  # a FIFO would block the read (#990)
            try:
                content = _text_of(self._read_inside(file_path))
                if query_lower in content.lower():
                    entry = self._load_entry(file_path)
                    entry.kb_name = self.name
                    entry.file_path = file_path
                    yield entry, file_path
            except Exception:
                logger.warning("Skipping unreadable entry: %s", file_path, exc_info=True)
                continue

    def get_by_tag(self, tag: str) -> Iterator[Entry]:
        """Get entries with a specific tag."""
        for entry, _ in self.list_entries():
            if tag in entry.tags:
                yield entry

    def get_by_date_range(self, date_from: str, date_to: str) -> Iterator[Entry]:
        """Get events within a date range."""
        for entry, _ in self.list_entries():
            if isinstance(entry, EventEntry) and entry.date:
                if date_from <= entry.date <= date_to:
                    yield entry

    def validate_all(self) -> list[tuple[Path, list[str]]]:
        """Validate all entries. Returns list of (path, errors) for invalid entries."""
        invalid = []
        for entry, file_path in self.list_entries():
            errors = entry.validate()
            if errors:
                invalid.append((file_path, errors))
        return invalid


class MultiKBRepository:
    """
    Repository manager for multiple KBs.

    Provides unified access to multiple KBRepositories.
    """

    def __init__(self, kb_configs: list[KBConfig]):
        self.repos = {config.name: KBRepository(config) for config in kb_configs}

    def get_repo(self, kb_name: str) -> KBRepository | None:
        """Get repository for a specific KB."""
        return self.repos.get(kb_name)

    def load(self, entry_id: str, kb_name: str | None = None) -> Entry | None:
        """Load an entry, optionally searching across all KBs."""
        if kb_name:
            repo = self.get_repo(kb_name)
            return repo.load(entry_id) if repo else None

        # Search all KBs
        for repo in self.repos.values():
            entry = repo.load(entry_id)
            if entry:
                return entry
        return None

    def search(self, query: str, kb_name: str | None = None) -> Iterator[tuple[Entry, Path]]:
        """Search across KBs."""
        if kb_name:
            repo = self.get_repo(kb_name)
            if repo:
                yield from repo.search_files(query)
        else:
            for repo in self.repos.values():
                yield from repo.search_files(query)

    def list_all_entries(self) -> Iterator[tuple[str, Entry, Path]]:
        """List all entries across all KBs. Yields (kb_name, entry, path)."""
        for kb_name, repo in self.repos.items():
            for entry, path in repo.list_entries():
                yield kb_name, entry, path

    def count_all(self) -> dict:
        """Count entries in all KBs."""
        return {name: repo.count() for name, repo in self.repos.items()}
