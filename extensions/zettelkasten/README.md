# Zettelkasten

Example plugin. `zettelkasten` exists to show how a Pyrite plugin adds entry
types, CLI commands, MCP tools and a preset; it is a reference for plugin
authors, not a supported product.

## What this example demonstrates

Personal knowledge management in the Zettelkasten tradition, built as a
Pyrite plugin:

- **Entry types:** `zettel` (fleeting, literature, permanent or hub note,
  with a `maturity` level and a `processing_stage`) and `literature_note`.
- **CLI commands:** `pyrite zettel`.
- **MCP tools:** `zettel_inbox` (unprocessed fleeting notes),
  `zettel_graph` (link structure around a note).
- **Relationship types:** `elaborates`, `branches_from`, `synthesizes`
  (and their inverses), for linking notes as they mature.
- **Preset:** `zettelkasten`, for `pyrite init --template zettelkasten`.

`zettel_graph` accepts a depth from 1 to 3 (default 1). Depth 1 returns the
center note's links; each additional level expands readable neighbors and
includes their own links. Neighbor records include their note ID, KB, and hop
distance. Same-KB records use the note ID as the key; cross-KB records use
`kb_name:id`. Missing or unreadable targets are not expanded.

Read `extensions/zettelkasten/src/pyrite_zettelkasten/plugin.py` for the
full `ZettelkastenPlugin` implementation, and `entry_types.py`,
`validators.py` for how the CEQRC (Capture, Elaborate, Question, Review,
Connect) workflow is modeled.

## Install

```bash
pip install -e extensions/zettelkasten
```

## See also

`kb/standards/extension-development.md` — the package layout and rules this
example follows, and the standard a new plugin author should read alongside
this code.
