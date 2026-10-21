---
id: file-operations
title: File Operations
type: component
tags:
- core
- storage
importance: 5
kind: module
path: pyrite/storage/file_operations.py
owner: core
---

The text-level editor. `apply(text, ops, span)` applies field operations (`Set`, `Append`, `Remove`, `Unset`, `ReplaceBody`, `AddSubkey`) to an entry file's own text and returns the new text and a report. It is a pure function: it reads no file and writes none. Existing bytes are spliced, never re-emitted through a serializer; only the bytes of a new value are written, by the module's small emitter. Where the model decides that a file is not editable, it refuses (`OperationRefusedError`) rather than guess.

The invariant it keeps (a write changes what was asked and nothing else, and its post-check) is [[adr-0042]] decisions 1, 2, 3 and 6. The module docstring at the top of `pyrite/storage/file_operations.py` holds the span rule and the stated limits, each pinned by a `test_limit_*` test in `tests/test_file_operations.py`; read it before changing a splice.

## Public functions (line numbers on this branch)

- `apply(text, ops, span)` — `pyrite/storage/file_operations.py:321`; raises `OperationRefusedError` (:205) and changes nothing when an operation cannot be applied as asked
- Operation types: `Set` :251, `Append` :262, `Remove` :270, `Unset` :278, `ReplaceBody` :285, `AddSubkey` :294; results `OpResult` :305, `Report` :311; the span `FrontmatterSpan` :218
- `TimestampText` :228 and `QuotedText` :239 — string subtypes that tell `apply` to write a timestamp bare or a value quoted
- `values_equal(a, b)` — :371; the equality of decision 3
- `parse_path(path, op)` — :1110; a dotted/segmented path into the frontmatter
- `unmodellable_reason(text, span)` — :629; why `apply` would refuse every operation on this frontmatter, and the line of the key it cannot model
- `unmodellable_in_text(text)` :739, `may_be_unmodellable(loaded)` :753, `reason_for_read(reason)` :727 — the same rule for readers; `frontmatter_refusal(root, y)` :664 is the one rule `_parse` and `unmodellable_reason` share

`apply` takes its `FrontmatterSpan` from the caller (`pyrite.utils.frontmatter.split_frontmatter`); this module has no splitter.

## Who calls it

Every write of an entry file computes its new text here, then publishes it through [[entry-writer]] ([[adr-0042]] decision 12):

- `KBService._update` and `KBService.apply_operations` (`pyrite/services/kb_service.py:1835`, :1818) — an update, a link, a field write. Change an existing entry through `apply_operations`; nothing else writes one.
- `repository.new_entry_text` — a create's text, `apply` onto a skeleton ([[adr-0042]] decision 13); `KBRepository.rename` and the wikilink rewrite `_rewrite_as_values` (`pyrite/storage/repository.py:162`) — see [[kb-repository]]
- `TaskService`, `QAFixService`, `SchemaService` (`schema migrate`, `pyrite/services/schema_service.py:177`) and software-kb's `migrate-standards` (`extensions/software-kb/src/pyrite_software_kb/cli.py:723`)
- `IndexManager` (`pyrite/storage/index.py:392`, :1537) — the `unmodellable_*` check, so `index health` and `qa validate` list a file exactly when `apply` would refuse it

The ratchet `tests/test_entry_file_writers_are_one_function.py` fails if any other module writes an entry file's bytes.

## Related

- [[entry-writer]] — publishes what `apply` computes
- [[kb-repository]], [[kb-service]], [[document-manager]]
- [[adr-0042]]; [[adr-0051]] covers the `metadata.k` spelling, which `KBService.apply_operations` resolves before it builds operations, not this module
