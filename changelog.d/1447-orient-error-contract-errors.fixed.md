- `pyrite orient` now names the per-field `errors` rows in its error contract
  (`{error, error_code, suggestion?, errors?, retryable}`, with one row per
  refused field). A refusal about a field has carried them since #1381, but an
  agent that learned the contract from `orient` never heard about them (#1447).
