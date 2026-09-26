- Reads that follow links between knowledge bases now stay within the KBs the
  caller can read: backlinks, outlinks, the graph and its link counts, QA link
  checks, and entry lookups made without naming a KB, on both the REST API
  and MCP, including plugin tools. A link to an entry the caller cannot read
  is shown as a link to a missing entry, and an entry in such a KB is reported
  as not found. Callers without KB-level restrictions see no change.
