- **`pyrite ci` and `pyrite serve` declare ranges for `--tier` and `--port` (#1563).**
  `ci --tier` now declares the valid range (1 or 2) and refuses an out-of-range tier as a usage
  error (`USAGE_ERROR`, exit 2) instead of `INVALID_TIER` (exit 1). `serve --port` declares
  `min=1, max=65535` and refuses nonpositive and out-of-range port values before starting.
  Upgrade note: a script that matched `INVALID_TIER` or exit 1 from `pyrite ci --tier` now gets
  `USAGE_ERROR` and exit 2.
