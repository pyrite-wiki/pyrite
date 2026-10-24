- `pyrite search` accepts repeatable `--type` flags (matching any listed type) and repeatable `--exclude-type` flags across keyword, semantic, hybrid, and `--files` modes (#627).
- When a backend cannot honor type filters in its vector leg, the fallback
  warning names the normalized `entry_types` filter, including callers that
  supplied the single legacy `entry_type`.
