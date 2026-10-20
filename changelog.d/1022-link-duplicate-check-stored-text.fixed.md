- `pyrite link`, `add_link` and `add_links` compare a target with a hand
  file's `links:` item as the text the index stores it, so `pyrite link src 1`
  against `target: true` is a no-op instead of writing a second item (#1022),
  and a file's `kb: ops` (a shortname) is the same link as `--target-kb ops-kb`.
  A frontmatter link to a timestamp id (`target: 2024-01-01 10:00:00`) now
  resolves to the entry with that id. The service-level tests are ported from
  @slophisticated's #999.
