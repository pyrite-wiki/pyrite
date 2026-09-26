"""Cascade Series plugin — investigative journalism knowledge management for pyrite."""

from typing import Any, ClassVar

from pyrite_journalism_investigation.queries import query_network
from pyrite_journalism_investigation.utils import parse_meta

from pyrite.plugins.capabilities import Capability

from .entry_types import (
    ActorEntry,
    CascadeEventEntry,
    CascadeOrgEntry,
    MechanismEntry,
    SceneEntry,
    SolidarityEventEntry,
    StatisticEntry,
    ThemeEntry,
    TimelineEventEntry,
    VictimEntry,
)
from .hooks import _on_actor_saved, resolve_actor_links


class CascadePlugin:
    """Cascade Series plugin for pyrite.

    Provides entry types for investigative journalism research
    covering actors, organizations, events, themes, mechanisms,
    scenes, victims, and statistics — plus a timeline of 4,000+ events.
    """

    name = "cascade"
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

    def get_cli_commands(self) -> list[tuple[str, Any]]:
        from .cli import cascade_app

        return [("cascade", cascade_app)]

    def get_entry_types(self) -> dict[str, type]:
        return {
            "actor": ActorEntry,
            "cascade_org": CascadeOrgEntry,
            "cascade_event": CascadeEventEntry,
            "timeline_event": TimelineEventEntry,
            "theme": ThemeEntry,
            "victim": VictimEntry,
            "statistic": StatisticEntry,
            "mechanism": MechanismEntry,
            "scene": SceneEntry,
            "solidarity_event": SolidarityEventEntry,
        }

    def get_kb_types(self) -> list[str]:
        return ["cascade-research", "cascade-timeline", "cascade-solidarity"]

    def get_relationship_types(self) -> dict[str, dict]:
        return {
            "member_of": {
                "inverse": "has_member",
                "description": "Actor is a member of an organization",
            },
            "has_member": {
                "inverse": "member_of",
                "description": "Organization has a member",
            },
            "investigated": {
                "inverse": "investigated_by",
                "description": "Actor investigated an event",
            },
            "investigated_by": {
                "inverse": "investigated",
                "description": "Event was investigated by an actor",
            },
            "funded_by": {
                "inverse": "funds",
                "description": "Organization is funded by another organization",
            },
            "funds": {
                "inverse": "funded_by",
                "description": "Organization funds another organization",
            },
            "capture_mechanism": {
                "inverse": "enabled_capture",
                "description": "Mechanism used in a capture event",
            },
            "enabled_capture": {
                "inverse": "capture_mechanism",
                "description": "Event enabled by a capture mechanism",
            },
            "built_on": {
                "inverse": "enabled",
                "description": "Solidarity infrastructure built on earlier infrastructure",
            },
            "enabled": {
                "inverse": "built_on",
                "description": "Earlier infrastructure enabled later solidarity infrastructure",
            },
            "responded_to": {
                "inverse": "provoked_response",
                "description": "Solidarity action responded to a capture event",
            },
            "provoked_response": {
                "inverse": "responded_to",
                "description": "Capture event provoked a solidarity response",
            },
            "actor_reference": {
                "inverse": "has_actor",
                "description": "Event references an actor (resolved from string or wikilink)",
            },
            "has_actor": {
                "inverse": "actor_reference",
                "description": "Actor is referenced by an event",
            },
        }

    def get_validators(self) -> list:
        return [_validate_cascade_entry]

    def get_hooks(self) -> dict[str, list]:
        return {
            "before_save": [resolve_actor_links],
            "after_save": [_on_actor_saved],
        }

    def get_mcp_tools(self, tier: str) -> dict[str, dict]:
        tools: dict[str, dict[str, Any]] = {}
        if tier in ("read", "write", "admin"):
            tools["cascade_actors"] = {
                "description": "List actors by capture_lane, era, tier, or importance",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "capture_lane": {"type": "string", "description": "Filter by capture lane"},
                        "era": {"type": "string", "description": "Filter by era"},
                        "min_importance": {
                            "type": "integer",
                            "description": "Minimum importance (1-10)",
                        },
                        "kb_name": {
                            "type": "string",
                            "description": "KB name (default: cascade-research)",
                        },
                    },
                    "required": [],
                },
                "handler": self._mcp_actors,
            }
            tools["cascade_timeline"] = {
                "description": "Query timeline events by date range, capture_lane, or actor",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "from_date": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                        "to_date": {"type": "string", "description": "End date (YYYY-MM-DD)"},
                        "capture_lane": {"type": "string", "description": "Filter by capture lane"},
                        "actor": {"type": "string", "description": "Filter by actor name"},
                        "min_importance": {
                            "type": "integer",
                            "description": "Minimum importance (1-10)",
                        },
                        "limit": {"type": "integer", "description": "Max results (default 50)"},
                        "kb_name": {
                            "type": "string",
                            "description": "KB name (default: cascade-timeline)",
                        },
                    },
                    "required": [],
                },
                "handler": self._mcp_timeline,
            }
            tools["cascade_network"] = {
                "description": (
                    "Get actor/org/event connection network for a given entity. "
                    "At most `limit` outlinks and `limit` backlinks are returned "
                    "(default 50 each); the response carries the true totals and "
                    "`truncated`."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "entry_id": {
                            "type": "string",
                            "description": "Entry ID to get network for",
                        },
                        "kb_name": {"type": "string", "description": "KB name"},
                        "limit": {
                            "type": "integer",
                            "description": "Max results per direction (default 50; 0 = no cap)",
                        },
                        "offset": {
                            "type": "integer",
                            "description": "Results to skip per direction (default 0)",
                        },
                    },
                    "required": ["entry_id", "kb_name"],
                },
                "handler": self._mcp_network,
            }
            tools["solidarity_timeline"] = {
                "description": "Query solidarity events by date range, infrastructure_type, or actor",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "from_date": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                        "to_date": {"type": "string", "description": "End date (YYYY-MM-DD)"},
                        "infrastructure_type": {
                            "type": "string",
                            "description": "Filter by infrastructure type",
                        },
                        "actor": {"type": "string", "description": "Filter by actor name"},
                        "min_importance": {
                            "type": "integer",
                            "description": "Minimum importance (1-10)",
                        },
                        "limit": {"type": "integer", "description": "Max results (default 50)"},
                        "kb_name": {
                            "type": "string",
                            "description": "KB name (default: cascade-solidarity)",
                        },
                    },
                    "required": [],
                },
                "handler": self._mcp_solidarity_timeline,
            }
            tools["solidarity_infrastructure_types"] = {
                "description": "List all infrastructure types with entry counts",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "kb_name": {
                            "type": "string",
                            "description": "KB name (default: cascade-solidarity)",
                        },
                    },
                    "required": [],
                },
                "handler": self._mcp_solidarity_infrastructure_types,
            }
            tools["cascade_capture_lanes"] = {
                "description": "List all capture lanes with entry counts",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "kb_name": {"type": "string", "description": "KB name (optional)"},
                    },
                    "required": [],
                },
                "handler": self._mcp_capture_lanes,
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
    # MCP tool handlers
    # =========================================================================

    def _mcp_actors(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None = None
    ) -> dict[str, Any]:
        """List actors with optional filters.

        `kb_name` defaults to `cascade-research`; a scoped caller is served
        that default only if they may read it, because the query carries the
        readable set alongside the name (#223).
        """
        db, should_close = self._get_db()
        kb_name = args.get("kb_name", "cascade-research")
        capture_lane = args.get("capture_lane", "").lower()
        era = args.get("era", "").lower()
        min_importance = args.get("min_importance", 0)

        try:
            results = db.list_entries(
                kb_name=kb_name, kb_names=readable_kbs, entry_type="actor", limit=500
            )
            actors = []
            for r in results:
                imp = int(r.get("importance", 5))
                if min_importance and imp < min_importance:
                    continue
                meta = parse_meta(r)
                lanes = [l.lower() for l in (meta.get("capture_lanes") or [])]
                if capture_lane and capture_lane not in lanes:
                    continue
                actor_era = str(meta.get("era", "")).lower()
                if era and era not in actor_era:
                    continue
                actors.append(
                    {
                        "id": r.get("id"),
                        "title": r.get("title"),
                        "importance": imp,
                        "era": meta.get("era", ""),
                        "capture_lanes": meta.get("capture_lanes", []),
                    }
                )
            actors.sort(key=lambda a: a["importance"], reverse=True)
            return {"count": len(actors), "actors": actors}
        finally:
            if should_close:
                db.close()

    def _mcp_timeline(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None = None
    ) -> dict[str, Any]:
        """Query timeline events.

        `kb_name` defaults to `cascade-timeline`, served to a scoped caller
        only if they may read it (#223).
        """
        db, should_close = self._get_db()
        kb_name = args.get("kb_name", "cascade-timeline")
        from_date = args.get("from_date", "")
        to_date = args.get("to_date", "")
        capture_lane = args.get("capture_lane", "").lower()
        actor = args.get("actor", "").lower()
        min_importance = args.get("min_importance", 0)
        limit = args.get("limit", 50)

        try:
            results = db.list_entries(
                kb_name=kb_name, kb_names=readable_kbs, entry_type="timeline_event", limit=5000
            )
            events = []
            for r in results:
                imp = int(r.get("importance", 5))
                if min_importance and imp < min_importance:
                    continue
                meta = parse_meta(r)
                date = str(r.get("date", meta.get("date", "")))
                if from_date and date < from_date:
                    continue
                if to_date and date > to_date:
                    continue
                actors_list = [a.lower() for a in (meta.get("actors") or [])]
                if actor and not any(actor in a for a in actors_list):
                    continue
                lanes = [l.lower() for l in (meta.get("capture_lanes") or [])]
                if capture_lane and capture_lane not in lanes:
                    continue
                events.append(
                    {
                        "id": r.get("id"),
                        "title": r.get("title"),
                        "date": date,
                        "importance": imp,
                        "actors": meta.get("actors", []),
                    }
                )
                if len(events) >= limit:
                    break
            events.sort(key=lambda e: e.get("date", ""))
            return {"count": len(events), "events": events}
        finally:
            if should_close:
                db.close()

    def _mcp_network(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None = None
    ) -> dict[str, Any]:
        """Get connection network for an entity.

        Delegates to journalism-investigation query_network — identical logic,
        including its bound on the links by the caller's readable set.
        """
        db, should_close = self._get_db()
        try:
            return query_network(
                db,
                args["kb_name"],
                args["entry_id"],
                limit=args.get("limit", 50),
                offset=args.get("offset", 0),
                readable_kbs=readable_kbs,
            )
        finally:
            if should_close:
                db.close()

    def _mcp_solidarity_timeline(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None = None
    ) -> dict[str, Any]:
        """Query solidarity events.

        `kb_name` defaults to `cascade-solidarity`, served to a scoped caller
        only if they may read it (#223).
        """
        db, should_close = self._get_db()
        kb_name = args.get("kb_name", "cascade-solidarity")
        from_date = args.get("from_date", "")
        to_date = args.get("to_date", "")
        infra_type = args.get("infrastructure_type", "").lower()
        actor = args.get("actor", "").lower()
        min_importance = args.get("min_importance", 0)
        limit = args.get("limit", 50)

        try:
            results = db.list_entries(
                kb_name=kb_name, kb_names=readable_kbs, entry_type="solidarity_event", limit=5000
            )
            events = []
            for r in results:
                imp = int(r.get("importance", 5))
                if min_importance and imp < min_importance:
                    continue
                meta = parse_meta(r)
                date = str(r.get("date", meta.get("date", "")))
                if from_date and date < from_date:
                    continue
                if to_date and date > to_date:
                    continue
                actors_list = [a.lower() for a in (meta.get("actors") or [])]
                if actor and not any(actor in a for a in actors_list):
                    continue
                types_list = [t.lower() for t in (meta.get("infrastructure_types") or [])]
                if infra_type and not any(infra_type in t for t in types_list):
                    continue
                events.append(
                    {
                        "id": r.get("id"),
                        "title": r.get("title"),
                        "date": date,
                        "importance": imp,
                        "actors": meta.get("actors", []),
                        "infrastructure_types": meta.get("infrastructure_types", []),
                    }
                )
                if len(events) >= limit:
                    break
            events.sort(key=lambda e: e.get("date", ""))
            return {"count": len(events), "events": events}
        finally:
            if should_close:
                db.close()

    def _mcp_solidarity_infrastructure_types(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None = None
    ) -> dict[str, Any]:
        """List all infrastructure types with counts.

        `kb_name` defaults to `cascade-solidarity`, served to a scoped caller
        only if they may read it (#223).
        """
        db, should_close = self._get_db()
        kb_name = args.get("kb_name", "cascade-solidarity")

        try:
            results = db.list_entries(kb_name=kb_name, kb_names=readable_kbs, limit=5000)
            type_counts: dict[str, int] = {}
            for r in results:
                meta = parse_meta(r)
                for t in meta.get("infrastructure_types") or []:
                    type_counts[t] = type_counts.get(t, 0) + 1
            types = [
                {"type": k, "count": v} for k, v in sorted(type_counts.items(), key=lambda x: -x[1])
            ]
            return {"count": len(types), "infrastructure_types": types}
        finally:
            if should_close:
                db.close()

    def _mcp_capture_lanes(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None = None
    ) -> dict[str, Any]:
        """List all capture lanes with counts.

        With no `kb_name` this spans every KB, so the caller's readable set
        narrows the query (#223) rather than the page afterwards.
        """
        db, should_close = self._get_db()
        kb_name = args.get("kb_name")

        try:
            results = db.list_entries(kb_name=kb_name, kb_names=readable_kbs, limit=5000)
            lane_counts: dict[str, int] = {}
            for r in results:
                meta = parse_meta(r)
                for lane in meta.get("capture_lanes") or []:
                    lane_counts[lane] = lane_counts.get(lane, 0) + 1
            lanes = [
                {"lane": k, "count": v} for k, v in sorted(lane_counts.items(), key=lambda x: -x[1])
            ]
            return {"count": len(lanes), "lanes": lanes}
        finally:
            if should_close:
                db.close()


def _validate_cascade_entry(entry_type: str, fields: dict, ctx: dict) -> list[dict]:
    """Validate Cascade Series entries.

    Plugin validator contract (#379, #376, #48): binds
    (entry_type: str, fields: dict, ctx: dict) and returns a list of issue
    dicts with a `message` (or `field`) key and an optional `severity`
    (default: error).
    """
    errors: list[dict] = []

    if entry_type == "actor" and not fields.get("title"):
        errors.append({"field": "title", "message": "Actor must have a title"})

    if entry_type == "timeline_event":
        if not fields.get("date"):
            errors.append({"field": "date", "message": "Timeline event must have a date"})
        if not fields.get("title"):
            errors.append({"field": "title", "message": "Timeline event must have a title"})

    if entry_type == "solidarity_event":
        if not fields.get("date"):
            errors.append({"field": "date", "message": "Solidarity event must have a date"})
        if not fields.get("title"):
            errors.append({"field": "title", "message": "Solidarity event must have a title"})

    importance = fields.get("importance")
    if importance is not None and isinstance(importance, int):
        if importance < 1 or importance > 10:
            errors.append(
                {
                    "field": "importance",
                    "message": f"Importance must be 1-10, got: {importance}",
                }
            )

    return errors
