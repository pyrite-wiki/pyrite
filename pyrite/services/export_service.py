"""
Export Service

KB export and git operations, including export-to-repo (clone, export, commit, push).
"""

import logging
import re
import tempfile
from pathlib import Path

from ..config import PyriteConfig
from ..exceptions import PyriteError
from ..storage.database import PyriteDB
from ..utils.sanitize import sanitize_filename, unique_path_component

logger = logging.getLogger(__name__)


def _yaml_quote(value: str) -> str:
    """Quote a YAML scalar value if it contains special characters."""
    if not value:
        return '""'
    # Values that need quoting: contains :, #, newline, leading/trailing spaces,
    # looks like a boolean/null, or starts with special chars
    needs_quoting = (
        ":" in value
        or "#" in value
        or "\n" in value
        or value != value.strip()
        or value.startswith(("{", "[", "'", '"', "&", "*", "!", "|", ">", "%", "@", "`"))
        or value.lower() in ("true", "false", "yes", "no", "null", "~", "on", "off")
        or re.match(r"^[\d.]+$", value)  # looks like a number
    )
    if needs_quoting:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        return f'"{escaped}"'
    return value


class ExportService:
    """Service for KB export and git operations."""

    def __init__(self, config: PyriteConfig, db: PyriteDB):
        self.config = config
        self.db = db

    def export_kb_to_directory(self, kb_name: str, target_dir: Path) -> dict:
        """Export all entries in a KB as markdown files to a directory.

        Args:
            kb_name: Name of the KB to export
            target_dir: Target directory for exported files

        Returns:
            Summary dict with entries_exported and files_created
        """
        import shutil

        kb_config = self.config.require_kb(kb_name, db=self.db)  # the one resolver (#952)

        target_dir.mkdir(parents=True, exist_ok=True)

        # Copy kb.yaml if it exists
        kb_yaml_src = kb_config.kb_yaml_path
        if kb_yaml_src.exists():
            shutil.copy2(kb_yaml_src, target_dir / "kb.yaml")

        # Get all entries from the KB
        entries = self.db.list_entries(kb_name=kb_name, limit=100000)

        files_created = 0
        for entry in entries:
            entry_type = entry.get("entry_type", "note")
            entry_id = entry.get("id", "unknown")
            title = entry.get("title", "")
            body = entry.get("body", "")

            # Organize by entry_type subdirectory. entry_type is a stored,
            # caller-controlled value (unknown types pass through verbatim
            # for GenericEntry/plugin support) -- sanitize it before it
            # becomes a path component so an absolute or `..`-bearing type
            # cannot write outside target_dir (#221).
            type_dir = target_dir / sanitize_filename(entry_type)
            type_dir.mkdir(parents=True, exist_ok=True)

            # Build YAML frontmatter
            frontmatter_fields = {
                "title": title,
                "type": entry_type,
            }
            if entry.get("date"):
                frontmatter_fields["date"] = entry["date"]
            if entry.get("status"):
                frontmatter_fields["status"] = entry["status"]
            if entry.get("tags"):
                frontmatter_fields["tags"] = entry["tags"]

            # Format as markdown with frontmatter using proper YAML quoting
            fm_lines = ["---"]
            for key, val in frontmatter_fields.items():
                if isinstance(val, list):
                    fm_lines.append(f"{key}:")
                    for item in val:
                        fm_lines.append(f"  - {_yaml_quote(str(item))}")
                else:
                    fm_lines.append(f"{key}: {_yaml_quote(str(val))}")
            fm_lines.append("---")
            fm_lines.append("")

            content = "\n".join(fm_lines) + (body or "")

            # unique_path_component (not sanitize_filename directly) so
            # distinct ids that sanitize alike (e.g. "a/b" and "a_b") get
            # distinct filenames instead of one silently overwriting the
            # other (#221 redispatch cold read).
            file_path = type_dir / f"{unique_path_component(entry_id)}.md"
            file_path.write_text(content, encoding="utf-8")
            files_created += 1

        return {
            "entries_exported": len(entries),
            "files_created": files_created,
        }

    def export_kb_to_repo(
        self,
        kb_name: str,
        repo_url: str,
        github_token: str | None = None,
        branch: str = "main",
        commit_message: str | None = None,
    ) -> dict:
        """Export a KB's entries into a target repo: clone, write entries, commit, push.

        Entries are written into a ``kb_name/`` subdirectory inside the target repo
        so that multiple KBs can coexist.

        Args:
            kb_name: KB to export
            repo_url: Remote repo URL to push to
            github_token: OAuth token (optional for public repos)
            branch: Branch to push to
            commit_message: Custom commit message

        Returns:
            Summary dict with entries_exported, commit_hash, etc.

        Raises:
            InvalidGitRefError: `branch` is not a plain branch name.
        """
        from .git_service import GitService

        self.config.require_kb(kb_name, db=self.db)  # the one resolver (#952)
        # The branch is cloned and then pushed: refuse a value that is not a
        # plain branch name before either runs.
        GitService.validate_branch_name(branch)

        with tempfile.TemporaryDirectory() as tmpdir:
            clone_path = Path(tmpdir) / "repo"

            # Clone the target repo
            success, msg = GitService.clone(
                repo_url, clone_path, branch=branch, depth=None, token=github_token
            )
            if not success:
                return {"success": False, "error": f"Clone failed: {msg}"}

            # Export entries into kb_name/ subdirectory
            export_dir = clone_path / kb_name
            export_dir.mkdir(parents=True, exist_ok=True)
            result = self.export_kb_to_directory(kb_name, export_dir)

            if result["entries_exported"] == 0:
                return {"success": True, "entries_exported": 0, "message": "No entries to export"}

            # Commit
            if not commit_message:
                commit_message = f"Export KB '{kb_name}': {result['entries_exported']} entries"

            commit_ok, commit_result = GitService.commit(clone_path, commit_message)
            if not commit_ok:
                return {
                    "success": False,
                    "error": commit_result.get("error", "Commit failed"),
                }

            # Push
            push_ok, push_msg = GitService.push(
                clone_path, remote="origin", branch=branch, token=github_token
            )
            if not push_ok:
                return {"success": False, "error": push_msg}

            return {
                "success": True,
                **result,
                "commit_hash": commit_result.get("commit_hash", ""),
                "branch": branch,
                "message": f"Exported {result['entries_exported']} entries and pushed to {repo_url}",
            }

    def commit_kb(
        self,
        kb_name: str,
        message: str,
        paths: list[str] | None = None,
        sign_off: bool = False,
    ) -> dict:
        """
        Commit changes in a KB's git repository.

        Args:
            kb_name: Knowledge base name
            message: Commit message
            paths: Specific file paths to stage (all changes if None)
            sign_off: Add Signed-off-by line

        Returns:
            dict with success, commit_hash, files_changed, etc.

        After a successful commit, the commit's entry versions are recorded
        in the index database (#432). If that recording fails, the shared
        database session is rolled back so it stays usable -- which also
        discards any database work the caller left pending (uncommitted) on
        that session. Commit your own session work before calling this.

        Raises:
            KBNotFoundError: KB doesn't exist
            PyriteError: KB is not in a git repository
        """
        from .git_service import GitService

        kb = self.config.require_kb(kb_name, db=self.db)  # the one resolver (#952)

        if not GitService.is_git_repo(kb.path):
            raise PyriteError(f"KB '{kb_name}' is not in a git repository")

        success, result = GitService.commit(kb.path, message, paths=paths, sign_off=sign_off)

        if success:
            # Record entry_version rows for this commit so the version list
            # and read endpoints serve it immediately, without a reindex
            # (#432). Every commit path (REST, MCP, CLI, KBService.publish)
            # goes through this method, so recording here covers them all.
            commit_hash = result.get("commit_hash")
            if commit_hash:
                try:
                    from .version_service import VersionService

                    VersionService(self.config, self.db).record_commit(kb_name, commit_hash)
                except Exception:
                    # A recording failure must not turn a successful commit
                    # into a reported failure -- the commit already
                    # happened. The next `index build --with-attribution`
                    # still recovers these rows.
                    #
                    # The rollback is not optional: a failed INSERT (e.g.
                    # "database is locked" under write contention from a
                    # concurrent index sync) leaves this ORM session's
                    # transaction in a failed state. Without rolling it
                    # back here, every later call on this exact session --
                    # which the MCP server and the CLI both reuse across
                    # requests, not just this one call -- raises
                    # PendingRollbackError instead of doing its own work
                    # (coordinator cold read on #432).
                    self.db.session.rollback()
                    logger.warning(
                        "Failed to record entry_version rows for %s@%s",
                        kb_name,
                        commit_hash,
                        exc_info=True,
                    )
            return {"success": True, **result}
        return {"success": False, "error": result.get("error", "Unknown error")}

    @staticmethod
    def export_collection_entries(
        entries: list,
        output_dir: Path,
        bundle_strategy=None,
        source_mode=None,
        title: str = "Exported Knowledge Base",
    ) -> dict:
        """Export a list of Entry objects as NotebookLM-ready markdown.

        Args:
            entries: List of Entry objects to export
            output_dir: Directory to write output files
            bundle_strategy: How to group entries into files (default: AUTO)
            source_mode: How to handle source visibility (default: PUBLIC)
            title: Title for the manifest document

        Returns:
            Summary dict with entries_exported, files_created
        """
        from ..renderers.notebooklm import (
            BundleStrategy,
            SourceMode,
            bundle_entries,
            generate_manifest,
        )

        if bundle_strategy is None:
            bundle_strategy = BundleStrategy.AUTO
        if source_mode is None:
            source_mode = SourceMode.PUBLIC

        if not entries:
            return {"entries_exported": 0, "files_created": 0}

        output_dir.mkdir(parents=True, exist_ok=True)

        # Bundle entries into files
        files = bundle_entries(entries, strategy=bundle_strategy, source_mode=source_mode)

        # Write bundled files
        for filename, content in files.items():
            (output_dir / filename).write_text(content, encoding="utf-8")

        # Generate and write manifest
        manifest = generate_manifest(entries, title=title)
        (output_dir / "_manifest.md").write_text(manifest, encoding="utf-8")

        return {
            "entries_exported": len(entries),
            "files_created": len(files),
        }

    def push_kb(
        self,
        kb_name: str,
        remote: str = "origin",
        branch: str | None = None,
    ) -> dict:
        """
        Push KB commits to a remote repository.

        Args:
            kb_name: Knowledge base name
            remote: Remote name (default: origin)
            branch: Branch to push (default: current branch)

        Returns:
            dict with success and message

        Raises:
            KBNotFoundError: KB doesn't exist
            PyriteError: KB is not in a git repository
        """
        from .git_service import GitService

        kb = self.config.require_kb(kb_name, db=self.db)  # the one resolver (#952)

        if not GitService.is_git_repo(kb.path):
            raise PyriteError(f"KB '{kb_name}' is not in a git repository")

        success, msg = GitService.push(kb.path, remote=remote, branch=branch)
        return {"success": success, "message": msg}
