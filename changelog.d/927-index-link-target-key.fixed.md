- **Re-indexing a hand file no longer rewrites its links on every pass (#927).**
  A hand file's link fields are whatever YAML read — `target: 2024` is an
  integer, `relation: true` a boolean — while the link index stores them as text.
  The comparison keyed on the raw value, so it never matched the stored row: the
  link was deleted and re-inserted every pass, and a file holding both
  `target: 2024` and `target: '2024'` gained a duplicate row each time (2, 3, 4,
  5...). The index now compares every part of a link the way SQLite stores it, so
  a re-index matches the row it wrote last time, two spellings that store alike
  collapse into one link (last note wins, as they already did for identical
  string targets), and a duplicate no longer leaves spares behind. A null field,
  or one that is a list or a mapping, is still refused rather than stored.