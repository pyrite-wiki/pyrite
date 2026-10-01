"""
Base Entry Model

Abstract base for all KB entry types.
"""

import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from functools import cache
from pathlib import Path
from typing import Any, ClassVar

from ..exceptions import FrontmatterError
from ..schema import Link, Provenance, Source
from ..utils.yaml import dump_yaml, load_yaml

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    """Return current UTC time (timezone-aware)."""
    return datetime.now(UTC)


# The value `importance` takes when frontmatter does not set it. Kept as a name
# so the write path can tell "unset" from "deliberately 5".
DEFAULT_IMPORTANCE = 5


# Base keys every class handles through _base_kwargs / _base_frontmatter even
# when it does not re-emit them (empty values are omitted on write).
#
# `body`, `file_path`, `kb_name` and `extra_frontmatter` are Entry attributes,
# never frontmatter. They are listed so that a loader which puts them into the
# meta dict it passes to capture_extra_frontmatter cannot have them recorded as
# undeclared frontmatter and written back into the file on the next save (#46).
_BASE_CONSUMED_KEYS = frozenset(
    {
        "body",
        "file_path",
        "kb_name",
        "extra_frontmatter",
        "id",
        "title",
        "type",
        "summary",
        "tags",
        "aliases",
        "sources",
        "links",
        "provenance",
        "metadata",
        "importance",
        "lifecycle",
        "created_at",
        "updated_at",
        "_schema_version",
    }
)

# #151: timestamps are written back only to a file that already carried them.
# `capture_extra_frontmatter` records their absence (like the always-written
# defaults) so a no-op round trip cannot invent them, and `_base_frontmatter`
# emits them only when that flag has been cleared -- by the source file having
# the key, or by an explicit assignment (see `__setattr__`).
_TIMESTAMP_KEYS = ("created_at", "updated_at")

# Attributes, never frontmatter (see `_BASE_CONSUMED_KEYS`): a source key of one
# of these names is not a value the model re-serializes, so it is never "kept".
_NEVER_FRONTMATTER_KEYS = frozenset({"body", "file_path", "kb_name", "extra_frontmatter"})

# Stands for "the serialized model has no such key" in the kept-key comparison.
_ABSENT = object()


def _default_valued_keys_absent_from(entry: "Entry", meta: dict[str, Any]) -> frozenset[str]:
    """Keys a pristine instance of ``entry``'s class writes, that ``meta`` lacks.

    Some fields are serialized even at their default value, because the default
    is a meaningful choice a user may have made (`importance: 5`, `rank: 0`).
    That is right for an entry built in memory, but on a load -> save round trip
    it invents keys the file never had, turning a one-field update into a
    frontmatter rewrite (#46).

    The set is computed by serializing a default-constructed instance of the
    same class: whatever it emits is an always-written key, and the ones absent
    from the source frontmatter are the ones to keep absent. Doing it
    empirically means a plugin type gets the behaviour without declaring
    anything, matching how ``capture_extra_frontmatter`` decides "unknown".
    """
    return frozenset(k for k in _always_written_keys(type(entry)) if k not in meta)


@cache
def _pristine_frontmatter(cls: type) -> dict[str, Any]:
    """The frontmatter a default-constructed ``cls`` emits, as a plain dict.

    Used by the write path to tell "this key still holds the value the class
    would have invented" from "the user set it to something". Depends only on
    the class, so it is computed once per type rather than per save.

    The returned mapping is SHARED by every entry of the class and must be
    treated as read-only. It is only ever compared against, never copied into
    an entry's frontmatter, so no pristine value can reach a file; a caller
    that mutated it would corrupt the write path for every entry of that type.
    """
    try:
        return dict(cls(id="probe", title="probe").to_frontmatter())
    except Exception:
        logger.debug("no pristine-frontmatter probe for %s", cls.__name__, exc_info=True)
        return {}


def _always_written_keys(cls: type) -> frozenset[str]:
    """Frontmatter keys ``cls`` emits for a default-constructed instance.

    Derived from the same cached probe as ``_pristine_frontmatter`` so that a
    class is instantiated once, not twice: this runs in the hot path of a full
    index sync. A class needing more constructor arguments opts out of the
    mechanism (its probe is empty, so it simply keeps writing its defaults);
    the failure is logged there rather than swallowed, so an opted-out type is
    discoverable.
    """
    return frozenset(_pristine_frontmatter(cls))


def capture_extra_frontmatter(entry: "Entry", meta: dict[str, Any]) -> None:
    """Record the top-level keys ``entry``'s class did not re-emit.

    Called by every load path after ``from_frontmatter``. "Unknown" is decided
    empirically -- a key is kept if serializing the freshly loaded entry does
    not produce it -- so no class has to list its own fields, and a plugin
    type gets the guarantee for free.
    """
    # Set before serializing: both influence what to_frontmatter emits, and
    # `emitted` below must reflect the decisions the write path will make.
    entry._absent_default_keys = _default_valued_keys_absent_from(entry, meta) | frozenset(
        # #151: created_at/updated_at are only written back to a file that
        # already carried them. Recording their absence here (same mechanism as
        # the always-written defaults above) is what keeps a no-op round trip
        # from inventing them, while an explicit assignment clears the flag
        # (see __setattr__) and reaches the file from then on.
        k
        for k in _TIMESTAMP_KEYS
        if k not in meta
    )
    # #173 review: a timestamp key whose source node is PRESENT but does not
    # parse to an instant (`created_at:` with no value, `created_at: ''`,
    # `created_at: Jan 15 2026`) gives parse_datetime() nothing to read, so the
    # attribute holds the "now" fallback. The write path must not mistake that
    # fallback for an edit: record the keys here so _frontmatter_for_file can
    # hand the raw node back to the restyle untouched. An explicit assignment
    # clears the key (see __setattr__), so a real edit still reaches the file.
    entry._unparsed_timestamp_keys = frozenset(
        k for k in _TIMESTAMP_KEYS if k in meta and _timestamp_instant(meta[k]) is None
    )
    # Keep the mapping as ruamel parsed it, for style on the way back out.
    # Stored by reference, not copied: copying is what mangled anchors and
    # merge keys, and the write path only ever READS this. Two entries loaded
    # from one `meta` dict therefore share it -- safe while it stays read-only,
    # so do not mutate it here or in _restyle_like_source.
    entry._source_frontmatter = meta
    try:
        emitted = entry.to_frontmatter()
    except Exception:
        # A class that cannot serialize is not this helper's problem, but a type
        # silently losing its unknown-key protection should be discoverable.
        logger.debug(
            "no extra-frontmatter capture for %s: to_frontmatter failed",
            type(entry).__name__,
            exc_info=True,
        )
        return
    extras = {
        k: v
        for k, v in meta.items()
        if k not in emitted and k not in _BASE_CONSUMED_KEYS and k not in entry.FRONTMATTER_ALIASES
    }
    if extras:
        entry.extra_frontmatter = extras
    # #557: a key the model reads but cannot write back as the file had it --
    # `provenance:` with sub-keys `Provenance` does not know, `sources: a book`,
    # `importance: high`, a `links: [a, b]` shorthand. Recorded with the value
    # the model serialized at load, so the write path can tell "nobody changed
    # this field" (write the file's node back) from a real change.
    unrepresented = {
        k: _plain(emitted.get(k, _ABSENT))
        for k, v in meta.items()
        if k not in extras
        and k not in _TIMESTAMP_KEYS
        and k not in _NEVER_FRONTMATTER_KEYS
        and k not in entry.FRONTMATTER_ALIASES
        and _plain(v) != _plain(emitted.get(k, _ABSENT))
    }
    if unrepresented:
        entry._unrepresented_keys = unrepresented


def _plain(value: Any) -> Any:
    """Strip ruamel node types so two values can be compared by content.

    Booleans are tagged, because `1 == True` and `0 == False` in Python: a bool
    written over an equal int (or the reverse) would otherwise compare equal,
    be judged "unchanged", and the old value silently kept.
    """
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, Mapping):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def _keep_sequence_style(old: Any, new: Any) -> Any:
    """Return ``new``, rendered in ``old``'s sequence style where that applies.

    A list whose contents changed still wants the brackets it had:
    `tags: [a, b]` edited to three tags should stay on one line rather than
    becoming a four-line block list.
    """
    if not isinstance(new, list) or isinstance(new, str):
        return new
    try:
        from ruamel.yaml.comments import CommentedSeq

        if isinstance(old, CommentedSeq) and old.fa.flow_style():
            seq = CommentedSeq(new)
            seq.fa.set_flow_style()
            return seq
    except Exception:  # style is a nicety; never fail a save for it
        logger.debug("could not carry sequence style", exc_info=True)
    return new


def _timestamp_instant(value: Any) -> datetime | None:
    """``value`` as a UTC instant, or ``None`` when it is not one.

    Two callers ask different questions of the same answer: whether a source
    node parses at all (``capture_extra_frontmatter``) and whether a freshly
    built value still means the same instant as the source node
    (``_timestamp_same_instant``). ``None``, ``''`` and ``Jan 15 2026`` are not
    instants. A bare ``date`` is midnight UTC and a naive ``datetime`` is read
    as UTC, both matching ``parse_datetime``.
    """
    value = _plain(value)
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(re.sub(r"Z$", "+00:00", value))
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    return None


def _timestamp_same_instant(old: Any, new: Any) -> bool:
    """True when ``new`` re-serialises the same instant as the raw ``old``
    source node, so the source node (and its style) can be kept.

    ``#151`` normalises a bare YAML date to a ``datetime`` on load, and the
    write path hands ruamel a ``datetime`` back, so the plain equality check
    in ``_restyle_like_source`` no longer sees them as "unchanged" and would
    replace `created_at: 2026-01-15` with a full timestamp. Comparing the
    parsed instants instead keeps the original node whenever the value still
    means what the file said; a genuinely changed timestamp (a refreshed
    `updated_at`, say) still falls through and is written in the new form.
    Naive values are read as UTC, matching ``parse_datetime``.

    Only timestamps take this path -- see the ``key in _TIMESTAMP_KEYS``
    guard in ``_restyle_like_source``. Applying it to every key would let an
    edit of a title from `"2026-01-15"` to `"2026-01-15T00:00:00Z"` keep the
    old node: the CLI would report success and the file would never change.
    """
    parsed_old = _timestamp_instant(old)
    parsed_new = _timestamp_instant(new)
    return parsed_old is not None and parsed_new is not None and parsed_old == parsed_new


def _restyle_like_source(meta: dict[str, Any], source: Any) -> dict[str, Any]:
    """Re-emit ``meta`` in ``source``'s key order and YAML style.

    ``meta`` is a freshly built plain dict: correct in content, but it has lost
    the key order, quoting and flow/block style that ruamel preserved when the
    file was read. Writing it as-is turns a one-field update into a diff that
    touches every line, which is how the #46 frontmatter corruption stayed
    invisible in review.

    For each key ``source`` had and ``meta`` still has, the source node is
    reused *by reference* when the value is unchanged (keeping `tags: [a, b]`
    inline, `"2026-07-03"` quoted, and any anchor or merge key intact) and
    replaced when it is not. Keys dropped from ``meta`` are dropped here too,
    and keys ``meta`` added are appended in its own order. With no source -- a
    newly created entry -- ``meta`` is returned untouched.
    """
    if not isinstance(source, Mapping):
        return meta

    try:
        from ruamel.yaml.comments import CommentedMap

        restyled: Any = CommentedMap()
    except Exception:  # no ruamel: order still helps, style is a nicety
        logger.debug("restyle: ruamel CommentedMap unavailable", exc_info=True)
        restyled = {}

    # Source order first, then whatever `meta` added, in its own order.
    #
    # Unchanged values are carried over BY REFERENCE, never copied. An earlier
    # version deep-copied the whole source mapping to inherit its style, which
    # also resolved YAML anchors and merge keys: `<<: *anch` came back as a
    # literal `<<:` key whose value was the merged mapping, and the merged keys
    # were then emitted a second time as siblings -- a duplicate key in a file
    # that had none. Referencing the original node keeps its anchor, alias,
    # merge and comment attachments exactly as ruamel parsed them.
    for key, old in source.items():
        if key not in meta:
            continue
        new = meta[key]
        unchanged = _plain(old) == _plain(new) or (
            key in _TIMESTAMP_KEYS and _timestamp_same_instant(old, new)
        )
        restyled[key] = old if unchanged else _keep_sequence_style(old, new)

    for key, value in meta.items():
        if key not in restyled:
            restyled[key] = value

    return restyled


@dataclass
class Entry(ABC):
    """
    Abstract base class for all KB entries.

    All entries share:
    - ID and title
    - Body content
    - Tags and links
    - Sources and provenance
    - Timestamps
    - Metadata dict for extension fields
    """

    id: str
    title: str
    body: str = ""
    summary: str = ""
    tags: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)
    provenance: Provenance | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    importance: int = DEFAULT_IMPORTANCE
    lifecycle: str = "active"
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)

    # KB reference (set when loaded)
    kb_name: str = ""
    file_path: Path | None = None
    _schema_version: int = 0
    # Top-level frontmatter keys this class did not declare, captured at load
    # and written back at save, so a load -> save through a typed class never
    # deletes what it does not understand (`milestone:`, `github_issue:`, a
    # field a plugin added last week). Not indexed, not compared.
    extra_frontmatter: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    # Keys whose value equals their default and which this entry was loaded
    # WITHOUT. `importance: 5` and `rank: 0` are deliberately serialized even at
    # their defaults -- commit 7783335 fixed the data loss of dropping an
    # explicit `importance: 5` -- but that rule made a one-field update add
    # those lines to files that never had them (#46). So the rule now applies
    # to entries built in memory (the set is empty, everything is written),
    # while a load records which default-valued keys were absent so re-saving
    # does not invent them. Not a constructor argument: subclasses build kwargs
    # from _base_kwargs and would have to thread it through.
    _absent_default_keys: frozenset[str] = field(
        default=frozenset(), init=False, repr=False, compare=False
    )

    # Timestamp keys whose source frontmatter node exists but does not parse
    # to an instant (`created_at:` with no value, `''`, `Jan 15 2026`).
    # `parse_datetime` reads those as "now", so the attribute cannot be told
    # apart from an edit by value alone: `_frontmatter_for_file` keeps the raw
    # source node instead, and an explicit assignment clears the key here
    # (see `__setattr__`) so a real edit still lands (#173 review).
    _unparsed_timestamp_keys: frozenset[str] = field(
        default=frozenset(), init=False, repr=False, compare=False
    )

    # Source keys whose value the model cannot write back as the file had it
    # (#557), mapped to what the model serialized for them at load. The write
    # path puts the file's own node back while the model still serializes the
    # load-time value; an assignment removes the key (see `__setattr__`), so
    # a value a caller names always reaches the file. ``None`` when there are
    # none: a dict default would need a factory, and `__setattr__` reads this
    # before the dataclass `__init__` has assigned it.
    _unrepresented_keys: dict[str, Any] | None = field(
        default=None, init=False, repr=False, compare=False
    )

    def __setattr__(self, name: str, value: Any) -> None:
        """Assigning a field makes it explicit, whatever value it is given.

        `_absent_default_keys` records what the SOURCE FILE lacked. Without
        this, that load-time fact was also read as the user's intent forever
        after, so setting `importance = 5` (or `rank = 0`) on a file that had
        no such key wrote nothing and still reported success -- the data loss
        of commit 7783335 through a new door, and the mirror image of #46.

        Intercepting assignment rather than clearing the set inside
        KBService.update_entry covers every writer: the CLI, REST
        PUT/PATCH /api/entries, MCP entry_update, and software-kb's `sw
        reorder` (which legitimately computes rank=0) all assign attributes,
        but only one of them goes through that service method.
        """
        if name in self._absent_default_keys:
            # frozenset, so rebind rather than mutate a shared instance.
            super().__setattr__("_absent_default_keys", self._absent_default_keys - {name})
        if name in self._unparsed_timestamp_keys:
            # Same rule for a timestamp the source carried but did not parse:
            # the assignment is the user's value and must reach the file.
            super().__setattr__("_unparsed_timestamp_keys", self._unparsed_timestamp_keys - {name})
        kept = self._unrepresented_keys
        if kept and name in kept:
            # #557: the file's own value is kept only while nobody sets the
            # field; an assignment is the caller's value, even one equal to
            # what the model read (`importance = 5` over `importance: high`).
            super().__setattr__("_unrepresented_keys", {k: v for k, v in kept.items() if k != name})
        super().__setattr__(name, value)

    def touch_updated_at(self) -> None:
        """Refresh ``updated_at`` as *bookkeeping*, without inventing the key.

        ``KBRepository.save``, ``KBService.update_entry`` and ``sw link`` all
        stamp ``updated_at`` on every write. Once the write path started
        re-emitting the key for files that carry it (#151), that stamp could no
        longer go through a plain assignment: ``__setattr__`` treats any
        assignment as explicit and clears the key from
        ``_absent_default_keys``, so a file that never had ``updated_at`` would
        have grown one on its first save -- the #46 no-growth invariant,
        arriving through the repository instead of the service layer.

        ``object.__setattr__`` deliberately bypasses that hook: the in-memory
        value stays fresh, while the key's file-presence remains whatever the
        source file had.
        """
        object.__setattr__(self, "updated_at", _utcnow())

    # The frontmatter mapping this entry was parsed from, as ruamel returned it
    # (a CommentedMap carrying key order, quoting and flow/block style). The
    # write path uses it to re-emit unchanged keys exactly as they were, so a
    # one-field update produces a one-line diff instead of reordering and
    # restyling the whole block (#46). Never read as data -- only as style.
    _source_frontmatter: Any = field(default=None, init=False, repr=False, compare=False)

    # Legacy frontmatter keys a class reads under another name (e.g. `participants`
    # -> `actors`). They are consumed, not unknown, so they are not preserved
    # as extras (which would write the value twice).
    FRONTMATTER_ALIASES: ClassVar[frozenset[str]] = frozenset()

    # Fields this type's own service logic maintains -- an audit trail, a
    # number a command assigns -- which a caller's field update must not set
    # (#378 review). `KBService.update` refuses them and
    # `KBService.updatable_fields` leaves them out; in-process callers
    # (`update_entry`) still write them. Plugin entry types declare theirs the
    # same way. Identity and storage fields (id, path, links...) are managed by
    # the service for every type and need not be listed.
    managed_fields: ClassVar[frozenset[str]] = frozenset()

    @property
    @abstractmethod
    def entry_type(self) -> str:
        """Return the entry type identifier."""
        pass

    @abstractmethod
    def to_frontmatter(self) -> dict[str, Any]:
        """Convert to YAML frontmatter dictionary."""
        pass

    @classmethod
    @abstractmethod
    def from_frontmatter(cls, meta: dict[str, Any], body: str) -> "Entry":
        """Create from parsed frontmatter and body."""
        pass

    @staticmethod
    def _base_kwargs(meta: dict[str, Any], body: str) -> dict[str, Any]:
        """Extract common constructor kwargs from frontmatter.

        Subclass from_frontmatter methods should call this and extend with
        type-specific fields, eliminating boilerplate.
        """
        from ..schema import generate_entry_id
        from ..utils.parse import safe_int

        entry_id = meta.get("id", "")
        if not entry_id:
            entry_id = generate_entry_id(meta.get("title", ""))

        prov_data = meta.get("provenance")
        provenance = Provenance.from_dict(prov_data) if prov_data else None

        return {
            "id": entry_id,
            "title": meta.get("title", ""),
            "body": body,
            "summary": meta.get("summary", ""),
            "importance": safe_int(meta.get("importance"), 5),
            "tags": _as_list(meta.get("tags")),
            "aliases": _as_list(meta.get("aliases")),
            "sources": parse_sources(meta.get("sources")),
            "links": parse_links(meta.get("links")),
            "provenance": provenance,
            "metadata": meta.get("metadata", {}),
            "created_at": parse_datetime(meta.get("created_at")),
            "updated_at": parse_datetime(meta.get("updated_at")),
            "_schema_version": safe_int(meta.get("_schema_version"), 0),
        }

    def _base_frontmatter(self) -> dict[str, Any]:
        """Build common frontmatter fields.

        Starts from the undeclared keys captured at load, so declared fields
        set below (and in subclasses) always win over a stale extra.
        """
        meta: dict[str, Any] = dict(self.extra_frontmatter)
        meta.update(
            {
                "id": self.id,
                "title": self.title,
                "type": self.entry_type,
            }
        )

        if self.tags:
            meta["tags"] = self.tags
        if self.aliases:
            meta["aliases"] = self.aliases
        if self.sources:
            meta["sources"] = [s.to_dict() for s in self.sources]
        if self.links:
            meta["links"] = [l.to_dict() for l in self.links]
        if self.provenance:
            prov = self.provenance.to_dict()
            if prov:
                meta["provenance"] = prov
        # Always reported, including at its default (commit 7783335: dropping
        # an explicit `importance: 5` was itself silent data loss). Keeping it
        # here means the index and the renderers see the entry's real value;
        # whether the key reaches the FILE is decided once, centrally, in
        # _frontmatter_for_file.
        meta["importance"] = self.importance
        # created_at/updated_at: from_frontmatter() reads them off the file, but
        # _base_frontmatter() never re-emitted them, so an explicit key in the
        # source was silently dropped on the next save (#151). Emitted only for
        # an entry loaded from a file that carried them (or assigned since --
        # see __setattr__): unlike `importance` there is no meaningful default
        # to compare against -- a pristine instance's timestamp is just its
        # construction time -- so a file that never had the keys must not grow
        # them, and neither must a newly created entry (#46).
        #
        # Emitted as a datetime (second precision), not as an isoformat()
        # string: ruamel writes a str *quoted* and keeps the microseconds,
        # while a datetime becomes a plain YAML timestamp -- the shape such
        # files already use. A value the source file already had is restored
        # to its original node (and therefore its original style) by
        # _restyle_like_source, so this only decides the shape of a *changed*
        # value.
        if self._source_frontmatter is not None:
            for ts_key in _TIMESTAMP_KEYS:
                if ts_key not in self._absent_default_keys:
                    value = getattr(self, ts_key)
                    meta[ts_key] = (
                        value.replace(microsecond=0) if isinstance(value, datetime) else value
                    )
        if self.lifecycle != "active":
            meta["lifecycle"] = self.lifecycle
        if self.metadata:
            meta["metadata"] = self.metadata
        if self._schema_version > 0:
            meta["_schema_version"] = self._schema_version

        return meta

    def to_db_dict(self, kb_name: str, file_path: str) -> dict[str, Any]:
        """Serialize for DB. Subclasses override to add type-specific fields."""
        return {
            "id": self.id,
            "kb_name": kb_name,
            "entry_type": self.entry_type,
            "title": self.title,
            "body": self.body,
            "summary": self.summary,
            "file_path": file_path,
            "lifecycle": self.lifecycle,
            "metadata": self.metadata,
        }

    def _frontmatter_for_file(self) -> dict[str, Any]:
        """``to_frontmatter`` minus the default-valued keys the source lacked.

        Why this is not done inside ``to_frontmatter``: that method has two
        consumers wanting opposite things. The FILE must keep the shape its
        author gave it -- a backlog item written without a `status:` line does
        not grow one because something else was updated (#46). But the INDEX
        is built from ``to_frontmatter`` too (storage/index.py stores whatever
        it does not have a column for into the metadata column), and `sw
        backlog` filters on the `status` and `priority` it finds there. An
        entry loaded from a file with no `status:` key genuinely IS
        `proposed`; suppressing that everywhere would make it invisible to
        every status filter. So suppression is a property of the file, and
        this is the only place it applies.

        Doing it here rather than at each call site is what makes the
        guarantee general: 32 of the 48 registered entry types emit some field
        unconditionally at its default (`ADREntry` its `adr_number` and
        `status`, `ZettelEntry` its `maturity`, `QAAssessmentEntry` four
        keys), and guarding them one by one both misses types and leaves every
        future plugin type unprotected -- which is exactly how `priority` on
        BacklogItemEntry survived the first fix while `rank`, three lines
        below it, was guarded.

        A key is dropped only when the source file lacked it AND its value is
        still the one a pristine instance would emit. An explicit assignment
        clears the key from ``_absent_default_keys`` (see ``__setattr__``), so
        deliberately setting `rank = 0` or `status = "proposed"` still
        reaches the file; the value check is a second guard for any writer
        that reaches the field without going through attribute assignment.

        The same reasoning covers a timestamp whose source node did not parse
        (`created_at:` with no value, `''`, `Jan 15 2026`): the attribute only
        holds ``parse_datetime``'s "now" fallback, and emitting that would
        replace a value the file already had with a wrong one -- worse than
        the drop this branch fixes (#173 review). Those keys get the raw
        source node back, so the restyle keeps it verbatim.
        """
        meta = self.to_frontmatter()
        if self._source_frontmatter is not None:
            for ts_key in self._unparsed_timestamp_keys:
                if ts_key in meta and ts_key in self._source_frontmatter:
                    meta[ts_key] = self._source_frontmatter[ts_key]
            # #557: a key the model could not represent goes back as the file
            # had it, unless the field changed since load -- by assignment
            # (cleared in `__setattr__`) or in place (`links.append`, caught
            # by the value no longer serializing as it did at load).
            loaded = self._unrepresented_keys or {}
            for key, value in self.unrepresented_frontmatter().items():
                if _plain(meta.get(key, _ABSENT)) == loaded[key]:
                    meta[key] = value
        absent = self._absent_default_keys
        if not absent:
            return meta
        pristine = _pristine_frontmatter(type(self))
        return {
            k: v
            for k, v in meta.items()
            if not (k in absent and k in pristine and _plain(pristine[k]) == _plain(v))
        }

    def unrepresented_frontmatter(self) -> dict[str, Any]:
        """The file's keys whose value this entry's model does not reproduce.

        Maps each such key to the value as the file has it: a `provenance:`
        block of the KB's own shape, `importance: high`, a `links: [a, b]`
        shorthand. The write path keeps these as they are (#557); `qa
        validate` uses them to report a reserved key a KB uses for a field of
        its own. Empty for an entry not loaded from a file.
        """
        source = self._source_frontmatter
        if not isinstance(source, Mapping):
            return {}
        return {k: source[k] for k in (self._unrepresented_keys or {}) if k in source}

    def to_markdown(self) -> str:
        """Convert to markdown string with YAML frontmatter."""
        meta = _restyle_like_source(self._frontmatter_for_file(), self._source_frontmatter)
        yaml_front = dump_yaml(meta)
        # Exactly one trailing newline: the loader strips the body anyway, and a
        # body that already ended in newlines produced a blank last line that
        # failed the end-of-file hook on every freshly created entry.
        body = self.body.rstrip("\n")
        return f"---\n{yaml_front}\n---\n" + (f"\n{body}\n" if body else "")

    @classmethod
    def from_markdown(cls, text: str) -> "Entry":
        """Parse from markdown string with YAML frontmatter.

        The opening `---` fence MUST be on line 1. If the file starts
        with anything else (body prose, a blank line, a BOM), the file is
        treated as having no frontmatter — even if a stray `---` divider
        appears later in the body. Pre-fix, ``re.split`` on
        ``^---\\s*$`` with ``MULTILINE`` would match body horizontal-rule
        dividers and feed body prose to the YAML loader, producing
        confusing ``ComposerError``/alias errors deep in ruamel
        (see Tier A bug r1030).
        """
        # Strip a UTF-8 BOM if present so files saved by Windows editors
        # still match the fence-at-line-1 rule.
        if text.startswith("﻿"):
            text = text[1:]

        # Require the fence at line 1. Anything else means no frontmatter
        # block, regardless of body content.
        if not text.startswith(("---\n", "---\r\n")):
            raise FrontmatterError("Invalid entry format: missing YAML frontmatter")

        # Drop the opening fence and split on the next `---` line.
        # maxsplit=1 here so any later `---` lines stay in the body.
        after_open = text.split("\n", 1)[1] if "\n" in text else ""
        close_parts = re.split(r"^---\s*$", after_open, flags=re.MULTILINE, maxsplit=1)
        if len(close_parts) < 2:
            raise FrontmatterError("Invalid entry format: missing YAML frontmatter")

        meta = load_yaml(close_parts[0])
        body = close_parts[1].strip()

        entry = cls.from_frontmatter(meta, body)
        # Restore lifecycle from frontmatter (base field, not in subclass constructors)
        if entry is not None:
            entry.lifecycle = meta.get("lifecycle", "active")
            capture_extra_frontmatter(entry, meta)
        return entry

    @classmethod
    def load(cls, path: Path) -> "Entry":
        """Load entry from file."""
        text = path.read_text(encoding="utf-8")
        entry = cls.from_markdown(text)
        entry.file_path = path
        return entry

    @staticmethod
    def _publish_exclusive(tmp: str, path: Path) -> None:
        """Publish ``tmp`` to ``path``, refusing if ``path`` already exists.

        Tries, in order, the first mechanism this filesystem supports:

        1. ``os.link`` -- atomic, and leaves ``tmp`` a second name for the
           same inode, unlinked below once ``path`` is published.
        2. ``os.link`` raises ``OSError`` other than ``FileExistsError`` on a
           filesystem with no hard-link support (FAT/exFAT, some SMB/FUSE
           mounts) -- every create would fail there, not just a colliding
           one (#391 cold read round 3). Fall back to claiming the NAME
           exclusively (``O_CREAT | O_EXCL``, which every filesystem that
           can create files at all supports), then ``os.replace`` the
           already-fully-written temp file over it: the claim is what is
           exclusive, and the replace that follows only ever targets a path
           this call itself just created, so it cannot overwrite a
           concurrent winner.
        3. If ``O_EXCL`` itself is unsupported (not ``FileExistsError``),
           there is no atomic mechanism left on this filesystem: fall back
           to an ordinary ``os.replace``, guarded only by the write
           pipeline's own ``exists()`` check, same as before this method
           existed.

        Raises ``FileExistsError`` whenever any step finds ``path`` already
        taken; never silently overwrites it. Always consumes ``tmp`` --
        either as the second name ``os.link`` gave it (unlinked here so only
        ``path`` remains), or by moving it via ``os.replace`` -- so the
        caller never has its own cleanup to do on a successful return.
        """
        import os

        try:
            os.link(tmp, path)
            os.unlink(tmp)
            return
        except FileExistsError:
            raise
        except OSError:
            pass

        try:
            os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        except FileExistsError:
            raise
        except OSError:
            pass

        os.replace(tmp, path)

    def save(self, path: Path | None = None, *, exclusive: bool = False) -> Path:
        """Save entry to file.

        Args:
            path: Target path; defaults to ``self.file_path``.
            exclusive: ``True`` publishes exclusively (see
                :meth:`_publish_exclusive`) instead of with ``os.replace`` --
                it raises ``FileExistsError`` if ``path`` already exists,
                instead of silently overwriting it. For the create path only
                (#391 cold read round 2): the write-pipeline's own
                `resolved_path.exists()` check and this publish step are two
                different moments, so two truly concurrent creates can both
                pass the check before either publishes. This closes that
                window at the one place a filesystem call can enforce it
                atomically; it does not replace the check (which gives the
                friendlier error message and DB-index-consistency path in
                the common, non-racing case).
        """
        if path is None:
            path = self.file_path
        if path is None:
            raise ValueError("No path specified and no file_path set")

        path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic replace: agents race on the same file (claim vs reset, claim
        # vs claim) and a plain write_text lets a concurrent load() read a
        # truncated file. Temp file in the same directory so the rename is
        # atomic on POSIX and Windows; a failed write leaves the old file intact.
        import os
        import tempfile

        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(self.to_markdown())
            # mkstemp gives 0600; keep the existing file's mode, or what a
            # plain write would have produced under the current umask.
            try:
                mode = os.stat(path).st_mode & 0o777
            except FileNotFoundError:
                umask = os.umask(0)
                os.umask(umask)
                mode = 0o666 & ~umask
            os.chmod(tmp, mode)
            if exclusive:
                self._publish_exclusive(tmp, path)
            else:
                os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        self.file_path = path
        return path

    def add_link(self, target: str, relation: str, note: str = "", kb: str = "") -> None:
        """Add a link to another entry."""
        self.links.append(Link(target=target, relation=relation, note=note, kb=kb))

    def add_source(self, title: str, url: str, **kwargs) -> None:
        """Add a source reference."""
        self.sources.append(Source(title=title, url=url, **kwargs))

    def validate(self) -> list[str]:
        """Validate entry. Returns list of errors."""
        errors = []
        if not self.id:
            errors.append("Entry must have an ID")
        if not self.title:
            errors.append("Entry must have a title")
        return errors

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(id={self.id!r}, title={self.title!r})"


def parse_datetime(s: Any) -> datetime:
    """Parse a frontmatter timestamp into a timezone-aware ``datetime``.

    Accepted forms:

    * ``datetime`` -- an aware value is returned unchanged (its offset kept);
      a naive one (e.g. an unquoted ``created_at: 2026-01-15T09:00:00``, which
      ruamel hands back as a naive ``TimeStamp``) is anchored to UTC, so
      comparisons against ``_utcnow()`` cannot raise ``TypeError``.
    * ``datetime.date`` -- a bare YAML date (``created_at: 2026-01-15``) is
      loaded by the YAML parser as a ``date``, not a ``str``. Anchored to
      midnight **UTC**: every timestamp the model itself *creates* is UTC
      (``_utcnow``), so a file-authored date must never read back as the
      load time (#151).
    * ISO-8601 ``str`` -- a trailing ``Z`` is treated as UTC; a naive string
      with no offset (e.g. ``2026-01-15T09:00:00``) is anchored to UTC, so
      comparisons against ``_utcnow()`` cannot raise ``TypeError``.
    * anything else (missing/empty/unparseable) -- "now" in UTC.
    """
    if isinstance(s, datetime):
        return s if s.tzinfo is not None else s.replace(tzinfo=UTC)
    if isinstance(s, date):
        return datetime(s.year, s.month, s.day, tzinfo=UTC)
    if not s:
        return _utcnow()
    try:
        # Try ISO format
        if isinstance(s, str):
            s = re.sub(r"Z$", "+00:00", s)
            parsed = datetime.fromisoformat(s)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed
    except Exception:
        logger.warning("Failed to parse datetime: %s", s, exc_info=True)
    return _utcnow()


def _as_list(value: Any) -> list[Any]:
    """A list-valued key (``tags``, ``aliases``) as a list.

    ``tags: Foo`` is one tag. Taken as it was, the str reached the index,
    which iterates it: the entry was tagged `F`, `o` and `kb_get` returned
    ``['F', 'o']``, which an echoed update then wrote to the file (#561).
    The write path keeps the file's own ``tags: Foo`` while the field is
    unchanged (#557).
    """
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    return [value]


def _is_url(text: str) -> bool:
    return text.startswith(("http://", "https://"))


def _scalar_source(s: Any) -> Source:
    """A bare (non-dict) source item: a title (`a book`), or, when it is a
    URL, the source's URL too -- read as a title only,
    `sources: [https://x.org]` lost its URL (`url: ''`) in the index and the
    API (#561)."""
    return Source(title=str(s), url=str(s) if _is_url(str(s)) else "")


def parse_sources(sources_data: Any) -> list[Source]:
    """Parse sources from various formats.

    A bare scalar `sources:` value -- not a list at all, e.g.
    `sources: https://x.org` or `sources: a book` -- is one source, same as
    a one-item list would be: a URL becomes a source with `url`, any other
    text a source with `title` (#569). Read as `[]` before this, a scalar
    source silently disappeared from the index, search and the API.
    """
    if not sources_data:
        return []
    if isinstance(sources_data, list):
        return [
            Source.from_dict(s) if isinstance(s, dict) else _scalar_source(s) for s in sources_data
        ]
    if isinstance(sources_data, str):
        return [_scalar_source(sources_data)]
    return []


def parse_links(links_data: Any) -> list[Link]:
    """Parse links from various formats."""
    if not links_data:
        return []
    if isinstance(links_data, list):
        return [
            Link.from_dict(l) if isinstance(l, dict) else Link(target=str(l), relation="related")
            for l in links_data
        ]
    return []
