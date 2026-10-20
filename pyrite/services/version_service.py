"""
Version Service

Retrieves entry version history and content at specific git commits.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from ..config import PyriteConfig
from ..exceptions import InvalidGitRefError
from ..storage.database import PyriteDB

logger = logging.getLogger(__name__)

# A git object id, abbreviated or full: SHA-1 (40) or SHA-256 (64) hex, at
# least 4 characters (git's own minimum abbreviation). Nothing else -- no
# symbolic refs, no revision expressions, nothing git could read as an option.
_COMMIT_HASH_RE = re.compile(r"[0-9a-fA-F]{4,64}")


class VersionService:
    """Service for entry version history operations."""

    def __init__(self, config: PyriteConfig, db: PyriteDB):
        self.config = config
        self.db = db

    def get_entry_versions(self, entry_id: str, kb_name: str, limit: int = 50) -> list[dict]:
        """Get version history for an entry."""
        return self.db.get_entry_versions(entry_id, kb_name, limit=limit)

    def record_commit(self, kb_name: str, commit_hash: str) -> int:
        """Record entry_version rows for one commit's changed entries.

        Called right after a server write commits (ExportService.commit_kb),
        so a commit is a readable version immediately -- no reindex required
        (#432). Only `.md` files that resolve to a currently-indexed entry
        (via get_entries_for_indexing's id/file_path map) produce a row; a
        commit that touches no entry file records nothing. Idempotent via
        upsert_entry_version's existing (entry_id, kb_name, commit_hash)
        dedup.

        `file_path` is stored KB-relative (matching get_commit_file_changes'
        already-KB-relative, already-outside-KB-refused output), never
        absolute -- an absolute path breaks the moment the KB's directory
        moves (#432 cold read).

        `change_type` is "created" for the file this commit added (status
        "A"), "modified" for anything else -- matching what
        index_with_attribution derives from the full log, not a blanket
        "modified" for every row.

        Returns the number of entry_version rows written (existing rows
        that only had their file_path backfilled do not count).
        """
        from ..services.git_service import GitService

        kb_config = self.config.get_kb(kb_name)
        if not kb_config:
            return 0

        kb_path = kb_config.path
        if not GitService.is_git_repo(kb_path):
            return 0

        commit_info = GitService.get_commit_info(kb_path, commit_hash)
        if commit_info is None:
            return 0

        # (status, KB-relative path, KB-relative rename source) per changed
        # .md file -- already resolved against the KB's repo prefix and
        # refused if outside it (GitService.get_commit_file_changes).
        changed = [
            change
            for change in GitService.get_commit_file_changes(kb_path, commit_hash)
            if change[1].endswith(".md")
        ]
        if not changed:
            return 0

        # Path -> entry id, from the index (not from parsing frontmatter
        # again): the same source of truth IndexManager itself uses. Loaded
        # once for this whole commit, not once per changed file. A row whose
        # path is not under the KB cannot match any changed file; it is
        # skipped, never allowed to stop the others being recorded.
        entries_by_path: dict[str, str] = {}
        for e in self.db.get_entries_for_indexing(kb_name):
            try:
                if not e["file_path"]:
                    raise ValueError("no file path")
                entries_by_path[str(Path(e["file_path"]).relative_to(kb_path))] = e["id"]
            except ValueError:
                logger.warning(
                    "Skipping index row %s: its path is not under KB %s", e["id"], kb_name
                )

        recorded = 0
        for status, kb_relative_path, source in changed:
            entry_id = entries_by_path.get(kb_relative_path)
            if entry_id is None:
                continue
            existed = self.db.entry_version_exists(entry_id, kb_name, commit_hash)
            change_type = "created" if status == "A" else "modified"
            if status.startswith("R") and not self._same_entry_at(
                kb_path, f"{commit_info['hash']}^", source, entry_id
            ):
                # git paired this file with a different entry's (or one
                # from outside the KB): from this entry's view, an add.
                change_type = "created"
            self.db.upsert_entry_version(
                entry_id=entry_id,
                kb_name=kb_name,
                commit_hash=commit_info["hash"],
                author_name=commit_info["author_name"],
                author_email=commit_info["author_email"],
                commit_date=commit_info["date"],
                message=commit_info["message"],
                change_type=change_type,
                file_path=kb_relative_path,
            )
            if not existed:
                recorded += 1
        return recorded

    @staticmethod
    def _same_entry_at(
        kb_path: Path, rev: str, kb_relative_path: str | None, entry_id: str
    ) -> bool:
        """True when `kb_relative_path` holds entry `entry_id` in `rev`'s
        tree. git pairs renames by similarity, and entries share frontmatter
        boilerplate, so only the id says two paths are one entry (#432)."""
        from ..models.core_types import entry_id_from_markdown
        from ..services.git_service import GitService

        if kb_relative_path is None:
            return False
        text = GitService.read_file_at(kb_path, rev, kb_relative_path)
        return text is not None and entry_id_from_markdown(text) == entry_id

    def get_entry_at_version(self, entry_id: str, kb_name: str, commit_hash: str) -> str | None:
        """Get entry content at a specific git commit.

        Returns None when the KB, the entry or the object is unknown, or the
        entry's file does not exist at that commit.

        Raises:
            InvalidGitRefError: `commit_hash` is not a hex object id (checked
                before any lookup or git call), or it names an object that is
                not a commit, such as a tree or a blob. This service is the
                only path from a caller-supplied hash to git.
        """
        import subprocess

        if not _COMMIT_HASH_RE.fullmatch(commit_hash):
            raise InvalidGitRefError("Invalid commit hash: expected a hex object id")

        from ..services.git_service import SERVABLE, GitService

        kb_config = self.config.get_kb(kb_name)
        if not kb_config:
            return None

        kb_path = kb_config.path
        if not GitService.is_git_repo(kb_path):
            return None

        # Find the file path for this entry
        entry = self.db.get_entry(entry_id, kb_name)
        if not entry or not entry.get("file_path"):
            return None

        def _rev_parse(rev: str) -> str | None:
            result = subprocess.run(
                ["git", "rev-parse", "--verify", "--quiet", "--end-of-options", rev],
                cwd=str(kb_path),
                capture_output=True,
                text=True,
                timeout=10,
                env=GitService.subprocess_env(),
            )
            return result.stdout.strip() if result.returncode == 0 else None

        try:
            # The id must name a commit (an annotated tag peels to its commit).
            # `<tree>:<path>` would otherwise read a path from any tree.
            if _rev_parse(f"{commit_hash}^{{object}}") is None:
                return None
            commit = _rev_parse(f"{commit_hash}^{{commit}}")
        except Exception:
            logger.warning("Git rev-parse failed for KB", exc_info=True)
            return None
        if commit is None:
            raise InvalidGitRefError("Invalid commit hash: the object is not a commit")

        # The commit must be one of THIS entry's recorded versions -- not
        # merely any commit that exists in the repo. Without this, any
        # commit id served whatever content that commit's tree happened to
        # have at this entry's path, including commits that never touched
        # this entry (#415). Membership is checked on the peeled, full
        # commit id so an abbreviated form of a recorded hash still matches.
        if not self.db.entry_version_exists(entry_id, kb_name, commit):
            return None

        # Read at the path this *version* had, if recorded (#432) -- a
        # renamed entry's pre-rename commits have their own path, which can
        # differ from the entry's current one. Looked up by the peeled full
        # commit id, since that's what was stored, not the caller's
        # possibly-abbreviated hash. Falls back to the entry's current path
        # for rows recorded before #432 (no stored path), same as today.
        versioned_path = self.db.get_entry_version_file_path(entry_id, kb_name, commit)
        try:
            current_rel_path = str(Path(entry["file_path"]).relative_to(kb_path))
        except ValueError:
            current_rel_path = None
        verdict, text = GitService.version_servability(
            kb_path, commit, versioned_path, current_rel_path, entry_id
        )
        return text if verdict == SERVABLE else None
