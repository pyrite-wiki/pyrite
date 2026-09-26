"""Social KB plugin — Everything2-inspired community knowledge base for pyrite."""

import logging
from collections.abc import Callable
from typing import Any, ClassVar

from pyrite.plugins.capabilities import Capability
from pyrite.plugins.scoping import kb_scope_clause

from .entry_types import UserProfileEntry, WriteupEntry
from .hooks import (
    after_delete_adjust_reputation,
    after_save_update_counts,
    before_save_author_check,
)
from .preset import SOCIAL_PRESET
from .tables import SOCIAL_TABLES
from .validators import validate_social

logger = logging.getLogger(__name__)


class SocialPlugin:
    """Social KB plugin for pyrite.

    Provides community-driven knowledge management with user-authored writeups,
    voting, reputation tracking, and author-only editing enforcement.
    """

    name = "social"
    # Tier A r1500 (Option B): declared plugin capabilities.
    capabilities: ClassVar[set[Capability]] = {
        Capability.SCHEMA,
        Capability.STORAGE,
        Capability.SURFACE,
        Capability.CONTEXT,
    }

    def __init__(self):
        self.ctx = None

    def set_context(self, ctx) -> None:
        """Receive shared dependencies from the plugin infrastructure."""
        self.ctx = ctx

    def get_entry_types(self) -> dict[str, type]:
        return {
            "writeup": WriteupEntry,
            "user_profile": UserProfileEntry,
        }

    def get_kb_types(self) -> list[str]:
        return ["social"]

    def get_cli_commands(self) -> list[tuple[str, Any]]:
        from .cli import social_app

        return [("social", social_app)]

    def get_mcp_tools(self, tier: str) -> dict[str, dict]:
        tools = {}

        # Read-tier tools
        if tier in ("read", "write", "admin"):
            tools["social_top"] = {
                "description": "Get highest-voted writeups in a social KB",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "kb_name": {"type": "string", "description": "KB name (optional)"},
                        "period": {
                            "type": "string",
                            "enum": ["week", "month", "all"],
                            "description": "Time period (default: all)",
                        },
                        "limit": {"type": "integer", "description": "Max results (default 10)"},
                    },
                    "required": [],
                },
                "handler": self._mcp_top,
            }
            tools["social_newest"] = {
                "description": "Get most recent writeups in a social KB",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "kb_name": {"type": "string", "description": "KB name (optional)"},
                        "limit": {"type": "integer", "description": "Max results (default 10)"},
                    },
                    "required": [],
                },
                "handler": self._mcp_newest,
            }
            tools["social_reputation"] = {
                "description": "Get reputation score for a user",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "user_id": {"type": "string", "description": "User ID"},
                    },
                    "required": ["user_id"],
                },
                "handler": self._mcp_reputation,
            }

        # Write-tier tools
        if tier in ("write", "admin"):
            tools["social_vote"] = {
                "description": "Vote on a writeup (up or down)",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "entry_id": {"type": "string", "description": "Entry ID to vote on"},
                        "kb_name": {"type": "string", "description": "KB name"},
                        "user_id": {"type": "string", "description": "Voter user ID"},
                        "value": {
                            "type": "integer",
                            "enum": [1, -1],
                            "description": "Vote value: 1 (up) or -1 (down)",
                        },
                    },
                    "required": ["entry_id", "kb_name", "user_id", "value"],
                },
                "handler": self._mcp_vote,
            }
            tools["social_post"] = {
                "description": "Create a new writeup in a social KB",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "kb_name": {"type": "string", "description": "Target KB"},
                        "title": {"type": "string", "description": "Writeup title"},
                        "body": {"type": "string", "description": "Writeup body (markdown)"},
                        "author_id": {"type": "string", "description": "Author user ID"},
                        "writeup_type": {
                            "type": "string",
                            "enum": ["essay", "story", "review", "howto", "opinion"],
                            "description": "Writeup type (default: essay)",
                        },
                        "tags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Tags",
                        },
                    },
                    "required": ["kb_name", "title", "body", "author_id"],
                },
                "handler": self._mcp_post,
            }

        return tools

    def get_db_tables(self) -> list[dict]:
        return SOCIAL_TABLES

    def get_hooks(self) -> dict[str, list[Callable]]:
        return {
            "before_save": [before_save_author_check],
            "after_save": [after_save_update_counts],
            "after_delete": [after_delete_adjust_reputation],
        }

    def get_validators(self) -> list[Callable]:
        return [validate_social]

    def get_kb_presets(self) -> dict[str, dict]:
        return {"social": SOCIAL_PRESET}

    # =========================================================================
    # MCP tool handlers
    # =========================================================================

    def _get_db(self):
        """Get DB from injected context, falling back to self-bootstrap."""
        if self.ctx is not None:
            return self.ctx.db, False  # db, should_close
        from pyrite.config import load_config
        from pyrite.storage.database import PyriteDB

        config = load_config()
        return PyriteDB(config.settings.index_path), True

    def _mcp_top(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None = None
    ) -> dict[str, Any]:
        """Get highest-voted writeups."""
        import json

        db, should_close = self._get_db()
        kb_name = args.get("kb_name")
        period = args.get("period", "all")
        limit = args.get("limit", 10)

        try:
            query = """
                SELECT e.id, e.title, e.kb_name, e.metadata,
                       COALESCE(SUM(v.value), 0) as score
                FROM entry e
                LEFT JOIN social_vote v ON e.id = v.entry_id AND e.kb_name = v.kb_name
                WHERE e.entry_type = 'writeup'
            """
            params: list = []
            clause, scope_params = kb_scope_clause("e.kb_name", kb_name, readable_kbs)
            query += clause
            params.extend(scope_params)
            if period == "week":
                query += " AND v.created_at >= datetime('now', '-7 days')"
            elif period == "month":
                query += " AND v.created_at >= datetime('now', '-30 days')"
            query += " GROUP BY e.id, e.kb_name ORDER BY score DESC LIMIT ?"
            params.append(limit)

            rows = db._raw_conn.execute(query, params).fetchall()
            results = []
            for row in rows:
                meta = {}
                if row["metadata"]:
                    try:
                        meta = json.loads(row["metadata"])
                    except (json.JSONDecodeError, TypeError):
                        pass
                results.append(
                    {
                        "id": row["id"],
                        "title": row["title"],
                        "kb_name": row["kb_name"],
                        "score": row["score"],
                        "author_id": meta.get("author_id", ""),
                    }
                )
            return {"count": len(results), "top": results}
        finally:
            if should_close:
                db.close()

    def _mcp_newest(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None = None
    ) -> dict[str, Any]:
        """Get most recent writeups."""
        import json

        db, should_close = self._get_db()
        kb_name = args.get("kb_name")
        limit = args.get("limit", 10)

        try:
            query = "SELECT * FROM entry WHERE entry_type = 'writeup'"
            params: list = []
            clause, scope_params = kb_scope_clause("kb_name", kb_name, readable_kbs)
            query += clause
            params.extend(scope_params)
            query += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)

            rows = db._raw_conn.execute(query, params).fetchall()
            results = []
            for row in rows:
                meta = {}
                if row["metadata"]:
                    try:
                        meta = json.loads(row["metadata"])
                    except (json.JSONDecodeError, TypeError):
                        pass
                results.append(
                    {
                        "id": row["id"],
                        "title": row["title"],
                        "kb_name": row["kb_name"],
                        "author_id": meta.get("author_id", ""),
                        "writeup_type": meta.get("writeup_type", "essay"),
                        "created_at": str(row["created_at"] or ""),
                    }
                )
            return {"count": len(results), "newest": results}
        finally:
            if should_close:
                db.close()

    def _mcp_reputation(
        self, args: dict[str, Any], *, readable_kbs: set[str] | None = None
    ) -> dict[str, Any]:
        """Get user reputation, summed over the KBs the caller may read.

        Both parts are KB-derived: votes on the user's entries, and log
        adjustments recorded per KB. A scoped caller's sum covers only its
        readable set; log rows recorded without a KB (before the hooks kept
        one) cannot be placed, so they count only for an unscoped caller.
        """
        db, should_close = self._get_db()
        user_id = args["user_id"]

        try:
            vote_scope, vote_params = kb_scope_clause("v.kb_name", None, readable_kbs)
            row = db._raw_conn.execute(
                """SELECT COALESCE(SUM(v.value), 0) as total
                   FROM social_vote v
                   JOIN entry e ON v.entry_id = e.id AND v.kb_name = e.kb_name
                   WHERE json_extract(e.metadata, '$.author_id') = ?"""
                + vote_scope,
                (user_id, *vote_params),
            ).fetchone()
            vote_rep = row["total"] if row else 0

            log_scope, log_params = kb_scope_clause("kb_name", None, readable_kbs)
            log_row = db._raw_conn.execute(
                "SELECT COALESCE(SUM(delta), 0) as total FROM social_reputation_log WHERE user_id = ?"
                + log_scope,
                (user_id, *log_params),
            ).fetchone()
            log_rep = log_row["total"] if log_row else 0

            return {
                "user_id": user_id,
                "reputation": vote_rep + log_rep,
                "from_votes": vote_rep,
                "from_adjustments": log_rep,
            }
        finally:
            if should_close:
                db.close()

    def _mcp_vote(self, args: dict[str, Any]) -> dict[str, Any]:
        """Cast a vote on a writeup."""
        from datetime import UTC, datetime

        db, should_close = self._get_db()

        try:
            now = datetime.now(UTC).isoformat()
            db._raw_conn.execute(
                """INSERT INTO social_vote (entry_id, kb_name, user_id, value, created_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(entry_id, kb_name, user_id)
                   DO UPDATE SET value = ?, created_at = ?""",
                (
                    args["entry_id"],
                    args["kb_name"],
                    args["user_id"],
                    args["value"],
                    now,
                    args["value"],
                    now,
                ),
            )
            db._raw_conn.commit()
            return {"voted": True, "entry_id": args["entry_id"], "value": args["value"]}
        finally:
            if should_close:
                db.close()

    def _mcp_post(self, args: dict[str, Any]) -> dict[str, Any]:
        """Create a new writeup through the KBService write pipeline (#391).

        Was: build a WriteupEntry, `KBRepository.save` then
        `IndexManager.index_entry` directly, skipping `KBService._prepare` --
        no exists check (an existing id was silently overwritten), no
        validators, no before/after-save hooks, and error responses carried no
        `error_code`. Now goes through `KBService.create_entry`, the same
        pipeline `software-kb`'s `_mcp_create_backlog_item` uses.
        """
        from pyrite.exceptions import PyriteError
        from pyrite.services.kb_service import KBService

        db, should_close = self._get_db()
        config = self.ctx.config if self.ctx else None
        if config is None:
            from pyrite.config import load_config

            config = load_config()

        kb_name = args["kb_name"]
        kb_config = config.get_kb(kb_name)
        if not kb_config:
            return {"error": f"KB '{kb_name}' not found", "error_code": "KB_NOT_FOUND"}

        try:
            svc = KBService(config, db)
            entry = svc.create_entry(
                kb_name,
                None,
                args["title"],
                "writeup",
                args["body"],
                author_id=args["author_id"],
                writeup_type=args.get("writeup_type", "essay"),
                tags=args.get("tags", []),
            )
            from pyrite.storage.repository import KBRepository

            repo = KBRepository(kb_config)
            file_path = repo._resolve_file_path(entry, repo._infer_subdir(entry))
            return {"created": True, "entry_id": entry.id, "file_path": str(file_path)}
        except PyriteError as e:
            code = getattr(e, "error_code", None) or "CREATE_FAILED"
            # A StorageError/PluginError/ConfigError (or a subclass that
            # doesn't set its own) can carry server-side detail in str(e) --
            # a real path, a driver's own text. public_message, when set, is
            # what every other transport already shows; the real detail
            # still reaches the log (ADR-0037 theme 2 round 2, item 1).
            public_message = getattr(e, "public_message", None)
            if public_message is not None:
                logger.warning("%s", e)
                message = public_message
            else:
                message = str(e)
            return {"error": message, "error_code": code}
        finally:
            if should_close:
                db.close()
