---
id: kb-resolver
title: KB Resolver
type: component
tags:
- core
- services
importance: 5
kind: module
path: pyrite/services/kb_resolver.py
owner: core
---

The one resolver from a caller's KB string to one KB, a KB set, or one refusal. Every surface asks it and none decides on its own: REST through `pyrite/server/authz.py` (`resolve_named_kb`) and `AccessPolicy`, MCP in `_kb_refusal` (`pyrite/server/mcp_server.py:415`) at the dispatch, prompt and resource chokepoints, the CLI through `cli.context.require_kb`, and services through `PyriteConfig.require_kb` (`pyrite/config.py:519`).

The rule it keeps is [[adr-0049]] (the URL names the KB; scope, an address and a link's meaning are three things each decided once, with [[adr-0037]] §4 behind the refusal). The module docstring states the cases: a blank name names no KB; a full name the caller may read is that KB; anything else is one `KBNotFoundError`, with the same answer for an unknown and an unreadable KB, and a shortname is link syntax only. `readable_kbs` is a required keyword, so forgetting it is a `TypeError`, never "see everything".

## Public functions (line numbers on this branch)

- `resolve_kb(config, name, *, readable_kbs, db=None)` — `pyrite/services/kb_resolver.py:87`; the KB, None for a blank name, or `KBNotFoundError`
- `kb_set(config, name, *, readable_kbs, db=None)` — :109; the set a read spans: the one KB, else the caller's readable set
- `readable_kb_named(config, name, readable_kbs)` — :138; the KB a link prefix `[[name:id]]` means (full name, then shortname, readable set only)
- `readable_kb_with_shortname(config, name, readable_kbs)` — :127
- `readable_kb_names(config, readable_kbs)` :59 and `near_kb_names(config, name, readable_kbs)` :66 — the sorted readable names and the near matches offered to an unscoped caller only
- `HOW_TO_LIST_KBS` :51 and `NEAR_MATCH_LIMIT` :56 — the static hint and the limit used in every refusal

Existence is one lookup (`_lookup` :77): config.yaml, then the registry cache, then the live `kb` table when a `db` is given, so a KB registered after a server loaded its config is found.

## Who calls it

- `KBService.resolve_kb` (`pyrite/services/kb_service.py:817`) — the form REST's `authz` and the MCP chokepoints use, through the service's database
- `PyriteConfig.require_kb` (`pyrite/config.py:519`) and `cli.context` (`pyrite/cli/context.py:49`), unscoped
- `AccessPolicy` (`pyrite/services/access_policy.py:452`, :465), `WikilinkService` (`readable_kb_named`, `pyrite/services/wikilink_service.py:15`), `id_pin_service` (:578), and `pyrite/server/authz.py:108` for `HOW_TO_LIST_KBS`

`tests/test_kb_resolver.py` pins the function; `tests/test_kb_walk.py` pins that each REST route, MCP tool and CLI command that takes a KB name asks it.

## Related

- [[config-system]] — `PyriteConfig` and the KB registry it reads
- [[access-policy]] — the read scope that supplies `readable_kbs`
- [[kb-service]]
- [[adr-0049]]
