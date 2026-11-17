- **`pyrite qa duplicates --threshold` works on Typer 0.29 (#1597).**
  The option used a `click_type=` keyword that Typer 0.29 removed, so every
  Pyrite command line crashed on import. It now uses a `parser=` that still
  refuses values outside 0 to 1 (including NaN) as a usage error.
