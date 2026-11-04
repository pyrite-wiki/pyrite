- `qa check-urls --sample` refuses a negative value as a usage error — exit 2
  with a single `USAGE_ERROR` JSON document naming `--sample` under
  `--format json` — instead of crashing inside `random.sample` (#1399).
  `--sample 0` (all URLs) and positive samples behave as before.
