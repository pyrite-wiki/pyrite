"""`--fields` on a single entry reaches CSV, Markdown and Rich too (#667, review of #721).

`get --fields` and `GET /entries/{id}?fields=` project one record. Before this,
neither passed `fields` to the formatter: CSV had no column for a requested
field the entry lacks, and Markdown fell to a Python repr.
"""

import csv
import io
from contextlib import contextmanager
from copy import deepcopy
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.formats import format_response
from pyrite.services.read_shaping import project_fields

FIELDS = ["status", "missing"]
ENTRY = {
    "id": "entry-1",
    "kb_name": "kb",
    "title": "T",
    "status": "waiting",
    "body": "excluded secret",
    "entry_type": "note",
}


def test_serializers_project_one_entry_as_one_row():
    record = project_fields(ENTRY, FIELDS)
    content, _ = format_response(record, "csv", fields=FIELDS)
    rows = list(csv.reader(io.StringIO(content)))
    assert rows == [["id", "kb_name", "status", "missing"], ["entry-1", "kb", "waiting", ""]]
    md, _ = format_response(record, "markdown", fields=FIELDS)
    assert md.splitlines()[0] == "| id | kb_name | status | missing |"
    assert "| entry-1 | kb | waiting |  |" in md


@pytest.mark.control(reason="no projection requested: the existing rendering must not move")
def test_single_entry_without_fields_keeps_its_rendering():
    content, _ = format_response(deepcopy(ENTRY), "markdown")
    assert content.startswith("---")


@pytest.mark.parametrize("fmt", ["csv", "markdown", "rich"])
def test_get_projects_in_every_format(fmt):
    svc = MagicMock()
    svc.get_entry.return_value = deepcopy(ENTRY)

    @contextmanager
    def context():
        yield MagicMock(), MagicMock(), svc

    with patch("pyrite.cli.entry_commands.cli_context", context):
        result = CliRunner().invoke(
            app,
            ["get", "entry-1", "--fields", ",".join(FIELDS), "--format", fmt],
            terminal_width=120,
        )
    assert result.exit_code == 0, result.exception or result.output
    out = result.stdout
    assert "excluded secret" not in out
    positions = [out.index(name) for name in ["id", "kb_name", "status", "missing"]]
    assert positions == sorted(positions)
    assert "waiting" in out
    if fmt == "csv":
        assert list(csv.reader(io.StringIO(out)))[0] == ["id", "kb_name", "status", "missing"]


def test_rest_get_entry_csv_projects(make_client):
    client, _, _ = make_client(kb_name="kb")
    with patch("pyrite.services.kb_service.KBService.get_entry", return_value=deepcopy(ENTRY)):
        response = client.get(
            "/api/entries/entry-1",
            params={"fields": ",".join(FIELDS), "kb": "kb"},
            headers={"Accept": "text/csv"},
        )
    assert response.status_code == 200, response.text
    assert list(csv.reader(io.StringIO(response.text))) == [
        ["id", "kb_name", "status", "missing"],
        ["entry-1", "kb", "waiting", ""],
    ]
