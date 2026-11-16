- A kb.yaml-declared type's `file_pattern` `{date}` now resolves from the
  entry's metadata when the attribute is absent, and a missing date falls
  back to the default filename, so entries land as `<date>--<slug>.md`
  instead of `--<slug>.md` (#1467).
