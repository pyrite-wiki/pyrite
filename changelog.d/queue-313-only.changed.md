- **The merge queue tests on Python 3.13 only; the push to `dev` tests 3.11 and 3.12.**
  Each commit is still tested once on every supported interpreter, but a merge
  group is a third the size, so it finishes inside the queue's check timeout
  when runners are scarce. A failure that shows only on 3.11 or 3.12 now turns
  `dev` red after the merge instead of blocking it. Pull requests still test
  on 3.12 (all three when they touch test infrastructure).
