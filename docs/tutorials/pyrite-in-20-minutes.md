# Pyrite in 20 minutes

You will ask Pyrite about itself, then have an agent research a topic into a knowledge base of your own, then see how to build a third one. By the end you will know what Pyrite is for: **knowledge that agents write and that a person can trust**, kept as plain files you can read, diff and keep without Pyrite.

Pyrite is alpha. This tutorial uses only what the project lists as supported: files, the index, search, the CLI and the core MCP tools. One step uses something experimental, and says so where it does.

<!-- This page is a test. scripts/run_tutorial.py runs every bash block below in order, in a throwaway HOME, and checks the answers the page promises (the "runner:" comments say which). Steps marked "Ask Claude" cannot run without a person and an agent; the commands after them check the result. -->

**You need:**

- macOS or Linux, `git`, and Python 3.11 or newer. `python3 --version` tells you; the Python that ships with macOS is older, so use Homebrew's (`brew install python`).
- [Claude Code](https://docs.claude.com/en/docs/claude-code) for the steps marked **Ask Claude**. Any MCP client works the same way, but only Claude Code is covered here. Without an agent you can still do every command step, and you will see what the agent would have been shown.
- About 2 GB of disk. Most of it is the local embedding model's libraries.

Commands you type are in `bash` blocks. Things you say to Claude are in `text` blocks under an **Ask Claude** heading. Stay in one terminal tab, in the tutorial folder, until the page tells you to open a new one.

## Act one: ask Pyrite about itself

### Install Pyrite

There is no PyPI package yet, so install from git, into a folder of its own.

```bash
mkdir pyrite-tutorial && cd pyrite-tutorial
```

```bash
git clone https://github.com/pyrite-wiki/pyrite.git
python3 -m venv pyrite/.venv
source pyrite/.venv/bin/activate
pip install -e "./pyrite[cli,semantic]"
```

`[cli,semantic]` is the smallest set that does everything here. `[all]`, which the README uses, adds the web server and the developer tools; you can add them later.

```bash
pyrite --version
```

You should see a version number. If the shell says `command not found`, you are in a new terminal: run `source pyrite/.venv/bin/activate` from the tutorial folder first. You will need that line every time you open a new terminal.

### Get some knowledge bases

A knowledge base (a KB) is a folder of Markdown files with a little YAML at the top of each, plus a `kb.yaml` that says what types of entry it holds. Pyrite's own project knowledge lives in one: `pyrite/kb/`, with its design page, its architecture decision records (ADRs), topic maps and standards.

For something to read it against, there are 26 more, mostly written about the thinkers whose ideas Pyrite's design and process draw on: Goldratt, Reinertsen, the Poppendiecks, Deming, Toyota's production system and others. They are in a separate repository, under a CC BY-SA 4.0 licence.

```bash
git clone https://github.com/pyrite-wiki/pyrite-kb-demo.git
```

Tell Pyrite where all of them are. Use `--format rich` here: in the default format this command lists what it found and registers nothing ([#660](https://github.com/pyrite-wiki/pyrite/issues/660)).

```bash
pyrite kb discover pyrite-kb-demo --add --format rich
pyrite kb discover pyrite/kb --add --format rich
```

You should see a table of KBs marked `New`, and `Added 26 KB(s) to registry` and then `Added 1 KB(s) to registry`.

### Build the index

Pyrite does not search your files directly. It builds an index (a SQLite file under `~/.pyrite/`) from them, and the index can be deleted and rebuilt at any time because the files are the source of truth. The same command also builds the embeddings that semantic search uses. The first time, it downloads a small embedding model (about 90 MB).

```bash
pyrite index build
```

This reads about 5,300 entries and writes their embeddings; on the machine this page was written on it took a little over two minutes, the download included. You will see a progress bar per KB, then `Generating embeddings...` and a count of the entries embedded.

### Search from the terminal

```bash
pyrite search "andon" -k tps --fields id,title
```

You should see the `andon` entry first: Toyota's cord that lets any worker stop the line. Keyword search matches the words you give it. Semantic search matches what you mean:

```bash
pyrite search "too much going on at once slows everything down" -k reinertsen --mode semantic --fields id,title -n 5
```

The entries that come back are about batch size and fast feedback, though the sentence uses neither phrase. Run it again with `--mode keyword` and compare: keyword search is looking for those words, not that meaning.

### Connect Claude Code

```bash
pyrite mcp-setup
```

This tells Claude Code (and Claude Desktop, if it finds it) to start Pyrite's MCP server, the part that lets an agent search, read and write your KBs. It prints a JSON report of what it changed. The entry runs this install's `pyrite` by its full path, so it works from any folder, at the `write` tier: the agent can read and create entries. `pyrite mcp-setup --tier read` gives an agent that can only read. Running the command again is safe.

Check that Claude Code can start it:

```text
$ claude mcp list
pyrite: …/pyrite-tutorial/pyrite/.venv/bin/pyrite mcp --tier write - ✔ Connected
```

(This one is not run by the page's test, which has no Claude Code to ask.)

### Ask Pyrite about itself

Start Claude Code from the tutorial folder, so that it can see both the KBs and the source in `./pyrite`:

```text
$ claude
```

The first time, it asks whether you trust the folder and whether it may use the `pyrite` tools. The read tools are safe to allow.

Each question below shows what to ask, which entries a good answer cites, and the terminal command that finds the same entries without an agent. If Claude's answer does not cite them, ask it to look again, or run the command and read the entries yourself: `pyrite get <id> -k <kb>`. Agents are fluent and sometimes wrong, and a citation you can open is the reason to prefer this over an answer from memory.

#### 1. Why are knowledge bases plain files?

**Ask Claude:**

```text
Why does Pyrite keep knowledge bases as plain files? Cite the entries you used by id.
```

A good answer cites `adr-0001` (Git-Native Markdown Storage), `founding-design-principles-why-markdown-yaml-in-git` and the design page, `design`. It says that the files are the source of truth and the index is a rebuildable cache.

```bash
pyrite search "markdown git files" -k pyrite --fields id,title
```

<!-- runner: expect-ids pyrite:adr-0001 pyrite:founding-design-principles-why-markdown-yaml-in-git -->

The design page, on one page, is `pyrite get design -k pyrite --format markdown`. Read it when you finish here.

#### 2. How should an agent change an entry?

**Ask Claude:**

```text
How should an agent change an entry in a Pyrite knowledge base, and what should it never do? Look at Pyrite's design page and its topic maps, and say which parts are decided and which the code does not do yet.
```

A good answer cites `design` (principle 3: a write does what was asked, loses nothing, and reports it), `map-write-read-path-and-identity` and `adr-0042`. The last section of the design page lists where the code is behind the design; a good answer says so rather than describing the design as if it all worked.

```bash
pyrite search "write read path identity" -k pyrite --fields id,title
```

<!-- runner: expect-ids pyrite:map-write-read-path-and-identity pyrite:adr-0042 -->

#### 3. What is the Andon cord in Pyrite's process, and where does it come from?

**Ask Claude:**

```text
Pyrite's development process is written down in skills under ./pyrite/.claude/skills, a hidden folder, so search it by name. What is the Andon cord in Pyrite's own development process, and where does the idea come from? Cite the file and the entry ids.
```

Tell it where to look, as above. This answer lives in a skill file, not in a KB entry, and in a hidden folder; an agent that is not told will often search past it and report that Pyrite has no such thing. (Ours missed it on one of two tries before we named the folder, and found it on both after.) A good answer cites the section "The Andon cord" in `pyrite/.claude/skills/pyrite-conductor/SKILL.md` for the rule (anyone can stop the loop by opening an issue labelled `andon`) and `andon` in the `tps` KB for where the idea comes from.

```bash
pyrite search "andon" -k tps --fields id,title
```

<!-- runner: expect-ids tps:andon -->

```bash
grep -n "Andon cord" pyrite/.claude/skills/pyrite-conductor/SKILL.md
```

#### 4. What did Pyrite take from Goldratt?

**Ask Claude:**

```text
What did Pyrite take from Eliyahu Goldratt? Cite entry ids from the pyrite and goldratt knowledge bases.
```

A good answer cites `adr-0019` (the pull-based kanban workflow, which treats human review attention as the constraint and applies the Five Focusing Steps to it) and `five-focusing-steps` or `theory-of-constraints` in `goldratt`.

```bash
pyrite search "Goldratt" -k pyrite --fields id,title
```

<!-- runner: expect-ids pyrite:adr-0019 -->

```bash
pyrite search "focusing steps" -k goldratt --fields id,title
```

<!-- runner: expect-ids goldratt:five-focusing-steps -->

#### 5. How do Pyrite's contributors test, and who argued for building quality in?

**Ask Claude:**

```text
In what order does a change to Pyrite get tested on its way to dev and main, and why is the cheapest check first? Which thinkers in the demo knowledge bases argued for building quality in? Cite entry ids.
```

A good answer cites `adr-0032` (branch flow and test progression: the cheap checks first, the expensive ones nearer release) and `testing-standards` from the `pyrite` KB, and from the demo KBs `build-integrity-in` (Poppendiecks) and `the-14-points-for-management` (Deming; "cease dependence on inspection"). Pyrite's own entries do not say that these ideas came from those thinkers; if the answer says so, it should say it is connecting them, not quoting a source.

```bash
pyrite search "branch flow" -k pyrite --fields id,title
```

<!-- runner: expect-ids pyrite:adr-0032 -->

```bash
pyrite search "integrity" -k poppendiecks --fields id,title
```

<!-- runner: expect-ids poppendiecks:build-integrity-in -->

```bash
pyrite search "14 points" -k deming --fields id,title
```

<!-- runner: expect-ids deming:the-14-points-for-management -->

That is the first thing Pyrite is for: an agent that answers from entries you can open and check, in KBs somebody wrote down on purpose.

## Act two: your own knowledge base, with an agent doing the research

### Make it

A new KB is a new folder. The `movement` template has the types this research needs: `practice`, `source`, `organization`, `person` and `note`. Put the folder next to the others and make it a git repository, so that everything the agent adds shows up as a diff.

```bash
pyrite init --template movement --path wip-research --name wip-research
cd wip-research
git init -q
git add -A
git commit -q -m "Start the WIP research KB"
cd ..
```

<!-- runner: health-kb wip-research -->

(`git` needs your name and email set, as always. If the commit complains, run `git config --global user.name "Your Name"` and `git config --global user.email you@example.com`.)

### Ask Claude to research into it

Start a new Claude Code session in the tutorial folder, or carry on in the one you have.

**Ask Claude:**

```text
Research how teams use work-in-progress (WIP) limits and write what you find into the wip-research knowledge base. First look in the demo knowledge bases (reinertsen, poppendiecks, anderson, goldratt) for the concepts that already exist. Then use web search to find where the idea comes from, how teams choose a limit, and what goes wrong. Write short typed entries: practice entries for the ideas, source entries (with a url) for what you read, organization entries for teams that describe their own use. Every claim must trace to a source entry. Link to the demo concepts with [[kb:id]] wikilinks, for example [[reinertsen:wip-constraints]]. If you cannot verify a claim, say so in the entry.
```

Claude will ask permission to search the web and to create entries. Allow them. It searches your KBs first, which takes a few seconds, then searches the web and writes entries as it goes. Expect a couple of minutes.

This step uses web search, which is Claude Code's, not Pyrite's; and what comes back depends on what the web says today. Yours will not match anyone else's. The page's test stands in for this step with five small entries written for it, and checks everything after.

<!-- runner: seed tests/fixtures/tutorials/pyrite-in-20-minutes/wip-research wip-research -->

### See what it did

The agent wrote files. Look at them as you would anyone else's work.

```bash
cd wip-research
git status --short
```

You should see new `practices/`, `sources/` and maybe `organizations/` folders. Open one:

```bash
head -30 "$(ls practices/*.md | head -1)"
```

That is the whole entry: YAML, then Markdown, with `[[wikilinks]]` into the demo KBs. Open the folder in any editor and you can read and change all of it. Nothing about it needs Pyrite.

See exactly what the agent added, and keep it:

```bash
git add -A
git diff --cached --stat
git commit -q -m "Research: WIP limits (agent)"
git log --stat -1
```

From now on `git log` is the history of what was added and by whom, and `git diff` shows any later change line by line. If an agent changes a field you did not ask it to, you will see it there first.

### A malformed entry is refused

Each type in `kb.yaml` says what an entry may contain. `practice` has a `status` that must be `active`, `deprecated` or `evolved`. Ask for something else, as a careless agent or a typo would:

<!-- runner: expect-exit 1 -->

```bash
pyrite create -k wip-research --type practice --title "Pull it all" --status bogus
```

<!-- runner: expect-text SCHEMA_VIOLATION -->

You should see `SCHEMA_VIOLATION` and a message naming the field and the allowed values, and the command exits with status 1. The agent's `kb_create` tool goes through the same check, so a bad write comes back to it as an error it can read and correct. Nothing was written:

```bash
git status --short
```

That prints nothing.

### Your research is now part of the others

Links go both ways. The demo KBs did not change, but Pyrite can tell you what now links into them:

```bash
pyrite backlinks wip-constraints -k reinertsen --format csv
```

<!-- runner: expect-text wip-limits-in-practice -->

Your `wip-limits-in-practice` entry is in the list, next to the demo's own entries that link to Reinertsen's WIP constraints. The next agent that reads `wip-constraints` finds your research from there.

### Come back in a new session

Close Claude Code and the terminal. Open a new terminal and return to the tutorial folder:

```bash
cd ..
source pyrite/.venv/bin/activate
pyrite search "WIP" -k wip-research --fields id,title
```

<!-- runner: expect-ids wip-research:wip-limits-in-practice wip-research:choosing-a-wip-limit -->

The index, the config and the files all outlived the session. Now start `claude` again in the tutorial folder, and:

**Ask Claude:**

```text
What did we find out about choosing a WIP limit? Use the wip-research knowledge base, and tell me which claims it marks as unverified.
```

A good answer cites `choosing-a-wip-limit` and repeats its caveats. The agent has no memory of last time. The KB is the memory, and it is a folder in git.

## Act three: make it yours

The demo repository is a place to put a KB of your own, and the way to share one.

**Fork it.** Fork `pyrite-wiki/pyrite-kb-demo` on GitHub and clone your fork. With the GitHub CLI:

```text
$ gh repo fork pyrite-wiki/pyrite-kb-demo --clone
```

(Not run by the page's test: a fork needs your GitHub account.) Then register your fork's KBs where `pyrite kb discover` looks, as you did above.

**Pick an idea.** One that you know and that has a shape: a thinker or practitioner you have read closely (an "intellectual biography"), a movement and its practices (`movement`, as in Act two), a tool's history, the decisions your team made. Thirty to fifty entries is where a KB starts to be dense enough to be worth searching.

**Build it with the KB-builder skill.** Pyrite ships one for Claude Code, `kb-lifecycle`: it writes the `kb.yaml`, populates the KB in stages with research before writing, fact-checks, and links it to other KBs. Make it available to Claude Code once:

```bash
mkdir -p ~/.claude/skills
cp -r pyrite/.claude/skills/kb-lifecycle ~/.claude/skills/
```

Then, from your fork's folder:

**Ask Claude:**

```text
Use the kb-lifecycle skill to create a knowledge base about <your idea>. Start with the kb.yaml and show it to me before you write any entries.
```

The skill is built around one hard-won rule: agents invent plausible details, so every claim needs a source and every KB needs a fact-checking pass. Read its `SKILL.md` before you let it write a hundred entries. This step is experimental: the skill is Pyrite's, but the agent that runs it is yours, and a full build takes much longer than this tutorial.

**Share it.** Commit, push to your fork and open a pull request against `pyrite-kb-demo` if you would like it in the collection. A list of KBs made with Pyrite is planned; until it exists, a repository on GitHub with a `kb.yaml` at its root is all someone needs to clone it and run `pyrite kb discover`.

## Where to read next

- **The design, on one page:** `pyrite get design -k pyrite --format markdown`. What Pyrite is, nine principles, and where the code is behind them.
- **The topic maps:** one short page per area, from the write path to plugins, linked from the design page. They are the files in `pyrite/kb/maps/`; read one with `pyrite get map-write-read-path-and-identity -k pyrite --format markdown`.
- **The decisions:** the architecture decision records are the files in `pyrite/kb/adrs/`. Ask Claude "which ADRs are still proposed, and what would each change?"
- **Reference:** [Getting started](../getting-started.md), the [README](../../README.md), and [Writing a plugin](plugin-writing.md).

Everything you did in Act one is also something an agent can do for you through Pyrite's MCP tools; and everything in Act two is a folder you can zip, commit and hand to someone who has never heard of Pyrite. That pairing is the point.
