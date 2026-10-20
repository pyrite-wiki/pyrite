# Test-Driven Development for Pyrite

## The Cycle

```
RED ──→ Verify Fails ──→ GREEN ──→ Verify Passes ──→ REFACTOR ──→ Verify Still Passes ──→ Commit ──→ Next
 ↑                                                                                                     │
 └─────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

## RED — Write Failing Test

Write ONE test showing what should happen. One behavior per test.

**Good:**
```python
def test_search_returns_matching_entries(tmp_kb):
    create_entry(tmp_kb, type="note", title="Alpha", body="test content")
    create_entry(tmp_kb, type="note", title="Beta", body="other content")

    results = search(tmp_kb, "alpha")

    assert len(results) == 1
    assert results[0].title == "Alpha"
```

**Bad:**
```python
def test_search(tmp_kb):
    # Tests multiple behaviors, vague name, no assertions about what was returned
    results = search(tmp_kb, "anything")
    assert results is not None
```

**Requirements:**
- Clear name describing the behavior being tested
- One behavior per test — "and" in the name means split it
- Real code, not mocks (mocks only when unavoidable: network, external APIs)
- Test the public interface, not internals

## Verify RED — Watch It Fail

**MANDATORY. Never skip.**

```bash
# Backend
.venv/bin/pytest tests/path/test_file.py::test_name -v

# Frontend unit
cd web && npx vitest run src/path/file.test.ts -t "test name"

# Frontend E2E
scripts/e2e.sh e2e/file.spec.ts   # Playwright under the machine lock
```

Confirm:
- Test **fails** (not errors from typos/imports)
- Failure message matches expectation (feature missing, not broken setup)

**Test passes immediately?** You're testing existing behavior. Fix the test.

## GREEN — Minimal Code

Write the simplest code that makes the test pass. Nothing more.

**Don't:**
- Add error handling for cases not tested yet
- Add configuration for hypothetical future needs
- Refactor surrounding code
- Add features beyond what the test requires

## Verify GREEN

**MANDATORY.**

Run the specific test AND the full suite for the area:

```bash
# Backend: specific test, then the affected tests (the whole suite is CI's
# job; if you need it, scripts/heavy_lock.py -- <command>)
.venv/bin/pytest tests/path/test_file.py::test_name -v
scripts/test-affected --run

# Frontend: specific then full
cd web && npx vitest run src/path/file.test.ts
cd web && npm run test:unit
```

- Specific test passes
- No other tests broken
- Output clean (no warnings, no errors)

**Test still fails?** Fix code, not test. The test defines correct behavior.

## REFACTOR — Clean Up

Only after green:
- Remove duplication
- Improve names
- Extract helpers if genuinely shared

**Keep tests green.** Don't add behavior during refactor.

## Pyrite-Specific Test Patterns

### Backend (pytest)

**Fixture for temporary KB:**
```python
@pytest.fixture
def tmp_kb(tmp_path):
    kb_dir = tmp_path / "test-kb"
    kb_dir.mkdir()
    (kb_dir / "kb.yaml").write_text("name: test\nkb_type: software\n")
    return kb_dir
```

**Fixture for isolated plugin registry:**
```python
@pytest.fixture
def patched_registry():
    from pyrite.plugins import registry as reg_module
    from pyrite.plugins.registry import PluginRegistry
    old = reg_module._registry
    reg_module._registry = PluginRegistry()
    yield reg_module._registry
    reg_module._registry = old
```

**Use `in` not `len` for registry assertions** (other plugins may be installed):
```python
assert "my_type" in registry.get_all_entry_types()  # Good
assert len(registry.get_all_entry_types()) == 1       # Bad
```

### Frontend Unit (Vitest + testing-library)

```typescript
import { render, screen } from '@testing-library/svelte';
import MyComponent from './MyComponent.svelte';

test('renders entry title', () => {
    render(MyComponent, { props: { title: 'Test Entry' } });
    expect(screen.getByText('Test Entry')).toBeInTheDocument();
});
```

**Svelte 5 resolve conditions:** Vitest config has `resolve.conditions: ['browser', 'svelte']` to avoid server-side bundle issues. Tests use jsdom environment.

### Frontend E2E (Playwright)

```typescript
test('navigates to entry', async ({ page }) => {
    await page.goto('/entries');
    await page.click('text=My Entry');
    await expect(page).toHaveURL(/\/entries\//);
});
```

**Playwright config** starts both FastAPI (:8088) and Vite (:5173) dev servers. Use `reuseExistingServer` in dev.

### 8-Section Extension Test Structure

1. `TestPluginRegistration` — plugin discovered, name correct
2. `TestEntryTypes` — from_frontmatter, to_frontmatter, entry_type property
3. `TestValidators` — valid passes, invalid caught, unrelated types return `[]`
4. `TestHooks` — hook callbacks invoked correctly
5. `TestWorkflows` — state transitions, invalid transitions rejected
6. `TestDBTables` — table definitions valid
7. `TestPreset` — preset contains expected types and directories
8. `TestCoreIntegration` — entry_from_frontmatter dispatches, get_entry_class resolves

## Common Rationalizations

| Excuse | Reality |
|--------|---------|
| "Too simple to test" | Simple code breaks. Test takes 30 seconds. |
| "I'll test after" | Tests passing immediately prove nothing — you never saw it fail. |
| "Need to explore first" | Fine. Throw away exploration. Start with TDD. |
| "Keep code as reference" | You'll adapt it. That's testing-after. Delete means delete. |
| "Tests after achieve same goals" | Tests-after: "what does this do?" Tests-first: "what should this do?" |
| "Already manually tested" | Ad-hoc ≠ systematic. No record, can't re-run. |
| "Deleting X hours is wasteful" | Sunk cost fallacy. Keeping unverified code is tech debt. |

## Red Flags — STOP and Start Over

If you catch yourself:
- Writing production code before the test
- Test passing immediately (you're testing existing behavior)
- Can't explain why the test failed
- Rationalizing "just this once"
- Saying "I already manually tested it"

**Delete the code. Write the test first. Start over.**
