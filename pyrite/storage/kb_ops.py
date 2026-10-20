"""
KB registration and statistics.

Mixin class for KB management operations.
"""

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from .models import KB

if TYPE_CHECKING:
    from ..config import PyriteConfig

logger = logging.getLogger(__name__)


def ephemeral_root(config: "PyriteConfig") -> Path:
    """The directory ephemeral KBs are created in: <workspace>/ephemeral."""
    return (config.settings.workspace_path / "ephemeral").resolve()


def is_ephemeral_kb_path(root: Path, path: str) -> bool:
    """True when `path` resolves strictly inside the ephemeral root `root`.

    A registry row at such a path is an ephemeral KB's, whether or not its
    config.yaml entry survives. The root itself is not an ephemeral KB.
    (`kb.path` is NOT NULL.)
    """
    resolved = Path(path).expanduser().resolve()
    return resolved != root and resolved.is_relative_to(root)


class KBOpsMixin:
    """KB registration, stats, and indexing metadata."""

    def register_kb(
        self,
        name: str,
        kb_type: str,
        path: str,
        description: str = "",
        source: str = "user",
        default_role: str | None = None,
    ) -> None:
        """Register a KB in the index."""
        type_str = kb_type.value if hasattr(kb_type, "value") else kb_type
        existing = self.session.get(KB, name)
        if existing:
            existing.kb_type = type_str
            existing.path = path
            existing.description = description
            existing.source = source
            if default_role is not None:
                existing.default_role = default_role
        else:
            kb = KB(
                name=name,
                kb_type=type_str,
                path=path,
                description=description,
                source=source,
                default_role=default_role,
            )
            self.session.add(kb)
        self.session.commit()

    def insert_new_kb(
        self,
        name: str,
        kb_type: str,
        path: str,
        description: str = "",
        source: str = "user",
        default_role: str | None = None,
    ) -> bool:
        """Register a KB only if no row has this name; never overwrite one.

        Returns False when the name is already registered. The primary key
        makes this atomic: of two concurrent inserts, one fails.
        `default_role` is written with the row, so a KB's access policy exists
        from the moment the KB does.
        """
        type_str = kb_type.value if hasattr(kb_type, "value") else kb_type
        if self.session.get(KB, name) is not None:
            return False
        self.session.add(
            KB(
                name=name,
                kb_type=type_str,
                path=path,
                description=description,
                source=source,
                default_role=default_role,
            )
        )
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            return False
        return True

    def merge_registered_kbs(self, config: "PyriteConfig") -> int:
        """Merge DB-registered KBs (from ``pyrite kb add``) into ``config``.

        Consolidates three near-identical copies of this raw-SQL merge that
        previously lived in cli/context.py, server/mcp_server.py, and
        server/api.py -- the latter two swallowed query failures with a
        bare ``except Exception: pass``, silently disappearing
        DB-registered KBs from the MCP/REST surfaces on any DB hiccup
        (fail-open-exception-sweep site #1, the dual-registry class again).

        Query failure (e.g. the ``kb`` table not existing yet on first
        run) is expected and non-fatal -- logged at warning so it's
        visible, but does not raise, since callers run this during
        construction and must not crash on a transient/first-run issue.

        This loop is the one owner of an orphaned ephemeral KB's policy: a
        row whose path is an ephemeral KB's (`is_ephemeral_kb_path`) and
        that has no default_role loads with "none", decided from the rows
        read here and from nothing else. Writing "none" back to those rows
        afterwards is best-effort record-keeping, never a precondition.

        It commits or rolls back its own session: do not call it in the
        middle of a transaction.

        Returns the number of KBs merged (0 on failure or none pending).
        """
        try:
            rows = self.session.execute(
                text(
                    "SELECT name, path, kb_type, description, default_role "
                    # A row with no source is a user row, as the registry
                    # and the resolver read it (`source or "user"`).
                    "FROM kb WHERE source = 'user' OR source IS NULL"
                )
            ).fetchall()
            # End the read transaction: a session left inside one blocks WAL
            # checkpoints for as long as it lives.
            self.session.commit()
        except SQLAlchemyError:
            self.session.rollback()
            logger.warning(
                "Could not query DB-registered KBs to merge into config "
                "(kb table may not exist yet on first run)",
                exc_info=True,
            )
            return 0

        if not rows:
            return 0
        root = ephemeral_root(config)
        db_kbs = []
        orphans = []
        for name, path, kb_type, description, default_role in rows:
            if default_role is None:
                try:
                    orphan = is_ephemeral_kb_path(root, path)
                except (OSError, RuntimeError, ValueError):
                    # A path that cannot be resolved (an unknown ~user, a
                    # symlink loop) must not stop the load, and fails closed:
                    # private for this process, nothing written back.
                    logger.warning(
                        "Could not resolve the path of KB %r (%s); loading it private ('none')",
                        name,
                        path,
                        exc_info=True,
                    )
                    default_role = "none"
                    orphan = False
                if orphan:
                    logger.warning(
                        "Ephemeral KB %r has no default_role in the registry; "
                        "loading it private ('none')",
                        name,
                    )
                    default_role = "none"
                    orphans.append(name)
            db_kbs.append(
                {
                    "name": name,
                    "path": path,
                    "kb_type": kb_type,
                    "description": description or "",
                    "default_role": default_role,
                }
            )
        merged = config.register_db_kbs(db_kbs)
        if orphans:
            self._record_private_policy(orphans)
        return merged

    def _record_private_policy(self, names: list[str]) -> None:
        """Best effort: write "none" to the rows `merge_registered_kbs` loaded private.

        Only record-keeping, so a later reader of the row sees the policy the
        load applied. A failure (a locked or read-only index) is rolled back
        and logged; the merged KB is private either way.
        """
        try:
            for name in names:
                self.session.execute(
                    text(
                        "UPDATE kb SET default_role = 'none' "
                        "WHERE name = :n AND default_role IS NULL"
                    ),
                    {"n": name},
                )
            self.session.commit()
        except SQLAlchemyError:
            self.session.rollback()
            logger.warning(
                "Could not record the private policy for %s; it applies for this load",
                ", ".join(names),
                exc_info=True,
            )

    def update_kb_default_role(self, name: str, default_role: str | None) -> bool:
        """Update a KB's default_role. Returns True if KB was found."""
        kb = self.session.get(KB, name)
        if not kb:
            return False
        kb.default_role = default_role
        self.session.commit()
        return True

    def unregister_kb(self, name: str) -> None:
        """Remove a KB, all its entries, and every per-KB grant on it.

        The one place a KB is deleted -- registry removal (REST, MCP, CLI),
        repo unsubscribe and ephemeral expiry all come through here -- so the
        grants go here too. A `kb_permission` row outliving its KB is
        inherited by the next KB registered under the same name. Deleted
        even when the KB row is already gone, and in the same transaction.
        """
        kb = self.session.get(KB, name)
        if kb:
            self.session.delete(kb)
        self.session.execute(
            text("DELETE FROM kb_permission WHERE kb_name = :kb_name"), {"kb_name": name}
        )
        self.session.commit()

    def get_kb_stats(self, name: str) -> dict[str, Any] | None:
        """Get statistics for a KB."""
        row = self.session.execute(
            text("""
                SELECT k.*, COUNT(e.id) as actual_count
                FROM kb k
                LEFT JOIN entry e ON k.name = e.kb_name
                WHERE k.name = :name
                GROUP BY k.name
            """),
            {"name": name},
        ).fetchone()
        if row is None:
            return None
        return dict(row._mapping)

    def get_type_counts(
        self, kb_name: str | None = None, kb_names: set[str] | list[str] | None = None
    ) -> list[dict[str, Any]]:
        """Get entry counts grouped by entry_type, restricted to ``kb_names`` when given."""
        if kb_names is not None:
            from .backends.base_backend import kb_names_clause

            params: dict[str, Any] = {}
            scope = kb_names_clause("kb_name", kb_names, params)
            rows = self.session.execute(
                text(
                    f"SELECT entry_type, COUNT(*) as count FROM entry WHERE {scope} "
                    "GROUP BY entry_type ORDER BY count DESC"
                ),
                params,
            ).fetchall()
            return [dict(r._mapping) for r in rows]
        if kb_name:
            rows = self.session.execute(
                text(
                    "SELECT entry_type, COUNT(*) as count FROM entry WHERE kb_name = :kb GROUP BY entry_type ORDER BY count DESC"
                ),
                {"kb": kb_name},
            ).fetchall()
        else:
            rows = self.session.execute(
                text(
                    "SELECT entry_type, COUNT(*) as count FROM entry GROUP BY entry_type ORDER BY count DESC"
                ),
            ).fetchall()
        return [dict(r._mapping) for r in rows]

    def link_kb_to_repo(self, kb_name: str, repo_id: int, repo_subpath: str) -> None:
        """Associate a KB with a repo."""
        kb = self.session.get(KB, kb_name)
        if kb:
            kb.repo_id = repo_id
            kb.repo_subpath = repo_subpath
            self.session.commit()

    def get_kbs_for_repo(self, repo_id: int) -> list[dict[str, Any]]:
        """Get KBs associated with a repo."""
        kbs = self.session.query(KB).filter_by(repo_id=repo_id).all()
        return [{"name": kb.name, "path": kb.path, "repo_subpath": kb.repo_subpath} for kb in kbs]

    def update_kb_indexed(self, name: str, entry_count: int) -> None:
        """Update KB last indexed time and count."""
        kb = self.session.get(KB, name)
        if kb:
            kb.last_indexed = datetime.now(UTC).isoformat()
            kb.entry_count = entry_count
            self.session.commit()
