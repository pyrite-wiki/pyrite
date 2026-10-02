---
id: map-plugins-protocols-and-extensions
title: "How do plugins add a type? Topic map: plugins, protocols, extensions and the plugin contract"
type: note
tags:
- map
- design
- plugins
- extensions
- protocols
- plugin-api
- entry-types
- hooks
- types
---

# How do plugins add a type?

Answers "how do plugins add a type?", "what is the plugin contract?", "what is
a protocol?". Layer 2 under [[design]] (P6). For `kb.yaml`-only types see
[[map-types-and-schema]].

## The design today

1. A plugin is a class registered under the `pyrite.plugins` entry-point group,
   with a unique `name` and a `capabilities` set; the registry dispatches only
   what a plugin declares and skips a plugin that declares none. **decided**
   [[adr-0002]] addenda.
2. A plugin adds types (`get_entry_types`, `get_type_metadata`,
   `get_field_schemas`, `get_protocols`, `get_kb_types`, `get_kb_presets`),
   tools and commands (`get_mcp_tools(tier)`, `get_cli_commands`), validators,
   hooks, workflows, DB tables and migrations. **decided** [[adr-0002]]; the
   current protocol has 20 methods ([[adr-0040]]).
3. A KB selects a type or preset by name, only among those installed plugins
   registered. **decided** [[adr-0040]] section 3.
4. Types interoperate through protocols: structural, not nominal; small
   primitives that compose; platform-backed behaviour; a schema that has the
   fields gets the behaviour. **decided** [[adr-0014]].
5. Core protocols are dataclass mixins (`Assignable`, `Temporal`, `Locatable`,
   `Statusable`, `Prioritizable`, and `Parentable` in code) that a `kb.yaml`
   type can declare. **decided** [[adr-0017]]; the mixins carry their own
   serialisation.
6. The public contract is one façade, `pyrite.plugin_api`, with a separate
   `PLUGIN_API_VERSION`, a snapshot test, a one-minor deprecation window and
   plugins pinned by core minor. **accepted, not built** [[adr-0040]]
   sections 1, 2, 4.
7. Every extension except software-kb moves to its own repository; cascade is
   deleted; a conformance kit each plugin runs on itself replaces core tests
   that name plugin tools. **accepted, not built** [[adr-0040]] sections 1, 6, 7.
8. Plugins are trusted code the operator installs, with no sandbox; nothing in a
   KB's files can install, import or enable one. **decided** [[adr-0040]]
   section 5.
9. A plugin creates entries only through `ctx.kb_service`, so every entry has a
   file; plugins register no REST routes. **accepted, not built** [[adr-0040]]
   section 3.
10. A protocol is a data contract, derived information, explicit operations and
    refusals; no protocol operation writes a file as a side effect; a hook only
    refuses; serialising an existing file is not a type's job. **proposed**
    ADR-0045 decisions 2, 3, 6, 9.
11. Rollup, unblock and evidence aggregation are derived, not written.
    **proposed** ADR-0045 decision 7 (maintainer, 2026-10-02).
12. The contract is alpha; it freezes later than [[adr-0040]] says (0.28 in the
    release line). ADR-0045 decision 10 (proposed).

## Invariants a test could check

- Every dispatched method has a capability; a plugin with no `capabilities` is
  skipped everywhere ([[adr-0002]]).
- A plugin's source imports no `pyrite.*` module except `pyrite.plugin_api`;
  every tool declares its relation to KB content; scoping holds for every read
  tool through the real dispatcher; every index row a plugin's writes created
  has a file ([[adr-0040]] section 6).
- The façade's names and signatures match a pinned snapshot ([[adr-0040]]
  section 4).
- Every protocol a type declares is satisfied by its schema; a type's reading
  does not depend on index state (ADR-0045, replacing the round-trip check).

## ADRs in reading order

[[adr-0014]] (the model), [[adr-0017]] (the implementation; the two disagree),
[[adr-0002]] (mechanism), ADR-0045 (proposed; reconciles them), [[adr-0040]]
(the contract; read with ADR-0045's list of amended sentences),
[[adr-0009]]. Also `kb/designs/alpha-supported-surface.md` (proposed). History
only: [[adr-0022]] (a type pattern a plugin first needed).

## Where the code starts

Components [[plugin-system]], [[pyrite-plugin-protocol]], [[protocols-module]],
[[software-kb-extension]], [[zettelkasten-extension]], [[social-extension]],
[[encyclopedia-extension]], [[cascade-extension]]. Paths:
`pyrite/plugins/protocol.py`, `pyrite/plugins/registry.py`,
`pyrite/plugins/capabilities.py`, `pyrite/models/protocols.py`,
`extensions/`. Standard `extension-development` and `plugin-developer-guide`.

## Tests that pin it

`tests/test_plugin_contract.py`, `tests/test_plugin_integration.py`,
`tests/test_mcp_tool_registry_is_scoped.py`. Proposed and absent:
`tests/test_plugin_api_snapshot.py` and `pyrite.plugins.testing`
([[adr-0040]]).

## Known gaps

- No `pyrite.plugin_api` façade, `PLUGIN_API_VERSION` or `plugins:` allowlist
  exists; `extensions/cascade/` is still in tree ([[adr-audit-2026-10]]).
- [[adr-0040]]'s inventory counts internal-use sites per extension:
  journalism-investigation 36, social 31, encyclopedia 19, cascade 17,
  zettelkasten 13 (software-kb, which stays, 84).
- [[adr-0014]] phases 1 and 2 (protocol entries, `requires_protocols`,
  `pyrite protocol show`) do not exist; the audit marks the ADR current and
  ADR-0045 disagrees on that point.
- [[adr-0014]] and [[adr-0017]] contradict each other (structural versus
  inherited); ADR-0045 asks the maintainer to reconcile (question 1).
- [[adr-0040]]'s round-trip conformance check and the "no signature changes"
  claim conflict with ADR-0042 and ADR-0045 (proposed). A spike over the 37
  extension classes is owed before the contract freezes.
