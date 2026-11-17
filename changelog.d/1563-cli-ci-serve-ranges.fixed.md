- **`pyrite ci` and `pyrite serve` declare ranges for `--tier` and `--port` (#1563).**
  `ci --tier` now declares `min=1, max=2` and refuses an out-of-range tier as a usage error
  (`USAGE_ERROR`, exit 2) instead of `INVALID_TIER` (exit 1). `serve --port` declares
  `min=1, max=65535` and refuses nonpositive and out-of-range port values before starting.
