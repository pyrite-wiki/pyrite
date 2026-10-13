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
    content, _ = format_response(record, "csv", fields=FIELDS, single_entry=True)
    rows = list(csv.reader(io.StringIO(content)))
    assert rows == [["id", "kb_name", "status", "missing"], ["entry-1", "kb", "waiting", ""]]
    md, _ = format_response(record, "markdown", fields=FIELDS, single_entry=True)
    assert md.splitlines()[0] == "| id | kb_name | status | missing |"
    assert "| entry-1 | kb | waiting |  |" in md


@pytest.mark.control(reason="no projection requested: the existing rendering must not move")
def test_single_entry_without_fields_keeps_its_rendering():
    content, _ = format_response(deepcopy(ENTRY), "markdown")
    assert content.startswith("---")


@pytest.mark.parametrize("fmt", ["csv", "markdown", "rich"])
def test_get_projects_in_every_format(fmt, console_width):
    svc = MagicMock()
    svc.get_entry.return_value = deepcopy(ENTRY)

    @contextmanager
    def context():
        yield MagicMock(), MagicMock(), svc

    console_width(120)
    with patch("pyrite.cli.entry_commands.cli_context", context):
        result = CliRunner().invoke(
            app,
            ["get", "entry-1", "--fields", ",".join(FIELDS), "--format", fmt],
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


def test_a_single_record_with_an_envelope_key_is_still_one_record():
    """The caller says it is a single entry; a key named `results` does not change that."""
    record = dict(ENTRY, results="not an envelope")
    content, _ = format_response(record, "csv", fields=["results"], single_entry=True)
    assert list(csv.reader(io.StringIO(content)))[1] == ["entry-1", "kb", "not an envelope"]


def test_non_dict_rows_are_skipped_like_the_unprojected_branches():
    payload = {"results": [project_fields(ENTRY, FIELDS), "stray", None]}
    for fmt in ("csv", "markdown"):
        content, _ = format_response(payload, fmt, fields=FIELDS)
        assert "waiting" in content


@pytest.mark.control(
    reason="the one-row table and the frontmatter view were both already true at 16ceeb0b; this pins the documented shape change"
)
def test_markdown_projection_replaces_the_frontmatter_view():
    """A shape change, pinned: with fields, Markdown is a one-row table (body flattened);
    without, it is the frontmatter block plus body."""
    full = dict(ENTRY, body="line one\nline two")
    projected, _ = format_response(
        project_fields(full, ["title", "body"]),
        "markdown",
        fields=["title", "body"],
        single_entry=True,
    )
    assert projected.splitlines()[0] == "| id | kb_name | title | body |"
    assert "line one<br>line two" in projected
    unprojected, _ = format_response(full, "markdown")
    assert unprojected.startswith("---")


def test_markdown_header_rule_is_three_dashes_per_column():
    md, _ = format_response(
        project_fields(ENTRY, FIELDS), "markdown", fields=FIELDS, single_entry=True
    )
    assert md.splitlines()[1] == "| --- | --- | --- | --- |"


@pytest.mark.control(
    reason="the empty string under a projection was already true at 16ceeb0b; this pins the documented shape change"
)
def test_empty_projected_markdown_is_empty():
    """A shape change, pinned: no `# Search: q (0 results)` line under a projection."""
    md, _ = format_response({"query": "q", "count": 0, "results": []}, "markdown", fields=FIELDS)
    assert md == ""
    legacy, _ = format_response({"query": "q", "count": 0, "results": []}, "markdown")
    assert legacy.startswith("# Search: q (0 results)")


@pytest.mark.parametrize(
    ("accept", "check"),
    [
        ("text/markdown", lambda t: t.splitlines()[0] == "| id | kb_name | status | missing |"),
        pytest.param(
            "text/yaml",
            lambda t: "missing" not in t and "waiting" in t,
            marks=pytest.mark.control(reason="YAML already projected"),
        ),
    ],
)
def test_rest_get_entry_markdown_and_yaml(make_client, accept, check):
    client, _, _ = make_client(kb_name="kb")
    with patch("pyrite.services.kb_service.KBService.get_entry", return_value=deepcopy(ENTRY)):
        response = client.get(
            "/api/entries/entry-1",
            params={"fields": ",".join(FIELDS), "kb": "kb"},
            headers={"Accept": accept},
        )
    assert response.status_code == 200, response.text
    assert check(response.text), response.text
    assert "excluded secret" not in response.text
