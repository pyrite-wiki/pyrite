# Systematic Debugging for Pyrite

## The Iron Law

```
NO FIXES WITHOUT ROOT CAUSE INVESTIGATION FIRST
```

Random fixes waste time and create new bugs. Systematic debugging is faster than guess-and-check. Always.

## The Four Phases

Complete each phase before proceeding to the next.

### Phase 1: Root Cause Investigation

**BEFORE attempting ANY fix:**

**1. Read error messages carefully.**
Don't skip past them. They often contain the exact solution.
- Stack traces: read completely, note file paths and line numbers
- Error codes: look them up
- Warnings: don't ignore them

**2. Reproduce consistently.**
- Can you trigger it reliably?
- Exact steps to reproduce?
- If not reproducible: gather more data, don't guess

**3. Check recent changes.**
```bash
git diff                    # Unstaged changes
git log --oneline -10       # Recent commits
git diff HEAD~3             # Last 3 commits
```

**4. Trace data flow backward.**
When the error is deep in the call stack, trace backward to find the origin:

- Where does the bad value appear? → What called this? → What passed the bad value? → Keep going up
- Fix at the **source**, not where the error manifests
- After fixing at source, add validation at each layer (defense in depth)

**Example:** `file_path: None` validation error in `EntryResponse`
- Symptom: Pydantic validation fails in API response
- One level up: `list_entries()` returns rows with `file_path=None`
- Root cause: `EntryResponse.file_path` typed as `str` but DB has NULL values
- Fix at source: Change schema to `file_path: str | None = None`
- Defense: `list_entries()` should also handle None gracefully

**5. For multi-component systems, add diagnostic instrumentation:**
```python
# Before the problematic operation
import traceback
print(f"DEBUG: entering function with arg={arg!r}")
print(f"DEBUG: stack:\n{''.join(traceback.format_stack()[-5:])}")
```

Run once, gather evidence, THEN analyze.

### Phase 2: Pattern Analysis

**1. Find working examples** — similar code in the codebase that works
**2. Compare** — what's different between working and broken?
**3. List every difference**, however small. Don't assume "that can't matter."

### Phase 3: Hypothesis and Testing

**1. Form ONE hypothesis.** State clearly: "I think X because Y."
**2. Test minimally.** Smallest possible change. One variable at a time.
**3. Did it work?**
  - Yes → Phase 4
  - No → Form NEW hypothesis. Don't pile more fixes on top.

### Phase 4: Implementation

**1. Write a failing test** reproducing the bug (use TDD skill)
**2. Implement the fix** — address root cause, not symptom
**3. Verify** — test passes, no other tests broken
**4. Add defense in depth** — validation at every layer the data passes through

**If 3+ fix attempts fail:**

This isn't a failed hypothesis — it's likely a wrong architecture. STOP.

- Is this pattern fundamentally sound?
- Are we "sticking with it through sheer inertia"?
- Discuss with the user before attempting more fixes

## Pyrite-Specific Debugging

### Backend issues
```bash
# Run specific test with verbose output
.venv/bin/pytest tests/test_file.py::test_name -v -s

# Run with print output visible
.venv/bin/pytest tests/test_file.py -v -s --tb=long

# Check DB state
.venv/bin/python -c "from pyrite.storage.database import PyriteDB; db = PyriteDB(':memory:'); ..."
```

### Frontend issues
```bash
# Run specific vitest
cd web && npx vitest run src/lib/stores/entries.test.ts

# Playwright with headed browser (see what's happening)
scripts/e2e.sh --headed

# Playwright debug mode
scripts/e2e.sh --debug

# Check console errors
scripts/e2e.sh --reporter=line
```

### API issues
```bash
# Test API directly
curl -s http://localhost:8088/api/kbs | python -m json.tool
curl -s http://localhost:8088/health | python -m json.tool

# Check if server is running
lsof -i :8088
```

### Common Pyrite bugs and their root causes

| Symptom | Likely Root Cause |
|---------|-------------------|
| `ModuleNotFoundError: pyrite_*` | Extension not pip installed in `.venv/` |
| FTS5 search returns nothing | Index not built (`pyrite index build`) |
| API returns HTML instead of JSON | SPA fallback catching API route — check `/api` prefix |
| Playwright timeout | Server not started, or localhost vs 127.0.0.1 mismatch |
| Svelte "mount not available" | Missing `resolve.conditions: ['browser', 'svelte']` in vitest |
| Pydantic validation error | Schema field type doesn't match DB value (usually `None` for `str`) |
| Pre-commit hook fails twice | ruff-format reformatted files — re-stage and commit again |

## Red Flags — STOP and Return to Phase 1

- "Quick fix for now, investigate later"
- "Just try changing X and see"
- "Add multiple changes, run tests"
- "I don't fully understand but this might work"
- Proposing solutions before tracing data flow
- "One more fix attempt" (when already tried 2+)
- Each fix reveals new problem in a different place

**ALL of these mean: STOP. Return to Phase 1.**

## Common Rationalizations

| Excuse | Reality |
|--------|---------|
| "Issue is simple" | Simple issues have root causes too |
| "Emergency, no time" | Systematic is FASTER than thrashing |
| "Just try this first" | First fix sets the pattern. Do it right. |
| "Multiple fixes saves time" | Can't isolate what worked. Causes new bugs. |
| "I see the problem" | Seeing symptoms ≠ understanding root cause |

---

## Agent-Quality Debugging

**This is different from code debugging.** When an AI agent produces a bad entry, wrong link, or low-quality content, the root cause is often not a code bug — it's a context, schema, or pipeline gap. Use this protocol instead of the Four Phases above.

### The question to ask first

**"Was this a code bug or an agent-quality bug?"**

- Code bug: wrong behavior given correct input (use Four Phases above)
- Agent-quality bug: correct code, but the agent's output is wrong/incomplete/mislinked

### Provenance debugging

Trace backward from the bad output:

1. **What entry is wrong?** Read it fully — `pyrite get <id> -k <kb> --format yaml`
2. **What tool call produced it?** Check the MCP call log or agent transcript
3. **What context did the agent have?** Was the schema visible? Were validation warnings surfaced?
4. **Where did the pipeline fail to catch it?**
   - Schema validation: Did the field have a type/options constraint? Should it?
   - Type resolution: Did `_resolve_entry_type` send it to the wrong class?
   - build_entry: Did unknown kwargs end up in metadata instead of a proper field?
   - Indexing: Was the entry indexed with the right type/tags?

### Common agent-quality failure patterns

| Pattern | Likely Cause | Fix |
|---------|-------------|-----|
| Wrong entry type | Agent used core type name, plugin subtype expected | Improve MCP tool description to list valid types |
| Missing field | Agent didn't know the field existed | Add field to schema, surface in MCP inputSchema |
| Bad link target | Agent guessed an entry ID that doesn't exist | Add link target validation in schema.py |
| Vocabulary mismatch | Agent used free text where controlled vocabulary exists | Add select/multi-select field with options |
| Duplicate entry | Agent couldn't find existing entry to update | Improve search, add MCP tool for exact-match lookup |
| Body too sparse | Agent wrote a stub instead of real content | Add minimum body length validation in QA |

### Key principle

**Every agent-quality bug should produce a schema or validation improvement.** If the agent *could* have produced bad output, it eventually *will*. The fix is always structural — better constraints, better tool descriptions, better validation — not "tell the agent to be more careful."
