"""Journalism Investigation plugin for pyrite."""

import logging
import secrets
from typing import Any, ClassVar

from pyrite.plugins.capabilities import Capability

from .entry_types import (
    AccountEntry,
    AssetEntry,
    ClaimEntry,
    DocumentSourceEntry,
    EvidenceEntry,
    FundingEntry,
    InvestigationEventEntry,
    LegalActionEntry,
    MembershipEntry,
    OwnershipEntry,
    TransactionEntry,
)
from .hooks import enrich_connection_links
from .preset import JOURNALISM_INVESTIGATION_PRESET, KNOWN_ENTITIES_PRESET
from .queries import (
    query_claims,
    query_entities,
    query_evidence_chain,
    query_network,
    query_sources,
    query_timeline,
)
from .utils import parse_meta
from .validators import validate_investigation_entry

logger = logging.getLogger(__name__)


def _safe_error(e: Exception) -> str:
    """The message an MCP create tool may show for ``e``.

    ADR-0037 theme 2 round 2 (conductor cold read of 5d65caa7, item 1): a
    broad ``except Exception as e: return {"error": str(e)}`` catches a
    StorageError/PluginError/ConfigError (or a subclass that doesn't set its
    own) too, and those can carry server-side detail in str(e) -- a real
    path, a driver's own text. public_message, when set, is what every
    other transport already shows; the real detail still reaches the log.
    """
    public_message = getattr(e, "public_message", None)
    if public_message is not None:
        logger.warning("%s", e)
        return public_message
    return str(e)


# Prefix for the fresh guard name used when a scoped caller may not read the KB
# a tool resolves to (#223). KB names are unrestricted, so a fixed public name
# could itself be a real KB and serve its entries. A fresh unguessable name on
# every denied call cannot be registered ahead of use. It remains a *name*
# rather than None on purpose: None is how the query functions spell "every
# KB", so passing it would serve the index.
_UNREADABLE_KB_PREFIX = "(unreadable-"


class JournalismInvestigationPlugin:
    """Journalism Investigation plugin for pyrite.

    Provides entry types for investigative journalism research:
    entities (asset, account, document_source), events (investigation_event,
    transaction, legal_action), and investigation-specific relationship types.
    """

    name = "journalism_investigation"
    # Tier A r1500 (Option B): declared plugin capabilities.
    capabilities: ClassVar[set[Capability]] = {
        Capability.SCHEMA,
        Capability.STORAGE,
        Capability.SURFACE,
        Capability.DOMAIN,
        Capability.CONTEXT,
    }

    def __init__(self):
        self.ctx = None

    def set_context(self, ctx) -> None:
        """Receive shared dependencies from the plugin infrastructure."""
        self.ctx = ctx

    def _default_investigation_kb(self) -> str | None:
        """Return the name of the first journalism-investigation KB, or None."""
        if self.ctx is not None and hasattr(self.ctx, "config") and self.ctx.config:
            for kb in self.ctx.config.knowledge_bases:
                if getattr(kb, "kb_type", None) == "journalism-investigation":
                    return kb.name
        return None

    def _resolve_kb(self, args: dict[str, Any]) -> str:
        """Resolve kb_name from args, default investigation KB, or 'investigation'."""
        return args.get("kb_name") or self._default_investigation_kb() or "investigation"

    def _readable_kb(self, args: dict[str, Any], readable_kbs: set[str] | None) -> str:
        """The KB this call reads, or a name that matches nothing (#223).

        `_resolve_kb` settles on one KB -- the caller's, or this plugin's
        default -- and a scoped caller may not be able to read that default.
        The chokepoint already refuses a call that *names* an unreadable KB, so
        the default is the case left, and it is answered with whatever the
        query returns for a KB that has no entries: the tool's own shape, with
        nothing in it.

        A guard *name* rather than `None`, on purpose. `None` is how the query
        functions spell "every KB", so passing it for an unreadable default
        would serve the index instead of nothing.
        """
        kb = self._resolve_kb(args)
        if readable_kbs is None or kb in readable_kbs:
            return kb
        return f"{_UNREADABLE_KB_PREFIX}{secrets.token_hex(32)})"

    def _scoped_kb_names(
        self, requested: list[str] | None, readable_kbs: set[str] | None
    ) -> list[str] | None:
        """The KBs a cross-KB tool may actually search (#223).

        `None` is how `cross_kb_search` and `find_duplicates` spell "every KB",
        so an unscoped caller keeps it unchanged. A scoped caller gets the
        intersection of the KBs they asked for and the ones they may read, or
        simply the readable set when they asked for nothing -- and an empty
        result stays an *empty list*, never `None`, because `None` would search
        the whole index. Callers short-circuit on the empty list.
        """
        if readable_kbs is None:
            return requested
        if not requested:
            return sorted(readable_kbs)
        return [name for name in requested if name in readable_kbs]

    def get_entry_types(self) -> dict[str, type]:
        return {
            "asset": AssetEntry,
            "account": AccountEntry,
            "document_source": DocumentSourceEntry,
            "investigation_event": InvestigationEventEntry,
            "transaction": TransactionEntry,
            "legal_action": LegalActionEntry,
            "claim": ClaimEntry,
            "evidence": EvidenceEntry,
            "ownership": OwnershipEntry,
            "membership": MembershipEntry,
            "funding": FundingEntry,
        }

    def get_cli_commands(self) -> list[tuple[str, Any]]:
        from .cli import investigation_app

        return [("investigation", investigation_app)]

    def get_kb_types(self) -> list[str]:
        return ["journalism-investigation", "known-entities"]

    def get_kb_presets(self) -> dict[str, dict]:
        return {
            "journalism-investigation": JOURNALISM_INVESTIGATION_PRESET,
            "known-entities": KNOWN_ENTITIES_PRESET,
        }

    def get_relationship_types(self) -> dict[str, dict]:
        # Note: member_of/has_member, funded_by/funds, investigated/investigated_by
        # are already registered by the cascade plugin. We only register types
        # that are new to journalism-investigation.
        return {
            "owns": {
                "inverse": "owned_by",
                "description": "Entity owns an asset or organization",
            },
            "owned_by": {
                "inverse": "owns",
                "description": "Asset or organization is owned by an entity",
            },
            "sourced_from": {
                "inverse": "source_for",
                "description": "Claim or fact is sourced from a document",
            },
            "source_for": {
                "inverse": "sourced_from",
                "description": "Document is a source for a claim or fact",
            },
            "corroborates": {
                "inverse": "corroborated_by",
                "description": "Evidence corroborates a claim",
            },
            "corroborated_by": {
                "inverse": "corroborates",
                "description": "Claim is corroborated by evidence",
            },
            "contradicts": {
                "inverse": "contradicted_by",
                "description": "Evidence contradicts a claim",
            },
            "contradicted_by": {
                "inverse": "contradicts",
                "description": "Claim is contradicted by evidence",
            },
            "beneficial_owner_of": {
                "inverse": "beneficially_owned_by",
                "description": "Person is the beneficial owner of an entity",
            },
            "beneficially_owned_by": {
                "inverse": "beneficial_owner_of",
                "description": "Entity is beneficially owned by a person",
            },
            "transacted_with": {
                "inverse": "received_transaction_from",
                "description": "Entity transacted with another entity",
            },
            "received_transaction_from": {
                "inverse": "transacted_with",
                "description": "Entity received a transaction from another entity",
            },
            "party_to": {
                "inverse": "has_party",
                "description": "Entity is a party to a legal action",
            },
            "has_party": {
                "inverse": "party_to",
                "description": "Legal action has an entity as a party",
            },
            "supports": {
                "inverse": "supported_by",
                "description": "Evidence supports a claim",
            },
            "supported_by": {
                "inverse": "supports",
                "description": "Claim is supported by evidence",
            },
            "same_as": {
                "inverse": "same_as",
                "description": "Entity is the same as another entity in a different KB",
            },
        }

    def get_validators(self) -> list:
        return [validate_investigation_entry]

    def get_hooks(self) -> dict[str, list]:
        return {"before_save": [enrich_connection_links]}

    def get_mcp_tools(self, tier: str) -> dict[str, dict]:
        tools: dict[str, dict[str, Any]] = {}
        if tier in ("read", "write", "admin"):
            tools["investigation_timeline"] = {
                "description": "Search for events in your investigation by date, actor, or type. Example: find all events involving 'Putin' since 2020",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "from_date": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                        "to_date": {"type": "string", "description": "End date (YYYY-MM-DD)"},
                        "actor": {
                            "type": "string",
                            "description": "Filter by actor name (substring match)",
                        },
                        "event_type": {
                            "type": "string",
                            "description": "Filter by type: investigation_event, transaction, legal_action",
                        },
                        "min_importance": {
                            "type": "integer",
                            "description": "Minimum importance (1-10)",
                        },
                        "limit": {"type": "integer", "description": "Max results (default 50)"},
                        "kb_name": {
                            "type": "string",
                            "description": "KB name (auto-detected if omitted)",
                        },
                    },
                    "required": [],
                },
                "handler": self._mcp_timeline,
            }
            tools["investigation_entities"] = {
                "description": "Look up people, organizations, or assets in your investigation. Filter by type (person/org/asset), jurisdiction, or importance",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "entity_type": {
                            "type": "string",
                            "description": "Filter by type: person, organization, asset, account",
                        },
                        "min_importance": {
                            "type": "integer",
                            "description": "Minimum importance (1-10)",
                        },
                        "jurisdiction": {
                            "type": "string",
                            "description": "Filter by jurisdiction (substring match)",
                        },
                        "limit": {"type": "integer", "description": "Max results (default 50)"},
                        "kb_name": {
                            "type": "string",
                            "description": "KB name (auto-detected if omitted)",
                        },
                    },
                    "required": [],
                },
                "handler": self._mcp_entities,
            }
            tools["investigation_network"] = {
                "description": "See all connections for a specific entity — who they're linked to, what events involve them, and through what relationships. Paged per direction: at most `limit` of each (default 50), with the true totals and `truncated` in the response",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "entry_id": {
                            "type": "string",
                            "description": "Entry ID to get network for",
                        },
                        "kb_name": {
                            "type": "string",
                            "description": "KB name (auto-detected if omitted)",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max results per direction (default 50; 0 = no cap)",
                        },
                        "offset": {
                            "type": "integer",
                            "description": "Results to skip per direction (default 0)",
                        },
                    },
                    "required": ["entry_id"],
                },
                "handler": self._mcp_network,
            }
            tools["investigation_sources"] = {
                "description": "Find source documents by reliability tier, classification, or date range. Helps identify gaps in sourcing",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "reliability": {
                            "type": "string",
                            "description": "Filter by reliability: high, medium, low, unknown",
                        },
                        "classification": {
                            "type": "string",
                            "description": "Filter by classification: public, leaked, foia, court_filing, etc.",
                        },
                        "from_date": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                        "to_date": {"type": "string", "description": "End date (YYYY-MM-DD)"},
                        "limit": {"type": "integer", "description": "Max results (default 50)"},
                        "kb_name": {
                            "type": "string",
                            "description": "KB name (auto-detected if omitted)",
                        },
                    },
                    "required": [],
                },
                "handler": self._mcp_sources,
            }
            tools["investigation_claims"] = {
                "description": "Review claims by verification status. Find unverified claims that need evidence, or see what's been corroborated",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "claim_status": {
                            "type": "string",
                            "description": "Filter by status: unverified, partially_verified, corroborated, disputed, retracted",
                        },
                        "confidence": {
                            "type": "string",
                            "description": "Filter by confidence: high, medium, low",
                        },
                        "min_importance": {
                            "type": "integer",
                            "description": "Minimum importance (1-10)",
                        },
                        "limit": {"type": "integer", "description": "Max results (default 50)"},
                        "kb_name": {
                            "type": "string",
                            "description": "KB name (auto-detected if omitted)",
                        },
                    },
                    "required": [],
                },
                "handler": self._mcp_claims,
            }
            tools["investigation_evidence_chain"] = {
                "description": "Trace the full evidence chain for a claim — from the claim through supporting evidence to original source documents",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "claim_id": {"type": "string", "description": "Claim entry ID to trace"},
                        "kb_name": {
                            "type": "string",
                            "description": "KB name (auto-detected if omitted)",
                        },
                    },
                    "required": ["claim_id"],
                },
                "handler": self._mcp_evidence_chain,
            }
            tools["investigation_money_flow"] = {
                "description": "Trace money flows for an entity — follow transaction chains, aggregate totals, and detect circular flows",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "entry_id": {
                            "type": "string",
                            "description": "Entity entry ID to trace flows for",
                        },
                        "direction": {
                            "type": "string",
                            "description": "Flow direction: outbound, inbound, or both (default both)",
                        },
                        "max_hops": {
                            "type": "integer",
                            "description": "Max transaction hops to follow (default 3)",
                        },
                        "from_date": {
                            "type": "string",
                            "description": "Start date filter (YYYY-MM-DD)",
                        },
                        "to_date": {
                            "type": "string",
                            "description": "End date filter (YYYY-MM-DD)",
                        },
                        "kb_name": {
                            "type": "string",
                            "description": "KB name (auto-detected if omitted)",
                        },
                    },
                    "required": ["entry_id"],
                },
                "handler": self._mcp_money_flow,
            }
            tools["investigation_qa_report"] = {
                "description": "Run a quality check on your investigation — finds missing sources, unverified claims, and structural issues",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "kb_name": {
                            "type": "string",
                            "description": "KB name (auto-detected if omitted)",
                        },
                        "stale_days": {
                            "type": "integer",
                            "description": "Days before unverified claims are stale (default 30)",
                        },
                    },
                    "required": [],
                },
                "handler": self._mcp_qa_report,
            }
            tools["investigation_export_pack"] = {
                "description": "Export the investigation as a self-contained pack in JSON or Markdown format. Includes timeline, entities, connections, claims, sources, and evidence chains",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "kb_name": {
                            "type": "string",
                            "description": "KB name (auto-detected if omitted)",
                        },
                        "format": {
                            "type": "string",
                            "description": "Export format: json or markdown (default json)",
                        },
                        "redact_sources": {
                            "type": "boolean",
                            "description": "Redact source URLs and titles (default false)",
                        },
                        "min_importance": {
                            "type": "integer",
                            "description": "Minimum importance filter (1-10, default 0 = all)",
                        },
                    },
                    "required": [],
                },
                "handler": self._mcp_export_pack,
            }
            tools["investigation_ownership_chain"] = {
                "description": "Trace ownership chains for an entity through intermediaries to find beneficial owners, compute effective ownership percentages, and detect shell companies",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "entry_id": {
                            "type": "string",
                            "description": "Entity entry ID to trace ownership for",
                        },
                        "kb_name": {
                            "type": "string",
                            "description": "KB name (auto-detected if omitted)",
                        },
                        "max_depth": {
                            "type": "integer",
                            "description": "Maximum chain depth to traverse (default 5)",
                        },
                    },
                    "required": ["entry_id"],
                },
                "handler": self._mcp_ownership_chain,
            }
            tools["investigation_find_duplicates"] = {
                "description": "Scan for duplicate entities across multiple KBs using exact, alias, and fuzzy title matching",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "kb_names": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "KBs to scan (omit for all)",
                        },
                        "entry_types": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Filter to specific types (default: person, organization, asset, account)",
                        },
                        "threshold": {
                            "type": "number",
                            "description": "Minimum fuzzy match ratio (default 0.85)",
                        },
                        "kb_name": {
                            "type": "string",
                            "description": "KB name (for context, auto-detected if omitted)",
                        },
                    },
                    "required": [],
                },
                "handler": self._mcp_find_duplicates,
            }
            tools["investigation_ftm_export"] = {
                "description": "Export investigation entries as FollowTheMoney (FtM) JSON for Aleph interop. Optionally filter by entry type",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "kb_name": {
                            "type": "string",
                            "description": "KB name (auto-detected if omitted)",
                        },
                        "entry_types": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Entry types to export (e.g. person, organization). Omit for all",
                        },
                    },
                    "required": [],
                },
                "handler": self._mcp_ftm_export,
            }
            # Read-only tools: they take the readable set and write nothing, so
            # they belong at the read tier (MCP write tools need per-KB write).
            tools["investigation_search_all"] = {
                "description": "Search across all your investigation KBs at once. Returns results grouped by KB so you can see where an entity or topic appears across investigations",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                        "kb_names": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Specific KBs to search (omit for all)",
                        },
                        "entry_type": {"type": "string", "description": "Filter by entry type"},
                        "correlate": {
                            "type": "boolean",
                            "description": "Group results by entity identity across KBs (default false)",
                        },
                        "limit": {"type": "integer", "description": "Max results (default 50)"},
                    },
                    "required": ["query"],
                },
                "handler": self._mcp_search_all,
            }
            tools["investigation_status"] = {
                "description": "Get a comprehensive status report for the investigation — entity/event/claim counts, unverified claims, and evidence gaps. Use this to rebuild context when returning to an investigation",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "kb_name": {
                            "type": "string",
                            "description": "KB name (auto-detected if omitted)",
                        },
                    },
                    "required": [],
                },
                "handler": self._mcp_investigation_status,
            }
        if tier in ("write", "admin"):
            tools["investigation_create_entity"] = {
                "description": "Add a new person, organization, or asset to your investigation. Provide a type and title at minimum",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "entity_type": {
                            "type": "string",
                            "description": "Type: person, organization, asset, account",
                        },
                        "title": {"type": "string", "description": "Entity name/title"},
                        "body": {"type": "string", "description": "Description and context"},
                        "importance": {
                            "type": "integer",
                            "description": "Importance 1-10 (default 5)",
                        },
                        "fields": {
                            "type": "object",
                            "description": "Type-specific fields (e.g. asset_type, jurisdiction)",
                        },
                        "tags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Tags",
                        },
                        "kb_name": {
                            "type": "string",
                            "description": "KB name (auto-detected if omitted)",
                        },
                    },
                    "required": ["entity_type", "title"],
                },
                "handler": self._mcp_create_entity,
            }
            tools["investigation_create_event"] = {
                "description": "Record a new event or incident in your investigation timeline. Specify a type, title, and date",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "event_type": {
                            "type": "string",
                            "description": "Type: investigation_event, transaction, legal_action",
                        },
                        "title": {"type": "string", "description": "Event title"},
                        "date": {"type": "string", "description": "Event date (YYYY-MM-DD)"},
                        "body": {"type": "string", "description": "Narrative context"},
                        "importance": {
                            "type": "integer",
                            "description": "Importance 1-10 (default 5)",
                        },
                        "fields": {
                            "type": "object",
                            "description": "Type-specific fields (e.g. sender, receiver, case_type)",
                        },
                        "tags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Tags",
                        },
                        "kb_name": {
                            "type": "string",
                            "description": "KB name (auto-detected if omitted)",
                        },
                    },
                    "required": ["event_type", "title", "date"],
                },
                "handler": self._mcp_create_event,
            }
            tools["investigation_create_claim"] = {
                "description": "Document a claim or allegation that needs verification. Provide the assertion text and optionally link evidence",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Claim title"},
                        "assertion": {
                            "type": "string",
                            "description": "The specific factual assertion",
                        },
                        "evidence_refs": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Wikilinks to evidence entries",
                        },
                        "body": {"type": "string", "description": "Narrative context"},
                        "importance": {
                            "type": "integer",
                            "description": "Importance 1-10 (default 5)",
                        },
                        "tags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Tags",
                        },
                        "kb_name": {
                            "type": "string",
                            "description": "KB name (auto-detected if omitted)",
                        },
                    },
                    "required": ["title", "assertion"],
                },
                "handler": self._mcp_create_claim,
            }
            tools["investigation_start"] = {
                "description": "Start a new investigation — create the investigation entry with scope, key questions, and initial entities to research",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Investigation title"},
                        "scope": {
                            "type": "string",
                            "description": "What the investigation is about",
                        },
                        "key_questions": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Key questions to investigate",
                        },
                        "initial_entities": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "type": {
                                        "type": "string",
                                        "description": "person, organization, asset, account",
                                    },
                                },
                            },
                            "description": "Initial entities to create",
                        },
                        "kb_name": {
                            "type": "string",
                            "description": "KB name (auto-detected if omitted)",
                        },
                    },
                    "required": ["title"],
                },
                "handler": self._mcp_investigation_start,
            }
            tools["investigation_promote_claim"] = {
                "description": "Promote a corroborated or partially-verified claim to a structured edge-entity (ownership, membership, funding) in the investigation graph",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "claim_id": {
                            "type": "string",
                            "description": "ID of the claim entry to promote",
                        },
                        "edge_type": {
                            "type": "string",
                            "description": "Edge type: ownership, membership, funding",
                        },
                        "kb_name": {
                            "type": "string",
                            "description": "KB name (auto-detected if omitted)",
                        },
                        "endpoint_fields": {
                            "type": "object",
                            "properties": {
                                name: {"type": "string"}
                                for name in (
                                    "owner",
                                    "asset",
                                    "funder",
                                    "recipient",
                                    "person",
                                    "organization",
                                )
                            },
                            "additionalProperties": False,
                            "description": (
                                "The edge type's own required relationship fields: "
                                "{owner, asset} for ownership, {funder, recipient} for "
                                "funding, {person, organization} for membership. A "
                                "promotion missing them is refused (#424); the same "
                                "check runs for dry_run."
                            ),
                        },
                        "dry_run": {
                            "type": "boolean",
                            "description": "Preview without creating (default false)",
                        },
                    },
                    "required": ["claim_id", "edge_type"],
                },
                "handler": self._mcp_promote_claim,
            }
            tools["investigation_log_source"] = {
                "description": "Log a source document (news article, court filing, leak, etc.) with reliability assessment and classification",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Source document title"},
                        "url": {"type": "string", "description": "Source URL"},
                        "reliability": {
                            "type": "string",
                            "description": "Reliability: high, medium, low, unknown",
                        },
                        "classification": {
                            "type": "string",
                            "description": "Classification: public, leaked, foia, court_filing, etc.",
                        },
                        "obtained_method": {
                            "type": "string",
                            "description": "How the source was obtained",
                        },
                        "body": {"type": "string", "description": "Notes about the source"},
                        "importance": {
                            "type": "integer",
                            "description": "Importance 1-10 (default 5)",
                        },
                        "tags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Tags",
                        },
                        "kb_name": {
                            "type": "string",
                            "description": "KB name (auto-detected if omitted)",
                        },
                    },
                    "required": ["title"],
                },
                "handler": self._mcp_log_source,
            }
            tools["investigation_bulk_edges"] = {
                "description": "Create multiple connection entries (ownership, membership, funding) at once. Validates all edges before creating, skips duplicates, and auto-generates titles/IDs",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "edges": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "type": {
                                        "type": "string",
                                        "description": "Edge type: ownership, membership, funding",
                                    },
                                    "fields": {
                                        "type": "object",
                                        "description": "Type-specific fields (e.g. owner, asset for ownership)",
                                    },
                                    "title": {
                                        "type": "string",
                                        "description": "Optional custom title (auto-generated if omitted)",
                                    },
                                },
                                "required": ["type", "fields"],
                            },
                            "description": "Array of edge definitions to create",
                        },
                        "kb_name": {
                            "type": "string",
                            "description": "KB name (auto-detected if omitted)",
                        },
                        "dry_run": {
                            "type": "boolean",
                            "description": "Preview without creating (default false)",
                        },
                    },
                    "required": ["edges"],
                },
                "handler": self._mcp_bulk_edges,
            }
            tools["investigation_ftm_import"] = {
                "description": "Import FollowTheMoney (FtM) entities into the investigation KB for Aleph interop",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "entities": {
                            "type": "array",
                            "items": {"type": "object"},
                            "description": "Array of FtM entity objects with id, schema, and properties",
                        },
                        "kb_name": {
                            "type": "string",
                            "description": "KB name (auto-detected if omitted)",
                        },
                        "dry_run": {
                            "type": "boolean",
                            "description": "Preview without importing (default false)",
                        },
                    },
                    "required": ["entities"],
                },
                "handler": self._mcp_ftm_import,
            }
        return tools

    # =========================================================================
    # Internal helpers
    # =========================================================================

    def _get_db(self):
        """Get DB from injected context, falling back to self-bootstrap."""
        if self.ctx is not None:
            return self.ctx.db, False
        from pyrite.config import load_config
        from pyrite.storage.database import PyriteDB

        config = load_config()
        return PyriteDB(config.settings.index_path), True

    # =========================================================================
    # Read-tier MCP tool handlers — delegate to pure query functions
    # =========================================================================

    def _mcp_timeline(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None = None
    ) -> dict[str, Any]:
        db, should_close = self._get_db()
        try:
            return query_timeline(
                db,
                self._readable_kb(args, readable_kbs),
                from_date=args.get("from_date", ""),
                to_date=args.get("to_date", ""),
                actor=args.get("actor", ""),
                event_type=args.get("event_type", ""),
                min_importance=args.get("min_importance", 0),
                limit=args.get("limit", 50),
            )
        finally:
            if should_close:
                db.close()

    def _mcp_entities(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None = None
    ) -> dict[str, Any]:
        db, should_close = self._get_db()
        try:
            return query_entities(
                db,
                self._readable_kb(args, readable_kbs),
                entity_type=args.get("entity_type", ""),
                min_importance=args.get("min_importance", 0),
                jurisdiction=args.get("jurisdiction", ""),
                limit=args.get("limit", 50),
            )
        finally:
            if should_close:
                db.close()

    def _mcp_network(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None = None
    ) -> dict[str, Any]:
        db, should_close = self._get_db()
        try:
            return query_network(
                db,
                self._readable_kb(args, readable_kbs),
                args["entry_id"],
                limit=args.get("limit", 50),
                offset=args.get("offset", 0),
                readable_kbs=readable_kbs,
            )
        finally:
            if should_close:
                db.close()

    def _mcp_sources(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None = None
    ) -> dict[str, Any]:
        db, should_close = self._get_db()
        try:
            return query_sources(
                db,
                self._readable_kb(args, readable_kbs),
                reliability=args.get("reliability", ""),
                classification=args.get("classification", ""),
                from_date=args.get("from_date", ""),
                to_date=args.get("to_date", ""),
                limit=args.get("limit", 50),
            )
        finally:
            if should_close:
                db.close()

    def _mcp_claims(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None = None
    ) -> dict[str, Any]:
        db, should_close = self._get_db()
        try:
            return query_claims(
                db,
                self._readable_kb(args, readable_kbs),
                claim_status=args.get("claim_status", ""),
                confidence=args.get("confidence", ""),
                min_importance=args.get("min_importance", 0),
                limit=args.get("limit", 50),
            )
        finally:
            if should_close:
                db.close()

    def _mcp_evidence_chain(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None = None
    ) -> dict[str, Any]:
        db, should_close = self._get_db()
        try:
            return query_evidence_chain(db, self._readable_kb(args, readable_kbs), args["claim_id"])
        finally:
            if should_close:
                db.close()

    def _mcp_export_pack(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None = None
    ) -> dict[str, Any]:
        from .export import build_investigation_pack, export_as_json, export_as_markdown

        db, should_close = self._get_db()
        try:
            pack = build_investigation_pack(
                db,
                self._readable_kb(args, readable_kbs),
                redact_sources=args.get("redact_sources", False),
                min_importance=args.get("min_importance", 0),
            )
            fmt = args.get("format", "json")
            if fmt == "markdown":
                return {"format": "markdown", "content": export_as_markdown(pack)}
            return {"format": "json", "content": export_as_json(pack)}
        finally:
            if should_close:
                db.close()

    def _mcp_money_flow(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None = None
    ) -> dict[str, Any]:
        from .money_flow import trace_money_flow

        db, should_close = self._get_db()
        try:
            return trace_money_flow(
                db,
                self._readable_kb(args, readable_kbs),
                args["entry_id"],
                direction=args.get("direction", "both"),
                max_hops=args.get("max_hops", 3),
                from_date=args.get("from_date", ""),
                to_date=args.get("to_date", ""),
            )
        finally:
            if should_close:
                db.close()

    def _mcp_qa_report(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None = None
    ) -> dict[str, Any]:
        from .qa import compute_qa_metrics

        db, should_close = self._get_db()
        try:
            return compute_qa_metrics(
                db,
                self._readable_kb(args, readable_kbs),
                stale_days=args.get("stale_days", 30),
            )
        finally:
            if should_close:
                db.close()

    def _mcp_ownership_chain(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None = None
    ) -> dict[str, Any]:
        from .ownership import trace_ownership_chain

        db, should_close = self._get_db()
        try:
            return trace_ownership_chain(
                db,
                self._readable_kb(args, readable_kbs),
                entity_id=args["entry_id"],
                max_depth=args.get("max_depth", 5),
            )
        finally:
            if should_close:
                db.close()

    def _mcp_find_duplicates(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None = None
    ) -> dict[str, Any]:
        """Find near-duplicates, narrowed to the KBs the caller may read (#223)."""
        from .dedup import find_duplicates

        kb_names = self._scoped_kb_names(args.get("kb_names"), readable_kbs)
        if kb_names is not None and not kb_names:
            return {"duplicates": []}

        db, should_close = self._get_db()
        try:
            return {
                "duplicates": find_duplicates(
                    db,
                    kb_names=kb_names,
                    entry_types=args.get("entry_types"),
                    threshold=args.get("threshold", 0.85),
                ),
            }
        finally:
            if should_close:
                db.close()

    # =========================================================================
    # Write-tier MCP tool handlers
    # =========================================================================

    def _get_kb_service(self):
        """Get KBService from context."""
        if self.ctx is not None and hasattr(self.ctx, "kb_service"):
            return self.ctx.kb_service
        return None

    def _mcp_create_entity(self, args: dict[str, Any]) -> dict[str, Any]:
        """Create an entity entry."""
        from pyrite.schema import generate_entry_id

        kb_name = self._resolve_kb(args)
        entity_type = args["entity_type"]
        title = args["title"]

        valid_types = {"person", "organization", "asset", "account"}
        if entity_type not in valid_types:
            return {"error": f"Invalid entity_type: {entity_type}. Must be one of {valid_types}"}

        entry_id = generate_entry_id(title)
        fields = args.get("fields", {}) or {}

        kb_service = self._get_kb_service()
        if kb_service is None:
            return {
                "error": "No KB service available — write tools require a running server context"
            }

        try:
            kb_service.create_entry(
                kb_name=kb_name,
                entry_id=entry_id,
                title=title,
                entry_type=entity_type,
                body=args.get("body", ""),
                importance=args.get("importance", 5),
                tags=args.get("tags", []),
                **fields,
            )
            return {"created": entry_id, "type": entity_type, "title": title}
        except Exception as e:
            return {"error": _safe_error(e)}

    def _mcp_create_event(self, args: dict[str, Any]) -> dict[str, Any]:
        """Create an event entry."""
        from pyrite.schema import generate_entry_id

        kb_name = self._resolve_kb(args)
        event_type = args["event_type"]
        title = args["title"]
        date = args["date"]

        valid_types = {"investigation_event", "transaction", "legal_action"}
        if event_type not in valid_types:
            return {"error": f"Invalid event_type: {event_type}. Must be one of {valid_types}"}

        entry_id = generate_entry_id(title)
        fields = args.get("fields", {}) or {}

        kb_service = self._get_kb_service()
        if kb_service is None:
            return {
                "error": "No KB service available — write tools require a running server context"
            }

        try:
            kb_service.create_entry(
                kb_name=kb_name,
                entry_id=entry_id,
                title=title,
                entry_type=event_type,
                body=args.get("body", ""),
                date=date,
                importance=args.get("importance", 5),
                tags=args.get("tags", []),
                **fields,
            )
            return {"created": entry_id, "type": event_type, "title": title}
        except Exception as e:
            return {"error": _safe_error(e)}

    def _mcp_create_claim(self, args: dict[str, Any]) -> dict[str, Any]:
        """Create a claim entry."""
        from pyrite.schema import generate_entry_id

        kb_name = self._resolve_kb(args)
        title = args["title"]
        assertion = args["assertion"]

        entry_id = generate_entry_id(title)
        evidence_refs = args.get("evidence_refs", []) or []
        warnings = []

        if not evidence_refs:
            warnings.append("Claim created with no evidence references — mark for follow-up")

        kb_service = self._get_kb_service()
        if kb_service is None:
            return {
                "error": "No KB service available — write tools require a running server context"
            }

        try:
            kb_service.create_entry(
                kb_name=kb_name,
                entry_id=entry_id,
                title=title,
                entry_type="claim",
                body=args.get("body", ""),
                assertion=assertion,
                claim_status="unverified",
                confidence="low",
                evidence_refs=evidence_refs,
                importance=args.get("importance", 5),
                tags=args.get("tags", []),
            )
            result: dict[str, Any] = {"created": entry_id, "type": "claim", "title": title}
            if warnings:
                result["warnings"] = warnings
            return result
        except Exception as e:
            return {"error": _safe_error(e)}

    def _mcp_log_source(self, args: dict[str, Any]) -> dict[str, Any]:
        """Log a source document."""
        from pyrite.schema import generate_entry_id

        kb_name = self._resolve_kb(args)
        title = args["title"]

        entry_id = generate_entry_id(title)

        kb_service = self._get_kb_service()
        if kb_service is None:
            return {
                "error": "No KB service available — write tools require a running server context"
            }

        try:
            kb_service.create_entry(
                kb_name=kb_name,
                entry_id=entry_id,
                title=title,
                entry_type="document_source",
                body=args.get("body", ""),
                reliability=args.get("reliability", "unknown"),
                classification=args.get("classification", ""),
                url=args.get("url", ""),
                obtained_method=args.get("obtained_method", ""),
                importance=args.get("importance", 5),
                tags=args.get("tags", []),
            )
            return {"created": entry_id, "type": "document_source", "title": title}
        except Exception as e:
            return {"error": _safe_error(e)}

    def _mcp_search_all(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None = None
    ) -> dict[str, Any]:
        """Search across KBs, narrowed to the ones the caller may read (#223).

        Omitting `kb_names` used to mean "every KB"; for a scoped caller it now
        means "every KB you may read", and a request that names KBs keeps the
        ones among them that are readable. `None` still means every KB for an
        unscoped caller (global admin, operator API key, local stdio).
        """
        from .cross_kb_search import correlate_results, cross_kb_search

        kb_names = self._scoped_kb_names(args.get("kb_names"), readable_kbs)
        if kb_names is not None and not kb_names:
            return {"query": args["query"], "total_count": 0, "groups": []}

        db, should_close = self._get_db()
        try:
            result = cross_kb_search(
                db,
                args["query"],
                kb_names=kb_names,
                entry_type=args.get("entry_type"),
                limit=args.get("limit", 50),
            )

            if args.get("correlate"):
                flat = [r for g in result["groups"] for r in g["results"]]
                correlated = correlate_results(flat)
                return {
                    "query": args["query"],
                    "total_count": result["total_count"],
                    "correlated": correlated,
                    "summary": f"Found {len(correlated)} entities across {len(result['groups'])} KBs",
                }

            return result
        finally:
            if should_close:
                db.close()

    def _mcp_investigation_start(self, args: dict[str, Any]) -> dict[str, Any]:
        """Create a new investigation."""
        from .investigation_setup import create_investigation

        db, should_close = self._get_db()
        try:
            return create_investigation(
                db=db,
                kb_name=self._resolve_kb(args),
                title=args.get("title", ""),
                scope=args.get("scope", ""),
                key_questions=args.get("key_questions"),
                initial_entities=args.get("initial_entities"),
            )
        finally:
            if should_close:
                db.close()

    def _mcp_investigation_status(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None = None
    ) -> dict[str, Any]:
        """Get investigation status report."""
        from .investigation_setup import build_investigation_status

        db, should_close = self._get_db()
        try:
            return build_investigation_status(db, self._readable_kb(args, readable_kbs))
        finally:
            if should_close:
                db.close()

    def _mcp_bulk_edges(self, args: dict[str, Any]) -> dict[str, Any]:
        """Create multiple connection entries in batch."""
        from .bulk import create_edge_batch

        db, should_close = self._get_db()
        kb_name = self._resolve_kb(args)
        try:
            return create_edge_batch(
                db=db,
                kb_name=kb_name,
                edges=args.get("edges", []),
                dry_run=args.get("dry_run", False),
            )
        finally:
            if should_close:
                db.close()

    def _mcp_promote_claim(self, args: dict[str, Any]) -> dict[str, Any]:
        """Promote a corroborated claim to an edge-entity."""
        from .promote import promote_claim_to_edge

        db, should_close = self._get_db()
        kb_service = self._get_kb_service()
        if kb_service is None:
            if should_close:
                db.close()
            return {
                "error": "No KB service available — write tools require a running server context"
            }

        kb_name = self._resolve_kb(args)
        try:
            return promote_claim_to_edge(
                db=db,
                kb_name=kb_name,
                claim_id=args["claim_id"],
                edge_type=args["edge_type"],
                kb_service=kb_service,
                endpoint_fields=args.get("endpoint_fields") or {},
                dry_run=args.get("dry_run", False),
            )
        finally:
            if should_close:
                db.close()

    def _mcp_ftm_export(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None = None
    ) -> dict[str, Any]:
        """Export KB entries as FtM JSON."""
        from .ftm import export_ftm

        db, should_close = self._get_db()
        try:
            return {
                "entities": export_ftm(
                    db,
                    self._readable_kb(args, readable_kbs),
                    entry_types=args.get("entry_types"),
                ),
            }
        finally:
            if should_close:
                db.close()

    def _mcp_ftm_import(self, args: dict[str, Any]) -> dict[str, Any]:
        """Import FtM entities into the KB."""
        from .ftm import import_ftm

        db, should_close = self._get_db()
        try:
            return import_ftm(
                db,
                self._resolve_kb(args),
                args.get("entities", []),
                dry_run=args.get("dry_run", False),
            )
        finally:
            if should_close:
                db.close()


# Backward-compatible aliases for external code that imports private names
_parse_meta = parse_meta
_validate_investigation_entry = validate_investigation_entry
