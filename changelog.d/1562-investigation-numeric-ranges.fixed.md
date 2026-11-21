- **`pyrite investigation` options declare valid numeric ranges (#1562).**
  Numeric CLI options across `pyrite investigation` subcommands now declare their
  ranges and refuse out-of-range values as usage errors (exit 2) before executing:
  `--min-importance` (0 to 10), `--limit` (minimum 1), `ownership --depth` (minimum 1),
  `money-flow --hops` (minimum 1), `qa --stale-days` (minimum 0), and `dedup --threshold`
  (0.0 to 1.0).
