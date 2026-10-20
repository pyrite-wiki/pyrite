- **`zettel_graph` changes what it returns (#523).** `depth` is capped at 3:
  `1`-`3` are accepted and anything else (0, negative, above 3, non-integer)
  returns `{"error": "depth must be between 1 and 3"}` instead of being
  accepted. Each entry under `neighbors` now carries `id`, `kb_name` and the
  hop `depth` it was found at, and is keyed by the bare entry id when it is in
  the KB you asked about and by `kb:id` when it lives in another KB. Callers
  that looked up neighbours by id alone must use the `kb:id` key for
  other-KB neighbours. Neighbours in KBs the caller cannot read are never
  expanded.
