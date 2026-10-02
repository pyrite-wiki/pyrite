- A command's default output is its result. `pyrite`, `pyrite-admin` and
  `pyrite-read` now log at WARNING by default through one shared function, so
  `init`, `create`, `search` and `index embed` no longer print one
  `Applied migration` line per migration, a `search.query` line per search or
  the sqlite-vec line on stderr; stdio `pyrite mcp` stops filling the MCP
  client's server log the same way. `-v` (INFO) and `-vv` (DEBUG) restore the
  lines from any position on the command line, and `PYRITE_LOG_LEVEL` sets the
  level without a flag. `pyrite serve` and `pyrite-server` keep INFO as the
  operator's log. `pyrite-admin`, `pyrite-read` and `pyrite-server` now print
  warnings, formatted like `pyrite`'s; before, the package's inert handler
  swallowed them. (#584)
- `pyrite search --mode semantic|hybrid` without the `[semantic]` extra (or
  without the sqlite-vec extension) now returns a `warnings` entry naming
  `pip install pyrite[semantic]` on the CLI, REST and MCP, instead of a silent
  empty leg. The "no embeddings yet" warning no longer says "only the keyword
  leg ran" in pure semantic mode, where no keyword leg runs. The CLI prints
  warnings that contain brackets, such as the install line, intact. (#43, #584)
- `pyrite index embed` counts the entries it settled from the embed queue as
  embedded instead of skipped: after writes it printed `Embedded: 0, Skipped: 4`
  for a run that added four vectors. (#584)
