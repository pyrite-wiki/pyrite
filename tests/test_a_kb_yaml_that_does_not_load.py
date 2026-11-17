"""A kb.yaml that does not load stops only its own KB, and is named (#1547).

Pyrite can read the file's bytes (unlike #1542's ``KB_UNREADABLE``) but cannot
turn them into a mapping: bytes that are not UTF-8, a YAML syntax error, a
duplicate key, a top level that is not a mapping, a document over the YAML
limits. Then:

1. Every other KB works, on every surface, and both servers start.
2. The KB is never read as "a KB with no schema": no reconcile retires its
   rows.
3. One function turns kb.yaml into a mapping (``read_kb_yaml``), and every
   reader of a kb.yaml goes through it.
4. Every operation that needs its schema answers one refusal on CLI, MCP and
   REST: ``KB_CONFIG_INVALID``, not retryable, one fixed message. No network
   response carries the parser's words.
5. ``kb.yaml``'s bytes never change, ``schema add-type`` and ``schema set``
   included.
6. ``KB_UNREADABLE`` keeps the meaning, message and code #1551 shipped.

The KB is named in ``kb list``, in ``index health`` (unhealthy) and in the
sync result (exit 3), with the parser's words on the local CLI only.

Parametrised over ``SHAPES`` times ``REGISTRATIONS``: a new shape or a new
way to register a KB is a new row in a list, and every test here runs on it.

Medium: real files, the real ``load_config``, the CLI in-process,
``PyriteMCPServer._dispatch_tool``, the app as ``pyrite serve`` builds it.
"""

from __future__ import annotations

import ast
import contextlib
import json
from pathlib import Path

import pytest

from pyrite.config import KBConfig, load_config
from tests._world import _create_app_without_static, build_world

ENTRY = "---\nid: x\ntype: note\ntitle: X\n---\nBody\n"
GOOD_KB_YAML = b"name: bad\ntypes: {}\n"

#: Shapes 1 to 5 of #1547's groom. Each is bytes Pyrite can read and cannot
#: load as a mapping. Shape 6 (a key with the wrong shape) is #1498's.
SHAPES: dict[str, bytes] = {
    "not-utf8": b"name: bad\ndescription: caf\xe9\n",
    "syntax-error": b"name: bad\ntypes: {note: [unclosed\n",
    "control-character": b"name: \x07bad\n",
    "duplicate-key": b"name: bad\nname: bad\n",
    "top-level-list": b"- a\n- b\n",
    "top-level-scalar": b"just a string\n",
    "yaml-1.3": b"%YAML 1.3\n---\nname: bad\n",
    "too-many-aliases": b"a: &a [x, x, x]\n"
    + b"".join(b"k%d: [*a, *a, *a, *a, *a]\n" % i for i in range(250)),
}

#: The two ways a KB is registered: in config.yaml, or by `pyrite kb add`
#: (a database row, which never runs `KBConfig.load_kb_yaml`).
REGISTRATIONS = ["config-yaml", "kb-add"]

#: The one message, written here by hand (not read from the class under test).
FIXED_MESSAGE = (
    "This knowledge base's kb.yaml could not be loaded (it is not valid UTF-8 "
    "YAML holding a mapping); the operator needs to correct the file."
)

#: Text that would be the parser's or the server's, never Pyrite's own.
PARSER_MARKERS = ("<unicode string>", "codec", "line 1", "line 2", "line 3", "column")


#: Shapes Pyrite refuses before any parser runs (``YamlLimitError``, its own
#: text): no parser's words exist for them.
LIMIT_SHAPES = {"yaml-1.3", "too-many-aliases"}


def _parser_words(raw: bytes) -> str:
    """What a parser says of ``raw``, from the parser itself (not Pyrite)."""
    from ruamel.yaml import YAML

    if raw in {SHAPES[s] for s in LIMIT_SHAPES}:
        return ""

    try:
        YAML(typ="rt").load(raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - the parser's own words, whatever they are
        return str(exc)
    return ""


@pytest.fixture(params=[(s, r) for s in SHAPES for r in REGISTRATIONS], ids=lambda p: "-".join(p))
def broken(request, tmp_path):
    """Two KBs, good and bad, both indexed; then bad's kb.yaml is replaced."""
    shape, registration = request.param
    good, bad = tmp_path / "good", tmp_path / "bad"
    good.mkdir()
    bad.mkdir()
    (bad / "kb.yaml").write_bytes(GOOD_KB_YAML)
    (bad / "x.md").write_text(ENTRY, encoding="utf-8")
    (good / "g.md").write_text(ENTRY.replace("id: x", "id: g"), encoding="utf-8")
    kbs = [KBConfig(name="good", path=good, kb_type="generic")]
    if registration == "config-yaml":
        kbs.append(KBConfig(name="bad", path=bad, kb_type="generic"))
    world = build_world(tmp_path / "w", kbs=kbs, rest=False)
    try:
        if registration == "kb-add":
            added = world.cli("kb", "add", str(bad), "--name", "bad")
            assert added.exit_code == 0, added.output
            synced = world.cli("index", "sync", "-k", "bad", "--no-embed")
            assert synced.exit_code == 0, synced.output
        assert _rows(world, "bad") == ["x"]
        (bad / "kb.yaml").write_bytes(SHAPES[shape])
        yield world, bad, SHAPES[shape]
    finally:
        world.close()


@contextlib.contextmanager
def _servers(world):
    """MCP and REST started now, from the config as it loads now (a restart)."""
    from starlette.testclient import TestClient

    from pyrite.server.mcp_server import PyriteMCPServer

    with world.pointed():
        config = load_config()
    mcp = PyriteMCPServer(config=config, tier="admin")
    app = _create_app_without_static(config, world.root)
    client = TestClient(app)
    client.__enter__()
    try:
        yield config, mcp, client
    finally:
        client.__exit__(None, None, None)
        mcp.close()
        state_db = getattr(app.state, "pyrite_db", None)
        if state_db is not None:
            state_db.close()


def _rows(world, kb):
    return sorted(
        r["id"] for r in world.db.execute_sql("SELECT id FROM entry WHERE kb_name = :k", {"k": kb})
    )


def _json(result) -> dict:
    return json.loads(result.output.strip().splitlines()[-1])


def _json_doc(result) -> dict:
    """The JSON document a command printed, whatever log lines precede it."""
    out = result.output
    return json.loads(out[out.index("{") :])


def _no_parser_text(text: str, bad: Path, raw: bytes) -> None:
    for marker in PARSER_MARKERS:
        assert marker not in text, (marker, text)
    assert str(bad) not in text, text
    words = _parser_words(raw)
    if words:
        assert words.splitlines()[0] not in text, text


# -- 1. Every other KB works, and both servers start --------------------------


def test_every_command_on_the_good_kb_works(broken):
    world, bad, raw = broken

    got = world.cli("get", "g", "-k", "good", "--format", "json")
    assert got.exit_code == 0, got.output
    assert _json_doc(got)["id"] == "g"
    found = world.cli("search", "Body", "-k", "good", "--format", "json")
    assert found.exit_code == 0, found.output
    made = world.cli("create", "-k", "good", "-t", "note", "--title", "New", "--format", "json")
    assert made.exit_code == 0, made.output
    listed = world.cli("kb", "list", "--format", "json")
    assert listed.exit_code == 0, listed.output
    assert {"good", "bad"} <= {kb["name"] for kb in _json_doc(listed)["kbs"]}
    health = world.cli("index", "health", "--format", "json")
    assert health.exit_code == 1, health.output  # unhealthy, and still a report
    assert _json_doc(health)["status"] == "unhealthy"
    sync = world.cli("index", "sync", "--no-embed")
    assert sync.exit_code == 3, sync.output  # partial: good synced, bad not
    assert _rows(world, "good") == ["g", "new"]

    with _servers(world) as (_config, mcp, client):
        assert mcp._dispatch_tool("kb_get", {"kb_name": "good", "entry_id": "g"})["entry"]
        names = {kb["name"] for kb in mcp._dispatch_tool("kb_list", {})["knowledge_bases"]}
        assert {"good", "bad"} <= names
        res = mcp._dispatch_tool("kb_index_sync", {"kb_name": "good"})
        assert "error_code" not in res, res
        assert client.get("/api/entries/g", params={"kb": "good"}).status_code == 200
        kbs = client.get("/api/kbs")
        assert kbs.status_code == 200, kbs.text
        assert {"good", "bad"} <= {kb["name"] for kb in kbs.json()["kbs"]}


def test_qa_over_every_kb_reports_it_and_goes_on(broken):
    """QA's folder gate is ``check_readable``: the bad KB is one issue in a run
    over every KB, and an assessment of it refuses with its code."""
    world, bad, raw = broken

    validated = world.cli("qa", "validate", "--format", "json")
    issues = _json_doc(validated)["issues"]  # a report, not a crash
    rows = [i for i in issues if i["kb_name"] == "bad" and i["rule"] == "kb_unreadable"]
    assert len(rows) == 1 and "KB_CONFIG_INVALID" in rows[0]["message"], issues
    assessed = world.cli("qa", "assess", "bad", "--format", "json")
    assert assessed.exit_code == 1, assessed.output
    assert _json(assessed)["error_code"] == "KB_CONFIG_INVALID"
    assert (bad / "kb.yaml").read_bytes() == raw


# -- The bad KB is named --------------------------------------------------------


def test_kb_list_names_it_with_the_code_and_the_parsers_words(broken):
    world, bad, raw = broken

    listed = world.cli("kb", "list", "--format", "json")
    assert listed.exit_code == 0, listed.output
    rows = {kb["name"]: kb for kb in _json_doc(listed)["kbs"]}
    assert "unreadable" not in rows["good"] and "error_code" not in rows["good"]
    row = rows["bad"]
    assert row["error_code"] == "KB_CONFIG_INVALID"
    assert row["unreadable"] == FIXED_MESSAGE
    # The local CLI is the operator: it is shown why (ADR-0052 N3).
    assert row["error_detail"] and row["error_detail"] != FIXED_MESSAGE


def test_index_health_names_it_and_is_unhealthy(broken):
    world, bad, raw = broken

    health = world.cli("index", "health", "--format", "json")
    assert health.exit_code == 1, health.output
    report = _json_doc(health)
    assert report["status"] == "unhealthy"
    rows = [r for r in report["schema_errors"] if r["kb"] == "bad"]
    assert len(rows) == 1, report["schema_errors"]
    assert rows[0]["error_code"] == "KB_CONFIG_INVALID"
    # Its files are not "malformed": the KB's schema is what failed to load.
    checks = report["checks"]
    for key in ("malformed_frontmatter", "unreadable_files", "missing_files"):
        assert not [r for r in checks[key] if r["kb"] == "bad"], (key, checks[key])
    words = _parser_words(raw)
    if words:  # the local CLI carries the parser's words
        assert words.splitlines()[0] in rows[0]["error"], rows[0]


def test_a_network_caller_gets_the_health_row_without_the_parsers_words(broken):
    world, bad, raw = broken

    with _servers(world) as (_config, _mcp, client):
        resp = client.get("/api/kbs/bad/health")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["healthy"] is False
        rows = body["schema_errors"]
        assert [r["error_code"] for r in rows] == ["KB_CONFIG_INVALID"]
        # `path` is this route's own field (the operator's registry entry);
        # the row itself carries no parser text and no path.
        _no_parser_text(json.dumps(rows), bad, raw)


def test_the_sync_result_names_it_on_every_surface(broken):
    world, bad, raw = broken
    from pyrite.storage.index import IndexManager

    sync = world.cli("index", "sync", "--no-embed")
    assert sync.exit_code == 3, sync.output
    assert "Not synced: bad" in sync.output

    with world.pointed():
        config = load_config()
    world.db.merge_registered_kbs(config)  # a `kb add` KB, as the CLI and servers see it
    results = IndexManager(world.db, config).sync_incremental()
    assert [(r["kb"], r["error_code"]) for r in results["unreadable_kbs"]] == [
        ("bad", "KB_CONFIG_INVALID")
    ]

    with _servers(world) as (_config, mcp, client):
        res = mcp._dispatch_tool("kb_index_sync", {})
        assert [(r["kb"], r["error_code"]) for r in res["unreadable_kbs"]] == [
            ("bad", "KB_CONFIG_INVALID")
        ], res
        resp = client.post("/api/index/sync", params={"wait": "true"})
        assert resp.status_code == 200, resp.text
        assert [(r["kb"], r["error_code"]) for r in resp.json()["unreadable_kbs"]] == [
            ("bad", "KB_CONFIG_INVALID")
        ]
        _no_parser_text(resp.text, bad, raw)
    assert _rows(world, "bad") == ["x"]


# -- 4. One code on every surface and verb --------------------------------------


def test_every_surface_and_verb_answers_one_refusal(broken):
    world, bad, raw = broken

    answers: dict[str, tuple] = {}
    cli = {
        "get": ["get", "x", "-k", "bad", "--format", "json"],
        "create": ["create", "-k", "bad", "-t", "note", "--title", "New", "--format", "json"],
        "update": ["update", "x", "-k", "bad", "--title", "T", "--format", "json"],
        "delete": ["delete", "x", "-k", "bad", "--force", "--format", "json"],
        "schema show": ["kb", "schema", "show", "bad", "--format", "json"],
    }
    for verb, args in cli.items():
        result = world.cli(*args)
        assert result.exit_code == 1, (verb, result.output)
        body = _json(result)
        answers[f"cli {verb}"] = (body["error_code"], body["error"], body["retryable"])

    with _servers(world) as (_config, mcp, client):
        tools = {
            "get": ("kb_get", {"entry_id": "x"}),
            "create": ("kb_create", {"entry_type": "note", "title": "New"}),
            "update": ("kb_update", {"entry_id": "x", "title": "T"}),
            "delete": ("kb_delete", {"entry_id": "x"}),
            "schema show": ("kb_manage", {"action": "show_schema"}),
        }
        for verb, (tool, args) in tools.items():
            res = mcp._dispatch_tool(tool, {"kb_name": "bad", **args})
            answers[f"mcp {verb}"] = (res.get("error_code"), res.get("error"), res.get("retryable"))

        calls = {
            "get": lambda: client.get("/api/entries/x", params={"kb": "bad"}),
            "create": lambda: client.post(
                "/api/entries", json={"kb": "bad", "entry_type": "note", "title": "New"}
            ),
            "update": lambda: client.put("/api/entries/x", json={"kb": "bad", "title": "T"}),
            "delete": lambda: client.delete("/api/entries/x", params={"kb": "bad"}),
            "schema show": lambda: client.get("/api/kbs/bad/schema"),
        }
        for verb, call in calls.items():
            resp = call()
            assert resp.status_code == 500, (verb, resp.text)
            _no_parser_text(resp.text, bad, raw)
            detail = resp.json()["detail"]
            answers[f"rest {verb}"] = (detail["code"], detail["message"], detail["retryable"])

        # The other KB is served by the same servers.
        assert "entry" in mcp._dispatch_tool("kb_get", {"kb_name": "good", "entry_id": "g"})
        assert client.get("/api/entries/g", params={"kb": "good"}).status_code == 200

    assert len(set(answers.values())) == 1, answers
    code, message, retryable = next(iter(answers.values()))
    assert (code, message, retryable) == ("KB_CONFIG_INVALID", FIXED_MESSAGE, False)
    _no_parser_text(message, bad, raw)
    assert _rows(world, "bad") == ["x"]  # nothing deleted from the index


# -- 2. The rows are kept through every reconcile --------------------------------


def test_every_reconcile_keeps_its_rows(broken):
    world, bad, raw = broken

    build = world.cli("index", "build", "--no-embed")
    assert build.exit_code == 3, build.output
    assert _rows(world, "bad") == ["x"]

    reindex = world.cli("kb", "reindex", "-k", "bad", "--format", "json")
    assert reindex.exit_code == 1, reindex.output
    assert _json(reindex)["error_code"] == "KB_CONFIG_INVALID"
    assert _rows(world, "bad") == ["x"]

    one = world.cli("index", "sync", "-k", "bad", "--no-embed")
    assert one.exit_code == 1, one.output
    assert _rows(world, "bad") == ["x"]

    every = world.cli("index", "sync", "--no-embed")
    assert every.exit_code == 3, every.output
    assert _rows(world, "bad") == ["x"]
    assert _rows(world, "good") == ["g"]


def test_a_config_loaded_before_the_file_broke_keeps_its_rows(broken):
    """The groom's finding: a config (a running server's) loaded while kb.yaml
    was valid; the file breaks; the in-process rebuild must refuse, not read
    every file as malformed and retire every row."""
    world, bad, raw = broken
    from pyrite.config import PyriteConfig
    from pyrite.services.kb_registry_service import KBRegistryService
    from pyrite.storage.index import IndexManager

    config = world.config  # loaded by build_world, before the break
    if config.get_kb("bad") is None:  # the kb-add case: the registry's row
        bad_cfg = KBRegistryService(config, world.db, IndexManager(world.db, config)).get_kb_config(
            "bad"
        )
        config = PyriteConfig(
            knowledge_bases=[*config.knowledge_bases, bad_cfg], settings=config.settings
        )
    mgr = IndexManager(world.db, config)
    with pytest.raises(Exception) as refused:
        mgr.index_kb("bad")
    assert getattr(refused.value, "error_code", None) == "KB_CONFIG_INVALID", refused.value
    assert _rows(world, "bad") == ["x"]
    assert mgr.index_all() == {"good": 1}
    assert _rows(world, "bad") == ["x"]
    assert mgr.sync_incremental()["removed"] == 0
    assert _rows(world, "bad") == ["x"]


# -- 5. kb.yaml is never written ---------------------------------------------------


def test_kb_yaml_is_unchanged_after_every_command(broken, tmp_path):
    world, bad, raw = broken
    schema_file = tmp_path / "schema.yaml"
    schema_file.write_text("types:\n  note:\n    description: a note\n", encoding="utf-8")

    commands = [
        ["kb", "schema", "show", "bad"],
        ["kb", "schema", "add-type", "bad", "--type", "memo"],
        ["kb", "schema", "remove-type", "bad", "--type", "note"],
        ["kb", "schema", "set", "bad", "--schema-file", str(schema_file)],
        ["create", "-k", "bad", "-t", "note", "--title", "New"],
        ["update", "x", "-k", "bad", "--title", "T"],
        ["delete", "x", "-k", "bad", "--force"],
        ["get", "x", "-k", "bad"],
        ["kb", "list"],
        ["kb", "reindex", "-k", "bad"],
        ["index", "sync", "--no-embed"],
        ["index", "build", "--no-embed"],
        ["index", "health"],
        ["kb", "health", "bad"],
    ]
    for args in commands:
        world.cli(*args)
        assert (bad / "kb.yaml").read_bytes() == raw, args
    with _servers(world) as (_config, mcp, client):
        for action, extra in [
            ("add_type", {"type_name": "memo", "type_def": {}}),
            ("set_schema", {"schema": {"types": {}}}),
            ("remove_type", {"type_name": "note"}),
        ]:
            res = mcp._dispatch_tool("kb_manage", {"action": action, "kb_name": "bad", **extra})
            assert res.get("error_code") == "KB_CONFIG_INVALID", (action, res)
            assert (bad / "kb.yaml").read_bytes() == raw, action
    assert (bad / "x.md").read_text(encoding="utf-8") == ENTRY


# -- 3. One function turns kb.yaml into a mapping -------------------------------


@pytest.mark.parametrize("shape", list(SHAPES))
def test_every_kb_yaml_reader_answers_the_one_error(shape, tmp_path):
    from pyrite.config import PyriteConfig, Settings
    from pyrite.exceptions import public_text
    from pyrite.schema import KBSchema
    from pyrite.services.schema_service import SchemaService

    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "kb.yaml").write_bytes(SHAPES[shape])
    kb = KBConfig(name="bad", path=bad)
    readers = {
        "KBSchema.from_yaml": lambda: KBSchema.from_yaml(bad / "kb.yaml"),
        "KBConfig.kb_schema": lambda: kb.kb_schema,
        "KBConfig.check_readable": kb.check_readable,
        "SchemaService.show_schema": lambda: SchemaService(
            PyriteConfig(knowledge_bases=[kb], settings=Settings(index_path=tmp_path / "i.db"))
        ).show_schema("bad"),
    }
    for name, read in readers.items():
        with pytest.raises(Exception) as caught:
            read()
        assert getattr(caught.value, "error_code", None) == "KB_CONFIG_INVALID", (name, caught)
        assert caught.value.__cause__ is not None, name  # the parser's error, kept
        assert public_text(caught.value, local=True) == FIXED_MESSAGE, name

    # The load records it on the KB and goes on.
    assert kb.load_kb_yaml() is False
    assert kb.unreadable == FIXED_MESSAGE
    assert (bad / "kb.yaml").read_bytes() == SHAPES[shape]


@pytest.mark.parametrize("shape", list(SHAPES))
def test_read_kb_yaml_is_the_one_function(shape, tmp_path):
    from pyrite.exceptions import KBConfigInvalidError
    from pyrite.utils.yaml import read_kb_yaml

    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "kb.yaml").write_bytes(SHAPES[shape])
    with pytest.raises(KBConfigInvalidError):
        read_kb_yaml(bad)
    (bad / "kb.yaml").write_bytes(GOOD_KB_YAML)
    assert dict(read_kb_yaml(bad)) == {"name": "bad", "types": {}}
    (bad / "kb.yaml").unlink()
    assert read_kb_yaml(bad) == {}


@pytest.mark.control(reason="regime: an empty kb.yaml is a KB with no schema today, and stays so")
def test_an_empty_kb_yaml_is_a_kb_with_no_schema(tmp_path):
    kb_dir = tmp_path / "empty"
    kb_dir.mkdir()
    (kb_dir / "kb.yaml").write_bytes(b"")
    kb = KBConfig(name="empty", path=kb_dir)
    assert kb.load_kb_yaml() is True and kb.unreadable is None
    kb.check_readable()
    assert kb.kb_schema.types == {}


def test_one_bad_kb_yaml_does_not_stop_the_load(tmp_path):
    """Invariant 1 at its root: load_config records the error on that KB."""
    world_kbs = []
    for name in ("good", "bad"):
        (tmp_path / name).mkdir()
        world_kbs.append(KBConfig(name=name, path=tmp_path / name, kb_type="generic"))
    world = build_world(tmp_path / "w", kbs=world_kbs, rest=False)
    try:
        for shape, raw in SHAPES.items():
            (tmp_path / "bad" / "kb.yaml").write_bytes(raw)
            with world.pointed():
                config = load_config()
            assert config.get_kb("good").unreadable is None, shape
            assert config.get_kb("bad").unreadable == FIXED_MESSAGE, shape
    finally:
        world.close()


#: The only readers of a KB's own YAML through ``load_yaml_file(kb_root=...)``:
#: the one kb.yaml function, and a plugin's board.yaml (not a kb.yaml; out of
#: #1547's scope, and why the error is not raised inside load_yaml_file).
KB_ROOT_YAML_READERS = {
    ("pyrite/utils/yaml.py", "read_kb_yaml"),
    ("extensions/software-kb/src/pyrite_software_kb/board.py", "load_board_config"),
}


def test_kb_yaml_is_turned_into_a_mapping_in_one_place():
    root = Path(__file__).resolve().parents[1]
    files = [*root.glob("pyrite/**/*.py"), *root.glob("extensions/*/src/**/*.py")]
    found = set()
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for func in ast.walk(tree):
            if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for node in ast.walk(func):
                if (
                    isinstance(node, ast.Call)
                    and getattr(node.func, "id", getattr(node.func, "attr", None))
                    == "load_yaml_file"
                    and any(kw.arg == "kb_root" for kw in node.keywords)
                ):
                    found.add((str(path.relative_to(root)), func.name))
    assert found == KB_ROOT_YAML_READERS


# -- 6. KB_UNREADABLE keeps its meaning --------------------------------------------


def test_health_names_a_kb_unreadable_kb_in_the_shape_1551_shipped(tmp_path, monkeypatch):
    """``unreadable_kbs`` in index health stays ``{kb, error}``: #1547 adds
    ``error_code`` to the sync result's rows, not to this report's. The OS
    refusal is stood in for by the gate itself, so the test runs as root too
    (the permission tests of #1551 skip there)."""
    from pyrite.storage.index import IndexManager
    from pyrite.utils.kb_files import KBFilesUnreadableError

    (tmp_path / "locked").mkdir()
    world = build_world(
        tmp_path / "w",
        kbs=[KBConfig(name="locked", path=tmp_path / "locked", kb_type="generic")],
        rest=False,
    )
    try:

        def refuse(self):
            raise KBFilesUnreadableError()

        monkeypatch.setattr(KBConfig, "check_readable", refuse)
        health = IndexManager(world.db, world.config).check_health()
        assert health["unreadable_kbs"] == [{"kb": "locked", "error": KB_UNREADABLE_MESSAGE}]
    finally:
        world.close()


#: #1551's message, written here by hand.
KB_UNREADABLE_MESSAGE = (
    "Pyrite could not read this knowledge base's files: the operating "
    "system refused (for example, a folder or file Pyrite may not open)."
)


def test_kb_unreadable_is_unchanged_and_distinct():
    from pyrite.exceptions import KBConfigInvalidError, KBUnreadableError
    from pyrite.utils.kb_files import KBFilesUnreadableError

    assert KBFilesUnreadableError.error_code == "KB_UNREADABLE"
    assert KBFilesUnreadableError.MESSAGE == KB_UNREADABLE_MESSAGE
    assert KBFilesUnreadableError.public_message is None
    assert not issubclass(KBFilesUnreadableError, KBConfigInvalidError)
    assert not issubclass(KBConfigInvalidError, KBFilesUnreadableError)
    assert issubclass(KBConfigInvalidError, KBUnreadableError)
    assert KBConfigInvalidError.error_code == "KB_CONFIG_INVALID"
    assert KBConfigInvalidError.retryable is False
    assert KBConfigInvalidError.public_message == KBConfigInvalidError.MESSAGE
