Entry writes and the index sync leave a knowledge base's registry row (`source`, `path`, `description`) as the operator's registry operations set it; they only make the row when it is missing. A knowledge base defined in `config.yaml` stays protected from removal through the registry after users write to it.

A tag filter in search, keyword or semantic, on SQLite or Postgres, matches an entry's own tags only, never those of an entry with the same id in another knowledge base.
