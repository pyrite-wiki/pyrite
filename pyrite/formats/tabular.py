"""Explicit projected columns and cells shared by tabular output formats."""

import json
from typing import Any

from ..services.read_shaping import IDENTITY_FIELDS


def projected_columns(fields: list[str]) -> list[str]:
    """Identity first, then requested fields, including absent columns."""
    return list(dict.fromkeys((*IDENTITY_FIELDS, *fields)))


def projected_rows(data: dict, *, single: bool = False) -> list[dict] | None:
    """The records of a projected payload, or None when it has none to project.

    ``single=True`` says the payload *is* the one record (``get --fields``,
    ``GET /entries/{id}``); the caller knows, so it is not inferred from key
    names. Otherwise the payload is a ``results`` or ``entries`` envelope.
    A row that is not a dict is skipped, as the unprojected branches do.
    """
    if single:
        return [data]
    for key in ("results", "entries"):
        if isinstance(data.get(key), list):
            return [row for row in data[key] if isinstance(row, dict)]
    return None


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

    # The header rule, one cell at a time. Each constant holds a "|", which is how
    # tests/test_frontmatter_splitter.py tells a table separator from a fence.
    lines = [
        "| " + " | ".join(escaped(key) for key in columns) + " |",
        "".join("| --- " for _ in columns) + "|",
    ]
    lines.extend("| " + " | ".join(escaped(row.get(key)) for key in columns) + " |" for row in rows)
    return "\n".join(lines)


class ProjectedRich:
    """Projected records for a terminal: a table when it can be read, else a stack.

    Rich fits a table to the terminal by shrinking columns, ending a cell it
    cannot fit with an ellipsis and splitting a header across lines. In a
    projection that cut the id a reader keys on and the field names (review of
    #721), and its shrinking does not honour a column's ``min_width`` when the
    table is tight. So the widths are decided here, in terminal cells, and given
    to Rich as fixed column widths. What sets a column's minimum:

    * its header (``cell_len``: a wide character is 2 cells);
    * for the id column, its widest id;
    * the widest single character in any of its cells (a wide character cannot
      be folded into one cell);
    * three cells of chrome per column (a space either side, a border) and one
      more border.

    The table is used when every minimum fits; spare width then goes to the
    columns that want more, narrowest first, and long cells fold onto further
    lines. Narrower, each record prints as ``field: value`` lines, records
    separated by a blank line (a value that itself holds a blank line can look
    like a record boundary). Nothing is elided. Values are rendered literally,
    never as Rich markup.

    The width is the console's, which Rich takes from the terminal even when
    stdout is piped (any standard stream that is a terminal will do), 80 when no
    stream is a terminal, and ``COLUMNS`` overrides both. This is for people;
    scripts take json, yaml or csv. At width 1 a stacked value made of wide
    characters is dropped (a wide character needs 2 cells); from width 2 no value
    is cut.
    """

    def __init__(self, rows: list[dict], fields: list[str], title: str):
        self.rows = rows
        self.columns = projected_columns(fields)
        self.title = title

    def _cells(self, column: str) -> list[str]:
        return [cell_text(row.get(column)) for row in self.rows]

    def _minimum(self, column: str) -> int:
        """The narrowest this column may be, in terminal cells."""
        from rich.cells import cell_len

        cells = self._cells(column)
        width = max(cell_len(column), *(cell_len(ch) for cell in cells for ch in cell), 0)
        if column == "id":
            width = max(width, *(cell_len(cell) for cell in cells), 0)
        return width

    def _natural(self, column: str) -> int:
        """The width at which no cell of this column would fold."""
        from rich.cells import cell_len

        widest_line = max(
            (cell_len(line) for cell in self._cells(column) for line in cell.splitlines() or [""]),
            default=0,
        )
        return max(self._minimum(column), widest_line)

    def width_needed(self) -> int:
        return sum(self._minimum(c) for c in self.columns) + 3 * len(self.columns) + 1

    def _widths(self, max_width: int) -> list[int]:
        widths = [self._minimum(c) for c in self.columns]
        natural = [self._natural(c) for c in self.columns]
        spare = max_width - self.width_needed()
        while spare > 0:
            wanting = [i for i, w in enumerate(widths) if w < natural[i]]
            if not wanting:
                break
            widths[min(wanting, key=lambda i: widths[i])] += 1
            spare -= 1
        return widths

    def __rich_console__(self, console, options):
        from rich.table import Table
        from rich.text import Text

        if options.max_width >= self.width_needed():
            table = Table(title=Text(self.title))
            for column, width in zip(self.columns, self._widths(options.max_width), strict=True):
                table.add_column(Text(column), width=width, overflow="fold")
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
