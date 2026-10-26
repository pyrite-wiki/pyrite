- `qa fix` no longer offers a missing-field fix (`--fix-rule
  schema_violation`). It matched the word "missing" in an issue's message, and
  the KB schema's required-field errors say "required", so no validator could
  produce an issue it handled; its one default (`importance: 5`) could not be
  the field of a real error either, since the index always carries the model's
  own default. A required field is the author's to fill: the issue is reported
  as `manual` with `no_safe_default`, and `qa fix` still fixes invalid dates,
  broken links and tag casing.
- `qa validate` issues with `rule: "schema_violation"`, errors and warnings
  alike, now carry `schema_rule` (`required`, `enum`, ...), the rule the schema
  broke, so a required field is told from the issue's data and not from its
  message (#958). The key is absent when a plugin validator names no rule.
