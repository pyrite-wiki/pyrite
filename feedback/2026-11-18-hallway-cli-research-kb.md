## 2026-11-17 · local-government procurement research KB with a custom schema, CLI hallway test (Pyrite 0.26.4 candidate, 96bb4936) · claude-sonnet-5-5

Scenario: a custom schema (a `contract` type with `vendor`, `amount` number, `signed` date and `bidders` list; a `vendor` type; a `note` type), 15 entries, links, search, QA, a hand edit and an export. Invented subjects only (City of Harbor Falls, Acme Paving LLC). Isolated HOME and Pyrite config/data directories.

**Friction 1 — nothing says how to declare a typed field (slowed)**
**Command:** `pyrite kb add-type ... --required vendor --optional amount`, then looking for where `amount` gets a type.
**Got:** `add-type` takes only `--required`/`--optional` names. No doc explains how to declare a field's type in `kb.yaml`. The list of field types (text, number, date, datetime, checkbox, select, multi-select, object-ref, list, tags) exists only in `pyrite/schema/field_schema.py`.
**Would have helped:** a docs page with one worked `kb.yaml` showing each field type, and `add-type` taking `name:type` (or a pointer to the page in its help).

**Friction 2 — a field named `tags` under a type is dropped while `kb validate` says valid (annoyed)**
**Command:** declare `tags` as a field of a type in `kb.yaml`, then `pyrite kb validate`.
**Got:** the field is dropped with a log warning about reserved names; `kb validate` reports `all_valid`.
**Would have helped:** `kb validate` naming the reserved field names and which declaration it ignored.

**Friction 3 — a refusal for `-f amount=1,200` names the problem but not the fix (annoyed)**
**Command:** `pyrite create -k procurement -t contract --title ... -f amount=1,200` (and `-f amount='$1200'`).
**Got:** `SCHEMA_VIOLATION`, field `amount`, rule `field_number`, expected "numeric value", no suggestion. Bad dates are better: "expected YYYY-MM-DD".
**Would have helped:** a suggestion of the plain form ("write 1200, no thousands separator or currency sign").

**Friction 4 — `-i 7.5` gives a usage error that never says the range (annoyed)**
**Command:** `pyrite create ... -i 7.5`, against `-i 42`.
**Got:** `-i 7.5` is a Typer usage error ("valid integer") over two lines; it never says 1-10. `-i 42` says "Importance must be 1-10" with a suggestion.
**Would have helped:** the same range hint for a decimal.

**Friction 5 — a hand-written importance 7.5 reads as 7, and `qa validate` is odd about it (annoyed)**
**Got:** with `importance: 7.5` written by hand, `get` and the CSV show 7 while the file keeps 7.5. `qa validate` gives a `reserved_key_collision` warning ("rename the key") for importance. After `index sync`, `schema_violation` for `amount` and `signed` appeared; both were fixed to zero issues once the values were corrected.
**Would have helped:** one rule across `get`, CSV and `qa`: show the stored value, or flag it as out of range with the 1-10 hint.

**Friction 6 — keyword search does not see field values or tags (slowed)**
**Command:** `pyrite search "Acme" -k procurement`; `pyrite search "paving" -k procurement`.
**Got:** "Acme" misses contracts whose `vendor` field is Acme (the name is not in the body); "paving" misses 2 of 3 entries tagged `paving`.
**Would have helped:** field values and tags in the keyword index, or a note in the results that only title and body were searched.

**Friction 7 — relations are free text and backlinks show a different name (annoyed)**
**Command:** `pyrite create ... --link acme-paving:awarded_to`, then `pyrite backlinks acme-paving -k procurement`.
**Got:** backlinks show `Relation: related_to` and `Written as: awarded_to`. Free-text relations are not checked. `orient`'s `relationship_types` lacks the names actually used.
**Would have helped:** `orient` listing the relation names in use, and backlinks showing the written name first.

**Friction 8 — the KB is passed three ways and `--format` is uneven (annoyed)**
**Got:** `qa validate`, `qa stale` and `ci` take the KB positionally; `index sync` has no `--format`.
**Would have helped:** `-k` accepted everywhere.

**Friction 9 — `qa validate --format rich` truncates at 80 columns (annoyed)**
**Got:** long messages cut off, including the part naming the fix.

**Friction 10 — the default rubric flags every entry with no outgoing links (annoyed)**
**Got:** every entry with no outgoing links is flagged, including entries that are only ever link targets; there is no hint how to turn the rule off for a type.
**Would have helped:** the warning naming the `kb.yaml` setting that relaxes it per type.

**Friction 11 — export and CSV cannot carry custom fields (annoyed)**
**Got:** `export collection` produces only NotebookLM output; `list-entries --format csv` has no columns for custom fields.
**Would have helped:** a CSV export with the type's declared fields as columns.

## Re-check of 0.26.3 findings (feedback/2026-11-16-hallway-cli-research-kb.md)
- Fixed: importance 42 is refused on create; a declared list field now holds a list.
- Changed: `orient --format markdown` is refused with the list of formats; a hand edit is visible to `get` at once, search warns the index is stale and `qa` names the sync command; a bad `link` error now lists the real subcommands.
- Still there: `qa` takes the KB two ways; `index sync` has no `--format`; relation names are free text; field-only mentions are missed by search; export is NotebookLM-only.

## What worked well
- The typed schema is enforced on create, naming the field, the rule and the expected form.
- `--tags` values are trimmed.
- `create --link` wrote the entry and its links in one call.
- `qa validate` for `index_out_of_date` names the fix.
- Search snippets are highlighted and the results warn when the index is stale.
- Export gives one readable file per entry.
