- **`kb add` no longer scaffolds a KB you already built (#945).** Registering a
  directory that already has a `kb.yaml` left it alone, so a hand-maintained KB
  gained `_templates/` and an empty directory per entry type the moment it was
  registered with `--type`. Scaffolding still happens for a directory that is
  not a KB yet.
