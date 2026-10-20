---
id: adr-0048
title: "A KB declares its text conventions in kb.yaml; Pyrite emits them in new files, reports files that differ, and normalises only when asked"
type: adr
importance: 4
adr_number: 48
status: accepted
date: '2026-10-15'
tags: [architecture, files, write-path, kb-yaml, qa]
links:
- target: adr-0041
  relation: related
  kb: pyrite
- target: adr-0042
  relation: related
  kb: pyrite
- target: adr-0039
  relation: related
  kb: pyrite
---

# ADR-0048: A KB declares its text conventions in kb.yaml; Pyrite emits them in new files, reports files that differ, and normalises only when asked

> **Proposed** (2026-10-15) for #908. The maintainer accepts or rejects it.

## Context

A KB's files can use LF or CRLF, end with or without a newline, start with a
byte-order mark, and carry trailing spaces. Nothing says what a KB expects, so
each writer guesses. Measured on `dev` 8e15faa8, a one-field `pyrite update
--field status=done` on a hand-made file with a BOM, CRLF endings, trailing
spaces and no final newline wrote the whole file back as LF, dropped the BOM
and added a final newline (`Entry.save` writes `to_markdown()` through a
text-mode file, `pyrite/models/base.py:878-920`; `to_markdown` always ends in
exactly one `\n`, `:712-720`).

That is principle 3's known gap, and ADR-0042 closes it: the value-level patch
(`pyrite/storage/file_operations.py`) keeps every byte outside the spans it
was asked to change, and writes new lines in the file's own line ending
(`_Doc.eol`, `:612`). It is built and tested but not yet wired into the write
path (#730, phase P3). Once it is, an update no longer changes a file's
conventions.

What ADR-0042 does not answer:

1. Which conventions a **new** file gets (today: LF, final newline, no BOM,
   by accident of `to_markdown`).
2. How a person learns that files in their KB break the conventions they
   want, without Pyrite repairing them silently (principle 2, "not a
   formatter").
3. How they ask for a repair.

## Decision

1. **Where.** A KB declares its conventions in `kb.yaml`, in a `text:` block,
   beside `validation:` and the `exclude:` key decided on 2026-10-14 (#22).
   `kb.yaml` is the operator's documented file (principle 7). Pyrite does not
   read `.editorconfig` or `.gitattributes`: they belong to other tools, and
   two sources would need a precedence rule.

   ```yaml
   text:
     line_endings: lf          # lf | crlf
     final_newline: true       # true | false
     bom: false                # true | false
     trailing_whitespace: keep # keep | report
   ```

   An unknown key or value is refused when `kb.yaml` is loaded, with the key
   named.

2. **No `text:` block means nothing is declared.** New files get today's
   bytes (LF, a final newline, no BOM), now written down as the default.
   Nothing is reported.

3. **The setting governs only bytes Pyrite creates.** A new file follows the
   declared conventions. An existing file keeps its own on every write, even
   when it breaks the declared ones: new lines inside it use the file's line
   ending (ADR-0042 decision 3), and a replaced body is written in the file's
   line ending. A write never converts a file as a side effect.

4. **Reads are LF on every surface.** Text that CLI, REST and MCP return
   decodes universal newlines (it does today, through `read_text`).
   `content_hash` stays the hash of the raw bytes. A test pins both.

5. **Report.** When a convention is declared, `pyrite qa` reports each file
   that breaks it, as a finding per property (`text.line_endings`,
   `text.final_newline`, `text.bom`, `text.trailing_whitespace`).
   `index health` counts the files. Excluded files are skipped.

6. **Repair only when asked.** A `pyrite qa fix` rule for the `text.*`
   findings (its dry run lists the files and the properties it would change)
   rewrites only the declared properties. It writes each file with the stale check of
   ADR-0042 decision 11, and its outcome lists every file it changed.
   `trailing_whitespace` is never stripped by it: in Markdown two trailing
   spaces are a line break, so that property is report-only.

## Questions for the maintainer

- **Q1. kb.yaml or .editorconfig?** **Decided: kb.yaml only.** Recommendation was kb.yaml only. It is where
  the operator already configures this KB, and `exclude:` set the precedent on
  2026-10-14. Counter-case: `.editorconfig` is already read by every editor,
  so a hand-editor's own tools would enforce the same convention for free.
  Pyrite could read it later as a default for an undeclared `text:` block
  without changing this decision.
- **Q2. git line-ending conversion.** On a Windows checkout with
  `core.autocrlf=true`, the working tree holds CRLF while the repository holds
  LF, so a declared `lf` would flag every file. Recommendation: the check
  reads the working tree, and when git reports that it converts line endings
  for the file (`git check-attr eol`, `core.autocrlf`), the line-ending check
  is skipped for that file and the report names the reason. **Decided: skip
  and say why.** Counter-case:
  compare the index blob instead. That is truer, but it costs a git call per
  file.
- **Q3. Is the repair a `qa fix` rule or its own command?** Recommendation
  was its own command, because it rewrites files that hold no error, only a
  style. **Decided: a `qa fix` rule** (decision 6), so the finding and its repair
  live in one place.

## Alternatives considered

- **Normalise on every write.** Rejected. It changes bytes nobody asked to
  change, which principle 3 forbids.
- **Detect the KB's majority convention and use it for new files.**
  Rejected. It is implicit, it changes when files change, and it cannot be
  reviewed.
- **Both kb.yaml and .editorconfig, with `.editorconfig` winning.** Rejected
  for now. See Q1.

## Consequences

- Phase 1, which can land before #730: parse and validate `text:`,
  make create emit by it, and document it. Phase 2: the qa findings and the
  health count. Phase 3: the `qa fix` rule.
- The update-keeps-CRLF acceptance of #908 is satisfied by #730 P3, not by this
  ADR. `tests/test_doc_write_as_patch.py` already holds the `crlf` example as an
  expected failure.
- #638 (BOM read as `event`, BOM dropped on save) stays a bug. Its save half is
  closed by #730.

## Decided

Accepted by the maintainer (Mark), 2026-10-15, through the desk.
