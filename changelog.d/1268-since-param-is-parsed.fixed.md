- **A `since` value that cannot be read is now refused, not answered with an
  empty list (#1268).** `kb_recent {"since": "yesterday"}` returned
  `{"entries": [], "count": 0}` — the same answer as "nothing changed since
  then" — because the value was never parsed: entries were kept by comparing
  strings, `(updated_at or "") >= since`. MCP `kb_recent` and `pyrite recent
  --since` now share one parser (`pyrite/schema/validators.py`): a date
  (`2026-10-22`, midnight UTC) or an ISO 8601 datetime, with or without an
  offset. Anything else is a `VALIDATION_FAILED` that names `since`, the
  accepted forms and an example. An empty value still means "no lower bound".
- **`since` with a non-UTC offset now compares instants, not text (#1268).**
  `2026-10-23T05:00:00-04:00` is 09:00Z; the string comparison kept every entry
  updated earlier that UTC day. Filtering is done on parsed instants, so the
  answer matches the moment the caller named. `date_from`/`date_to` are
  unchanged (see #1270).
