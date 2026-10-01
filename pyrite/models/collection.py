"""Collection entry type — folder-backed collections (Phase 1)."""

from dataclasses import dataclass, field
from typing import Any

from ..schema import generate_entry_id
from .base import Entry, _as_list, parse_datetime, parse_links, parse_sources


@dataclass
class CollectionEntry(Entry):
    """A collection that groups entries, backed by a filesystem folder."""

    source_type: str = "folder"  # "folder" (Phase 1); "query" (Phase 2)
    query: str = ""  # Inline query string for virtual collections (source_type="query")
    description: str = ""
    icon: str = ""
    view_config: dict = field(default_factory=lambda: {"default_view": "list"})
    entry_filter: dict = field(default_factory=dict)
    folder_path: str = ""  # Relative path within KB (for folder collections)
    collection_type: str = "generic"  # Plugin-defined collection type

    @property
    def entry_type(self) -> str:
        return "collection"

    def to_frontmatter(self) -> dict[str, Any]:
        meta = self._base_frontmatter()
        meta["source_type"] = self.source_type
        if self.source_type == "query" and self.query:
            meta["query"] = self.query
        if self.description:
            meta["description"] = self.description
        if self.icon:
            meta["icon"] = self.icon
        meta["view_config"] = self.view_config
        if self.entry_filter:
            meta["entry_filter"] = self.entry_filter
        meta["folder_path"] = self.folder_path
        if self.collection_type and self.collection_type != "generic":
            meta["collection_type"] = self.collection_type
        if self.summary:
            meta["summary"] = self.summary
        return meta

    @classmethod
    def from_frontmatter(cls, meta: dict[str, Any], body: str) -> "CollectionEntry":
        kw = cls._base_kwargs(meta, body)
        kw["source_type"] = meta.get("source_type", "folder")
        kw["query"] = meta.get("query", "")
        kw["description"] = meta.get("description", "")
        kw["icon"] = meta.get("icon", "")
        kw["view_config"] = meta.get("view_config", {"default_view": "list"}) or {
            "default_view": "list"
        }
        kw["entry_filter"] = meta.get("entry_filter", {}) or {}
        kw["folder_path"] = meta.get("folder_path", "")
        kw["collection_type"] = meta.get("collection_type", "generic")
        return cls(**kw)

    @classmethod
    def from_collection_yaml(cls, yaml_data: dict[str, Any], folder_path: str) -> "CollectionEntry":
        """Create a CollectionEntry from a parsed __collection.yaml file."""
        title = yaml_data.get("title", "")
        entry_id = yaml_data.get("id", "")
        if not entry_id:
            # Generate ID from folder name
            folder_name = folder_path.rstrip("/").split("/")[-1] if folder_path else ""
            entry_id = f"collection-{folder_name}" if folder_name else generate_entry_id(title)

        return cls(
            id=entry_id,
            title=title,
            body=yaml_data.get("body", ""),
            summary=yaml_data.get("summary", ""),
            source_type=yaml_data.get("source_type", "folder"),
            query=yaml_data.get("query", ""),
            description=yaml_data.get("description", ""),
            icon=yaml_data.get("icon", ""),
            view_config=yaml_data.get("view_config", {"default_view": "list"})
            or {"default_view": "list"},
            entry_filter=yaml_data.get("entry_filter", {}) or {},
            folder_path=folder_path,
            collection_type=yaml_data.get("collection_type", "generic"),
            tags=_as_list(yaml_data.get("tags")),
            aliases=_as_list(yaml_data.get("aliases")),
            sources=parse_sources(yaml_data.get("sources")),
            links=parse_links(yaml_data.get("links")),
            metadata=yaml_data.get("metadata", {}),
            created_at=parse_datetime(yaml_data.get("created_at")),
            updated_at=parse_datetime(yaml_data.get("updated_at")),
        )
