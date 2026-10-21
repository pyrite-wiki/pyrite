- A create or import that asks for `lifecycle: archived` writes the key
  instead of dropping it and answering as if the entry were active: the base
  field is restored when the entry is built, as the loader already did for a
  file (#1097). A file that never had the key still does not grow one.
