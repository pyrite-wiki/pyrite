`qa check-urls --sample` refuses a negative value as a usage error (exit 2,
`USAGE_ERROR` JSON under `--format json`, naming `--sample`) instead of
crashing inside `random.sample`. `--sample 0` (all URLs) and positive samples
behave as before.
