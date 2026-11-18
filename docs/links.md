# Connect entries and repair broken links

This walkthrough uses a registered, writable `test-kb`. For output shapes and
exit-code contracts, see [JSON contracts](json-contracts.md). Linking is an
explicit file change; discovery only proposes candidates.

## Link two entries and find backlinks

```bash
pyrite create -k test-kb -t note --title Alpha --body "Evidence [[missing]]"
pyrite create -k test-kb -t note --title Beta --body "A supported claim"
pyrite link alpha beta -k test-kb --relation supports --bidi
pyrite backlinks beta -k test-kb
```

`--bidi` writes the reverse relationship too. Without it, the forward link
still appears in backlinks; a backlink is derived and does not require a
second written link. In entry bodies, `[[id]]` is a wikilink and `![[id]]`
is a transclusion. A relation's meaning is separate from whether the target
currently exists.

## Markdown links to entry files

An ordinary Markdown link to another entry's file is a link too, so a KB
written by hand or by another editor needs no wikilinks:

```bash
pyrite create -k test-kb -t note --title Gamma --body "See [Beta](beta.md#Claim)"
pyrite backlinks beta -k test-kb
```

Beta's backlinks now list Gamma with relation `markdown_link` (the reverse,
`markdown_linked_by`). The path is relative to the linking file's folder, as
a Markdown viewer reads it. It may carry a `#fragment`, `<angle brackets>`, a
title or `%20`, or be a reference-style link (`[Beta][b]` with `[b]: beta.md`).
The link follows the file, not its name: if Beta's id changes while the file
stays, the link names the new id; if the file moves or is deleted, the link is
broken. A path through a symbolic link inside the KB names the file it leads
to.

These are not entry links: a URL, a `#fragment` alone, an image, a link inside
code, a link to the file itself, a root-relative or absolute path such as
`/x.md` (the site's root may not be the KB's), a path that leaves the KB, and a
path the index walk does not list (`README.md`, `_templates/`, hidden folders,
other suffixes). The rule is about the path, not about what is in the file: a
listed `.md` file that is not indexed as an entry (its frontmatter does not
load, or another file wins its duplicate id) is a broken link.

A link whose path has no suffix (`beta`, not `beta.md`) counts only when
`beta.md` exists when the linking file is indexed; a target created later is
seen the next time the linking file is read. Likewise, a link through a
symbolic link names the file the symbolic link pointed at when the linking
file was indexed: point the symbolic link elsewhere and the link keeps the old
file until the linking file is read again. A `.md` link with nothing at its
path is a broken link, which `links check` reports under the path. The path is
compared as written: on a case-insensitive or normalising filesystem, a link
spelled `A.md` to the file `a.md` is reported broken.

## Check broken targets

Alpha's example body deliberately names a missing entry. The check below
returns its findings and exits1, so handle that exit in `set -e` automation.
Repair the target spelling or create the intended entry; checking itself does
not repair files.

<!-- expect-exit: 1 -->
```bash
pyrite links check -k test-kb
```

## Link many entries

Save this as `links.yaml`. Source and target are required. Quote IDs such as
`"2024"` or `"yes"` so YAML does not turn them into numbers or booleans.

```yaml
- source: alpha
  target: beta
  relation: references
- source: alpha
  relation: supports
```

A dry run shows the plan without changing entries. Each bad specification is
reported independently; the missing target in this example makes both runs
partial, exit3. In the real run, valid specifications are still written.

<!-- expect-exit: 3 -->
```bash
pyrite links bulk-create links.yaml -k test-kb --dry-run
```

<!-- expect-exit: 3 -->
```bash
pyrite links bulk-create links.yaml -k test-kb
```

## Find candidates and one-way links

```bash
pyrite links suggest alpha -k test-kb
pyrite links discover alpha -k test-kb --mode keyword --include-empty
pyrite links batch-suggest --source-kb test-kb --target-kb test-kb --mode keyword --include-empty
pyrite links asymmetric -k test-kb
pyrite links orphans -k test-kb
```

`discover` and `batch-suggest` normally omit entries with neither body nor
summary; `--include-empty` includes them. Suggestions are not automatic links.
Asymmetric links and orphans are review aids, not instructions to add every
possible edge.

## Link across KBs

A body prefix in `[[other-kb:id]]` may be the full KB name or its `shortname`.
A frontmatter link's `kb:` field resolves the same way. Resolution only sees
KBs the reader may read. In CLI options such as `-k` and `--target-kb`, use
full registered names: shortnames are link syntax, not alternate KB names.
See the [KB_NOT_FOUND contract](json-contracts.md#error-shape).

After adding or changing a shortname, run an index build: an incremental sync
does not reread an unchanged source file. `links_stale_kb` in index health
helps identify this condition. For a cross-KB CLI link, pass the source KB with
`-k` and the full destination name with `--target-kb`.
