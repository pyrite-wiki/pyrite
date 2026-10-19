# Upgrade Pyrite

A how-to for someone who runs Pyrite today (scripts, an MCP config, maybe a
server) and is moving to a newer release. One section per release, newest
first. Each says what stops working, what to run and in what order, and how
to tell you are done. The reasons and the full detail are in the
[changelog](../CHANGELOG.md); the rules are in
[JSON contracts](json-contracts.md), which this page links and does not
repeat.

## Upgrading from 0.25.8 to 0.25.9

Three things you do every day change, and every user rebuilds the index once.
The commands below that run Pyrite are run by CI
(`tests/test_doc_agent_contracts.py`) in a scratch HOME, in order, on two
throwaway KBs. Skip the next block if you try the steps on your own KBs; use
your own KB names where the page says `my-kb` and `second`.

<!-- expect-exit: 0 -->
```bash
pyrite init -t research -p ./my-kb --name my-kb --no-examples
pyrite init -t research -p ./second --name second --no-examples
```

### 1. Before you upgrade

If any file in your KBs has no `id:` line, pin its id first, with the version
you have now:
[Pin your entry ids before upgrading](pinning-entry-ids.md). Most KBs have
none.

### 2. Upgrade, then rebuild the index

Pyrite installs from source; from your checkout:

<!-- not-run: installs packages into the environment -->
```shell
git pull
pip install -e ".[all]"
```

This release adds two columns to the index (`link.target_prefix` and
`entry.aliases`), and a row indexed before the upgrade holds neither until it
is rebuilt. Rebuild, then check:

<!-- expect-text: "status": "healthy" -->
```bash
pyrite index build > /dev/null
pyrite index health | grep '"status"'
```

```text
  "status": "healthy",
```

You are done with this step when `status` is `healthy`. A `warning` names its
cause in one of the keys of `index health`. Four are new in this release;
each has a paragraph in [JSON contracts](json-contracts.md) (named `index
health` → the key), which says what it means and what to do:

- `links_stale_kb`: a count of links a rebuild would point at a different KB.
  Step 3 makes one. `pyrite index build` clears it.
- `double_spellings`: a file that writes one field twice. Step 5 makes one.
- `unmodellable_frontmatter`: a file whose frontmatter Pyrite cannot model
  (a duplicate or tagged key); Pyrite will not write to it, body included.
  Fix the file by hand.
- `removed_extensions`: a KB still using the deleted cascade extension. Step 4
  covers it.

### 3. Use the KB's full name, not its shortname

A KB's **shortname** (the `shortname:` key of its entry in `config.yaml`,
shown by `pyrite kb list`) is now link syntax only: `[[mk:some-entry]]` keeps
working, but `-k mk`, REST `kb=mk` (and `/api/kbs/mk/...`) and MCP
`kb_name="mk"` are refused with `KB_NOT_FOUND`. Your scripts and MCP configs
must name the KB. Give `my-kb` a shortname to see the refusal:

```bash
sed -i '/name: my-kb/a\  shortname: mk' ~/.pyrite/config.yaml
pyrite kb list | grep '"shortname"'
```

```text
      "shortname": "mk",
      "shortname": null,
```

An agent that sent `mk` yesterday now meets this, and the answer names the
full name:

<!-- expect-exit: 1 -->
<!-- expect-text: is the shortname of KB -->
```bash
pyrite list-entries -k mk
```

```json
{"did_you_mean": ["my-kb"], "error": "KB 'mk' not found", "error_code": "KB_NOT_FOUND", "retryable": false, "suggestion": "'mk' is the shortname of KB 'my-kb'. A shortname is link syntax only ([[mk:<id>]]); name the KB 'my-kb'."}
```

Send `my-kb` instead. The reference row for the code is
[`KB_NOT_FOUND`](json-contracts.md#write-refusals-create-import-update).

**Rebuild after you add or rename a shortname.** A file that links to
`[[k2:target]]` was indexed while `k2` named no KB. Give `second` the
shortname `k2` and the link now means a different KB, which `index sync` does
not notice in a file it has already read:

```bash
pyrite create -k second -t note --title Target --body "Target." > /dev/null
pyrite create -k my-kb -t note --title Source --body "See [[k2:target]]." > /dev/null
sed -i '/name: second/a\  shortname: k2' ~/.pyrite/config.yaml
pyrite index health | grep -m1 '"links_stale_kb"'
```

```text
  "links_stale_kb": 1,
```

`pyrite index build` clears it:

<!-- expect-text: "links_stale_kb": 0 -->
```bash
pyrite index build > /dev/null
pyrite index health | grep -m1 '"links_stale_kb"'
```

```text
  "links_stale_kb": 0,
```

### 4. Removed: Streamlit, the public site and the cascade extension

- **Streamlit UI (`pyrite/ui`).** The web app that `pyrite serve` serves
  replaced it. Nothing in a KB or a config referred to it;
  `pip uninstall streamlit` if you installed it only for Pyrite.
- **The built-in public site.** `/site`, `/viewer`, the live `/sitemap.xml`
  and `/robots.txt` and `POST /api/site/render` are gone, with the web app's
  "Render Site" button. An instance has no crawlable surface any more. To
  publish a KB, build a static site from its files with Hugo or Astro and
  deploy that on its own. A `site-cache/` directory left under
  `PYRITE_DATA_DIR` is inert and can be deleted.
- **The cascade extension.** Its KB types, entry types, `pyrite cascade`
  commands and MCP tools are gone. Remove the stale entry point in the
  environment that had it:

  <!-- not-run: uninstalls a package -->
  ```shell
  pip uninstall pyrite-cascade
  ```

  A KB registered as `cascade-research`, `cascade-timeline` or
  `cascade-solidarity` is still a KB: its files read and write as generic
  entries, and `pyrite orient -k <kb>` (`notices`) and `pyrite index health`
  (`removed_extensions`) say so, as the
  `orient` → `notices` paragraph of [JSON contracts](json-contracts.md)
  describes. For investigative types install journalism-investigation and set
  `kb_type: journalism-investigation`; to keep a type's folder, declare
  `subdirectory:` for it in `kb.yaml`.

The [changelog](../CHANGELOG.md) lists every removed route and option.

### 5. Behaviour you may notice

- **An update writes only what you named.** A title edit changes the title's
  line and nothing else: comments, key order and quoting stay. See
  [How Pyrite edits your files](how-pyrite-edits-your-files.md).
- **A field written twice is refused.** A file with `status:` and `metadata:
  {status: ...}` is read one way, and an update of that field is refused until
  you remove one spelling:

<!-- expect-exit: 1 -->
<!-- expect-text: the file spells 'status' twice -->
```bash
printf -- '---\nid: twice\ntitle: Twice\ntype: note\nstatus: draft\nmetadata:\n  status: final\n---\nBody.\n' > my-kb/notes/twice.md
pyrite update twice -k my-kb -f status=x
```

```json
{"outcome": "error", "error": "Cannot update status on 'twice': the file spells 'status' twice: `status`, `metadata.status`. A field has one spelling per file; remove one of them by hand, or `pyrite qa fix` merges an equal pair, then update.", "error_code": "VALIDATION_FAILED", "retryable": false}
```

- **A new file starts with `id:`**, and a value that needs quoting is
  double-quoted (`date: "2024-02-03"`; it was single-quoted).
- **Timestamps are checked.** `created_at` and `updated_at` accept only forms
  YAML reads as a date or timestamp, and forms 0.25.8 accepted are now
  refused: the **Timestamp input**
  paragraph of [JSON contracts](json-contracts.md) lists both.
- **`batch-read` and an unknown KB** differs by surface:
  [`batch-read` and a KB it cannot resolve](json-contracts.md#batch-read-and-a-kb-it-cannot-resolve).
