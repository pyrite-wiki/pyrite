---
id: graph-betweenness-centrality
title: "Graph Betweenness Centrality Sizing"
type: backlog_item
tags: [web, graph, visualization]
kind: enhancement
status: retired
priority: low
effort: M
---

Size graph nodes by betweenness centrality instead of (or in addition to) link count. BC highlights entries that *bridge* different topic areas — conceptual connectors — rather than just the most-linked entries.

- Compute BC on the graph subgraph returned by the API (client-side via Cytoscape's `betweennessCentrality()`)
- Add a "Size by" toggle in GraphControls: Link Count vs. Betweenness Centrality
- BC-sized nodes surface "bridging" entries that connect otherwise separate clusters

Inspired by InfraNodus (Paranyushkin, WWW'19) which uses BC as the primary node ranking metric for text network analysis.
