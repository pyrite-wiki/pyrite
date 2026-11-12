- `task create --priority` and `task update --priority` now reject values outside
  1–10 with a usage error (exit 2), before writing any task file (#1394).
