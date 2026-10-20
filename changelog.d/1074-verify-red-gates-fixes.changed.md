- `verify-red` is more honest, and stays advisory (#1074). It is not in `gate` and
  never blocks a merge; CI does not pass its `--enforce-fix` flag, which exists
  for local use. It grades against the merge commit's first parent, so a PR
  merged since yours was opened is no longer graded as yours. A
  `@pytest.mark.control` counts when written on the test or one of its
  parameters (however it was built); one the base already had on that cell is
  "inherited", and one added another way (class or module `pytestmark`, a hook,
  a fixture, the test body) is an error naming where. Its claim rule handles
  module-level tables on the test function itself: inline `parametrize` lists
  grade the whole function, and class-level, fixture-params, generate-hook and
  imported tables select nothing. Gating waits for #1104's model. The base
  collect is reported and capped at a minute. The `fix:` commit check now sees
  `fix(scope):` and `fix!:` too.
