"""Social KB lifecycle hooks.

Enforces author-only editing and maintains writeup counts.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from pyrite.models.base import Entry

logger = logging.getLogger(__name__)


def before_save_author_check(entry: Entry, context: dict[str, Any]) -> Entry:
    """Enforce author_edit_only policy.

    For writeup entries, the current user must be the author (or the operation
    must be a create). Uses the folder-per-author convention: the entry's file
    path should be under writeups/<author_id>/.
    """
    if entry.entry_type != "writeup":
        return entry

    author_id = getattr(entry, "author_id", "")
    user = context.get("user", "")
    operation = context.get("operation", "")

    # On create, set author_id to current user if not already set
    if operation == "create" and not author_id and user:
        entry.author_id = user
        return entry

    # On update, check that the user is the author
    if operation == "update" and user and author_id:
        if user != author_id:
            raise PermissionError(f"User '{user}' cannot edit writeup owned by '{author_id}'")

    return entry


def after_save_update_counts(entry: Entry, context: dict[str, Any]) -> None:
    """Update the author's writeup_count after saving a writeup.

    Uses PluginContext.db when available to write a reputation log entry.
    Falls back to logging when db is not available.
    """
    if entry.entry_type != "writeup":
        return

    author_id = getattr(entry, "author_id", "")
    if not author_id:
        return

    kb_name = context.get("kb_name", "")
    operation = context.get("operation", "")

    if operation != "create":
        return

    # Try to use the injected db from PluginContext
    db = getattr(context, "db", None)
    if db is not None:
        try:
            now = datetime.now(UTC).isoformat()
            db._raw_conn.execute(
                """INSERT INTO social_reputation_log
                   (user_id, delta, reason, entry_id, kb_name, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (author_id, 1, f"writeup_created:{entry.id}", entry.id, kb_name or None, now),
            )
            db._raw_conn.commit()
            logger.info("Writeup count incremented for %s in %s", author_id, kb_name)
        except Exception:
            logger.warning("Failed to update writeup count for %s", author_id, exc_info=True)
    else:
        logger.info(
            "Writeup created by %s in %s — writeup_count increment skipped (no db)",
            author_id,
            kb_name,
        )


def after_delete_adjust_reputation(entry: Entry, context: dict[str, Any]) -> None:
    """Adjust reputation when a writeup with votes is deleted.

    Uses PluginContext.db when available to log a reputation adjustment.
    Falls back to logging when db is not available.
    """
    if entry.entry_type != "writeup":
        return

    author_id = getattr(entry, "author_id", "")
    if not author_id:
        return

    db = getattr(context, "db", None)
    if db is not None:
        try:
            # Calculate total votes on this writeup
            row = db._raw_conn.execute(
                "SELECT COALESCE(SUM(value), 0) as total FROM social_vote WHERE entry_id = ? AND kb_name = ?",
                (entry.id, context.get("kb_name", "")),
            ).fetchone()
            vote_total = row["total"] if row else 0

            if vote_total != 0:
                now = datetime.now(UTC).isoformat()
                db._raw_conn.execute(
                    """INSERT INTO social_reputation_log
                       (user_id, delta, reason, entry_id, kb_name, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        author_id,
                        -vote_total,
                        f"writeup_deleted:{entry.id}",
                        entry.id,
                        context.get("kb_name") or None,
                        now,
                    ),
                )
                db._raw_conn.commit()
                logger.info(
                    "Reputation adjusted by %d for %s (writeup %s deleted)",
                    -vote_total,
                    author_id,
                    entry.id,
                )
        except Exception:
            logger.warning("Failed to adjust reputation for %s", author_id, exc_info=True)
    else:
        logger.info(
            "Writeup %s by %s deleted — reputation adjustments skipped (no db)",
            entry.id,
            author_id,
        )
