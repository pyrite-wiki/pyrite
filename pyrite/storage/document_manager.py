"""
Document Manager — Write-path coordination for KB entries.

Consolidates the repeated save-register-index pattern from KBService
into a single class, completing the ODM abstraction layer.
"""

import logging
from pathlib import Path

from ..config import KBConfig
from ..models import Entry
from .database import PyriteDB
from .index import IndexManager
from .repository import KBRepository

logger = logging.getLogger(__name__)


class DocumentManager:
    """Coordinates file storage and index updates for KB entries.

    Encapsulates the write-path pattern:
        KBRepository.save() → PyriteDB.register_kb() → IndexManager.index_entry()

    Read paths remain on PyriteDB / KBService directly.
    """

    def __init__(self, db: PyriteDB, index_mgr: IndexManager):
        self._db = db
        self._index_mgr = index_mgr

    def save_entry(
        self,
        entry: Entry,
        kb_name: str,
        kb_config: KBConfig,
        *,
        touch_updated_at: bool = True,
        is_create: bool = False,
    ) -> Path:
        """Save an entry to disk, register the KB, and index it.

        If the entry's resolved path has changed (e.g. due to a templated
        subdirectory like ``backlog/{status}``), the old file is removed.

        Args:
            entry: The entry to save.
            kb_name: Name of the knowledge base.
            kb_config: KB configuration (provides path, type, description).
            touch_updated_at: Passed through to ``KBRepository.save``; ``False``
                keeps a caller-supplied ``updated_at`` (#151).
            is_create: ``True`` only for a brand-new entry (the sole caller is
                ``KBService``'s create pipeline). A file's name is fixed at
                creation (#391 cold read): the default ``False`` keeps the
                entry's existing filename (``entry.file_path``'s basename)
                fixed across an update -- a `file_pattern` type's title or
                field edit must not rename the file every time, matching the
                subdirectory-preservation logic just below. ``True`` lets
                ``KBRepository.save`` resolve a fresh filename, since there
                is no existing one yet, and also publishes exclusively
                (``exclusive=True``): create means never overwrite, so the
                same flag that says "resolve a fresh path" says "refuse if
                something is already there when this writes" -- the
                write-pipeline's own exists() check and the write itself are
                two different moments, and two truly concurrent creates can
                both pass the check before either publishes (#391 cold read
                round 2).

        Returns:
            Path to the saved file.
        """
        repo = KBRepository(kb_config)

        # Find current on-disk location before saving to new path
        old_path = repo.find_file(entry.id)

        # Preserve deliberate placement on update. By default repo.save()
        # re-infers the type-default subdirectory, which would relocate a file
        # the user/convention deliberately put elsewhere (e.g. a backlog item in
        # backlog/done/, an ADR in adrs/). Only a *templated* subdirectory
        # (e.g. backlog/{status}) legitimately implies a move on field change;
        # for static/unset subdirs, keep the file where it already is.
        subdir = None
        if old_path is not None and not self._uses_templated_subdir(repo, entry):
            existing_subdir = self._subdir_of(old_path, kb_config.path)
            if existing_subdir is not None:
                subdir = existing_subdir

        file_path = repo.save(
            entry,
            subdir=subdir,
            touch_updated_at=touch_updated_at,
            keep_filename=not is_create,
            exclusive=is_create,
        )

        # Clean up old file if path changed (template-driven move)
        if old_path and old_path.resolve() != file_path.resolve() and old_path.exists():
            self._remove_old_file(old_path, kb_config.path)

        self._db.register_kb(
            name=kb_name,
            kb_type=kb_config.kb_type,
            path=str(kb_config.path),
            description=kb_config.description,
        )

        self._index_mgr.index_entry(entry, kb_name, file_path)
        return file_path

    @staticmethod
    def _uses_templated_subdir(repo: KBRepository, entry: Entry) -> bool:
        """True if the entry type's declared subdirectory has a ``{field}``
        placeholder, meaning a field change can legitimately move the file."""
        try:
            schema = repo.config.kb_schema
            type_schema = schema.get_type_schema(entry.entry_type)
        except Exception as e:
            logger.warning(
                "Schema lookup failed for entry type %r, assuming non-templated subdirectory: %s",
                entry.entry_type,
                e,
            )
            return False
        sub = getattr(type_schema, "subdirectory", None) if type_schema else None
        return bool(sub) and "{" in sub

    @staticmethod
    def _subdir_of(path: Path, kb_root: Path) -> str | None:
        """Subdirectory of ``path`` relative to the KB root, or None if the file
        sits at the KB root. Used to keep an entry in its existing location."""
        try:
            rel = path.resolve().relative_to(kb_root.resolve())
        except (ValueError, OSError):
            return None
        parent = rel.parent
        return None if str(parent) == "." else str(parent)

    def _remove_old_file(self, old_path: Path, kb_root: Path) -> None:
        """Remove old file after a template-driven path change. Git-aware."""
        import subprocess

        try:
            # Check if this is a git repo
            result = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=str(kb_root),
                capture_output=True,
                timeout=5,
            )
            if result.returncode == 0:
                subprocess.run(
                    ["git", "rm", "--quiet", "--force", str(old_path)],
                    cwd=str(kb_root),
                    capture_output=True,
                    timeout=10,
                )
            else:
                old_path.unlink(missing_ok=True)
        except Exception:
            old_path.unlink(missing_ok=True)
            logger.warning("Git-aware move failed, deleted old file directly", exc_info=True)

        # Clean up empty parent directories up to kb_root
        parent = old_path.parent
        try:
            resolved_root = kb_root.resolve()
            while parent.resolve() != resolved_root and not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent
        except OSError:
            pass

    def delete_entry(self, entry_id: str, kb_name: str, kb_config: KBConfig) -> bool:
        """Delete an entry from disk and remove it from the index.

        Args:
            entry_id: ID of the entry to delete.
            kb_name: Name of the knowledge base.
            kb_config: KB configuration.

        Returns:
            True if the file was deleted, False if not found.
        """
        repo = KBRepository(kb_config)
        # The index row's file is a candidate the repository verifies before
        # it walks the KB (ADR-0038 step 1). Every file holding the id comes
        # from the reconcile's plan (step 2, #494): it reads only files the
        # index does not know or whose stat moved, so a copy written by hand
        # since the last sync is found, and nothing is written to the index.
        row = self._db.get_entry(entry_id, kb_name)
        indexed = Path(row["file_path"]) if row and row.get("file_path") else None
        plan = self._index_mgr.plan_reconcile(kb_config)
        holders = [claim.path for claim in plan.holders.get(entry_id, [])]
        file_deleted = repo.delete(entry_id, indexed_path=indexed, holders=holders)
        self._db.delete_entry(entry_id, kb_name)
        return file_deleted

    def index_entry(self, entry: Entry, kb_name: str, file_path: Path) -> None:
        """Index an entry without writing to disk (re-index from existing file).

        Args:
            entry: The entry to index.
            kb_name: Name of the knowledge base.
            file_path: Path to the existing file on disk.
        """
        self._index_mgr.index_entry(entry, kb_name, file_path)
