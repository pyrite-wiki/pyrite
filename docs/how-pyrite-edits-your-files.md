# How Pyrite edits your files

A knowledge base is a folder of Markdown files with YAML frontmatter, kept in
git. You can edit those files by hand, with Hugo, with a script or with any
other tool, and Pyrite should be one more such tool. When Pyrite writes to one
of your files, it changes what it was asked to change and nothing else:

- an edit to one field changes that field's lines; your comments, key order,
  quoting, blank lines, list style and line endings stay as you wrote them;
- a write that changes nothing writes nothing, so `git diff` stays empty;
- a save hook may refuse a write, but it does not add to it;
- if the file changed after you read it, a write that would overwrite that
  change is refused, and the change is kept;
- a file keeps its identity when you edit it, with or without an `id:` line.

The design behind this is [ADR-0042](../kb/adrs/0042-a-write-changes-what-was-asked-and-nothing-else.md).

## Reading the examples

Each example below is three blocks:

- `file`: a file as it sits in your KB before the edit;
- `update`: the edit sent to Pyrite. The spelling of the operations (`set`,
  `append`, `replace`) is illustrative: Pyrite's write interfaces are alpha,
  and each surface (the CLI, the MCP server's `kb_update`, the REST API)
  spells them its own way;
- `diff`: every line the edit changes in the file, `-` for a line removed and
  `+` for a line added. An empty `diff` means the file is byte for byte as it
  was.

A word after `file` sets up the situation: `crlf` means the file uses Windows
line endings; `concurrent` means two writers start from the same file at the
same time; `stale` means the file is edited by hand between the read and the
write; `noid` means the file has no `id:` line. After `update`, `echo` means
the edit sends back exactly what a read of the entry returned.

These examples are run, not just read: `tests/test_doc_write_as_patch.py`
writes each `file` into a fresh KB, sends the `update` through the MCP
server's `kb_update`, and checks the file's diff against the `diff` block.

A passing example has to be passing for the reason it names, so some examples
carry controls the page does not show:

- the stale example also replaces with the hash of a re-read after the hand
  edit, in the same KB, and expects that write to happen: a refusal because
  the hash is stale must not look like a refusal because the file changed;
- the hook example reads the actor's link from the backlinks or from a
  derived key of a read, never from a `links:` block in your file;
- an example that watches the YAML emitter checks that the watch covers the
  emitter the update itself uses, not only the one a create uses.

## Where Pyrite does not keep this promise yet

Today an update re-renders the whole frontmatter from Pyrite's model of the
entry, two writers can overwrite each other, and a file with no `id:` is known
by its title, so the examples below fail. Until that is fixed, keep your KB in
git and read `git diff` after Pyrite writes. The test module lists every
example that fails, in `KNOWN_DIVERGENCES`, with what Pyrite does instead, and
marks it as an expected failure. When the write path is fixed and an example
starts to pass, the test suite fails until its mark is removed, so the list
only shrinks.

## One field edit yields that field's lines

Changing the title changes the title's line, and keeps its trailing comment.
The comment at the top, the blank line, the inline tag list, the indented
first line of the body and `flag: no` are left exactly as they were.

```file
---
# reviewed by hand, do not reorder
id: field-notes
title: Field notes   # working title
type: note

tags: [alpha, beta]
links:
- target: other-entry
  relation: related
flag: no
---

    an indented first line

Text.
```
```update
{"set": {"title": "Field notes, second draft"}}
```
```diff
-title: Field notes   # working title
+title: Field notes, second draft   # working title
```

## Appending to a list leaves the existing items alone

A link written by hand in an older spelling stays as it was; the new link is
added after it, in the spelling Pyrite writes.

```file
---
id: legacy-links
title: Legacy links
type: note
links:
- to: foo          # legacy spelling, hand written
  type: related
  since: 2020
---

Text.
```
```update
{"append": {"links": {"target": "bar", "relation": "related"}}}
```
```diff
+- target: bar
+  relation: related
```

## A save does not add what a hook used to add

A `timeline_event` in a KB with the plugin installed that resolves its actors.
The plugin may check the entry, and may refuse a save; it does not write
links into your file that you did not ask for.

```file
---
id: hearing
title: The hearing
type: timeline_event
date: 2026-01-01
actors: ["[[jane-doe]]"]
---

Text.
```
```update
{"set": {"title": "The hearing, postponed"}}
```
```diff
-title: The hearing
+title: The hearing, postponed
```

## Two writers on different keys both survive

Two writers start from the same file at the same moment: one changes the
summary, the other the importance. Both changes are in the file afterwards.

```file concurrent
---
id: shared
title: Shared
type: note
summary: one
importance: 3
---

Text.
```
```update
[{"set": {"summary": "two"}}, {"set": {"importance": 5}}]
```
```diff
-summary: one
+summary: two
-importance: 3
+importance: 5
```

## Sending a read back changes nothing

A tool that reads an entry and sends it straight back has asked for no
change, so the file is not written: the CRLF line endings, the quotes, the
comments and the date stay as they are.

```file crlf
---
# header comment
id: echo-me
title: "Quoted title"
type: note
date: 2026-01-15
tags: [alpha, beta]  # trailing
---

Body.
```
```update echo
{}
```
```diff
```

## A stale whole-document replace is refused

You read the entry, then someone edits the file by hand, then you send back a
whole new version of it, carrying the hash of what you read. Pyrite refuses
the write, says the file changed and to read it again, and the hand edit is
intact.

```file stale
---
id: stale-me
title: Stale me
type: note
status: draft
---

Text.
```
```update stale
{"replace": {"title": "Stale me, renamed", "status": "draft"}, "content_hash": "<hash at read>"}
```
```diff
```

## An id-less file keeps its identity when its title changes

A file with no `id:` line is known by its path, here `posts/intro`. Renaming
its title does not change that: reading `posts/intro` still returns it, and
no `id:` line is added.

```file noid
---
title: Intro
---

Text.
```
```update
{"set": {"title": "Introduction"}}
```
```diff
-title: Intro
+title: Introduction
```
