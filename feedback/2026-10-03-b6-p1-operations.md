## 2026-10-03 · B6 P1, a pure operations module (`apply(text, ops, span)`, PR #732) from a mission brief · claude-opus-5-5

One worker theme in a worktree: test the riskiest assumption (ruamel's composer spans), post a plan on the draft PR, TDD a new module against 17 hand-made shapes, mutation-check each guard, push. Issue #730's spike text, ADR-0042 and `id_pin_service`'s existing insert-and-verify carried it. Five places cost time, in order.

**Command:**
```
.venv/bin/python -c "from ruamel.yaml import YAML; n = YAML().compose(src); ..."
scripts/verify-red.sh
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

**Expected:** a composer node's `end_mark` is where its bytes end; verify-red can show behavioural reds; the skill's lint line passes on a clean branch.

**Got / Friction:**

1. **`end_mark` is not where a node's bytes end, and nothing in the KB says so.** A block sequence or block mapping ends after the comment line that follows it. A block scalar ends after the blank lines that follow it. An alias composes to the anchor's own node object, so its span is the anchor's bytes. Each would make an unset or a set eat or corrupt a neighbour's bytes. Found by probing before building; now in the module docstring of `pyrite/storage/file_operations.py`.
2. **ruamel's `TimeStamp` loses its tzinfo under `copy.deepcopy`** (0.19.1): `09:30-05:00` comes back as a naive `09:30`. Every Hugo date then failed the post-check. Fixed by converting to a plain `datetime` in `_plain`, with a comment.
3. **verify-red cannot show a behavioural red for a new pure module.** Every test imports the module, so the line is `0 red · 142 import-only`, which the skill says does not count, but it gives no route for this case. The evidence that mattered was removing each guard and naming the test that fails, done by hand with sed and `git checkout`. A `scripts/mutate` that takes a file, a snippet and a replacement, runs the tests and restores the file would make that table cheap and repeatable.
4. **The brief and the doc disagreed on the trailing-comment column.** The brief says a comment keeps its column; the doc's own example keeps the gap when the value grows. I chose "column when the new value fits, gap otherwise" and stated it on the PR.
5. **The skill's lint line still fails on a clean branch** (`deploy/*/create-user.py`, `scripts/*appointee*.py`, 20 errors). This is the earlier entry's item 5 again; I used `ruff check pyrite/ tests/`.

**Had to figure out:**
- Where the frontmatter ends, with no splitter to call: the span is a parameter, and the test module carries a small helper checked against `_frontmatter_of`. The splitter theme can repoint both.
- That ruff wants `...Error` on exception names (N818). `PinRefusedError` was the precedent.

**Would have helped:**
1. A component note on composer spans (`end_mark` traps, alias identity) beside `pyrite/utils/yaml.py`.
2. Guidance in pyrite-dev for verify-red on a new module, plus a small mutation helper.

**Worked well:** `scripts/test-affected` reused a stamped pass instead of running a second suite. The spike's list of shapes in #730 made the fixture table quick to write. `insert_id_line` showed the insert-then-reparse pattern this module generalises.

**Severity:** slowed. Items 1 and 2 would have shipped silent byte loss without the probe and the post-check.

**Round 1 (fix round, same day).** The cold read found what my report missed: my "independent" oracle was the module's own key-block scan copied character for character, so three comment-losing edits passed 2,602 sweep cases (Andon #745). What would have caught it before review: writing the property first ("bytes outside the named spans are identical") and picking the oracle's mechanism to differ from the code's (here: an indentation scan in the test, `end_mark` walk-back in the post-check, recursion in the edit). The 17 shapes also had no comment on a key line or between a dash-line pair and its sibling; a comment-dense fixture in the sweep turned the old module red at once.

**Round 2 (same day).** The byte property held. What failed was inside the spans: `_edit_map` compared keys by `id()`, which works only for one-character strings because CPython interns those. Every test key I wrote was one character (`x`, `y`, `a`), so the narrow path looked right, and the widen hid the failure by producing a correct value with the comments gone. Two things would have caught it earlier: asserting `report.widened == ()` for every edit that has a narrow form (a widen is a symptom, not a success), and fixtures with realistic key names. A second lesson: a check that replays exactly the check before it cannot be shown to do anything, because deleting it fails no test. I removed the byte replay from the final check and kept the reader-based value check, which a test can blind the first line for.
