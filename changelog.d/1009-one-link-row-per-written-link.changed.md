- The index keeps one link row per link a file writes (#1009). It used to keep
  the first link to each bare target id and drop the rest, so `[[ka:x]] and
  [[kb:x]]` recorded only the link to `ka:x` (`backlinks x -k kb` was empty),
  a second prefix of one target (`[[ops:x]] [[ops-kb:x]]`) was never checked
  by `index health`, and a body `[[x]]` beside a frontmatter link to `x` was
  lost. Two links are now one row only when they have the same target, KB,
  relation and written prefix. **Backlink and outlink counts change**: a
  source that links one entry in two ways is listed once per link in
  `backlinks`, `get` outlinks, the graph, `links check` and the
  `broken_links` count, and `![[x]]` is now stored as a `transclusion` rather
  than a `wikilink`. Rebuild the index (`pyrite index build`) to see the new
  rows for files that have not changed.
  The web backlinks panel and the `backlinks` table list a source once with
  each of its links' relations, and the graph draws one edge per pair of
  entries. Two spellings of one link (`2024` and `'2024'`) keep the first
  one's note.
