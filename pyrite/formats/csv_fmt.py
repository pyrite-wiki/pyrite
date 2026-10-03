"""CSV format serializer for tabular data."""

import csv
import io
from typing import Any

from .tabular import cell_text, projected_columns


def csv_serialize(data: Any, **kwargs) -> str:
    """Serialize tabular data to CSV.

    Works with list-type responses: search results, entries, timeline, tags.
    For non-tabular data, falls back to a simple key-value format.
    """
    if isinstance(data, dict):
        fields = kwargs.get("fields")
        if fields and ("results" in data or "entries" in data):
            rows = data["results"] if "results" in data else data["entries"]
            output = io.StringIO()
            columns = projected_columns(fields)
            writer = csv.DictWriter(output, fieldnames=columns)
            writer.writeheader()
            for row in rows:
                writer.writerow({key: cell_text(row.get(key)) for key in columns})
            return output.getvalue()
        if "results" in data:
            return _results_to_csv(data["results"])
        if "entries" in data:
            return _entries_to_csv(data["entries"])
        if "events" in data:
            return _events_to_csv(data["events"])
        if "tags" in data:
            return _tags_to_csv(data["tags"])
        if "kbs" in data:
            return _kbs_to_csv(data["kbs"])

    # Fallback: single row of key-value pairs
    if isinstance(data, dict):
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(data.keys())
        writer.writerow(data.values())
        return output.getvalue()

    return str(data)


def _results_to_csv(results: list) -> str:
    if not results:
        return ""
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=["id", "kb_name", "entry_type", "title", "date", "importance", "snippet"],
    )
    writer.writeheader()
    for r in results:
        if isinstance(r, dict):
            writer.writerow({k: r.get(k, "") for k in writer.fieldnames})
    return output.getvalue()


def _entries_to_csv(entries: list) -> str:
    if not entries:
        return ""
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=["id", "kb_name", "entry_type", "title", "date", "importance"],
    )
    writer.writeheader()
    for e in entries:
        if isinstance(e, dict):
            writer.writerow({k: e.get(k, "") for k in writer.fieldnames})
    return output.getvalue()


def _events_to_csv(events: list) -> str:
    if not events:
        return ""
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=["id", "date", "title", "importance", "kb_name"],
    )
    writer.writeheader()
    for e in events:
        if isinstance(e, dict):
            writer.writerow({k: e.get(k, "") for k in writer.fieldnames})
    return output.getvalue()


def _tags_to_csv(tags: list) -> str:
    if not tags:
        return ""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["tag", "count"])
    writer.writeheader()
    for t in tags:
        if isinstance(t, dict):
            row = {"tag": t.get("tag", t.get("name", "")), "count": t.get("count", 0)}
            writer.writerow(row)
    return output.getvalue()


def _kbs_to_csv(kbs: list) -> str:
    if not kbs:
        return ""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["name", "type", "path", "entries"])
    writer.writeheader()
    for kb in kbs:
        if isinstance(kb, dict):
            writer.writerow({k: kb.get(k, "") for k in writer.fieldnames})
    return output.getvalue()
