- **An update that only respells a number changes nothing (#1704).** One
  comparison now decides whether an update changes a value, for `apply`'s
  no-change check and for the `unchanged` report alike, where the two used
  to disagree. A number in another spelling is the same value: sending `5`
  or `5.0` over a file's `importance: 5.0` is reported unchanged and the
  file is left untouched, on MCP `kb_update`, REST `PUT`/`PATCH` and CLI
  `update -f`. An accepted importance is still stored as an int when the
  update actually changes the value (#1588).
