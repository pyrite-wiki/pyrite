- **`pyrite init --template research` now declares a `source` type (#1384).**
  A research KB created from the template rejected `create -t source` with
  `UNDECLARED_TYPE`, so the works the research cites had nowhere of their own
  to go. The template now ships the same `source` type as
  `intellectual-biography`, writing to `sources/`.
