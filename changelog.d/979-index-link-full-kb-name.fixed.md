- A `[[short:id]]` link is indexed under the KB's full name, as ADR-0049 says
  (#952, #979): the index used to store the prefix as written, so backlinks,
  the graph, `index health` and `qa check-links` called every shortname link
  broken while `resolve_entry` found it. A frontmatter `links:` item's `kb`
  resolves the same way. The prefix as written is kept beside the resolved
  name (`link.target_prefix`, index migration v28), and `index health` warns
  with `links_stale_kb`: link rows that point at a different KB than their
  file's prefix names now, so a rebuild would rewrite them. Two causes: a row
  indexed before this change (still naming a shortname), or a shortname
  renamed or added in config since the file was indexed (`index sync` does not
  re-read an unchanged file). **Run `pyrite index build` after upgrading**, and
  after changing a shortname; it re-reads every file and clears the count. A
  prefix that names no KB is stored as written and counted in `broken_links`,
  not `links_stale_kb`: a rebuild would write the same row.
- `pyrite batch-read kb:id ...` resolves the KB in each spec before reading
  anything (#952): an unknown KB or a shortname in any one spec refuses **the
  whole call** with `KB_NOT_FOUND` (exit 1, the full name suggested), where it
  used to read the other specs and list that one under `not_found`.
