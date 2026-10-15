- Every CLI command that takes a KB (`-k`/`--kb`, `--target-kb`, a positional
  KB) refuses a name that is not a KB with `KB_NOT_FOUND`, exit 1, before
  reading or writing anything (#924, #952). `tags`, `timeline`, `recent`,
  `list-entries`, `task list`, `qa validate`, `qa checkers`, `index build|sync|embed`,
  `pyrite-read tags`, `sw backlog` and the other extension commands answered an
  empty result and exit 0; `get`, `update`, `delete` and the task commands
  reported the entry, not the KB. A shortname there is refused with the full
  name suggested. The check is one helper in `pyrite.cli.context`
  (`require_kb` and `cli_context(kbs=...)`), and
  `tests/test_kb_walk_cli.py` walks every command of `pyrite`, `pyrite-read` and
  `pyrite-admin` to prove it.
