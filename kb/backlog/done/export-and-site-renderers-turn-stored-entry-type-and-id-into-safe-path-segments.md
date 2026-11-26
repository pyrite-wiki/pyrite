---
id: export-and-site-renderers-turn-stored-entry-type-and-id-into-safe-path-segments
title: Export and site renderers turn stored entry_type and id into safe path segments (#221)
type: backlog_item
tags:
- security
importance: 5
kind: bug
status: done
priority: high
assignee: agent:pyrite-worker-221
effort: M
rank: 0
---

# #221 — Export writes attacker-controlled content to an arbitrary absolute path via unsanitized entry_type

`pyrite/services/export_service.py:82` joins a caller-controlled `entry_type` into a filesystem path with no sanitizer, three lines before a sibling that *does* sanitize:

```python
type_dir = target_dir / entry_type          # :82  — unsanitized
type_dir.mkdir(parents=True, exist_ok=True) # :83  — CodeQL alert #16
...
file_path = type_dir / f"{sanitize_filename(entry_id)}.md"   # :112 — entry_id IS sanitized
file_path.write_text(content, encoding="utf-8")
```

## Why it escapes

`pathlib` discards the left operand when the right is absolute — no `../` required:

```
>>> Path('/safe/export/dir') / '/tmp/pwned'
PosixPath('/tmp/pwned')
```

## Why an arbitrary entry_type reaches it — chain verified hop by hop on dev

1. `POST /api/entries` takes `entry_type` as a free `str` (`endpoints/entries.py:765`).
2. `kb_service.py:249-251` returns an **unknown type verbatim**: `core_cls = ENTRY_TYPE_REGISTRY.get(entry_type)` / `if not core_cls: return entry_type`. Confirmed by reading.
3. `factory.py` falls back to `GenericEntry`, so the write succeeds.
4. `storage/models.py` stores it in an unconstrained column.
5. `POST /api/kbs/{kb}/export` joins it into the path above.

## Impact

**Write tier, two ordinary API calls**, no traversal sequence needed: create an entry whose `type` is an absolute path, then export the KB. Result is `mkdir(parents=True)` plus `write_text` of attacker-controlled `body` at an attacker-chosen location. Also reachable through the MCP `entry_create` tool.

## The trap for whoever fixes this

Accepting unknown entry types is a **deliberate feature** — it is how `GenericEntry` and plugin-declared types work. Do not "fix" this with an allowlist of known types; that silently breaks every extension. Sanitize the value where it becomes a **path component**, leaving persistence and round-tripping alone.

Suggested shape: a `sanitize_type_dir` helper in `pyrite/utils/sanitize.py` (alongside the existing `sanitize_filename`), applied at the export join. A test must assert that an entry with an absolute-path `type` round-trips through create → export with the file landing **inside** the export directory, and that a normal plugin type still gets its own subdirectory.

## Provenance

Found by the 2026-09-20 CodeQL triage spike, which corrected an earlier groom: that groom lumped all five `export_service` `py/path-injection` alerts under "the tainted value is `kb_name`, a dict lookup" — true for #14/#15/#17/#18 (`:64`, `:69`, `:112`, `:161`), **false for #16** (`:83`), where the tainted value is `entry_type` and there is no sanitizer. The conductor independently verified the path-join escape and the verbatim-passthrough hop before filing.

CodeQL alert #16 (`py/path-injection`, high). Not a 0.24.2 blocker by the maintainer's call that CodeQL triage is not release-blocking, but it is a real exploitable write and should not sit in a backlog item only.


---

## Groom 2026-09-23

**Still reproduces on dev 6e505be: yes. Verified by a probe, not just by reading.**
- `pyrite/services/export_service.py:82-83` still has `type_dir = target_dir / entry_type` / `mkdir(parents=True)`; `:111-112` sanitizes only the id.
- A stub-config probe calling `ExportService.export_kb_to_directory` with `entry_type` set to an absolute path outside `target_dir` wrote `x.md`, containing the entry body, **outside** the export directory.
- Every hop of the chain is still open:
  - `schemas.py:371`: `entry_type: str = "note"`, no validator;
  - `kb_service.py:338-340`: an unknown type is returned verbatim;
  - `POST /api/kbs/{kb}/export` (`endpoints/kbs.py:151`, write tier) → `export_kb_to_repo` → `export_kb_to_directory` (`export_service.py:162`).
- None of 0.25's three security fixes touched this path.

**Two more sites have the same shape.** Neither is on REST, but both write files from a stored `entry_type`. That is the same stored-payload class: someone with write access plants the type, and the operator's CLI export fires it.
- `pyrite/renderers/quartz.py:177`: `type_dir = output_dir / entry_type`, then `mkdir` and `write_text` (`pyrite export site`).
- `pyrite/renderers/notebooklm.py:214` (inside `bundle_entries`): `filename = f"{entry_type}.md"`, written by `export_service.py:271` as `output_dir / filename`. An absolute type escapes there too (`pyrite export collection --bundle by-type`).

A reviewer would recognise all three as one change: "a stored `entry_type` becomes a path component safely". So they go in one PR.

**Policy is not open.** The issue already settles it: keep accepting unknown types (GenericEntry and plugin types depend on it) and sanitize only where the value becomes a path component.

### Acceptance
1. New `sanitize_path_component` (or `sanitize_type_dir`) in `pyrite/utils/sanitize.py`. For any input it returns a single path component: no separators, not absolute, not `.`/`..`, no leading dot, never empty (fallback such as `_untyped`). Ordinary type names pass through unchanged: `note`, `backlog_item`, `cascade_event`, `timeline_event`, and hyphenated names.
2. Applied at all three joins: `export_service.py:82`, `quartz.py:177`, and the `by-type` filename in `notebooklm.py`.
3. **Stored data is not changed:** persistence, the DB column and the exported frontmatter `type:` keep the raw value. Only the directory or file name is sanitized. No allowlist of known types.
4. **Tests** (extend `tests/test_export_service.py`, `tests/test_quartz_renderer.py` and `tests/test_notebooklm_renderer.py`, plus unit tests for the helper):
   - For each of the three exporters, an entry whose type is an absolute path (e.g. `str(tmp_path / "outside")`) and one whose type is `../../outside` both land **inside** the output directory, and nothing is created at the outside path.
   - A normal plugin type still gets its own subdirectory (or `<type>.md` file for the bundle) with the name unchanged.
   - The exported frontmatter `type:` still carries the raw value.
5. The resulting output path is asserted to be under the output root (`resolve()` + `is_relative_to`) in at least the export_service test, so a future join cannot regress silently.
6. A changelog fragment in `changelog.d/`.

### Touches
- existing: `pyrite/utils/sanitize.py`, `pyrite/services/export_service.py`, `pyrite/renderers/quartz.py`, `pyrite/renderers/notebooklm.py`, `tests/test_export_service.py`, `tests/test_quartz_renderer.py`, `tests/test_notebooklm_renderer.py`
- new: a helper test (in an existing sanitize test file if one exists, else `tests/test_sanitize_path_component.py`), `changelog.d/<n>.security.md`

### Sequence
Independent of #218 and #207: no shared files.

### Model / weight / review
- **Model: sonnet.** Mechanical once the policy above is fixed. The shape, the sites and the trap ("no allowlist") are all spelled out, so a careful junior could do it from this ticket.
- **heavy: no.**
- **Cold read: yes.** It is a security fix on a write path. The reviewer checks that all three sites are covered, that no allowlist has crept in, and that the frontmatter round-trip is intact.

### Out of scope
- Constraining `entry_type` at create time (API, MCP or schema validation). This is the tempting fix, and it breaks extensions.
- The unsanitized `entry.id` in the same two renderers (`quartz.py:182` `f"{entry.id}.md"`, `notebooklm.py:196`). REST-created ids are slugified (`generate_entry_id`, `entries.py:840`), but ids indexed from git frontmatter are not. That is a real sibling finding but a different value. File a separate issue, or fold it in only if the reviewer agrees it is the same theme. The worker should flag it, not silently widen the PR.
- `kb_name` joins (CodeQL #14/#15/#17/#18). Those are dict lookups, already triaged.
- Closing the CodeQL alert is done in the GitHub UI after the merge, not by the PR.


## Conductor addition (2026-09-23): widened by one bounded item

The groom's "out of scope" note on unsanitized `entry.id` in `renderers/quartz.py:182` and `renderers/notebooklm.py:196` is **folded into this theme**: same files, same class (a stored value becomes a path segment), and a separate PR would conflict on both files. Acceptance: an entry whose `id` contains `../` or is absolute cannot write outside the output dir through either renderer; one test per renderer.

## Redispatch 2026-09-23: collision blocker fixed

The cold read on draft PR #324 found that sanitizing alone made **distinct unsafe values collide** onto the same output path component — a later entry (or type) silently overwrote an earlier one's export output. Reproduced before fixing: 3 `NoteEntry` + 1 `GenericEntry(type="note_", body="EVIL")` with `BundleStrategy.BY_TYPE` produced `files == ['note.md']` with `"Real 0"` gone; a write-tier user could erase a whole type from a NotebookLM export with the harmless-looking type `note_`.

**Fix:** added `unique_path_component(raw)` to `pyrite/utils/sanitize.py`. A value `sanitize_filename` leaves unchanged ("safe") keeps exactly that name — every existing export's filenames are unaffected. A value `sanitize_filename` had to change ("unsafe") gets a `-<first 8 hex of sha256(raw)>` suffix, so it can never collide with a safe value's name or another unsafe value's sanitized form. Pure function of the raw value — no shared "seen names" set needed across an export.

Applied at all four collision sites named in the redispatch:
- `pyrite/renderers/notebooklm.py` `_bundle_none` and `_bundle_by_type` — filenames now use `unique_path_component`.
- `pyrite/renderers/quartz.py` `export_site` — regrouped by **output folder** (`unique_path_component(entry_type)`), not raw type, so colliding types share one folder, one write pass, and one folder index listing every entry that landed there; the site index links each output folder once. Entry filenames use `unique_path_component`. An entry whose id sanitizes to exactly `index` (colliding with the folder's own `index.md`) is forced through the hash-suffixed path — fixed as a two-line addition, in scope per the redispatch note (pre-existing, same function).
- `pyrite/services/export_service.py` `export_kb_to_directory` — entry filenames use `unique_path_component`. The type-dir collision here was checked and found benign (both types already shared one `mkdir`-idempotent directory with distinct ids; no folder index is written in this function), but the id fix still applies since two distinct ids sanitizing alike would otherwise overwrite.

Also per the redispatch: replaced the `".." not in filename` assertions in `tests/test_notebooklm_renderer.py` (2 traversal tests) with the existing `resolve().is_relative_to(...)` containment assertion already used elsewhere in the same file — `..` stripping is an implementation detail of `sanitize_filename`, not a property worth pinning. `sanitize_filename`'s docstring now notes Windows drive-relative forms (`C:foo`) are not handled. Changelog fragment corrected: safe values keep today's names; unsafe values gain a short hash suffix (previously said "exactly as before" for all unknown types, which was false for values differing only by `_`/`..`).

New tests (red first, confirmed failing against the pre-fix code, then green): `tests/test_sanitize.py::TestUniquePathComponent` (6 tests), `tests/test_notebooklm_renderer.py` (2 collision regression tests), `tests/test_quartz_renderer.py` (4: id collision, type collision, site-index-links-once, index-id-not-overwritten), `tests/test_export_service.py` (2: id collision, type collision). Full suite green at 122 passed for the four touched test files; full `tests/ extensions/ -n 4` run for final verification.
