- Fix `pyrite zettel inbox` and `pyrite zettel maturity` crashing on an invalid
  full-text query. Both commands now list all indexed zettels, including those
  beyond the first page, and respect the selected KB (#866).
