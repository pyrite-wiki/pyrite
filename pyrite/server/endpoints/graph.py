"""Graph visualization endpoint."""

from collections import deque
from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from ...services.access_policy import KB, Action, ReadScope
from ...services.graph_service import GraphService
from ..api import get_graph_service, limiter
from ..authz import authorize
from ..schemas import GraphEdge, GraphNode, GraphResponse

router = APIRouter(tags=["Graph"])


def compute_betweenness_centrality(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> dict[tuple[str, str], float]:
    """Compute betweenness centrality using Brandes' algorithm with BFS.

    Treats the graph as undirected. Returns a dict mapping (id, kb_name) to
    a normalized centrality score in [0.0, 1.0].
    """
    # Build node keys and adjacency list
    node_keys = [(n["id"], n["kb_name"]) for n in nodes]
    node_set = set(node_keys)
    n = len(node_keys)

    if n < 3:
        return dict.fromkeys(node_keys, 0.0)

    adj: dict[tuple[str, str], list[tuple[str, str]]] = {k: [] for k in node_keys}
    for e in edges:
        src = (e["source_id"], e["source_kb"])
        tgt = (e["target_id"], e["target_kb"])
        if src in node_set and tgt in node_set:
            adj[src].append(tgt)
            adj[tgt].append(src)

    centrality: dict[tuple[str, str], float] = dict.fromkeys(node_keys, 0.0)

    # Brandes' algorithm: BFS from each source
    for s in node_keys:
        # BFS
        stack: list[tuple[str, str]] = []
        pred: dict[tuple[str, str], list[tuple[str, str]]] = {k: [] for k in node_keys}
        sigma: dict[tuple[str, str], int] = dict.fromkeys(node_keys, 0)
        sigma[s] = 1
        dist: dict[tuple[str, str], int] = dict.fromkeys(node_keys, -1)
        dist[s] = 0

        queue: deque[tuple[str, str]] = deque([s])
        while queue:
            v = queue.popleft()
            stack.append(v)
            for w in adj[v]:
                # w found for the first time?
                if dist[w] < 0:
                    dist[w] = dist[v] + 1
                    queue.append(w)
                # shortest path to w via v?
                if dist[w] == dist[v] + 1:
                    sigma[w] += sigma[v]
                    pred[w].append(v)

        # Accumulation
        delta: dict[tuple[str, str], float] = dict.fromkeys(node_keys, 0.0)
        while stack:
            w = stack.pop()
            for v in pred[w]:
                delta[v] += (sigma[v] / sigma[w]) * (1.0 + delta[w])
            if w != s:
                centrality[w] += delta[w]

    # Normalize: undirected graph — each pair (s,t) contributes from both
    # s-BFS and t-BFS, so normalization is (n-1)*(n-2) not divided by 2.
    norm = float((n - 1) * (n - 2))
    if norm > 0:
        for k in centrality:
            centrality[k] /= norm

    return centrality


@router.get("/graph", response_model=GraphResponse)
@limiter.limit("60/minute")
def get_graph(
    request: Request,
    center: str | None = Query(None, description="Center entry ID for BFS"),
    center_kb: str | None = Query(None, description="KB of center entry"),
    kb: str | None = Query(None, description="Filter to KB"),
    entry_type: str | None = Query(None, alias="type", description="Filter by entry type"),
    depth: int = Query(2, ge=1, le=3, description="Max hops from center"),
    limit: int = Query(500, ge=1, le=2000, description="Max nodes"),
    include_centrality: bool = Query(False, description="Compute betweenness centrality"),
    graph_svc: GraphService = Depends(get_graph_service),
    scope: ReadScope = Depends(authorize(Action.KB_READ, KB)),
):
    """Get graph data for knowledge graph visualization.

    `center_kb` is authorized like `kb` (it is in `KB_PARAM_NAMES`), so a
    centre in a KB the caller cannot read answers `KB_NOT_FOUND`, the same
    as a KB that does not exist. The walk itself is bounded by `scope` in
    the query, so nodes, edges and each `link_count` are computed over
    readable KBs only (P-R4, P-R5).
    """
    data = graph_svc.get_graph(
        center=center,
        center_kb=center_kb,
        kb_name=kb,
        entry_type=entry_type,
        depth=depth,
        limit=limit,
        readable_kbs=scope.as_set(),
    )

    if include_centrality:
        bc = compute_betweenness_centrality(data["nodes"], data["edges"])
        for n in data["nodes"]:
            n["centrality"] = bc.get((n["id"], n["kb_name"]), 0.0)

    nodes = [GraphNode(**n) for n in data["nodes"]]
    edges = [GraphEdge(**e) for e in data["edges"]]
    return GraphResponse(nodes=nodes, edges=edges)
