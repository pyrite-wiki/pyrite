"""A projected Rich table stays readable at ordinary terminal widths (#667, review of #721).

Rich shrinks columns to fit and ends a cell it cannot fit with an ellipsis. In a
projection the ``id`` column and the headers are what a reader keys on, so they
must never be cut: at the default 80 columns ``id`` was ``entry-one-lo…``.
"""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.formats.tabular import projected_rich_table

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


def render(width):
    console = Console(width=width, record=True, file=open("/dev/null", "w"))
    console.print(projected_rich_table(ROWS, FIELDS, "Entries"))
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


def test_search_prints_whole_id_at_80_columns():
    row = dict(ROWS[0], entry_type="note")
    svc = MagicMock()
    config = MagicMock()
    config.settings.search_mode = "keyword"

    @contextmanager
    def context():
        yield config, MagicMock(), svc

    args = ["search", "q", "--fields", ",".join(FIELDS), "--format", "rich"]
    with (
        patch("pyrite.cli.search_commands.load_config", return_value=config),
        patch("pyrite.cli.context.open_index_db"),
        patch("pyrite.storage.IndexManager") as mgr,
        patch("pyrite.cli.search_commands._warn_if_stale"),
        patch("pyrite.services.search_service.SearchService") as search,
    ):
        mgr.return_value.is_empty.return_value = False
        search.return_value.search.return_value = [row]
        result = CliRunner().invoke(app, args, terminal_width=80)
    assert result.exit_code == 0, result.exception or result.output
    assert ENTRY_ID in result.stdout
    assert "…" not in result.stdout
