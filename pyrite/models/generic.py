"""
Generic Entry Model

For custom types defined in kb.yaml that don't match a core type.
Custom fields live in self.metadata and round-trip through frontmatter.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .base import Entry

logger = logging.getLogger(__name__)

# Fields that are handled by Entry base or known frontmatter keys
_KNOWN_KEYS = {
    "id",
    "title",
    "type",
    "body",
    "summary",
    "tags",
    "aliases",
    "sources",
    "links",
    "provenance",
    "metadata",
    "created_at",
    "updated_at",
    "_schema_version",
    "file_path",
    "importance",
    "lifecycle",
}

# Sentinel for `GenericEntry._raw_metadata`: distinguishes "the source file had
# no `metadata:` key" from "it had one whose value was `null`".
_NO_RAW_METADATA = object()


@dataclass
class GenericEntry(Entry):
    """
    Flexible entry for kb.yaml-defined custom types.

    Custom fields live in self.metadata.
    Frontmatter round-trips all unknown keys through metadata.
    """

    _entry_type: str = "note"

    # Keys that arrived under an explicit nested `metadata:` block. Only these
    # are written back nested; undeclared top-level keys are promoted instead,
    # so the same key can never be emitted twice (issue #149). Private
    # bookkeeping, declared like Entry's `_absent_default_keys` and
    # `_source_frontmatter`: not a constructor argument, not compared, not in
    # `repr`. `dataclasses.replace` resets it, the same limitation
    # `_absent_default_keys` has (no caller in the tree today), and because it
    # is `compare=False` two entries can compare equal yet serialise
    # differently -- by design.
    _nested_metadata_keys: frozenset[str] = field(
        default=frozenset(), init=False, repr=False, compare=False
    )

    # The raw value of a `metadata:` key the loader could not model as a
    # mapping (a string, a list, a number, `null`). `metadata` is in
    # `_BASE_CONSUMED_KEYS`, so `capture_extra_frontmatter` will not carry it
    # and the next save would delete the author's value; keeping it here makes
    # the round trip faithful. Private bookkeeping, declared like
    # `_nested_metadata_keys`.
    _raw_metadata: Any = field(default=_NO_RAW_METADATA, init=False, repr=False, compare=False)

    # The nested value of a key the source spells twice (`rank: 1` and
    # `metadata: {rank: 2}`): the model reads the top-level spelling (Andon
    # #994, decision A) and a save must lose nothing (#1001, decision 4), so
    # the nested value is kept here and written back under `metadata:`.
    _shadowed_nested: dict[str, Any] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )

    @property
    def entry_type(self) -> str:
        return self._entry_type

    def to_frontmatter(self) -> dict[str, Any]:
        meta = self._base_frontmatter()
        if self.summary:
            meta["summary"] = self.summary
        # `_base_frontmatter` writes the whole `self.metadata` mapping as a
        # nested block; for an undeclared top-level key that duplicates the
        # promoted copy below and grows a `metadata:` block the source file
        # never had (#149). Keys that came from an explicit `metadata:` block
        # stay nested. A key whose name the base frontmatter already emits
        # (`title`, `id`, `type`, `importance`, ...) has to stay nested too:
        # promoting it would overwrite the base value, and dropping it -- what
        # the promotion loop used to do -- silently deleted a caller's value on
        # the create path. Everything else is promoted to the top level.
        nested = {k: v for k, v in self.metadata.items() if k in self._nested_metadata_keys}
        # A key the source spelled twice keeps its nested value nested and
        # its top-level value promoted: a save loses nothing (decision 4).
        nested.update(self._shadowed_nested)
        promoted: dict[str, Any] = {}
        for key, value in self.metadata.items():
            if key in nested and key not in self._shadowed_nested:
                continue
            if key in meta:
                nested[key] = value
            else:
                promoted[key] = value
        if nested:
            meta["metadata"] = nested
        elif self._raw_metadata is not _NO_RAW_METADATA:
            # A non-mapping `metadata:` value from the source file; write it
            # back unchanged so a save does not delete it.
            meta["metadata"] = self._raw_metadata
        else:
            meta.pop("metadata", None)
        meta.update(promoted)
        return meta

    @classmethod
    def from_frontmatter(cls, meta: dict[str, Any], body: str) -> "GenericEntry":
        kw = cls._base_kwargs(meta, body)

        # `metadata:` is optional, but when the file carries it, it must be a
        # mapping. A null or non-mapping value used to raise out of the merge
        # below, which made the loader fall back to another entry class and save
        # the file back as `type: event`; it is now treated as empty for
        # `self.metadata` and kept verbatim in `_raw_metadata` so the save can
        # write the author's value back.
        raw_metadata = meta.get("metadata")
        if "metadata" in meta and not isinstance(raw_metadata, Mapping):
            logger.warning(
                "%s %r: `metadata` frontmatter is %s, not a mapping; ignoring it for"
                " `metadata` and writing the value back verbatim",
                cls.__name__,
                meta.get("id", ""),
                type(raw_metadata).__name__,
            )
        explicit_metadata = dict(raw_metadata) if isinstance(raw_metadata, Mapping) else {}

        # Collect unknown frontmatter keys into metadata
        extra_metadata = {k: v for k, v in meta.items() if k not in _KNOWN_KEYS}
        # Merge: `metadata.k` is a second spelling of field `k` (Andon #994,
        # decision A), so on a collision the field's own name wins, as it does
        # for a declared field. The double spelling is reported by `index
        # health` and `qa validate`; an update of that field is refused.
        kw["metadata"] = {**explicit_metadata, **extra_metadata}

        kw["lifecycle"] = meta.get("lifecycle", "active")
        kw["_entry_type"] = meta.get("type", "note")
        entry = cls(**kw)
        # Remember which of those keys were nested in the source, so
        # to_frontmatter puts exactly them back nested and promotes the rest
        # (#149). Set after construction: it is private bookkeeping, not a field
        # the YAML layer should fill (mirrors Entry's `_absent_default_keys`).
        entry._nested_metadata_keys = frozenset(
            k for k in explicit_metadata if k not in extra_metadata
        )
        entry._shadowed_nested = {k: v for k, v in explicit_metadata.items() if k in extra_metadata}
        if "metadata" in meta and not isinstance(raw_metadata, Mapping):
            # Not modelling it as `self.metadata` must not mean losing it:
            # `metadata` is in `_BASE_CONSUMED_KEYS`, so
            # `capture_extra_frontmatter` will not carry it either. The save
            # path writes this back unchanged.
            entry._raw_metadata = raw_metadata
        return entry
