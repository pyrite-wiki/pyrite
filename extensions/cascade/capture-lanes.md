# Capture-lane vocabulary and drift

`cascade_capture_lanes` retains `count` and `lanes` (raw spellings) for compatibility.
Use `canonical` to distinguish the vocabulary from drift: options declared for
`capture_lanes` in the selected/readable KB schemas, including unused values at
count zero. With no KB name, the vocabulary is the union of readable schemas;
there is no built-in or guessed canonical list. `options`, the schema loader’s
`values` alias, and list `items.options` / `items.values` are supported.

Matching trims, lowercases, and replaces underscores and spaces with hyphens.
No synonyms or fuzzy matching are used. Canonical counts count each entry once
per canonical lane, even if it uses several formatting variants. Other values
retain their exact spelling and count once per entry. Nothing rewrites entries
or changes validation / `allow_other`.

`other` contains at most 50 distinct values, ordered by count descending then
spelling ascending; `other_total` and `other_has_more` report the omitted tail.
Canonical rows use the same ordering. The existing 5,000-entry scan bound is
retained: `scanned_entries` and `entries_has_more` disclose partial counts.
Archived entries remain excluded by the existing list query. These are an
indexed snapshot, not a transaction across simultaneous writes/schema edits.
