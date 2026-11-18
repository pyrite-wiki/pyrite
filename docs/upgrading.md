# Upgrade Pyrite

A how-to for someone who runs Pyrite today (scripts, an MCP config, maybe a
server) and is moving to a newer release. One section per release, newest
first. Each says what stops working, what to run and in what order, and how
to tell you are done. The reasons and the full detail are in the
[changelog](../CHANGELOG.md); the rules are in
[JSON contracts](json-contracts.md), which this page links and does not
repeat.

## Upgrading from 0.26.3 to 0.26.4

- **A software KB's `board.yaml`** that 0.26.3 read with an extra or misspelt
  key, or with any shape the changelog lists for #1538, now refuses the tools
  that read the board until the file is fixed. What the file may contain is in
  [plugins: board.yaml](plugins.md#boardyaml-lanes-wip-limits-and-gates).
- **A `judgment` or `agent_responsibility` criterion that carries `checker` or
  `params`** is now refused by the tools that read the board (0.26.3 loaded it
  and never ran the checker). Remove the key, or drop `type` to make the
  criterion a checker.
- **Exit codes that scripts match on change (#1563, #1564).** `pyrite ci
  --tier` and `pyrite qa assess --tier` out of range is now `USAGE_ERROR`,
  exit 2, where it was `INVALID_TIER`, exit 1. REST and MCP still answer
  `INVALID_TIER`. `qa assess --max-age` below 0 is refused.
- **`pyrite index sync` and `index build` exit 3 (1 when nothing synced)**
  where 0.26.2 and 0.26.3 exited 0, when a registered KB is unreadable, its
  `kb.yaml` does not load, or a folder cannot be listed (#1542, #1546, #1547).
  `pyrite kb reindex` does the same for a folder or file it cannot reach, and
  `pyrite qa duplicates` exits 3 when it leaves a KB out. `pyrite index
  health` is unhealthy (exit 1) for `unreadable_folders`, `unreadable_kbs`
  and `schema_errors` rows. Those rows use a new error code,
  `KB_CONFIG_INVALID`, for a `kb.yaml` that does not load. A bad `kb.yaml`
  now stops only its own KB.
- **`importance` must be a whole number from 1 to 10 on every entry type and
  every surface (#1537, #1588).** `create -f importance=10.7`, `=high` and
  `=true` are now refused, where they stored 10, 5 and 1; `update -f
  importance=5.5` is refused; a CSV import row with a bad cell is refused and
  the other rows are imported. REST answers 400 `VALIDATION_FAILED` where
  `POST` and `PUT` answered 422. A value already in a file is kept, with a
  warning. In a journalism-investigation KB the refusal is
  `VALIDATION_FAILED`, no longer `SCHEMA_VIOLATION`.
- **Numbers read as numbers, and `metadata` as an object (#1503).** From
  0.26.4 a field declared `number` reads as a number, and one declared
  `checkbox` as a boolean, on every reader, for values written from then on;
  a task's `priority` reads as a number on `kb_get` for old files too. A value
  already in a file is not re-read: a quoted `"7"` in a number field still
  reads as text, and `1` in a checkbox field still reads as an integer, after
  `index sync` and `index build`, until you write that field yourself (an
  update of another field leaves it as written). `pyrite qa validate` reports
  the quoted number as `number_as_text` and the `1`/`0` in a checkbox field as
  a `schema_violation` ("expected boolean, got int"). MCP `kb_find_*` and
  `kb_search`, `search --fields '*'` and REST search return `metadata` as an
  object, not JSON text, and `aliases` as a list: a client that ran
  `json.loads` on `metadata` must stop.
- **REST `PATCH` and `PUT` answer 422, not 400,** when an update would produce
  an entry that cannot be loaded (#1472); the message says the file is
  unchanged.
- **A folder named `_Templates` (any letter case) is skipped by the index walk
  (#1552).** Its entries lose their index rows at the next sync, and `pyrite
  index health` names its files that state an `id:` under
  `unlisted_entry_files`. Rename the folder, or move the entries out, before
  you sync. After a rename, `pyrite index health` reports the moved entries
  under `subdirectory_mismatches` (a warning): run `pyrite index reconcile
  <kb> --apply` to move them into their type's subdirectory.
- **A create stores the type you named (#468).** In software, zettelkasten,
  encyclopedia and social KBs, `-t note`, `document` and `person` are no
  longer stored as `adr`, `literature_note`, `article`, `writeup`,
  `design_doc` or `user_profile`. Entries already on disk keep their type. An
  out-of-tree plugin that relied on the old inference must declare
  `get_type_mappings()`. In a journalism-investigation KB, `-t event` and
  `-t document` now store `investigation_event` and `document_source` instead
  of being refused (#1526); `--allow-undeclared` no longer keeps `event`.
- **An existing known-entities KB must change `guidelines: <text>` to
  `guidelines: {canonical_records: <text>}` in its `kb.yaml`** (#468);
  otherwise `kb_schema` fails with `INTERNAL`. Pyrite does not edit the file.
- **The `research-queue` template is new** (`pyrite init -t research-queue`,
  #1663); it changes nothing for an existing KB.
- **`pyrite orient --format markdown` and `csv` are usage errors** (exit 2,
  #1501). `pyrite search --format rich` has an `ID` column and no fixed
  widths (#1519): this matters only if you screen-scrape it.
- **Typer 0.29 is allowed (#1432).** The wording of an out-of-range message
  changes; the exit code stays 2.
- **`orient` has new `kb_authored` and `pyrite_guidance` blocks (#1369).** The
  mixed keys they replace are deprecated; see "Still deprecated" below.
- **On Python 3.11.5 or newer, run `pip install -U "soupsieve>=4.0.0"`** (or
  `uv sync`); on 3.11.0 to 3.11.4, upgrade Python (#1536).
- **`pyrite delete` no longer removes a symlink to the entry;** it names the
  link in `links_left` (#1262). A dangling in-KB link is a `dangling_links`
  warning, no longer an unreadable file.
- **Smaller new refusals:** `kb create --ttl` below 0 (#1407), `wiki stubs
  --limit` below 1 (#1560), `sw` list `--limit` or `--offset` below 0 (#1565),
  an invalid `--sort`, `--sort-by` or `--sort-order` (#1387), and `qa assess
  --entry <missing>`, which exits 1 with `ENTRY_NOT_FOUND` (#1499).

## Upgrading from 0.26.2 to 0.26.3

The steps are in the [0.26.3 changelog](../CHANGELOG.md) under **Upgrade
notes**: a private repository reached over REST or MCP needs a
`git_credentials` entry, a proxy needs `git_proxy`, `git_identity` replaces
the global-identity fallback, and an invalid `default_role` stops the config
load. The settings are in
[configuration: Upgrading to 0.26.3](configuration.md#upgrading-to-0263-network-git).

## Upgrading from 0.25.8 to 0.26.0

Three things you do every day change, and every user rebuilds the index once.
The commands below that run Pyrite are run by CI
(`tests/test_doc_agent_contracts.py`) in a scratch HOME, in order, on two
throwaway KBs. Skip the next block if you try the steps on your own KBs; use
your own KB names where the page says `my-kb` and `second`.

**Demonstration blocks.** Steps 3 and 5 contain blocks marked
"Demonstration on the scratch KBs: do not run on your own". They append
shortnames to `~/.pyrite/config.yaml` and write junk entries so the page can
show the refusal. Read them; run them only on the scratch KBs.

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
cd web && npm ci && npm run build && cd ..
```

`pyrite serve` serves the gitignored `web/dist`, so the old build keeps
calling `/api/site/render`, which step 4 says is gone, until you rebuild it.
**Restart `pyrite serve` and your MCP clients** afterwards: a running process
keeps the old code. Installed from a release tag instead of a checkout?
Reinstall it:

<!-- not-run: installs packages into the environment -->
```shell
pip install --force-reinstall "pyrite[server,cli] @ git+https://github.com/pyrite-wiki/pyrite@v0.26.0"
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

You are done with this step when `status` is `healthy`. Anything else names
its cause in a key of `index health`. The **`index health` keys** table in
[JSON contracts](json-contracts.md) lists every key, whether it makes the status
a `warning` or `unhealthy` (exit `1`), and what to do. Count nothing by hand; read
that table. The keys this release added include:

- `links_stale_kb`: a count of links a rebuild would point at a different KB.
  Step 3 makes one. `pyrite index build` clears it.
- `double_spellings`: a file that writes one field twice. Step 5 makes one.
- `unmodellable_frontmatter`: a file whose frontmatter Pyrite cannot model
  (a duplicate or tagged key); Pyrite will not write to it, body included.
  Fix the file by hand.
- `removed_extensions`: a KB still using the deleted cascade extension. Step 4
  covers it.
- `unreadable_files` and `orphaned_entries`: these two are **failures**, not
  warnings. They make the status `unhealthy` and the exit code `1`.

**An unreadable file now fails the check.** On 0.25.8 a file with an encoding
or permission failure was listed under `malformed_frontmatter`, a warning with
exit `0`. It is now in `unreadable_files`, so a script that ran `pyrite index
health` and ignored a warning starts to fail. The fix is to repair the file
(its bytes, or its permissions), then `pyrite index sync`; see the **`index
health` file findings** paragraph of [JSON contracts](json-contracts.md).
*Demonstration on the scratch KBs: do not run on your own.*

<!-- expect-exit: 1 -->
<!-- expect-text: "status": "unhealthy" -->
```bash
printf -- '---\nid: bad\ntitle: Bad\ntype: note\n---\n\xff\xfe not UTF-8\n' > my-kb/notes/bad.md
set -o pipefail
pyrite index health 2>/dev/null | grep -m1 '"status"'
```

```text
  "status": "unhealthy",
```

Remove the file and the index is `healthy` again:

<!-- expect-text: "status": "healthy" -->
```bash
rm my-kb/notes/bad.md
pyrite index health | grep '"status"'
```

```text
  "status": "healthy",
```

### 3. Use the KB's full name, not its shortname

A KB's **shortname** (the `shortname:` key of its entry in `config.yaml`,
shown by `pyrite kb list`) is now link syntax only: `[[mk:some-entry]]` keeps
working, but `-k mk`, REST `kb=mk` (and `/api/kbs/mk/...`) and MCP
`kb_name="mk"` are refused with `KB_NOT_FOUND`. Your scripts and MCP configs
must name the KB. Give `my-kb` a shortname to see the refusal. *Demonstration on the scratch KBs: do not run on your own.*

```bash
perl -pi -e '$_ .= "  shortname: mk\n" if /name: my-kb$/' ~/.pyrite/config.yaml
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
not notice in a file it has already read. *Demonstration on the scratch KBs: do not run on your own.*

```bash
pyrite create -k second -t note --title Target --body "Target." > /dev/null
pyrite create -k my-kb -t note --title Source --body "See [[k2:target]]." > /dev/null
perl -pi -e '$_ .= "  shortname: k2\n" if /name: second$/' ~/.pyrite/config.yaml
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
  you remove one spelling. *Demonstration on the scratch KBs: do not run on your own.*

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

Beyond those, these changes turn a call that answered with exit `0` or a
different shape into one that refuses or reads differently. They are grouped by
who calls. Each bullet says what to do; the rule itself is in the page it links.

#### Scripts and CI (CLI)

- **An unknown KB is refused everywhere.** `tags`, `timeline`, `recent`,
  `list-entries`, `task list`, `qa validate`, `index build|sync|embed`, `sw
  backlog` and other commands used to answer an empty result with exit `0` for
  a KB name that is not a KB. They now answer `KB_NOT_FOUND`, exit `1`. A script
  that typed the name wrong now fails; fix the name. *Demonstration on the
  scratch KBs: do not run on your own.*

<!-- expect-exit: 1 -->
<!-- expect-text: KB_NOT_FOUND -->
```bash
pyrite tags -k no-such-kb
```

The reference row is [`KB_NOT_FOUND`](json-contracts.md#write-refusals-create-import-update).
- **`pyrite ci` fails when it validated nothing.** With no KB in `config.yaml`
  or the registry it exits `1` with `NO_KBS_TO_VALIDATE`; it used to print
  `pass` and exit `0`. It also validates a KB added with `pyrite kb add`, and
  reports an `index_out_of_date` error for one whose files the index does not
  match (added and not yet indexed). Run `pyrite index sync -k <kb>` before
  `ci`. The code is in the [Exit codes](json-contracts.md#exit-codes-cli) table.
- **`pyrite ci` and whole-KB QA run the named rubric checkers.** The checkers a
  KB names in `kb.yaml`, which single-entry validation already ran, now run over
  every entry too, and a missing or broken checker binding is reported instead
  of passing silently. A KB whose declared checks were never run across all its
  entries can start failing `pyrite ci` with no change to its files: run
  `pyrite qa validate <kb>` to see the findings before you upgrade CI (#1143).
  To adopt checks gradually, set `rubric_action: warn`
  in `kb.yaml` or `action: warn` on individual rubric items; `pyrite ci` then reports
  named findings without failing on them; existing bulk warnings still fail (#1166).
- **An unknown `--mode` is refused.** `pyrite search --mode zzz` and `links
  discover|batch-suggest --mode zzz` used to search as keyword and exit `0`.
  They exit `1` with `VALIDATION_FAILED` (REST: `400`); the valid modes are
  `keyword`, `semantic` and `hybrid`. *Demonstration on the scratch KBs: do not
  run on your own.*

<!-- expect-exit: 1 -->
<!-- expect-text: Valid modes -->
```bash
pyrite search x -k my-kb --mode zzz
```
- **`index embed` exits `1` when nothing could be embedded.** If the model
  cannot load, the entries stay owed an embedding and the command says so;
  `pyrite index embed` retries them. [Getting started](getting-started.md)
  describes the offline case.
- **A FIFO, a device or an empty path is refused as an input file.**
  `--body-file` and the other file options answer `VALIDATION_FAILED`, exit
  `1`, before any work.
- **Backlink and outlink counts change.** The index keeps one link row per link
  a file writes, `![[x]]` is a `transclusion` rather than a `wikilink`, and a
  row carries the `prefix` it was written with. Step 2's rebuild refreshes the
  rows; a script that counts them will count more. See [Backlink
  rows](json-contracts.md#backlink-rows).
- **`pyrite extension install --verify`** prints its warnings on stderr in text
  mode too, and exit `3` means the extension is installed but not verified
  (verification failed, or it declares no `pyrite.plugins` entry point).

#### Agents (MCP)

- **`kb_read_body` and `kb_delete` answer a typed code.** A missing entry is
  `ENTRY_NOT_FOUND` and a missing KB is `KB_NOT_FOUND`, as `kb_get` and
  `kb_update` already did. The code the tool used to send stays in
  `legacy_error_code` until 0.27. An agent that matched `NOT_FOUND`
  should match `error_code`; see [MCP code transitions](json-contracts.md#mcp-code-transitions).
- **A single-entry read puts a declared field at its own key.** `pyrite get`,
  `kb_get` and REST `GET /api/entries/{id}` used to leave some declared fields
  under `metadata`. A field the type declares (a task's `evidence` or
  `status_reason`, a backlog item's `rank`) is now at the top level, and
  `metadata` holds only keys the type does not declare. A client that read
  `metadata.evidence` must read `evidence`. See the [Entry
  envelope](json-contracts.md#entry-envelope).
- **`zettel_graph` caps `depth` at 3** (`1` to `3`; anything else answers `{"error":
  "depth must be between 1 and 3"}`), and keys a neighbour that lives in another
  KB as `kb:id` instead of the bare id.

#### REST clients

- **`PUT /api/kbs/{name}` changes only what you send.** An omitted field is
  left alone, `description: null` clears the description, and a key the route
  does not take (a typo, or `default_role`) or a blank `kb_type` is refused with
  `VALIDATION_FAILED` and nothing written. On a KB defined by hand in
  `config.yaml`, a metadata edit answers `409` `KB_DEFINED_IN_CONFIG`; edit
  `config.yaml` and restart instead.
- **`POST /api/entries` refuses a managed field under `metadata`** (a task's
  `status_change_log`, an ADR's `adr_number`, `file_path`) with
  `VALIDATION_FAILED`. See the `VALIDATION_FAILED` row of [JSON
  contracts](json-contracts.md#write-refusals-create-import-update).
- **Invite codes need a session cookie.** `POST/GET /auth/invite-codes` and
  `DELETE /auth/invite-codes/{code}` take a signed-in admin's `pyrite_session`
  cookie and nothing else; an API key is refused with `401`. How to make a code
  is in [Invite someone](web-ui.md#invite-someone).

### 6. Edit your kb.yaml

Two existing KBs need a `kb.yaml` edit; a new KB made by `pyrite init` already
has it.

- **A KB made from the `research`, `intellectual-biography` or `movement`
  template:** under `types.person.optional`, rename `affiliation` to
  `affiliations`. A create now writes every field the type declares, so until
  you rename it each new `person` file carries both `affiliations: []` and
  `affiliation: ""`.
- **A KB made from the `software` or `zettelkasten` template:** declare the
  `task` type and make a `tasks/` folder, or `pyrite task create` fails. Copy
  the `task` entry from a KB that `pyrite init -t software` makes today
  (`description`, `required: [title]`, `optional`, `subdirectory: tasks/`).

### 7. For plugin authors

- **`KBRepository.save` only creates.** An entry that already exists is refused
  with `ENTRY_EXISTS`. Update through the service, which edits the file's own
  text.
- **`PyriteConfig.kb_name_for` and `get_kb_by_shortname` are gone.** The one
  lookup is `readable_kb_named` in `pyrite/services/kb_resolver.py`, which takes
  a full name only (step 3).
- **A `before_save` hook can refuse an update but its changes no longer reach
  the file.** The hook contract is in [Writing a
  plugin](tutorials/plugin-writing.md).

### 8. Still deprecated

These interfaces remain supported in 0.26.0 and are scheduled for removal in
0.27. CLI aliases warn on stderr; response fields remain in responses until
removal. `db.conn` emits a `DeprecationWarning`, and `FRONTMATTER_ALIASES` logs
a warning once per class.

- **`-f` as `--format`** on the `task` commands leaves in 0.27. Use
  `--format`, or set `PYRITE_FORMAT`.
- **`pyrite export --format` (and `-f`)** leaves in 0.27. Use `--target`;
  the only target today is `notebooklm`.
- **MCP `legacy_error_code`** leaves in 0.27. Match `error_code` instead.
- **Search `distance`** leaves in 0.27. Read `similarity` instead.
- **`task status`** leaves in 0.27. Use `task get` instead.
- **`db.conn`** leaves in 0.27. Use `db.session` for writes or
  `db.execute_sql()` for read-only queries.
- **`FRONTMATTER_ALIASES`** leaves in 0.27. Declare `field_aliases` in the
  type's schema instead.
- **`pyrite sw standards`** leaves in 0.27.0. Use the validations and
  conventions commands instead.
- **The global git identity for network commits** leaves in 0.27.0. With
  `git_identity` unset, commits made for network callers use the server
  account's global `user.name` and `user.email`; set `git_identity`
  (`name`, `email`) in `config.yaml` (added in 0.26.3).
- **orient's `schema.guidelines`**, `schema.goals`, `schema.evaluation_rubric`,
  the per-type `ai_instructions`, `guidelines`, `goals` and
  `evaluation_rubric` under `schema.types`, and the top-level `guidelines`
  (always `{}`) leave orient in 0.27. Read `kb_authored` (the KB's own text,
  as written) and `pyrite_guidance` (Pyrite's defaults) instead; added in
  0.26.4 (#1369).

The table in [JSON contracts](json-contracts.md#format-defaults) says which
commands take `--format`.
