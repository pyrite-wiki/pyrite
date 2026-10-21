- An item appended to a list copies the quoting of the item beside it at
  every level: `- target: "foo"` gets `- target: "bar"`, not a bare `bar`,
  in block and flow style alike, for any key (an integer key included). A
  neighbour's spelling is used only where it reads back as the value;
  otherwise the emitter's own rule applies (#1023).
  The same holds for an update that inserts an item into an existing list and
  for the item-wise rebuild used when a narrow edit fails its post-check: a
  new item is spelled like the item beside it (the flow rebuild too), and a
  replaced item in that rebuild is spelled like the item it replaces.
  Not copied, and unchanged: a list rewritten afresh because no old item is
  kept, and a changed or new pair in the item-wise rebuild of a mapping, take
  the emitter's own rule.
  Thanks to @ANO3457, whose #1131 this is.
