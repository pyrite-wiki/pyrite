"""CSV format importer."""

import csv
import io
from typing import Any

from pyrite.exceptions import caller_owned


def import_csv(data: str | bytes) -> list[dict[str, Any]]:
    """Parse CSV with header row mapping to entry fields.

    Required columns: title
    Optional columns: id, body, entry_type/type, tags, date, importance, summary
    Tags can be semicolon-separated within the column.
    """
    if isinstance(data, bytes):
        try:
            data = data.decode("utf-8")
        except UnicodeDecodeError as e:  # the caller's upload: its bytes, its detail
            raise caller_owned(e) from None

    # Spreadsheet UTF-8 exports prefix the document, not its first field name.
    # Remove only that marker: a BOM inside an entry's text is content.
    data = data.removeprefix("\ufeff")

    reader = csv.DictReader(io.StringIO(data))
    entries = []

    try:
        rows = list(reader)
    except csv.Error as e:  # csv's text quotes only the caller's upload
        raise caller_owned(e) from None

    for row in rows:
        entry: dict[str, Any] = {
            "id": row.get("id", ""),
            "title": row.get("title", "Untitled"),
            "body": row.get("body", ""),
            "entry_type": row.get("entry_type", row.get("type", "note")),
        }

        # Parse tags (semicolon-separated)
        tags_str = row.get("tags", "")
        entry["tags"] = [t.strip() for t in tags_str.split(";") if t.strip()] if tags_str else []

        # Optional fields
        if row.get("date"):
            entry["date"] = row["date"]
        if row.get("importance"):
            # The cell's text as given: the one importance rule judges it on the
            # write (#1588). An int() here stored 5 for `10.7` or `high` and read
            # `+5` as 5 before the rule saw it. An empty cell is no value.
            entry["importance"] = row["importance"]
        if row.get("summary"):
            entry["summary"] = row["summary"]

        entries.append(entry)

    return entries
