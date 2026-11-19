"""Provenance tracking and relationship types."""

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Source:
    """
    Source reference with verification metadata.

    This is a first-class object for tracking provenance.
    """

    title: str
    url: str
    outlet: str = ""
    date: str | None = None
    author: str = ""
    source_type: str = "news"
    verified: bool = False
    verified_date: str | None = None
    verified_by: str = ""
    archive_url: str = ""
    key_facts_confirmed: list[str] = field(default_factory=list)
    confidence: str = "unverified"  # high, medium, low, unverified
    access: str = "public"  # public, paywalled, restricted, offline
    extra: dict[str, Any] = field(
        default_factory=dict
    )  # Preserve unknown fields (publisher, tier, etc.)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        result = {"title": self.title, "url": self.url}
        if self.outlet:
            result["outlet"] = self.outlet
        if self.date:
            result["date"] = self.date
        if self.author:
            result["author"] = self.author
        if self.source_type != "news":
            result["type"] = self.source_type
        if self.verified:
            result["verified"] = self.verified
            if self.verified_date:
                result["verified_date"] = self.verified_date
            if self.verified_by:
                result["verified_by"] = self.verified_by
        if self.archive_url:
            result["archive_url"] = self.archive_url
        if self.key_facts_confirmed:
            result["key_facts_confirmed"] = self.key_facts_confirmed
        if self.confidence != "unverified":
            result["confidence"] = self.confidence
        if self.access != "public":
            result["access"] = self.access
        # Preserve unknown fields round-trip
        for k, v in self.extra.items():
            if k not in result:
                result[k] = v
        return result

    # Known fields consumed by from_dict — everything else goes to extra
    _KNOWN_KEYS = frozenset(
        {
            "title",
            "url",
            "outlet",
            "date",
            "author",
            "type",
            "verified",
            "verified_date",
            "verified_by",
            "archive_url",
            "key_facts_confirmed",
            "confidence",
            "access",
        }
    )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Source":
        """Create from dictionary, preserving unknown fields in extra."""
        extra = {k: v for k, v in data.items() if k not in cls._KNOWN_KEYS}
        return cls(
            title=data.get("title", ""),
            url=data.get("url", ""),
            outlet=data.get("outlet", ""),
            date=data.get("date"),
            author=data.get("author", ""),
            source_type=data.get("type", "news"),
            verified=data.get("verified", False),
            verified_date=data.get("verified_date"),
            verified_by=data.get("verified_by", ""),
            archive_url=data.get("archive_url", ""),
            key_facts_confirmed=data.get("key_facts_confirmed", []),
            confidence=data.get("confidence", "unverified"),
            access=data.get("access", "public"),
            extra=extra,
        )


@dataclass
class Link:
    """
    Typed relationship between entries.

    Supports both Zettelkasten-style note links and entity relationships.
    """

    target: str  # Target entry ID or path
    relation: str  # Relationship type
    note: str = ""  # Optional description
    kb: str = ""  # Target KB (if cross-KB link)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        result = {"target": self.target, "relation": self.relation}
        if self.note:
            result["note"] = self.note
        if self.kb:
            result["kb"] = self.kb
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Link":
        """Create from dictionary."""
        # Handle legacy format {to: id, type: relation}
        if "to" in data:
            return cls(
                target=data["to"],
                relation=data.get("type", "related"),
                note=data.get("description", ""),
            )
        return cls(
            target=data.get("target", ""),
            relation=data.get("relation", "related"),
            note=data.get("note", ""),
            kb=data.get("kb", ""),
        )


# Relationship types with inverses
RELATIONSHIP_TYPES: dict[str, dict[str, Any]] = {
    # Entity relationships
    "owns": {"inverse": "owned_by", "description": "Ownership relationship"},
    "owned_by": {"inverse": "owns", "description": "Owned by another entity"},
    "controls": {"inverse": "controlled_by", "description": "Control relationship"},
    "controlled_by": {"inverse": "controls", "description": "Controlled by another entity"},
    "directs": {"inverse": "directed_by", "description": "Directorship"},
    "directed_by": {"inverse": "directs", "description": "Directed by another entity"},
    "advises": {"inverse": "advised_by", "description": "Advisory relationship"},
    "advised_by": {"inverse": "advises", "description": "Advised by another entity"},
    "member_of": {"inverse": "has_member", "description": "Membership"},
    "has_member": {"inverse": "member_of", "description": "Has member"},
    "employed_by": {"inverse": "employs", "description": "Employment relationship"},
    "employs": {"inverse": "employed_by", "description": "Employs"},
    "funds": {"inverse": "funded_by", "description": "Funding relationship"},
    "funded_by": {"inverse": "funds", "description": "Funded by"},
    "authored": {"inverse": "authored_by", "description": "Authorship"},
    "authored_by": {"inverse": "authored", "description": "Authored by"},
    "mentions": {"inverse": "mentioned_by", "description": "Mentions"},
    "mentioned_by": {"inverse": "mentions", "description": "Mentioned by"},
    "involves": {"inverse": "involved_in", "description": "Involvement"},
    "involved_in": {"inverse": "involves", "description": "Involved in"},
    "located_at": {"inverse": "location_of", "description": "Location relationship"},
    "location_of": {"inverse": "located_at", "description": "Location of"},
    "related_to": {"inverse": "related_to", "description": "General relationship"},
    # Zettelkasten note relationships
    "supports": {"inverse": "supported_by", "description": "Evidence supporting a claim"},
    "supported_by": {"inverse": "supports", "description": "Supported by evidence"},
    "contradicts": {"inverse": "contradicted_by", "description": "Contradicting evidence"},
    "contradicted_by": {"inverse": "contradicts", "description": "Contradicted by"},
    "extends": {"inverse": "extended_by", "description": "Extends or elaborates on"},
    "extended_by": {"inverse": "extends", "description": "Extended by"},
    "refines": {"inverse": "refined_by", "description": "Refines or narrows"},
    "refined_by": {"inverse": "refines", "description": "Refined by"},
    "is_example_of": {"inverse": "has_example", "description": "Example of a concept"},
    "has_example": {"inverse": "is_example_of", "description": "Has example"},
    "causally_precedes": {"inverse": "causally_follows", "description": "Causal predecessor"},
    "causally_follows": {"inverse": "causally_precedes", "description": "Causal successor"},
    "asks_question": {"inverse": "answers_question", "description": "Poses a question"},
    "answers_question": {"inverse": "asks_question", "description": "Answers a question"},
    "is_part_of": {"inverse": "contains_part", "description": "Part of a larger whole"},
    "contains_part": {"inverse": "is_part_of", "description": "Contains a part"},
    # Relations emitted by the indexer, with their reverse directions (#621).
    "transclusion": {"inverse": "transcluded_by", "description": "Embeds the target entry"},
    "transcluded_by": {"inverse": "transclusion", "description": "Embedded by another entry"},
    "references": {"inverse": "referenced_by", "description": "Object-reference field target"},
    "referenced_by": {"inverse": "references", "description": "Referenced by an object field"},
    # Body wikilink references
    "wikilink": {"inverse": "wikilinked_by", "description": "Inline wikilink reference"},
    "wikilinked_by": {"inverse": "wikilink", "description": "Referenced by inline wikilink"},
    # Body Markdown links to entry files (#1723)
    "markdown_link": {
        "inverse": "markdown_linked_by",
        "description": "Markdown link to the entry's file",
    },
    "markdown_linked_by": {
        "inverse": "markdown_link",
        "description": "Linked to by a Markdown link",
    },
}


@dataclass
class Provenance:
    """
    Provenance tracking for distributed research.

    Tracks who contributed what and when.
    """

    created_by: str = ""
    created_date: str = ""
    last_modified_by: str = ""
    last_modified_date: str = ""
    contributors: list[str] = field(default_factory=list)
    agent_version: str = ""  # For AI agent contributions
    agent_confidence: float = 1.0
    requires_human_review: bool = False
    auto_generated_fields: list[str] = field(default_factory=list)
    human_verified_fields: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary (only non-empty fields)."""
        result = {}
        if self.created_by:
            result["created_by"] = self.created_by
        if self.created_date:
            result["created_date"] = self.created_date
        if self.last_modified_by:
            result["last_modified_by"] = self.last_modified_by
        if self.last_modified_date:
            result["last_modified_date"] = self.last_modified_date
        if self.contributors:
            result["contributors"] = self.contributors
        if self.agent_version:
            result["agent_version"] = self.agent_version
            result["agent_confidence"] = self.agent_confidence
        if self.requires_human_review:
            result["requires_human_review"] = self.requires_human_review
        if self.auto_generated_fields:
            result["auto_generated_fields"] = self.auto_generated_fields
        if self.human_verified_fields:
            result["human_verified_fields"] = self.human_verified_fields
        return result

    @classmethod
    def from_dict(cls, data: Any) -> "Provenance":
        """Create from a dictionary.

        Tolerates malformed frontmatter where ``provenance`` is not a mapping
        (e.g. a bare string or list). In that case we return a default
        Provenance rather than crashing the entry loader.
        """
        if not isinstance(data, dict):
            logger.warning(
                "Ignoring malformed provenance (expected mapping, got %s)",
                type(data).__name__,
            )
            return cls()
        return cls(
            created_by=data.get("created_by", ""),
            created_date=data.get("created_date", ""),
            last_modified_by=data.get("last_modified_by", ""),
            last_modified_date=data.get("last_modified_date", ""),
            contributors=data.get("contributors", []),
            agent_version=data.get("agent_version", ""),
            agent_confidence=data.get("agent_confidence", 1.0),
            requires_human_review=data.get("requires_human_review", False),
            auto_generated_fields=data.get("auto_generated_fields", []),
            human_verified_fields=data.get("human_verified_fields", []),
        )


RELATIONSHIP_ALIASES = {"related": "related_to"}


def get_all_relationship_types() -> dict[str, dict[str, Any]]:
    """Get all relationship types: core + plugin-provided."""
    all_types = dict(RELATIONSHIP_TYPES)
    try:
        from ..plugins import get_registry

        all_types.update(get_registry().get_all_relationship_types())
    except Exception:
        logger.warning("Failed to load plugin relationship types", exc_info=True)
    return all_types


def known_inverse_relation(relation: str) -> str | None:
    """The inverse of a declared relationship type, or None when none is known.

    ``get_inverse_relation`` answers ``related_to`` for an unknown relation,
    which is right for writing a link on the other side (vague but true) and
    wrong for reporting what the relation is. Use this to tell the two apart.
    """
    declared = get_all_relationship_types().get(RELATIONSHIP_ALIASES.get(relation, relation))
    return declared["inverse"] if declared else None


def get_inverse_relation(relation: str) -> str:
    """Get the inverse of a relationship type.

    An unknown relation falls back to ``related_to``. This is a public export
    and ``link --bidirectional`` writes the result onto the target's file, so
    the fallback must stay a claim that is true in both directions (#527).
    """
    all_types = get_all_relationship_types()
    relation = RELATIONSHIP_ALIASES.get(relation, relation)
    if relation in all_types:
        return all_types[relation]["inverse"]
    return "related_to"
