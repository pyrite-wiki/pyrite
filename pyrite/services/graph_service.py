"""
Graph Service

Graph and link operations for knowledge base entries.
"""

from typing import Any

from ..storage.database import PyriteDB


class GraphService:
    """Service for graph and link operations."""

    def __init__(self, db: PyriteDB):
        self.db = db

    def get_graph(
        self,
        center: str | None = None,
        center_kb: str | None = None,
        kb_name: str | None = None,
        entry_type: str | None = None,
        depth: int = 2,
        limit: int = 500,
        *,
        readable_kbs: set[str] | None,
    ) -> dict[str, Any]:
        """Get graph data for visualization, bounded by the caller's readable set."""
        return self.db.get_graph_data(
            center=center,
            center_kb=center_kb,
            kb_name=kb_name,
            entry_type=entry_type,
            depth=depth,
            limit=limit,
            readable_kbs=readable_kbs,
        )

    def get_refs_to(self, entry_id: str, kb_name: str) -> list[dict[str, Any]]:
        """Get entries that reference this entry via object-ref fields."""
        return self.db.get_refs_to(entry_id, kb_name)

    def get_refs_from(self, entry_id: str, kb_name: str) -> list[dict[str, Any]]:
        """Get entries this entry references via object-ref fields."""
        return self.db.get_refs_from(entry_id, kb_name)

    def get_backlinks(
        self,
        entry_id: str,
        kb_name: str,
        limit: int = 0,
        offset: int = 0,
        *,
        readable_kbs: set[str] | None,
    ) -> list[dict[str, Any]]:
        """Get entries that link TO this entry, from KBs the caller can read."""
        return self.db.get_backlinks(
            entry_id, kb_name, limit=limit, offset=offset, readable_kbs=readable_kbs
        )

    def get_outlinks(
        self, entry_id: str, kb_name: str, *, readable_kbs: set[str] | None
    ) -> list[dict[str, Any]]:
        """Get entries that this entry links TO; an unreadable target reads as missing."""
        return self.db.get_outlinks(entry_id, kb_name, readable_kbs=readable_kbs)

    def get_edge_endpoints(self, entry_id: str, kb_name: str) -> list[dict[str, Any]]:
        """Get endpoints of an edge-type entry."""
        return self.db.get_edge_endpoints(entry_id, kb_name)

    def get_edges_by_endpoint(self, endpoint_id: str, kb_name: str) -> list[dict[str, Any]]:
        """Get edge entries where this entity is an endpoint."""
        return self.db.get_edges_by_endpoint(endpoint_id, kb_name)

    def get_edges_between(self, id_a: str, id_b: str, kb_name: str) -> list[dict[str, Any]]:
        """Get edge entries connecting two entities."""
        return self.db.get_edges_between(id_a, id_b, kb_name)

    def get_merged_backlinks(
        self,
        entry_id: str,
        kb_name: str,
        limit: int = 0,
        offset: int = 0,
        *,
        readable_kbs: set[str] | None,
    ) -> list[dict[str, Any]]:
        """Get unified backlinks: link-derived + edge-derived, labeled by source type.

        Link-derived backlinks are bounded by ``readable_kbs``; the
        edge-derived ones (`get_edges_by_endpoint`) are not scoped yet, and
        nothing on a server surface calls this method.

        Each result has a 'source_type' field: 'link' or 'edge'.
        Edge results include 'edge_type' and 'role' fields.
        """
        # Get link-derived backlinks
        link_backlinks = self.db.get_backlinks(
            entry_id, kb_name, limit=0, offset=0, readable_kbs=readable_kbs
        )
        for bl in link_backlinks:
            bl["source_type"] = "link"

        # Get edge-derived backlinks
        edge_entries = self.db.get_edges_by_endpoint(entry_id, kb_name)
        edge_backlinks = []
        seen_edge_ids: set[str] = set()
        for edge in edge_entries:
            edge_id = edge.get("id", "")
            if edge_id in seen_edge_ids:
                continue
            seen_edge_ids.add(edge_id)
            edge_backlinks.append(
                {
                    "id": edge_id,
                    "kb_name": edge.get("kb_name", kb_name),
                    "title": edge.get("title", ""),
                    "entry_type": edge.get("entry_type", ""),
                    "relation": f"edge:{edge.get('edge_type', '')}.{edge.get('role', '')}",
                    "source_type": "edge",
                    "edge_type": edge.get("edge_type", ""),
                    "role": edge.get("role", ""),
                }
            )

        # Merge and deduplicate by (id, kb_name) — prefer link-derived
        all_backlinks = link_backlinks + edge_backlinks
        seen: set[tuple[str, str]] = set()
        deduped = []
        for bl in all_backlinks:
            key = (bl.get("id", ""), bl.get("kb_name", ""))
            if key not in seen:
                seen.add(key)
                deduped.append(bl)

        # Apply limit/offset
        if offset > 0:
            deduped = deduped[offset:]
        if limit > 0:
            deduped = deduped[:limit]

        return deduped
