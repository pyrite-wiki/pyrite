---
id: map-links-blocks-collections-and-formats
title: "How are entries linked, and what is a collection or an edge? Topic map: links, blocks, collections and formats"
type: note
tags:
- map
- design
- links
- wikilinks
- backlinks
- blocks
- collections
- edge-entities
- formats
- content
---

# How are entries linked, and what is a collection or an edge?

Answers "how are entries linked?", "what is a collection?", "when is a
relationship an entry?", "what formats does Pyrite emit?". Layer 2 under
[[design]] (P1, P4).

## The design today

1. Entries link with `[[id]]`, `[[id#heading]]`, `[[id^block-id]]`, and embed
   with `![[...]]`; the syntax is Obsidian-compatible and cross-KB with a
   `kb:` prefix. A derived `block` table indexes blocks per entry. **decided**
   [[adr-0012]].
2. Backlinks, outlinks and edge endpoints are index rows rebuilt from the
   files; no save rewrites another entry's frontmatter. **decided**
   [[adr-0022]]; the save hooks that write links today are replaced by
   derivation in ADR-0042 and ADR-0045 (**decided**).
3. A relationship that carries data (an ownership with a percentage, a board
   membership with a term) is an edge-entity entry: two or more declared
   endpoints, all required, optional type constraints; provisional claims are
   not edges. **decided** [[adr-0022]].
4. `pyrite backlinks` returns one merged list labelled by source (edge,
   frontmatter link, wikilink). **decided** [[adr-0022]].
5. A deleted endpoint leaves a broken reference that QA reports; nothing is
   auto-deleted. **decided** [[adr-0022]].
6. A folder becomes a collection by holding `__collection.yaml`; a virtual
   collection is an entry whose source is a query; `__`-prefixed files are
   collection metadata, not entries. **decided** [[adr-0011]] (five phases;
   completion unchecked).
7. `object-ref` fields reference other entries and feed the reference table.
   **decided** [[adr-0008]].
8. Output formats (json, markdown, yaml, csv, toon, text) come from one registry
   behind `Accept` and `--format`; the file stays Markdown and YAML. Import
   reuses the registry and is marked future. **decided** [[adr-0010]].
9. Links may point up the durability ladder, never down: a durable entry must
   not link into an ephemeral KB. A QA rule enforces it. **accepted, not built**
   [[adr-0029]] section 3.
10. A rename rewrites inbound links as operations on the other files, under
    their locks. **decided** ADR-0042 decision 12.
11. Publishing to a static-site generator is an export; what that generator
    reserves is the exporter's concern. **decided** ADR-0041 decision 1. The
    accepted `/site/` cache is [[adr-0023]].

## Invariants a test could check

- An edge-type entry missing an endpoint, or with an endpoint of a type not in
  `accepts`, is rejected ([[adr-0022]]).
- Saving an edge-entity changes no other file ([[adr-0022]]).
- A block id is unique within an entry; a circular transclusion is cut at a
  depth limit ([[adr-0012]]).
- Rebuilding the index from files reproduces every link, backlink and block row
  ([[adr-0001]], [[adr-0012]]).
- A durable entry has no wikilink into an ephemeral KB ([[adr-0029]], unbuilt).
- Export and negotiated output never carry content from a KB the caller cannot
  read (`tests/test_kb_export_read_scoping.py`; [[adr-0037]]).

## ADRs in reading order

[[adr-0012]], [[adr-0022]], [[adr-0011]], [[adr-0010]], [[adr-0008]] (the
`object-ref` type). [[adr-0029]] section 3 for the link rule. History only:
[[adr-0020]] (collection-like board views are config and computed).

## Where the code starts

Components [[block-service]], [[markdown-block-extraction]],
[[wikilink-service]], [[collection-query-service]], [[collections-cli-commands]],
[[graph-service]], [[format-system]], [[export-service]], [[editor-system]].
Paths: `pyrite/services/block_service.py`, `pyrite/services/wikilink_service.py`,
`pyrite/services/collection_query.py`, `pyrite/formats/`.

## Tests that pin it

`tests/test_block_extraction.py`, `tests/test_block_refs.py`,
`tests/test_wikilink_service.py`, `tests/test_edge_endpoint_validation.py`,
`tests/test_edge_endpoint_sync.py`, `tests/test_backlinks_relation_surfaces.py`,
`tests/test_collection_query.py`, `tests/test_collections.py`,
`tests/test_formats.py`, `tests/test_import_export.py`.

## Known gaps

- Which of [[adr-0011]]'s phases shipped is unverified; the alpha entry cites an
  open defect on creating a collection in the web UI (#480).
- The import pipeline of [[adr-0010]] section 7 is "future"; the link rule is
  unbuilt.
- Hooks still write links (cascade and journalism-investigation); until
  ADR-0042 and ADR-0045 are accepted, a save can add links the caller did not
  send.
- Not decided: path spelling of an id with `/`, which wikilinks and routes
  would need to accept (ADR-0042 question 4).
