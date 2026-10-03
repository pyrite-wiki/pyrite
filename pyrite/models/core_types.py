"""
Core Entry Types

Pyrite ships these ~8 base types that any KB can use out of the box.
Every field is optional except title.
"""

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from ..schema import (
    EventStatus,
    ResearchStatus,
    generate_entry_id,
    generate_event_id,
    validate_date,
    validate_importance,
)
from ..utils.frontmatter import load_frontmatter
from ..utils.parse import safe_int
from .base import Entry, capture_extra_frontmatter
from .collection import CollectionEntry
from .protocols import Locatable, Statusable, Temporal

logger = logging.getLogger(__name__)


@dataclass
class NoteEntry(Entry):
    """General-purpose knowledge note."""

    @property
    def entry_type(self) -> str:
        return "note"

    def to_frontmatter(self) -> dict[str, Any]:
        meta = self._base_frontmatter()
        if self.summary:
            meta["summary"] = self.summary
        return meta

    @classmethod
    def from_frontmatter(cls, meta: dict[str, Any], body: str) -> "NoteEntry":
        return cls(**cls._base_kwargs(meta, body))


@dataclass
class PersonEntry(Locatable, Entry):
    """An individual — person profile."""

    role: str = ""
    affiliations: list[str] = field(default_factory=list)
    research_status: ResearchStatus = ResearchStatus.STUB

    @property
    def entry_type(self) -> str:
        return "person"

    def to_frontmatter(self) -> dict[str, Any]:
        meta = self._base_frontmatter()
        if self.role:
            meta["role"] = self.role
        if self.affiliations:
            meta["affiliations"] = self.affiliations
        meta["research_status"] = self.research_status.value
        if self.summary:
            meta["summary"] = self.summary
        return meta

    @classmethod
    def from_frontmatter(cls, meta: dict[str, Any], body: str) -> "PersonEntry":
        kw = cls._base_kwargs(meta, body)
        status_str = meta.get("research_status", "stub")
        try:
            kw["research_status"] = ResearchStatus(status_str)
        except ValueError:
            kw["research_status"] = ResearchStatus.STUB
        kw["role"] = meta.get("role", "")
        kw["affiliations"] = meta.get("affiliations", []) or []
        return cls(**kw)

    @classmethod
    def create(cls, name: str, **kwargs) -> "PersonEntry":
        """Create a new person entry with auto-generated ID."""
        parts = name.split()
        if len(parts) >= 2:
            entry_id = f"{parts[-1].lower()}-{'-'.join(parts[:-1]).lower()}"
        else:
            entry_id = name.lower()
        entry_id = re.sub(r"[^a-z0-9-]+", "", entry_id)
        return cls(id=entry_id, title=name, **kwargs)


@dataclass
class OrganizationEntry(Locatable, Entry):
    """A group, company, institution."""

    org_type: str = ""  # gov, ngo, corp, etc.
    jurisdiction: str = ""
    founded: str = ""
    research_status: ResearchStatus = ResearchStatus.STUB

    @property
    def entry_type(self) -> str:
        return "organization"

    def to_frontmatter(self) -> dict[str, Any]:
        meta = self._base_frontmatter()
        if self.org_type:
            meta["org_type"] = self.org_type
        if self.jurisdiction:
            meta["jurisdiction"] = self.jurisdiction
        if self.founded:
            meta["founded"] = self.founded
        meta["research_status"] = self.research_status.value
        if self.summary:
            meta["summary"] = self.summary
        return meta

    @classmethod
    def from_frontmatter(cls, meta: dict[str, Any], body: str) -> "OrganizationEntry":
        kw = cls._base_kwargs(meta, body)
        kw["org_type"] = meta.get("org_type", "")
        kw["jurisdiction"] = meta.get("jurisdiction", "")
        kw["founded"] = meta.get("founded", "")
        status_str = meta.get("research_status", "stub")
        try:
            kw["research_status"] = ResearchStatus(status_str)
        except ValueError:
            kw["research_status"] = ResearchStatus.STUB
        return cls(**kw)

    @classmethod
    def create(cls, name: str, **kwargs) -> "OrganizationEntry":
        """Create a new organization entry with auto-generated ID."""
        entry_id = generate_entry_id(name)
        return cls(id=entry_id, title=name, **kwargs)


@dataclass
class EventEntry(Temporal, Locatable, Statusable, Entry):
    """Something that happened — timeline event with canonical date."""

    status: EventStatus = EventStatus.CONFIRMED  # overrides Statusable.status with enum
    participants: list[str] = field(default_factory=list)
    notes: str = ""

    @property
    def entry_type(self) -> str:
        return "event"

    def to_frontmatter(self) -> dict[str, Any]:
        meta = self._base_frontmatter()
        if self.date:
            meta["date"] = self.date
        meta["status"] = self.status.value
        if self.location:
            meta["location"] = self.location
        if self.participants:
            # Serialize as `actors` — the cascade-timeline convention used by
            # the existing corpus and CONTRIBUTING.md. The internal property
            # stays `participants`; only the frontmatter key is `actors`.
            meta["actors"] = self.participants
        if self.notes:
            meta["notes"] = self.notes
        if self.summary:
            meta["summary"] = self.summary
        return meta

    @classmethod
    def from_frontmatter(cls, meta: dict[str, Any], body: str) -> "EventEntry":
        kw = cls._base_kwargs(meta, body)
        kw["date"] = meta.get("date", "")
        kw["location"] = meta.get("location", "")
        # Read `actors` (the written convention); accept legacy `participants`
        # for files written before the rename.
        kw["participants"] = meta.get("actors", meta.get("participants", [])) or []
        kw["notes"] = meta.get("notes", "")
        status_str = meta.get("status", "confirmed")
        try:
            kw["status"] = EventStatus(status_str)
        except ValueError:
            logger.warning(
                "Unknown event status '%s' for entry '%s', defaulting to 'confirmed'. "
                "Valid values: %s",
                status_str,
                meta.get("id", "?"),
                ", ".join(e.value for e in EventStatus),
            )
            kw["status"] = EventStatus.CONFIRMED
        return cls(**kw)

    def validate(self) -> list[str]:
        """Validate event entry."""
        errors = super().validate()

        if not self.date:
            errors.append("Event must have a date")
        elif not validate_date(self.date):
            errors.append(f"Invalid date format: {self.date} (expected YYYY-MM-DD)")

        if not validate_importance(self.importance):
            errors.append(f"Importance must be 1-10, got: {self.importance}")

        return errors

    @classmethod
    def create(cls, date: str, title: str, body: str = "", **kwargs) -> "EventEntry":
        """Create a new event with auto-generated ID."""
        event_id = generate_event_id(date, title)
        return cls(id=event_id, title=title, body=body, date=date, **kwargs)


@dataclass
class DocumentEntry(Temporal, Entry):
    """A reference document."""

    author: str = ""
    document_type: str = ""
    url: str = ""

    @property
    def entry_type(self) -> str:
        return "document"

    def to_frontmatter(self) -> dict[str, Any]:
        meta = self._base_frontmatter()
        if self.date:
            meta["date"] = self.date
        if self.author:
            meta["author"] = self.author
        if self.document_type:
            meta["document_type"] = self.document_type
        if self.url:
            meta["url"] = self.url
        if self.summary:
            meta["summary"] = self.summary
        return meta

    @classmethod
    def from_frontmatter(cls, meta: dict[str, Any], body: str) -> "DocumentEntry":
        kw = cls._base_kwargs(meta, body)
        kw["date"] = meta.get("date", "")
        kw["author"] = meta.get("author", "")
        kw["document_type"] = meta.get("document_type", "")
        kw["url"] = meta.get("url", "")
        return cls(**kw)

    @classmethod
    def create(cls, title: str, **kwargs) -> "DocumentEntry":
        """Create a new document entry."""
        entry_id = generate_entry_id(title)
        return cls(id=entry_id, title=title, **kwargs)


@dataclass
class TopicEntry(Entry):
    """A theme, subject area, or concept."""

    @property
    def entry_type(self) -> str:
        return "topic"

    def to_frontmatter(self) -> dict[str, Any]:
        meta = self._base_frontmatter()
        if self.summary:
            meta["summary"] = self.summary
        return meta

    @classmethod
    def from_frontmatter(cls, meta: dict[str, Any], body: str) -> "TopicEntry":
        return cls(**cls._base_kwargs(meta, body))


@dataclass
class RelationshipEntry(Entry):
    """A connection between entities — reified relationship."""

    source_entity: str = ""
    target_entity: str = ""
    relationship_type: str = ""

    @property
    def entry_type(self) -> str:
        return "relationship"

    def to_frontmatter(self) -> dict[str, Any]:
        meta = self._base_frontmatter()
        meta["source_entity"] = self.source_entity
        meta["target_entity"] = self.target_entity
        if self.relationship_type:
            meta["relationship_type"] = self.relationship_type
        if self.summary:
            meta["summary"] = self.summary
        return meta

    @classmethod
    def from_frontmatter(cls, meta: dict[str, Any], body: str) -> "RelationshipEntry":
        kw = cls._base_kwargs(meta, body)
        kw["source_entity"] = meta.get("source_entity", meta.get("source", ""))
        kw["target_entity"] = meta.get("target_entity", meta.get("target", ""))
        kw["relationship_type"] = meta.get("relationship_type", "")
        return cls(**kw)


@dataclass
class QAAssessmentEntry(Entry):
    """A QA assessment result — quality check on another entry."""

    target_entry: str = ""
    target_kb: str = ""
    tier: int = 1  # 1=structural, 2=semantic, 3=factual
    qa_status: str = "pass"  # pass/warn/fail
    issues: list[dict] = field(default_factory=list)
    issues_found: int = 0
    issues_resolved: int = 0
    assessed_at: str = ""  # ISO timestamp

    @property
    def entry_type(self) -> str:
        return "qa_assessment"

    def to_frontmatter(self) -> dict[str, Any]:
        meta = self._base_frontmatter()
        if self.target_entry:
            meta["target_entry"] = self.target_entry
        if self.target_kb:
            meta["target_kb"] = self.target_kb
        meta["tier"] = self.tier
        meta["qa_status"] = self.qa_status
        if self.issues:
            meta["issues"] = self.issues
        meta["issues_found"] = self.issues_found
        meta["issues_resolved"] = self.issues_resolved
        if self.assessed_at:
            meta["assessed_at"] = self.assessed_at
        if self.summary:
            meta["summary"] = self.summary
        return meta

    @classmethod
    def from_frontmatter(cls, meta: dict[str, Any], body: str) -> "QAAssessmentEntry":
        kw = cls._base_kwargs(meta, body)
        kw["target_entry"] = meta.get("target_entry", "")
        kw["target_kb"] = meta.get("target_kb", "")
        kw["tier"] = safe_int(meta.get("tier"), 1)
        kw["qa_status"] = meta.get("qa_status", "pass")
        kw["issues"] = meta.get("issues", []) or []
        kw["issues_found"] = safe_int(meta.get("issues_found"), 0)
        kw["issues_resolved"] = safe_int(meta.get("issues_resolved"), 0)
        kw["assessed_at"] = meta.get("assessed_at", "")
        return cls(**kw)


@dataclass
class TimelineEntry(Entry):
    """An ordered sequence of events."""

    date_range: str = ""

    @property
    def entry_type(self) -> str:
        return "timeline"

    def to_frontmatter(self) -> dict[str, Any]:
        meta = self._base_frontmatter()
        if self.date_range:
            meta["date_range"] = self.date_range
        if self.summary:
            meta["summary"] = self.summary
        return meta

    @classmethod
    def from_frontmatter(cls, meta: dict[str, Any], body: str) -> "TimelineEntry":
        kw = cls._base_kwargs(meta, body)
        kw["date_range"] = meta.get("date_range", "")
        return cls(**kw)


# Type registry: maps type name string to entry class
ENTRY_TYPE_REGISTRY: dict[str, type[Entry]] = {
    "note": NoteEntry,
    "person": PersonEntry,
    "organization": OrganizationEntry,
    "event": EventEntry,
    "document": DocumentEntry,
    "topic": TopicEntry,
    "relationship": RelationshipEntry,
    "timeline": TimelineEntry,
    "collection": CollectionEntry,
    "qa_assessment": QAAssessmentEntry,
}


def get_entry_class(entry_type: str) -> type[Entry]:
    """Get the entry class for a type name.

    Checks core types first, then plugin registry, then falls back to GenericEntry.
    """
    if entry_type in ENTRY_TYPE_REGISTRY:
        return ENTRY_TYPE_REGISTRY[entry_type]

    # Check plugin registry for custom entry types
    try:
        from ..plugins import get_registry

        plugin_types = get_registry().get_all_entry_types()
        if entry_type in plugin_types:
            return plugin_types[entry_type]
    except Exception:
        logger.warning("Plugin type lookup failed for %s", entry_type, exc_info=True)

    from .generic import GenericEntry

    return GenericEntry


def entry_from_frontmatter(meta: dict[str, Any], body: str) -> Entry:
    """Create an entry from frontmatter, auto-detecting the type."""
    if not meta.get("type"):
        # Silent fallback historically hid misclassifications (see
        # epic-normalization-and-data-cleanup). Surface it as a structured
        # warning so loaders can spot missing `type:` fields. File path is
        # not available at this layer; callers that know the path should
        # log additional context themselves.
        logger.warning(
            "Entry frontmatter missing 'type:' — falling back to 'note' "
            "(id=%s, title=%s, available_keys=%s)",
            meta.get("id", "?"),
            meta.get("title", "?"),
            sorted(meta.keys()),
        )
        entry_type = "note"
    else:
        entry_type = meta["type"]
    cls = get_entry_class(entry_type)
    entry = cls.from_frontmatter(meta, body)
    # Restore lifecycle from frontmatter (base field, not in subclass constructors)
    entry.lifecycle = meta.get("lifecycle", "active")
    capture_extra_frontmatter(entry, meta)
    return entry


def id_text(value: Any) -> str | None:
    """An entry id as text, the one way the index stores it and lookup
    compares it. A YAML scalar id (``id: true``, ``id: 123``, ``id: 1.50``,
    ``id: 2026-01-01``) loads as a bool, number or date; this spells it as
    SQLite's TEXT column always did (``true`` -> ``'1'``), so an id read from
    a file matches its index row. None for no id."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, datetime):
        return value.isoformat(" ")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def read_entry_id(
    text: str, migrate: Callable[[dict[str, Any]], dict[str, Any]] | None = None
) -> str | None:
    """The id an entry file's text holds: THE answer to "which id does this
    file hold" (ADR-0038 decision 1). Lookup (``KBRepository.find_file``,
    and through it create's exists check and delete) and history
    (``entry_id_from_markdown``) all ask it.

    It is the loader's rule: the frontmatter split as ``Entry.from_markdown``
    splits it (the closing ``---`` must be a line of its own, not the first
    ``---`` anywhere), the type dispatched as ``entry_from_frontmatter``
    dispatches it, so the id is an explicit ``id:`` or, when there is none,
    the one the type derives from the title. ``migrate`` is the repository's
    schema migration, applied to the frontmatter before the id is read, as
    the loader applies it.

    None when the text has no frontmatter; raises what the loader would
    raise on malformed frontmatter.
    """
    parsed = load_frontmatter(text)
    if parsed is None:
        return None
    meta, body = parsed
    if migrate is not None:
        meta = migrate(meta)
    if meta.get("id"):
        # Every entry class takes a truthy `id:` as is (`Entry._base_kwargs`),
        # so building the entry only to read it back costs a KB walk its
        # largest share for nothing.
        return id_text(meta["id"])
    return id_text(entry_from_frontmatter(meta, body).id)


def entry_id_from_markdown(text: str) -> str | None:
    """``read_entry_id``, with None for text that is not an entry.

    Used to tell a rename of one entry from a replacement by another: git
    pairs files by content similarity, and entries share frontmatter
    boilerplate, so similarity alone links unrelated entries (#432).
    Comparing ids assumes an id is unique within a KB, which the index
    already relies on.
    """
    try:
        return read_entry_id(text)
    except Exception:
        return None


def explicit_entry_id(text: str) -> str | None:
    """The ``id:`` an entry file's frontmatter states, or None when it
    states none (the id is then derived from the title, which may have
    changed between versions of the same entry)."""
    try:
        parsed = load_frontmatter(text)
        if parsed is None:
            return None
        return id_text(parsed[0].get("id"))
    except Exception:
        return None
