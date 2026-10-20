- `kb_update` (and REST `PUT`) refuse a whole `kb_get` envelope sent back as
  `entry`, and leave the file untouched, instead of writing it into the entry
  (#1024). Send the contents of `result["entry"]` at the top level.
