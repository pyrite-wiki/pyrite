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

_GATE = "gates:\n  in_progress:\n    policy: enforce\n    criteria:\n"
_BOMB = "a: &a [x, x, x, x, x, x, x, x, x, x]\n" + "".join(
    f"{chr(98 + i)}: &{chr(98 + i)} [" + ", ".join([f"*{chr(97 + i)}"] * 8) + "]\n"
    for i in range(9)
)

#: Every board.yaml the loader refuses, as its bytes. Each is refused with
#: VALIDATION_FAILED unless EXPECTED_CODE says otherwise.
REFUSED_SHAPES: dict[str, bytes] = {
    "does-not-parse": (VALID_GATED + "lanes: [unclosed\n").encode(),
    "not-utf8": b"lanes: []\nname: \xff\xfe " + MARKER.encode() + b"\n",
    "nested-too-deeply": ("lanes: " + "[" * 400 + MARKER + "]" * 400 + "\n").encode(),
    "over-the-yaml-limits": (_BOMB + f"# {MARKER}\n").encode(),
    "a-list": f"- {MARKER}\n- b\n".encode(),
    "a-string": f"{MARKER}\n".encode(),
    "unknown-top-level-key": f"lanes: []\ngate: {{in_progress: {MARKER}}}\n".encode(),
    "lanes-not-a-list": f"lanes: {{{MARKER}: 1}}\n".encode(),
    "lanes-a-number": f"lanes: 5  # {MARKER}\n".encode(),
    "gates-not-a-mapping": f"gates: [{MARKER}]\n".encode(),
    "lane-not-a-mapping": f"lanes:\n  - {MARKER}\n".encode(),
    "lane-without-a-name": b"lanes:\n  - statuses: [accepted]\n",
    "lane-without-statuses": f"lanes:\n  - name: {MARKER}\n".encode(),
    "lane-statuses-not-a-list": f"lanes:\n  - name: Ready\n    statuses: {MARKER}\n".encode(),
    "lane-unknown-key": (
        f"lanes:\n  - name: Ready\n    statuses: [in_progress]\n    wip-limit: 1  # {MARKER}\n"
    ).encode(),
    "lanes-with-the-same-name": (
        f"lanes:\n  - name: {MARKER}\n    statuses: [accepted]\n"
        f"  - name: {MARKER}\n    statuses: [in_progress]\n    wip_limit: 1\n"
    ).encode(),
    "a-status-in-two-lanes": (
        f"lanes:\n  - name: {MARKER}\n    statuses: [in_progress]\n    wip_limit: 9\n"
        "  - name: Two\n    statuses: [in_progress]\n    wip_limit: 1\n"
    ).encode(),
    "wip-limit-not-an-integer": (
        f"lanes:\n  - name: Ready\n    statuses: [in_progress]\n    wip_limit: {MARKER}\n"
    ).encode(),
    "wip-limit-negative": (
        f"lanes:\n  - name: {MARKER}\n    statuses: [in_progress]\n    wip_limit: -1\n"
    ).encode(),
    "wip-policy-unknown": f"lanes: []\nwip_policy: {MARKER}\n".encode(),
    "gate-keyed-by-a-number": f"gates:\n  5:\n    name: {MARKER}\n".encode(),
    "gate-keyed-by-an-unknown-status": f"gates:\n  {MARKER}:\n    criteria: []\n".encode(),
    "gate-not-a-mapping": f"gates:\n  in_progress: {MARKER}\n".encode(),
    "gate-unknown-key": f"gates:\n  in_progress:\n    criterias: [{MARKER}]\n".encode(),
    "gate-name-not-text": f"gates:\n  in_progress:\n    name: [{MARKER}]\n".encode(),
    "gate-policy-unknown": VALID_GATED.replace(
        "    policy: enforce", f"    policy: {MARKER}"
    ).encode(),
    "criteria-not-a-list": f"gates:\n  in_progress:\n    criteria: {MARKER}\n".encode(),
    "criterion-unknown-key": (
        _GATE + f"      - text: t\n        type: judgment\n        hints: {MARKER}\n"
    ).encode(),
    "criterion-text-not-text": (
        _GATE + f"      - text: [{MARKER}]\n        type: judgment\n"
    ).encode(),
    "criterion-hint-not-text": (
        _GATE + f"      - text: t\n        type: judgment\n        hint: [{MARKER}]\n"
    ).encode(),
    "criterion-type-unknown": (_GATE + f"      - text: t\n        type: {MARKER}\n").encode(),
    "checker-unknown": VALID_GATED.replace("checker: has_field", f"checker: {MARKER}").encode(),
    "checker-criterion-without-checker": (_GATE + "      - text: t\n").encode(),
    "checker-params-not-a-mapping": VALID_GATED.replace(
        "params: {field: effort}", f"params: [{MARKER}]"
    ).encode(),
    "checker-params-missing": VALID_GATED.replace("        params: {field: effort}\n", "").encode(),
    "checker-params-it-cannot-use": VALID_GATED.replace(
        "params: {field: effort}", f"params: {{nope: {MARKER}}}"
    ).encode(),
    "checker-pattern-that-does-not-compile": (
        _GATE + f"      - text: t\n        checker: body_has_pattern\n"
        f"        params: {{pattern: '({MARKER}'}}\n"
    ).encode(),
}

#: Shapes whose read the KB file reader refused: its code is kept.
EXPECTED_CODE = {
    "over-the-yaml-limits": "INVALID_FRONTMATTER",
    "a-link-out-of-the-kb": "OUTSIDE_KB_ROOT",
    "the-os-refuses-the-read": "KB_UNREADABLE",
}


def _link_out(world, tmp_path, monkeypatch):
    outside = tmp_path / "outside.yaml"
    outside.write_text(VALID_GATED)
    (world.kb_path(KB) / "board.yaml").symlink_to(outside)


def _a_directory(world, tmp_path, monkeypatch):
    (world.kb_path(KB) / "board.yaml").mkdir()
    (world.kb_path(KB) / "board.yaml" / MARKER).write_text(MARKER)


def _os_refuses(world, tmp_path, monkeypatch):
    """The container runs as root, so a mode cannot refuse the read: the KB
    file reader's own read is made to fail as the OS would."""
    from pyrite.utils.kb_files import KBFiles

    _write_board(world, VALID_GATED)
    real = KBFiles.read_bytes

    def read_bytes(self, path, *a, **k):
        if Path(path).name == "board.yaml":
            raise PermissionError(13, "Permission denied", str(path))
        return real(self, path, *a, **k)

    monkeypatch.setattr(KBFiles, "read_bytes", read_bytes)


#: Boards that are not plain bytes to write.
SET_UP: dict = {
    "a-link-out-of-the-kb": _link_out,
    "a-directory-named-board-yaml": _a_directory,
    "the-os-refuses-the-read": _os_refuses,
}

BROKEN = sorted([*REFUSED_SHAPES, *SET_UP])


def _break_the_board(world, shape, tmp_path, monkeypatch) -> None:
    if shape in REFUSED_SHAPES:
        _write_board(world, REFUSED_SHAPES[shape])
    else:
        SET_UP[shape](world, tmp_path, monkeypatch)


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


def _assert_board_refusal(answer: dict, shape: str) -> None:
    assert answer.get("error_code") == EXPECTED_CODE.get(shape, "VALIDATION_FAILED"), answer
    assert answer.get("retryable") is False, answer
    assert "board.yaml" in answer["error"], answer
    assert MARKER not in json.dumps(answer), answer


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


def test_the_walk_finds_the_known_loaders():
    """Not vacuous: the walk sees the handlers and commands this file tests."""
    methods = _plugin_methods_that_load_a_board()
    assert {"_mcp_claim", "get_orient_supplement", "_mcp_board"} <= methods, methods
    assert set(CLI_CALLS) <= _cli_commands_that_load_a_board()


#: The receivers, from the code: every registered MCP tool and every `pyrite
#: sw` command that reaches `_board_config`. A new one with no arguments in
#: MCP_CALLS or CLI_CALLS fails here.
DERIVED_TOOLS = {
    t for t, h in _registered_tools().items() if h in _plugin_methods_that_load_a_board()
}
DERIVED_COMMANDS = _cli_commands_that_load_a_board()
#: United with the argument tables so the test ids are the same on any tree
#: (verify-red compares them); each cell asserts the receiver is derived.
BOARD_TOOLS = sorted(DERIVED_TOOLS | set(MCP_CALLS))
BOARD_COMMANDS = sorted(DERIVED_COMMANDS | set(CLI_CALLS))


# -- a board that cannot be loaded: one refusal, everywhere ----------------------


@pytest.mark.parametrize("shape", BROKEN)
@pytest.mark.parametrize("tool", BOARD_TOOLS)
def test_a_board_that_cannot_be_loaded_refuses_over_mcp(world, tool, shape, tmp_path, monkeypatch):
    assert tool in MCP_CALLS, f"{tool} loads a board config: give it arguments in MCP_CALLS"
    assert tool in DERIVED_TOOLS, f"{tool} no longer reaches _board_config"
    _break_the_board(world, shape, tmp_path, monkeypatch)
    before = _snapshot(world)

    answer = _mcp(world, tool, MCP_CALLS[tool])

    _assert_board_refusal(answer, shape)
    assert _snapshot(world) == before


@pytest.mark.parametrize("shape", BROKEN)
@pytest.mark.parametrize("command", BOARD_COMMANDS)
def test_a_board_that_cannot_be_loaded_refuses_on_the_cli(
    world, command, shape, tmp_path, monkeypatch
):
    assert command in CLI_CALLS, f"`pyrite {command}` loads a board config: add it to CLI_CALLS"
    assert command in DERIVED_COMMANDS, f"`pyrite {command}` no longer reaches _board_config"
    _break_the_board(world, shape, tmp_path, monkeypatch)
    before = _snapshot(world)

    result = _cli(world, CLI_CALLS[command])

    assert result.exit_code == 1, result.output
    assert MARKER not in result.output, result.output
    assert "Traceback" not in result.output
    if command == "sw review":  # a rich-only command: the message, in the CLI's error line
        assert "board.yaml" in result.output, result.output
    else:
        _assert_board_refusal(_cli_json(result), shape)
    assert _snapshot(world) == before


@pytest.mark.parametrize("shape", BROKEN)
def test_orient_answers_and_says_what_is_wrong_with_the_board(world, shape, tmp_path, monkeypatch):
    _break_the_board(world, shape, tmp_path, monkeypatch)
    answer = _mcp(world, "kb_orient", {"kb_name": KB})
    software = answer.get("software")
    assert software is not None, answer
    assert "board.yaml" in software["board_error"], software
    assert software["recommended_next"] is None and software["board_summary"] == []
    assert MARKER not in json.dumps(answer)


@pytest.mark.parametrize("fmt", ["json", "rich"])
@pytest.mark.parametrize("shape", ["does-not-parse", "a-link-out-of-the-kb", "nested-too-deeply"])
def test_the_cli_orient_shows_the_board_error(world, shape, fmt, tmp_path, monkeypatch):
    from pyrite.plugins.registry import get_registry

    # The CLI is a fresh process: no plugin holds a server's context. In a
    # shared test worker an earlier server's (closed) one may still be set,
    # so it is cleared for this call and put back after.
    plugins = [p for p in get_registry()._plugins.values() if hasattr(p, "ctx")]
    saved = [p.ctx for p in plugins]
    _break_the_board(world, shape, tmp_path, monkeypatch)
    try:
        for plugin in plugins:
            plugin.ctx = None
        result = _cli(world, ["orient", "-k", KB, "--format", fmt])
    finally:
        for plugin, ctx in zip(plugins, saved, strict=True):
            plugin.ctx = ctx
    assert result.exit_code == 0, result.output
    assert "board.yaml" in result.output, result.output
    assert MARKER not in result.output


def test_a_checker_that_cannot_run_on_an_entry_refuses(world, monkeypatch):
    """A checker that fails on one entry (a pattern that takes too long on its
    body) is the board's refusal, without its text."""
    from pyrite.services import rubric_checkers

    real = rubric_checkers.NAMED_CHECKERS["has_field"]

    def has_field(entry, schema, params):
        if entry.get("id"):  # not the load-time dry run
            raise rubric_checkers.CheckerConfigError(f"took too long on {MARKER}")
        return real(entry, schema, params)

    monkeypatch.setitem(rubric_checkers.NAMED_CHECKERS, "has_field", has_field)
    _write_board(world, VALID_GATED)
    before = _snapshot(world)
    for tool in ("sw_claim", "sw_check_ready"):
        answer = _mcp(world, tool, MCP_CALLS[tool])
        _assert_board_refusal(answer, "runtime")
        assert "criterion 1" in answer["error"], answer
    assert _snapshot(world) == before


@pytest.mark.parametrize(
    "tool",
    [
        t
        if t == "sw_refine"
        else pytest.param(
            t, marks=pytest.mark.control(reason="answered without INTERNAL before #1538 too")
        )
        for t in BOARD_TOOLS
    ],
)
def test_a_call_with_no_kb_is_never_an_internal_error(world, tool):
    """No `kb_name` key at all: a refusal, or the built-in board's answer for
    the cross-KB views; never INTERNAL."""
    args = {k: v for k, v in MCP_CALLS[tool].items() if k != "kb_name"}
    answer = _mcp(world, tool, args)
    assert answer.get("error_code") != "INTERNAL", answer


def test_refine_naming_no_kb_is_refused_over_mcp(world):
    for args in ({}, {"kb_name": ""}):
        answer = _mcp(world, "sw_refine", args)
        assert answer["error_code"] == "VALIDATION_FAILED", answer


@pytest.mark.control(reason="the built-in board against rules this PR adds: no base to be red on")
def test_the_built_in_board_passes_the_loaders_own_checks(world):
    """Written out as a board.yaml, the built-in board loads: it obeys every
    rule the loader enforces."""
    import yaml
    from pyrite_software_kb.board import DEFAULT_BOARD_CONFIG

    _write_board(world, yaml.safe_dump(DEFAULT_BOARD_CONFIG))
    answer = _mcp(world, "sw_board", MCP_CALLS["sw_board"])
    assert [lane["name"] for lane in answer["lanes"]] == DEFAULT_LANES, answer


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


def test_refine_naming_no_kb_is_refused_on_the_cli(world, board_in_the_working_directory):
    result = _cli(world, ["sw", "refine"])
    assert result.exit_code == 1, result.output
    answer = _cli_json(result)
    assert answer["error_code"] == "VALIDATION_FAILED", answer
    assert MARKER not in result.output


@pytest.mark.parametrize(
    "gate",
    [
        {"policy": "enforced", "criteria": []},
        {"criteria": [{"text": "t", "checker": "has_feild"}]},
        {"criteria": [{"text": "t"}]},
        {"criteria": [{"text": "t", "type": "judgement"}]},
    ],
    ids=["policy-unknown", "checker-unknown", "no-checker", "type-unknown"],
)
def test_the_gate_never_records_what_it_could_not_run_as_passed(world, gate):
    """`_evaluate_gate` refuses on its own, even handed a config the loader
    did not validate."""
    from pyrite_software_kb.plugin import SoftwareKBPlugin

    from pyrite.exceptions import ValidationError

    row = {"id": "item-ready", "kb_name": KB, "title": "t", "status": "accepted"}
    with pytest.raises(ValidationError, match="board.yaml"):
        SoftwareKBPlugin()._evaluate_gate(
            world.db, {"gates": {"in_progress": gate}}, "in_progress", row, {}, readable_kbs=None
        )


def test_a_checker_criterion_without_a_checker_says_so(world):
    _write_board(world, REFUSED_SHAPES["checker-criterion-without-checker"])
    answer = _mcp(world, "sw_check_ready", MCP_CALLS["sw_check_ready"])
    assert "names no checker" in answer["error"], answer
