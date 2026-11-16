- **`pyrite qa duplicates -t <type>` reports candidate near-duplicates (#934),
  never merges entries.** It applies effective task status to explicit filters
  and the default live-entry scan, which skips entries whose status the type
  declares settled (retired, superseded, won't-do, done, ...); a type with no
  status is scanned whole. Identical token sets remain detectable in
  large clusters; `skipped_tokens` discloses bounded common-token expansion.
  A completed report exits 0; parse errors exit 2, while empty type/status
  values produce structured validation refusals (exit 1).
