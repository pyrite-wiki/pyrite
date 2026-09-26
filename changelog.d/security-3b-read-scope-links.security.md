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
- For extension authors: the link, entry-lookup, graph, QA, wikilink,
  collection-query and task-lookup methods in `pyrite.services` and
  `pyrite.storage` now take a required `readable_kbs` keyword. Pass the
  caller's readable set, or `pyrite.services.access_policy.UNSCOPED` when
  there is no caller to scope for.
