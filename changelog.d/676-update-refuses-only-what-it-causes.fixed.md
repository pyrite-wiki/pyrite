- An update is refused only for a violation it causes: a schema violation
  already in the file (a multi-select holding a scalar, a number out of range, a
  missing required field), in a field the request does not name, is now a
  warning with the write's result instead of a `SCHEMA_VIOLATION`, on every
  surface. A request that sets the field to another bad value, or triggers a new
  finding, is still refused; create stays strict (#676, ADR-0042 decision 8).
