## Summary
What changed and why, in a few sentences. If this fixes a bug, say which one:

Fixes #

## Plan / claim
Link to the issue comment where you claimed this (or paste the two or three
lines here if this PR is itself the claim).

## Testing
- [ ] `pytest tests/ extensions/ -n auto` passes locally (or: the pre-push hook ran it)
- [ ] `ruff check pyrite/ tests/ extensions/ && ruff format --check pyrite/ tests/ extensions/` is clean
- [ ] A `fix:` change includes a test that fails without the fix (the `commit-msg` hook and CI check that a fix touches `tests/`)
- [ ] Frontend change: `cd web && npm run check && npm run test:unit`

## Notes for the reviewer
Anything you were unsure about, or a decision you made that could have gone another way.
