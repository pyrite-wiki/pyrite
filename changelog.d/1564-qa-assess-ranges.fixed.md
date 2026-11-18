- **`pyrite qa assess` declares ranges for `--tier` and `--max-age` (#1564).**
  `qa assess` now declares the valid range for `--tier` (1 or 2) and `--max-age` (0 or greater),
  refusing out-of-range values as Typer usage errors (`USAGE_ERROR`, exit 2)
  instead of `INVALID_TIER` (exit 1) or accepting negative max age. Upgrade note:
  a script that matched `INVALID_TIER` or exit 1 from `pyrite qa assess --tier` now gets
  `USAGE_ERROR` and exit 2; REST and MCP still answer `INVALID_TIER`; `pyrite ci --tier` is a usage error too (#1563).
