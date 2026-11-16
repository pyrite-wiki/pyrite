- **`pyrite qa duplicates -t <type>` reports candidate near-duplicates (#934),
  never merges entries.** It applies effective task status to explicit filters
  and the default live-entry scan, which skips entries whose status the type
  declares settled (retired, superseded, won't-do, done, ...); a type with no
  status is scanned whole. Titles compare as runs of letters (Latin, Cyrillic, CJK, ...), casefolded and accent-folded; scripts that write vowels as combining marks (Devanagari, Thai) are not compared well; the
  threshold must be a number from 0 to 1 (`nan` is a usage error). A type declares
  what ends its live work with `settled_statuses` type metadata (plugin or `kb.yaml`); a wrong-shaped value is ignored and `qa validate` reports it. Identical token sets remain detectable in
  large clusters; `skipped_tokens` discloses bounded common-token expansion.
  A completed report exits 0; parse errors exit 2, while empty type/status
  values produce structured validation refusals (exit 1).
