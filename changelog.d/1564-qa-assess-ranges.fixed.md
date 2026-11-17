- **`pyrite qa assess` declares ranges for `--tier` and `--max-age` (#1564).**
  `qa assess` now declares `min=1, max=2` on `--tier` and `min=0` on `--max-age`,
  refusing out-of-range values as Typer usage errors (`USAGE_ERROR`, exit 2)
  instead of `INVALID_TIER` (exit 1) or accepting negative max age.
