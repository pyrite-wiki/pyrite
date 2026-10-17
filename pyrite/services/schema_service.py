"""Schema provisioning service — programmatic management of kb.yaml type
schemas, and the schema migration of a KB's entry files."""

from typing import Any

from pyrite.config import KBConfig, PyriteConfig
from pyrite.exceptions import KBNotFoundError, ValidationError
from pyrite.utils.yaml import dump_yaml_file, load_yaml_file


def migration_operations(before: dict[str, Any], after: dict[str, Any]) -> list[Any]:
    """The operations that take the frontmatter values ``before`` to
    ``after``: a ``Set`` per key added or changed (in ``after``'s order), an
    ``Unset`` per key dropped. A key whose value is equal is not named, so
    its bytes stay."""
    from pyrite.storage.file_operations import Set, Unset

    ops: list[Any] = []
    for key, value in after.items():
        if key not in before or before[key] != value:
            ops.append(Set(key, value))
    for key in before:
        if key not in after:
            ops.append(Unset(key))
    return ops


class SchemaService:
    """Manage kb.yaml schemas programmatically.

    Provides show/add/remove/set operations for type definitions
    in a KB's kb.yaml file.
    """

    def __init__(self, config: PyriteConfig):
        self.config = config

    def _get_kb(self, kb_name: str) -> KBConfig:
        """Get KB config or raise."""
        kb = self.config.get_kb(kb_name)
        if not kb:
            raise KBNotFoundError(f"KB '{kb_name}' not found")
        return kb

    def _require_kb_directory(self, kb: KBConfig) -> None:
        """Refuse schema writes when the registered KB directory is gone."""
        if not kb.path.is_dir():
            raise KBNotFoundError(
                f"KB '{kb.name}' directory is missing; re-register or remove the KB"
            )

    def _load_kb_yaml(self, kb: KBConfig) -> dict[str, Any]:
        """Load kb.yaml, returning empty dict if missing."""
        if kb.kb_yaml_path.exists():
            return load_yaml_file(kb.kb_yaml_path)
        return {}

    def _save_kb_yaml(self, kb: KBConfig, data: dict[str, Any]) -> None:
        """Save kb.yaml and refresh in-memory config."""
        dump_yaml_file(data, kb.kb_yaml_path)
        kb.load_kb_yaml()

    def show_schema(self, kb_name: str) -> dict[str, Any]:
        """Show the current schema for a KB."""
        kb = self._get_kb(kb_name)
        data = self._load_kb_yaml(kb)
        return {
            "kb_name": kb_name,
            "types": data.get("types", {}),
            "policies": data.get("policies", {}),
            "validation": data.get("validation", {}),
        }

    def add_type(self, kb_name: str, type_name: str, type_def: dict[str, Any]) -> dict[str, Any]:
        """Add a type definition to kb.yaml.

        Args:
            kb_name: Knowledge base name.
            type_name: Type name to add.
            type_def: Type definition with description, required, optional, subdirectory.

        Returns:
            Result dict with added status.
        """
        kb = self._get_kb(kb_name)
        self._require_kb_directory(kb)
        data = self._load_kb_yaml(kb)

        types = data.get("types", {})
        if type_name in types:
            return {"error": f"Type '{type_name}' already exists in KB '{kb_name}'"}

        types[type_name] = type_def
        data["types"] = types
        self._save_kb_yaml(kb, data)

        return {"added": True, "type_name": type_name, "kb_name": kb_name}

    def remove_type(self, kb_name: str, type_name: str) -> dict[str, Any]:
        """Remove a type definition from kb.yaml.

        Args:
            kb_name: Knowledge base name.
            type_name: Type name to remove.

        Returns:
            Result dict with removed status.
        """
        kb = self._get_kb(kb_name)
        self._require_kb_directory(kb)
        data = self._load_kb_yaml(kb)

        types = data.get("types", {})
        if type_name not in types:
            return {"error": f"Type '{type_name}' not found in KB '{kb_name}'"}

        del types[type_name]
        data["types"] = types
        self._save_kb_yaml(kb, data)

        return {"removed": True, "type_name": type_name, "kb_name": kb_name}

    def set_schema(self, kb_name: str, schema: dict[str, Any]) -> dict[str, Any]:
        """Replace the schema sections (types, policies, validation) in kb.yaml.

        Preserves name, description, and other top-level keys.

        Args:
            kb_name: Knowledge base name.
            schema: Dict with types, policies, validation keys.

        Returns:
            Result dict with set status.
        """
        kb = self._get_kb(kb_name)
        self._require_kb_directory(kb)
        data = self._load_kb_yaml(kb)

        if "types" in schema:
            data["types"] = schema["types"]
        if "policies" in schema:
            data["policies"] = schema["policies"]
        if "validation" in schema:
            data["validation"] = schema["validation"]

        self._save_kb_yaml(kb, data)

        type_count = len(data.get("types", {}))
        return {"set": True, "kb_name": kb_name, "type_count": type_count}

    def migrate_entries(
        self, kb_name: str, *, entry_type: str | None = None, dry_run: bool = False
    ) -> dict[str, Any]:
        """Migrate the KB's entry files to their type's schema version.

        Each entry whose ``_schema_version`` is behind its type's gets the
        registered migration chain applied to its frontmatter values, and
        the difference (a ``Set`` per key the chain added or changed, an
        ``Unset`` per key it dropped, ``_schema_version`` set to the type's
        version) is written onto the file's own bytes with ``apply`` and
        published with compare-and-replace (#955, B6 P4). Every other byte
        stays as written. ``dry_run`` counts and writes nothing.

        Returns ``checked`` (files read and judged), ``migrated`` (files
        behind, written unless ``dry_run``) and ``errors`` (``path`` and
        ``error`` per file that could not be read, judged or written; such
        a file is neither checked nor migrated).
        """
        from pyrite.storage.entry_writer import publish_entry_text
        from pyrite.storage.file_operations import FrontmatterSpan, apply
        from pyrite.storage.repository import KBRepository
        from pyrite.utils.frontmatter import Frontmatter, describe, split_frontmatter
        from pyrite.utils.yaml import load_yaml

        kb = self._get_kb(kb_name)
        repo = KBRepository(kb)
        checked = 0
        migrated = 0
        errors: list[dict[str, str]] = []

        for file_path in repo.list_files():
            try:
                raw = file_path.read_bytes()
                text = raw.decode("utf-8")
                split = split_frontmatter(text)
                if not isinstance(split, Frontmatter):
                    raise ValidationError(f"no frontmatter ({describe(split)})")
                values = load_yaml(split.text)
                if not isinstance(values, dict):
                    raise ValidationError("the frontmatter is not a mapping")
                if entry_type and values.get("type", "") != entry_type:
                    continue

                type_schema = kb.kb_schema.get_type_schema(values.get("type", ""))
                target = type_schema.version if type_schema else 0
                needs_save = target > 0 and int(values.get("_schema_version", 0)) != target

                if needs_save and not dry_run:
                    # `_maybe_migrate` is the load-time chain (the registry's
                    # functions on a copy of the values); the type's version
                    # is stamped even when no function is registered for it.
                    after = repo._maybe_migrate(dict(values))
                    after["_schema_version"] = target
                    ops = migration_operations(values, after)
                    span = FrontmatterSpan(split.yaml_start, split.close_start, split.close_end)
                    new_text, _report = apply(text, ops, span)
                    publish_entry_text(file_path, new_text, expect=raw)
                # Counted after the write: a file that failed to write was
                # neither checked nor migrated.
                checked += 1
                if needs_save:
                    migrated += 1
            except Exception as e:
                errors.append({"path": str(file_path), "error": str(e)})

        return {
            "kb_name": kb_name,
            "dry_run": dry_run,
            "checked": checked,
            "migrated": migrated,
            "errors": errors,
        }
