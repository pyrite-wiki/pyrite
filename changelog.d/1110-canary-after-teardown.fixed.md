- The memory-bound canary (`test_apps_a_test_builds_are_freed_after_its_teardown`) now builds its
  apps in the test body instead of through `make_client`, so pytest's `item.funcargs` no longer
  keeps all of them alive while it checks. The pre-push suite passes on a clean checkout and the
  check is a hard failure on every platform again (#1110).
