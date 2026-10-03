"""Core entry types and type metadata."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .kb_schema import KBSchema

logger = logging.getLogger(__name__)


# Core entry types shipped with pyrite
CORE_TYPES: dict[str, dict[str, Any]] = {
    "note": {
        "description": "General-purpose knowledge note",
        "subdirectory": "notes",
        "fields": {"tags": "list[str]", "links": "list[Link]"},
    },
    "person": {
        "description": "An individual",
        "subdirectory": "people",
        "fields": {
            "role": "str",
            "affiliations": "list[str]",
        },
    },
    "organization": {
        "description": "A group, company, institution",
        "subdirectory": "organizations",
        "fields": {
            "org_type": "str",  # gov, ngo, corp, etc.
            "jurisdiction": "str",
            "founded": "str",
        },
    },
    "event": {
        "description": "Something that happened",
        "subdirectory": "events",
        "fields": {
            "date": "str",
            "location": "str",
            "importance": "int",
            "status": "EventStatus",
            "actors": "list[str]",
        },
    },
    "document": {
        "description": "A reference document",
        "subdirectory": "documents",
        "fields": {
            "date": "str",
            "author": "str",
            "document_type": "str",
            "url": "str",
        },
    },
    "topic": {
        "description": "A theme, subject area, or concept",
        "subdirectory": "topics",
        "fields": {},
    },
    "relationship": {
        "description": "A connection between entities",
        "subdirectory": "relationships",
        "fields": {
            "source_entity": "str",
            "target_entity": "str",
            "relationship_type": "str",
        },
    },
    "timeline": {
        "description": "An ordered sequence of events",
        "subdirectory": "timelines",
        "fields": {
            "date_range": "str",
        },
    },
    "collection": {
        "description": "A folder-backed collection of entries",
        "subdirectory": None,  # Collections live in-place, not a dedicated folder
        "fields": {
            "source_type": "str",
            "description": "str",
            "icon": "str",
            "view_config": "dict",
            "folder_path": "str",
        },
    },
}


# =============================================================================
# System Intent -- truth-functional defaults every KB inherits
# =============================================================================

SYSTEM_INTENT: dict[str, Any] = {
    "guidelines": {
        "sourcing": "Claims should link to evidence. Prefer primary sources.",
        "cross_linking": "Entries should link to related entries for discoverability.",
        "completeness": "Fill all required fields. Use summary for quick orientation.",
    },
    "evaluation_rubric": [
        {"text": "Entry has a descriptive title", "checker": "descriptive_title"},
        {"text": "Entry body is non-empty", "covered_by": "schema"},
        {"text": "Entry has at least one tag", "checker": "has_tags"},
        {
            "text": "Entry links to at least one related entry (unless a stub)",
            "checker": "has_outlinks",
        },
    ],
}


# =============================================================================
# Core Type Metadata -- AI instructions, field descriptions, display hints
# =============================================================================

CORE_TYPE_METADATA: dict[str, dict[str, Any]] = {
    "note": {
        "ai_instructions": (
            "General-purpose knowledge note. Use for observations, analysis, "
            "summaries, or any content that doesn't fit a more specific type. "
            "Keep notes focused on a single topic."
        ),
        "field_descriptions": {
            "title": "Descriptive title summarizing the note's content",
            "tags": "Categorization tags for discovery and filtering",
            "links": "Typed relationships to other entries",
            "summary": "Brief one-line summary of the note",
        },
        "protocols": [],
        "display": {"icon": "file-text", "layout": "document"},
    },
    "event": {
        "ai_instructions": (
            "A specific occurrence with a known or approximate date. Always "
            "include a date. Set importance 1-10 based on significance. Use "
            "participants to link people/orgs involved."
        ),
        "field_descriptions": {
            "date": "When the event occurred (YYYY-MM-DD)",
            "importance": "Significance score from 1 (minor) to 10 (major)",
            "status": "Verification status: confirmed, disputed, alleged, rumored",
            "location": "Where the event took place",
            "actors": "People or organizations involved",
        },
        "protocols": ["temporal", "locatable", "statusable"],
        "display": {"icon": "calendar", "layout": "record"},
        # A hand-written file may say `participants:`; EventEntry reads it as `actors`.
        "field_aliases": {"participants": "actors"},
        "evaluation_rubric": [
            {"text": "Event has a date field", "covered_by": "schema"},
            {"text": "Event has an importance score between 1 and 10", "covered_by": "schema"},
        ],
    },
    "person": {
        "ai_instructions": (
            "An individual person. Use role to describe their primary function "
            "or position. List organizational affiliations. Set importance "
            "based on relevance to research."
        ),
        "field_descriptions": {
            "role": "Primary role, title, or position",
            "affiliations": "Organizations this person is associated with",
            "importance": "Relevance score from 1 (peripheral) to 10 (central)",
            "research_status": "How complete the profile is: stub, partial, draft, complete, published",
        },
        "protocols": ["locatable"],
        "display": {"icon": "user", "layout": "record"},
        "evaluation_rubric": [
            {
                "text": "Person has a role or position described",
                "checker": "has_field",
                "params": {"field": "role"},
            },
        ],
    },
    "organization": {
        "ai_instructions": (
            "A group, company, government body, or institution. Specify "
            "org_type (gov, ngo, corp, media, political, military, etc.) "
            "and jurisdiction where relevant."
        ),
        "field_descriptions": {
            "org_type": "Category: gov, ngo, corp, media, political, military, academic, religious, etc.",
            "jurisdiction": "Geographic or legal jurisdiction",
            "founded": "Date or year the organization was established",
            "importance": "Relevance score from 1 to 10",
        },
        "protocols": ["locatable"],
        "display": {"icon": "building", "layout": "record"},
    },
    "document": {
        "ai_instructions": (
            "A reference to an external document: report, filing, memo, "
            "article, etc. Always include the source URL when available. "
            "Use document_type to classify."
        ),
        "field_descriptions": {
            "date": "Publication or filing date",
            "author": "Author or issuing body",
            "document_type": "Category: report, filing, memo, article, press-release, court-document, etc.",
            "url": "URL to the original document",
        },
        "protocols": ["temporal"],
        "display": {"icon": "file", "layout": "document"},
        "evaluation_rubric": [
            {
                "text": "Document has a source URL or author",
                "checker": "has_any_field",
                "params": {"fields": ["url", "author"]},
            },
            {
                "text": "Document has a document_type classification",
                "checker": "has_field",
                "params": {"field": "document_type"},
            },
        ],
    },
    "topic": {
        "ai_instructions": (
            "A theme, subject area, or concept that entries can be organized "
            "around. Use as a hub to connect related entries via links and tags."
        ),
        "field_descriptions": {
            "importance": "Centrality of this topic to the research, 1-10",
        },
        "protocols": [],
        "display": {"icon": "hash", "layout": "document"},
    },
    "relationship": {
        "ai_instructions": (
            "A reified relationship between two entities. Use when the "
            "relationship itself has properties worth tracking (dates, "
            "evidence, context). For simple links, use the links field instead."
        ),
        "field_descriptions": {
            "source_entity": "Entry ID of the source entity",
            "target_entity": "Entry ID of the target entity",
            "relationship_type": "Type of relationship (see relationship_types in schema)",
        },
        "protocols": [],
        "display": {"icon": "link", "layout": "record"},
        # Legacy spellings RelationshipEntry.from_frontmatter reads.
        "field_aliases": {"source": "source_entity", "target": "target_entity"},
    },
    "timeline": {
        "ai_instructions": (
            "An ordered sequence of related events. Use to group events "
            "into a narrative arc. Reference individual events via links."
        ),
        "field_descriptions": {
            "date_range": "Time span covered, e.g. '2024-01 to 2025-06'",
        },
        "protocols": [],
        "display": {"icon": "clock", "layout": "document"},
    },
    "collection": {
        "ai_instructions": (
            "A folder-backed collection that groups related entries. "
            "Created automatically from __collection.yaml files in KB folders. "
            "Use to organize entries into browsable groups with list or table views."
        ),
        "field_descriptions": {
            "source_type": "Collection source: 'folder' for filesystem-backed",
            "description": "Human-readable description of the collection's purpose",
            "icon": "Icon identifier for display",
            "view_config": "View settings: default_view, table_columns",
            "folder_path": "Relative path to the folder within the KB",
        },
        "protocols": [],
        "display": {"icon": "folder", "layout": "record"},
    },
}


def resolve_type_metadata(type_name: str, kb_schema: KBSchema | None = None) -> dict[str, Any]:
    """Resolve type metadata using 4-layer precedence.

    Resolution order (highest priority first):
        1. KB-level overrides from kb.yaml
        2. Plugin metadata via get_type_metadata()
        3. Core defaults from CORE_TYPE_METADATA
        4. Empty defaults

    Returns:
        Dict with keys: ai_instructions, field_descriptions, display.
    """
    result: dict[str, Any] = {
        "ai_instructions": "",
        "field_descriptions": {},
        "protocols": [],
        "display": {},
        "guidelines": "",
        "goals": "",
        "evaluation_rubric": [],
    }

    # Layer 1: Core defaults (lowest priority base)
    if type_name in CORE_TYPE_METADATA:
        core = CORE_TYPE_METADATA[type_name]
        result["ai_instructions"] = core.get("ai_instructions", "")
        result["field_descriptions"] = dict(core.get("field_descriptions", {}))
        result["protocols"] = list(core.get("protocols", []))
        result["display"] = dict(core.get("display", {}))
        result["guidelines"] = core.get("guidelines", "")
        result["goals"] = core.get("goals", "")
        result["evaluation_rubric"] = list(core.get("evaluation_rubric", []))

    # Layer 2: Plugin metadata (overrides core)
    try:
        from ..plugins import get_registry

        plugin_meta = get_registry().get_all_type_metadata()
        if type_name in plugin_meta:
            pm = plugin_meta[type_name]
            if pm.get("ai_instructions"):
                result["ai_instructions"] = pm["ai_instructions"]
            result["field_descriptions"].update(pm.get("field_descriptions", {}))
            if pm.get("protocols"):
                result["protocols"] = list(pm["protocols"])
            result["display"].update(pm.get("display", {}))
            if pm.get("guidelines"):
                result["guidelines"] = pm["guidelines"]
            if pm.get("goals"):
                result["goals"] = pm["goals"]
            if pm.get("evaluation_rubric"):
                result["evaluation_rubric"] = list(pm["evaluation_rubric"])
    except Exception:
        logger.warning("Failed to load plugin type metadata for %s", type_name, exc_info=True)

    # Layer 3: KB-level overrides (highest priority)
    if kb_schema and type_name in kb_schema.types:
        ts = kb_schema.types[type_name]
        if ts.ai_instructions:
            result["ai_instructions"] = ts.ai_instructions
        if ts.field_descriptions:
            result["field_descriptions"].update(ts.field_descriptions)
        if ts.protocols:
            result["protocols"] = list(ts.protocols)
        if ts.display:
            result["display"].update(ts.display)
        if ts.guidelines:
            result["guidelines"] = ts.guidelines
        if ts.goals:
            result["goals"] = ts.goals
        if ts.evaluation_rubric:
            result["evaluation_rubric"] = list(ts.evaluation_rubric)

    return result
