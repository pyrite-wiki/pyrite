---
id: graph-service
type: component
title: "Graph Service"
kind: service
path: "pyrite/services/graph_service.py"
owner: "core"
dependencies: ["pyrite.storage"]
tags: [core, services, graph]
---

`GraphService` handles graph and link operations for knowledge base entries. Extracted from `KBService` in 0.18 to reduce god-class complexity.

## Methods

| Method | Description |
|--------|-------------|
| `get_graph()` | Get graph data for visualization (delegates to `db.get_graph_data`) |
| `get_refs_to()` | Get entries referencing a given entry via object-ref fields (any spelling of the value: `x`, `[[x]]`, `other:x`; #1693) |
| `get_refs_from()` | Get entries a given entry references via object-ref fields |
| `get_backlinks()` | Get entries that link TO a given entry (with pagination); rows carry `relation`, `forward_relation`, `inverse_relation` (see docs/json-contracts.md) |
| `get_outlinks()` | Get entries a given entry links TO |

## Architecture

All methods are thin wrappers around `PyriteDB` query methods. Constructor takes only `db: PyriteDB`.

`KBService` retains facade delegator methods with identical signatures, so all consumer callsites (endpoints, CLI, MCP) remain unchanged.

## Related

- [[kb-service]] — facade delegator for graph methods
- [[storage-layer]] — underlying graph queries
