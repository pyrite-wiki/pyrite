"""Cascade Series entry types for investigative journalism research."""

from dataclasses import dataclass, field
from typing import Any

from pyrite_journalism_investigation.entry_types import InvestigationEventEntry

from pyrite.models.base import Entry
from pyrite.models.core_types import EventEntry, OrganizationEntry, PersonEntry, TopicEntry
from pyrite.models.protocols import Locatable

# ---------------------------------------------------------------------------
# Actor — extends PersonEntry
# ---------------------------------------------------------------------------


@dataclass
class ActorEntry(PersonEntry):
    """A person in the Cascade Series — actor profile."""

    tier: int = 0
    era: str = ""
    capture_lanes: list[str] = field(default_factory=list)
    chapters: list[int] = field(default_factory=list)

    @property
    def entry_type(self) -> str:
        return "actor"

    def to_frontmatter(self) -> dict[str, Any]:
        meta = super().to_frontmatter()
        meta["type"] = "actor"
        meta["tier"] = self.tier
        if self.era:
            meta["era"] = self.era
        if self.capture_lanes:
            meta["capture_lanes"] = self.capture_lanes
        if self.chapters:
            meta["chapters"] = self.chapters
        return meta

    @classmethod
    def from_frontmatter(cls, meta: dict[str, Any], body: str) -> "ActorEntry":
        kw = cls._base_kwargs(meta, body)
        from pyrite.schema import ResearchStatus

        status_str = meta.get("research_status", "stub")
        try:
            research_status = ResearchStatus(status_str)
        except ValueError:
            research_status = ResearchStatus.STUB

        return cls(
            **kw,
            role=meta.get("role", ""),
            affiliations=meta.get("affiliations", []) or [],
            research_status=research_status,
            tier=int(meta.get("tier", 0)),
            era=str(meta.get("era", "")),
            capture_lanes=meta.get("capture_lanes", []) or [],
            chapters=meta.get("chapters", []) or [],
        )


# ---------------------------------------------------------------------------
# CascadeOrg — extends OrganizationEntry
# ---------------------------------------------------------------------------


@dataclass
class CascadeOrgEntry(OrganizationEntry):
    """An organization in the Cascade Series."""

    capture_lanes: list[str] = field(default_factory=list)
    chapters: list[int] = field(default_factory=list)

    @property
    def entry_type(self) -> str:
        return "cascade_org"

    def to_frontmatter(self) -> dict[str, Any]:
        meta = super().to_frontmatter()
        meta["type"] = "cascade_org"
        if self.capture_lanes:
            meta["capture_lanes"] = self.capture_lanes
        if self.chapters:
            meta["chapters"] = self.chapters
        return meta

    @classmethod
    def from_frontmatter(cls, meta: dict[str, Any], body: str) -> "CascadeOrgEntry":
        kw = cls._base_kwargs(meta, body)
        from pyrite.schema import ResearchStatus

        status_str = meta.get("research_status", "stub")
        try:
            research_status = ResearchStatus(status_str)
        except ValueError:
            research_status = ResearchStatus.STUB

        return cls(
            **kw,
            org_type=meta.get("org_type", ""),
            jurisdiction=meta.get("jurisdiction", ""),
            founded=str(meta.get("founded", "")),
            research_status=research_status,
            capture_lanes=meta.get("capture_lanes", []) or [],
            chapters=meta.get("chapters", []) or [],
        )


# ---------------------------------------------------------------------------
# CascadeEvent — extends EventEntry (research KB events)
# ---------------------------------------------------------------------------


@dataclass
class CascadeEventEntry(EventEntry):
    """A research KB event in the Cascade Series."""

    era: str = ""
    capture_lanes: list[str] = field(default_factory=list)
    chapters: list[int] = field(default_factory=list)

    @property
    def entry_type(self) -> str:
        return "cascade_event"

    def to_frontmatter(self) -> dict[str, Any]:
        meta = super().to_frontmatter()
        meta["type"] = "cascade_event"
        if self.era:
            meta["era"] = self.era
        if self.capture_lanes:
            meta["capture_lanes"] = self.capture_lanes
        if self.chapters:
            meta["chapters"] = self.chapters
        return meta

    @classmethod
    def from_frontmatter(cls, meta: dict[str, Any], body: str) -> "CascadeEventEntry":
        kw = cls._base_kwargs(meta, body)
        from pyrite.schema import EventStatus

        status_str = meta.get("status", "confirmed")
        try:
            status = EventStatus(status_str)
        except ValueError:
            status = EventStatus.CONFIRMED

        return cls(
            **kw,
            date=str(meta.get("date", meta.get("event_date", ""))),
            status=status,
            location=meta.get("location", ""),
            participants=meta.get("actors", meta.get("participants", [])) or [],
            notes=meta.get("notes", ""),
            era=str(meta.get("era", "")),
            capture_lanes=meta.get("capture_lanes", []) or [],
            chapters=meta.get("chapters", []) or [],
        )


# ---------------------------------------------------------------------------
# TimelineEvent — extends EventEntry (timeline KB events)
# ---------------------------------------------------------------------------


@dataclass
class TimelineEventEntry(InvestigationEventEntry):
    """A timeline event from the Cascade Series timeline KB.

    Extends InvestigationEventEntry to inherit actors, source_refs, and
    verification_status fields from the journalism-investigation plugin.
    """

    capture_lanes: list[str] = field(default_factory=list)
    capture_type: str = ""
    connections: list[str] = field(default_factory=list)
    patterns: list[str] = field(default_factory=list)

    @property
    def entry_type(self) -> str:
        return "timeline_event"

    def to_frontmatter(self) -> dict[str, Any]:
        meta = super().to_frontmatter()
        meta["type"] = "timeline_event"
        if self.capture_lanes:
            meta["capture_lanes"] = self.capture_lanes
        if self.capture_type:
            meta["capture_type"] = self.capture_type
        if self.connections:
            meta["connections"] = self.connections
        if self.patterns:
            meta["patterns"] = self.patterns
        return meta

    @classmethod
    def from_frontmatter(cls, meta: dict[str, Any], body: str) -> "TimelineEventEntry":
        kw = cls._base_kwargs(meta, body)
        from pyrite.schema import EventStatus

        status_str = meta.get("status", "confirmed")
        try:
            status = EventStatus(status_str)
        except ValueError:
            status = EventStatus.CONFIRMED

        return cls(
            **kw,
            date=str(meta.get("date", "")).strip("'"),
            status=status,
            location=meta.get("location", ""),
            participants=meta.get("actors", meta.get("participants", [])) or [],
            notes=meta.get("notes", ""),
            # JI-inherited fields
            actors=meta.get("actors", []) or [],
            source_refs=meta.get("source_refs", []) or [],
            verification_status=meta.get("verification_status", "unverified"),
            # Cascade-specific fields
            capture_lanes=meta.get("capture_lanes", []) or [],
            capture_type=meta.get("capture_type", ""),
            connections=meta.get("connections", []) or [],
            patterns=meta.get("patterns", []) or [],
        )


# ---------------------------------------------------------------------------
# SolidarityEvent — extends EventEntry (solidarity KB events)
# ---------------------------------------------------------------------------


@dataclass
class SolidarityEventEntry(EventEntry):
    """A solidarity/resistance event from the Infrastructure of Solidarity timeline."""

    infrastructure_types: list[str] = field(default_factory=list)
    actors: list[str] = field(default_factory=list)
    lineage: list[str] = field(default_factory=list)
    lineage_notes: str = ""
    legacy: list[str] = field(default_factory=list)
    legacy_notes: str = ""
    capture_response: list[str] = field(default_factory=list)
    outcome: str = ""

    @property
    def entry_type(self) -> str:
        return "solidarity_event"

    def to_frontmatter(self) -> dict[str, Any]:
        meta = super().to_frontmatter()
        meta["type"] = "solidarity_event"
        if self.infrastructure_types:
            meta["infrastructure_types"] = self.infrastructure_types
        if self.actors:
            meta["actors"] = self.actors
        if self.lineage:
            meta["lineage"] = self.lineage
        if self.lineage_notes:
            meta["lineage_notes"] = self.lineage_notes
        if self.legacy:
            meta["legacy"] = self.legacy
        if self.legacy_notes:
            meta["legacy_notes"] = self.legacy_notes
        if self.capture_response:
            meta["capture_response"] = self.capture_response
        if self.outcome:
            meta["outcome"] = self.outcome
        return meta

    @classmethod
    def from_frontmatter(cls, meta: dict[str, Any], body: str) -> "SolidarityEventEntry":
        kw = cls._base_kwargs(meta, body)
        from pyrite.schema import EventStatus

        status_str = meta.get("status", "confirmed")
        try:
            status = EventStatus(status_str)
        except ValueError:
            status = EventStatus.CONFIRMED

        return cls(
            **kw,
            date=str(meta.get("date", "")).strip("'"),
            status=status,
            location=meta.get("location", ""),
            participants=meta.get("actors", meta.get("participants", [])) or [],
            notes=meta.get("notes", ""),
            infrastructure_types=meta.get("infrastructure_types", []) or [],
            actors=meta.get("actors", []) or [],
            lineage=meta.get("lineage", []) or [],
            lineage_notes=meta.get("lineage_notes", ""),
            legacy=meta.get("legacy", []) or [],
            legacy_notes=meta.get("legacy_notes", ""),
            capture_response=meta.get("capture_response", []) or [],
            outcome=meta.get("outcome", ""),
        )


# ---------------------------------------------------------------------------
# Theme — extends TopicEntry
# ---------------------------------------------------------------------------


@dataclass
class ThemeEntry(TopicEntry):
    """A thematic topic in the Cascade Series."""

    research_status: str = "stub"

    @property
    def entry_type(self) -> str:
        return "theme"

    def to_frontmatter(self) -> dict[str, Any]:
        meta = super().to_frontmatter()
        meta["type"] = "theme"
        meta["research_status"] = self.research_status
        return meta

    @classmethod
    def from_frontmatter(cls, meta: dict[str, Any], body: str) -> "ThemeEntry":
        kw = cls._base_kwargs(meta, body)
        return cls(
            **kw,
            research_status=meta.get("research_status", "stub"),
        )


# ---------------------------------------------------------------------------
# Victim — extends Entry
# ---------------------------------------------------------------------------


@dataclass
class VictimEntry(Locatable, Entry):
    """A victim entry in the Cascade Series."""

    era: str = ""
    research_status: str = "stub"

    @property
    def entry_type(self) -> str:
        return "victim"

    def to_frontmatter(self) -> dict[str, Any]:
        meta = self._base_frontmatter()
        meta["type"] = "victim"
        if self.era:
            meta["era"] = self.era
        if self.location:
            meta["location"] = self.location
        meta["research_status"] = self.research_status
        if self.summary:
            meta["summary"] = self.summary
        return meta

    @classmethod
    def from_frontmatter(cls, meta: dict[str, Any], body: str) -> "VictimEntry":
        kw = cls._base_kwargs(meta, body)
        return cls(
            **kw,
            era=str(meta.get("era", "")),
            location=meta.get("location", ""),
            research_status=meta.get("research_status", "stub"),
        )


# ---------------------------------------------------------------------------
# Statistic — extends Entry
# ---------------------------------------------------------------------------


@dataclass
class StatisticEntry(Entry):
    """A statistical data point in the Cascade Series."""

    era: str = ""
    data_type: str = ""
    research_status: str = "stub"
    verified: bool = False

    @property
    def entry_type(self) -> str:
        return "statistic"

    def to_frontmatter(self) -> dict[str, Any]:
        meta = self._base_frontmatter()
        meta["type"] = "statistic"
        if self.era:
            meta["era"] = self.era
        if self.data_type:
            meta["data_type"] = self.data_type
        meta["research_status"] = self.research_status
        meta["verified"] = self.verified
        if self.summary:
            meta["summary"] = self.summary
        return meta

    @classmethod
    def from_frontmatter(cls, meta: dict[str, Any], body: str) -> "StatisticEntry":
        kw = cls._base_kwargs(meta, body)
        return cls(
            **kw,
            era=str(meta.get("era", "")),
            data_type=str(meta.get("data_type", "")),
            research_status=meta.get("research_status", "stub"),
            verified=bool(meta.get("verified", False)),
        )


# ---------------------------------------------------------------------------
# Mechanism — extends Entry
# ---------------------------------------------------------------------------


@dataclass
class MechanismEntry(Entry):
    """A capture mechanism in the Cascade Series."""

    synopsis: str = ""
    related_orgs: list[str] = field(default_factory=list)
    related_actors: list[str] = field(default_factory=list)
    chapters: list[int] = field(default_factory=list)
    word_count: int = 0

    @property
    def entry_type(self) -> str:
        return "mechanism"

    def to_frontmatter(self) -> dict[str, Any]:
        meta = self._base_frontmatter()
        meta["type"] = "mechanism"
        if self.synopsis:
            meta["synopsis"] = self.synopsis
        if self.related_orgs:
            meta["related_orgs"] = self.related_orgs
        if self.related_actors:
            meta["related_actors"] = self.related_actors
        if self.chapters:
            meta["chapters"] = self.chapters
        meta["word_count"] = self.word_count
        if self.summary:
            meta["summary"] = self.summary
        return meta

    @classmethod
    def from_frontmatter(cls, meta: dict[str, Any], body: str) -> "MechanismEntry":
        kw = cls._base_kwargs(meta, body)
        return cls(
            **kw,
            synopsis=meta.get("synopsis", ""),
            related_orgs=meta.get("related_orgs", []) or [],
            related_actors=meta.get("related_actors", []) or [],
            chapters=meta.get("chapters", []) or [],
            word_count=int(meta.get("word_count", 0)),
        )


# ---------------------------------------------------------------------------
# Scene — extends Entry
# ---------------------------------------------------------------------------


@dataclass
class SceneEntry(Entry):
    """A reconstructed scene in the Cascade Series."""

    scene_date: str = ""
    era: str = ""
    related_events: list[str] = field(default_factory=list)
    actors: list[str] = field(default_factory=list)

    @property
    def entry_type(self) -> str:
        return "scene"

    def to_frontmatter(self) -> dict[str, Any]:
        meta = self._base_frontmatter()
        meta["type"] = "scene"
        if self.scene_date:
            meta["scene_date"] = self.scene_date
        if self.era:
            meta["era"] = self.era
        if self.related_events:
            meta["related_events"] = self.related_events
        if self.actors:
            meta["actors"] = self.actors
        if self.summary:
            meta["summary"] = self.summary
        return meta

    @classmethod
    def from_frontmatter(cls, meta: dict[str, Any], body: str) -> "SceneEntry":
        kw = cls._base_kwargs(meta, body)
        return cls(
            **kw,
            scene_date=str(meta.get("scene_date", "")),
            era=str(meta.get("era", "")),
            related_events=meta.get("related_events", []) or [],
            actors=meta.get("actors", []) or [],
        )
