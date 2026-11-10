- `pyrite export site` names every entry it matched but could not load, with
  the loader's message, one line each, and exits 3 when some entries were
  exported and 1 when none were; `--allow-partial` restores exit 0 for callers
  that accept a partial site (#1325). `pyrite export collection` reports its
  unloadable rows the same way, and both commands say "1 entry" rather than
  "1 entries". Filtering with `--exclude-types`/`--exclude-status` is asked for
  and still exits 0.
