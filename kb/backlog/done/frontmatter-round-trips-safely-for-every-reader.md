---
id: frontmatter-round-trips-safely-for-every-reader
title: Frontmatter round-trips safely for every reader
type: backlog_item
tags:
- bug
- storage
importance: 5
kind: bug
status: done
priority: medium
assignee: agent:pyrite-worker-sonnet
effort: M
rank: 0
---

Theme: **frontmatter round-trips safely for every reader** — closes #568, and #569 items 1 and 2.

## Groom 2026-10-01 (conductor)

### Acceptance
1. (#568) When Pyrite serializes frontmatter, any *string* scalar that a YAML 1.1 reader (PyYAML `safe_load`) would resolve to a non-string is written quoted: `y|Y|yes|Yes|YES|n|N|no|No|NO|on|On|ON|off|Off|OFF|true|True|TRUE|false|False|FALSE` (as strings), `~`, `null`/`Null`/`NULL`, the empty string, and 1.1-only numeric forms the emitter would leave bare (sexagesimal like `1:30`, octal like `0777`, `0o17`, `1_000`). Genuine Python booleans and numbers stay unquoted. A value already quoted in the source keeps its quotes (round-trip preserve_quotes is unchanged). Applies at any depth (nested mappings, list items such as `tags: [yes]`).
2. (#568) Round-trip test: for every case above, `pyrite update -f key=<value>` (or the service write path) then PyYAML `safe_load` of the file's frontmatter returns the identical string; and Pyrite's own read returns the same string. A no-op load->save of a file that already has these values quoted is byte-identical.
3. (#569.1) REST `PUT /api/entries/{id}` routes the request through `KBService.split_echoed_update` exactly as MCP `kb_update` does (`pyrite/server/mcp_server.py` ~1315), so a client that echoes a whole read back does not rewrite `importance: high` as `5` or `tags: Foo` as `['Foo']`. If the MCP tool reports `ignored`/`unchanged`, the REST `UpdateResponse` reports them the same way (new optional fields; existing fields unchanged). Test: echo a read entry back through PUT; the file's `importance`/`tags` are unchanged.
4. (#569.2) A bare-string `sources:` scalar reads as a one-item list: a URL becomes a source with `url`, any other text a source with `title`. Test both, through the read path search/API see. Writing the entry back must not rewrite the file's scalar unless the caller changed sources (#557's rule: an update never loses or reshapes a key it wasn't asked to change).
5. Changelog fragments in `changelog.d/` (`.fixed.md`), one per issue or one combined.

### Touches
- existing, patch minimally: `pyrite/utils/yaml.py` (the representer / dump path), `pyrite/server/endpoints/entries.py` (`update_entry`), the REST `UpdateResponse` model (wherever it is defined), `pyrite/models/base.py` (`parse_sources`)
- new: `tests/test_frontmatter_yaml11_safe.py`, `tests/test_rest_put_echo.py`, `tests/test_scalar_sources.py` (or extend existing ones if a natural home exists)
- NOT touched: `pyrite/services/kb_service.py` (a parallel theme, #555, is editing it — call `split_echoed_update`, do not change it), `pyrite/schema/`, `pyrite/storage/index.py`.

### Out of scope
- #569 item 3 (the echo-vs-deliberate signal and `docs/agent-write-path.md`): it changes `split_echoed_update` and is sequenced after #555.
- Changing how Pyrite itself reads YAML (stays ruamel/1.2).
- The web editor.

Model: sonnet. Cold read: yes (REST public shape, file format).
