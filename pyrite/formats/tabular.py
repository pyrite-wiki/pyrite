"""Explicit projected columns and cells shared by tabular output formats."""

import json
from typing import Any

from ..services.read_shaping import IDENTITY_FIELDS


def projected_columns(fields: list[str]) -> list[str]:
    """Identity first, then requested fields, including absent columns."""
    return list(dict.fromkeys((*IDENTITY_FIELDS, *fields)))


def cell_text(value: Any) -> str:
    """Preserve complex values as JSON cells, and absent/null as empty cells."""
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def projected_markdown(rows: list[dict], fields: list[str]) -> str:
    """A Markdown table representing every requested column."""
    if not rows:
        return ""
    columns = projected_columns(fields)

    def escaped(value: Any) -> str:
        return (
            cell_text(value)
            .replace("\\", "\\\\")
            .replace("|", r"\|")
            .replace("\r\n", "\n")
            .replace("\r", "\n")
            .replace("\n", "<br>")
        )

    lines = [
        "| " + " | ".join(escaped(key) for key in columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    lines.extend("| " + " | ".join(escaped(row.get(key)) for key in columns) + " |" for row in rows)
    return "\n".join(lines)


def projected_rich_table(rows: list[dict], fields: list[str], title: str):
    """Render projected records literally rather than interpreting Rich markup."""
    from rich.table import Table
    from rich.text import Text

    columns = projected_columns(fields)
    table = Table(title=Text(title))
    for column in columns:
        table.add_column(Text(column))
    for row in rows:
        table.add_row(*(Text(cell_text(row.get(key))) for key in columns))
    return table
