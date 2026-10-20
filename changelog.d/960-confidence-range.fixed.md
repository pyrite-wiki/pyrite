- **A model's `confidence` of `NaN`, infinity or a value outside 0 to 1 is
  refused (#1055).** Tier 2 QA accepted it on a failed rubric item, and
  `pyrite ci --format json` then wrote `NaN` or `Infinity`, which strict JSON
  parsers reject. The entry is now `evaluation_failed` with the reason, as for
  a confidence that is not a number.
