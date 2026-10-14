"""A projected Rich table stays readable at ordinary terminal widths (#667, review of #721).

Rich shrinks columns to fit and ends a cell it cannot fit with an ellipsis. In a
projection the ``id`` column and the headers are what a reader keys on, so they
must never be cut: at the default 80 columns ``id`` was ``entry-one-lo…``.
"""

import io

import pytest
from rich.console import Console
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.formats.tabular import projected_rich_table
from tests.search_verdict_seed import cli_world, make_cli_world

ENTRY_ID = "entry-one-long-identifier"
ROWS = [
    {
        "id": ENTRY_ID,
        "kb_name": "pyrite",
        "title": "A title that is long enough to need wrapping",
        "body": "A body of some length, wrapped across several lines at a narrow width",
        "status": "active",
    }
]
FIELDS = ["title", "body", "status", "missing"]
HEADERS = ["id", "kb_name", *FIELDS]


def render(width, rows=ROWS, fields=FIELDS):
    console = Console(width=width, file=io.StringIO(), record=True)
    console.print(projected_rich_table(rows, fields, "Entries"))
    return console.export_text()


def test_id_is_whole_at_80_columns():
    text = render(80)
    assert ENTRY_ID in text
    assert "…" not in text


@pytest.mark.control(reason="headers already fit at 80; guards the id fix against squeezing them")
def test_headers_are_whole_at_80_columns():
    header_line = next(line for line in render(80).splitlines() if "kb_name" in line)
    for header in HEADERS:
        assert header in header_line


@pytest.mark.parametrize("width", [60, 40])
def test_nothing_is_cut_with_an_ellipsis_when_narrower(width):
    text = render(width)
    assert "…" not in text
    flat = "".join(text.replace("│", "").replace("┃", "").split())
    assert ENTRY_ID in flat
    for header in HEADERS:
        assert header in flat


@pytest.mark.parametrize("columns", [80, 30])
def test_search_prints_whole_id_at_the_console_width(columns, console_width, tmp_path):
    row = ROWS[0]
    config = make_cli_world(
        tmp_path, entry_id=ENTRY_ID, title=row["title"], body=row["body"], status=row["status"]
    )
    args = ["search", "wrapped", "--fields", ",".join(FIELDS), "--format", "rich"]
    console_width(columns)
    with cli_world(config):
        result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.exception or result.output
    assert ENTRY_ID in result.stdout
    assert "…" not in result.stdout
    assert ("┏" in result.stdout) == (columns == 80)


LONG_CELL = [{"id": "a" * 25, "kb_name": "p", "description": "d", "x": "x" * 300}]
LONG_CELL_FIELDS = ["description", "x"]
WIDE_ID = [{"id": "日本語日本語日本語日本語日本語日本語日本語", "kb_name": "k", "title": "t"}]
WIDE_ID_FIELDS = ["title"]
CASES = {
    "default": (ROWS, FIELDS),
    "long-cell-beside-long-header": (LONG_CELL, LONG_CELL_FIELDS),
    "wide-character-id": (WIDE_ID, WIDE_ID_FIELDS),
}


def flat(text):
    return "".join(text.replace("│", "").replace("┃", "").split())


@pytest.mark.parametrize("case", CASES)
def test_every_width_gives_a_whole_table_or_the_stack(case):
    """Whatever the width: the id and each header are whole in a table, or the
    output is the stacked layout. Never a split header, a cut id or a column
    with no width."""
    rows, fields = CASES[case]
    headers = ["id", "kb_name", *fields]
    needed = projected_rich_table(rows, fields, "Entries").width_needed()
    for width in range(12, needed + 25):
        text = render(width, rows, fields)
        assert "…" not in text, (width, text)
        if "┏" in text:
            assert width >= needed, (width, needed)
            header_line = next(line for line in text.splitlines() if "┃" in line)
            for header in headers:
                assert header in header_line, (width, header, text)
            assert any(rows[0]["id"] in line for line in text.splitlines()), (width, text)
        else:
            assert width < needed, (width, needed, text)
            if width > max(len(h) for h in headers) + 2:  # a header and ": " fit on a line
                for header in headers:
                    assert f"\n{header}: " in "\n" + text, (width, header, text)


@pytest.mark.parametrize("case", CASES)
def test_a_table_is_used_as_soon_as_it_fits(case):
    rows, fields = CASES[case]
    needed = projected_rich_table(rows, fields, "Entries").width_needed()
    assert "┏" in render(needed, rows, fields)
    assert "┏" not in render(needed - 1, rows, fields)


def test_width_needed_counts_terminal_cells_not_characters():
    # 21 wide characters are 42 cells; len() says 21.
    table = projected_rich_table(WIDE_ID, WIDE_ID_FIELDS, "T")
    assert table.width_needed() == 42 + len("kb_name") + len("title") + 3 * 3 + 1


def test_stacked_layout_separates_records_with_a_blank_line():
    text = render(20, ROWS + [dict(ROWS[0], id="second")], FIELDS)
    assert text.count("\n\n") >= 2
