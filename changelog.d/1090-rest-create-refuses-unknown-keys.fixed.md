- `POST /api/entries` refuses a top-level key its body does not know, naming it
  with 400 `VALIDATION_FAILED`, instead of dropping it and answering
  `created: true` with no warning: a managed field sent at the top level
  (`evidence`, `status_change_log`, `provenance`) can no longer vanish
  silently (#1090). The `metadata` envelope spelling is refused by the
  service, as before (#1010).
  The web New Entry form now sends its Status input in `metadata`, so it is
  not refused. Thanks to @ANO3457, whose #1124 this is.
