## 2026-10-03 · one frontmatter splitter: a refactor theme in a worktree (pyrite-dev skill, verify-red, index build) · claude-sonnet-5-5

Replaced two frontmatter splitters and about a dozen ad-hoc readers with one function, and compared `pyrite index build` before and after.

**Friction 1: no way to run the CLI against another revision of the code. Severity: had to figure out.** The parity check needs `index build` under `origin/dev` and under the branch. `python -m pyrite.cli` fails (`pyrite.cli` has no `__main__`). The editable install wins over `PYTHONPATH` when the working directory is the checkout, so the first "before" run silently imported the branch. It worked only from another directory, with `git archive origin/dev pyrite` on `PYTHONPATH` and `python -c "from pyrite.cli import main; ..."`. **Would have helped:** a `__main__.py` for `pyrite.cli`, and a line in the pyrite-dev skill on how to run "before" code.

**Friction 2: the item was not the first to ask for this. Severity: nearly duplicated.** `kb/backlog/shared-frontmatter-split-utility.md` (2026-07-03) already named eight of the same call sites and a "split_frontmatter" function. The new item did not link it, and its list of splitters (two) was short of the real count (eleven readers plus three scripts). I found it by grepping `kb/` for `from_markdown`. The old item also covers the drifted `_base_kwargs` and `parse_meta` copies, which this theme does not touch.

**Friction 3: `verify-red` called the whole new file import-only again. Severity: had to figure out.** Same as the B7 entry above: a top-level `from pyrite.utils.frontmatter import ...` made all 63 tests count as weak. Lazy imports inside the tests moved 19 to red. The 24 tests that exercise the new function itself stay import-only, and cannot be anything else for a new function; a reviewer reading the summary line cannot tell that from the reds it should worry about.

**Friction 4: `ruff check .` is still red on `dev` (20 errors under `deploy/` and `scripts/`). Severity: slowed.** I ran `ruff check pyrite extensions tests` and said so. The skill table still says `ruff check .`.

**Worked well:**
- `scripts/verify-red.sh` found twelve tests that passed without the change, which were controls I had not marked; marking them with a reason took one pass.
- A structural test written as an AST scan (calls to `re.*` or `str.find/startswith/split` with a `---` literal) caught every old splitter on its first run and let the table of "ways of splitting" test the scanner itself.
- `tests/conftest.py` fixtures (`kb_service`, `pyrite_config`) made reader tests for `add_entry_from_file` and templates a few lines each.

**Round 1 (fix round after the cold read): what the first pass got wrong, and what helped.**

**Friction 5: my oracle was the code under test. Severity: the property failed and my tests passed.** The first table's expected column came from `Entry.from_markdown`, so every row agreed with itself and I wrote "agrees" in a docstring. The cold read ran Hugo and found an opening/closing asymmetry (`--- ` rejected, `---  ` accepted), a phantom importer entry and three closers that crashed `list_templates`. Nothing in the pyrite-dev skill says where a parsing rule's expected values must come from. **Would have helped:** one line in the skill: "when the code implements a convention (Hugo, YAML, git), the expected column is the tool's own output, recorded, with a test that re-runs the tool when installed." Here that was `hugo` on the PATH, and it took four minutes to turn 14 guesses into 33 recorded rows.

**Friction 6: `verify-red` classes a test import-only when any helper it calls imports the new module. Severity: had to figure out.** My reader matrix had a `load_frontmatter` reader among twelve others, so the whole parametrized test counted as weak evidence until I removed it from the matrix. Same cause as friction 3, one level down.

**Friction 7: `git stash` in a shared worktree is a trap in a parity script. Severity: nearly misreported.** I stashed to get the "before" tree, forgot that "after" then ran stashed too, and both runs agreed. The check only caught it because the new edge files were still not indexed. Use `git archive` into the scratchpad for "before" and never touch the working tree.

**Worked well:** a live `hugo` build is a 30-line test harness; the reader matrix (every reader x every oracle row, refuse-or-read) found the template `YAMLError` and importer swallow without my having listed them.

**Round 2.**

**Friction 8: "refused" had two meanings and my test used the weaker one. Severity: a refusal that wasn't.** The oracle's honesty test counted any non-`Frontmatter` result as a refusal, so `----` as the first line (`NoFrontmatter`) passed as "agrees with Hugo, which errors". But `NoFrontmatter` means "plain text" to every reader, so `create --body-file` and `list_templates` stored the YAML as body. A typed result that readers can treat as plain text is not a refusal. **Would have helped:** the splitter's result types could have been named for what a reader does (`Plain`, `Refused`) rather than for what the scanner saw. Here the fix was a rule: a first line starting with `---` is never plain.

**Friction 9: the YAML column was prose I wrote.** The cold read's PyYAML and ruamel run contradicted three of its words (`open-trailing-tab` "fine", the three `open-*` "a plain scalar" rows were right only by luck). It is now a dict asserted against both parsers on every run. Eight lines of test code, and it found the tab row.

**Friction 10: a stream importer has no per-record boundary until it has parsed.** `import_cmd`'s docstring promised siblings are imported; for markdown that cannot be true. The choice stated instead: a `---` line followed by `title:`, `type:` or `id:` starts the next entry; anything else is a body rule.

**Round 3.**

**Friction 11: I tested my own examples, not the corpus. Severity: shipped a splitter that truncated 9 of Pyrite's own entries.** Rounds 1 and 2 pinned the stream rule with a dozen hand-written inputs; none had a fenced code block. The cold read ran the importer over `kb/` and found nine entries cut short (an ADR kept 51K of 68K characters). The test that now guards it is five lines: every `kb/` file with valid frontmatter imports as one entry whose body equals the split body. **Would have helped:** a line in the pyrite-dev skill: "a rule that decides what a file means gets a test over the real corpus (`kb/`, the fixture KBs), not only over inputs you wrote." Dropping the clever rule was the fix; the maintainer's "one entry per file" removed the whole class.

**Friction 12: dev moved under a three-round branch. Severity: the first push of round 3 failed pre-push.** B6 P1 (`file_operations.py`) landed during the rounds and imported the private `_frontmatter_of` this branch removes; the rebase was clean, and only the pre-push run (13 minutes) found the broken import and a fence constant in the new file. The coordinator had said P1 would be repointed after both landed; nothing marked the moment. **Would have helped:** `git grep` for a removed name against `origin/dev` as the first step of every round, before the suite.
