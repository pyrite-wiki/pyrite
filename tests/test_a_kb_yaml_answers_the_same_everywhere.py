"""Whatever a path named kb.yaml is now, every reader in every process gives
that KB the same answer, and nothing else stops (#1547, fix round 1).

The answer is a mapping, or one typed refusal. It holds:

- **in every process state** (``STATES``): loaded with the file broken;
  loaded good with the schema read once, then broken (a running server);
  loaded broken, then repaired. ``KBConfig.kb_schema`` keeps its cache only
  while kb.yaml is the file it read (``_kb_yaml_stamp``), and
  ``unreadable``/``readability_error`` ask the file each time;
- **for every kind of path** (``KINDS``): a regular file of each shape, a
  directory, a FIFO, a socket, a dangling link, a link out of the KB, a link
  loop, nesting past the recursion limit (flow and block), a large file.
  ``read_kb_yaml`` lets out a mapping or a ``PyriteError``, nothing else, and
  the other KB, ``pyrite kb list`` and ``pyrite index health`` still answer;
- **for every walk over more than one KB**: each CLI command whose ``-k`` is
  optional, run without it, and each registered MCP tool whose ``kb_name`` is
  optional, called without it, is not stopped by the broken KB (the lists
  come from the CLI tree and the MCP tool list, so a new member is walked);
  ``qa duplicates`` and ``list_edge_types`` name it and go on;
- **in every print site**: every command of ``pyrite``, ``pyrite-read`` and
  ``pyrite-admin`` that names the broken KB prints the one fixed message for
  ``KB_CONFIG_INVALID`` (``str()`` is that message).

The walks run each command with placeholder values for its required options
(``args_for``), so a command whose placeholder does not reach a KB walk is
walked without proving much; ``qa duplicates`` and ``list_edge_types`` have
their own tests with real arguments.
"""

from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path

import pytest

from pyrite.config import KBConfig, load_config
from tests._world import build_world
from tests.test_a_kb_yaml_that_does_not_load import (
    ENTRY,
    FIXED_MESSAGE,
    GOOD_KB_YAML,
    SHAPES,
    _json_doc,
    _rows,
)

BROKEN = SHAPES["duplicate-key"]


def _two_kbs(tmp_path, *, bad_yaml: bytes = GOOD_KB_YAML):
    """good and bad, both in config.yaml, both indexed."""
    good, bad = tmp_path / "good", tmp_path / "bad"
    good.mkdir()
    bad.mkdir()
    (bad / "kb.yaml").write_bytes(bad_yaml)
    (bad / "x.md").write_text(ENTRY, encoding="utf-8")
    (good / "g.md").write_text(ENTRY.replace("id: x", "id: g"), encoding="utf-8")
    world = build_world(
        tmp_path / "w",
        kbs=[
            KBConfig(name="good", path=good, kb_type="generic"),
            KBConfig(name="bad", path=bad, kb_type="generic"),
        ],
        rest=False,
    )
    return world, good, bad


# -- (a) Process states x readers ---------------------------------------------


#: How the process came to hold its config, and whether kb.yaml is broken now.
STATES = {
    "loaded-broken": True,
    "broke-after-load": True,
    "repaired-after-load": False,
}


@pytest.mark.parametrize("shape", list(SHAPES))
@pytest.mark.parametrize("state", list(STATES))
def test_every_reader_answers_what_the_file_is_now(state, shape, tmp_path):
    """One config object, as a running server holds it, read after the file
    changed: every reader answers the file as it is now."""
    from pyrite.exceptions import KBUnreadableError
    from pyrite.server.mcp_server import PyriteMCPServer
    from pyrite.services.index_health import health_verdict
    from pyrite.services.schema_service import SchemaService
    from pyrite.storage.index import IndexManager

    world, good, bad = _two_kbs(tmp_path)
    kb_yaml = bad / "kb.yaml"
    try:
        if state in ("loaded-broken", "repaired-after-load"):
            kb_yaml.write_bytes(SHAPES[shape])
        with world.pointed():
            config = load_config()
        mcp = PyriteMCPServer(config=config, tier="admin")
        try:
            bad_cfg = config.get_kb("bad")
            if state == "broke-after-load":
                assert bad_cfg.kb_schema is not None  # read once, cached
                assert mcp._dispatch_tool("kb_get", {"kb_name": "bad", "entry_id": "x"})["entry"]
                kb_yaml.write_bytes(SHAPES[shape])
            elif state == "repaired-after-load":
                with pytest.raises(KBUnreadableError):
                    _ = bad_cfg.kb_schema
                assert bad_cfg.unreadable == FIXED_MESSAGE
                kb_yaml.write_bytes(GOOD_KB_YAML)
            broken = STATES[state]
            mgr = IndexManager(world.db, config)

            # The KB's own readers.
            readers = {
                "kb_schema": lambda: bad_cfg.kb_schema,
                "check_readable": bad_cfg.check_readable,
                "schema show": lambda: SchemaService(config).show_schema("bad"),
                "mcp kb_get": lambda: _refused(
                    mcp._dispatch_tool("kb_get", {"kb_name": "bad", "entry_id": "x"})
                ),
                "mcp kb_create": lambda: _refused(
                    mcp._dispatch_tool(
                        "kb_create", {"kb_name": "bad", "entry_type": "note", "title": "N"}
                    )
                ),
            }
            for name, read in readers.items():
                if broken:
                    with pytest.raises(KBUnreadableError) as caught:
                        read()
                    assert caught.value.error_code == "KB_CONFIG_INVALID", name
                else:
                    read()
            if broken:
                assert bad_cfg.unreadable == FIXED_MESSAGE
                assert bad_cfg.readability_error().error_code == "KB_CONFIG_INVALID"
            else:
                assert bad_cfg.unreadable is None and bad_cfg.readability_error() is None

            # Health: the verdict and the file comparison.
            health = mgr.check_health()
            rows = [r for r in health["schema_errors"] if r["kb"] == "bad"]
            for key in ("malformed_frontmatter", "missing_files", "unindexed_files"):
                assert not [r for r in health[key] if r["kb"] == "bad"], (key, health[key])
            if broken:
                assert [r.get("error_code") for r in rows] == ["KB_CONFIG_INVALID"], health
                assert health_verdict(health)["status"] == "unhealthy"
            else:
                assert rows == [] and health_verdict(health)["status"] != "unhealthy", health

            # Sync names it, keeps its rows; repaired, it syncs.
            synced = mgr.sync_incremental()
            assert synced["removed"] == 0
            named = [r["kb"] for r in synced["unreadable_kbs"]]
            assert named == (["bad"] if broken else []), synced
            # Broken: the row set is unchanged. Repaired: kb_create above wrote "n".
            assert _rows(world, "bad") == (["x"] if broken else ["n", "x"])

            # The other KB is unaffected throughout.
            assert mcp._dispatch_tool("kb_get", {"kb_name": "good", "entry_id": "g"})["entry"]
            assert kb_yaml.read_bytes() == (SHAPES[shape] if broken else GOOD_KB_YAML)
        finally:
            mcp.close()
    finally:
        world.close()


def _refused(res: dict) -> dict:
    """An MCP refusal envelope as the exception it reports, for one check."""
    from pyrite.exceptions import KBUnreadableError

    if res.get("error_code"):
        err = KBUnreadableError(res.get("error", ""))
        err.error_code = res["error_code"]
        raise err
    return res


# -- (c) What a path named kb.yaml can be --------------------------------------


def _make(kind: str, kb: Path) -> None:
    path = kb / "kb.yaml"
    path.unlink()
    if kind in SHAPES:
        path.write_bytes(SHAPES[kind])
    elif kind == "directory":
        path.mkdir()
    elif kind == "fifo":
        if not hasattr(os, "mkfifo"):
            pytest.skip("this platform cannot create a FIFO")
        os.mkfifo(path)
    elif kind == "socket":
        if not hasattr(socket, "AF_UNIX"):
            pytest.skip("this platform has no Unix sockets")
        sock = socket.socket(socket.AF_UNIX)
        try:
            sock.bind(str(path))
        except OSError as e:  # a path longer than sun_path holds
            pytest.skip(f"cannot bind a Unix socket at this path: {e}")
        finally:
            sock.close()
    elif kind == "dangling-link":
        path.symlink_to(kb / "nothere.yaml")
    elif kind == "link-out":
        path.symlink_to(kb.parent / "outside.yaml")
        (kb.parent / "outside.yaml").write_text("name: outside\n", encoding="utf-8")
    elif kind == "link-loop":
        path.symlink_to(path)
    elif kind == "deep-flow":
        limit = sys.getrecursionlimit() + 100
        path.write_text("a: " + "[" * limit + "]" * limit + "\n", encoding="utf-8")
    elif kind == "deep-block":
        limit = sys.getrecursionlimit() + 100
        lines = "".join(" " * i + f"k{i}:\n" for i in range(limit))
        path.write_text(lines + " " * limit + "v: 1\n", encoding="utf-8")
    elif kind == "large":
        path.write_text("name: big\nnote: " + "x" * (8 * 1024 * 1024) + "\n", encoding="utf-8")
    else:
        raise AssertionError(kind)


#: Every kind, and what ``read_kb_yaml`` answers for it, written by hand:
#: a mapping ("mapping", or "empty" for ``{}``) or an error code.
KINDS: dict[str, str] = {
    **dict.fromkeys(SHAPES, "KB_CONFIG_INVALID"),
    "directory": "KB_CONFIG_INVALID",
    "fifo": "KB_CONFIG_INVALID",
    # The OS refuses to open a socket (ENXIO): #1551's answer.
    "socket": "KB_UNREADABLE",
    # Nothing is there: as for no kb.yaml.
    "dangling-link": "empty",
    "link-loop": "empty",
    "link-out": "OUTSIDE_KB_ROOT",
    "deep-flow": "KB_CONFIG_INVALID",
    "deep-block": "KB_CONFIG_INVALID",
    "large": "mapping",
}


#: Kinds whose answer did not change in round 1 (they answered before it):
#: pinned as regimes, marked so verify-red reads them as deliberate.
ANSWERED_BEFORE = {*SHAPES, "socket", "dangling-link", "link-loop", "link-out", "large"}


@pytest.mark.parametrize(
    "kind",
    [
        pytest.param(
            kind,
            marks=pytest.mark.control(reason="a kind that already answered; pinned as a regime"),
        )
        if kind in ANSWERED_BEFORE
        else kind
        for kind in KINDS
    ],
)
def test_every_kind_of_path_answers_a_mapping_or_a_typed_error(kind, tmp_path):
    from pyrite.exceptions import PyriteError
    from pyrite.utils.yaml import read_kb_yaml

    world, good, bad = _two_kbs(tmp_path)
    try:
        _make(kind, bad)
        expected = KINDS[kind]
        if expected in ("empty", "mapping"):
            answer = read_kb_yaml(bad)
            assert isinstance(answer, dict), type(answer)
            assert expected == ("empty" if answer == {} else "mapping"), (kind, answer)
        else:
            with pytest.raises(PyriteError) as caught:  # nothing else leaves it
                read_kb_yaml(bad)
            assert caught.value.error_code == expected, (kind, caught.value.error_code)

        got = world.cli("get", "g", "-k", "good", "--format", "json")
        assert got.exit_code == 0, got.output
        listed = world.cli("kb", "list", "--format", "json")
        assert listed.exit_code == 0, listed.output
        assert {"good", "bad"} <= {kb["name"] for kb in _json_doc(listed)["kbs"]}
        health = world.cli("index", "health", "--format", "json")
        assert health.exit_code in (0, 1), health.output  # a report either way
        assert _json_doc(health)["status"] in ("healthy", "warning", "unhealthy")
        assert _rows(world, "bad") == ["x"]
    finally:
        world.close()


# -- (b) Every walk over more than one KB ---------------------------------------

#: Commands the walks do not run, and why: each serves or blocks.
NOT_WALKED = {"serve", "mcp", "mcp-setup", "watch", "init", "tui"}


def _cli_leaves():
    """(root, path, click command) for every leaf of the three console scripts."""
    import typer

    from tests.outcome_walk import ROOTS, leaves, root_app

    for root in ROOTS:
        for path, cmd in leaves(typer.main.get_command(root_app(root))):
            if not set(path) & NOT_WALKED:
                yield root, path, cmd


def _kb_params(cmd):
    """The command's KB option (``-k``/``--kb``) and KB argument, if any."""
    option = [p for p in cmd.params if {"-k", "--kb"} & set(getattr(p, "opts", []))]
    argument = [
        p
        for p in cmd.params
        if p.param_type_name == "argument" and p.name in ("kb_name", "kb", "name")
    ]
    return option, argument


def _args(cmd, *, kb: str | None) -> list[str]:
    option, argument = _kb_params(cmd)
    args: list[str] = []
    for p in cmd.params:
        if p in option:
            if kb is not None:
                args += [p.opts[0], kb]
            continue
        if p in argument:
            args += [kb or "good"]
            continue
        if not p.required:
            continue
        value = "note" if p.name in ("entry_type", "type", "type_name") else "x"
        args += [value] if p.param_type_name == "argument" else [p.opts[0], value]
    if any("--format" in getattr(p, "opts", []) for p in cmd.params):
        args += ["--format", "json"]
    return args


def _error_code(output: str) -> str | None:
    try:
        doc = json.loads(output[output.index("{") :])
    except ValueError:
        return None
    return doc.get("error_code") if isinstance(doc, dict) else None


@pytest.fixture
def one_broken(tmp_path, monkeypatch):
    # The walks run commands that write relative to the working directory
    # (an export's default folder): run them where nothing is kept.
    (tmp_path / "cwd").mkdir()
    monkeypatch.chdir(tmp_path / "cwd")
    world, good, bad = _two_kbs(tmp_path)
    (bad / "kb.yaml").write_bytes(BROKEN)
    try:
        yield world, bad
    finally:
        world.close()


def _run(world, bad, root, path, args):
    """Run one command; put config.yaml and kb.yaml back after it."""
    from tests.outcome_walk import root_app

    config_file = world.config_dir / "config.yaml"
    saved = config_file.read_bytes()
    try:
        return world.cli(*path, *args, input="", app=root_app(root))
    finally:
        config_file.write_bytes(saved)
        (bad / "kb.yaml").write_bytes(BROKEN)


def test_no_cli_command_that_spans_kbs_is_stopped_by_one_kb_yaml(one_broken):
    """Every command that does not require a KB (no required ``-k``, no KB
    argument), run without one: such a command spans the KBs, or none."""
    world, bad = one_broken
    walked, stopped = [], []
    for root, path, cmd in _cli_leaves():
        option, argument = _kb_params(cmd)
        if argument or (option and option[0].required):
            continue
        result = _run(world, bad, root, path, _args(cmd, kb=None))
        walked.append(" ".join((root, *path)))
        if _error_code(result.output) == "KB_CONFIG_INVALID":
            stopped.append(" ".join((root, *path)))
    assert len(walked) > 20, walked  # the walk found the commands
    assert stopped == [], stopped


def test_no_mcp_tool_that_spans_kbs_is_stopped_by_one_kb_yaml(one_broken):
    """Every registered tool whose ``kb_name`` is optional, called without it."""
    from pyrite.server.mcp_server import PyriteMCPServer

    world, bad = one_broken
    with world.pointed():
        config = load_config()
    mcp = PyriteMCPServer(config=config, tier="admin")
    placeholder = {"string": "x", "integer": 1, "number": 1, "boolean": False}
    walked, stopped = [], []
    try:
        for tool in mcp.get_tools_list():
            schema = tool.get("inputSchema") or {}
            props, required = schema.get("properties", {}), schema.get("required", [])
            if "kb_name" not in props or "kb_name" in required:
                continue
            args = {k: placeholder.get(props[k].get("type"), "x") for k in required}
            res = mcp._dispatch_tool(tool["name"], args)
            walked.append(tool["name"])
            if isinstance(res, dict) and res.get("error_code") == "KB_CONFIG_INVALID":
                stopped.append(tool["name"])
    finally:
        mcp.close()
    assert len(walked) > 20, walked
    assert stopped == [], stopped


def test_qa_duplicates_names_the_kb_and_compares_the_others(one_broken):
    world, bad = one_broken
    good = world.kb_path("good")
    for i in (1, 2):
        (good / f"dup-{i}.md").write_text(
            f"---\nid: dup-{i}\ntype: note\ntitle: The same title\n---\nB\n", encoding="utf-8"
        )
    assert world.cli("index", "sync", "-k", "good", "--no-embed").exit_code == 0

    result = world.cli("qa", "duplicates", "--type", "note", "--format", "json")
    assert result.exit_code == 3, result.output  # partial: bad left out
    doc = _json_doc(result)
    assert [(r["kb"], r["error_code"]) for r in doc["unreadable_kbs"]] == [
        ("bad", "KB_CONFIG_INVALID")
    ]
    assert doc["unreadable_kbs"][0]["error"] == FIXED_MESSAGE
    assert {(p["a"]["id"], p["b"]["id"]) for p in doc["pairs"]} >= {("dup-1", "dup-2")}


def test_list_edge_types_names_the_kb_and_lists_the_others(one_broken):
    from pyrite.server.mcp_server import PyriteMCPServer

    world, bad = one_broken
    with world.pointed():
        config = load_config()
    mcp = PyriteMCPServer(config=config, tier="admin")
    try:
        every = mcp._dispatch_tool("list_edge_types", {})
        assert "error_code" not in every, every
        assert [(r["kb"], r["error_code"]) for r in every["unreadable_kbs"]] == [
            ("bad", "KB_CONFIG_INVALID")
        ]
        assert "error_code" not in mcp._dispatch_tool("list_edge_types", {"kb_name": "good"})
        one = mcp._dispatch_tool("list_edge_types", {"kb_name": "bad"})
        assert (one["error_code"], one["error"]) == ("KB_CONFIG_INVALID", FIXED_MESSAGE)
    finally:
        mcp.close()


# -- Every print site shows the one message --------------------------------------


def test_every_console_script_prints_the_one_message(one_broken):
    """Every command of pyrite, pyrite-read and pyrite-admin that names the
    broken KB (``-k bad`` or a KB argument): a ``KB_CONFIG_INVALID`` it prints
    carries the fixed message, never the file's path or the parser's words."""
    world, bad = one_broken
    walked, wrong = [], []
    for root, path, cmd in _cli_leaves():
        option, argument = _kb_params(cmd)
        if not option and not argument:
            continue
        result = _run(world, bad, root, path, _args(cmd, kb="bad"))
        walked.append(" ".join((root, *path)))
        out = result.output
        if _error_code(out) == "KB_CONFIG_INVALID":
            message = json.loads(out[out.index("{") :])["error"]
        elif "ERROR [KB_CONFIG_INVALID]: " in out:  # a command that prints text
            message = out.split("ERROR [KB_CONFIG_INVALID]: ", 1)[1].strip()
        else:
            continue
        if not message.startswith(FIXED_MESSAGE) or str(bad) in message:
            wrong.append((" ".join((root, *path)), message[:120]))
    assert {"pyrite-read get", "pyrite get"} <= set(walked), walked
    assert wrong == [], wrong
