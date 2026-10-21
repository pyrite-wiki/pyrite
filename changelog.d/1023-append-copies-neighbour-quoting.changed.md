- An item appended to a list copies the quoting of the item beside it at
  every level: `- target: "foo"` gets `- target: "bar"`, not a bare `bar`,
  in block and flow style alike. A neighbour's spelling is used only where it
  reads back as the value; otherwise the emitter's own rule applies (#1023).
  This holds for an append and for an update that inserts into an existing
  list (block or flow, and the item-wise rebuild when a narrow edit fails its
  post-check). A list rewritten afresh because no old item is kept, and a
  changed pair in the item-wise rebuild of a mapping, still take the emitter's
  own rule. Thanks to @ANO3457, whose #1131 this is.
