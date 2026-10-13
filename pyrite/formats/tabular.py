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


class ProjectedRich:
    """Projected records for a terminal: a table when it can be read, else a stack.

    Rich fits a table to the terminal by shrinking columns and ending cells it
    cannot fit with an ellipsis. In a projection that cut the id a reader keys on
    (``entry-one-lo…``, review of #721), and below a certain width every header.
    So the table is used only when the id and every header fit whole; narrower,
    each record is printed as ``field: value`` lines. Nothing is ever elided.
    Values are rendered literally, never as Rich markup.
    """

    def __init__(self, rows: list[dict], fields: list[str], title: str):
        self.rows = rows
        self.columns = projected_columns(fields)
        self.title = title

    def _widest_id(self) -> int:
        return max((len(cell_text(row.get("id"))) for row in self.rows), default=0)

    def _width_needed(self) -> int:
        first = max(self._widest_id(), len("id"))
        rest = sum(len(column) for column in self.columns if column != "id")
        borders = 3 * len(self.columns) + 1
        return first + rest + borders

    def __rich_console__(self, console, options):
        from rich.table import Table
        from rich.text import Text

        if options.max_width >= self._width_needed():
            table = Table(title=Text(self.title))
            for column in self.columns:
                if column == "id":
                    table.add_column(Text(column), no_wrap=True, min_width=self._widest_id())
                else:
                    table.add_column(Text(column), overflow="fold")
            for row in self.rows:
                table.add_row(*(Text(cell_text(row.get(key))) for key in self.columns))
            yield table
            return
        yield Text(self.title, style="bold")
        for row in self.rows:
            yield Text("")
            for key in self.columns:
                yield Text(f"{key}: {cell_text(row.get(key))}", overflow="fold")


def projected_rich_table(rows: list[dict], fields: list[str], title: str):
    """Render projected records literally rather than interpreting Rich markup."""
    return ProjectedRich(rows, fields, title)
