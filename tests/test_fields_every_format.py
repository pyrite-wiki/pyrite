"""An explicit projection survives every output encoder and transport (#667)."""

import csv
import io
import json
from contextlib import contextmanager
from copy import deepcopy
from unittest.mock import MagicMock, patch

import pytest
import yaml
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.formats import format_response
from pyrite.services.read_shaping import project_fields

FIELDS = ["status", "title", "missing"]
COLUMNS = ["id", "kb_name", *FIELDS]
ROW = {
    "id": "entry-1",
    "kb_name": "kb",
    "status": "waiting",
    "title": "Requested title",
    "entry_type": "note",
    "snippet": '<mark>one</mark>\nsecond, "quoted" line',
    "tags": ["a", "b"],
    "metadata": {"x": [1, 2]},
    "body": "excluded secret",
}


def assert_projected(text, fmt):
    if fmt in ("json", "yaml"):
        data = json.loads(text) if fmt == "json" else yaml.safe_load(text)
        rows = data.get("results", data.get("entries"))
        assert list(rows[0]) == COLUMNS[:-1]
        assert rows[0]["status"] == "waiting"
        assert "missing" not in rows[0]
    elif fmt == "csv":
        rows = list(csv.reader(io.StringIO(text)))
        assert rows[0] == COLUMNS
        assert rows[1] == ["entry-1", "kb", "waiting", ROW["title"], ""]
    else:
        # Headers are literal field names, preserving the requested order.
        positions = [text.index(name) for name in COLUMNS]
        assert positions == sorted(positions)
        assert "waiting" in text
        assert ROW["title"] in text
    assert "excluded secret" not in text


@pytest.mark.parametrize("fmt", ["csv", "markdown"])
@pytest.mark.parametrize("envelope", ["results", "entries"])
def test_serializers_keep_the_ordered_projection(fmt, envelope):
    content, _ = format_response({envelope: [project_fields(ROW, FIELDS)]}, fmt, fields=FIELDS)
    assert_projected(content, fmt)


def test_csv_projected_complex_cells_round_trip():
    content, _ = format_response({"results": [ROW]}, "csv", fields=["tags", "metadata", "snippet"])
    rows = list(csv.DictReader(io.StringIO(content)))
    assert json.loads(rows[0]["tags"]) == ROW["tags"]
    assert json.loads(rows[0]["metadata"]) == ROW["metadata"]
    assert rows[0]["snippet"] == ROW["snippet"]
    assert list(rows[0]) == ["id", "kb_name", "tags", "metadata", "snippet"]


def test_empty_projected_csv_keeps_requested_headers():
    content, _ = format_response({"results": []}, "csv", fields=FIELDS)
    assert list(csv.reader(io.StringIO(content))) == [COLUMNS]


@pytest.mark.parametrize("fmt", ["csv", "markdown"])
def test_identity_order_duplicates_and_sparse_rows(fmt):
    data = {
        "entries": [
            {"id": "a", "kb_name": "kb", "status": "ready"},
            {"id": "b", "kb_name": "kb", "title": "B"},
        ]
    }
    content, _ = format_response(data, fmt, fields=["title", "id", "status", "title"])
    if fmt == "csv":
        rows = list(csv.reader(io.StringIO(content)))
        assert rows == [
            ["id", "kb_name", "title", "status"],
            ["a", "kb", "", "ready"],
            ["b", "kb", "B", ""],
        ]
    else:
        assert "| id | kb_name | title | status |" in content
        assert "| a | kb |  | ready |" in content


def test_markdown_projected_cells_escape_table_separators():
    content, _ = format_response(
        {"entries": [{"id": "a", "kb_name": "kb", "title": "A|B\nC"}]},
        "markdown",
        fields=["title"],
    )
    assert r"A\|B<br>C" in content


@pytest.mark.control(reason="Without --fields the existing CSV columns remain unchanged")
def test_default_csv_columns_are_unchanged():
    content, _ = format_response({"results": [ROW]}, "csv")
    assert next(csv.reader(io.StringIO(content))) == [
        "id",
        "kb_name",
        "entry_type",
        "title",
        "date",
        "importance",
        "snippet",
    ]


@pytest.mark.control(reason="Without --fields the existing Markdown result list is unchanged")
def test_default_markdown_list_is_unchanged():
    content, _ = format_response({"results": [ROW]}, "markdown")
    assert "- **Requested title** (kb)" in content
    assert ROW["snippet"] in content


@pytest.mark.parametrize("command", ["search", "recent", "list-entries", "batch-read"])
@pytest.mark.parametrize(
    "fmt",
    [
        pytest.param(
            "json", marks=pytest.mark.control(reason="JSON already renders projected records")
        ),
        pytest.param(
            "yaml", marks=pytest.mark.control(reason="YAML already renders projected records")
        ),
        "csv",
        "markdown",
        "rich",
    ],
)
def test_cli_projection_in_every_format(command, fmt):
    svc = MagicMock()
    svc.list_entries.return_value = [deepcopy(ROW)]
    svc.get_entries.return_value = [deepcopy(ROW)]
    svc.count_entries.return_value = 1
    config = MagicMock()
    config.settings.search_mode = "keyword"

    @contextmanager
    def context():
        yield config, MagicMock(), svc

    args = [command]
    if command == "search":
        args.append("query")
    elif command == "batch-read":
        args.append("kb:entry-1")
    args += ["--fields", ",".join(FIELDS), "--format", fmt]
    with (
        patch("pyrite.cli.browse_commands.cli_context", context),
        patch("pyrite.cli.search_commands.load_config", return_value=config),
        patch("pyrite.cli.context.open_index_db"),
        patch("pyrite.storage.IndexManager") as mgr,
        patch("pyrite.cli.search_commands._warn_if_stale"),
        patch("pyrite.services.search_service.SearchService") as search,
    ):
        mgr.return_value.is_empty.return_value = False
        search.return_value.search.return_value = [deepcopy(ROW)]
        result = CliRunner().invoke(app, args, terminal_width=180)
    assert result.exit_code == 0, result.exception or result.output
    assert_projected(result.stdout, fmt)


def test_rest_csv_uses_requested_projection(make_client):
    client, _, _ = make_client(kb_name="kb")
    with (
        patch("pyrite.services.search_service.SearchService.search", return_value=[ROW]),
        patch("pyrite.services.kb_service.KBService.count_entries", return_value=1),
    ):
        response = client.get(
            "/api/search",
            params={"q": "query", "fields": ",".join(FIELDS)},
            headers={"Accept": "text/csv"},
        )
    assert response.status_code == 200, response.text
    assert_projected(response.text, "csv")


@pytest.mark.parametrize("command", ["search", "recent", "list-entries", "batch-read"])
def test_projected_rich_cells_are_literal(monkeypatch, command):
    monkeypatch.setitem(ROW, "title", "[bold]Requested title[/bold]")
    test_cli_projection_in_every_format(command, "rich")


@pytest.fixture
def indexed_projection(make_client):
    from pyrite.models import EventEntry
    from pyrite.storage.index import IndexManager
    from pyrite.storage.repository import KBRepository

    client, config, db = make_client(kb_name="kb")
    entry = EventEntry.create(
        title="Projection needle", date="2025-01-01", body="A body containing needle."
    )
    entry.tags = ["needle", "second"]
    KBRepository(config.get_kb("kb")).save(entry)
    IndexManager(db, config).index_all()
    return client, config, entry.id


@pytest.mark.parametrize("command", ["search", "recent", "list-entries", "batch-read"])
@pytest.mark.parametrize("fmt", ["csv", "markdown", "rich"])
def test_real_index_projection_reaches_all_cli_encoders(indexed_projection, command, fmt):
    _, config, entry_id = indexed_projection
    args = [command]
    if command == "search":
        args.append("needle")
    elif command == "batch-read":
        args.append(f"kb:{entry_id}")
    args += ["--fields", "tags,entry_type", "--format", fmt]
    with (
        patch("pyrite.cli.context.load_config", return_value=config),
        patch("pyrite.cli.search_commands.load_config", return_value=config),
    ):
        result = CliRunner().invoke(app, args, terminal_width=180)
    assert result.exit_code == 0, result.exception or result.output
    if fmt == "csv":
        rows = list(csv.DictReader(io.StringIO(result.stdout)))
        assert list(rows[0]) == ["id", "kb_name", "tags", "entry_type"]
        if command == "search":
            # Search records do not carry tags today; projection must still
            # retain the requested column without inventing a value.
            assert rows[0]["tags"] == ""
        else:
            assert json.loads(rows[0]["tags"]) == ["needle", "second"]
    else:
        assert result.stdout.index("tags") < result.stdout.index("entry_type")
        if command != "search":
            assert "needle" in result.stdout and "second" in result.stdout
    assert "Projection needle" not in result.stdout


def test_real_rest_search_projection(indexed_projection):
    client, _, _ = indexed_projection
    response = client.get(
        "/api/search",
        params={"q": "needle", "fields": "tags,entry_type"},
        headers={"Accept": "text/csv"},
    )
    assert response.status_code == 200, response.text
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert list(rows[0]) == ["id", "kb_name", "tags", "entry_type"]
    assert rows[0]["tags"] == ""


@pytest.mark.parametrize("command", ["search", "recent", "list-entries"])
def test_empty_cli_csv_retains_projection(indexed_projection, command):
    _, config, _ = indexed_projection
    args = [command]
    if command == "search":
        args.append("nomatchxyz")
    else:
        args += ["--type", "person"]
    args += ["--fields", "status,title", "--format", "csv"]
    with (
        patch("pyrite.cli.context.load_config", return_value=config),
        patch("pyrite.cli.search_commands.load_config", return_value=config),
    ):
        result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.exception or result.output
    reader = csv.DictReader(io.StringIO(result.stdout))
    assert reader.fieldnames == ["id", "kb_name", "status", "title"]
    assert list(reader) == []
