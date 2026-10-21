- An item appended to a list copies the quoting of the item beside it at
  every level: `- target: "foo"` gets `- target: "bar"`, not a bare `bar`,
  in block and flow style alike. A neighbour's spelling is used only where it
  reads back as the value; otherwise the emitter's own rule applies (#1023).
