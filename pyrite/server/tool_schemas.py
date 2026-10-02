"""
MCP tool schema definitions for Pyrite.

Static description and inputSchema data for all MCP tools, separated from
handler logic in mcp_server.py. Each dict maps tool_name -> {description, inputSchema}.

Descriptions that quote a body bound write it as a placeholder --
``{body_chunk_default}``, ``{body_chunk_max}``, ``{body_response_budget}`` --
rather than a literal. ADR-0034 rule 4 makes those three numbers
configuration, and the tool descriptions must report the *effective* values,
not the compiled-in ones: an agent tuning its calls to "max 50000" on a
deployment whose ceiling is 20,000 has been misled by its own tool schema.
:func:`render_tool_schemas` substitutes them at server start.
"""

import copy
from typing import Any


def render_tool_schemas(schemas: dict[str, Any], **values: int) -> dict[str, Any]:
    """Return a deep copy of ``schemas`` with description placeholders filled.

    Only ``description`` strings are formatted; every other value is copied
    verbatim, so a JSON-schema keyword containing braces cannot be mangled.
    A description naming a placeholder this call was not given is left as-is
    rather than raising, so adding a schema never breaks server start.
    """

    def render(node: Any, key: str | None = None) -> Any:
        if isinstance(node, dict):
            return {k: render(v, k) for k, v in node.items()}
        if isinstance(node, list):
            return [render(v) for v in node]
        if key == "description" and isinstance(node, str) and "{" in node:
            try:
                return node.format(**values)
            except (KeyError, IndexError, ValueError):
                return node
        return copy.deepcopy(node)

    return render(schemas)


READ_TOOLS = {
    "kb_list": {
        "description": "List all mounted knowledge bases with their types and entry counts",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    "kb_search": {
        "description": 'Full-text search across knowledge bases. Supports FTS5 query syntax (AND, OR, NOT, phrases in quotes). Returns entries with snippets ranked by relevance. Filters (entry_type, tags, date_from/date_to, fips, state, status) apply in every mode, on every search leg -- keyword, semantic and hybrid alike -- so a filtered result never contains an entry the filter excluded. When a leg cannot honour a filter it is dropped rather than returning unfiltered rows, and the response carries a "warnings" array naming it; no "warnings" key means every filter was applied on every leg (the key is absent, never null -- test for its presence). The full contract is stated once, in the docstring of SearchService.search: "what a search response owes its caller". Auto-quote rule: special-char tokens (hyphens, dots, colons) are auto-quoted ONLY when the query has no AND/OR/NOT operator and no existing quote. Once you use an operator or a phrase quote, quote special-char tokens yourself (e.g. \'"family separation" "cross-link"\') or the query can fail with error_code QUERY_SYNTAX (deterministic, not retryable).',
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query (FTS5 syntax supported)",
                },
                "kb_name": {
                    "type": "string",
                    "description": "Limit search to specific KB (optional)",
                },
                "entry_type": {
                    "type": "string",
                    "description": "Filter by entry type: note, person, organization, event, document, topic, etc.",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Filter by tags (entries must have ALL specified tags)",
                },
                "date_from": {
                    "type": "string",
                    "description": "Start date (YYYY-MM-DD)",
                },
                "date_to": {
                    "type": "string",
                    "description": "End date (YYYY-MM-DD)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum results to return (default 20)",
                },
                "offset": {
                    "type": "integer",
                    "description": "Pagination offset (default 0)",
                },
                "mode": {
                    "type": "string",
                    "enum": ["keyword", "semantic", "hybrid"],
                    "description": "Search mode: keyword (FTS5), semantic (vector), or hybrid. Default: hybrid",
                },
                "expand": {
                    "type": "boolean",
                    "description": "Use AI query expansion for additional search terms. Default: false",
                },
                "fips": {
                    "type": "string",
                    "description": "Filter by county FIPS code (5-digit, e.g. '12086' for Miami-Dade). Works across all KBs.",
                },
                "state": {
                    "type": "string",
                    "description": "Filter by US state abbreviation (e.g. 'FL', 'TX'). Works across all KBs.",
                },
                "status": {
                    "type": "string",
                    "description": "Filter by the indexed 'status' frontmatter field (e.g. 'unprocessed', 'draft', 'done'). Works across all KBs and entry types. Other metadata fields (e.g. 'readiness') are NOT reachable via this parameter.",
                },
                "include_body": {
                    "type": "boolean",
                    "description": "Include body text in results, bounded to {body_chunk_default} chars per body within a {body_response_budget}-char response budget. Default: false (returns snippet instead, saving tokens). Truncated bodies carry body_truncated; continue them with kb_read_body.",
                },
                "fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Return only these fields per result (e.g. ['id','title','tags']). Omit for default fields. Overrides include_body; asking for 'body' returns the same bounded body, with its truncation markers. `id` and `kb_name` are always included.",
                },
            },
            "required": ["query"],
        },
    },
    "kb_get": {
        "description": "Get a specific entry by its ID. Returns full content including body, metadata, sources, and links. Bodies over {body_chunk_default} chars are auto-truncated; use body_offset/body_limit to paginate, or kb_read_body for continuation. A truncated body always carries body_truncated, body_length, body_offset and body_chunk_size.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entry_id": {
                    "type": "string",
                    "description": "The entry ID (e.g., '2025-01-20--event-slug' or 'alice-smith')",
                },
                "kb_name": {
                    "type": "string",
                    "description": "KB name (optional - searches all KBs if not provided)",
                },
                "fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Return only these fields (e.g. ['id','title','body']). Omit for all fields. Body chunking still applies, and a projection that keeps `body` also keeps the truncation markers. `id` and `kb_name` are always included.",
                },
                "body_offset": {
                    "type": "integer",
                    "description": "Start position in body text (default 0). Use to paginate large bodies.",
                },
                "body_limit": {
                    "type": "integer",
                    "description": "Max body chars to return (default {body_chunk_default}, max {body_chunk_max}). Values above the max are clamped to it.",
                },
            },
            "required": ["entry_id"],
        },
    },
    "kb_timeline": {
        "description": "Get timeline events within a date range, optionally filtered by importance.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "date_from": {
                    "type": "string",
                    "description": "Start date (YYYY-MM-DD)",
                },
                "date_to": {"type": "string", "description": "End date (YYYY-MM-DD)"},
                "min_importance": {
                    "type": "integer",
                    "description": "Minimum importance score (1-10)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum results (default 50)",
                },
                "offset": {
                    "type": "integer",
                    "description": "Pagination offset (default 0)",
                },
            },
            "required": [],
        },
    },
    "kb_backlinks": {
        "description": "Get all entries that link TO a given entry (reverse link lookup).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entry_id": {
                    "type": "string",
                    "description": "Entry ID to find backlinks for",
                },
                "kb_name": {"type": "string", "description": "KB name"},
                "limit": {
                    "type": "integer",
                    "description": "Maximum results (default 100)",
                },
                "offset": {
                    "type": "integer",
                    "description": "Pagination offset (default 0)",
                },
            },
            "required": ["entry_id", "kb_name"],
        },
    },
    "kb_tags": {
        "description": "Get all tags with their usage counts, optionally filtered by KB. Supports hierarchical /-separated tags.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kb_name": {
                    "type": "string",
                    "description": "Filter to specific KB (optional)",
                },
                "prefix": {
                    "type": "string",
                    "description": "Filter tags starting with prefix",
                },
                "tree": {
                    "type": "boolean",
                    "description": "Return hierarchical tree instead of flat list",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum tags to return (default 100)",
                },
                "offset": {
                    "type": "integer",
                    "description": "Pagination offset (default 0)",
                },
            },
            "required": [],
        },
    },
    "kb_stats": {
        "description": "Get index statistics: entry counts, tag counts, link counts per KB.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    "kb_schema": {
        "description": "Get the schema for a knowledge base. Returns available entry types, required/optional fields, validation rules, and relationship types. Essential for agents creating entries.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kb_name": {
                    "type": "string",
                    "description": "KB name to get schema for",
                },
            },
            "required": ["kb_name"],
        },
    },
    "kb_orient": {
        "description": "Use this first. With no kb_name it lists the KBs you can read. With kb_name it returns a one-shot summary of that KB: description, entry counts by type, top tags, recent changes, and the schema of the types the KB accepts. Pass detail='brief' for a read session: it omits the write-side schema blocks (ai_instructions, evaluation_rubric, guidelines, goals), which kb_schema returns when you need to write. brief keeps relationship_types, for following links.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kb_name": {
                    # Nullable: a client that fills every optional argument
                    # sends null for the one it has no value for.
                    "type": ["string", "null"],
                    "description": "KB to orient in. Omit (or null) to list the KBs you can read.",
                },
                "recent_limit": {
                    "type": "integer",
                    "description": "Number of recent entries to include (default 5)",
                },
                "detail": {
                    "type": ["string", "null"],
                    "enum": ["brief", "full", None],
                    "description": "full (default; null is the same): everything. brief: omits ai_instructions, evaluation_rubric, guidelines and goals.",
                },
            },
        },
    },
    "kb_qa_validate": {
        "description": "Validate KB structural integrity. Checks missing titles, empty bodies, broken links, orphans, invalid dates, importance range, and schema violations.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kb_name": {
                    "type": "string",
                    "description": "KB to validate (validates all if not provided)",
                },
                "entry_id": {
                    "type": "string",
                    "description": "Validate a single entry (requires kb_name)",
                },
                "severity": {
                    "type": "string",
                    "enum": ["error", "warning", "info"],
                    "description": "Minimum severity to include (default: warning)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum issues to return (default 50)",
                },
            },
            "required": [],
        },
    },
    "kb_batch_read": {
        "description": "Fetch multiple entries in one call. Faster than sequential kb_get when you need 2+ entries. Returns found entries and lists any IDs not found. Max 50 entries per call. Bodies over {body_chunk_default} chars are auto-truncated, and the whole response carries at most {body_response_budget} body chars: bodies are filled in request order, so entries past the budget come back with an empty body plus body_truncated and their true body_length — read them with kb_read_body.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entries": {
                    "type": "array",
                    "description": "List of entries to fetch (max 50)",
                    "maxItems": 50,
                    "items": {
                        "type": "object",
                        "properties": {
                            "entry_id": {"type": "string", "description": "Entry ID"},
                            "kb_name": {"type": "string", "description": "KB name"},
                        },
                        "required": ["entry_id", "kb_name"],
                    },
                },
                "fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Return only these fields per entry. Omit for all fields. Body chunking and the response budget still apply, and a projection that keeps `body` also keeps the truncation markers. `id` and `kb_name` are always included.",
                },
                "body_offset": {
                    "type": "integer",
                    "description": "Start position in body text (default 0). Applied to all entries.",
                },
                "body_limit": {
                    "type": "integer",
                    "description": "Max body chars to return per entry (default {body_chunk_default}, max {body_chunk_max}), within the {body_response_budget}-char response budget.",
                },
            },
            "required": ["entries"],
        },
    },
    "kb_read_body": {
        "description": "Read a chunk of an entry's body text. Lightweight continuation tool — returns body only, no entry metadata. Use after kb_get indicates body_truncated: true.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entry_id": {
                    "type": "string",
                    "description": "Entry ID to read body from",
                },
                "kb_name": {
                    "type": "string",
                    "description": "KB name (optional - searches all KBs if not provided)",
                },
                "body_offset": {
                    "type": "integer",
                    "description": "Start position in body text (default 0)",
                },
                "body_limit": {
                    "type": "integer",
                    "description": "Max chars to return (default {body_chunk_default}, max {body_chunk_max}). Values above the max are clamped to it.",
                },
            },
            "required": ["entry_id"],
        },
    },
    "kb_list_entries": {
        "description": "Browse entries in a KB with optional filters. Use for orientation — see what types exist, browse by tag, or paginate through entries. Lighter than kb_search when you don't need full-text matching.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kb_name": {
                    "type": "string",
                    "description": "KB to browse (optional — lists all KBs if omitted)",
                },
                "entry_type": {"type": "string", "description": "Filter by entry type"},
                "tag": {"type": "string", "description": "Filter by tag"},
                "sort_by": {
                    "type": "string",
                    "enum": ["title", "updated_at", "created_at", "entry_type"],
                    "description": "Sort column (default: updated_at)",
                },
                "sort_order": {
                    "type": "string",
                    "enum": ["asc", "desc"],
                    "description": "Sort direction (default: desc)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max entries to return (default 50, max 200)",
                },
                "offset": {"type": "integer", "description": "Pagination offset (default 0)"},
                "fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Return only these fields per entry. Omit for all fields. `id` and `kb_name` are always included.",
                },
            },
            "required": [],
        },
    },
    "kb_recent": {
        "description": "Get recently changed entries. Use to see what's new or changed in a KB. Thin wrapper over list-entries sorted by updated_at desc.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kb_name": {"type": "string", "description": "KB to query (optional)"},
                "entry_type": {"type": "string", "description": "Filter by entry type"},
                "limit": {"type": "integer", "description": "Max entries to return (default 20)"},
                "since": {
                    "type": "string",
                    "description": "Only entries updated after this ISO datetime (e.g. 2025-01-01T00:00:00)",
                },
                "fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Return only these fields per entry. Omit for all fields. `id` and `kb_name` are always included.",
                },
            },
            "required": [],
        },
    },
    "kb_qa_status": {
        "description": "Get QA status dashboard with issue counts by severity and rule.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kb_name": {
                    "type": "string",
                    "description": "KB to check (checks all if not provided)",
                },
            },
            "required": [],
        },
    },
    "kb_find_by_assignee": {
        "description": "Find entries assigned to a specific agent or user, across all entry types and KBs. Uses the Assignable protocol (ADR-0017).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "assignee": {
                    "type": "string",
                    "description": "Assignee identifier (e.g. 'agent:researcher', 'alice')",
                },
                "kb_name": {
                    "type": "string",
                    "description": "Limit to specific KB (optional)",
                },
                "status": {
                    "type": "string",
                    "description": "Filter by status (optional)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum results (default 50)",
                },
                "offset": {
                    "type": "integer",
                    "description": "Pagination offset (default 0)",
                },
            },
            "required": ["assignee"],
        },
    },
    "kb_find_overdue": {
        "description": "Find entries with overdue due_date (before a given date, default today). Only returns entries not yet done or failed. Uses the Temporal protocol (ADR-0017).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "as_of": {
                    "type": "string",
                    "description": "Reference date in YYYY-MM-DD format (default: today)",
                },
                "kb_name": {
                    "type": "string",
                    "description": "Limit to specific KB (optional)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum results (default 50)",
                },
                "offset": {
                    "type": "integer",
                    "description": "Pagination offset (default 0)",
                },
            },
            "required": [],
        },
    },
    "kb_find_by_status": {
        "description": "Find entries by status across all entry types and KBs. Uses the Statusable protocol (ADR-0017).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Status value to match (e.g. 'open', 'in_progress', 'done', 'draft')",
                },
                "kb_name": {
                    "type": "string",
                    "description": "Limit to specific KB (optional)",
                },
                "entry_type": {
                    "type": "string",
                    "description": "Filter by entry type (optional)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum results (default 50)",
                },
                "offset": {
                    "type": "integer",
                    "description": "Pagination offset (default 0)",
                },
            },
            "required": ["status"],
        },
    },
    "kb_find_by_location": {
        "description": "Find entries by location (substring match) across all entry types and KBs. Uses the Locatable protocol (ADR-0017).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "Location to search for (substring match)",
                },
                "kb_name": {
                    "type": "string",
                    "description": "Limit to specific KB (optional)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum results (default 50)",
                },
                "offset": {
                    "type": "integer",
                    "description": "Pagination offset (default 0)",
                },
            },
            "required": ["location"],
        },
    },
    "kb_index_job_status": {
        "description": "Check status of a background index job. Omit job_id to list active jobs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "string",
                    "description": "Job ID to check (optional - lists active jobs if not provided)",
                }
            },
            "required": [],
        },
    },
    "list_edge_types": {
        "description": "List available edge (relationship) types in a knowledge base. Edge types are entry types that represent typed relationships between entities (e.g. ownership, membership, funding). Returns schema information including endpoint fields and accepted entity types.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kb_name": {
                    "type": "string",
                    "description": "KB to query. If omitted, checks all KBs.",
                },
            },
            "required": [],
        },
    },
    "task_list": {
        "description": "List tasks, filter by status/assignee/parent. Use to find available work or check progress.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kb_name": {"type": "string", "description": "KB name (optional)"},
                "status": {
                    "type": "string",
                    "enum": [
                        "open",
                        "claimed",
                        "in_progress",
                        "blocked",
                        "review",
                        "done",
                        "failed",
                    ],
                    "description": "Filter by task status",
                },
                "assignee": {
                    "type": "string",
                    "description": "Filter by assignee (e.g. agent:claude-code-7a3f)",
                },
                "parent": {
                    "type": "string",
                    "description": "Filter by parent task ID",
                },
            },
            "required": [],
        },
    },
    "task_subtree": {
        "description": "Get all descendants (children, grandchildren, etc.) of a task.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Root task ID"},
                "kb_name": {"type": "string", "description": "KB name (optional)"},
            },
            "required": ["task_id"],
        },
    },
    "task_ancestors": {
        "description": "Get parent chain from task to root (immediate parent first).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task ID"},
                "kb_name": {"type": "string", "description": "KB name (optional)"},
            },
            "required": ["task_id"],
        },
    },
    "task_blocked_by": {
        "description": "Get all tasks transitively blocking a given task (full dependency chain).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task ID"},
                "kb_name": {"type": "string", "description": "KB name (optional)"},
            },
            "required": ["task_id"],
        },
    },
    "task_critical_path": {
        "description": "Find the longest chain of unresolved dependencies blocking a task. Useful for identifying what to unblock first.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task ID"},
                "kb_name": {"type": "string", "description": "KB name (optional)"},
            },
            "required": ["task_id"],
        },
    },
    "task_status": {
        "description": "Get task details including children, dependencies, and evidence links.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task entry ID"},
                "kb_name": {"type": "string", "description": "KB name (optional)"},
            },
            "required": ["task_id"],
        },
    },
    "kb_batch_suggest": {
        "description": "Batch-compare all entries between two KBs to find potential cross-KB links. For each entry in source_kb, finds similar entries in target_kb. Deduplicates bidirectional matches.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source_kb": {
                    "type": "string",
                    "description": "KB to find connections FROM",
                },
                "target_kb": {
                    "type": "string",
                    "description": "KB to find connections TO",
                },
                "limit_per_entry": {
                    "type": "integer",
                    "description": "Max matches per source entry (default 3)",
                },
                "mode": {
                    "type": "string",
                    "enum": ["keyword", "semantic", "hybrid"],
                    "description": "Search mode (default: keyword)",
                },
                "exclude_linked": {
                    "type": "boolean",
                    "description": "Exclude already-linked pairs (default: true)",
                },
            },
            "required": ["source_kb", "target_kb"],
        },
    },
    "kb_discover_neighbors": {
        "description": "Find related entries across all KBs, including the source KB, unless target_kb limits the search. Excludes the source entry and, by default, already-linked entries.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entry_id": {
                    "type": "string",
                    "description": "Source entry ID to find neighbors for",
                },
                "kb_name": {
                    "type": "string",
                    "description": "KB containing the source entry",
                },
                "target_kb": {
                    "type": "string",
                    "description": "Limit candidates to this KB (omit to search all KBs, including the source KB)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum results (default 10)",
                },
                "mode": {
                    "type": "string",
                    "enum": ["keyword", "semantic", "hybrid"],
                    "description": "Search mode (default: hybrid, falls back to keyword if no embeddings)",
                },
                "exclude_linked": {
                    "type": "boolean",
                    "description": "Exclude entries that already have a link to the source (default: true)",
                },
            },
            "required": ["entry_id", "kb_name"],
        },
    },
}

WRITE_TOOLS = {
    "kb_create": {
        "description": "Create a new entry in a knowledge base. Validates against kb.yaml schema and returns warnings for unknown vocabulary values. Use kb_schema first to discover valid types and fields. Refuses a body carrying body_truncated: true (a partial read from kb_get/kb_batch_read); assemble the whole body with kb_read_body first.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kb_name": {"type": "string", "description": "Target KB name"},
                "entry_type": {
                    "type": "string",
                    "description": "Entry type: note, person, organization, event, document, topic, relationship, timeline, or custom type from kb.yaml",
                },
                "title": {"type": "string", "description": "Entry title"},
                "body": {
                    "type": "string",
                    "description": "Entry body content (markdown)",
                },
                "date": {
                    "type": "string",
                    "description": "Date (YYYY-MM-DD) - required for events",
                },
                "importance": {
                    "type": "integer",
                    "description": "Importance score 1-10 (default 5)",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tags for categorization",
                },
                "participants": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Participants involved (for events)",
                },
                "role": {
                    "type": "string",
                    "description": "Role description (for person entries)",
                },
                "metadata": {
                    "type": "object",
                    "description": "Additional fields for custom types or extension fields",
                },
                "validate": {
                    "type": "boolean",
                    "description": "Run QA validation after save and return issues. Also runs automatically if KB has qa_on_write: true in kb.yaml.",
                },
                "allow_undeclared": {
                    "type": "boolean",
                    "description": "Allow an entry_type the KB's kb.yaml does not declare (refused otherwise, core types included). The entry will be flagged by index health.",
                },
            },
            "required": ["kb_name", "entry_type", "title"],
        },
    },
    "kb_bulk_create": {
        "description": "Create multiple entries in one batch. One call instead of many kb_create calls; each entry is indexed as it is saved, and embedding is batched after the loop. Each entry goes through the same checks as kb_create (declared type, existing id, kb.yaml schema and plugin validators). After parameter validation, entries succeed or fail independently: a refused entry (a missing or empty title, an undeclared type, an existing id, a schema violation) is reported per entry, with the same error_code kb_create gives, without rejecting valid siblings. The results list preserves input order. Invalid parameter types, an unavailable KB, or an invalid batch size still reject the call. Max 50 entries per call. Refuses any entry whose body is carrying body_truncated: true (a partial read from kb_get/kb_batch_read); assemble the whole body with kb_read_body first.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kb_name": {"type": "string", "description": "Target KB name"},
                "entries": {
                    "type": "array",
                    "description": "Array of entry specs (max 50)",
                    "maxItems": 50,
                    "items": {
                        "type": "object",
                        "properties": {
                            "entry_type": {
                                "type": "string",
                                "description": "Entry type: note, person, organization, event, etc.",
                            },
                            "title": {
                                "type": "string",
                                "description": "Entry title",
                            },
                            "body": {
                                "type": "string",
                                "description": "Entry body content (markdown)",
                            },
                            "date": {"type": "string", "description": "Date (YYYY-MM-DD)"},
                            "importance": {"type": "integer", "description": "Importance 1-10"},
                            "tags": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Tags for categorization",
                            },
                            "metadata": {
                                "type": "object",
                                "description": "Additional fields",
                            },
                        },
                    },
                },
                "allow_undeclared": {
                    "type": "boolean",
                    "description": "Allow entry types the KB's kb.yaml does not declare (each such item is refused otherwise).",
                },
            },
            "required": ["kb_name", "entries"],
        },
    },
    "kb_update": {
        "description": "Update an existing entry. Only provided fields are updated; a field the entry type does not declare is stored as a custom field, as the CLI and REST do. Keys a kb_get result carries that no update writes (id, file_path, timestamps, links, index columns, nulls) are not written and are listed in the result's `ignored`. When the request is a kb_get result sent back, a field still at the value kb_get returned is not written either, and is listed in the result's `unchanged`; a request that names fields on its own writes each one as sent. Runs schema validation and returns warnings for unknown select/multi-select values. Refuses a body carrying body_truncated: true (a partial read from kb_get/kb_batch_read); assemble the whole body with kb_read_body first.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entry_id": {"type": "string", "description": "Entry ID to update"},
                "kb_name": {"type": "string", "description": "KB containing the entry"},
                "title": {"type": "string", "description": "New title"},
                "body": {"type": "string", "description": "New body content (markdown)"},
                "importance": {"type": "integer", "description": "Importance score 1-10"},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Replacement tags (overwrites existing)",
                },
                "participants": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Participants involved (for events)",
                },
                "metadata": {
                    "type": "object",
                    "description": "Additional/extension fields to update",
                },
                "status": {
                    "type": "string",
                    "description": "Status field (e.g. draft, confirmed, done)",
                },
                "date": {"type": "string", "description": "Date (YYYY-MM-DD)"},
                "location": {"type": "string", "description": "Location/venue"},
                "summary": {"type": "string", "description": "Short summary or subtitle"},
                "validate": {
                    "type": "boolean",
                    "description": "Run QA validation after save and return issues. Also runs automatically if KB has qa_on_write: true in kb.yaml.",
                },
            },
            "required": ["entry_id", "kb_name"],
        },
    },
    "kb_delete": {
        "description": "Delete an entry from a knowledge base.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entry_id": {"type": "string", "description": "Entry ID to delete"},
                "kb_name": {"type": "string", "description": "KB name"},
            },
            "required": ["entry_id", "kb_name"],
        },
    },
    "kb_link": {
        "description": "Create a link between two entries. Adds a typed relationship from source to target. Idempotent \u2014 linking the same pair twice has no effect.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source_id": {
                    "type": "string",
                    "description": "Source entry ID",
                },
                "source_kb": {
                    "type": "string",
                    "description": "KB containing the source entry",
                },
                "target_id": {
                    "type": "string",
                    "description": "Target entry ID",
                },
                "relation": {
                    "type": "string",
                    "description": "Relationship type (default: related_to)",
                },
                "target_kb": {
                    "type": "string",
                    "description": "KB containing the target entry (defaults to source_kb)",
                },
                "note": {
                    "type": "string",
                    "description": "Optional note about the link",
                },
                "allow_dangling": {
                    "type": "boolean",
                    "description": "If true, permit linking to a target that doesn't exist yet (forward reference). Default false.",
                    "default": False,
                },
            },
            "required": ["source_id", "source_kb", "target_id"],
        },
    },
    "task_create": {
        "description": "Create a new task with optional parent, priority, and assignee.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kb_name": {"type": "string", "description": "KB name"},
                "title": {"type": "string", "description": "Task title"},
                "parent": {"type": "string", "description": "Parent task entry ID"},
                "priority": {
                    "type": "integer",
                    "description": "Priority 1-10 (default 5)",
                    "minimum": 1,
                    "maximum": 10,
                },
                "assignee": {
                    "type": "string",
                    "description": "Assignee (e.g. agent:claude-code-7a3f)",
                },
                "body": {"type": "string", "description": "Task description"},
                "dependencies": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Dependency entry IDs",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tags for the new task",
                },
            },
            "required": ["kb_name", "title"],
        },
    },
    "task_update": {
        "description": "Update task fields (status, assignee, priority).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task entry ID"},
                "kb_name": {"type": "string", "description": "KB name"},
                "status": {
                    "type": "string",
                    "enum": [
                        "open",
                        "claimed",
                        "in_progress",
                        "blocked",
                        "review",
                        "done",
                        "failed",
                    ],
                    "description": "New status",
                },
                "assignee": {"type": "string", "description": "New assignee"},
                "priority": {
                    "type": "integer",
                    "description": "New priority 1-10",
                    "minimum": 1,
                    "maximum": 10,
                },
            },
            "required": ["task_id", "kb_name"],
        },
    },
    "task_claim": {
        "description": "Atomically claim an open task. Fails if task is not open.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task entry ID"},
                "kb_name": {"type": "string", "description": "KB name"},
                "assignee": {
                    "type": "string",
                    "description": "Assignee (e.g. agent:claude-code-7a3f)",
                },
            },
            "required": ["task_id", "kb_name", "assignee"],
        },
    },
    "task_decompose": {
        "description": "Decompose a parent task into child tasks.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "parent_id": {"type": "string", "description": "Parent task entry ID"},
                "kb_name": {"type": "string", "description": "KB name"},
                "children": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "body": {"type": "string"},
                            "priority": {"type": "integer", "minimum": 1, "maximum": 10},
                            "assignee": {"type": "string"},
                        },
                        "required": ["title"],
                    },
                    "description": "Child task specs",
                },
            },
            "required": ["parent_id", "kb_name", "children"],
        },
    },
    "task_checkpoint": {
        "description": "Log a checkpoint on a task with optional confidence and evidence.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task entry ID"},
                "kb_name": {"type": "string", "description": "KB name"},
                "message": {"type": "string", "description": "Checkpoint message"},
                "confidence": {
                    "type": "number",
                    "description": "Confidence 0.0-1.0",
                    "minimum": 0,
                    "maximum": 1,
                },
                "partial_evidence": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Evidence entry IDs",
                },
            },
            "required": ["task_id", "kb_name", "message"],
        },
    },
    "kb_qa_assess": {
        "description": "Run QA assessment on an entry or entire KB. Creates qa_assessment entries recording results. Optionally creates tasks for failures.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kb_name": {
                    "type": "string",
                    "description": "KB to assess",
                },
                "entry_id": {
                    "type": "string",
                    "description": "Specific entry to assess (assesses entire KB if omitted)",
                },
                "tier": {
                    "type": "integer",
                    "description": "Assessment tier: 1=structural (default)",
                },
                "max_age_hours": {
                    "type": "integer",
                    "description": "Skip entries assessed within this many hours (default 24, 0 to reassess all)",
                },
                "create_tasks": {
                    "type": "boolean",
                    "description": "Create tasks for failed assessments (default false)",
                },
            },
            "required": ["kb_name"],
        },
    },
}

ADMIN_TOOLS = {
    "kb_index_sync": {
        "description": "Sync the search index with file changes. Use after editing files directly. Set background=true for async execution.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kb_name": {
                    "type": "string",
                    "description": "Sync specific KB (optional - syncs all if not provided)",
                },
                "background": {
                    "type": "boolean",
                    "description": "Run in background (returns job_id for polling). Default: false",
                    "default": False,
                },
            },
            "required": [],
        },
    },
    "kb_manage": {
        "description": "Manage knowledge bases: add, remove, discover, validate.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "discover",
                        "validate",
                        "show_schema",
                        "add_type",
                        "remove_type",
                        "set_schema",
                    ],
                    "description": "Management action",
                },
                "kb_name": {"type": "string", "description": "KB name (for validate)"},
                "type_name": {
                    "type": "string",
                    "description": "Type name (for add_type, remove_type)",
                },
                "type_def": {
                    "type": "object",
                    "description": "Type definition with description, required, optional, subdirectory (for add_type)",
                },
                "schema": {
                    "type": "object",
                    "description": "Schema object with types, policies, validation keys (for set_schema)",
                },
            },
            "required": ["action"],
        },
    },
    "kb_commit": {
        "description": "Commit changes in a KB's git repository. Stages and commits files with a message. Admin tier only \u2014 prevents write-tier agents from self-approving changes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kb": {
                    "type": "string",
                    "description": "Knowledge base name",
                },
                "message": {
                    "type": "string",
                    "description": "Commit message describing the changes",
                },
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Specific file paths to commit (commits all changes if omitted)",
                },
                "sign_off": {
                    "type": "boolean",
                    "description": "Add Signed-off-by line to commit",
                },
            },
            "required": ["kb", "message"],
        },
    },
    "kb_push": {
        "description": "Push KB commits to a remote repository. Requires a configured remote. Admin tier only.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kb": {
                    "type": "string",
                    "description": "Knowledge base name",
                },
                "remote": {
                    "type": "string",
                    "description": "Name of a remote configured in the KB's repository "
                    "(default: origin); URLs and paths are refused",
                },
                "branch": {
                    "type": "string",
                    "description": "Branch name to push (default: current branch)",
                },
            },
            "required": ["kb"],
        },
    },
    "kb_registry_add": {
        "description": "Register a new user KB by path. Does not modify config.yaml.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name for the new KB"},
                "path": {"type": "string", "description": "Filesystem path to the KB directory"},
                "kb_type": {"type": "string", "description": "KB type (default: generic)"},
                "description": {"type": "string", "description": "Optional description"},
            },
            "required": ["name", "path"],
        },
    },
    "kb_registry_remove": {
        "description": "Remove a user-added KB from the registry. Config-protected KBs cannot be removed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name of the KB to remove"},
            },
            "required": ["name"],
        },
    },
    "kb_registry_reindex": {
        "description": "Reindex a specific KB — sync files to the search index.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name of the KB to reindex"},
            },
            "required": ["name"],
        },
    },
    "kb_registry_health": {
        "description": "Check KB health: path exists, file count vs index count, staleness.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name of the KB to check"},
            },
            "required": ["name"],
        },
    },
}
