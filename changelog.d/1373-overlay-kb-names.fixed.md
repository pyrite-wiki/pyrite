- **`GET /api/entries` no longer returns 500 for a signed-in user with an open
  change set (#1373).** The overlay search backend now accepts the `kb_names`
  filter the entries route passes, so listing and counting entries on a
  review-landing KB respect the caller's readable KBs instead of raising
  `TypeError`.
