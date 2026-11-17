- **`pyrite wiki stubs` refuses non-positive `--limit` (#1560).**
  `wiki stubs` now declares `min=1` on `--limit`, rejecting 0 and negative
  values as usage errors instead of reading them as 1.
