- `pyrite export site` names every entry it matched but could not load, with
  the loader's message, one line each, and exits 3 when some entries were
  exported and 1 when none were; `--allow-partial` restores exit 0 for callers
  that accept a partial site (#1325). `pyrite export collection` reports its
  unloadable rows the same way, and both commands say "1 entry" rather than
  "1 entries". Filtering with `--exclude-types`/`--exclude-status` is asked for
  and still exits 0; names and reasons are printed literally, so a YAML message
  that quotes `[...]` does not reach Rich's markup parser. A folder collection
  now exports exactly the files the indexer lists: a README, a hidden file and
  a `_templates` scaffold file are no longer exported, a file whose name starts
  with `__` now is, and a `folder_path` that leaves the KB exports nothing. A
  `folder_path` that only matches after normalising (`stuff/../stuff`) or an
  in-KB symlink to another folder matches its files again.
