# Pin your entry ids before upgrading

A file's `id:` is part of Pyrite's contract with it. A file with no `id:`
line still works today: Pyrite gives it an id made from its title (`title:
Meeting Notes` is `meeting-notes`), and every link and `pyrite get` uses that
id. A coming release changes that rule: a file with no `id:` will be known by
its path instead (ADR-0042). So that nothing which links to such a file
changes, **write each file's current id into it before you upgrade.**

Two commands do it. Each id-less file gains exactly one line, `id: <the id it
has today>`, as the last line of its frontmatter. No other byte of any file
changes: line endings, comments, key order, quoting and body stay as they
are. Most KBs have no such files; step 1 tells you.

The commands below use a KB named `notes`; use your own KB's name (`pyrite kb
list` shows them). Run them from your KB's folder, with the version you have
now installed.

## 1. List the files with no id

<!-- expect-exit: 3 -->
```bash
pyrite ids missing -k notes
```

Each line is a file's path and the id it has today. The command exits `0`
when every file has an id (you are done: upgrade) and `3` when some do not.
It also lists files it could not read (invalid YAML, no frontmatter), which
it never changes; fix those by hand.

If two files have one id, they are listed as a **collision**. Only one of
them is reachable today: the other is shadowed. Step 3 is for those.

## 2. Read the plan, then pin

<!-- expect-exit: 3 -->
```bash
pyrite ids pin -k notes --dry-run
```

The dry run writes nothing. It shows each file and the line it would gain,
each collision you need to settle, and each file it would leave alone and
why. Its exit code is the one the real run would have: `3` here, because
this KB has a collision. Pin everything else:

<!-- expect-exit: 3 -->
```bash
pyrite ids pin -k notes
```

Files in a collision are left untouched until you choose; everything else is
pinned, and the index is synced.

## 3. If two files share an id, choose which keeps it

Keep the id on one file and give each other one a new id with `--rename
<path>=<new-id>` (the path as `ids missing` printed it). The renamed file was
never reachable under the old id, so nothing that links to the id changes.

<!-- expect-exit: 0 -->
```bash
pyrite ids pin -k notes --rename archive/meeting-notes.md=meeting-notes-2019
```

A new id must be a plain file name (no `/`, no leading `.`) that no other
file holds. When a file already *states* the shared id in its own `id:`
line, it keeps it: every id-less file in that group needs a `--rename`.

## 4. Check, commit, upgrade

<!-- expect-exit: 0 -->
```bash
pyrite ids missing -k notes
git diff --stat
```

`ids missing` now exits `0`, and `git diff --stat` shows one added line per
file. Read the diff, commit it, then upgrade Pyrite and rebuild the index:

<!-- expect-exit: 0 -->
```bash
git commit -qam "Pin entry ids before upgrading Pyrite"
pyrite index build -k notes
```

## For agents

Every command above takes `--format json` and returns the same as data:
`ids missing` gives `missing` (`path`, `id`, `status`), `collisions` (`id`,
`claimants` with `path` and `explicit`) and `skipped` (`path`, `reason`);
`ids pin` gives `pinned`, `refused`, `skipped`, `dry_run` and `synced`.
Branch on the exit code: `0` done, `3` files are left (the output says which
and why), `1` an error such as an unknown KB or an invalid `--rename`, in
which case nothing was written.

## What the commands never do

- Rewrite a file. The pin is one inserted line; before writing, Pyrite reads
  the result back and refuses the file if anything other than the new `id`
  would change (frontmatter written as one `{...}` mapping, or indented, is
  refused this way: add the line by hand).
- Choose between two files that share an id.
- Fill in an empty `id:` line (`id: ''`); it is reported, for you to edit.
- Overwrite a file that changed after it was read; run the command again.
- Touch files Pyrite does not index: `README.md`, hidden files and folders,
  `_templates/`.
