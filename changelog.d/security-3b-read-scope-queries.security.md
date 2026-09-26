- Collection queries, stored query collections, title and wikilink lookups,
  wanted pages and the social reputation score now stay within the knowledge
  bases the caller can read, including a knowledge base named inside the query
  or link text; one the caller cannot read answers as one that does not exist.
  The social plugin now records the knowledge base on each reputation
  adjustment; adjustments recorded before this release, which carry none, count
  only for unscoped callers.
- `POST /api/repos/{name}/sync` with an empty name now answers as an unknown
  repository instead of syncing every repository. `pyrite repo sync` with no
  name still syncs all of them.
