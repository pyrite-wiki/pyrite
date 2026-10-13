"""Every command and endpoint that accepts `fields` is listed, and each is pinned (#667).

`ACCEPTORS` is the inventory, and the parametrised tests below are driven from
it. `test_the_inventory_is_what_the_source_has` walks the AST of every module
under `pyrite/` and `extensions/*/src` and finds each function that
  * calls `parse_fields_param` or `project_fields` (the shared rule, however its
    argument is spelled: `fields`, `req.fields`, `body.get("fields")`, a keyword),
  * spells the option `"--fields"` (either quote), or
  * takes a parameter named `fields` whose default is a `Query(...)`/`Option(...)`.
A function found and not listed fails; a listed one no longer found fails; a
listed CLI or REST one with no test case fails. So a new acceptor cannot be added
without being classified and exercised.

What it cannot see: an acceptor that neither uses the shared rule nor declares
`--fields` or a `fields` Query/Option (one that splits the string itself). Review
catches that; the scan does not.

Classification per format: "projected" (through the shared code), or "not
projected" with a test that pins what it does instead.
"""

import ast
import csv
import io
import json
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

#: (file, function) -> (kind, case). `case` keys CLI_ACCEPTORS / REST_ACCEPTORS below.
ACCEPTORS = {
    ("pyrite/cli/search_commands.py", "search"): ("cli", "search"),
    ("pyrite/cli/browse_commands.py", "recent_entries"): ("cli", "recent"),
    ("pyrite/cli/browse_commands.py", "list_entries_cmd"): ("cli", "list-entries"),
    ("pyrite/cli/browse_commands.py", "batch_read"): ("cli", "batch-read"),
    ("pyrite/cli/entry_commands.py", "get_entry"): ("cli", "get"),
    ("pyrite/server/endpoints/search.py", "search"): ("rest", "GET /api/search"),
    ("pyrite/server/endpoints/entries.py", "get_entry"): ("rest", "GET /api/entries/{id}"),
    ("pyrite/server/endpoints/entries.py", "batch_read_entries"): (
        "rest",
        "POST /api/entries/batch",
    ),
    # MCP tools shape their records with the shared rule but return JSON only.
    ("pyrite/server/mcp_server.py", "_project_fields"): ("mcp", None),
}
#: Not acceptors, by design, and pinned below: `GET /api/entries` takes no `fields`.


def _own_nodes(fn):
    """The nodes of a function, not those of the functions nested in it."""
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


def _is_fields_acceptor(fn):
    for node in _own_nodes(fn):
        if isinstance(node, ast.Call):
            callee = node.func
            name = callee.id if isinstance(callee, ast.Name) else getattr(callee, "attr", None)
            if name in ("parse_fields_param", "project_fields"):
                return True
        if isinstance(node, ast.Constant) and node.value == "--fields":
            return True
    args = fn.args
    positional = args.posonlyargs + args.args
    defaults = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    pairs = [
        *zip(positional, defaults, strict=True),
        *zip(args.kwonlyargs, args.kw_defaults, strict=True),
    ]
    for arg, default in pairs:
        if arg.arg == "fields" and isinstance(default, ast.Call):
            callee = default.func
            name = callee.id if isinstance(callee, ast.Name) else getattr(callee, "attr", None)
            if name in ("Query", "Option"):
                return True
    return False


def _scan():
    found = set()
    paths = [*(ROOT / "pyrite").rglob("*.py"), *ROOT.glob("extensions/*/src/**/*.py")]
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_fields_acceptor(fn):
                found.add((str(path.relative_to(ROOT)), fn.name))
    return found


@pytest.mark.control(
    reason="every listed function already exists on dev; the test guards the next acceptor, not this change"
)
def test_the_inventory_is_what_the_source_has():
    found = _scan()
    assert found == set(ACCEPTORS), (
        "a function that accepts or applies `fields` appeared or went. Classify it in "
        f"ACCEPTORS and give it a case below. new: {sorted(found - set(ACCEPTORS))}, "
        f"gone: {sorted(set(ACCEPTORS) - found)}"
    )


@pytest.mark.control(reason="a consistency check of the inventory table against the case tables")
def test_every_listed_acceptor_has_a_case():
    cli = {case for kind, case in ACCEPTORS.values() if kind == "cli"}
    rest = {case for kind, case in ACCEPTORS.values() if kind == "rest"}
    assert cli == set(CLI_ACCEPTORS)
    assert rest == set(REST_ACCEPTORS)


@pytest.mark.control(reason="MCP was JSON only on dev; this pins the claim in the docs table")
def test_mcp_returns_json_only():
    """The MCP row is "JSON only" because the server never reaches the format registry."""
    text = (ROOT / "pyrite/server/mcp_server.py").read_text(encoding="utf-8")
    assert "format_response" not in text and "negotiate_response" not in text


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
@pytest.mark.parametrize(
    "fmt",
    [
        "csv",
        "markdown",
        "rich",
        pytest.param("yaml", marks=pytest.mark.control(reason="YAML already projected")),
        pytest.param("json", marks=pytest.mark.control(reason="JSON already projected")),
    ],
)
def test_cli_acceptor_projects_in_every_format(command, fmt, console_width):
    args = [*CLI_ACCEPTORS[command], "--fields", ",".join(FIELDS), "--format", fmt]
    console_width(200)
    with _cli_patched():
        result = CliRunner().invoke(app, args)
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


def test_search_files_is_not_projected_and_says_so(tmp_path, console_width):
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
    console_width(100)
    with (
        patch("pyrite.cli.search_commands.load_config", return_value=config),
        patch("pyrite.cli.search_commands.KBRepository", return_value=repo),
    ):
        result = CliRunner().invoke(
            app, ["search", "needle", "--files", "--fields", "status", "--format", "csv"]
        )
    assert result.exit_code == 0, result.exception or result.output
    assert "Needle" in result.stdout
    assert "status" not in result.stdout.lower()  # no projected column anywhere in the output
    assert "--fields is not applied" in result.stderr
    assert "(--files)" not in result.stderr


def test_the_rich_fallback_to_file_search_says_so_without_naming_files():
    """When the index search fails in Rich mode the command falls back to file search; a user
    who never passed `--files` must not be told about `--files`."""
    config = MagicMock()
    config.settings.search_mode = "keyword"
    with (
        patch("pyrite.cli.search_commands.load_config", return_value=config),
        patch("pyrite.cli.context.open_index_db"),
        patch("pyrite.storage.IndexManager") as mgr,
        patch("pyrite.cli.search_commands._warn_if_stale"),
        patch("pyrite.services.search_service.SearchService") as search,
        patch("pyrite.cli.search_commands._search_files") as files,
    ):
        mgr.return_value.is_empty.return_value = False
        search.return_value.search.side_effect = RuntimeError("index unreadable")
        result = CliRunner().invoke(app, ["search", "q", "--fields", "status", "--format", "rich"])
    files.assert_called_once()
    assert "--fields is not applied by file search" in result.stderr
    assert "--files" not in result.stderr


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
@pytest.mark.parametrize(
    "accept",
    [
        "text/csv",
        "text/markdown",
        pytest.param("text/yaml", marks=pytest.mark.control(reason="YAML already projected")),
    ],
)
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


@pytest.mark.control(reason="the 400 already existed; this keeps the new parse from loosening it")
def test_batch_fields_still_refuses_a_malformed_list(make_client):
    client, _, _ = make_client(kb_name="kb")
    response = _rest(
        client,
        "post",
        "/api/entries/batch",
        json={"entries": [{"entry_id": "entry-1", "kb_name": "kb"}], "fields": [1]},
    )
    assert response.status_code == 400


@pytest.mark.control(reason="documents what GET /api/entries already does with `fields`")
@pytest.mark.parametrize("accept", ["text/csv", "application/json"])
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


def _batch(client, fields, accept=None):
    body = {"entries": [{"entry_id": "entry-1", "kb_name": "kb"}], "fields": fields}
    headers = {"Accept": accept} if accept else {}
    return _rest(client, "post", "/api/entries/batch", json=body, headers=headers)


@pytest.mark.parametrize("fields", [[""], [" "], ["", " "]])
def test_batch_fields_that_clean_to_nothing_keep_the_identity_pair_only(make_client, fields):
    """A non-empty list that cleans to nothing is still a projection (it was, before the list went
    through parse_fields_param): `id` and `kb_name`, not the whole record."""
    client, _, _ = make_client(kb_name="kb")
    assert list(_batch(client, fields).json()["entries"][0]) == ["id", "kb_name"]
    csv_text = _batch(client, fields, "text/csv").text
    assert list(csv.reader(io.StringIO(csv_text)))[0] == ["id", "kb_name"]


@pytest.mark.control(
    reason="an empty list was already no projection; pins it beside the case above"
)
def test_batch_empty_fields_list_is_no_projection(make_client):
    client, _, _ = make_client(kb_name="kb")
    assert "body" in _batch(client, []).json()["entries"][0]


@pytest.mark.control(
    reason="the body list was never split on commas; this pins the documented difference from ?fields="
)
def test_batch_fields_are_not_split_on_commas_in_the_json_body(make_client):
    """Unlike `?fields=a,b`, the body's list names fields one by one: `["a,b"]` is one field."""
    client, _, _ = make_client(kb_name="kb")
    row = dict(ROW, **{"a,b": "joined", "a": "no"})
    with patch("pyrite.services.kb_service.KBService.get_entries", return_value=[row]):
        body = {"entries": [{"entry_id": "entry-1", "kb_name": "kb"}], "fields": ["a,b"]}
        record = client.post("/api/entries/batch", json=body).json()["entries"][0]
    assert record == {"id": "entry-1", "kb_name": "kb", "a,b": "joined"}
