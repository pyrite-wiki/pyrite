- **`pyrite search --batch queries.json` runs many searches in one process (#1692).**
  The file (`-` for stdin) is a JSON list of `{"query", "kb", "type", "mode", "limit",
  "fields", "status", "tag"}` objects, at most 50; the command-line options are the
  defaults. The answer is `{"count": N, "searches": [...]}` in input order, each slot
  what a single `pyrite search` prints for the same arguments, with one config load,
  one index and one embedding-model load. A refused query (syntax, length, unknown KB,
  unknown key) fills its own slot with the error shape and does not fail the others:
  exit 3 if some were refused, 1 if all. `--expand` and `--files` cannot be combined
  with `--batch`. CLI only.
