- **Opening an index whose table has rows and lacks a column SQLite cannot add
  in place now refuses with `STORAGE_ERROR` and a rebuild suggestion at plan
  time, instead of failing inside the repair and telling the user to report a
  bug (#1318).** This covers a non-constant default (`CURRENT_TIMESTAMP`,
  `CURRENT_DATE`, `CURRENT_TIME`, or a parenthesised expression that is not a
  literal) and a `STORED` generated column. An empty table still takes such a
  column, and constant defaults such as `'active'` or `0` still add in place on
  a table with rows.
