# Software KB relations

Run `pyrite sw relations` for the default JSON output (a mapping with the
declared relations under `relations` and the root `outcome` field),
`--format yaml` for YAML, or `--format rich` for a table. The entry types
below describe the intended link direction.

| Relation | Inverse | Source types | Target types |
| --- | --- | --- | --- |
| `affected_by` | `affects` | `component` | `backlog_item` |
| `affects` | `affected_by` | `backlog_item` | `component` |
| `amended_by` | `amends` | `adr` | `adr` |
| `amends` | `amended_by` | `adr` | `adr` |
| `blocked_by` | `blocks` | `backlog_item` | `backlog_item` |
| `blocks` | `blocked_by` | `backlog_item` | `backlog_item` |
| `depended_on_by` | `depends_on` | `component` | `component` |
| `depends_on` | `depended_on_by` | `component` | `component` |
| `documented_by` | `documents` | `component` | `runbook` |
| `documents` | `documented_by` | `runbook` | `component` |
| `has_session` | `session_for` | `backlog_item` | `work_log` |
| `has_subtask` | `subtask_of` | `backlog_item` | `backlog_item` |
| `implemented_by` | `implements` | `adr`, `design_doc` | `component` |
| `implements` | `implemented_by` | `component` | `adr`, `design_doc` |
| `session_for` | `has_session` | `work_log` | `backlog_item` |
| `subtask_of` | `has_subtask` | `backlog_item` | `backlog_item` |
| `superseded_by` | `supersedes` | `adr` | `adr` |
| `supersedes` | `superseded_by` | `adr` | `adr` |
| `tracked_by` | `tracks` | `adr`, `design_doc` | `backlog_item` |
| `tracks` | `tracked_by` | `backlog_item` | `adr`, `design_doc` |
