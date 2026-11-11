"""Per-user git worktree management for multi-user collaboration.

Creates and manages git worktrees that give each user an isolated
working copy on their own branch. All reads come from main; writes
go to the user's worktree. An admin merge queue integrates changes.

See ADR-0024 for the design rationale.
"""

import dataclasses
import logging
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pyrite.config import KBConfig, PyriteConfig
from pyrite.exceptions import (
    EntryNotFoundError,
    KBNotFoundError,
    StorageError,
    ValidationError,
)
from pyrite.storage.database import PyriteDB

logger = logging.getLogger(__name__)


@dataclass
class WorktreeInfo:
    """Information about a user's worktree."""

    id: int
    user_id: int
    username: str
    kb_name: str
    repo_path: str
    branch: str
    worktree_path: str
    diff_db_path: str
    status: str  # active, submitted, merged, rejected
    submitted_at: str | None = None
    merged_at: str | None = None
    rejected_at: str | None = None
    feedback: str | None = None
    created_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


class WorktreeService:
    """Manages per-user git worktrees for collaborative editing.

    Each user gets a worktree on branch user/{username}, created on
    first write. Worktrees share the git object store with main repo
    (zero-copy). Each worktree gets a small diff index (SQLite) for
    the user's changed entries.
    """

    def __init__(self, config: PyriteConfig, db: PyriteDB):
        self.config = config
        self.db = db

    @staticmethod
    def _sanitize_username(username: str) -> str:
        """Sanitize username for use in filesystem paths and git branch names."""
        import re

        # Strip path traversal, null bytes, control chars
        safe = re.sub(r"[/\\.\x00-\x1f~^:?*\[\]@{} ]", "-", username)
        safe = safe.strip("-")
        if not safe:
            raise ValidationError("Username produces empty sanitized string")
        return safe[:64]  # cap length

    def _find_repo_root(self, kb_config: KBConfig) -> Path | None:
        """Find the git repo root for a KB."""
        from pyrite.services.git_service import GitService

        path = kb_config.path
        # Walk up to find .git
        for _ in range(10):
            if (path / ".git").exists():
                return path
            if path.parent == path:
                break
            path = path.parent
        # Check if the KB path itself is in a git repo
        if GitService.is_git_repo(kb_config.path):
            # Use rev-parse to find the root
            import subprocess

            result = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=str(kb_config.path),
                capture_output=True,
                text=True,
                env=GitService.subprocess_env(),
            )
            if result.returncode == 0:
                return Path(result.stdout.strip())
        return None

    def _compute_paths(self, repo_root: Path, username: str) -> tuple[Path, Path]:
        """Compute worktree and diff DB paths for a user.

        Returns (worktree_path, diff_db_path).
        """
        worktrees_dir = repo_root.parent / ".pyrite-worktrees"
        worktree_path = worktrees_dir / username
        diff_db_path = worktree_path / ".pyrite" / "diff-index.db"
        return worktree_path, diff_db_path

    def _get_worktree_row(self, kb_name: str, user_id: int) -> dict[str, Any] | None:
        """Get worktree record from DB."""
        row = self.db._raw_conn.execute(
            "SELECT * FROM worktree WHERE kb_name = ? AND user_id = ?",
            (kb_name, user_id),
        ).fetchone()
        return dict(row) if row else None

    def _row_to_info(self, row: dict[str, Any]) -> WorktreeInfo:
        """Convert a DB row to WorktreeInfo."""
        return WorktreeInfo(
            id=row["id"],
            user_id=row["user_id"],
            username=row["username"],
            kb_name=row["kb_name"],
            repo_path=row["repo_path"],
            branch=row["branch"],
            worktree_path=row["worktree_path"],
            diff_db_path=row["diff_db_path"],
            status=row["status"],
            submitted_at=row.get("submitted_at"),
            merged_at=row.get("merged_at"),
            rejected_at=row.get("rejected_at"),
            feedback=row.get("feedback"),
            created_at=row.get("created_at"),
        )

    def ensure_worktree(self, kb_name: str, user_id: int, username: str) -> WorktreeInfo:
        """Create a worktree for the user if it doesn't exist.

        Args:
            kb_name: Knowledge base name.
            user_id: User's numeric ID.
            username: User's display name (used in branch name and path).

        Returns:
            WorktreeInfo with paths and status.

        Raises:
            ValueError: If KB not found or not in a git repo.
        """
        # Check if already exists and still active
        row = self._get_worktree_row(kb_name, user_id)
        if row and row["status"] in ("active", "submitted"):
            return self._row_to_info(row)
        elif row and row["status"] in ("merged", "rejected"):
            # Reset a closed worktree back to active for reuse
            now = datetime.now(UTC).isoformat()
            self.db._raw_conn.execute(
                "UPDATE worktree SET status = 'active', submitted_at = NULL, "
                "merged_at = NULL, rejected_at = NULL, feedback = NULL, updated_at = ? "
                "WHERE id = ?",
                (now, row["id"]),
            )
            self.db._raw_conn.commit()
            row = self._get_worktree_row(kb_name, user_id)
            return self._row_to_info(row)

        # Sanitize username for paths and branch names
        safe_username = self._sanitize_username(username)

        # Resolve KB and repo
        kb_config = self.config.get_kb(kb_name)
        if not kb_config:
            raise KBNotFoundError(f"KB '{kb_name}' not found")

        repo_root = self._find_repo_root(kb_config)
        if not repo_root:
            raise ValidationError(f"KB '{kb_name}' is not in a git repository")

        # Compute paths
        branch = f"user/{safe_username}"
        worktree_path, diff_db_path = self._compute_paths(repo_root, safe_username)

        # Create git worktree
        from pyrite.services.git_service import GitService

        success, msg = GitService.worktree_add(repo_root, worktree_path, branch)
        if not success:
            raise StorageError(f"Failed to create worktree: {msg}")

        # Ensure diff DB directory exists and initialize the diff index
        diff_db_path.parent.mkdir(parents=True, exist_ok=True)
        # The diff index lives inside the worktree (.pyrite/), so self-exclude it
        # from git — otherwise `git add .` on submit commits the SQLite binaries
        # into the user's branch and they'd merge into the KB.
        (diff_db_path.parent / ".gitignore").write_text("*\n")
        # Pre-create the diff DB so it's ready for immediate use. Register the KB
        # in it (pointing at the worktree path) so the first overlay entry write
        # satisfies the entry→kb foreign key — without this, any write into the
        # worktree fails with a FOREIGN KEY constraint error.
        _diff_db = PyriteDB(diff_db_path)
        _diff_db.register_kb(
            name=kb_name,
            kb_type=kb_config.kb_type,
            path=str(worktree_path),
            description=kb_config.description,
        )
        _diff_db.close()

        # Insert DB record
        now = datetime.now(UTC).isoformat()
        self.db._raw_conn.execute(
            """INSERT INTO worktree
            (user_id, username, kb_name, repo_path, branch,
             worktree_path, diff_db_path, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
            (
                user_id,
                safe_username,
                kb_name,
                str(repo_root),
                branch,
                str(worktree_path),
                str(diff_db_path),
                now,
                now,
            ),
        )
        self.db._raw_conn.commit()

        row = self._get_worktree_row(kb_name, user_id)
        return self._row_to_info(row)

    def get_worktree(self, kb_name: str, user_id: int) -> WorktreeInfo | None:
        """Get an existing worktree for a user, or None."""
        row = self._get_worktree_row(kb_name, user_id)
        return self._row_to_info(row) if row else None

    def list_worktrees(self, kb_name: str | None = None) -> list[WorktreeInfo]:
        """List worktrees, optionally filtered by KB."""
        if kb_name:
            rows = self.db._raw_conn.execute(
                "SELECT * FROM worktree WHERE kb_name = ? ORDER BY created_at",
                (kb_name,),
            ).fetchall()
        else:
            rows = self.db._raw_conn.execute(
                "SELECT * FROM worktree ORDER BY created_at"
            ).fetchall()
        return [self._row_to_info(dict(r)) for r in rows]

    def get_submissions(self, kb_name: str | None = None) -> list[WorktreeInfo]:
        """List worktrees with status 'submitted'."""
        if kb_name:
            rows = self.db._raw_conn.execute(
                "SELECT * FROM worktree WHERE status = 'submitted' AND kb_name = ? ORDER BY submitted_at",
                (kb_name,),
            ).fetchall()
        else:
            rows = self.db._raw_conn.execute(
                "SELECT * FROM worktree WHERE status = 'submitted' ORDER BY submitted_at"
            ).fetchall()
        return [self._row_to_info(dict(r)) for r in rows]

    def submit(self, kb_name: str, user_id: int) -> WorktreeInfo:
        """Submit a user's worktree changes for admin review.

        Auto-commits any pending changes in the worktree, then
        marks the worktree as submitted.
        """
        row = self._get_worktree_row(kb_name, user_id)
        if not row:
            raise EntryNotFoundError(f"No worktree for user {user_id} in KB '{kb_name}'")

        wt = self._row_to_info(row)
        worktree_path = Path(wt.worktree_path)

        # Auto-commit any pending changes
        from pyrite.services.git_service import GitService

        status = GitService.get_status(worktree_path)
        if not status.get("clean", True):
            committed, result = GitService.commit(
                worktree_path,
                message=f"Submit changes from {wt.username}",
                paths=None,
            )
            if not committed:
                logger.error("Worktree submit commit failed: %s", result.get("error"))
                raise StorageError("Could not commit worktree changes; submission was not recorded")

        # Update status
        now = datetime.now(UTC).isoformat()
        self.db._raw_conn.execute(
            "UPDATE worktree SET status = 'submitted', submitted_at = ?, updated_at = ? "
            "WHERE id = ?",
            (now, now, wt.id),
        )
        self.db._raw_conn.commit()

        row = self._get_worktree_row(kb_name, user_id)
        return self._row_to_info(row)

    def merge(self, kb_name: str, user_id: int) -> tuple[bool, str]:
        """Merge a user's branch into main.

        Returns (success, message). On conflict, returns (False, details).
        After successful merge, rebases the user's branch and clears the diff index.
        """
        # A read-only KB takes no write, a merged change set included: refused
        # before git touches the repository.
        self.config.require_kb(kb_name, db=self.db).refuse_writes()
        row = self._get_worktree_row(kb_name, user_id)
        if not row:
            return False, f"No worktree for user {user_id} in KB '{kb_name}'"

        wt = self._row_to_info(row)
        repo_path = Path(wt.repo_path)

        from pyrite.services.git_service import GitService

        # Uncommitted edits (including a failed submit) and an empty branch
        # have nothing to land. Git merge calls that a successful no-op;
        # the user-facing operation must not claim a change was merged.
        compared, diff = GitService.diff_branches(repo_path, "main", wt.branch)
        if not compared:
            return False, "Could not compare committed worktree changes"
        if not diff.strip():
            return False, "No committed worktree changes to merge; reject this empty submission"

        # Merge user branch into main
        success, msg = GitService.merge_branch(repo_path, wt.branch, into="main")
        if not success:
            return False, msg

        # Update status
        now = datetime.now(UTC).isoformat()
        self.db._raw_conn.execute(
            "UPDATE worktree SET status = 'merged', merged_at = ?, updated_at = ? WHERE id = ?",
            (now, now, wt.id),
        )
        self.db._raw_conn.commit()

        # Clear the diff index
        diff_db_path = Path(wt.diff_db_path)
        if diff_db_path.exists():
            diff_db_path.unlink()

        logger.info("Merged worktree %s/%s into main", wt.username, kb_name)
        return True, f"Merged {wt.branch} into main"

    def reject(self, kb_name: str, user_id: int, feedback: str = "") -> WorktreeInfo:
        """Reject a submission with optional feedback."""
        row = self._get_worktree_row(kb_name, user_id)
        if not row:
            raise EntryNotFoundError(f"No worktree for user {user_id} in KB '{kb_name}'")

        now = datetime.now(UTC).isoformat()
        self.db._raw_conn.execute(
            "UPDATE worktree SET status = 'rejected', rejected_at = ?, feedback = ?, updated_at = ? "
            "WHERE id = ?",
            (now, feedback, now, row["id"]),
        )
        self.db._raw_conn.commit()

        row = self._get_worktree_row(kb_name, user_id)
        return self._row_to_info(row)

    def reset_to_main(self, kb_name: str, user_id: int) -> WorktreeInfo:
        """Reset user's worktree to main branch content.

        Discards all user changes, resets branch to main HEAD,
        and clears the diff index.
        """
        row = self._get_worktree_row(kb_name, user_id)
        if not row:
            raise EntryNotFoundError(f"No worktree for user {user_id} in KB '{kb_name}'")

        wt = self._row_to_info(row)
        worktree_path = Path(wt.worktree_path)

        # Reset branch to main
        import subprocess

        from pyrite.services.git_service import GitService

        subprocess.run(
            ["git", "reset", "--hard", "main"],
            cwd=str(worktree_path),
            capture_output=True,
            text=True,
            env=GitService.subprocess_env(),
        )

        # Clear diff index
        diff_db_path = Path(wt.diff_db_path)
        if diff_db_path.exists():
            diff_db_path.unlink()

        # Reset status to active
        now = datetime.now(UTC).isoformat()
        self.db._raw_conn.execute(
            "UPDATE worktree SET status = 'active', submitted_at = NULL, "
            "merged_at = NULL, rejected_at = NULL, feedback = NULL, updated_at = ? "
            "WHERE id = ?",
            (now, wt.id),
        )
        self.db._raw_conn.commit()

        row = self._get_worktree_row(kb_name, user_id)
        return self._row_to_info(row)

    def delete_worktree(self, kb_name: str, user_id: int) -> bool:
        """Remove a user's worktree entirely."""
        row = self._get_worktree_row(kb_name, user_id)
        if not row:
            return False

        wt = self._row_to_info(row)
        repo_path = Path(wt.repo_path)
        worktree_path = Path(wt.worktree_path)

        # Remove git worktree
        from pyrite.services.git_service import GitService

        GitService.worktree_remove(repo_path, worktree_path, force=True)

        # Clean up worktree directory if it still exists
        if worktree_path.exists():
            shutil.rmtree(worktree_path, ignore_errors=True)

        # Remove DB record
        self.db._raw_conn.execute("DELETE FROM worktree WHERE id = ?", (wt.id,))
        self.db._raw_conn.commit()

        logger.info("Deleted worktree %s/%s", wt.username, kb_name)
        return True

    def get_user_kb_config(self, kb_name: str, user_id: int) -> KBConfig | None:
        """Get a KBConfig clone with path pointing to the user's worktree.

        Returns None if the user has no worktree for this KB.
        """
        row = self._get_worktree_row(kb_name, user_id)
        if not row:
            return None

        kb_config = self.config.get_kb(kb_name)
        if not kb_config:
            return None

        wt = self._row_to_info(row)
        worktree_path = Path(wt.worktree_path)

        # If KB is a sub-path within a repo, compute the full path
        if kb_config.repo_subpath:
            user_kb_path = worktree_path / kb_config.repo_subpath
        else:
            # KB path relative to repo root
            repo_root = Path(wt.repo_path)
            try:
                relative = kb_config.path.relative_to(repo_root)
                user_kb_path = worktree_path / relative
            except ValueError:
                # KB path is the repo root itself
                user_kb_path = worktree_path

        return dataclasses.replace(kb_config, path=user_kb_path)

    def get_user_diff_db(self, kb_name: str, user_id: int) -> PyriteDB | None:
        """Get a PyriteDB instance for the user's diff index.

        Returns None if the user has no worktree for this KB.
        The caller should cache this — PyriteDB init is heavyweight.
        """
        row = self._get_worktree_row(kb_name, user_id)
        if not row:
            return None
        return PyriteDB(Path(row["diff_db_path"]))
