- **`pyrite sw` list commands refuse negative `--limit` and `--offset` (#1565).**
  `sw adrs`, `sw backlog`, `sw standards`, and `sw components` now declare
  `min=0` on `--limit` and `--offset`, rejecting negative values as usage
  errors instead of accepting them. `--limit 0` keeps meaning all.
