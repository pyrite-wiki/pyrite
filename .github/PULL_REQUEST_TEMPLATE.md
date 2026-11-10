## Summary
What changed and why, in a few sentences. If this fixes a bug, say which one:

Fixes #

## Plan / claim
Link to the issue comment where you claimed this (or paste the two or three
lines here if this PR is itself the claim).

## Testing
- [ ] Tests required by [CONTRIBUTING.md — Running the tests](https://github.com/pyrite-wiki/pyrite/blob/dev/CONTRIBUTING.md#running-the-tests) passed; record the command, tested head and result.
- [ ] The lint checks in [CONTRIBUTING.md — Lint](https://github.com/pyrite-wiki/pyrite/blob/dev/CONTRIBUTING.md#lint) passed.
- [ ] A `fix:` change includes a test that fails without the fix (the `commit-msg` hook checks that a `fix:` commit touches `tests/`)
- [ ] Frontend change: `cd web && npm run check && npm run test:unit`

## Notes for the reviewer
Anything you were unsure about, or a decision you made that could have gone another way.
