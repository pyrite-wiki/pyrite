- Reads that follow links between knowledge bases now stay within the KBs the
  caller can read, on the REST API and MCP, plugin tools included:
  - backlinks and outlinks;
  - the graph and its link counts;
  - QA validation, assessment and status;
  - link-discovery exclusions;
  - entry and task lookups made without naming a KB.
  A link to an entry the caller cannot read is shown as a link to a missing
  entry, and an entry or task in such a KB is reported as not found. Callers
  without KB-level restrictions see no change.
- An empty or whitespace-only KB name is treated as naming no knowledge base,
  everywhere. A request that sends one is answered within the caller's own
  readable KBs, including the AI summarize, auto-tag and suggest-links
  requests, a collection's metadata, and an entry's blocks.
- For extension authors, a breaking change: the storage and service reads
  that can cross knowledge bases now require a `readable_kbs` keyword and
  raise `TypeError` without it. These include `PyriteDB.get_backlinks`,
  `get_outlinks`, `get_graph_data`, `get_orphans` and `get_related`, and
  `KBService.get_entry`, plus the graph, QA, wikilink, collection-query,
  link-discovery and task-lookup methods. Pass the caller's readable set.
  Pass `pyrite.services.access_policy.UNSCOPED` only from code that has no
  caller identity at all, such as a command-line tool; never from a request
  or tool handler.
