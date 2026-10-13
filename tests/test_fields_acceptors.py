"""Every command and endpoint that accepts `fields` is listed, and each is pinned (#667).

`ACCEPTORS` is the list. A new `--fields` option, `fields=` query parameter or
`fields` body key found in the source and absent here fails
`test_every_acceptor_in_the_source_is_listed`. Each entry says, per format,
whether the shared projection (`pyrite/formats/tabular.py`) applies, and the
parametrised tests below hold it to that.

Classification per format: "projected" (through the shared code), or
"not projected" with a test that pins what it does instead.
"""

import csv
import io
import json
import re
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from typer.testing import CliRunner

from pyrite.cli import app

ROOT = Path(__file__).resolve().parent.parent
FIELDS = ["status", "missing"]
COLUMNS = ["id", "kb_name", "status", "missing"]
ROW = {
    "id": "entry-1",
    "kb_name": "kb",
    "title": "T",
    "status": "waiting",
    "tags": ["a"],
    "entry_type": "note",
    "body": "excluded secret",
}

#: file -> (count of `"--fields"` options, count of REST `fields` readers)
ACCEPTORS_BY_FILE = {
    "pyrite/cli/browse_commands.py": (3, 0),  # recent, list-entries, batch-read
    "pyrite/cli/entry_commands.py": (1, 0),  # get
    "pyrite/cli/search_commands.py": (1, 0),  # search (and search --files, below)
    "pyrite/server/endpoints/search.py": (0, 1),  # GET /api/search
    "pyrite/server/endpoints/entries.py": (0, 2),  # GET /api/entries/{id}, POST /api/entries/batch
    "pyrite/server/mcp_server.py": (0, 0),  # JSON only; see MCP_JSON_ONLY
}
#: MCP tools take `fields` but return JSON, never CSV/Markdown/Rich: nothing to project.
MCP_JSON_ONLY = True
#: Takes no `fields`, by design: GET /api/entries lists with the legacy columns.
NOT_ACCEPTORS = ["GET /api/entries"]


def _source_files():
    for base in ("pyrite/cli", "pyrite/server/endpoints", "extensions"):
        yield from (ROOT / base).rglob("*.py")


def _counts(path):
    text = path.read_text(encoding="utf-8")
    options = len(re.findall(r'"--fields"', text))
    rest = 0
    if "server" in path.parts:
        rest = len(re.findall(r"parse_fields_param\((?:fields|fields_param)\)", text))
    return options, rest


def test_every_acceptor_in_the_source_is_listed():
    found = {}
    for path in _source_files():
        counts = _counts(path)
        if counts != (0, 0):
            found[str(path.relative_to(ROOT))] = counts
    expected = {k: v for k, v in ACCEPTORS_BY_FILE.items() if v != (0, 0)}
    assert found == expected, (
        "a `--fields` option or a REST `fields` reader appeared or went. Classify it per "
        "format in tests/test_fields_acceptors.py (ACCEPTORS_BY_FILE and the tests below)."
    )


# ---------------------------------------------------------------- CLI acceptors


@contextmanager
def _cli_patched():
    svc = MagicMock()
    svc.list_entries.return_value = [deepcopy(ROW)]
    svc.get_entries.return_value = [deepcopy(ROW)]
    svc.get_entry.return_value = deepcopy(ROW)
    svc.count_entries.return_value = 1
    config = MagicMock()
    config.settings.search_mode = "keyword"

    @contextmanager
    def context():
        yield config, MagicMock(), svc

    with (
        patch("pyrite.cli.browse_commands.cli_context", context),
        patch("pyrite.cli.entry_commands.cli_context", context),
        patch("pyrite.cli.search_commands.load_config", return_value=config),
        patch("pyrite.cli.context.open_index_db"),
        patch("pyrite.storage.IndexManager") as mgr,
        patch("pyrite.cli.search_commands._warn_if_stale"),
        patch("pyrite.services.search_service.SearchService") as search,
    ):
        mgr.return_value.is_empty.return_value = False
        search.return_value.search.return_value = [deepcopy(ROW)]
        yield config


CLI_ACCEPTORS = {
    "search": ["search", "q"],
    "recent": ["recent"],
    "list-entries": ["list-entries"],
    "batch-read": ["batch-read", "kb:entry-1"],
    "get": ["get", "entry-1"],
}


@pytest.mark.parametrize("command", CLI_ACCEPTORS)
@pytest.mark.parametrize("fmt", ["csv", "markdown", "rich", "yaml", "json"])
def test_cli_acceptor_projects_in_every_format(command, fmt):
    args = [*CLI_ACCEPTORS[command], "--fields", ",".join(FIELDS), "--format", fmt]
    with _cli_patched():
        result = CliRunner(env={"COLUMNS": "200"}).invoke(app, args)
    assert result.exit_code == 0, result.exception or result.output
    out = result.stdout
    assert "excluded secret" not in out
    assert "waiting" in out
    if fmt == "csv":
        assert list(csv.reader(io.StringIO(out)))[0] == COLUMNS
    elif fmt == "markdown":
        assert out.splitlines()[0] == "| id | kb_name | status | missing |"
    elif fmt == "rich":
        positions = [out.index(name) for name in COLUMNS]
        assert positions == sorted(positions)
    else:
        data = json.loads(out) if fmt == "json" else yaml.safe_load(out)
        record = data.get("results", data.get("entries", [data]))[0]
        assert list(record) == ["id", "kb_name", "status"]  # absent keys are not invented


def test_search_files_is_not_projected_and_says_so(tmp_path):
    """`search --files` is the pre-index fallback. It prints its own Rich table
    (KB, Type, Title, ID) whatever `--format` and `--fields` say; it now tells the
    caller on stderr that `--fields` was not applied."""
    kb_path = tmp_path / "kb"
    kb_path.mkdir()
    (kb_path / "a.md").write_text("---\nid: a\ntitle: Needle\ntype: note\n---\nneedle body\n")
    from pyrite.models.core_types import ENTRY_TYPE_REGISTRY  # noqa: F401

    config = MagicMock()
    kb = MagicMock()
    kb.name = "kb"
    kb.path = kb_path
    config.knowledge_bases = [kb]
    config.get_kb.return_value = kb
    entry = MagicMock(entry_type="note", title="Needle", id="a")
    repo = MagicMock()
    repo.list_files.return_value = [kb_path / "a.md"]
    repo._load_entry.return_value = entry
    with (
        patch("pyrite.cli.search_commands.load_config", return_value=config),
        patch("pyrite.cli.search_commands.KBRepository", return_value=repo),
    ):
        result = CliRunner(env={"COLUMNS": "100"}).invoke(
            app, ["search", "needle", "--files", "--fields", "status", "--format", "csv"]
        )
    assert result.exit_code == 0, result.exception or result.output
    assert "Needle" in result.stdout and "status" not in result.stdout.split("Needle")[0].lower()
    assert "--fields is not applied" in result.stderr


# --------------------------------------------------------------- REST acceptors


def _rest(client, method, url, **kwargs):
    with (
        patch("pyrite.services.search_service.SearchService.search", return_value=[deepcopy(ROW)]),
        patch("pyrite.services.kb_service.KBService.count_entries", return_value=1),
        patch("pyrite.services.kb_service.KBService.get_entry", return_value=deepcopy(ROW)),
        patch("pyrite.services.kb_service.KBService.get_entries", return_value=[deepcopy(ROW)]),
    ):
        return getattr(client, method)(url, **kwargs)


REST_ACCEPTORS = {
    "GET /api/search": ("get", "/api/search", {"params": {"q": "q", "fields": "status,missing"}}),
    "GET /api/entries/{id}": (
        "get",
        "/api/entries/entry-1",
        {"params": {"fields": "status,missing", "kb": "kb"}},
    ),
    "POST /api/entries/batch": (
        "post",
        "/api/entries/batch",
        {
            "json": {
                "entries": [{"entry_id": "entry-1", "kb_name": "kb"}],
                "fields": ["status", "missing"],
            }
        },
    ),
}


@pytest.mark.parametrize("acceptor", REST_ACCEPTORS)
@pytest.mark.parametrize("accept", ["text/csv", "text/markdown", "text/yaml"])
def test_rest_acceptor_projects_in_every_negotiated_format(make_client, acceptor, accept):
    client, _, _ = make_client(kb_name="kb")
    method, url, kwargs = REST_ACCEPTORS[acceptor]
    response = _rest(client, method, url, headers={"Accept": accept}, **kwargs)
    assert response.status_code == 200, response.text
    text = response.text
    assert "excluded secret" not in text
    assert "waiting" in text
    if accept == "text/csv":
        assert list(csv.reader(io.StringIO(text)))[0] == COLUMNS
    elif accept == "text/markdown":
        assert text.splitlines()[0] == "| id | kb_name | status | missing |"
    else:
        assert "missing" not in text  # YAML omits absent keys, like JSON


def test_batch_fields_are_cleaned_like_the_query_string_form(make_client):
    """The JSON list goes through the same parse as `?fields=`: stripped, deduplicated,
    empties dropped. Before, " status " was a key that matched nothing."""
    client, _, _ = make_client(kb_name="kb")
    body = {
        "entries": [{"entry_id": "entry-1", "kb_name": "kb"}],
        "fields": [" status ", "", "status", "missing"],
    }
    response = _rest(
        client, "post", "/api/entries/batch", json=body, headers={"Accept": "text/csv"}
    )
    assert list(csv.reader(io.StringIO(response.text)))[0] == COLUMNS
    as_json = _rest(client, "post", "/api/entries/batch", json=body)
    assert list(as_json.json()["entries"][0]) == ["id", "kb_name", "status"]


def test_batch_fields_still_refuses_a_malformed_list(make_client):
    client, _, _ = make_client(kb_name="kb")
    response = _rest(
        client,
        "post",
        "/api/entries/batch",
        json={"entries": [{"entry_id": "entry-1", "kb_name": "kb"}], "fields": [1]},
    )
    assert response.status_code == 400


@pytest.mark.parametrize(
    "accept",
    [
        "text/csv",
        pytest.param(
            "application/json", marks=pytest.mark.control(reason="JSON is not negotiated by fields")
        ),
    ],
)
def test_get_entries_list_takes_no_fields(make_client, accept):
    """`GET /api/entries` has no `fields` parameter (CLI `list-entries` does); documented, pinned:
    the parameter changes nothing."""
    client, _, _ = make_client(kb_name="kb")
    with (
        patch("pyrite.services.kb_service.KBService.list_entries", return_value=[deepcopy(ROW)]),
        patch("pyrite.services.kb_service.KBService.count_entries", return_value=1),
    ):
        plain = client.get("/api/entries", params={"kb": "kb"}, headers={"Accept": accept})
        with_fields = client.get(
            "/api/entries",
            params={"kb": "kb", "fields": "status,missing"},
            headers={"Accept": accept},
        )
    assert plain.status_code == 200
    assert with_fields.text == plain.text
