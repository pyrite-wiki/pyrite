"""
KB Registry Service — DB-first unified KB lifecycle management.

All surfaces (CLI, REST API, MCP, Web UI) delegate KB CRUD here.
Config.yaml KBs are seeded as source="config"; user-added KBs are source="user".
"""

import logging
from pathlib import Path
from typing import Any

from ..config import KBConfig, PyriteConfig
from ..exceptions import (
    ConfigError,
    KBAlreadyExistsError,
    KBDefinedInConfigError,
    KBNotFoundError,
    KBProtectedError,
)
from ..storage.database import PyriteDB
from ..storage.index import IndexManager
from .credential_events import announce_kb_policy_change
from .site_cache import drop_kb_site_pages

logger = logging.getLogger(__name__)


class KBRegistryService:
    """Unified KB registry backed by the DB kb table."""

    def __init__(self, config: PyriteConfig, db: PyriteDB, index_mgr: IndexManager | None = None):
        # `index_mgr` is needed only by the operations that index (add, reindex,
        # health); the access policy reads registrations without one.
        self.config = config
        self.db = db
        self.index_mgr = index_mgr

    def is_registered(self, name: str) -> bool:
        """Does the index registry hold a KB called `name`?

        A plain read of the `kb` table through this service's session, never
        the ORM identity map: committed rows are seen at once; a row written but
        not yet committed on another connection is not.
        """
        return bool(self.db.execute_sql("SELECT 1 FROM kb WHERE name = :name", {"name": name}))

    def registered_default_role(self, name: str) -> str | None:
        """The `default_role` the registry row stores, as stored -- unconfined.

        None when the row has none or there is no row. Whether an untrusted
        config may honour it is the access policy's question
        (`PyriteConfig.confined_default_role`), not the registry's.
        """
        rows = self.db.execute_sql("SELECT default_role FROM kb WHERE name = :name", {"name": name})
        return rows[0]["default_role"] if rows else None

    def seed_from_config(self) -> int:
        """Reconcile config.yaml KB ownership in the DB. Idempotent.

        KBs removed from config.yaml become user-managed without losing data.
        Returns the number of KBs seeded from the current config.
        """
        from sqlalchemy import update

        from ..storage.models import KB

        count = 0
        for kb in self.config.knowledge_bases:
            self.db.register_kb(
                name=kb.name,
                kb_type=kb.kb_type,
                path=str(kb.path),
                description=kb.description,
                source="config",
                default_role=kb.default_role,
            )
            count += 1

        config_names = [kb.name for kb in self.config.knowledge_bases]
        self.db.session.execute(
            update(KB)
            .where(KB.source == "config", KB.name.not_in(config_names))
            .values(source="user")
        )
        self.db.session.commit()
        self.db.merge_registered_kbs(self.config)
        return count

    def list_kbs(self, type_filter: str | None = None) -> list[dict[str, Any]]:
        """List all KBs from DB, enriched with config metadata."""
        from sqlalchemy import text

        query = "SELECT * FROM kb ORDER BY name"
        rows = self.db.session.execute(text(query)).fetchall()

        result = []
        for row in rows:
            r = dict(row._mapping)
            # Enrich with config metadata (read_only, shortname, etc.)
            cfg = self.config.get_kb(r["name"])
            kb_info = {
                "name": r["name"],
                "type": r.get("kb_type", "generic"),
                "path": r.get("path", ""),
                "description": r.get("description", ""),
                "source": r.get("source", "user"),
                "read_only": cfg.read_only if cfg else False,
                "shortname": cfg.shortname if cfg else None,
                "entries": r.get("entry_count", 0) or 0,
                "indexed": bool(r.get("last_indexed")),
                "last_indexed": r.get("last_indexed"),
                "default_role": r.get("default_role"),
                "default_role_editable": not self.config.default_role_is_hand_written(r["name"]),
            }
            if type_filter and kb_info["type"] != type_filter:
                continue
            result.append(kb_info)
        return result

    def get_kb(self, name: str) -> dict[str, Any] | None:
        """Get a single KB from DB."""
        from ..storage.models import KB

        kb = self.db.session.get(KB, name)
        if not kb:
            return None
        cfg = self.config.get_kb(name)
        return {
            "name": kb.name,
            "type": kb.kb_type,
            "path": kb.path,
            "description": kb.description or "",
            "source": kb.source or "user",
            "read_only": cfg.read_only if cfg else False,
            "shortname": cfg.shortname if cfg else None,
            "entries": kb.entry_count or 0,
            "indexed": bool(kb.last_indexed),
            "last_indexed": kb.last_indexed,
            "default_role": kb.default_role,
            "default_role_editable": not self.config.default_role_is_hand_written(name),
        }

    def add_kb(
        self,
        name: str,
        path: str,
        kb_type: str = "generic",
        description: str = "",
    ) -> dict[str, Any]:
        """Register a new user KB in the DB (not config.yaml).

        If ``kb_type`` matches a registered plugin preset, the preset is
        materialized into the KB directory (kb.yaml, subdirectories, templates).

        Raises KBAlreadyExistsError (a ConfigError) if a KB with the same
        name already exists.
        """
        from ..storage.models import KB

        existing = self.db.session.get(KB, name)
        if existing:
            raise KBAlreadyExistsError(f"KB '{name}' already exists")

        # Checked before anything is created: no directory, no preset, no row.
        self.config.refuse_outside_tree(path, f"adding KB '{name}'")
        resolved = Path(path).expanduser().resolve()
        resolved.mkdir(parents=True, exist_ok=True)

        # Apply plugin preset if kb_type matches one
        self._apply_preset(resolved, name, kb_type, description)

        self.db.register_kb(
            name=name,
            kb_type=kb_type,
            path=str(resolved),
            description=description,
            source="user",
        )

        # config._db_kb_cache is only populated once, on first DB access
        # (create_app()'s _app_get_db() calls db.merge_registered_kbs()
        # lazily and then never again). Without this, a KB registered here
        # is invisible to config.get_kb()/all_kbs() -- and therefore to
        # KBService.get_kb() and entry creation -- for the rest of this
        # process's life, even though it's already committed to the DB.
        # Update the in-memory cache immediately so it's usable in the same
        # request/process, not just after a restart.
        self.config.register_db_kbs(
            [
                {
                    "name": name,
                    "path": str(resolved),
                    "kb_type": kb_type,
                    "description": description,
                    "default_role": None,
                }
            ]
        )

        return self.get_kb(name)  # type: ignore[return-value]

    def _apply_preset(self, kb_path: Path, name: str, kb_type: str, description: str) -> None:
        """Materialize a plugin preset into the KB directory.

        Writes kb.yaml with type definitions, creates subdirectories,
        and generates entry templates for each type.
        """
        from ..utils.yaml import dump_yaml_file

        try:
            from ..plugins import get_registry

            presets = get_registry().get_all_kb_presets()
        except Exception:
            logger.debug("No plugin registry available, skipping preset", exc_info=True)
            return

        preset = presets.get(kb_type)
        if not preset:
            return

        logger.info("Applying preset '%s' to KB '%s'", kb_type, name)

        # Build kb.yaml content
        kb_yaml: dict[str, Any] = {
            "name": name,
            "kb_type": kb_type,
            "description": description or preset.get("description", ""),
        }
        if preset.get("guidelines"):
            kb_yaml["guidelines"] = preset["guidelines"]
        if preset.get("policies"):
            kb_yaml["policies"] = preset["policies"]

        # Types section
        types_section: dict[str, Any] = {}
        for type_name, type_info in preset.get("types", {}).items():
            td: dict[str, Any] = {}
            if type_info.get("description"):
                td["description"] = type_info["description"]
            if type_info.get("required"):
                td["required"] = type_info["required"]
            if type_info.get("optional"):
                td["optional"] = type_info["optional"]
            if type_info.get("subdirectory"):
                td["subdirectory"] = type_info["subdirectory"]
            if type_info.get("edge_type"):
                td["edge_type"] = True
            if type_info.get("endpoints"):
                td["endpoints"] = type_info["endpoints"]
            types_section[type_name] = td
        if types_section:
            kb_yaml["types"] = types_section

        # Write kb.yaml
        kb_yaml_path = kb_path / "kb.yaml"
        if not kb_yaml_path.exists():
            dump_yaml_file(kb_yaml, kb_yaml_path)
            logger.info("Wrote kb.yaml for KB '%s'", name)

        # Create subdirectories
        for dirname in preset.get("directories", []):
            (kb_path / dirname).mkdir(parents=True, exist_ok=True)

        # Generate entry templates
        templates_dir = kb_path / "_templates"
        templates_dir.mkdir(exist_ok=True)
        for type_name, type_info in preset.get("types", {}).items():
            template_path = templates_dir / f"{type_name}.md"
            if template_path.exists():
                continue
            template_content = self._generate_template(type_name, type_info)
            template_path.write_text(template_content, encoding="utf-8")

        logger.info(
            "Preset '%s' applied: %d types, %d directories, %d templates",
            kb_type,
            len(types_section),
            len(preset.get("directories", [])),
            len(preset.get("types", {})),
        )

    @staticmethod
    def _generate_template(type_name: str, type_info: dict[str, Any]) -> str:
        """Generate an entry template markdown file for a type."""
        lines = ["---"]
        lines.append(f"template_name: {type_name}")
        lines.append(
            f"template_description: Create a new {type_info.get('description', type_name)}"
        )
        lines.append(f"entry_type: {type_name}")
        # Add optional fields as empty frontmatter values
        for field_name in type_info.get("optional", []):
            if field_name in ("importance", "tags"):
                continue
            lines.append(f"{field_name}: ")
        lines.append("---")
        lines.append("")
        # Body with guidance
        desc = type_info.get("description", type_name)
        lines.append(f"<!-- {desc} -->")
        lines.append("")
        return "\n".join(lines)

    def remove_kb(self, name: str) -> bool:
        """Remove a user-added KB. Raises KBProtectedError for config KBs."""
        from ..storage.models import KB

        kb = self.db.session.get(KB, name)
        if not kb:
            raise KBNotFoundError(f"KB '{name}' not found")

        if (kb.source or "user") == "config":
            raise KBProtectedError(
                f"KB '{name}' is defined in config.yaml and cannot be removed via the registry. "
                "Edit config.yaml to remove it."
            )

        self.db.unregister_kb(name)
        self.config.forget_db_kb(name)
        announce_kb_policy_change(name)
        drop_kb_site_pages(self.config, self.db, name)
        return True

    def update_kb(self, name: str, **updates: Any) -> dict[str, Any]:
        """Update KB metadata (description, kb_type, default_role).

        The change is visible to this process's config -- and so to the
        access policy and every anonymous surface -- when this returns.

        ``default_role`` has one source of truth per KB. A registry KB's is
        its row. A KB the server itself wrote into config.yaml (ephemeral,
        repo-subscribed) has its entry there, which the server rewrites with
        the row. A KB an operator wrote into config.yaml by hand is theirs:
        the change is refused (`KBDefinedInConfigError`, naming the file)
        rather than reported as done and overridden by the file.
        """
        from ..config import current_config_file, save_config
        from ..storage.models import KB

        kb = self.db.session.get(KB, name)
        if not kb:
            raise KBNotFoundError(f"KB '{name}' not found")

        if "default_role" in updates and self.config.default_role_is_hand_written(name):
            raise KBDefinedInConfigError(
                f"KB '{name}' is defined by hand in {current_config_file()}, which sets its "
                "default_role. Change default_role there and restart the server."
            )
        allowed = {"description", "kb_type", "default_role"}
        if "default_role" in updates and (
            self.config.confined_default_role(updates["default_role"]) != updates["default_role"]
        ):
            raise ConfigError(
                f"Refusing to set default_role '{updates['default_role']}' on '{name}': this "
                "server runs on an untrusted repo-local config, where a KB can only be closed. "
                "Publish KBs from a trusted config (~/.pyrite or PYRITE_CONFIG_DIR)."
            )
        current = self.config.get_kb(name)
        role_before = current.default_role if current is not None else kb.default_role
        for key, value in updates.items():
            if key in allowed:
                setattr(kb, key, value)
        written = self.config.server_written_kb(name)
        if "default_role" in updates and written is not None:
            # The server's own config.yaml entry is this KB's policy: rewrite
            # it first, so a refused save leaves neither changed.
            previous = written.default_role
            written.default_role = updates["default_role"]
            try:
                save_config(self.config)
            except BaseException:
                written.default_role = previous
                self.db.session.rollback()
                raise
        self.db.session.commit()
        self._refresh_config_view()
        if "default_role" in updates and updates["default_role"] != role_before:
            announce_kb_policy_change(name)
            drop_kb_site_pages(self.config, self.db, name)
        return self.get_kb(name)  # type: ignore[return-value]

    def _refresh_config_view(self) -> None:
        """Make the config's view of the registry KBs equal the rows.

        Re-read through `merge_registered_kbs`, the one loader of registry
        rows (it applies the untrusted-config confinement and the orphaned
        ephemeral rule), which builds the new lookup aside and swaps it in:
        a row the loader now refuses leaves it, and no reader sees the KB
        missing while it is rebuilt.
        """
        self.db.merge_registered_kbs(self.config)

    def reindex_kb(self, name: str) -> dict[str, int]:
        """Reindex a specific KB. Works for both config and user KBs."""
        # Try config first
        kb_config = self.config.get_kb(name)
        if kb_config:
            return self.index_mgr.sync_kb(kb_config)

        # Fall back to DB-only KB
        kb_config = self.get_kb_config(name)
        if not kb_config:
            raise KBNotFoundError(f"KB '{name}' not found")
        return self.index_mgr.sync_kb(kb_config)

    def health_kb(self, name: str) -> dict[str, Any]:
        """Check KB health: path exists, file count vs index count, staleness."""
        from ..storage.models import KB

        kb = self.db.session.get(KB, name)
        if not kb:
            raise KBNotFoundError(f"KB '{name}' not found")

        kb_path = Path(kb.path)
        path_exists = kb_path.exists()

        file_count = 0
        if path_exists:
            file_count = sum(
                1 for f in kb_path.rglob("*.md") if not any(p.startswith(".") for p in f.parts)
            )

        entry_count = kb.entry_count or 0
        healthy = path_exists and abs(file_count - entry_count) <= max(1, entry_count * 0.1)

        return {
            "name": name,
            "healthy": healthy,
            "path_exists": path_exists,
            "path": kb.path,
            "file_count": file_count,
            "entry_count": entry_count,
            "last_indexed": kb.last_indexed,
            "source": kb.source or "user",
        }

    def get_kb_config(self, name: str) -> KBConfig | None:
        """Build a KBConfig from a DB row (for DB-only KBs)."""
        # Try config first
        cfg = self.config.get_kb(name)
        if cfg:
            return cfg

        # Build from DB row
        from ..storage.models import KB

        kb = self.db.session.get(KB, name)
        if not kb:
            return None

        # The same confinement as loading the registry: a row refused there
        # (outside an untrusted config's tree) is not found here either.
        return self.config.kb_config_from_registry_row(
            {
                "name": kb.name,
                "path": kb.path,
                "kb_type": kb.kb_type,
                "description": kb.description,
                "default_role": kb.default_role,
            }
        )
