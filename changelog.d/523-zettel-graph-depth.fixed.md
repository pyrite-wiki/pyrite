- **`zettel_graph` now honours `depth` (#523).** `depth: 2` or `3` used to
  return only first-hop neighbour links; it now expands readable neighbours hop
  by hop, visiting each entry once, so cycles do not repeat.
