"""A software KB's board config is evaluated or the call is refused (#1538).

A software KB keeps its quality gates and WIP limits in ``board.yaml``. The
property: **a gate or limit that ``board.yaml`` declares is evaluated or the
call is refused** -- no answer says "ready", "passed" or "no limit" for a
check that did not run. A file that cannot be turned into a board config is
one ``BoardConfigError`` (``VALIDATION_FAILED``, not retryable) whose text
names ``board.yaml`` and the kind of problem, never the file's values. The
KB file reader's own refusals keep their codes. ``board.yaml`` is the user's
file and is only read: the entry file is byte-identical after every refusal.

And the board config is read from the KB's own root: a call naming a KB that
does not resolve is refused, and a ``board.yaml`` anywhere else (the process's
working directory) never reaches an answer.

Every case runs through the real surfaces: ``PyriteMCPServer._dispatch_tool``
and the ``pyrite sw`` commands, which call the same handlers.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from pyrite.config import KBConfig
from tests._world import build_world

KB = "sw"
#: A value planted in every refused file: it must reach no answer.
MARKER = "zq-marker-5d1c"

VALID_GATED = f"""\
lanes:
  - name: Ready
    statuses: [accepted]
  - name: In Progress
    statuses: [in_progress]
    wip_limit: 4
  - name: Review
    statuses: [review]
    wip_limit: 2
  - name: Done
    statuses: [done]
wip_policy: enforce
gates:
  in_progress:
    name: {MARKER} ready
    policy: enforce
    criteria:
      - text: Effort estimated
        checker: has_field
        params: {{field: effort}}
  done:
    name: Definition of Done
    policy: enforce
    criteria:
      - text: Effort estimated
        checker: has_field
        params: {{field: effort}}
"""

#: Every shape the loader refuses, as the bytes of ``board.yaml``.
REFUSED_SHAPES: dict[str, bytes] = {
    "does-not-parse": (VALID_GATED + "lanes: [unclosed\n").encode(),
    "not-utf8": b"lanes: []\nname: \xff\xfe " + MARKER.encode() + b"\n",
    "a-list": f"- {MARKER}\n- b\n".encode(),
    "a-string": f"{MARKER}\n".encode(),
    "lanes-not-a-list": f"lanes: {{{MARKER}: 1}}\n".encode(),
    "gates-not-a-mapping": f"gates: [{MARKER}]\n".encode(),
    "lane-not-a-mapping": f"lanes:\n  - {MARKER}\n".encode(),
    "lane-without-statuses": f"lanes:\n  - name: {MARKER}\n".encode(),
    "lane-statuses-not-a-list": f"lanes:\n  - name: Ready\n    statuses: {MARKER}\n".encode(),
    "wip-limit-not-an-integer": (
        f"lanes:\n  - name: Ready\n    statuses: [in_progress]\n    wip_limit: {MARKER}\n"
    ).encode(),
    "wip-policy-unknown": f"lanes: []\nwip_policy: {MARKER}\n".encode(),
    "gate-not-a-mapping": f"gates:\n  in_progress: {MARKER}\n".encode(),
    "gate-policy-unknown": VALID_GATED.replace("policy: enforce", f"policy: {MARKER}").encode(),
    "criteria-not-a-list": f"gates:\n  in_progress:\n    criteria: {MARKER}\n".encode(),
    "criterion-type-unknown": (
        f"gates:\n  in_progress:\n    criteria:\n      - text: t\n        type: {MARKER}\n"
    ).encode(),
    "checker-unknown": VALID_GATED.replace("checker: has_field", f"checker: {MARKER}").encode(),
    "checker-criterion-without-checker": (
        b"gates:\n  in_progress:\n    policy: enforce\n    criteria:\n      - text: t\n"
    ),
    "checker-params-not-a-mapping": VALID_GATED.replace(
        "params: {field: effort}", f"params: [{MARKER}]"
    ).encode(),
}

# -- the world ----------------------------------------------------------------

_ITEMS = {
    # id: status
    "item-ready": "accepted",
    "item-move": "accepted",
    "item-review": "review",
}


def _entry_text(entry_id: str, status: str) -> str:
    return (
        f"---\nid: {entry_id}\ntitle: {entry_id}\ntype: backlog_item\nkind: bug\n"
        f"status: {status}\npriority: high\n---\nbody\n"
    )


@pytest.fixture
def world(tmp_path):
    w = build_world(
        tmp_path / "w",
        kbs=[KBConfig(name=KB, path=tmp_path / "w" / "kbs" / KB, kb_type="software")],
        rest=False,
    )
    for entry_id, status in _ITEMS.items():
        w.add_entry(KB, _entry_text(entry_id, status), filename=f"backlog/{entry_id}.md")
    try:
        yield w
    finally:
        w.close()


def _write_board(world, data: bytes | str) -> Path:
    path = world.kb_path(KB) / "board.yaml"
    path.write_bytes(data.encode() if isinstance(data, str) else data)
    return path


def _snapshot(world) -> dict[str, bytes]:
    root = world.kb_path(KB)
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and ".pyrite" not in p.parts
    }


# -- the tools that decide or report a gate, and the views ----------------------

#: name -> (MCP arguments, CLI argv or None). The MCP call is made by the
#: dispatcher, which sets the session's person arguments.
MCP_CALLS: dict[str, dict] = {
    "sw_claim": {"item_id": "item-ready", "kb_name": KB, "assignee": "probe"},
    "sw_transition": {"item_id": "item-move", "kb_name": KB, "to_status": "in_progress"},
    "sw_review": {
        "item_id": "item-review",
        "kb_name": KB,
        "outcome": "approved",
        "reviewer": "r",
    },
    "sw_pull_next": {"kb_name": KB},
    "sw_check_ready": {"item_id": "item-ready", "kb_name": KB},
    "sw_refine": {"kb_name": KB},
    "sw_board": {"kb_name": KB},
    "sw_review_queue": {"kb_name": KB},
}

CLI_CALLS: dict[str, list[str]] = {
    "sw check-ready": ["sw", "check-ready", "item-ready", "-k", KB],
    "sw refine": ["sw", "refine", "-k", KB],
    "sw review": ["sw", "review", "item-review", "-o", "approved", "--reviewer", "r", "-k", KB],
    "sw pull-next": ["sw", "pull-next", "-k", KB],
    "sw board": ["sw", "board", "-k", KB],
}


def _mcp(world, tool: str, args: dict) -> dict:
    server = world.mcp(tier="admin")
    return server._dispatch_tool(tool, dict(args))


def _cli(world, argv: list[str]):
    return world.cli(*argv)


def _cli_json(result) -> dict:
    """The JSON object a command printed (its last one)."""
    text = result.output.strip()
    start = text.find("{")
    assert start != -1, result.output
    return json.loads(text[start:])


def _assert_board_refusal(answer: dict) -> None:
    assert answer.get("error_code") == "VALIDATION_FAILED", answer
    assert answer.get("retryable") is False, answer
    assert "board.yaml" in answer["error"], answer
    assert MARKER not in json.dumps(answer), answer


# -- refused shapes ---------------------------------------------------------------


@pytest.mark.parametrize("shape", sorted(REFUSED_SHAPES))
@pytest.mark.parametrize("tool", sorted(MCP_CALLS))
def test_a_board_that_cannot_be_loaded_refuses_over_mcp(world, tool, shape):
    _write_board(world, REFUSED_SHAPES[shape])
    before = _snapshot(world)

    answer = _mcp(world, tool, MCP_CALLS[tool])

    _assert_board_refusal(answer)
    assert _snapshot(world) == before


@pytest.mark.parametrize("shape", sorted(REFUSED_SHAPES))
@pytest.mark.parametrize("command", sorted(CLI_CALLS))
def test_a_board_that_cannot_be_loaded_refuses_on_the_cli(world, command, shape):
    _write_board(world, REFUSED_SHAPES[shape])
    before = _snapshot(world)

    result = _cli(world, CLI_CALLS[command])

    assert result.exit_code == 1, result.output
    assert MARKER not in result.output, result.output
    if command == "sw review":  # a rich-only command: the message, in the CLI's error line
        assert "board.yaml" in result.output, result.output
    else:
        _assert_board_refusal(_cli_json(result))
    assert _snapshot(world) == before


def test_orient_says_what_is_wrong_with_the_board(world):
    _write_board(world, REFUSED_SHAPES["does-not-parse"])
    answer = _mcp(world, "kb_orient", {"kb_name": KB})
    block = json.dumps(answer)
    assert "board_error" in block, answer
    assert MARKER not in block


# -- the reader's own refusals keep their codes ----------------------------------


@pytest.mark.parametrize("tool", ["sw_claim", "sw_check_ready", "sw_pull_next"])
def test_a_board_over_the_yaml_limits_keeps_the_readers_code(world, tool):
    bomb = "a: &a [x, x, x, x, x, x, x, x, x, x]\n" + "".join(
        f"{chr(98 + i)}: &{chr(98 + i)} [*{chr(97 + i)}, *{chr(97 + i)}, *{chr(97 + i)}, "
        f"*{chr(97 + i)}, *{chr(97 + i)}, *{chr(97 + i)}, *{chr(97 + i)}, *{chr(97 + i)}]\n"
        for i in range(9)
    )
    _write_board(world, bomb)
    before = _snapshot(world)
    answer = _mcp(world, tool, MCP_CALLS[tool])
    assert answer.get("error_code") == "INVALID_FRONTMATTER", answer
    assert _snapshot(world) == before


@pytest.mark.parametrize("tool", ["sw_claim", "sw_check_ready", "sw_pull_next"])
def test_a_board_that_links_out_of_the_kb_keeps_the_readers_code(world, tool, tmp_path):
    outside = tmp_path / "outside.yaml"
    outside.write_text(VALID_GATED)
    (world.kb_path(KB) / "board.yaml").symlink_to(outside)
    answer = _mcp(world, tool, MCP_CALLS[tool])
    assert answer.get("error_code") == "OUTSIDE_KB_ROOT", answer
    assert MARKER not in json.dumps(answer)


# -- controls: what does not change ------------------------------------------------


@pytest.mark.control(reason="no board.yaml: the defaults, as before (#1538 changes no answer)")
def test_no_board_file_gives_the_default_gate_at_warn(world):
    answer = _mcp(world, "sw_check_ready", MCP_CALLS["sw_check_ready"])
    assert answer["gate"]["gate_name"] == "Definition of Ready"
    assert answer["gate"]["policy"] == "warn"
    claim = _mcp(world, "sw_claim", MCP_CALLS["sw_claim"])
    assert claim["claimed"] is True, claim


@pytest.mark.control(reason="an empty board.yaml declares nothing: no gates, as before")
def test_an_empty_board_file_declares_no_gate(world):
    _write_board(world, b"")
    answer = _mcp(world, "sw_check_ready", MCP_CALLS["sw_check_ready"])
    assert answer["ready"] is True and answer["gate"] is None, answer
    assert _mcp(world, "sw_claim", MCP_CALLS["sw_claim"])["claimed"] is True


@pytest.mark.control(reason="a valid board with no gates key: no gates, as before")
def test_a_valid_board_without_gates_declares_no_gate(world):
    _write_board(world, "lanes:\n  - name: Ready\n    statuses: [accepted]\n")
    assert _mcp(world, "sw_check_ready", MCP_CALLS["sw_check_ready"])["gate"] is None
    board = _mcp(world, "sw_board", MCP_CALLS["sw_board"])
    assert [lane["name"] for lane in board["lanes"]] == ["Ready"], board


@pytest.mark.control(reason="a valid enforce gate that runs and fails keeps its answer")
def test_a_valid_enforced_gate_runs_and_blocks(world):
    path = _write_board(world, VALID_GATED)
    before = path.read_bytes()
    answer = _mcp(world, "sw_claim", MCP_CALLS["sw_claim"])
    assert answer["claimed"] is False and answer["error"] == "Gate check failed", answer
    assert answer["gate"]["criteria"][0]["passed"] is False
    assert path.read_bytes() == before
    ready = _mcp(world, "sw_check_ready", MCP_CALLS["sw_check_ready"])
    assert ready["ready"] is False
    pull = _mcp(world, "sw_pull_next", MCP_CALLS["sw_pull_next"])
    assert pull["wip_status"] == {"current": 0, "limit": 4, "policy": "enforce"}, pull


@pytest.mark.control(reason="a valid warn gate that fails reports and lets the claim through")
def test_a_valid_warn_gate_runs_and_passes_the_claim(world):
    _write_board(world, VALID_GATED.replace("policy: enforce", "policy: warn"))
    answer = _mcp(world, "sw_claim", MCP_CALLS["sw_claim"])
    assert answer["claimed"] is True, answer
    assert answer["gate"]["passed"] is False and answer["gate"]["policy"] == "warn"


@pytest.mark.control(reason="a checker missing its parameter keeps CheckerConfigError's answer")
def test_a_checker_missing_its_parameter_keeps_its_answer(world):
    _write_board(world, VALID_GATED.replace("        params: {field: effort}\n", ""))
    answer = _mcp(world, "sw_claim", MCP_CALLS["sw_claim"])
    assert answer["error_code"] == "VALIDATION_FAILED", answer
    assert "has_field requires" in answer["error"], answer


# -- where the board config is read from --------------------------------------------


@pytest.fixture
def board_in_the_working_directory(tmp_path, monkeypatch):
    """A board.yaml with the marker in the process's working directory: it is
    nobody's KB, so it must reach no answer."""
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (cwd / "board.yaml").write_text(
        f"lanes:\n  - name: {MARKER}\n    statuses: [accepted, review, in_progress]\n"
        f"    wip_limit: 99\n"
    )
    monkeypatch.chdir(cwd)
    return cwd


#: Handlers that load a board config for a named KB, called directly with a
#: KB the plugin's config does not know (the index still has its rows).
HANDLER_CALLS: dict[str, dict] = {
    "_mcp_claim": {**MCP_CALLS["sw_claim"], "kb_name": "gone"},
    "_mcp_transition": {**MCP_CALLS["sw_transition"], "kb_name": "gone"},
    "_mcp_review": {**MCP_CALLS["sw_review"], "kb_name": "gone", "reviewer": "r"},
    "_mcp_pull_next": {"kb_name": "gone"},
    "_mcp_check_ready": {**MCP_CALLS["sw_check_ready"], "kb_name": "gone"},
    "_mcp_refine": {"kb_name": "gone"},
    "_mcp_board": {"kb_name": "gone"},
    "_mcp_review_queue": {"kb_name": "gone"},
}


@pytest.fixture
def gone_world(tmp_path):
    """An index whose rows belong to a KB named 'gone' that no config holds."""
    w = build_world(
        tmp_path / "w",
        kbs=[KBConfig(name="gone", path=tmp_path / "w" / "kbs" / "gone", kb_type="software")],
        rest=False,
    )
    for entry_id, status in _ITEMS.items():
        w.add_entry("gone", _entry_text(entry_id, status), filename=f"backlog/{entry_id}.md")
    try:
        yield w
    finally:
        w.close()


def _plugin_without_the_kb(world):
    from pyrite.config import PyriteConfig
    from pyrite.plugins.context import PluginContext
    from pyrite_software_kb.plugin import SoftwareKBPlugin

    config = PyriteConfig(knowledge_bases=[], settings=world.config.settings)
    # The registry row `build_world` indexed would let the resolver find the
    # KB through the database: drop it, so the KB is truly unknown.
    conn = world.db._raw_conn
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("DELETE FROM kb WHERE name = 'gone'")
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    plugin = SoftwareKBPlugin()
    plugin.set_context(PluginContext(config=config, db=world.db))
    return plugin


@pytest.mark.parametrize("handler", sorted(HANDLER_CALLS))
def test_a_kb_that_does_not_resolve_is_refused(gone_world, handler, board_in_the_working_directory):
    from pyrite.exceptions import KBNotFoundError

    plugin = _plugin_without_the_kb(gone_world)
    try:
        answer = getattr(plugin, handler)(dict(HANDLER_CALLS[handler]))
    except KBNotFoundError:
        return
    pytest.fail(f"{handler} answered for a KB that does not resolve: {answer}")


#: The cross-KB views answer a call naming no KB from the built-in defaults
#: (Backlog, Ready, In Progress at 5, Review at 3, Done; WIP policy warn) and
#: read no file.
DEFAULT_LANES = ["Backlog", "Ready", "In Progress", "Review", "Done"]


def test_the_board_naming_no_kb_shows_the_default_lanes(world, board_in_the_working_directory):
    answer = _mcp(world, "sw_board", {})
    assert [lane["name"] for lane in answer["lanes"]] == DEFAULT_LANES, answer
    assert MARKER not in json.dumps(answer)


def test_the_review_queue_naming_no_kb_shows_the_default_limit(
    world, board_in_the_working_directory
):
    answer = _mcp(world, "sw_review_queue", {})
    assert answer["wip_limit"] == 3, answer


def test_pull_next_naming_no_kb_applies_the_default_limit(world, board_in_the_working_directory):
    answer = _mcp(world, "sw_pull_next", {})
    assert answer["wip_status"]["limit"] == 5, answer
    assert answer["wip_status"]["policy"] == "warn", answer


def test_the_cli_board_naming_no_kb_shows_the_default_lanes(world, board_in_the_working_directory):
    result = _cli(world, ["sw", "board"])
    assert [lane["name"] for lane in _cli_json(result)["lanes"]] == DEFAULT_LANES, result.output


def test_the_cli_pull_next_naming_no_kb_applies_the_default_limit(
    world, board_in_the_working_directory
):
    result = _cli(world, ["sw", "pull-next"])
    assert _cli_json(result)["wip_status"]["limit"] == 5, result.output


@pytest.mark.parametrize("command", sorted(CLI_CALLS))
def test_a_cli_command_reads_the_named_kbs_board(world, command, board_in_the_working_directory):
    _write_board(world, VALID_GATED.replace(MARKER, "kb-own"))
    result = _cli(world, CLI_CALLS[command])
    assert MARKER not in result.output, result.output


def test_the_cli_pull_next_applies_the_named_kbs_wip_limit(world, board_in_the_working_directory):
    _write_board(world, VALID_GATED)
    result = _cli(world, CLI_CALLS["sw pull-next"])
    assert _cli_json(result)["wip_status"]["limit"] == 4, result.output


def test_the_loader_takes_a_resolved_kb_only(tmp_path):
    from pyrite_software_kb.board import load_board_config

    (tmp_path / "board.yaml").write_text("lanes: []\n")
    with pytest.raises(TypeError):
        load_board_config(tmp_path)
    with pytest.raises(TypeError):
        load_board_config(os.fspath(tmp_path))


@pytest.mark.control(reason="a valid enforced gate on review and transition runs and blocks")
def test_a_valid_enforced_gate_blocks_review_and_transition(world):
    _write_board(world, VALID_GATED)
    before = _snapshot(world)
    review = _mcp(world, "sw_review", MCP_CALLS["sw_review"])
    assert review["reviewed"] is False and review["error"] == "DoD gate check failed", review
    move = _mcp(world, "sw_transition", MCP_CALLS["sw_transition"])
    assert move["transitioned"] is False and move["error"] == "Gate check failed", move
    assert _snapshot(world) == before


# -- every tool and command that loads a board config is covered here ------------

_SW = Path(__file__).resolve().parent.parent / "extensions/software-kb/src/pyrite_software_kb"


def _plugin_methods_that_load_a_board() -> set[str]:
    """Methods of SoftwareKBPlugin that reach `_board_config`, directly or
    through another method (the plugin's source, walked)."""
    import ast

    tree = ast.parse((_SW / "plugin.py").read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "SoftwareKBPlugin")
    calls: dict[str, set[str]] = {}
    for fn in cls.body:
        if isinstance(fn, ast.FunctionDef):
            calls[fn.name] = {
                n.func.attr
                for n in ast.walk(fn)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            }
    reach = {"_board_config"}
    while True:
        more = {name for name, called in calls.items() if called & reach} - reach
        if not more:
            return reach - {"_board_config"}
        reach |= more


def _cli_commands_that_load_a_board() -> set[str]:
    """`pyrite sw` commands whose function calls a plugin method that loads one."""
    import ast

    from pyrite_software_kb.cli import sw_app

    loaders = _plugin_methods_that_load_a_board() | {"_board_config"}
    tree = ast.parse((_SW / "cli.py").read_text(encoding="utf-8"))
    functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    out = set()
    for command in sw_app.registered_commands:
        fn = functions.get(command.callback.__name__)
        called = {n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)} if fn else set()
        if called & loaders:
            out.add(f"sw {command.name or command.callback.__name__.replace('_', '-')}")
    return out


def _registered_tools() -> dict[str, str]:
    from pyrite_software_kb.plugin import SoftwareKBPlugin

    tools = SoftwareKBPlugin().get_mcp_tools("admin")
    return {name: spec["handler"].__name__ for name, spec in tools.items()}


@pytest.mark.parametrize("tool", sorted(_registered_tools()))
def test_every_tool_that_loads_a_board_is_tested_here(tool):
    if _registered_tools()[tool] in _plugin_methods_that_load_a_board():
        assert tool in MCP_CALLS, f"{tool} loads a board config: add it to MCP_CALLS"


def _sw_commands() -> list[str]:
    from pyrite_software_kb.cli import sw_app

    return sorted(
        f"sw {c.name or c.callback.__name__.replace('_', '-')}" for c in sw_app.registered_commands
    )


@pytest.mark.parametrize("command", _sw_commands())
def test_every_command_that_loads_a_board_is_tested_here(command):
    if command in _cli_commands_that_load_a_board():
        assert command in CLI_CALLS, f"`pyrite {command}` loads a board config: add it to CLI_CALLS"


def test_the_walk_finds_the_known_loaders():
    """Not vacuous: the walk sees the handlers and commands this file tests."""
    methods = _plugin_methods_that_load_a_board()
    assert {"_mcp_claim", "get_orient_supplement", "_mcp_board"} <= methods, methods
    assert set(CLI_CALLS) <= _cli_commands_that_load_a_board()


def test_refine_naming_no_kb_is_refused_on_the_cli(world, board_in_the_working_directory):
    result = _cli(world, ["sw", "refine"])
    assert result.exit_code == 1, result.output
    answer = _cli_json(result)
    assert answer["error_code"] == "VALIDATION_FAILED", answer
    assert MARKER not in result.output
