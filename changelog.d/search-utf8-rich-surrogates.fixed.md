- **`pyrite search` in rich output no longer raises `UnicodeEncodeError` on non-UTF-8 characters (#1767).**
  A query or KB name with lone surrogates or non-UTF-8 bytes is refused with `VALIDATION_FAILED` (exit 1) on a single search, safely escaped in `--batch` rich output, and never ends with a traceback.
