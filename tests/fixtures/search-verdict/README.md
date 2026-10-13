# Fixture KBs for the search verdict (#668)

Two small KBs for `tests/test_search_verdict_fixture_kb.py`, which runs the
labelled queries from the spike on #668 through the real embedding model.

- `tps/`: ten concept entries from the `tps` KB of
  [pyrite-kb-demo](https://github.com/pyrite-wiki/pyrite-kb-demo)
  (`tps/concepts/`), licensed CC BY-SA 4.0. The bodies are unchanged apart from
  wikilinks reduced to their text; the frontmatter is cut down to `id`,
  `title`, `type: note` and up to four tags, plus a `license: CC-BY-SA-4.0`
  field. These ten files stay under CC BY-SA 4.0 (see `NOTICE` at the
  repository root). The licence is a frontmatter field, not body text, so
  it is not embedded and does not move any similarity.
- `pyrite/`: twelve entries from this repository's own `kb/` (ADRs and
  component docs), frontmatter cut down the same way and bodies cut to their
  first 1,800 characters or so. Only the first 500 characters of a body are
  embedded by the default model.

What the queries rely on, so do not add it: `pyrite/` never mentions the Andon
cord, Elasticsearch or "Flurbex"; `tps/` never mentions Pyrite or MCP.

The fixture is too small to hold one case the spike measured: a near-domain
absence ("Elasticsearch backend") is told from an answer by how little its top
hit stands above the next nine results, and twelve entries are not a crowd. On
the 936-entry `kb/` the same query reads `none` (margin 0.09); here it reads
`weak`. The test pins that it is never `strong`.
