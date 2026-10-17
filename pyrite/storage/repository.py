"""
KB Repository - File-based Storage

Handles reading and writing entries to/from markdown files.
Each KB is a directory of markdown files with YAML frontmatter.
"""

import glob
import io
import logging
import os
import stat
from collections.abc import Iterator
from pathlib import Path

from ..config import KBConfig
from ..exceptions import (
    ConflictError,
    EntryNotFoundError,
    FrontmatterError,
    KBReadOnlyError,
    ValidationError,
)
from ..migrations import get_migration_registry, load_plugin_migrations
from ..models import Entry, EventEntry
from ..models.collection import CollectionEntry
from ..models.core_types import entry_from_frontmatter, explicit_entry_id, read_entry_id
from ..schema import CORE_TYPES
from ..utils.frontmatter import Frontmatter, split_frontmatter
from ..utils.yaml import load_yaml, load_yaml_file
from .entry_writer import (
    FileChanged,
    create_entry_text,
    publish_entry_text,
    raw_scalar,
    updated_at_stamp,
)

logger = logging.getLogger(__name__)

#: How many times a rename re-reads a linking file whose bytes changed under
#: it before giving up (the same bound as an update's, ADR-0042 decision 10).
_REWRITE_ATTEMPTS = 6


def _rewrite_wikilinks(text: str, old_id: str, new_id: str) -> tuple[str, int]:
    """``text`` with every ``[[old_id]]`` and ``[[old_id|alias]]`` naming
    ``new_id``, and how many were rewritten, as operations on the file's own
    bytes (#955, B6 P4): a ``ReplaceBody`` where the body holds one, a ``Set``
    of each frontmatter value that holds one; every other byte, a comment
    included, stays. A file with no frontmatter is rewritten as text."""
    import re

    from .file_operations import FrontmatterSpan, ReplaceBody, Set, apply

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
        return substitute(text)
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
    entry_id = meta.get("id", entry.id)
    body = (entry.body or "").rstrip("\n")
    skeleton = f"---\nid: {_id_text(entry_id)}\n---\n" + (f"\n{body}\n" if body else "")
    split = split_frontmatter(skeleton)
    assert isinstance(split, Frontmatter), skeleton
    span = FrontmatterSpan(split.yaml_start, split.close_start, split.close_end)
    ops = [Set(key, _plain_for_file(value)) for key, value in meta.items() if key != "id"]
    if _plain_for_file(entry_id) != entry_id:
        ops.insert(0, Set("id", _plain_for_file(entry_id)))
    text, _report = apply(skeleton, ops, span)
    return text


def _renamed_text(text: str, split: Frontmatter, new_id: str) -> str:
    """``text`` with ``id: <new_id>`` set (added as the last frontmatter line
    where the file had none) and ``updated_at`` stamped where the file
    carries it, through ``apply`` (#955, B6 P4)."""
    from .file_operations import FrontmatterSpan, Set, apply

    span = FrontmatterSpan(split.yaml_start, split.close_start, split.close_end)
    ops: list[object] = [Set("id", _plain_for_file(new_id))]
    values = load_yaml(split.text)
    if isinstance(values, dict) and "updated_at" in values:
        stamp = updated_at_stamp(values["updated_at"], raw_scalar(split.text, "updated_at"))
        if stamp is not None:
            ops.append(Set("updated_at", stamp))
    new_text, _report = apply(text, ops, span)
    return new_text


def _id_text(entry_id: object) -> str:
    """``id`` on the skeleton as a plain scalar; a value a YAML 1.1 reader
    would misread, or that is not a plain string, is set through apply."""
    from .file_operations import _plain_ok  # the one spelling rule for scalars

    text = str(entry_id)
    if isinstance(entry_id, str) and _plain_ok(text, False, False) and text == text.strip():
        return text
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


def _event_fallback(file_path: Path, text: str | None) -> Entry:
    """``EventEntry.load``, from ``text`` when the caller already read it."""
    if text is None:
        return EventEntry.load(file_path)
    entry = EventEntry.from_markdown(text)
    entry.file_path = file_path
    return entry


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

    def _load_entry(self, file_path: Path, text: str | None = None) -> Entry:
        """Load an entry from a file, dispatching to the correct type.

        ``text``: the file's text, already read (``load_entry_from_bytes``);
        read here when None.
        """
        # Try to load with type-aware parsing
        try:
            # Read frontmatter to determine type
            if text is None:
                text = file_path.read_text(encoding="utf-8")
            split = split_frontmatter(text)
            if isinstance(split, Frontmatter):
                fm = load_yaml(split.text)
                if fm and isinstance(fm, dict):
                    body = split.body
                    fm = self._maybe_migrate(fm)
                    # `fm` is the entry's frontmatter and nothing else. It
                    # used to get `body` and `file_path` injected here, but
                    # no from_frontmatter reads either (body arrives as the
                    # positional argument, file_path is set by the caller),
                    # while capture_extra_frontmatter reads this same dict to
                    # decide which keys the class did not declare -- so the
                    # two internals were recorded as "unknown frontmatter"
                    # and written back into the file on the next save (#46).
                    entry = entry_from_frontmatter(fm, body)
                    # Preserve references in metadata if present in frontmatter
                    # (typed entries drop unknown fields during from_frontmatter)
                    if "references" in fm and fm["references"]:
                        if not hasattr(entry, "metadata") or not entry.metadata:
                            entry.metadata = {}
                        if "references" not in entry.metadata:
                            entry.metadata["references"] = fm["references"]
                    return entry

            # Fallback: try EventEntry.load for backward compat
            return _event_fallback(file_path, text)
        except FrontmatterError:
            # Malformed YAML: the EventEntry fallback re-parses the same bytes
            # and would fail identically, so re-raise rather than retry.
            logger.warning("Entry load failed for %s: malformed frontmatter", file_path)
            raise
        except Exception as e:
            # Log a one-line warning instead of a full traceback. Per-file
            # tracebacks spam the operator during bulk sync (a research KB's
            # drafts hit ~13 of these every operation). The underlying parse
            # error is wrapped into FrontmatterError on re-raise from
            # EventEntry.load if the cause is YAML; sync_incremental's
            # malformed-summary captures it. Tier A 1080.
            logger.warning(
                "Entry load failed for %s, trying EventEntry fallback: %s",
                file_path,
                e,
            )
            return _event_fallback(file_path, text)

    def _maybe_migrate(self, fm: dict) -> dict:
        """Apply pending schema migrations to frontmatter if needed."""
        if not hasattr(self.config, "kb_yaml_path") or not self.config.kb_yaml_path.exists():
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
        `file_pattern` filenames are also built from entry data.
        """
        root = self.path.resolve()
        resolved = file_path.resolve()
        if resolved != root and root not in resolved.parents:
            raise ValidationError(f"Refusing to write outside KB '{self.name}': {file_path}")
        return file_path

    def _get_file_path(self, entry_id: str, subdir: str | None = None) -> Path:
        """Get file path for an entry."""
        self._validate_entry_id(entry_id)
        if subdir:
            return self._contained(self.path / subdir / f"{entry_id}.md")
        return self._contained(self.path / f"{entry_id}.md")

    def _resolve_file_path(self, entry: Entry, subdir: str | None) -> Path:
        """Resolve the file path for an entry, respecting file_pattern if set."""
        schema = self.config.kb_schema
        type_schema = schema.get_type_schema(entry.entry_type)
        if type_schema:
            custom_name = type_schema.resolve_filename(entry)
            if custom_name:
                # file_pattern provides the full filename (with .md)
                if subdir:
                    return self._contained(self.path / subdir / custom_name)
                return self._contained(self.path / custom_name)
        return self._get_file_path(entry.id, subdir)

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
        """Check if an entry exists."""
        return self.find_file(entry_id) is not None

    def _lexically_inside(self, path: Path) -> bool:
        """``path`` stays under the KB root once ``..`` is collapsed.

        Lexical, not ``resolve()``: it refuses ``<kb>/../x`` (a ``collection-..``
        lookup reached one level up through ``rglob("..")``) without changing
        how symlinks placed inside a KB behave -- that question belongs to the
        multi-user security review, not to this fix.
        """
        root = os.path.normpath(os.path.abspath(self.path))
        target = os.path.normpath(os.path.abspath(path))
        return os.path.commonpath([root, target]) == root

    def _filename_candidates(self, entry_id: str) -> Iterator[Path]:
        """Files whose NAME matches ``entry_id``; ``entry_id`` must already be
        a plain stem. A name is a hint for a fast lookup, never evidence of
        the id the file holds: ``find_file`` verifies each one."""
        root_path = self.path / f"{entry_id}.md"
        if root_path.is_file() and self._listed(root_path):
            yield root_path

        # glob.escape: an id is a name, not a pattern -- `*` must not match
        # (and so let `kb_delete` remove) whichever entry globs first.
        filename = glob.escape(f"{entry_id}.md")
        for match in self.path.rglob(filename):
            if match != root_path and self._listed(match):
                yield match

        # Collection entries (collection-<folder_name>)
        if entry_id.startswith("collection-"):
            folder_name = entry_id[len("collection-") :]
            if folder_name in ("", ".", ".."):
                return
            for subdir in self.path.rglob(glob.escape(folder_name)):
                if subdir.is_dir():
                    yaml_path = subdir / "__collection.yaml"
                    if yaml_path.exists():
                        yield yaml_path

    def id_of_file(self, file_path: Path) -> str | None:
        """The id ``file_path`` holds, by the loader's rule; None when it is
        not a readable entry.

        Markdown goes through ``read_entry_id`` -- the one function that
        answers this (ADR-0038 decision 1) -- with this KB's schema
        migrations applied first, as ``_load_entry`` applies them.
        """
        return self.read_id(file_path)[0]

    def read_id(self, file_path: Path) -> tuple[str | None, str | None]:
        """``id_of_file`` with the reason: ``(id, None)``, or ``(None, why)``
        where ``why`` is the loader's own message (``title must be a string,
        got a list``), or None when the file simply holds no entry. A path
        that is not a regular file is not read (a FIFO would block)."""
        if not _is_regular_file(file_path):
            return None, "not a regular file"
        try:
            if file_path.name == "__collection.yaml":
                return self._load_collection(file_path).id or None, None
            return (
                read_entry_id(file_path.read_text(encoding="utf-8"), migrate=self._maybe_migrate),
                None,
            )
        except Exception as e:
            logger.warning("Skipping unreadable file during id lookup: %s (%s)", file_path, e)
            # One line, KB-relative: a ruamel message spans lines and may name
            # an absolute path, and this text is shown to the user.
            why = (str(e).splitlines() or [""])[0].replace(f"{self.path}/", "")
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
                if self._lexically_inside(candidate) and self.id_of_file(candidate) == entry_id:
                    return candidate

        for md_file in self.list_files():
            if md_file not in tried and self.id_of_file(md_file) == entry_id:
                return md_file
        return None

    def _holds(self, file_path: Path, entry_id: str) -> str | None:
        """How ``file_path`` holds ``entry_id``: ``"explicit"`` (its ``id:``
        says so), ``"derived"`` (the id comes from its title), or None."""
        if not file_path.is_file() or self.id_of_file(file_path) != entry_id:
            return None
        if file_path.name == "__collection.yaml":
            return "explicit"  # named by its folder, or its own ``id:``
        try:
            stated = explicit_entry_id(file_path.read_text(encoding="utf-8"))
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
        if indexed_path is not None and self._lexically_inside(indexed_path):
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

        held = {f: self._holds(f, entry_id) for f in self.list_files()}
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
            held = self.id_of_file(candidate)
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
        return file_path, file_path.read_bytes()

    def load(self, entry_id: str) -> Entry | None:
        """Load an entry by ID."""
        file_path = self.find_file(entry_id)
        if not file_path:
            return None

        try:
            entry = self._load_entry(file_path)
            entry.kb_name = self.name
            entry.file_path = file_path
            return entry
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
    ) -> Path:
        """
        Save an entry to file.

        Args:
            entry: The entry to save
            subdir: Optional subdirectory (auto-inferred if not provided)
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
            exclusive: Passed to ``Entry.save``: publish with ``os.link``
                instead of ``os.replace``, so a target that already exists
                raises ``FileExistsError`` instead of being silently
                overwritten (#391 cold read round 2 -- narrows, at the one
                filesystem-atomic point available, the TOCTOU window
                between the write pipeline's own exists() check and the
                write that follows it). Create-path callers only; an update
                means to overwrite.

        Returns:
            Path to the saved file
        """
        if self.config.read_only:
            raise KBReadOnlyError(f"KB '{self.name}' is read-only")

        file_path = self._target_path(entry, subdir, keep_filename)
        file_path.parent.mkdir(parents=True, exist_ok=True)

        # Stamp current schema version on save
        if hasattr(self.config, "kb_yaml_path") and self.config.kb_yaml_path.exists():
            type_schema = self.config.kb_schema.get_type_schema(entry.entry_type)
            if type_schema and type_schema.version > 0:
                entry._schema_version = type_schema.version

        if touch_updated_at:
            entry.touch_updated_at()
        # The type's values written onto a skeleton by apply() (ADR-0042
        # decision 13; #955, B6 C2): to_frontmatter() decides which values the
        # new file gets, never its bytes. A create publishes only if nothing
        # is there (expect=ABSENT). The non-exclusive form is the whole-file
        # overwrite `repo.save(entry)` callers and fixtures keep (no service
        # path writes an existing entry this way since P4): published against
        # the bytes read, so a file changed meanwhile is a ConflictError.
        text = new_entry_text(entry)
        if exclusive:
            create_entry_text(file_path, text)
        else:
            try:
                raw = file_path.read_bytes()
            except FileNotFoundError:
                create_entry_text(file_path, text)
            else:
                try:
                    publish_entry_text(file_path, text, expect=raw)
                except FileChanged as e:
                    raise ConflictError(
                        f"'{entry.id}' in KB '{self.name}' changed while it was being "
                        "saved; nothing was written. Read it again and retry."
                    ) from e
        entry.kb_name = self.name
        entry.file_path = file_path

        return file_path

    def _target_path(self, entry: Entry, subdir: str | None, keep_filename: bool) -> Path:
        """Where ``entry`` lives: ``subdir`` (inferred from the type when
        None) and either its existing filename (``keep_filename``, an update
        or a rename: a file's name is fixed at creation, #391) or a fresh one
        from the type's ``file_pattern`` or its id."""
        if subdir is None:
            subdir = self._infer_subdir(entry)
        if keep_filename and entry.file_path is not None:
            filename = entry.file_path.name
            if subdir:
                return self._contained(self.path / subdir / filename)
            return self._contained(self.path / filename)
        # Check for custom file_pattern in the type schema
        return self._resolve_file_path(entry, subdir)

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
        if self.config.read_only:
            raise KBReadOnlyError(f"KB '{self.name}' is read-only")

        files = self.files_to_delete(entry_id, indexed_path, holders)
        for file_path in files:
            file_path.unlink(missing_ok=True)
        return bool(files)

    # ---------------------------------------------------------------
    # Rename — Tier A r1700. The smallest useful slice: same-KB file
    # rename + frontmatter id rewrite + wikilink rewrite. Cross-KB
    # rewrite, redirect-stub creation, and the `move` (subdir-change)
    # variant are filed as r1700 follow-ups.
    # ---------------------------------------------------------------

    def rename(
        self,
        old_id: str,
        new_id: str,
        *,
        update_links: bool = True,
        dry_run: bool = False,
    ) -> dict:
        """Rename an entry: move file, rewrite frontmatter id, rewrite
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
            ``new_id``, ``files_rewritten``, ``links_rewritten``,
            ``dry_run``.

        Raises:
            EntryNotFoundError: if ``old_id`` doesn't exist in this KB.
            ValidationError: if the new ID equals the old ID or already exists.
            KBReadOnlyError: if the KB is read-only.
        """
        if self.config.read_only and not dry_run:
            raise KBReadOnlyError(f"KB '{self.name}' is read-only")

        # A rename must change the id; otherwise callers can silently skip index repair.
        self._validate_entry_id(new_id)

        if old_id == new_id:
            raise ValidationError(f"Cannot rename '{old_id}': new id equals old id")

        src = self.find_file(old_id)
        if not src or not src.exists():
            raise EntryNotFoundError(
                f"Entry '{old_id}' not found in KB '{self.name}'{self.not_found_hint(old_id)}"
            )

        if self.find_file(new_id):
            raise ValidationError(
                f"Cannot rename '{old_id}' to '{new_id}': target already exists in KB '{self.name}'"
            )

        # Plan the link rewrite. We scan every body once and substitute
        # `[[<old_id>]]` and `[[<old_id>|...]]`. Substring matches at
        # arbitrary positions are NOT touched (the spec calls for exact
        # wikilink-id match only).
        files_rewritten = 0
        links_rewritten = 0
        body_rewrites: list[tuple[Path, bytes, str]] = []  # (path, bytes read, new_text)

        if update_links:
            for md_file in self.list_files():
                # The source file itself will get a fresh write below;
                # don't pre-count its body or double-write.
                if md_file.resolve() == src.resolve():
                    continue
                try:
                    raw = md_file.read_bytes()
                    text = raw.decode("utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                new_text, count = _rewrite_wikilinks(text, old_id, new_id)
                if count == 0:
                    continue
                files_rewritten += 1
                links_rewritten += count
                body_rewrites.append((md_file, raw, new_text))

        if dry_run:
            return {
                "renamed": True,
                "old_id": old_id,
                "new_id": new_id,
                "files_rewritten": files_rewritten,
                "links_rewritten": links_rewritten,
                "dry_run": True,
            }

        # Execute. Rewrite-other-files first so a failure leaves the
        # rename incomplete rather than orphaned wikilinks against a
        # missing source. Each is published against the bytes it was
        # computed on; a file that changed meanwhile is re-read and
        # re-rewritten (#955, B6 P4).
        for path, raw, new_text in body_rewrites:
            self._publish_rewrite(path, raw, new_text, old_id, new_id)

        # The source: one `Set("id", new_id)` on its own bytes (and the
        # `updated_at` bookkeeping where the file carries it), published at
        # the new path with expect=ABSENT, then the old file removed. The
        # model is loaded only to resolve the new path; it supplies no bytes.
        raw = src.read_bytes()
        text = raw.decode("utf-8")
        split = split_frontmatter(text)
        if not isinstance(split, Frontmatter):
            raise ValidationError(f"Cannot rename '{old_id}': the file has no frontmatter")
        new_text = _renamed_text(text, split, new_id)
        entry = self._load_entry(src, text)
        entry.id = new_id
        # File pattern in some plugin schemas can pull subdir from id;
        # keep the entry in the same subdir it lived in by saving with
        # the explicit relative subdir of the old file.
        rel = src.parent.relative_to(self.path)
        subdir = str(rel) if str(rel) != "." else None

        # #391 cold read (round 2): a file's name is fixed at creation, so a
        # `file_pattern` type's filename is NOT re-derived from the new id on
        # rename -- only the frontmatter `id:` changes, exactly as an update
        # already keeps the file where it is. Without this, a pattern with
        # no `{id}`/`{slug}` placeholder (e.g. the software-kb `adr` type's
        # `{adr_number:04d}-{title}.md`) re-resolves to the SAME path as the
        # source (neither field changes on a rename), and the unconditional
        # `src.unlink()` below used to delete the file it had just written.
        schema = self.config.kb_schema
        type_schema = schema.get_type_schema(entry.entry_type)
        has_file_pattern = bool(type_schema and type_schema.file_pattern)
        entry.file_path = src
        file_path = self._target_path(entry, subdir, keep_filename=has_file_pattern)

        if file_path.resolve() == src.resolve():
            try:
                publish_entry_text(src, new_text, expect=raw)
            except FileChanged as e:
                raise ConflictError(
                    f"'{old_id}' in KB '{self.name}' changed while the rename ran; "
                    "nothing was written. Read it again and retry."
                ) from e
        else:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                create_entry_text(file_path, new_text)
            except FileExistsError:
                raise ValidationError(
                    f"Cannot rename '{old_id}' to '{new_id}': target already exists "
                    f"in KB '{self.name}'"
                ) from None
            # Delete the old file only AFTER the new one is on disk, and
            # only if the rename actually produced a DIFFERENT file (defense
            # in depth: the file_pattern branch above already keeps them
            # equal by construction).
            if src.exists():
                src.unlink()

        return {
            "renamed": True,
            "old_id": old_id,
            "new_id": new_id,
            "files_rewritten": files_rewritten,
            "links_rewritten": links_rewritten,
            "dry_run": False,
        }

    def _publish_rewrite(self, path: Path, raw: bytes, new_text: str, old_id: str, new_id: str):
        """Publish a linking file's rewrite against the bytes it was computed
        on; if the file changed meanwhile, re-read and rewrite again, up to
        ``_REWRITE_ATTEMPTS`` times."""
        for _attempt in range(_REWRITE_ATTEMPTS):
            try:
                publish_entry_text(path, new_text, expect=raw)
                return
            except FileChanged:
                raw = path.read_bytes()
                new_text, count = _rewrite_wikilinks(raw.decode("utf-8"), old_id, new_id)
                if count == 0:
                    return
        raise ConflictError(
            f"{path.name} in KB '{self.name}' kept changing while the rename of "
            f"'{old_id}' rewrote its links; nothing more was written. Retry."
        )

    def list_files(self) -> Iterator[Path]:
        """Iterate over all markdown files in the KB."""
        for md_file in self.path.rglob("*.md"):
            if self._listed(md_file):
                yield md_file

    def _listed(self, md_file: Path) -> bool:
        """Whether ``list_files`` walks ``md_file``; ``find_file`` uses the
        same rule, so what sync indexes is what lookup finds."""
        try:
            rel = md_file.relative_to(self.path)
        except ValueError:
            return False
        # Skip hidden directories and files (check relative path only,
        # so a KB stored under e.g. ~/.pyrite/kbs/ is not skipped)
        if any(part.startswith(".") for part in rel.parts):
            return False
        # Skip template scaffold files in _templates directories
        if "_templates" in rel.parts:
            return False
        # Skip README files (case-insensitive) — they lack frontmatter
        return md_file.name.lower() != "readme.md"

    def list_all_files(self) -> Iterator[Path]:
        """Iterate over all entry file paths (md + collection yaml) without parsing."""
        yield from self.list_files()
        for yaml_file in self.path.rglob("__collection.yaml"):
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
        text = io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8").read()
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
        for yaml_file in self.path.rglob("__collection.yaml"):
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
        """Load a CollectionEntry from a __collection.yaml file."""
        data = load_yaml_file(yaml_file)
        folder_path = str(yaml_file.parent.relative_to(self.path))
        return CollectionEntry.from_collection_yaml(data, folder_path)

    def count(self) -> int:
        """Count total entries in the KB."""
        md_count = sum(1 for _ in self.list_files())
        yaml_count = sum(
            1
            for f in self.path.rglob("__collection.yaml")
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
            try:
                content = file_path.read_text(encoding="utf-8")
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
