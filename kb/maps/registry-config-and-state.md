---
id: map-registry-config-and-state
title: "Who can add a KB? Topic map: the KB registry, config, secrets and runtime state"
type: note
tags:
- map
- design
- registry
- config
- secrets
- state
- operator
- libraries
- ephemeral
---

# Who can add a KB?

Answers "who can add a KB?", "where does this setting live?", "is this
configuration or content?". Layer 2 under [[design]] (P5, P7).

## The design today

1. A KB's existence and policy are recorded in one YAML file the operator
   owns; the database `kb` table is a derived cache. **decided** [[adr-0029]]
   section 1; restated and extended by ADR-0039 decision 1 (**decided**).
2. The server never writes that file. Registry changes are the operator
   editing a documented file; a CLI convenience (`kb add`, `repo subscribe`)
   may write it under a cross-process lock and ADR-0042's rule. **decided**
   ADR-0039 decision 3, ADR-0043 decision 5.
3. Ephemeral KBs are database rows only, named `eph-*`, never in the file.
   **decided** [[adr-0029]] section 3; **decided** ADR-0039 decision 4.
   The code writes them to the file.
4. One file per library; a deployment serves one library; a library is a
   closed file with no overlays. **accepted, not built** [[adr-0029]] section 2
   (0.26); **decided** ADR-0039 decision 2.
5. Secrets (API keys, AI keys) live in a separate owner-readable file, with
   environment variables first, so the registry file can be reviewed and put in
   git. **decided** ADR-0039 decision 5.
6. Grants and per-KB policy (`default_role`, `read_only`, `published`,
   `landing`) are the operator's, in the file, never in a KB's own tree.
   **decided** ADR-0039 decisions 6 and 11, ADR-0043 decision 5.
   [[adr-0029]] section 4 had grants as database state.
7. Four classes: KB content (files), operator configuration (files), derived
   (rebuilt), state (database: users, sessions, invites, queues, leases, stars,
   reviews, quotas). A rebuild loses only state, and the docs list it.
   **decided** ADR-0039 decision 7; [[adr-0029]] section 4 (**not built**).
8. A registry change reaches every reader at its next decision, by one
   snapshot per decision and one `KBRegistryChanged` event. **decided**
   ADR-0039 decisions 8 and 9.
9. KB policy is local: a plugin may recommend a preset, the KB owner adopts it.
   **decided** [[adr-0009]] decision 7.
10. Provider and model settings sit in the operator's config; keys never go to
    the browser. **decided** [[adr-0007]]. Read-bound sizes are environment
    variables, validated at start. **decided** [[adr-0034]].
11. Pyrite is a guest in files it does not own: record ownership, refuse what
    it cannot show it wrote, return the rest unchanged, change all or nothing.
    Standard `pyrite-is-a-guest-in-state-it-does-not-own`.

## Invariants a test could check

- R1 every named KB is listed in the file or is an ephemeral row; R2 no code
  outside the registry module reads `knowledge_bases` or `FROM kb` (allowlist
  shrinks); R8 no path from REST, MCP, web, worker or service calls
  `save_config`; R10 only the file publishes (ADR-0039).
- R3 indexing writes no policy; R7 a removed KB's name is released whole.
- S1 every table declared derived or state (ADR-0039).
- Ephemerals are never in the library file ([[adr-0029]] section 3).
- A load and save of a host file with no change is byte-identical or refused
  (the guest standard, property 3).

## ADRs in reading order

[[adr-0029]] (read its audit standing first: contradicted, mostly unbuilt),
ADR-0039 (accepted; replaces an earlier text of the same number that made the
database the registry), ADR-0043 (accepted; which plane config belongs to),
[[adr-0009]], [[adr-0007]]. History only: [[adr-0003]] (superseded),
[[adr-0018]] (its DB registry outlived it; superseded in part).

## Where the code starts

Components [[config-system]], [[kb-registry-service]], [[ephemeral-service]],
[[settings-service]], [[repo-service]]. Paths: `pyrite/config.py`
(`_db_kb_cache`, `save_config`), `pyrite/services/kb_registry_service.py`,
`pyrite/services/ephemeral_service.py`.

## Tests that pin it

`tests/test_config.py`, `tests/test_config_atomic_save.py`,
`tests/test_config_isolation.py`, `tests/test_kb_config_reconciliation.py`,
`tests/test_ephemeral_kbs.py`. No ADR names a test for the registry; ADR-0039
proposes an invariant harness (R1 to R10, two views on one index file).

## Known gaps

- Three registry designs exist ([[adr-0029]], the earlier ADR-0039, ADR-0039
  now); none is accepted over the others. The file, the cache and the table are
  each written by several processes; the config lock is per process.
- Ephemeral KBs are written to the config file against [[adr-0029]] section 3;
  secrets share the registry file; no state-table registry exists.
- Libraries, leases and `retention` are 0.26 ([[adr-0029]]) and unbuilt.
- Not decided: hot reload versus restart on a file change (ADR-0039
  question 1).
