"""
Ephemeral KB Service

Lifecycle management for temporary knowledge bases with TTL.
"""

import logging
import shutil
import time
from pathlib import Path

from ..config import KBConfig, PyriteConfig, check_config_save, save_config
from ..storage.database import PyriteDB
from .credential_events import announce_kb_policy_change
from .kb_names import PLAIN_KB_NAME_RULE, is_plain_kb_name, kb_name_in_use

logger = logging.getLogger(__name__)


class InvalidEphemeralKBNameError(ValueError):
    """The requested ephemeral KB name is unsafe or already in use."""


class EphemeralKBService:
    """Service for ephemeral KB lifecycle management."""

    def __init__(self, config: PyriteConfig, db: PyriteDB):
        self.config = config
        self.db = db

    def create_ephemeral_kb(
        self,
        name: str,
        ttl: int = 3600,
        description: str = "",
        default_role: str = "none",
    ) -> KBConfig:
        """Create an ephemeral KB with TTL.

        Ephemeral KBs are private by default (`default_role` "none": only
        per-KB grantees and global admins). The policy is persisted with the
        KB -- in the registry row and in config.yaml -- in the same step that
        creates it, so every process resolves the same one.

        Raises InvalidEphemeralKBNameError for a name that is not a plain
        name, that another KB uses (in config or in the registry table), or
        whose directory already exists. The directory is created with
        exist_ok=False, so it is the claim: of two concurrent creates of one
        name, or of two names one case-insensitive filesystem folds together,
        exactly one gets it, and a leftover directory is never adopted.
        """
        # The name becomes a directory under <workspace>/ephemeral/ and is
        # chosen by any write-role user, so it must be a plain name.
        if not is_plain_kb_name(name):
            raise InvalidEphemeralKBNameError(
                f"Invalid ephemeral KB name: use {PLAIN_KB_NAME_RULE}"
            )
        if kb_name_in_use(self.config, self.db, name):
            raise InvalidEphemeralKBNameError("That KB name is not available")
        ephemeral_dir = self._root() / name
        if not self._inside_root(ephemeral_dir):
            raise InvalidEphemeralKBNameError("Invalid ephemeral KB name")
        self._root().mkdir(parents=True, exist_ok=True)
        try:
            ephemeral_dir.mkdir(exist_ok=False)
        except FileExistsError:
            raise InvalidEphemeralKBNameError("That KB name is not available") from None

        try:
            return self._register(name, ephemeral_dir, ttl, description, default_role)
        except BaseException:
            # The directory is ours (we just created it); do not leave a
            # leftover that would block the name forever.
            shutil.rmtree(ephemeral_dir, ignore_errors=True)
            raise

    def _register(
        self,
        name: str,
        ephemeral_dir: Path,
        ttl: int,
        description: str,
        default_role: str,
    ) -> KBConfig:
        description = description or f"Ephemeral KB (TTL: {ttl}s)"
        # Insert-only: another process may have registered the name since the
        # check above, and that row must not be overwritten.
        if not self.db.insert_new_kb(
            name=name,
            kb_type="generic",
            path=str(ephemeral_dir),
            description=description,
            default_role=default_role,
        ):
            raise InvalidEphemeralKBNameError("That KB name is not available")
        kb = KBConfig(
            name=name,
            path=ephemeral_dir,
            kb_type="generic",
            description=description,
            ephemeral=True,
            ttl=ttl,
            created_at_ts=time.time(),
            default_role=default_role,
        )
        try:
            self.config.add_kb(kb)
            save_config(self.config)
        except BaseException:
            if self.config.get_kb(name) is kb:
                self.config.remove_kb(name)
            self.db.unregister_kb(name)
            raise
        return kb

    def _root(self) -> Path:
        return self.config.settings.workspace_path / "ephemeral"

    def _inside_root(self, path: Path) -> bool:
        """True when path resolves strictly inside the ephemeral root."""
        root = self._root().resolve()
        resolved = Path(path).resolve()
        return resolved != root and resolved.is_relative_to(root)

    def _remove_dir(self, kb: KBConfig) -> None:
        """Delete an expired KB's directory -- only ever inside the ephemeral root.

        A KB persisted to config before names were validated can point at any
        directory (another KB's, say); expiring it must not delete that.
        """
        if not kb.path.exists():
            return
        if not self._inside_root(kb.path):
            logger.warning(
                "Ephemeral KB %r points outside %s; unregistering it without deleting %s",
                kb.name,
                self._root(),
                kb.path,
            )
            return
        shutil.rmtree(kb.path, ignore_errors=True)

    def list_ephemeral_kbs(self) -> list[dict]:
        """List all active ephemeral KBs with metadata."""
        now = time.time()
        result = []
        for kb in self.config.knowledge_bases:
            if not kb.ephemeral:
                continue
            expires_at = (kb.created_at_ts + kb.ttl) if kb.created_at_ts and kb.ttl else None
            result.append(
                {
                    "name": kb.name,
                    "path": str(kb.path),
                    "created_at": kb.created_at_ts,
                    "ttl": kb.ttl,
                    "expires_at": expires_at,
                    "expired": expires_at is not None and now > expires_at,
                }
            )
        return result

    def _remove(self, kb: KBConfig) -> None:
        """Remove an ephemeral KB: its index rows, its grants, its files, its config.

        `unregister_kb` deletes the per-KB grants with the KB, including the
        admin grant `AuthService.create_user_ephemeral_kb` records for the
        creator. Does not save the config; callers do, once.
        """
        self.db.unregister_kb(kb.name)
        # Grants go with the KB inside db.unregister_kb; the directory is
        # never deleted outside the ephemeral root (see _remove_dir).
        self._remove_dir(kb)
        self.config.remove_kb(kb.name)
        announce_kb_policy_change(kb.name)

    def force_expire_kb(self, name: str) -> bool:
        """Force-expire a specific ephemeral KB. Returns True if removed."""
        kb = next((k for k in self.config.knowledge_bases if k.name == name), None)
        if not kb or not kb.ephemeral:
            return False
        # Before the rows and the directory go (#377).
        check_config_save(self.config, removed=[name])
        self._remove(kb)
        save_config(self.config, removed=[name])
        return True

    def gc_ephemeral_kbs(self) -> list[str]:
        """Garbage-collect expired ephemeral KBs. Returns list of removed KB names."""
        now = time.time()
        expired = [
            kb
            for kb in self.config.knowledge_bases
            if kb.ephemeral and kb.ttl and kb.created_at_ts and now - kb.created_at_ts > kb.ttl
        ]
        removed = [kb.name for kb in expired]
        if not removed:
            return removed

        # Before any rows or directories go (#377).
        check_config_save(self.config, removed=removed)
        for kb in expired:
            self._remove(kb)
        save_config(self.config, removed=removed)

        return removed
