- **`pyrite-read` now shows the local operator's error details (#1460).** `pyrite-read`
  now binds `local_process(True)` and `acting_as(OPERATOR)` in its root callback,
  matching `pyrite` and `pyrite-admin`. Messages built with `outside_text` no longer
  fall back to "the server log has the details" for local reads.
