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
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.config import KBConfig, PyriteConfig, Settings
from tests.search_verdict_seed import cli_world, make_cli_world, seed_entry_world

ROOT = Path(__file__).resolve().parent.parent
FIELDS = ["status", "missing"]
COLUMNS = ["id", "kb_name", "status", "missing"]
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
    # The CLI search row's field list (`--fields` > PYRITE_SEARCH_FIELDS > the lean
    # default); `search` above applies it, tests/test_search_contract.py pins it.
    ("pyrite/services/read_shaping.py", "resolve_search_fields"): ("helper", None),
    # The CLI's `--fields` parser: `parse_fields_param` plus `*` meaning every field.
    ("pyrite/services/read_shaping.py", "parse_cli_fields"): ("helper", None),
    # File search names the ignored `--fields` / PYRITE_SEARCH_FIELDS on stderr.
    ("pyrite/cli/search_commands.py", "_warn_fields_not_applied"): ("helper", None),
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


@pytest.fixture
def cli_config(tmp_path):
    """A real KB and index holding `entry-1`; the CLI is pointed at it, nothing is mocked."""
    return make_cli_world(tmp_path)


CLI_ACCEPTORS = {
    "search": ["search", "excluded"],
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
def test_cli_acceptor_projects_in_every_format(command, fmt, console_width, cli_config):
    args = [*CLI_ACCEPTORS[command], "--fields", ",".join(FIELDS), "--format", fmt]
    console_width(200)
    with cli_world(cli_config):
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


@pytest.mark.parametrize("command", CLI_ACCEPTORS)
@pytest.mark.parametrize("fmt", ["json", "yaml"])
def test_cli_acceptor_star_means_every_field(command, fmt, cli_config):
    """`--fields '*'` is the whole record on every command that takes `--fields`, not
    the identity pair (the parser used to treat `*` as one unknown name)."""
    args = [*CLI_ACCEPTORS[command], "--fields", "*", "--format", fmt]
    with cli_world(cli_config):
        result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.exception or result.output
    data = json.loads(result.stdout) if fmt == "json" else yaml.safe_load(result.stdout)
    record = data.get("results", data.get("entries", [data]))[0]
    # The index columns the lean search row drops, on every command: not the identity pair.
    assert {"id", "kb_name", "entry_type", "title", "status", "summary", "file_path"} <= set(record)
    assert {"created_at", "indexed_at", "metadata"} <= set(record), sorted(record)
    # Search leaves `body` to --include-body, as it did before `--fields`.
    assert ("body" in record) == (command != "search")


def test_search_files_is_not_projected_and_says_so(tmp_path, console_width):
    """`search --files` is the pre-index fallback. It prints its own Rich table
    (KB, Type, Title, ID) whatever `--format` and `--fields` say; it now tells the
    caller on stderr that `--fields` was not applied."""
    kb_path = tmp_path / "kb"
    kb_path.mkdir()
    (kb_path / "a.md").write_text("---\nid: a\ntitle: Needle\ntype: note\n---\nneedle body\n")
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="kb", path=kb_path, kb_type="generic")],
        settings=Settings(index_path=tmp_path / "index.db"),
    )
    console_width(100)
    with cli_world(config):
        result = CliRunner().invoke(
            app, ["search", "needle", "--files", "--fields", "status", "--format", "csv"]
        )
    assert result.exit_code == 0, result.exception or result.output
    assert "Needle" in result.stdout
    assert "status" not in result.stdout.lower()  # no projected column anywhere in the output
    assert "--fields is not applied" in result.stderr
    assert "(--files)" not in result.stderr


def test_search_files_says_so_when_the_variable_is_what_was_ignored(tmp_path):
    """`PYRITE_SEARCH_FIELDS` is ignored by file search as `--fields` is: the caller is told."""
    kb_path = tmp_path / "kb"
    kb_path.mkdir()
    (kb_path / "a.md").write_text("---\nid: a\ntitle: Needle\ntype: note\n---\nneedle body\n")
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="kb", path=kb_path, kb_type="generic")],
        settings=Settings(index_path=tmp_path / "index.db"),
    )
    with cli_world(config):
        result = CliRunner().invoke(
            app, ["search", "needle", "--files"], env={"PYRITE_SEARCH_FIELDS": "title"}
        )
    assert result.exit_code == 0, result.exception or result.output
    assert "PYRITE_SEARCH_FIELDS is not applied by file search" in result.stderr


def test_the_rich_fallback_to_file_search_says_so_without_naming_files(tmp_path):
    """When the index search fails in Rich mode the command falls back to file search; a user
    who never passed `--files` must not be told about `--files`.

    The index here is not a database, so the failure is the real one; the KB's files are
    on disk, so the fallback really searches them."""
    config = make_cli_world(tmp_path)
    assert (tmp_path / "kb" / "notes" / "entry-1.md").exists()  # the fallback has a file to find
    (tmp_path / "index.db").write_bytes(b"not a database" * 100)
    with cli_world(config):
        result = CliRunner().invoke(
            app, ["search", "excluded", "--fields", "status", "--format", "rich"]
        )
    assert result.exit_code == 0, result.exception or result.output
    assert "--fields is not applied by file search" in result.stderr
    assert "--files" not in result.stderr
    assert "entry-1" in result.stdout  # the file search found the entry


# --------------------------------------------------------------- REST acceptors


@pytest.fixture
def rest_client(make_client):
    """A real app over a real KB and index holding `entry-1`."""
    client, config, db = make_client(kb_name="kb")
    seed_entry_world(config, db)
    return client


def _rest(client, method, url, **kwargs):
    return getattr(client, method)(url, **kwargs)


REST_ACCEPTORS = {
    "GET /api/search": (
        "get",
        "/api/search",
        {"params": {"q": "excluded", "fields": "status,missing"}},
    ),
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
def test_rest_acceptor_projects_in_every_negotiated_format(rest_client, acceptor, accept):
    client = rest_client
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


def test_batch_fields_are_cleaned_like_the_query_string_form(rest_client):
    """The JSON list goes through the same parse as `?fields=`: stripped, deduplicated,
    empties dropped. Before, " status " was a key that matched nothing."""
    client = rest_client
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
def test_batch_fields_still_refuses_a_malformed_list(rest_client):
    client = rest_client
    response = _rest(
        client,
        "post",
        "/api/entries/batch",
        json={"entries": [{"entry_id": "entry-1", "kb_name": "kb"}], "fields": [1]},
    )
    assert response.status_code == 400


@pytest.mark.control(reason="documents what GET /api/entries already does with `fields`")
@pytest.mark.parametrize("accept", ["text/csv", "application/json"])
def test_get_entries_list_takes_no_fields(rest_client, accept):
    """`GET /api/entries` has no `fields` parameter (CLI `list-entries` does); documented, pinned:
    the parameter changes nothing."""
    client = rest_client
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
def test_batch_fields_that_clean_to_nothing_keep_the_identity_pair_only(rest_client, fields):
    """A non-empty list that cleans to nothing is still a projection (it was, before the list went
    through parse_fields_param): `id` and `kb_name`, not the whole record."""
    client = rest_client
    assert list(_batch(client, fields).json()["entries"][0]) == ["id", "kb_name"]
    csv_text = _batch(client, fields, "text/csv").text
    assert list(csv.reader(io.StringIO(csv_text)))[0] == ["id", "kb_name"]


@pytest.mark.control(
    reason="an empty list was already no projection; pins it beside the case above"
)
def test_batch_empty_fields_list_is_no_projection(rest_client):
    client = rest_client
    assert "body" in _batch(client, []).json()["entries"][0]


@pytest.mark.control(
    reason="the body list was never split on commas; this pins the documented difference from ?fields="
)
def test_batch_fields_are_not_split_on_commas_in_the_json_body(rest_client):
    """Unlike `?fields=a,b`, the body's list names fields one by one: `["status,title"]` is one
    field, which no record has. Split on the comma it would project `status` and `title`, both of
    which this entry has (the control call below shows the projection reads them)."""
    client = rest_client
    entries = [{"entry_id": "entry-1", "kb_name": "kb"}]
    one_by_one = client.post(
        "/api/entries/batch", json={"entries": entries, "fields": ["status", "title"]}
    ).json()["entries"][0]
    assert one_by_one == {"id": "entry-1", "kb_name": "kb", "status": "waiting", "title": "T"}
    joined = client.post(
        "/api/entries/batch", json={"entries": entries, "fields": ["status,title"]}
    ).json()["entries"][0]
    assert joined == {"id": "entry-1", "kb_name": "kb"}
