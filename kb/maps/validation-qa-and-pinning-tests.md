---
id: map-validation-qa-and-pinning-tests
title: "What test fails if code goes around this rule? Topic map: validation, QA and the tests that pin the design"
type: note
tags:
- map
- design
- qa
- validation
- testing
- invariants
- structural-tests
- evaluation
- rubric
---

# What test fails if code goes around this rule?

Answers "how is this rule enforced and what test fails if code goes around it?",
"what do QA and gates check?", "how should a test for a design rule be built?".
Layer 2 under [[design]] (P8, P9). Each other map lists its own pinning tests;
this one collects the method.

## The design today

1. A write is validated against the type's schema (required, type, options);
   plugin validators add domain rules; an enum is checked by one function on
   every path. **decided** [[adr-0008]].
2. Types, KBs and gates carry rubrics; a gate criterion is a named `checker`
   (auto-evaluated), `judgment` or `agent_responsibility` (always pass).
   **decided** [[adr-0021]]; plugins add checkers through `get_rubric_checkers`
   ([[adr-0002]]).
3. A rule is enforced at one point, with a test that fails when new code goes
   around it: the authorization guard walks every REST operation and MCP tool,
   and its allowlist only shrinks. **decided** [[adr-0037]] section 5.
4. Characterization goldens are written before a migration; a migration PR may
   not change one. **decided** [[adr-0037]] migration.
5. A state-machine test drives the real storage with a property-based harness:
   one strict expected failure per known violation, failing with an
   `AssertionError` that names the invariant; a pinned seed; mutation checks that
   turn it red; an unexpected pass fails the suite. **proposed** [[adr-0038]]
   (the harness landed; the ADR is still labelled proposed).
6. Plugins run a conformance kit on themselves: structure, signatures, scoping
   through the real dispatcher, writes make files, no imports beyond the façade.
   **accepted, not built** [[adr-0040]] section 6.
7. Tests are layered by cost: commit hooks (seconds), pre-push affected tests, a
   PR check on top of current `dev`, a push-to-`dev` smoke job of the assembled
   artifact, then release checks. **decided** [[adr-0032]] sections 3 and 3a.
8. Docs that teach a command run as that command's test; the passage is written
   first and its examples are the test, over files Pyrite did not write.
   **proposed** ADR-0041 to ADR-0045 acceptance sections; [[design]] P9.
9. A benchmark with a pass criterion decides a backend question. **decided**
   [[adr-0016]].
10. QA reports what Pyrite no longer repairs on save: files with no `id:`,
    aliases, schema-behind files, links to retired derived ids. **proposed**
    ADR-0042 decisions 7 and 8.

## Invariants a test could check

- A new REST route or MCP tool without a declared action fails CI; a handler
  that compares roles fails the grep ratchet ([[adr-0037]] section 5).
- Required CI check names are pinned by a test, so a renamed job cannot silently
  unprotect a branch ([[adr-0032]] migration step 5).
- `fix:` commits must touch `tests/` ([[adr-0032]]'s hooks); once built, a
  change to the plugin API snapshot needs a `plugin-api` changelog fragment
  ([[adr-0040]] section 4).
- Each xfail in the invariant harness is tied to one issue and fails with its
  invariant id; removing a fix without removing its xfail fails ([[adr-0038]]).
- `docs/*.yaml`, templates and presets satisfy their own enum rules
  ([[adr-0008]] point 6).

## ADRs in reading order

[[adr-0032]] (layers), [[adr-0037]] (guards), [[adr-0038]] (the harness method),
[[adr-0008]] (validation), [[adr-0021]] (gates), [[adr-0016]] (benchmarks),
[[adr-0040]] (the kit). Standard `testing-standards`.

## Where the code starts

Components [[qa-service]], [[rubric-checkers]], [[llm-rubric-evaluator]],
[[schema-validation]]. Paths: `pyrite/services/qa_service.py`,
`pyrite/services/rubric_checkers.py`, `pyrite/schema/enum_check.py`,
`scripts/test-affected`, `.pre-commit-config.yaml`, `scripts/run_tutorial.sh`.

## Tests that pin it

`tests/test_every_entry_point_passes_the_policy.py`,
`tests/test_read_scoping_is_structural.py`,
`tests/test_kb_write_guard_is_structural.py`, `tests/test_layer_boundaries.py`,
`tests/test_storage_invariants.py`, `tests/test_plugin_contract.py`,
`tests/test_dev_process_config.py`, `tests/test_enum_enforcement.py`,
`tests/characterization/`, `tests/e2e/`, `tests/test_gates.py`.

## Known gaps

- No `tests/test_doc_*` file exists; the doc-as-test runners proposed in
  ADR-0041 to ADR-0045 are unwritten. `scripts/run_tutorial.sh` runs the
  getting-started guide in the smoke job ([[adr-0032]]).
- [[adr-0037]] themes 3b to 5 have not landed, so its allowlist is not empty and
  the four older structural tests have not folded in.
- `judgment` criteria cannot fail by design ([[adr-0021]]).
- [[adr-0032]] section 3b is `proposed` inside an accepted ADR and
  `scripts/release.py` does the opposite; the merge method is stated two ways
  ([[adr-audit-2026-10]]); the live repository settings were not verified.
- Not decided: whether a wrong declaration (right presence, wrong action) is
  caught outside the generated matrix ([[adr-0037]] costs).
