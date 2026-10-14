"""`pyrite mcp-setup` does what was asked, loses nothing, and reports it (#582).

Pyrite is a guest in the files this command touches. The rule, decided from
the file as it is (no record of what Pyrite wrote, no inference from what an
entry looks like):

- no ``pyrite`` entry: write it;
- already equal in what was asked (the command path; the tier when ``--tier``
  is given): write nothing;
- differs in what was asked: change exactly that, report old and new;
- carries anything else (env, extra args, a tier nobody asked to change,
  unknown keys): keep it;
- cannot be done safely: stop, file untouched.

Fixtures are written out by hand in the shapes users have on disk: what the
clients write themselves (2 spaces, LF, no trailing newline, non-ASCII raw;
measured in the #582 spike), what README.md teaches, and hand-formatted
variants. None is ``json.dumps`` of what the command emits, and round-trip
assertions compare bytes.

Nothing here touches a real client file: each test runs in a temp ``HOME``
with ``PATH`` narrowed to a scratch ``bin/``, where a fake ``claude`` stands in
for Claude Code. The fake copies what Claude Code 2.1.287 was observed to do:
``mcp add -s user`` rewrites ``$HOME/.claude.json`` in its own style; adding a
name that exists, or removing one that does not, exits 1. It can also be told
to fail, hang, stop being runnable, exit 0 having written nothing, or write
another entry than the one asked for. ``test_real_claude_code_reads_back_the_entry``
checks the same contract against the real binary when one is installed.

The report is true of the file (#690): the last section reads the exits of
the two setup functions out of the source, requires a case for each, and
judges each report against the file as the test reads it, running every
``by_hand`` command against the fake ``claude``.
"""

from __future__ import annotations

import ast
import builtins
import json
import os
import random
import re
import shlex
import shutil
import stat
import subprocess
import sys
import sysconfig
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pyrite.cli import app

REPO = Path(__file__).resolve().parent.parent
runner = CliRunner()

#: The real Claude Code CLI, looked up before any test narrows PATH.
REAL_CLAUDE = shutil.which("claude")
REAL_PLATFORM = sys.platform

#: The `pyrite` script of the install under test: what every entry must run.
NEW = str((Path(sysconfig.get_path("scripts")) / "pyrite").absolute())
OLD = "/old/venv/bin/pyrite"
SECRET = "sk-secret-value-never-printed"

FAKE_CLAUDE = textwrap.dedent(
    """\
    #!@PYTHON@
    import json, os, sys, time
    from pathlib import Path

    home = Path(os.environ["HOME"])
    cfg = Path(os.environ.get("CLAUDE_CONFIG_DIR") or home) / ".claude.json"
    args = sys.argv[1:]
    log = home / "claude-calls.jsonl"
    earlier = [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []
    with open(log, "a") as f:
        f.write(json.dumps(args) + "\\n")
    verb = args[1] if len(args) > 1 else ""
    nth = sum(1 for call in earlier if call[:2] == args[:2]) + 1

    def told(var):
        # "add" means every add; "add#1" means the first add only.
        return any(p in (verb, f"{verb}#{nth}") for p in os.environ.get(var, "").split(",") if p)

    if told("FAKE_CLAUDE_HANG"):
        time.sleep(120)
    if told("FAKE_CLAUDE_GARBLE"):
        # Bytes no codec can read as text, on both streams.
        sys.stdout.buffer.write(b"\\xff\\xfe\\x81 garbled\\n")
        sys.stderr.buffer.write(b"\\xff\\xfe\\x81 garbled\\n")
        sys.stdout.flush()
        sys.stderr.flush()
    if told("FAKE_CLAUDE_FAIL"):
        sys.exit(f"fake claude: E_REFUSED {verb}")
    if told("FAKE_CLAUDE_CORRUPT"):
        cfg.write_text("")
        sys.exit(f"fake claude: E_CORRUPT {verb}")
    if told("FAKE_CLAUDE_NOOP"):
        # Exit 0 having written nothing: what a caller sees when another
        # Claude Code process writes its own copy of the file over this one.
        print(f"File modified: {cfg}")
        sys.exit(0)
    # utf-8-sig: Claude Code 2.1.296 reads a file with a BOM and drops the BOM.
    data = json.loads(cfg.read_text(encoding="utf-8-sig")) if cfg.exists() else {}
    servers = data.setdefault("mcpServers", {})
    if args[:2] == ["mcp", "add"]:
        sep = args.index("--")
        opts, (command, *rest) = args[2:sep], args[sep + 1 :]
        scope, variables, names, i = "local", {}, [], 0
        while i < len(opts):
            if opts[i] in ("-s", "--scope"):
                scope = opts[i + 1]
            elif opts[i] in ("-e", "--env"):
                key, _, value = opts[i + 1].partition("=")
                variables[key] = value
            else:
                names.append(opts[i])
                i -= 1
            i += 2
        name = names[0]
        if scope != "user":
            sys.exit(f"fake claude: only user scope is emulated, got {scope}")
        if name in servers:
            # Deliberately not Claude Code's wording: mcp-setup must not
            # depend on message text.
            sys.exit(f"fake claude: E_DUPLICATE {name}")
        servers[name] = {"type": "stdio", "command": command, "args": rest, "env": variables}
        if told("FAKE_CLAUDE_OTHER"):
            # Not the entry asked for: another writer's, or a client that
            # stores it differently.
            servers[name] = {"type": "stdio", "command": "/third/bin/pyrite"}
            servers[name].update(args=["mcp"], env={})
    elif args[:2] == ["mcp", "remove"]:
        name = [a for a in args[2:] if not a.startswith("-") and a != "user"][0]
        if name not in servers:
            sys.exit(f"fake claude: E_MISSING {name}")
        del servers[name]
    else:
        sys.exit(f"fake claude: unsupported {args}")
    cfg.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"File modified: {cfg}")
    if told("FAKE_CLAUDE_LIE"):
        sys.exit(f"fake claude: E_LIE {verb}")
    if told("FAKE_CLAUDE_HANG_AFTER"):
        time.sleep(120)
    if told("FAKE_CLAUDE_VANISH"):
        os.chmod(sys.argv[0], 0o644)
    """
)


def _module():
    """The command's module, or None where it does not exist yet (dev, for
    verify-red), so a test there fails on behaviour and not on an import."""
    try:
        import pyrite.cli.mcp_setup_command as mod
    except ImportError:
        return None
    return mod


def _guard_paths_inside(monkeypatch, mod, root: Path) -> None:
    """Fail the test if mcp-setup computes or writes a client path outside `root`."""
    root = root.resolve()

    def inside(path, what):
        resolved = Path(os.path.realpath(path))
        assert resolved.is_relative_to(root), f"{what} outside the test's temp dir: {path}"
        return path

    for name in ("desktop_config_path", "claude_code_config_path"):
        real = getattr(mod, name)
        monkeypatch.setattr(mod, name, lambda real=real, name=name: inside(real(), name))
    real_write = mod.atomic_write_text
    monkeypatch.setattr(
        mod, "atomic_write_text", lambda path, text: real_write(inside(path, "write"), text)
    )


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A temp HOME, a PATH holding only a scratch bin/, and a scratch cwd.

    Every variable a client path is computed from points inside tmp_path:
    HOME (POSIX Path.home()), USERPROFILE (Windows Path.home()), APPDATA
    (Windows Desktop), XDG_CONFIG_HOME (Linux Desktop); CLAUDE_CONFIG_DIR is
    unset so Claude Code's file is $HOME/.claude.json. A guard fails the test
    if a computed or written path still lands outside tmp_path. The "does the
    command start" probe is stubbed (one real subprocess per test otherwise);
    the two tests about it put the real one back.
    """
    home = tmp_path / "home"
    home.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("APPDATA", str(home / "AppData" / "Roaming"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("PATH", str(bindir))
    for var in (
        "PYRITE_CONFIG_DIR",
        "PYRITE_DATA_DIR",
        "PYRITE_FORMAT",
        "CLAUDE_CONFIG_DIR",
        "HOMEDRIVE",
        "HOMEPATH",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    # cli_error's console wraps at 80 columns when not on a terminal, inside
    # the long temp paths these tests look for.
    monkeypatch.setenv("COLUMNS", "1000")
    monkeypatch.chdir(work)

    class Env:
        pass

    e = Env()
    e.home, e.bin, e.work, e.tmp = home, bindir, work, tmp_path
    e.mod = _module()
    e.real_verify = None
    if e.mod is not None:
        _guard_paths_inside(monkeypatch, e.mod, tmp_path)
        e.real_verify = e.mod._verify_starts
        monkeypatch.setattr(e.mod, "_verify_starts", lambda *args, **kwargs: None)
    return e


def install_fake_claude(env) -> Path:
    claude = env.bin / "claude"
    claude.write_text(FAKE_CLAUDE.replace("@PYTHON@", sys.executable))
    claude.chmod(0o755)
    return claude


def claude_calls(env) -> list[list[str]]:
    log = env.home / "claude-calls.jsonl"
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines()]


def macos_desktop_config(home: Path) -> Path:
    return home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"


def linux_desktop_config(home: Path) -> Path:
    return home / ".config" / "Claude" / "claude_desktop_config.json"


def install_desktop(config: Path) -> Path:
    """Claude Desktop is installed: its config directory exists."""
    config.parent.mkdir(parents=True, exist_ok=True)
    return config


def servers_in(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))["mcpServers"]


def assert_starts_pyrite_at(entry: dict, tier: str) -> None:
    """The property a client needs: an absolute command that exists, and the
    tier named explicitly so a new entry depends on no default."""
    command = Path(entry["command"])
    assert command.is_absolute(), entry
    assert command.is_file(), entry
    assert command.name in ("pyrite", "pyrite.exe"), entry
    assert entry["args"] == ["mcp", "--tier", tier], entry


def setup(*args: str):
    return runner.invoke(app, ["mcp-setup", *args])


def report_of(result) -> dict:
    """The one JSON document the command prints by default."""
    try:
        return json.loads(result.stdout)
    except ValueError:
        raise AssertionError(f"stdout is not one JSON document:\n{result.output}") from None


def only_client(result) -> dict:
    clients = report_of(result)["clients"]
    assert len(clients) == 1, clients
    return clients[0]


# -- hand-authored fixtures -------------------------------------------------------

#: A config in the style the clients write: 2 spaces, LF, no trailing newline,
#: non-ASCII raw, another server and other top-level keys. @PYRITE@ is where a
#: `pyrite` member goes (or nothing).
CLIENT_STYLE = (
    "{\n"
    '  "globalShortcut": "Ctrl+Space",\n'
    '  "greeting": "café ☕ — naïve",\n'
    '  "mcpServers": {\n'
    '    "filesystem": {\n'
    '      "command": "npx",\n'
    '      "args": [\n'
    '        "-y",\n'
    '        "@modelcontextprotocol/server-filesystem"\n'
    "      ]\n"
    "    }@PYRITE@\n"
    "  },\n"
    '  "preferences": {\n'
    '    "sidebarWidth": 320,\n'
    '    "ratio": 1.5\n'
    "  }\n"
    "}"
)


def _claude_json() -> str:
    """`~/.claude.json` as Claude Code keeps it: large, many other top-level
    keys, a per-project `mcpServers` that is not the user scope's, 2 spaces,
    LF, no trailing newline, non-ASCII raw."""
    state = "".join(f'  "tipsHistory{n}": {n},\n' for n in range(110))
    return (
        "{\n"
        '  "numStartups": 412,\n'
        '  "userID": "0f3c…redacted",\n' + state + '  "projects": {\n'
        '    "/Users/someone/code": {\n'
        '      "mcpServers": {\n'
        '        "pyrite": {\n'
        '          "command": "/a/decoy/that/is/not/user/scope"\n'
        "        }\n"
        "      }\n"
        "    }\n"
        "  },\n"
        '  "mcpServers": {\n'
        '    "filesystem": {\n'
        '      "type": "stdio",\n'
        '      "command": "npx",\n'
        '      "args": [\n'
        '        "-y",\n'
        '        "@modelcontextprotocol/server-filesystem"\n'
        "      ],\n"
        '      "env": {}\n'
        "    }@PYRITE@\n"
        "  }\n"
        "}"
    )


def args_text(*args: str) -> str:
    return '"args": [\n' + ",\n".join(f'        "{a}"' for a in args) + "\n      ]"


def member(*fields: str, name: str = "pyrite") -> str:
    """A server member as the clients lay one out, to drop into @PYRITE@."""
    return f',\n    "{name}": {{\n' + ",\n".join("      " + f for f in fields) + "\n    }"


def cmd_text(command: str) -> str:
    return f'"command": "{command}"'


class Target:
    """One of the four places the command changes."""

    def __init__(self, env, kind: str):
        self.env, self.kind = env, kind
        self.template = CLIENT_STYLE
        if kind == "desktop":
            self.path = install_desktop(linux_desktop_config(env.home))
            self.args = ["--client", "claude-desktop"]
        elif kind == "project":
            self.path = env.work / ".mcp.json"
            self.args = ["--project"]
        elif kind == "config":
            self.path = env.tmp / "elsewhere" / "mcp.json"
            self.path.parent.mkdir()
            self.args = ["--config", str(self.path)]
        else:
            install_fake_claude(env)
            self.path = env.home / ".claude.json"
            self.args = ["--client", "claude-code"]
            self.template = _claude_json()

    @property
    def is_file(self) -> bool:
        return self.kind != "claude-code"

    def text(self, pyrite: str = "") -> str:
        return self.template.replace("@PYRITE@", pyrite)

    def seed(self, pyrite: str = "") -> bytes:
        data = self.text(pyrite).encode("utf-8")
        self.path.write_bytes(data)
        return data

    def run(self, *extra: str):
        return setup(*self.args, *extra)

    def entry(self) -> dict:
        return servers_in(self.path)["pyrite"]

    def written_default(self, tier: str = "write") -> str:
        """The member each target gets when none was there, as text."""
        if self.kind == "project":
            fields = [
                '"type": "stdio"',
                cmd_text(NEW),
                args_text("mcp", "--tier", tier),
                '"env": {}',
            ]
        else:
            fields = [cmd_text(NEW), args_text("mcp", "--tier", tier)]
        return member(*fields)

    def client_written(self, command: str, *args: str) -> str:
        """An existing entry in the shape this target's client writes."""
        if self.kind in ("project", "claude-code"):
            return member('"type": "stdio"', cmd_text(command), args_text(*args), '"env": {}')
        return member(cmd_text(command), args_text(*args))


TARGETS = ["desktop", "project", "config", "claude-code"]
FILE_TARGETS = ["desktop", "project", "config"]


@pytest.fixture(params=TARGETS)
def target(env, request):
    return Target(env, request.param)


@pytest.fixture(params=FILE_TARGETS)
def file_target(env, request):
    return Target(env, request.param)


def snapshot(path: Path):
    st = path.stat()
    return path.read_bytes(), st.st_mtime_ns, st.st_ino


# -- the matrix: existing entry x target -------------------------------------------


def test_absent_entry_is_written(target):
    target.seed()

    result = target.run()

    assert result.exit_code == 0, result.output
    item = only_client(result)
    assert item["status"] == "created", item
    if target.is_file:
        assert target.path.read_bytes() == target.text(target.written_default()).encode()
    else:
        assert claude_calls(target.env) == [
            ["mcp", "add", "-s", "user", "pyrite", "--", NEW, "mcp", "--tier", "write"]
        ]
        assert_starts_pyrite_at(target.entry(), "write")


def test_absent_file_is_created_in_the_clients_style(target):
    """No file yet: 2 spaces, LF and no trailing newline, as the clients write."""
    result = target.run()

    assert result.exit_code == 0, result.output
    assert only_client(result)["status"] == "created"
    assert_starts_pyrite_at(target.entry(), "write")
    if target.is_file:
        expected = '{\n  "mcpServers": {' + target.written_default()[1:] + "\n  }\n}"
        assert target.path.read_bytes() == expected.encode()


def test_equal_entry_writes_nothing(target):
    target.seed(target.client_written(NEW, "mcp", "--tier", "write"))
    before = snapshot(target.path)

    result = target.run()

    assert result.exit_code == 0, result.output
    assert only_client(result)["status"] == "unchanged"
    assert snapshot(target.path) == before
    assert claude_calls(target.env) == []


def test_equal_in_value_but_spelled_differently_writes_nothing(target):
    """One line, an escaped solidus, no type or env: the same entry."""
    spelled = NEW.replace("/", "\\/")
    one_line = f',\n    "pyrite": {{"args":["mcp","--tier","write"], "command":"{spelled}"}}'
    target.seed(one_line)
    before = snapshot(target.path)

    result = target.run("--tier", "write")

    assert result.exit_code == 0, result.output
    assert only_client(result)["status"] == "unchanged"
    assert snapshot(target.path) == before
    assert claude_calls(target.env) == []


def test_only_the_command_changes_when_only_the_command_differs(target):
    before = target.seed(target.client_written(OLD, "mcp", "--tier", "write"))

    result = target.run()

    assert result.exit_code == 0, result.output
    item = only_client(result)
    assert item["status"] == "changed", item
    assert item["changes"] == [{"field": "command", "old": OLD, "new": NEW}]
    if target.is_file:
        assert target.path.read_bytes() == before.replace(OLD.encode(), NEW.encode())
    else:
        assert [c[:2] for c in claude_calls(target.env)] == [["mcp", "remove"], ["mcp", "add"]]
        assert target.entry() == {
            "type": "stdio",
            "command": NEW,
            "args": ["mcp", "--tier", "write"],
            "env": {},
        }
        assert servers_in(target.path)["filesystem"]["command"] == "npx"


def test_tier_changes_when_tier_is_asked_for(target):
    before = target.seed(target.client_written(NEW, "mcp", "--tier", "admin"))

    result = target.run("--tier", "read")

    assert result.exit_code == 0, result.output
    item = only_client(result)
    assert item["status"] == "changed", item
    assert item["changes"] == [
        {"field": "args", "old": ["mcp", "--tier", "admin"], "new": ["mcp", "--tier", "read"]}
    ]
    if target.is_file:
        assert target.path.read_bytes() == before.replace(b'"admin"', b'"read"')
    else:
        assert target.entry()["args"] == ["mcp", "--tier", "read"]


def test_a_tier_nobody_asked_to_change_is_kept(target):
    before = target.seed(target.client_written(OLD, "mcp", "--tier", "admin"))

    result = target.run()

    assert result.exit_code == 0, result.output
    item = only_client(result)
    assert item["changes"] == [{"field": "command", "old": OLD, "new": NEW}]
    assert target.entry()["args"] == ["mcp", "--tier", "admin"]
    if target.is_file:
        assert target.path.read_bytes() == before.replace(OLD.encode(), NEW.encode())


def test_a_different_tier_alone_is_unchanged_without_tier(target):
    target.seed(target.client_written(NEW, "mcp", "--tier", "admin"))
    before = snapshot(target.path)

    result = target.run()

    assert result.exit_code == 0, result.output
    assert only_client(result)["status"] == "unchanged"
    assert snapshot(target.path) == before
    assert claude_calls(target.env) == []


CARRIES_EXTRA = (
    cmd_text(OLD),
    args_text("mcp", "--tier", "admin", "-vv"),
    '"env": {\n        "OPENAI_API_KEY": "' + SECRET + '"\n      }',
    '"timeout": 30000',
)


def test_what_the_entry_carries_is_kept_in_a_file(file_target):
    """Env, an argument after the tier, an unknown key: all survive, byte for
    byte; only the command path moves."""
    before = file_target.seed(member(*CARRIES_EXTRA))

    result = file_target.run()

    assert result.exit_code == 0, result.output
    item = only_client(result)
    assert item["status"] == "changed", item
    assert item["changes"] == [{"field": "command", "old": OLD, "new": NEW}]
    assert file_target.path.read_bytes() == before.replace(OLD.encode(), NEW.encode())
    kept = " ".join(item["kept"])
    assert "OPENAI_API_KEY" in kept and "-vv" in kept and "timeout" in kept and "admin" in kept
    assert SECRET not in result.output


def test_user_scope_entry_with_env_or_unknown_keys_stops_before_any_claude_call(env):
    """`claude mcp add` cannot reproduce env without putting its values on a
    command line, and drops unknown keys: stop, and say what to run by hand."""
    target = Target(env, "claude-code")
    target.seed(member(*CARRIES_EXTRA))
    before = snapshot(target.path)

    result = target.run()

    assert result.exit_code == 1, result.output
    item = only_client(result)
    assert item["status"] == "stopped", item
    assert item["error_code"] == "CLIENT_ENTRY_NEEDS_HAND_EDIT"
    assert claude_calls(env) == []
    assert snapshot(target.path) == before
    by_hand = "\n".join(item["by_hand"])
    assert "claude mcp remove -s user pyrite" in by_hand
    assert "claude mcp add -s user pyrite" in by_hand and NEW in by_hand
    assert "OPENAI_API_KEY" in by_hand and "timeout" in result.output
    assert SECRET not in result.output


def test_user_scope_entry_with_env_and_the_same_path_is_unchanged(env):
    target = Target(env, "claude-code")
    target.seed(member(*CARRIES_EXTRA).replace(OLD, NEW))
    before = snapshot(target.path)

    result = target.run()

    assert result.exit_code == 0, result.output
    assert only_client(result)["status"] == "unchanged"
    assert snapshot(target.path) == before
    assert claude_calls(env) == []


#: What the old `pyrite mcp-setup` wrote when `pyrite-admin` was not on PATH.
OLD_PYTHON_M = (cmd_text("python"), args_text("-m", "pyrite.admin_cli", "mcp"), '"env": {}')


def test_args_not_beginning_with_mcp_stop_and_name_force(target):
    target.seed(member(*OLD_PYTHON_M))
    before = snapshot(target.path)

    result = target.run()

    assert result.exit_code == 1, result.output
    item = only_client(result)
    assert item["status"] == "stopped", item
    assert item["error_code"] == "ENTRY_NOT_MCP"
    assert "--force" in item["suggestion"]
    assert snapshot(target.path) == before
    assert claude_calls(target.env) == []


@pytest.mark.parametrize("existing", [OLD_PYTHON_M, CARRIES_EXTRA], ids=["odd", "carries-extra"])
def test_force_writes_the_default_entry_and_names_what_it_discarded(target, existing):
    target.seed(member(*existing) + member(cmd_text(OLD), name="pyrite-read"))

    result = target.run("--force")

    assert result.exit_code == 0, result.output
    item = only_client(result)
    assert item["status"] == "changed", item
    discarded = " ".join(item["discarded"])
    if existing is CARRIES_EXTRA:
        assert "OPENAI_API_KEY" in discarded and "-vv" in discarded and "timeout" in discarded
    else:
        assert "pyrite.admin_cli" in discarded
    assert SECRET not in result.output
    if target.is_file:
        expected = target.text(target.written_default() + member(cmd_text(OLD), name="pyrite-read"))
        assert target.path.read_bytes() == expected.encode()
    else:
        assert_starts_pyrite_at(target.entry(), "write")
        assert servers_in(target.path)["pyrite-read"] == {"command": OLD}


OLD_TRIO = (
    member(cmd_text("/opt/legacy/bin/pyrite-admin"), args_text("mcp", "--tier", "read"), name="pyrite-read")
    + member(cmd_text("python"), args_text("-m", "pyrite.admin_cli", "mcp", "--tier", "write"), name="pyrite-write")
    + member(cmd_text("/opt/legacy/bin/pyrite-admin"), args_text("mcp", "--tier", "admin"), name="pyrite-admin")
)  # fmt: skip


@pytest.mark.parametrize("pyrite", ["absent", "differs"])
def test_the_old_trio_is_reported_and_never_removed(target, pyrite):
    """What the removed `pyrite-admin mcp-setup` left. Whose they are is not
    Pyrite's to guess: they stay, byte for byte, and the report names them."""
    existing = "" if pyrite == "absent" else target.client_written(OLD, "mcp", "--tier", "write")
    before = target.seed(OLD_TRIO + existing)

    result = target.run()

    assert result.exit_code == 0, result.output
    item = only_client(result)
    assert item["stale"] == ["pyrite-read", "pyrite-write", "pyrite-admin"]
    assert any("pyrite-read" in note for note in item["notes"])
    servers = servers_in(target.path)
    assert servers["pyrite-write"]["args"] == ["-m", "pyrite.admin_cli", "mcp", "--tier", "write"]
    assert_starts_pyrite_at(servers["pyrite"], "write")
    if target.is_file:
        if pyrite == "absent":
            expected = target.text(OLD_TRIO + target.written_default()).encode()
        else:
            expected = before.replace(OLD.encode(), NEW.encode())
        assert target.path.read_bytes() == expected
    else:
        assert all(call[-1] != "pyrite-read" for call in claude_calls(target.env))


# -- how the tier is written in an existing entry ----------------------------------


@pytest.mark.parametrize(
    ("old_args", "new_args"),
    [
        (["mcp", "--tier", "admin"], ["mcp", "--tier", "read"]),
        # `pyrite mcp` reads only --tier: a `-t` (pyrite-admin's) is rewritten.
        (["mcp", "-t", "admin"], ["mcp", "--tier", "read"]),
        (["mcp", "--tier=admin"], ["mcp", "--tier=read"]),
        (["mcp"], ["mcp", "--tier", "read"]),
        (["mcp", "--tier", "admin", "-vv"], ["mcp", "--tier", "read", "-vv"]),
        (["mcp", "-vv"], ["mcp", "--tier", "read", "-vv"]),
    ],
    ids=["long", "short", "equals", "absent", "args-after", "absent-with-args"],
)
def test_tier_is_changed_in_the_form_the_entry_uses(env, old_args, new_args):
    target = Target(env, "config")
    target.seed(member(cmd_text(NEW), args_text(*old_args)))

    result = target.run("--tier", "read")

    assert result.exit_code == 0, result.output
    assert only_client(result)["changes"] == [{"field": "args", "old": old_args, "new": new_args}]
    assert (
        target.path.read_bytes()
        == target.text(member(cmd_text(NEW), args_text(*new_args))).encode()
    )


def test_an_entry_whose_args_this_pyrite_rejects_is_not_written(target, monkeypatch):
    """The probe runs the entry as it would be written (`<cmd> <args> --help`):
    an option this install's pyrite rejects stops the write, whatever it is."""
    if target.env.mod is not None:
        monkeypatch.setattr(target.env.mod, "_verify_starts", target.env.real_verify)
    target.seed(member(cmd_text(OLD), args_text("mcp", "--no-such-option")))
    before = snapshot(target.path)

    result = target.run()

    assert result.exit_code == 1, result.output
    item = only_client(result)
    assert item["status"] == "stopped" and item["error_code"] == "ENTRY_WOULD_NOT_START", item
    assert "--no-such-option" in item["error"] and "--force" in item["suggestion"]
    assert snapshot(target.path) == before
    assert claude_calls(target.env) == []


@pytest.mark.parametrize("kind", ["config", "claude-code"])
def test_a_pyrite_admin_short_tier_entry_is_written_in_the_form_pyrite_reads(
    env, monkeypatch, kind
):
    """`pyrite-admin mcp -t admin` works; `pyrite mcp -t admin` exits 2. The
    entry keeps its tier, written as --tier, and the probe confirms it starts."""
    if env.mod is not None:
        monkeypatch.setattr(env.mod, "_verify_starts", env.real_verify)
    target = Target(env, kind)
    target.seed(target.client_written("/opt/legacy/bin/pyrite-admin", "mcp", "-t", "admin"))

    result = target.run()

    assert result.exit_code == 0, result.output
    item = only_client(result)
    assert (item["status"], item["tier"]) == ("changed", "admin"), item
    assert target.entry()["args"] == ["mcp", "--tier", "admin"]
    assert target.entry()["command"] == NEW


def test_readme_shaped_entry_without_a_tier_is_left_as_written(env):
    """README.md taught `"args": ["mcp"]` for a year. With no --tier asked,
    nothing about the tier is added."""
    target = Target(env, "config")
    target.seed(member(cmd_text(NEW), args_text("mcp")))
    before = snapshot(target.path)

    result = target.run()

    assert result.exit_code == 0, result.output
    assert only_client(result)["status"] == "unchanged"
    assert snapshot(target.path) == before


# -- entry values that are not what an entry should be -----------------------------


@pytest.mark.parametrize(
    "value",
    [
        '"just a string"',
        "null",
        "[1, 2]",
        '{"command": "/x/pyrite", "args": 5}',
        '{"command": "/x/pyrite", "args": "mcp"}',
        '{"command": "/x/pyrite", "args": null}',
        '{"command": "/x/pyrite", "args": ["mcp", 7]}',
        '{"command": 12, "args": ["mcp"]}',
        '{"args": ["mcp"]}',
        '{"command": "/x/pyrite", "args": ["mcp", "--tier"]}',
    ],
    ids=[
        "entry-string",
        "entry-null",
        "entry-array",
        "args-number",
        "args-string",
        "args-null",
        "args-element-number",
        "command-number",
        "command-missing",
        "tier-without-value",
    ],
)
def test_a_malformed_entry_is_an_error_code_not_a_traceback(target, value):
    target.seed(f',\n    "pyrite": {value}')
    before = snapshot(target.path)

    result = target.run("--tier", "read")

    assert result.exit_code == 1, result.output
    item = only_client(result)
    assert item["status"] == "stopped", item
    assert item["error_code"] in ("ENTRY_MALFORMED", "ENTRY_NOT_MCP"), item
    assert "--force" in item["suggestion"]
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert snapshot(target.path) == before
    assert claude_calls(target.env) == []


def test_env_that_is_not_an_object_is_kept_in_a_file(env):
    target = Target(env, "config")
    before = target.seed(member(cmd_text(OLD), args_text("mcp"), '"env": "odd"'))

    result = target.run()

    assert result.exit_code == 0, result.output
    assert target.path.read_bytes() == before.replace(OLD.encode(), NEW.encode())


# -- the rest of the file: the file's own style ------------------------------------

TWO_SPACE = (
    "{\n"
    '  "mcpServers": {\n'
    '    "other": {\n'
    '      "command": "/bin/true",\n'
    '      "args": []\n'
    "    }@PYRITE@\n"
    "  }\n"
    "}"
)


def restyle(text: str, indent: str, eol: str, bom: bool, tail: str) -> bytes:
    """The same hand-written document in another style: each leading pair of
    spaces becomes `indent`, each LF becomes `eol`."""
    lines = []
    for line in text.split("\n"):
        body = line.lstrip(" ")
        lines.append(indent * ((len(line) - len(body)) // 2) + body)
    out = eol.join(lines) + tail
    return (b"\xef\xbb\xbf" if bom else b"") + out.encode("utf-8")


STYLES = {
    "client": ("  ", "\n", False, ""),
    "trailing-newline": ("  ", "\n", False, "\n"),
    "two-trailing-newlines": ("  ", "\n", False, "\n\n"),
    "tabs": ("\t", "\n", False, "\n"),
    "four-spaces": ("    ", "\n", False, "\n"),
    "crlf": ("  ", "\r\n", False, "\r\n"),
    "bom": ("  ", "\n", True, ""),
    "tabs-crlf-bom": ("\t", "\r\n", True, "\r\n"),
}


@pytest.mark.parametrize("style", STYLES)
@pytest.mark.parametrize("existing", ["absent", "differs"])
def test_the_file_comes_back_in_its_own_style(env, style, existing):
    """Indent unit, line ending, BOM and trailing bytes are the file's, and
    the bytes outside the `pyrite` entry are identical."""
    target = Target(env, "config")
    old = member(cmd_text(OLD), args_text("mcp", "--tier", "write"))
    new = member(cmd_text(NEW), args_text("mcp", "--tier", "write"))
    before = restyle(
        TWO_SPACE.replace("@PYRITE@", old if existing == "differs" else ""), *STYLES[style]
    )
    target.path.write_bytes(before)

    result = target.run()

    assert result.exit_code == 0, result.output
    assert target.path.read_bytes() == restyle(TWO_SPACE.replace("@PYRITE@", new), *STYLES[style])


#: `.mcp.json` exactly as `claude mcp add -s project fs -e K=v -- npx -y fs`
#: wrote it (Claude Code 2.1.287, scratch HOME, 2026-10-02).
REAL_MCP_JSON = (
    "{\n"
    '  "mcpServers": {\n'
    '    "fs": {\n'
    '      "type": "stdio",\n'
    '      "command": "npx",\n'
    '      "args": [\n'
    '        "-y",\n'
    '        "fs"\n'
    "      ],\n"
    '      "env": {\n'
    '        "K": "v"\n'
    "      }\n"
    "    }@PYRITE@\n"
    "  }\n"
    "}"
)


def test_a_project_file_the_client_wrote_gains_only_the_entry(env):
    target = Target(env, "project")
    target.template = REAL_MCP_JSON
    target.seed()

    result = target.run()

    assert result.exit_code == 0, result.output
    assert target.path.read_bytes() == target.text(target.written_default()).encode()


@pytest.mark.parametrize(
    "text",
    ["", " \n\t\n", "{}", "{\n}", '{\n  "mcpServers": {}\n}', '{\n  "other": 1\n}'],
    ids=[
        "empty",
        "whitespace",
        "empty-object",
        "empty-object-2-lines",
        "empty-servers",
        "no-servers",
    ],
)
def test_a_file_with_no_servers_yet_gets_the_entry(env, text):
    target = Target(env, "config")
    target.path.write_text(text)

    result = target.run()

    assert result.exit_code == 0, result.output
    data = json.loads(target.path.read_text())
    assert_starts_pyrite_at(data.pop("mcpServers")["pyrite"], "write")
    assert data == ({"other": 1} if "other" in text else {})


def readme_snippet(doc: str) -> str:
    """The hand-written `mcpServers` block the doc teaches, as its text."""
    blocks = re.findall(r"```json\n(.*?)```", (REPO / doc).read_text(), flags=re.S)
    return next(b for b in blocks if '"mcpServers"' in b and '"pyrite"' in b)


@pytest.mark.parametrize("doc", ["README.md", "docs/getting-started.md"])
def test_the_entry_the_docs_teach_is_moved_to_this_install(env, doc):
    """The doc is the fixture: a user who pasted its snippet and then runs
    mcp-setup gets the command path changed and nothing else."""
    snippet = readme_snippet(doc)
    target = Target(env, "config")
    target.path.write_text(snippet)
    taught = json.loads(snippet)["mcpServers"]["pyrite"]

    result = target.run()

    assert result.exit_code == 0, result.output
    item = only_client(result)
    assert item["changes"] == [{"field": "command", "old": taught["command"], "new": NEW}]
    assert json.loads(target.path.read_text())["mcpServers"]["pyrite"] == {**taught, "command": NEW}
    assert target.path.read_text().endswith("}\n")


HAND_FORMATTED = (
    "{\n"
    '    "mcpServers": {\n'
    '        "other": {"command": "C:\\/tools\\/x", "args": ["-a", "-b"], "n": 1.10, "big": 1e5},\n'
    '        "name": "caf\\u00e9 \\u0041",\n'
    '\t"id": 12345678901234567890\n'
    "    }\n"
    "}\n"
)

HAND_FORMATTED_AFTER = (
    "{\n"
    '    "mcpServers": {\n'
    '        "other": {\n'
    '            "command": "C:/tools/x",\n'
    '            "args": [\n'
    '                "-a",\n'
    '                "-b"\n'
    "            ],\n"
    '            "n": 1.1,\n'
    '            "big": 100000.0\n'
    "        },\n"
    '        "name": "caf\\u00e9 A",\n'
    '        "id": 12345678901234567890,\n'
    '        "pyrite": {\n'
    '            "command": "@NEW@",\n'
    '            "args": [\n'
    '                "mcp",\n'
    '                "--tier",\n'
    '                "write"\n'
    "            ]\n"
    "        }\n"
    "    }\n"
    "}\n"
)


def test_the_respellings_that_remain_for_a_hand_formatted_file(env):
    """Pinned because the docs name them: a file a person formatted comes back
    with every value equal and some spelled the json module's way. One-line
    arrays and objects open out, a line indented differently is brought into
    line, `\\/` becomes `/`, a `\\u` escape of an ASCII
    character becomes the character, `1.10` becomes `1.1` and `1e5` becomes
    `100000.0`. A file the client wrote has none of these."""
    target = Target(env, "config")
    target.path.write_text(HAND_FORMATTED)

    result = target.run()

    assert result.exit_code == 0, result.output
    assert target.path.read_text() == HAND_FORMATTED_AFTER.replace("@NEW@", NEW)
    assert any("respelled" in note for note in only_client(result)["notes"])


def _emit(v, unit: str, level: int) -> str:
    """A hand-written emitter for the property test's documents: one item per
    line, the way the clients lay a file out. Independent of the command."""
    pad, inner = unit * level, unit * (level + 1)
    if isinstance(v, dict):
        if not v:
            return "{}"
        body = ",\n".join(f'{inner}"{k}": {_emit(x, unit, level + 1)}' for k, x in v.items())
        return "{\n" + body + "\n" + pad + "}"
    if isinstance(v, list):
        if not v:
            return "[]"
        return "[\n" + ",\n".join(inner + _emit(x, unit, level + 1) for x in v) + "\n" + pad + "]"
    if isinstance(v, str):
        return f'"{v}"'
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    return repr(v)


_WORDS = ["alpha", "b c", "naïve", "日本", "x/y", "-", "", "@scope/pkg"]


def _random_value(rng: random.Random, depth: int):
    kind = rng.choice("sssnnbz" if depth > 2 else "sssnnbzooa")
    if kind == "s":
        return rng.choice(_WORDS)
    if kind == "n":
        return rng.choice([0, 7, -3, 1.5, 320, 0.25, 12345678901234567890])
    if kind == "b":
        return rng.choice([True, False])
    if kind == "z":
        return None
    if kind == "a":
        return [_random_value(rng, depth + 1) for _ in range(rng.randint(0, 3))]
    return {f"k{i}": _random_value(rng, depth + 1) for i in range(rng.randint(0, 3))}


def test_seeded_random_documents_keep_every_byte_outside_the_entry(env):
    """Property: for a document laid out consistently in any indent unit, line
    ending, BOM and tail, the output is the input with the command path
    replaced, or with the member added last, and nothing else."""
    rng = random.Random(582)
    target = Target(env, "config")

    for case in range(150):
        head = {f"a{i}": _random_value(rng, 0) for i in range(rng.randint(0, 3))}
        foot = {f"z{i}": _random_value(rng, 0) for i in range(rng.randint(0, 2))}
        servers = [
            (f"s{i}", {"command": rng.choice(_WORDS), "args": _random_value(rng, 2)})
            for i in range(rng.randint(0, 3))
        ]
        has_entry = rng.random() < 0.6
        position = rng.randint(0, len(servers)) if has_entry else len(servers)
        unit = rng.choice(["  ", "    ", "\t", " ", "   "])
        eol = rng.choice(["\n", "\r\n"])
        bom = b"\xef\xbb\xbf" if rng.random() < 0.2 else b""
        tail = rng.choice(["", "\n", "\n\n"])
        texts = {}
        for command in (OLD, NEW):
            placed = list(servers)
            if has_entry or command == NEW:
                entry = {"command": command, "args": ["mcp", "--tier", "write"]}
                placed.insert(position, ("pyrite", entry))
            doc = {**head, "mcpServers": dict(placed), **foot}
            texts[command] = bom + (_emit(doc, unit, 0) + tail).replace("\n", eol).encode("utf-8")
        target.path.write_bytes(texts[OLD])

        result = target.run()

        assert result.exit_code == 0, f"case {case}: {result.output}\n{texts[OLD]!r}"
        assert target.path.read_bytes() == texts[NEW], f"case {case}: {texts[OLD]!r}"


# -- a file that cannot be read or written back safely: stop, untouched ------------


@pytest.mark.parametrize(
    "content",
    [
        b'{"mcpServers": {',
        b'["not", "an", "object"]',
        b'{"mcpServers": []}',
        b'{"mcpServers": null}',
        b'{\n  // a comment\n  "mcpServers": {}\n}',
        b'{"mcpServers": {}, /* c */ "a": 1}',
        b'{"mcpServers": {},}',
        b'{"mcpServers": {}, "x": NaN}',
        b'{"mcpServers": {}, "x": -Infinity}',
        b'{"mcpServers": {}, "x": 1e400}',
        b'{"mcpServers": {}, "name": "caf\xe9"}',
        b'{"mcpServers": {}, "mcpServers": {"a": {}}}',
        b'{"mcpServers": {"pyrite": {"command": "a"}, "pyrite": {"command": "b"}}}',
        b'{"mcpServers": {}, "x": {"k": 1, "k": 2}}',
        b'{"mcpServers": {}, "x": 0.123456789012345678901234567890}',
        b'{"mcpServers": {}, "name": "caf\xc3\xa9 \\ud800"}',
        b"[" * 100_000 + b"]" * 100_000,
        b'{"a":' * 100_000 + b"1" + b"}" * 100_000,
    ],
    ids=[
        "truncated",
        "top-level-array",
        "servers-array",
        "servers-null",
        "line-comment",
        "block-comment",
        "trailing-comma",
        "nan",
        "infinity",
        "overflowing-number",
        "latin-1",
        "duplicate-mcpServers",
        "duplicate-pyrite",
        "duplicate-key-elsewhere",
        "number-with-more-digits-than-a-float",
        "lone-surrogate-beside-raw-non-ascii",
        "arrays-nested-100000-deep",
        "objects-nested-100000-deep",
    ],
)
def test_a_file_that_cannot_come_back_whole_is_refused_untouched(file_target, content):
    file_target.path.write_bytes(content)
    before = snapshot(file_target.path)

    result = file_target.run()

    assert result.exit_code == 1, result.output[-2000:]
    item = only_client(result)
    assert item["status"] == "stopped", item
    # Valid JSON whose values a rewrite cannot give back is caught by the
    # check on the output; everything else when the file is read.
    lost = (b"1e400", b"0.1234567890123456789", b"\\ud800")
    expected = "CONFIG_NOT_PRESERVED" if any(m in content for m in lost) else "CONFIG_INVALID"
    assert item["error_code"] == expected, item
    assert item["suggestion"]
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert snapshot(file_target.path) == before
    assert sorted(p.name for p in file_target.path.parent.iterdir()) == [file_target.path.name]


@pytest.mark.parametrize("depth", [300, 500, 900, 2000])
def test_a_file_nested_hundreds_deep_is_written_whole_or_config_invalid(file_target, depth):
    """json.loads reads deeper than the encoder and the check can walk. At
    any depth: written with every value intact, or CONFIG_INVALID with the
    file untouched; never INTERNAL_ERROR. Where the line falls depends on the
    stack, so the test does not pin it."""
    nested = '{"a": ' * depth + "1" + "}" * depth
    file_target.path.write_text('{"mcpServers": {}, "x": ' + nested + "}")
    before = snapshot(file_target.path)

    result = file_target.run()

    item = only_client(result)
    if item["status"] == "created":
        assert json.loads(file_target.path.read_text())["x"] == json.loads(nested)
    else:
        _assert_stopped_untouched(file_target, result, "CONFIG_INVALID", before)
        assert "nested too deeply" in item["error"]


def test_invalid_json_names_the_line_and_column(env):
    target = Target(env, "config")
    target.path.write_text('{\n  "mcpServers": {,\n}')

    result = target.run()

    assert "line 2" in only_client(result)["error"]


def test_a_decoy_mcp_servers_elsewhere_is_never_touched(env):
    """Only the top-level member is the client's list of servers."""
    target = Target(env, "config")
    before = (
        "{\n"
        '  "note": "the \\"mcpServers\\" key holds \\"pyrite\\": {...}",\n'
        '  "projects": {\n'
        '    "/p": {\n'
        '      "mcpServers": {\n'
        '        "pyrite": {\n'
        '          "command": "/decoy"\n'
        "        }\n"
        "      }\n"
        "    }\n"
        "  }\n"
        "}"
    )
    target.path.write_text(before)

    result = target.run()

    assert result.exit_code == 0, result.output
    new = member(cmd_text(NEW), args_text("mcp", "--tier", "write"))[2:]
    assert target.path.read_text() == before[:-2] + ',\n  "mcpServers": {\n' + new + "\n  }\n}"


def test_output_that_would_change_another_value_is_never_written(env, monkeypatch):
    """The check before the replace: the new text is parsed and compared with
    the old outside the `pyrite` entry. Forced here by a serializer that
    drops a server."""
    target = Target(env, "config")
    target.seed()
    before = snapshot(target.path)
    if env.mod is not None:
        real = env.mod._serialize

        def lossy(data, style):
            data = json.loads(json.dumps(data))
            del data["mcpServers"]["filesystem"]
            return real(data, style)

        monkeypatch.setattr(env.mod, "_serialize", lossy)

    result = target.run()

    assert result.exit_code == 1, result.output
    assert only_client(result)["error_code"] == "CONFIG_NOT_PRESERVED"
    assert snapshot(target.path) == before


def test_a_file_changed_between_the_read_and_the_replace_stops(target, monkeypatch):
    """Another writer got in after the decision was made: nothing is written
    over it, and the user is told to re-run."""
    target.seed(target.client_written(OLD, "mcp", "--tier", "write"))
    theirs = target.text(target.client_written("/someone/elses/pyrite", "mcp")).encode()
    if target.env.mod is not None:
        real = target.env.mod._decide

        def decide_then_someone_writes(*args, **kwargs):
            plan = real(*args, **kwargs)
            target.path.write_bytes(theirs)
            return plan

        monkeypatch.setattr(target.env.mod, "_decide", decide_then_someone_writes)

    result = target.run()

    assert result.exit_code == 1, result.output
    item = only_client(result)
    assert item["status"] == "stopped" and item["error_code"] == "CONFIG_CHANGED", item
    assert "re-run" in item["suggestion"]
    assert target.path.read_bytes() == theirs
    assert claude_calls(target.env) == []


@pytest.mark.control(
    reason="passes on dev, which wrote in place and so kept the mode; guards that "
    "replacing the file by rename keeps it too"
)
def test_rewrite_keeps_the_files_mode(env):
    target = Target(env, "config")
    target.seed()
    target.path.chmod(0o640)

    result = target.run()

    assert result.exit_code == 0, result.output
    assert stat.S_IMODE(target.path.stat().st_mode) == 0o640


def test_a_new_desktop_config_is_private_like_the_one_the_client_creates(env):
    """Claude Desktop creates its config 0600 (spike). A new `.mcp.json` or
    `--config` file gets the umask's mode, as `claude mcp add -s project` does."""
    desktop = Target(env, "desktop")
    project = Target(env, "project")
    old = os.umask(0o022)
    try:
        assert desktop.run().exit_code == 0
        assert project.run().exit_code == 0
    finally:
        os.umask(old)

    assert stat.S_IMODE(desktop.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(project.path.stat().st_mode) == 0o644


def _assert_stopped_untouched(target, result, code: str, before) -> dict:
    assert result.exit_code == 1, result.output
    item = only_client(result)
    assert item["status"] == "stopped" and item["error_code"] == code, item
    assert snapshot(target.path) == before
    return item


def test_read_only_file_is_refused_not_replaced(file_target):
    file_target.seed()
    file_target.path.chmod(0o444)
    before = snapshot(file_target.path)

    result = file_target.run()

    _assert_stopped_untouched(file_target, result, "CONFIG_NOT_WRITABLE", before)
    assert stat.S_IMODE(file_target.path.stat().st_mode) == 0o444


def test_hard_linked_file_is_refused_because_it_cannot_be_replaced_atomically(file_target):
    """atomic_write_text writes a hard-linked file in place; a crash there
    leaves half a config. For a guest file that is not all-or-nothing."""
    file_target.seed()
    other_name = file_target.env.tmp / "second-name.json"
    os.link(file_target.path, other_name)
    before = snapshot(file_target.path)

    result = file_target.run()

    item = _assert_stopped_untouched(file_target, result, "CONFIG_NOT_WRITABLE", before)
    assert "hard link" in item["error"]
    assert other_name.read_bytes() == before[0]


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="needs mode bits to bind")
def test_file_in_a_directory_that_cannot_be_written_is_refused(file_target):
    """The other in-place fallback of atomic_write_text."""
    file_target.seed()
    before = snapshot(file_target.path)
    file_target.path.parent.chmod(0o555)
    try:
        result = file_target.run()
    finally:
        file_target.path.parent.chmod(0o755)

    item = _assert_stopped_untouched(file_target, result, "CONFIG_NOT_WRITABLE", before)
    assert "directory" in item["error"]


def test_a_file_another_user_owns_is_refused(file_target, monkeypatch):
    """The third in-place fallback of atomic_write_text: it cannot give a new
    file another user's ownership, so it would write in place."""
    if not hasattr(os, "geteuid"):
        pytest.skip("no POSIX ownership")
    file_target.seed()
    before = snapshot(file_target.path)
    monkeypatch.setattr(os, "geteuid", lambda: file_target.path.stat().st_uid + 1)

    result = file_target.run()

    item = _assert_stopped_untouched(file_target, result, "CONFIG_NOT_WRITABLE", before)
    assert "another user" in item["error"]


def test_a_failed_first_write_leaves_no_directory_behind(env, monkeypatch):
    path = env.tmp / "not" / "there" / "mcp.json"

    def interrupted(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", interrupted)

    result = setup("--config", str(path))

    assert result.exit_code == 1, result.output
    assert only_client(result)["error_code"] == "CONFIG_WRITE_FAILED"
    assert not (env.tmp / "not").exists()


def test_a_missing_directory_is_created_for_a_named_file(env):
    path = env.tmp / "not" / "there" / "yet" / "mcp.json"

    result = setup("--config", str(path))

    assert result.exit_code == 0, result.output
    assert_starts_pyrite_at(servers_in(path)["pyrite"], "write")


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_symlinked_config_is_written_through_the_link(env):
    """A dotfiles setup: the client's path is a link to a file elsewhere. The
    target gets the entry; the link stays a link."""
    real = env.tmp / "dotfiles" / "claude_desktop_config.json"
    real.parent.mkdir()
    real.write_text(TWO_SPACE.replace("@PYRITE@", ""))
    target = Target(env, "desktop")
    target.path.symlink_to(real)

    result = target.run()

    assert result.exit_code == 0, result.output
    assert target.path.is_symlink() and os.readlink(target.path) == str(real)
    new = member(cmd_text(NEW), args_text("mcp", "--tier", "write"))
    assert real.read_text() == TWO_SPACE.replace("@PYRITE@", new)
    assert only_client(result)["written_to"] == str(real)
    assert sorted(p.name for p in real.parent.iterdir()) == [real.name]


def test_a_write_that_fails_part_way_leaves_the_old_file_whole(file_target, monkeypatch):
    file_target.seed()
    before = snapshot(file_target.path)

    def interrupted(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", interrupted)

    result = file_target.run()

    _assert_stopped_untouched(file_target, result, "CONFIG_WRITE_FAILED", before)
    assert sorted(p.name for p in file_target.path.parent.iterdir()) == [file_target.path.name]


def test_any_exception_while_writing_is_reported_for_that_client(env, monkeypatch):
    """Not only OSError: whatever goes wrong changing one client is that
    client's reported outcome, never a traceback after another was changed."""
    install_fake_claude(env)
    desktop = Target(env, "desktop")
    desktop.seed()
    before = snapshot(desktop.path)
    if env.mod is not None:

        def broken(*args, **kwargs):
            raise RuntimeError("a bug nobody predicted")

        monkeypatch.setattr(env.mod, "atomic_write_text", broken)

    result = setup()

    assert result.exit_code == 1, result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)
    by_client = {c["client"]: c for c in report_of(result)["clients"]}
    assert by_client["claude-code"]["status"] == "created"
    assert by_client["claude-desktop"]["status"] == "stopped"
    assert by_client["claude-desktop"]["error_code"] == "INTERNAL_ERROR"
    assert "a bug nobody predicted" in by_client["claude-desktop"]["error"]
    assert snapshot(desktop.path) == before


# -- both clients: each is all or nothing, and the report says which changed -------


def test_first_client_changed_and_second_stopped(env):
    install_fake_claude(env)
    desktop = Target(env, "desktop")
    desktop.path.write_text('{"mcpServers": {')

    result = setup()

    assert result.exit_code == 1, result.output
    by_client = {c["client"]: c["status"] for c in report_of(result)["clients"]}
    assert by_client == {"claude-code": "created", "claude-desktop": "stopped"}
    assert_starts_pyrite_at(servers_in(env.home / ".claude.json")["pyrite"], "write")
    assert desktop.path.read_text() == '{"mcpServers": {'


def test_first_client_stopped_and_second_changed(env):
    install_fake_claude(env)
    (env.home / ".claude.json").write_text("")
    desktop = Target(env, "desktop")

    result = setup()

    assert result.exit_code == 1, result.output
    by_client = {c["client"]: c["status"] for c in report_of(result)["clients"]}
    assert by_client == {"claude-code": "stopped", "claude-desktop": "created"}
    assert_starts_pyrite_at(desktop.entry(), "write")
    assert (env.home / ".claude.json").read_text() == ""
    assert claude_calls(env) == []


def test_both_clients_found_are_both_configured_and_named(env):
    install_fake_claude(env)
    desktop = Target(env, "desktop")

    result = setup("--tier", "read")

    assert result.exit_code == 0, result.output
    assert_starts_pyrite_at(desktop.entry(), "read")
    assert_starts_pyrite_at(servers_in(env.home / ".claude.json")["pyrite"], "read")
    report = report_of(result)
    assert {c["client"]: c["location"] for c in report["clients"]} == {
        "claude-code": str(env.home / ".claude.json"),
        "claude-desktop": str(desktop.path),
    }
    assert report["new_entry"]["command"] == [NEW, "mcp", "--tier", "read"]


# -- Claude Code user scope: only through `claude`, and never half done ------------


@pytest.mark.parametrize(
    "content",
    [b"", b"  \n", b'{"mcpServers": {', b"[]", b'{"mcpServers": []}', b'{"x": NaN}', b"\xff\xfe"],
    ids=["empty", "whitespace", "truncated", "array", "servers-array", "nan", "not-utf-8"],
)
def test_claude_is_not_called_when_its_config_is_empty_or_unparseable(env, content):
    """Claude Code declares such a file corrupted and replaces it with fresh
    state on any `claude mcp` call (spike, 2.1.287)."""
    install_fake_claude(env)
    config = env.home / ".claude.json"
    config.write_bytes(content)

    result = setup("--client", "claude-code")

    assert result.exit_code == 1, result.output
    item = only_client(result)
    assert item["status"] == "stopped" and item["error_code"] == "CLIENT_CONFIG_UNSAFE", item
    assert claude_calls(env) == []
    assert config.read_bytes() == content


def test_claude_config_nested_absurdly_deep_is_an_error_code(env):
    install_fake_claude(env)
    (env.home / ".claude.json").write_bytes(b'{"a":' * 100_000 + b"1" + b"}" * 100_000)

    result = setup("--client", "claude-code")

    assert result.exit_code == 1, result.output[-2000:]
    assert only_client(result)["error_code"] == "CLIENT_CONFIG_UNSAFE"
    assert claude_calls(env) == []


def test_claude_config_without_mcp_servers_gets_the_entry(env):
    install_fake_claude(env)
    (env.home / ".claude.json").write_text('{\n  "numStartups": 3\n}')

    result = setup("--client", "claude-code")

    assert result.exit_code == 0, result.output
    assert_starts_pyrite_at(servers_in(env.home / ".claude.json")["pyrite"], "write")


def test_claude_config_dir_is_where_the_entry_is_read(env, monkeypatch):
    """CLAUDE_CONFIG_DIR moves Claude Code's .claude.json."""
    target = Target(env, "claude-code")
    alt = env.tmp / "claude-config"
    alt.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(alt))
    target.path = alt / ".claude.json"
    target.seed(target.client_written(NEW, "mcp", "--tier", "write"))

    result = target.run()

    assert result.exit_code == 0, result.output
    item = only_client(result)
    assert item["status"] == "unchanged" and item["location"] == str(alt / ".claude.json")
    assert claude_calls(env) == []


def test_claude_codes_own_file_is_never_written_by_pyrite(env, monkeypatch):
    """Created, changed: every change to ~/.claude.json is made by `claude`.
    Pyrite's one file writer is never reached."""
    target = Target(env, "claude-code")
    if env.mod is not None:

        def never(path, text):
            raise AssertionError(f"mcp-setup wrote {path} itself")

        monkeypatch.setattr(env.mod, "atomic_write_text", never)

    created = target.run()
    changed = target.run("--tier", "read")

    assert (created.exit_code, changed.exit_code) == (0, 0), created.output + changed.output
    assert [c[:2] for c in claude_calls(env)] == [["mcp", "add"], ["mcp", "remove"], ["mcp", "add"]]
    assert only_client(changed)["status"] == "changed"


def _seed_changeable(env) -> Target:
    target = Target(env, "claude-code")
    target.seed(target.client_written(OLD, "mcp", "--tier", "admin"))
    return target


OLD_ENTRY = {"type": "stdio", "command": OLD, "args": ["mcp", "--tier", "admin"], "env": {}}


def test_a_failed_remove_changes_nothing_and_no_add_is_tried(env, monkeypatch):
    target = _seed_changeable(env)
    before = target.path.read_bytes()
    monkeypatch.setenv("FAKE_CLAUDE_FAIL", "remove")

    result = target.run()

    assert result.exit_code == 1, result.output
    item = only_client(result)
    assert item["status"] == "stopped" and item["error_code"] == "CLIENT_COMMAND_FAILED", item
    assert [c[:2] for c in claude_calls(env)] == [["mcp", "remove"]]
    assert target.path.read_bytes() == before


def test_an_add_that_times_out_after_remove_restores_the_previous_entry(env, monkeypatch):
    target = _seed_changeable(env)
    monkeypatch.setenv("FAKE_CLAUDE_HANG", "add#1")
    if env.mod is not None:
        monkeypatch.setattr(env.mod, "_SUBPROCESS_TIMEOUT", 8)

    result = target.run()

    assert result.exit_code == 1, result.output
    item = only_client(result)
    assert item["status"] == "stopped", item
    assert "restored" in item["error"]
    assert target.entry() == OLD_ENTRY


@pytest.mark.parametrize("how", ["FAKE_CLAUDE_FAIL", "FAKE_CLAUDE_HANG"])
def test_when_the_restore_fails_too_the_report_holds_exactly_what_was_removed(
    env, monkeypatch, how
):
    target = _seed_changeable(env)
    monkeypatch.setenv(how, "add")
    if env.mod is not None:
        monkeypatch.setattr(env.mod, "_SUBPROCESS_TIMEOUT", 8)

    result = target.run()

    assert result.exit_code == 1, result.output
    item = only_client(result)
    assert item["status"] == "failed" and item["error_code"] == "CLIENT_COMMAND_FAILED", item
    assert item["removed"] == {"command": OLD, "args": ["mcp", "--tier", "admin"]}
    assert f"claude mcp add -s user pyrite -- {OLD} mcp --tier admin" in "\n".join(item["by_hand"])
    assert "pyrite" not in servers_in(target.path)


@pytest.mark.parametrize(
    "fails", ["", "add#1", "add"], ids=["add-succeeds", "restored", "not-restored"]
)
def test_undecodable_output_from_claude_is_judged_like_any_other(env, monkeypatch, fails):
    """`claude` printing bytes that are not text (or Windows reading Node's
    UTF-8 as cp1252) must not end the sequence after `remove`."""
    target = _seed_changeable(env)
    monkeypatch.setenv("FAKE_CLAUDE_GARBLE", "add")
    monkeypatch.setenv("FAKE_CLAUDE_FAIL", fails)

    result = target.run()

    item = only_client(result)
    assert item.get("error_code") != "INTERNAL_ERROR", item
    if fails == "":
        assert result.exit_code == 0 and item["status"] == "changed", item
    elif fails == "add#1":
        assert item["status"] == "stopped" and "restored" in item["error"], item
        assert target.entry() == OLD_ENTRY
    else:
        assert item["status"] == "failed", item
        assert item["removed"] == {"command": OLD, "args": ["mcp", "--tier", "admin"]}
        assert f"-- {OLD} mcp --tier admin" in item["by_hand"][0]
        # The client's own words survive the bad bytes (replaced, not fatal).
        assert "E_REFUSED add" in item["error"] and "garbled" in item["error"], item


def test_an_entry_with_an_argument_no_command_line_can_carry_stops_before_remove(env):
    """A NUL in the entry's own args (valid JSON: "\\u0000") cannot be passed
    to `claude mcp add`, so the entry could be removed and never added again,
    by this command or by a printed one. It stops before `remove`, naming the
    arguments it carries. (On dev the entry was removed only with --force or
    where the start check was skipped, which this file's `env` fixture does;
    without --force `_verify_starts` stopped first with ENTRY_WOULD_NOT_START.
    What #690 changes is the reason given: the NUL is a part the run would
    keep, and no command line can carry it.)"""
    target = Target(env, "claude-code")
    target.seed(member(cmd_text(OLD), args_text("mcp", "a\\u0000b")))
    before = snapshot(target.path)

    result = target.run()

    assert result.exit_code == 1, result.output
    item = only_client(result)
    assert item["status"] == "stopped", item
    assert item["error_code"] == "CLIENT_ENTRY_NEEDS_HAND_EDIT", item
    assert item["entry"] == {"command": OLD, "args": ["mcp", "a\x00b"]}
    assert "args" in item["error"] and "a\x00b" in item["error"], item
    assert "by_hand" not in item and str(target.path) in item["suggestion"], item
    assert claude_calls(env) == []
    assert snapshot(target.path) == before


#: Each step after `remove` where something can go wrong, and how to make it
#: go wrong there: (stub instructions, failing read-backs after this many
#: reads, the outcome the guarantee allows). Reads: 1 decides, 2 re-checks
#: before any call, 3 settles the failure, 4 checks the restore.
AFTER_REMOVE = {
    "remove-fails-but-removed": ({"FAKE_CLAUDE_LIE": "remove"}, None, "stopped"),
    "add-fails-but-added": ({"FAKE_CLAUDE_LIE": "add"}, None, "changed"),
    "remove-leaves-file-unreadable": ({"FAKE_CLAUDE_CORRUPT": "remove"}, None, "failed"),
    "add-fails": ({"FAKE_CLAUDE_FAIL": "add#1"}, None, "stopped"),
    "add-output-undecodable": (
        {"FAKE_CLAUDE_GARBLE": "add", "FAKE_CLAUDE_FAIL": "add#1"}, None, "stopped"
    ),
    "add-cannot-run": ({"FAKE_CLAUDE_VANISH": "remove"}, None, "failed"),
    "add-leaves-file-unreadable": ({"FAKE_CLAUDE_CORRUPT": "add"}, None, "failed"),
    "read-back-raises": ({"FAKE_CLAUDE_FAIL": "add"}, 2, "failed"),
    "restore-fails": ({"FAKE_CLAUDE_FAIL": "add"}, None, "failed"),
    "restore-read-back-raises": ({"FAKE_CLAUDE_FAIL": "add"}, 3, "failed"),
}  # fmt: skip


#: Steps whose outcome #690 did not change (it edited what this test asserts
#: where the last read of the file fails, and what `by_hand` holds).
_SAME_AS_BEFORE_690 = pytest.mark.control(
    reason="passes on dev: #690 did not change what these steps end in; the test body was "
    "edited for the steps that end with the entry gone"
)


@pytest.mark.parametrize(
    "step",
    [
        pytest.param(step, marks=_SAME_AS_BEFORE_690) if outcome != "failed" else step
        for step, (_, _, outcome) in AFTER_REMOVE.items()
    ],
)
def test_a_failure_at_any_step_after_remove_restores_or_reports_what_was_removed(
    env, monkeypatch, step
):
    """Criterion 5, by construction: whatever fails after `remove` is
    attempted, the entry is in place (as it was, or restored), or the report
    holds exactly what was removed and the command that puts it back. Where
    the last read of the file failed there is no command: Claude Code
    replaces a file it cannot read (#690)."""
    stub, reads_ok, outcome = AFTER_REMOVE[step]
    target = _seed_changeable(env)
    for var, value in stub.items():
        monkeypatch.setenv(var, value)
    if reads_ok is not None and env.mod is not None:
        real, reads = env.mod._read_claude_config, []

        def read_then_break(config):
            reads.append(config)
            if len(reads) > reads_ok:
                raise RuntimeError("a bug nobody predicted")
            return real(config)

        monkeypatch.setattr(env.mod, "_read_claude_config", read_then_break)

    result = target.run()

    item = only_client(result)
    assert item["status"] == outcome, item
    if outcome == "changed":  # the file holds the new entry: judged by the file
        assert result.exit_code == 0 and target.entry()["command"] == NEW, result.output
        return
    assert result.exit_code == 1, result.output
    assert item["error_code"] == "CLIENT_COMMAND_FAILED", item
    if outcome == "stopped":
        assert target.entry() == OLD_ENTRY
    else:
        assert item["removed"] == {"command": OLD, "args": ["mcp", "--tier", "admin"]}
    if outcome == "failed" and ("unreadable" in step or reads_ok is not None):
        assert "could not be read back" in item["error"]
        assert "by_hand" not in item and not CLAUDE_COMMAND.search(item["suggestion"]), item
    elif outcome == "failed":
        assert item["by_hand"] == [f"claude mcp add -s user pyrite -- {OLD} mcp --tier admin"]
    if "unreadable" in step:
        # Claude Code replaces a file it cannot read: it is not called again.
        assert claude_calls(env)[-1][:2] == ["mcp", step.split("-")[0]]


def test_a_call_that_times_out_after_doing_its_work_is_judged_by_the_file(env, monkeypatch):
    """Whether a timed-out `claude` changed its file is read back, not
    assumed: here it wrote the entry and then hung."""
    install_fake_claude(env)
    monkeypatch.setenv("FAKE_CLAUDE_HANG_AFTER", "add")
    if env.mod is not None:
        monkeypatch.setattr(env.mod, "_SUBPROCESS_TIMEOUT", 8)

    result = setup("--client", "claude-code")

    assert result.exit_code == 0, result.output
    item = only_client(result)
    assert item["status"] == "created", item
    assert any("timed out" in note for note in item["notes"])
    assert_starts_pyrite_at(servers_in(env.home / ".claude.json")["pyrite"], "write")


def test_a_first_add_that_leaves_the_config_unreadable_claims_nothing(env, monkeypatch):
    install_fake_claude(env)
    monkeypatch.setenv("FAKE_CLAUDE_CORRUPT", "add")

    result = setup("--client", "claude-code")

    assert result.exit_code == 1, result.output
    item = only_client(result)
    assert item["status"] == "failed" and "could not be read back" in item["error"], item


def test_a_failed_first_add_changes_nothing(env, monkeypatch):
    install_fake_claude(env)
    monkeypatch.setenv("FAKE_CLAUDE_FAIL", "add")

    result = setup("--client", "claude-code")

    assert result.exit_code == 1, result.output
    item = only_client(result)
    assert item["status"] == "stopped" and item["error_code"] == "CLIENT_COMMAND_FAILED", item
    assert not (env.home / ".claude.json").exists()


def test_claude_code_is_found_in_its_windows_install_dir(env, monkeypatch):
    """The native installer puts claude.exe in %USERPROFILE%\\.local\\bin, which
    need not be on PATH."""
    monkeypatch.setattr(sys, "platform", "win32")
    # Not on PATH. Python 3.13's shutil.which takes its Windows branch from
    # sys.platform and calls _winapi, which a Linux runner does not have.
    monkeypatch.setattr(shutil, "which", lambda *args, **kwargs: None)
    local_bin = env.home / ".local" / "bin"
    local_bin.mkdir(parents=True)
    exe = local_bin / "claude.exe"
    exe.write_text(FAKE_CLAUDE.replace("@PYTHON@", sys.executable))
    exe.chmod(0o755)

    result = setup("--client", "claude-code")

    assert result.exit_code == 0, result.output
    assert_starts_pyrite_at(servers_in(env.home / ".claude.json")["pyrite"], "write")


@pytest.mark.skipif(REAL_CLAUDE is None, reason="Claude Code is not installed")
def test_real_claude_code_reads_back_the_entry(env, monkeypatch):
    """The fake's contract, checked against the real client in a temp HOME:
    `claude mcp get` reads the entry from its own config; a re-run at another
    tier goes through remove and add; and an empty config is never handed to
    the client (it would replace it)."""
    monkeypatch.setenv("PATH", f"{env.bin}{os.pathsep}{Path(REAL_CLAUDE).parent}")
    if env.mod is not None:
        monkeypatch.setattr(env.mod, "_verify_starts", env.real_verify)
    config = env.home / ".claude.json"

    result = setup("--client", "claude-code")

    assert result.exit_code == 0, result.output
    assert_starts_pyrite_at(servers_in(config)["pyrite"], "write")
    got = subprocess.run(
        [REAL_CLAUDE, "mcp", "get", "pyrite"],
        cwd=env.work,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert "Args: mcp --tier write" in got.stdout, got.stdout + got.stderr
    assert "User config" in got.stdout, got.stdout
    assert "Connected" in got.stdout, got.stdout

    again = setup("--client", "claude-code", "--tier", "read")

    assert again.exit_code == 0, again.output
    assert only_client(again)["status"] == "changed"
    assert_starts_pyrite_at(servers_in(config)["pyrite"], "read")

    config.write_text("")
    refused = setup("--client", "claude-code")

    assert refused.exit_code == 1, refused.output
    assert only_client(refused)["error_code"] == "CLIENT_CONFIG_UNSAFE"
    assert config.read_text() == ""


# -- env: never added to an entry that exists --------------------------------------


def test_a_new_entry_pins_an_explicit_config_dir(env, monkeypatch):
    """A GUI-launched server gets no shell environment, so the config the user
    set up with would otherwise be silently swapped for ~/.pyrite."""
    install_fake_claude(env)
    desktop = Target(env, "desktop")
    monkeypatch.setenv("PYRITE_CONFIG_DIR", str(env.tmp / "my-config"))

    result = setup()

    assert result.exit_code == 0, result.output
    assert desktop.entry()["env"] == {"PYRITE_CONFIG_DIR": str(env.tmp / "my-config")}
    by_client = {c["client"]: c for c in report_of(result)["clients"]}
    assert by_client["claude-desktop"]["pinned"] == {
        "PYRITE_CONFIG_DIR": str(env.tmp / "my-config")
    }
    # Claude Code inherits the launching shell and resolves per project.
    assert servers_in(env.home / ".claude.json")["pyrite"]["env"] == {}


def test_the_text_report_shows_a_new_entrys_pin_in_full(env, monkeypatch):
    Target(env, "desktop")
    monkeypatch.setenv("PYRITE_CONFIG_DIR", str(env.tmp / "my-config"))
    monkeypatch.setenv("PYRITE_FORMAT", "rich")

    result = setup()

    assert result.exit_code == 0, result.output
    assert f"pinned: PYRITE_CONFIG_DIR={env.tmp / 'my-config'}" in result.output


@pytest.mark.parametrize(
    "existing_env", [None, '"env": {\n        "PYRITE_CONFIG_DIR": "/their/own"\n      }']
)
def test_env_is_never_added_to_an_entry_that_exists(env, monkeypatch, existing_env):
    """The shell names a config directory the entry does not. Nothing is
    written; the report says so and gives the line to add by hand."""
    target = Target(env, "desktop")
    fields = [cmd_text(NEW), args_text("mcp", "--tier", "write")] + (
        [existing_env] if existing_env else []
    )
    target.seed(member(*fields))
    before = snapshot(target.path)
    monkeypatch.setenv("PYRITE_CONFIG_DIR", str(env.tmp / "my-config"))

    result = target.run()

    assert result.exit_code == 0, result.output
    item = only_client(result)
    assert item["status"] == "unchanged"
    assert snapshot(target.path) == before
    note = next(n for n in item["notes"] if "PYRITE_CONFIG_DIR" in n)
    assert f'"PYRITE_CONFIG_DIR": "{env.tmp / "my-config"}"' in note
    assert "/their/own" not in result.output


def test_env_is_not_added_when_the_command_changes_either(env, monkeypatch):
    target = Target(env, "config")
    before = target.seed(member(cmd_text(OLD), args_text("mcp")))
    monkeypatch.setenv("PYRITE_DATA_DIR", str(env.tmp / "data"))

    result = target.run()

    assert result.exit_code == 0, result.output
    assert target.path.read_bytes() == before.replace(OLD.encode(), NEW.encode())
    assert any("PYRITE_DATA_DIR" in n for n in only_client(result)["notes"])


def test_force_writes_the_default_entry_pin_included(env, monkeypatch):
    target = Target(env, "config")
    target.seed(member(cmd_text(NEW), args_text("mcp", "--tier", "write")))
    monkeypatch.setenv("PYRITE_CONFIG_DIR", str(env.tmp / "my-config"))

    result = target.run("--force")

    assert result.exit_code == 0, result.output
    assert target.entry()["env"] == {"PYRITE_CONFIG_DIR": str(env.tmp / "my-config")}


def test_force_on_an_entry_already_the_default_writes_nothing(target):
    target.seed(target.client_written(NEW, "mcp", "--tier", "write"))
    before = snapshot(target.path)

    result = target.run("--force")

    assert result.exit_code == 0, result.output
    assert only_client(result)["status"] == "unchanged"
    assert snapshot(target.path) == before
    assert claude_calls(target.env) == []


def test_a_new_entry_never_pins_a_repo_local_config(env):
    """An explicit config dir is trusted (config.py resolve_config_source), so
    pinning a cloned tree's .pyrite/ would promote it."""
    (env.work / ".pyrite").mkdir()
    (env.work / ".pyrite" / "config.yaml").write_text("knowledge_bases: []\n")
    desktop = Target(env, "desktop")

    result = desktop.run()

    assert result.exit_code == 0, result.output
    assert desktop.entry().get("env", {}) == {}
    assert "~/.pyrite" in " ".join(report_of(result)["notes"])


# -- the output contract ---------------------------------------------------------------


def test_the_default_output_is_one_json_document(env):
    Target(env, "desktop")

    result = setup()

    report = json.loads(result.stdout)
    assert report["ok"] is True and report["new_entry"]["tier"] == "write"
    assert [c["status"] for c in report["clients"]] == ["created"]


def test_pyrite_format_asks_for_text_and_the_option_overrides_it(env, monkeypatch):
    Target(env, "desktop")
    monkeypatch.setenv("PYRITE_FORMAT", "rich")

    text = setup()
    as_json = setup("--format", "json")

    assert text.exit_code == 0, text.output
    assert "Claude Desktop" in text.output and "created" in text.output
    with pytest.raises(ValueError):
        json.loads(text.stdout)
    assert json.loads(as_json.stdout)["clients"][0]["status"] == "unchanged"


def test_text_report_reads_well_with_one_client_changed_and_one_stopped(env, monkeypatch):
    install_fake_claude(env)
    desktop = Target(env, "desktop")
    desktop.seed(member(*OLD_PYTHON_M))
    monkeypatch.setenv("PYRITE_FORMAT", "rich")

    result = setup()

    assert result.exit_code == 1, result.output
    lines = result.output.splitlines()
    code = next(i for i, line in enumerate(lines) if "Claude Code" in line)
    desk = next(i for i, line in enumerate(lines) if "Claude Desktop" in line)
    assert "created" in lines[code] and "stopped" in lines[desk]
    from tests.cli_help import plain

    assert "ENTRY_NOT_MCP" in result.output and "--force" in plain(result.output)


def test_text_report_gives_old_and_new_as_a_person_would_type_them(env, monkeypatch):
    target = Target(env, "config")
    target.seed(member(cmd_text(OLD), args_text("mcp", "--tier", "admin")))
    monkeypatch.setenv("PYRITE_FORMAT", "rich")

    result = target.run("--tier", "read")

    assert f"command: {OLD} -> {NEW}" in result.output
    assert "args: mcp --tier admin -> mcp --tier read" in result.output


@pytest.mark.parametrize(
    ("args", "tier", "named"),
    [(["mcp", "--tier", "read"], "read", True), (["mcp"], "write", False)],
    ids=["kept-read", "no-tier"],
)
def test_the_report_gives_the_tier_the_entry_serves_not_the_default(
    env, monkeypatch, args, tier, named
):
    """Without --tier an existing entry keeps its own tier; the report says
    which, and how many tools that is, in JSON and in text."""
    target = Target(env, "config")
    target.seed(member(cmd_text(OLD), args_text(*args)))

    item = only_client(target.run())

    assert (item["tier"], item["tier_named"]) == (tier, named), item
    assert item["tools"] == env.mod.mcp_tool_counts()[tier]  # what `pyrite mcp --help` counts
    target.seed(member(cmd_text(OLD), args_text(*args)))
    monkeypatch.setenv("PYRITE_FORMAT", "rich")
    text = target.run().output
    assert f"({tier} tier" in text and f"{item['tools']} tools)" in text


@pytest.mark.control(
    reason="passes on dev, where mcp-setup had no --format at all; guards that the "
    "local declaration never grows the -f that #303 retires"
)
def test_there_is_no_short_f_for_format(env):
    """`-f` stops meaning `--format` (#303); here it never did."""
    Target(env, "desktop")

    result = setup("-f", "json")

    assert result.exit_code == 2, result.output


@pytest.mark.parametrize(
    ("args", "says"),
    [
        (["--project", "--client", "claude-desktop"], "Claude Desktop has none"),
        (["--config", "x.json", "--client", "claude-code"], "does not combine"),
        (["--config", "x.json", "--project"], "does not combine"),
        (["--client", "cursor"], "claude-code, claude-desktop"),
        (["--format", "yamlish"], "unknown format"),
    ],
    ids=["project-desktop", "config-client", "config-project", "unknown-client", "unknown-format"],
)
def test_contradictory_or_unknown_options_are_usage_errors(env, args, says):
    install_fake_claude(env)

    result = setup(*args)

    assert result.exit_code == 2, result.output
    assert says in " ".join(result.output.split())
    assert claude_calls(env) == []
    assert not (env.work / "x.json").exists() and not (env.work / ".mcp.json").exists()


def test_no_client_found_exits_nonzero_with_the_manual_entry(env):
    result = setup()

    assert result.exit_code == 1, result.output
    report = report_of(result)
    assert report["error_code"] == "CLIENT_NOT_FOUND"
    assert report["manual"]["mcpServers"]["pyrite"]["args"] == ["mcp", "--tier", "write"]
    assert "claude mcp add" in report["manual_claude_code"]
    assert sorted(p.name for p in env.home.iterdir()) == []


def test_unknown_tier_is_invalid_tier_and_writes_nothing(env):
    install_fake_claude(env)
    desktop = Target(env, "desktop")

    result = setup("--tier", "root")

    assert result.exit_code == 1, result.output
    assert report_of(result)["error_code"] == "INVALID_TIER"
    assert not desktop.path.exists()
    assert claude_calls(env) == []


def test_named_client_that_is_not_installed_is_client_not_found(env):
    result = setup("--client", "claude-code")

    assert result.exit_code == 1, result.output
    report = report_of(result)
    assert report["error_code"] == "CLIENT_NOT_FOUND"
    assert "--project" in report["suggestion"]


def test_short_tier_flag_works_like_pyrite_mcp(env):
    desktop = Target(env, "desktop")

    result = setup("-t", "read")

    assert result.exit_code == 0, result.output
    assert_starts_pyrite_at(desktop.entry(), "read")


def test_help_states_the_rule(env):
    result = runner.invoke(app, ["mcp-setup", "--help"], env={"COLUMNS": "1000"})

    assert result.exit_code == 0, result.output
    assert "current directory" in result.output
    assert "discard" in result.output  # what --force means
    assert "-f," not in result.output


def test_project_writes_mcp_json_without_calling_claude(env):
    install_fake_claude(env)

    result = setup("--project")

    assert result.exit_code == 0, result.output
    assert_starts_pyrite_at(servers_in(env.work / ".mcp.json")["pyrite"], "write")
    assert claude_calls(env) == []
    assert not (env.home / ".claude.json").exists()


# -- each OS's Desktop path ----------------------------------------------------------


def test_macos_desktop_reads_back_the_write_tier(env, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    config = install_desktop(macos_desktop_config(env.home))

    result = setup()

    assert result.exit_code == 0, result.output
    assert_starts_pyrite_at(servers_in(config)["pyrite"], "write")
    assert only_client(result)["location"] == str(config)


@pytest.mark.parametrize("platform", ["darwin", "win32", "linux"])
def test_desktop_config_path_follows_each_os_rule(env, monkeypatch, platform):
    if platform == "darwin":
        config = macos_desktop_config(env.home)
    elif platform == "win32":
        appdata = env.tmp / "AppData" / "Roaming"
        monkeypatch.setenv("APPDATA", str(appdata))
        config = appdata / "Claude" / "claude_desktop_config.json"
    else:
        xdg = env.tmp / "xdg"
        monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
        config = xdg / "Claude" / "claude_desktop_config.json"
    install_desktop(config)
    monkeypatch.setattr(sys, "platform", platform)

    result = setup("--client", "claude-desktop")

    assert result.exit_code == 0, result.output
    assert_starts_pyrite_at(servers_in(config)["pyrite"], "write")


@pytest.mark.parametrize(
    ("platform", "unset", "expected"),
    [
        ("win32", "APPDATA", ("AppData", "Roaming", "Claude")),
        ("linux", "XDG_CONFIG_HOME", (".config", "Claude")),
    ],
)
def test_desktop_path_falls_back_to_the_home_default(env, monkeypatch, platform, unset, expected):
    """With APPDATA (Windows) or XDG_CONFIG_HOME (Linux) unset, Desktop's
    directory is the documented default under the home directory."""
    monkeypatch.delenv(unset)
    config = env.home.joinpath(*expected, "claude_desktop_config.json")
    install_desktop(config)
    monkeypatch.setattr(sys, "platform", platform)

    result = setup("--client", "claude-desktop")

    assert result.exit_code == 0, result.output
    assert_starts_pyrite_at(servers_in(config)["pyrite"], "write")


# -- the executable written ----------------------------------------------------------


def _fake_scripts_dir(monkeypatch, scripts: Path) -> None:
    """Point the interpreter's scripts directory (where pip puts `pyrite`)
    somewhere else, for every scheme."""
    real = sysconfig.get_path

    def get_path(name, *args, **kwargs):
        return str(scripts) if name == "scripts" else real(name, *args, **kwargs)

    monkeypatch.setattr(sysconfig, "get_path", get_path)


def test_command_that_cannot_start_writes_nothing(env, monkeypatch):
    scripts = env.tmp / "broken-install" / "bin"
    scripts.mkdir(parents=True)
    broken = scripts / "pyrite"
    broken.write_text("#!/bin/sh\necho 'ModuleNotFoundError: No module named mcp' >&2\nexit 1\n")
    broken.chmod(0o755)
    _fake_scripts_dir(monkeypatch, scripts)
    if env.mod is not None:
        monkeypatch.setattr(env.mod, "_verify_starts", env.real_verify)
    desktop = Target(env, "desktop")

    result = setup()

    assert result.exit_code == 1, result.output
    report = report_of(result)
    assert report["error_code"] == "MCP_COMMAND_FAILED"
    assert "No module named mcp" in report["error"]
    assert not desktop.path.exists()


def test_missing_pyrite_executable_is_a_clear_error(env, monkeypatch):
    empty = env.tmp / "no-scripts"
    empty.mkdir()
    _fake_scripts_dir(monkeypatch, empty)
    desktop = Target(env, "desktop")

    result = setup()

    assert result.exit_code == 1, result.output
    assert report_of(result)["error_code"] == "PYRITE_EXECUTABLE_NOT_FOUND"
    assert not desktop.path.exists()


def _fake_install(root: Path) -> Path:
    """A second install of the pyrite under test at `root`: a venv that imports
    it from REPO, the tree this test file is in (not whatever tree the
    editable install points at; verify-red runs this file in another tree),
    and chains to our site-packages for the dependencies, with a `pyrite`
    script written the way pip writes one for a path with spaces (a /bin/sh
    trampoline)."""
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(root)], check=True, timeout=120
    )
    ours = sysconfig.get_path("purelib")
    theirs = Path(
        subprocess.run(
            [
                str(root / "bin" / "python"),
                "-c",
                "import sysconfig; print(sysconfig.get_path('purelib'))",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        ).stdout.strip()
    )
    (theirs / "chain.pth").write_text(f"{REPO}\nimport site; site.addsitedir({ours!r})\n")
    script = root / "bin" / "pyrite"
    script.write_text(
        "#!/bin/sh\n"
        f'\'\'\'exec\' "{root / "bin" / "python"}" "$0" "$@"\n'
        "' '''\n"
        "import sys\n"
        "from pyrite.cli import main\n"
        "sys.exit(main())\n"
    )
    script.chmod(0o755)
    return script


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX venv layout")
def test_install_path_with_python_and_spaces_off_path_is_written_verbatim(env):
    """`~/Library/Python/3.12/bin` (pip --user) or a pyenv path contains
    "python"; a GUI client has no venv on PATH; a path may have spaces. The
    entry must still be the absolute path of the pyrite that ran. A real
    process, end to end: the start probe, the default JSON on a pipe, exit 0."""
    script = _fake_install(env.tmp / "My Apps" / "Python" / "3.12")
    # A real process sees the real platform, not the fixture's.
    where = macos_desktop_config if REAL_PLATFORM == "darwin" else linux_desktop_config
    desktop = install_desktop(where(env.home))
    run_env = {
        "HOME": str(env.home),
        "PATH": str(env.bin),
        "PYRITE_AUTO_EMBED": "0",
        "NO_COLOR": "1",
    }

    proc = subprocess.run(
        [str(script), "mcp-setup"],
        cwd=env.work,
        env=run_env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    entry = servers_in(desktop)["pyrite"]
    assert entry["command"] == str(script)
    assert_starts_pyrite_at(entry, "write")
    assert json.loads(proc.stdout)["clients"][0]["status"] == "created"


# -- pyrite-admin, and the other stdio entry points ---------------------------------


def test_pyrite_admin_mcp_setup_points_to_pyrite_mcp_setup():
    from pyrite.admin_cli import app as admin_app

    result = runner.invoke(admin_app, ["mcp-setup", "--tier", "read"])

    assert result.exit_code == 1, result.output
    assert "COMMAND_MOVED" in result.output
    assert "pyrite mcp-setup" in result.output


class _Recorder:
    started: list[str] = []

    def __init__(self, tier, **kwargs):
        type(self).started.append(tier)

    def run_stdio(self):
        pass

    def close(self):
        pass


@pytest.fixture
def started(monkeypatch):
    import pyrite.server.mcp_server as mcp_server

    _Recorder.started = []
    _Recorder.VALID_TIERS = mcp_server.PyriteMCPServer.VALID_TIERS
    monkeypatch.setattr(mcp_server, "PyriteMCPServer", _Recorder)
    return _Recorder.started


def test_pyrite_admin_mcp_defaults_to_the_write_tier(started):
    from pyrite.admin_cli import app as admin_app

    result = runner.invoke(admin_app, ["mcp"])

    assert result.exit_code == 0, result.output
    assert started == ["write"]


def test_pyrite_admin_mcp_unknown_tier_is_invalid_tier():
    from pyrite.admin_cli import app as admin_app

    result = runner.invoke(admin_app, ["mcp", "--tier", "root"])

    assert result.exit_code == 1, result.output
    assert "INVALID_TIER" in result.output


def test_mcp_server_module_entry_defaults_to_write(started, monkeypatch):
    """`python -m pyrite.server.mcp_server` is the third stdio entry point."""
    import pyrite.server.mcp_server as mcp_server

    monkeypatch.setattr(sys, "argv", ["pyrite.server.mcp_server"])

    mcp_server.main()

    assert started == ["write"]


def test_pyrite_mcp_package_serve_defaults_to_write(started, monkeypatch):
    """The separately published `pyrite-mcp serve` (pyrite-mcp/)."""
    monkeypatch.syspath_prepend(str(REPO / "pyrite-mcp"))
    sys.modules.pop("pyrite_mcp.__main__", None)
    import pyrite_mcp.__main__ as pyrite_mcp_main

    monkeypatch.setattr(sys, "argv", ["pyrite-mcp", "serve"])

    pyrite_mcp_main.main()

    assert started == ["write"]


# -- the docs are the acceptance test ------------------------------------------------


def _tutorial(tmp_path: Path, doc_text: str, **more_env: str) -> subprocess.CompletedProcess:
    doc = tmp_path / "doc.md"
    doc.write_text(doc_text)
    path = os.pathsep.join([str(Path(sys.executable).parent), "/usr/bin", "/bin"])
    return subprocess.run(
        [sys.executable, str(REPO / "scripts" / "run_tutorial.py"), str(doc)],
        # PYTHONPATH: the `pyrite` the tutorial runs is this tree's, not the
        # one the editable install points at (verify-red runs another tree).
        env={
            "PATH": path,
            "HOME": str(tmp_path),
            "NO_COLOR": "1",
            "PYTHONPATH": str(REPO),
            **more_env,
        },
        capture_output=True,
        text=True,
        timeout=300,
    )


@pytest.mark.control(
    reason="passes on dev, where mcp-setup always exits 0; guards that the "
    "tutorial still passes on a runner with no Claude client now that "
    "mcp-setup exits 1 when it finds none (the stub claude in run_tutorial.py)"
)
def test_tutorial_mcp_setup_block_passes_on_a_runner_with_no_client(tmp_path):
    proc = _tutorial(tmp_path, "```bash\npyrite mcp-setup\n```\n")

    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.control(
    reason="passes on dev, where the stub claude wrote nothing and mcp-setup took its "
    "exit 0 as success; guards that the stub now writes the temp HOME's file, never "
    "the caller's, once mcp-setup reads the file back (#690)"
)
def test_tutorial_stub_claude_never_touches_the_callers_claude_config(tmp_path):
    """A caller with CLAUDE_CONFIG_DIR set (a cloud session) runs the
    tutorial: its own Claude Code file is neither read nor written."""
    theirs = tmp_path / "their-claude"
    theirs.mkdir()
    config = theirs / ".claude.json"
    config.write_text('{"numStartups": 3}')
    before = snapshot(config)

    proc = _tutorial(tmp_path, "```bash\npyrite mcp-setup\n```\n", CLAUDE_CONFIG_DIR=str(theirs))

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert snapshot(config) == before


def test_tutorial_reads_back_what_was_asked_of_claude(tmp_path):
    """The tutorial's stub claude records each call, and the runner checks
    every `claude mcp add` it recorded: an absolute command that exists, and
    an explicit --tier. A doc that registers a bare `pyrite mcp` fails."""
    proc = _tutorial(tmp_path, "```bash\nclaude mcp add -s user pyrite -- pyrite mcp\n```\n")

    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "claude mcp add" in proc.stderr and "absolute" in proc.stderr


@pytest.mark.parametrize("doc", ["docs/getting-started.md", "scripts/run_tutorial.py"])
def test_docs_name_no_path_that_no_client_reads(doc):
    text = (REPO / doc).read_text()

    assert "~/.claude/claude_desktop_config.json" not in text


# -- the report is true of the file (#690) -------------------------------------------
#
# Every status and every `by_hand` line is true of the file as last read. The
# oracle below is the file as this test reads it after the run, and again
# after running each printed command against the fake `claude`: never a
# restatement of the branch the command took.

MODULE = REPO / "pyrite" / "cli" / "mcp_setup_command.py"
ABSENT, UNREADABLE = "no entry", "unreadable"
#: A `claude` call a person could copy out of a report.
CLAUDE_COMMAND = re.compile(r"claude mcp (add|remove|get|list)\b")
RESTORES, POINTS_HERE = "put the previous entry back", "point it at this install"


def exits_of(function: str) -> dict[str, tuple[int, int]]:
    """Every way out of `function` that reports an outcome, read from its
    source: each `raise` and each `_record(...)` call, nested functions
    included, as {name: (first line, last line)}. A `_StopError` is named for
    what it reports (`CODE status`); names are numbered in source order."""
    tree = ast.parse(MODULE.read_text())
    func = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == function)
    found = []
    for node in ast.walk(func):
        if isinstance(node, ast.Raise):
            exc = node.exc
            if exc is None:
                label = "re-raise"
            elif isinstance(exc, ast.Call) and ast.unparse(exc.func) == "_StopError":
                status = [ast.literal_eval(k.value) for k in exc.keywords if k.arg == "status"]
                label = f"{ast.literal_eval(exc.args[0])} {(status or ['stopped'])[0]}"
            else:
                label = "raise " + ast.unparse(exc.func if isinstance(exc, ast.Call) else exc)
        elif isinstance(node, ast.Call) and ast.unparse(node.func) == "_record":
            label = "_record"
        else:
            continue
        found.append((node.lineno, node.end_lineno, label))
    exits: dict[str, tuple[int, int]] = {}
    count: dict[str, int] = {}
    for first, last, label in sorted(found):
        count[label] = count.get(label, 0) + 1
        exits[f"{label} #{count[label]}"] = (first, last)
    return exits


@pytest.fixture
def exit_taken(env, monkeypatch):
    """Call it after a run: the name (as `exits_of` gives it) of the exit of
    `_setup_claude_code` (or the function named) the run left by, or
    `via <function>` when a function it calls stopped the run. Found by where each `_StopError` was built and
    `_record` was called, so a case cannot claim an exit it did not take."""
    mod = env.mod
    hits: list[tuple[str, int, str]] = []

    def here(code: str = "") -> None:
        frame = sys._getframe(2)
        hits.append((frame.f_code.co_name, frame.f_lineno, code))

    class Recorded(mod._StopError):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            here(self.error_code)

    real_record = mod._record

    def record(*args, **kwargs):
        here()
        return real_record(*args, **kwargs)

    monkeypatch.setattr(mod, "_StopError", Recorded)
    monkeypatch.setattr(mod, "_record", record)

    def taken(item: dict, function: str = "_setup_claude_code") -> str:
        exits = exits_of(function)
        inside = [name for _, line, _ in hits for name, (a, b) in exits.items() if a <= line <= b]
        if inside:
            return inside[-1]
        # Some helpers build a _StopError that a caller swallows; the one that
        # ended the run is the last with the code the report carries.
        outside = [name for name, _, code in hits if code == item.get("error_code")]
        return f"via {outside[-1]}" if outside else "no exit seen"

    return taken


def user_scope_entry(config: Path):
    """The user-scope `pyrite` entry as this test reads Claude Code's file:
    the entry, ABSENT, or UNREADABLE (what Claude Code would replace)."""
    try:
        data = json.loads(config.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ABSENT
    except (OSError, ValueError):
        return UNREADABLE
    if not isinstance(data, dict) or not isinstance(data.get("mcpServers", {}), dict):
        return UNREADABLE
    return data.get("mcpServers", {}).get("pyrite", ABSENT)


def shown(entry: dict) -> dict:
    """How a report shows an entry: env and unknown keys by name only."""
    if not isinstance(entry, dict):
        kind = {str: "a string", type(None): "null"}.get(type(entry), "a number")
        return {"value": kind}
    out = {"command": entry["command"], "args": entry["args"]}
    if entry.get("env"):
        out["env_keys"] = list(entry["env"])
    other = [k for k in entry if k not in ("command", "args", "env", "type")]
    if other:
        out["other_keys"] = other
    return out


def same_entry(a, b) -> bool:
    """Equal as a client reads them: `"type": "stdio"` and an empty env say nothing."""

    def plain(entry):
        if not isinstance(entry, dict):
            return entry
        return {k: v for k, v in entry.items() if (k, v) not in (("type", "stdio"), ("env", {}))}

    return plain(a) == plain(b)


def run_by_hand(env, commands: list[str], old) -> None:
    """Run each printed command, in order, as a person would once the cause
    is fixed: the fake `claude`, runnable, with nothing told to fail. An env
    value the report never prints (`KEY=<its value>`) is filled in from the
    entry that was there, as the suggestion says to."""
    fake = install_fake_claude(env)
    clean = {k: v for k, v in os.environ.items() if not k.startswith("FAKE_CLAUDE_")}
    for command in commands:
        argv = shlex.split(command)
        assert argv[:2] == ["claude", "mcp"], f"not a command: {command}"
        for i, arg in enumerate(argv):
            if arg.endswith("=<its value>"):
                key = arg.removesuffix("=<its value>")
                argv[i] = f"{key}={old['env'][key]}"
        proc = subprocess.run(
            [str(fake), *argv[1:]], env=clean, capture_output=True, text=True, timeout=60
        )
        assert proc.returncode == 0, (
            f"`{command}` fails on the state the report describes: {proc.stderr}"
        )


PLAIN = ('"type": "stdio"', cmd_text(OLD), args_text("mcp", "--tier", "admin"), '"env": {}')
HERE = ('"type": "stdio"', cmd_text(NEW), args_text("mcp", "--tier", "write"), '"env": {}')
WITH_ENV = (
    cmd_text(OLD),
    args_text("mcp", "--tier", "admin"),
    '"env": {\n        "OPENAI_API_KEY": "' + SECRET + '"\n      }',
)
ANOTHER = {"type": "stdio", "command": "/third/bin/pyrite", "args": ["mcp"], "env": {}}
ANOTHER_FILE = json.dumps({"mcpServers": {"pyrite": ANOTHER}}, indent=2).encode()


@dataclass
class Case:
    """One run of `mcp-setup --client claude-code` and what must be true after it."""

    exit: str  # the exit of _setup_claude_code it must leave by (`exits_of`)
    status: str
    file: str  # what the file holds afterwards: old | new | other | ABSENT | UNREADABLE
    seed: object = PLAIN  # the entry's fields; None: no entry; bytes: the whole file
    args: tuple = ()
    stub: dict = field(default_factory=dict)  # what the fake `claude` is told
    by_hand: int = 0  # how many commands the report gives
    #: (n, bytes): another writer replaces the file just before the n-th read.
    writer: tuple | None = None
    probe: bool = False  # run the real start check
    claude: str = "fake"  # or "no-interpreter": installed, and cannot be run
    locked: bool = False  # the file's mode is 000 when the command runs
    #: This exit was true of the file before #690 (safe by reading): the case
    #: passes on dev, and is here so the exit stays true and stays enumerated.
    was_true: bool = False
    calls: list | None = None  # the `claude mcp` verbs the run may make
    more: str = ""  # members seeded beside the entry


#: `pyrite` entries that are not objects, as whole files.
NOT_AN_OBJECT = {
    kind: json.dumps({"mcpServers": {"pyrite": value}}, indent=2).encode()
    for kind, value in (("a string", "not-an-entry"), ("null", None))
}
#: What a `pyrite` entry looks like once this run's change is in: the old
#: entry's tier and the path of this install.
THE_NEW_ENTRY_FILE = json.dumps(
    {"mcpServers": {"pyrite": {**OLD_ENTRY, "command": NEW}}}, indent=2
).encode()

S1, S2, S3 = (f"CLIENT_COMMAND_FAILED stopped #{n}" for n in (1, 2, 3))
F_UNREADABLE, F_OTHER, F_ABSENT = (f"CLIENT_COMMAND_FAILED failed #{n}" for n in (1, 2, 3))
HAND_EDIT = "CLIENT_ENTRY_NEEDS_HAND_EDIT stopped #1"
FORCE = ("--force",)

CLAUDE_CODE_CASES = {
    # Regimes of the existing entry: absent | reproducible | with env | with
    # unknown keys | a third entry in place; the read-back unreadable;
    # --force; `claude` that cannot be run, times out, exits 0 without writing.
    "equal": Case("_record #1", "unchanged", "new", seed=HERE, was_true=True),
    "env-and-the-path-must-change": Case(HAND_EDIT, "stopped", "old", seed=WITH_ENV, by_hand=2),
    "env-args-and-unknown-keys": Case(HAND_EDIT, "stopped", "old", seed=CARRIES_EXTRA, by_hand=2),
    "an-argument-no-command-line-can-carry": Case(
        HAND_EDIT, "stopped", "old", seed=(cmd_text(OLD), args_text("mcp", "a\\u0000b")), calls=[]
    ),
    "entry-moved-before-any-call": Case(
        "CONFIG_CHANGED stopped #1",
        "stopped",
        "other",
        writer=(2, ANOTHER_FILE),
        calls=[],
        was_true=True,
    ),
    "absent": Case("_record #2", "created", "new", seed=None, was_true=True),
    "absent, add-exits-1-having-written": Case(
        "_record #2", "created", "new", seed=None, stub={"FAKE_CLAUDE_LIE": "add"}, was_true=True
    ),
    "absent, add-fails": Case(
        S1, "stopped", ABSENT, seed=None, stub={"FAKE_CLAUDE_FAIL": "add"}, by_hand=1
    ),
    "absent, add-exits-0-without-writing": Case(
        S1, "stopped", ABSENT, seed=None, stub={"FAKE_CLAUDE_NOOP": "add"}, by_hand=1
    ),
    "absent, claude-cannot-be-run": Case(
        S1, "stopped", ABSENT, seed=None, claude="no-interpreter", by_hand=1
    ),
    "absent, add-times-out": Case(
        S1, "stopped", ABSENT, seed=None, stub={"FAKE_CLAUDE_HANG": "add"}, by_hand=1
    ),
    "absent, add-leaves-the-file-unreadable": Case(
        F_UNREADABLE, "failed", UNREADABLE, seed=None, stub={"FAKE_CLAUDE_CORRUPT": "add"}
    ),
    "absent, add-writes-another-entry": Case(
        F_OTHER, "failed", "other", seed=None, stub={"FAKE_CLAUDE_OTHER": "add"}, calls=["add"]
    ),
    "absent, add-writes-another-entry-and-exits-1": Case(
        F_OTHER,
        "failed",
        "other",
        seed=None,
        stub={"FAKE_CLAUDE_OTHER": "add", "FAKE_CLAUDE_LIE": "add"},
    ),
    "differs": Case("_record #3", "changed", "new", was_true=True),
    "differs, add-exits-1-having-written": Case(
        "_record #3", "changed", "new", stub={"FAKE_CLAUDE_LIE": "add"}, was_true=True
    ),
    "force-over-env": Case(
        "_record #3", "changed", "new", seed=CARRIES_EXTRA, args=FORCE, was_true=True
    ),
    "remove-fails": Case(S2, "stopped", "old", stub={"FAKE_CLAUDE_FAIL": "remove"}, by_hand=2),
    "remove-and-add-exit-0-without-writing": Case(
        S2,
        "stopped",
        "old",
        stub={"FAKE_CLAUDE_NOOP": "remove,add"},
        by_hand=2,
        calls=["remove", "add"],
    ),
    "force-over-env, remove-fails": Case(
        S2,
        "stopped",
        "old",
        seed=CARRIES_EXTRA,
        args=FORCE,
        stub={"FAKE_CLAUDE_FAIL": "remove"},
        by_hand=2,
    ),
    "add-fails-and-the-entry-is-restored": Case(
        S3, "stopped", "old", stub={"FAKE_CLAUDE_FAIL": "add#1"}, by_hand=2
    ),
    "add-fails-and-so-does-the-restore": Case(
        F_ABSENT, "failed", ABSENT, stub={"FAKE_CLAUDE_FAIL": "add"}, by_hand=1
    ),
    "add-fails-and-the-restore-exits-0-without-writing": Case(
        F_ABSENT,
        "failed",
        ABSENT,
        stub={"FAKE_CLAUDE_FAIL": "add#1", "FAKE_CLAUDE_NOOP": "add#2"},
        by_hand=1,
    ),
    "add-exits-0-without-writing-after-remove": Case(
        F_ABSENT,
        "failed",
        ABSENT,
        stub={"FAKE_CLAUDE_NOOP": "add"},
        by_hand=1,
        calls=["remove", "add"],  # an exit 0 is not followed by another `claude` call
    ),
    "claude-stops-being-runnable-after-remove": Case(
        F_ABSENT, "failed", ABSENT, stub={"FAKE_CLAUDE_VANISH": "remove"}, by_hand=1
    ),
    "force-over-env, add-fails": Case(
        F_ABSENT,
        "failed",
        ABSENT,
        seed=CARRIES_EXTRA,
        args=FORCE,
        stub={"FAKE_CLAUDE_FAIL": "add"},
        calls=["remove", "add"],
    ),
    "add-writes-another-entry-after-remove": Case(
        F_OTHER,
        "failed",
        "other",
        stub={"FAKE_CLAUDE_OTHER": "add"},
        by_hand=2,
        calls=["remove", "add"],
    ),
    "add-writes-another-entry-and-exits-1-after-remove": Case(
        F_OTHER,
        "failed",
        "other",
        stub={"FAKE_CLAUDE_OTHER": "add", "FAKE_CLAUDE_LIE": "add"},
        by_hand=2,
        calls=["remove", "add"],
    ),
    "force-over-env, add-writes-another-entry": Case(
        F_OTHER,
        "failed",
        "other",
        seed=CARRIES_EXTRA,
        args=FORCE,
        stub={"FAKE_CLAUDE_OTHER": "add"},
    ),
    "remove-leaves-the-file-unreadable": Case(
        F_UNREADABLE,
        "failed",
        UNREADABLE,
        stub={"FAKE_CLAUDE_CORRUPT": "remove"},
        calls=["remove"],
    ),
    "add-leaves-the-file-unreadable": Case(
        F_UNREADABLE,
        "failed",
        UNREADABLE,
        stub={"FAKE_CLAUDE_CORRUPT": "add"},
        calls=["remove", "add"],
    ),
    "old-trio-beside-it, add-leaves-the-file-unreadable": Case(
        F_UNREADABLE, "failed", UNREADABLE, stub={"FAKE_CLAUDE_CORRUPT": "add"}, more=OLD_TRIO
    ),
    # An entry that is not an object (a hand edit, a broken tool): `--force`
    # discards it, and the report must still tell "still there" from "gone".
    "force-over-a-string-entry, remove-fails": Case(
        S2,
        "stopped",
        "old",
        seed=NOT_AN_OBJECT["a string"],
        args=FORCE,
        stub={"FAKE_CLAUDE_FAIL": "remove"},
        by_hand=2,
        calls=["remove"],
    ),
    "force-over-a-null-entry, remove-fails": Case(
        S2,
        "stopped",
        "old",
        seed=NOT_AN_OBJECT["null"],
        args=FORCE,
        stub={"FAKE_CLAUDE_FAIL": "remove"},
        by_hand=2,
        calls=["remove"],
    ),
    "force-over-a-string-entry, add-fails": Case(
        F_ABSENT,
        "failed",
        ABSENT,
        seed=NOT_AN_OBJECT["a string"],
        args=FORCE,
        stub={"FAKE_CLAUDE_FAIL": "add"},
        calls=["remove", "add"],
    ),
    "force-over-a-string-entry": Case(
        "_record #3",
        "changed",
        "new",
        seed=NOT_AN_OBJECT["a string"],
        args=FORCE,
        was_true=True,
    ),
    # The NUL is in the part this run replaces (the command), so nothing it
    # keeps is lost: not a hand edit. (A NUL in a kept argument still is.)
    "a-command-no-command-line-can-carry-is-being-replaced": Case(
        "_record #3",
        "changed",
        "new",
        seed=('"command": "/old/\\u0000bin/pyrite"', args_text("mcp", "--tier", "admin")),
        calls=["remove", "add"],
        was_true=True,  # a plain change on dev; this branch's first commit refused it
    ),
    # Another writer put the new entry there while the restore ran: the
    # report says what the last read saw, not "another entry".
    "add-fails, the restore runs, and the last read holds the new entry": Case(
        "_record #3",
        "changed",
        "new",
        stub={"FAKE_CLAUDE_FAIL": "add#1"},
        writer=(4, THE_NEW_ENTRY_FILE),
    ),
    # Exits of the functions it calls: nothing was changed.
    "file-empty": Case(
        "via _read_claude_config", "stopped", UNREADABLE, seed=b"", calls=[], was_true=True
    ),
    "file-mode-000": Case(
        "via _read_claude_config", "stopped", UNREADABLE, locked=True, calls=[], was_true=True
    ),
    "file-unreadable-at-the-re-read": Case(
        "via _read_claude_config", "stopped", UNREADABLE, writer=(2, b""), calls=[], was_true=True
    ),
    "args-do-not-begin-with-mcp": Case(
        "via _read_entry", "stopped", "old", seed=OLD_PYTHON_M, was_true=True
    ),
    "entry-is-not-a-server": Case(
        "via _command_and_args", "stopped", "old", seed=('"command": 7',), was_true=True
    ),
    "arguments-this-install-rejects": Case(
        "via _verify_starts",
        "stopped",
        "old",
        seed=(cmd_text(OLD), args_text("mcp", "--tier", "admin", "bogus")),
        probe=True,
        calls=[],
    ),
}


def _break(mod, monkeypatch, name: str, exc: Exception) -> None:
    def broken(*args, **kwargs):
        raise exc

    monkeypatch.setattr(mod, name, broken)


def _move_the_file_before_the_replace(mod, monkeypatch, path: Path) -> None:
    real = mod._check_replaceable

    def moved(real_path):
        real(real_path)
        path.write_text('{"mcpServers": {}, "another": "writer"}')

    monkeypatch.setattr(mod, "_check_replaceable", moved)


#: The exits of `_setup_file` (Desktop, --project, --config), which #690 did
#: not change: {exit: (the `pyrite` member there before or the whole file,
#: what to break, status, error code)}. None prints a `claude` command, and a
#: write is `atomic_write_text`, which raises or has replaced the file.
FILE_EXITS = {
    "_record #1": (member(cmd_text(NEW), args_text("mcp", "--tier", "write")), None, "unchanged", None),
    "CONFIG_INVALID stopped #1": ("", ("_serialize", RecursionError()), "stopped", "CONFIG_INVALID"),
    "raise _not_preserved #1": ("", ("_serialize", ValueError("inf")), "stopped", "CONFIG_NOT_PRESERVED"),
    "CONFIG_CHANGED stopped #1": ("", "another writer", "stopped", "CONFIG_CHANGED"),
    "re-raise #1": ("", ("atomic_write_text", RuntimeError("a bug")), "stopped", "INTERNAL_ERROR"),
    "CONFIG_WRITE_FAILED stopped #1": ("", ("atomic_write_text", OSError("disk full")), "stopped", "CONFIG_WRITE_FAILED"),
    "_record #2": ("", None, "created", None),
}  # fmt: skip


@pytest.mark.control(
    reason="_setup_file is not changed by #690: its exits were already true of the file. "
    "This is the classification the groom asked for, kept as a test so a new exit of "
    "_setup_file needs a case too (test_every_exit_of_the_two_setup_functions_has_a_case)"
)
@pytest.mark.parametrize("name", FILE_EXITS)
def test_every_exit_of_setup_file_leaves_the_file_whole_and_names_no_claude_command(
    env, monkeypatch, exit_taken, name
):
    seed, breakage, status, code = FILE_EXITS[name]
    target = Target(env, "config")
    if isinstance(seed, bytes):
        target.path.write_bytes(seed)
    else:
        target.seed(seed)
    before = target.path.read_bytes()
    if breakage == "another writer":
        _move_the_file_before_the_replace(env.mod, monkeypatch, target.path)
    elif breakage:
        _break(env.mod, monkeypatch, *breakage)

    result = target.run()

    item = only_client(result)
    assert (item["status"], item.get("error_code")) == (status, code), item
    # Where the _StopError is built: at the exit, in the helper the exit
    # raises the result of, or (a bare `raise`) nowhere: the caller's
    # catch-all reports that one.
    seen = {"re-raise #1": "no exit seen", "raise _not_preserved #1": "via _not_preserved"}
    assert exit_taken(item, "_setup_file") == seen.get(name, name)
    assert not CLAUDE_COMMAND.search(json.dumps(item)), item
    if status == "created":
        assert_starts_pyrite_at(target.entry(), "write")
    elif breakage != "another writer":
        assert target.path.read_bytes() == before


def test_every_exit_of_the_two_setup_functions_has_a_case():
    """The list of exits is the source's, not this file's: add a `raise` or a
    `_record` to either function and this fails until a case leaves by it."""
    covered = {case.exit for case in CLAUDE_CODE_CASES.values() if not case.exit.startswith("via ")}

    assert set(exits_of("_setup_claude_code")) == covered
    assert set(exits_of("_setup_file")) == set(FILE_EXITS)


_WAS_TRUE = pytest.mark.control(
    reason="passes on dev: this exit of _setup_claude_code was already true of the file "
    "(classified safe by reading on #690); the case keeps it true and enumerated"
)


@pytest.mark.parametrize(
    "name",
    [
        pytest.param(name, marks=_WAS_TRUE) if case.was_true else name
        for name, case in CLAUDE_CODE_CASES.items()
    ],
)
def test_the_report_is_true_of_the_file_and_every_printed_command_works(
    env, monkeypatch, exit_taken, name
):
    """Acceptance 1 and 3 of #690, for every exit of `_setup_claude_code`:
    the status and the entries the report shows are what the file holds; each
    `by_hand` command, run in order on that state, leaves the file as the
    suggestion says; and a file that cannot be read gets no `claude` command."""
    case = CLAUDE_CODE_CASES[name]
    target = Target(env, "claude-code")
    config = target.path
    if isinstance(case.seed, bytes):
        config.write_bytes(case.seed)
    else:
        target.seed(case.more + (member(*case.seed) if case.seed else ""))
    if case.claude == "no-interpreter":
        (env.bin / "claude").write_text("#!/nonexistent/python\n")
    old = user_scope_entry(config)
    if case.locked:
        if os.name != "posix" or os.geteuid() == 0:
            pytest.skip("needs mode bits to bind")
        config.chmod(0)
    for var, value in case.stub.items():
        monkeypatch.setenv(var, value)
    if "FAKE_CLAUDE_HANG" in case.stub:
        monkeypatch.setattr(env.mod, "_SUBPROCESS_TIMEOUT", 8)
    if case.probe:
        monkeypatch.setattr(env.mod, "_verify_starts", env.real_verify)
    if case.writer:
        nth, content = case.writer
        real, reads = env.mod._read_claude_config, []

        def read(path):
            reads.append(path)
            if len(reads) == nth:
                config.write_bytes(content)
            return real(path)

        monkeypatch.setattr(env.mod, "_read_claude_config", read)

    result = target.run(*case.args)

    item = only_client(result)
    assert exit_taken(item) == case.exit, item
    assert item["status"] == case.status, item
    assert result.exit_code == (0 if case.status in ("created", "unchanged", "changed") else 1)
    assert SECRET not in result.output
    if case.calls is not None:
        assert [call[1] for call in claude_calls(env)] == case.calls
    now = user_scope_entry(config)
    if case.locked:
        config.chmod(0o600)
    as_it_was = same_entry(now, old)
    if now in (ABSENT, UNREADABLE):
        state = now
    else:
        is_new = isinstance(now, dict) and now.get("command") == NEW
        state = "new" if is_new else "old" if as_it_was else "other"
    assert state == case.file, (now, item)

    suggestion = item.get("suggestion", "")
    by_hand = item.get("by_hand", [])
    if now is UNREADABLE:
        # Claude Code replaces a file it cannot read: no command that runs it.
        said = [suggestion, *by_hand, *item["notes"]]
        assert not [text for text in said if CLAUDE_COMMAND.search(text)], item
        assert "by_hand" not in item and "entry" not in item, item
        return
    if case.status in ("created", "changed"):
        assert item["entry"] == shown(now), item
    elif case.status == "failed":
        assert not as_it_was, item
        assert item.get("removed") == (None if old is ABSENT else shown(old)), item
        assert item.get("entry") == (None if now is ABSENT else shown(now)), item
    else:  # unchanged, stopped: this run changed nothing
        assert as_it_was or case.writer, item
        if "entry" in item:
            assert item["entry"] == shown(now), item

    assert len(by_hand) == case.by_hand, item
    restores, points_here = RESTORES in suggestion, POINTS_HERE in suggestion
    if not by_hand:
        assert "by_hand" not in item and "by_hand" not in suggestion, item
        if "to add this install's entry" in suggestion:
            for var in case.stub:
                monkeypatch.delenv(var)
            again = only_client(setup("--client", "claude-code"))
            assert again["status"] == "created", again
        return
    assert "by_hand" in suggestion and restores != points_here, suggestion
    if "<its value>" in " ".join(by_hand):
        # Run as printed it would store the placeholder: the report says so.
        assert "replace each `<its value>`" in suggestion, suggestion
    run_by_hand(env, by_hand, old)
    after = user_scope_entry(config)
    if restores:
        assert same_entry(after, old), (after, item)
    elif case.args == FORCE or old is ABSENT:
        assert same_entry(after, {"command": NEW, "args": ["mcp", "--tier", "write"]}), after
    else:
        # The path moved; what the entry carried is there, or the suggestion
        # names each key `claude mcp add` could not write back.
        writable = {k: v for k, v in old.items() if k in ("command", "args", "env", "type")}
        assert same_entry(after, {**writable, "command": NEW}), (after, item)
        assert all(key in suggestion for key in old if key not in writable), suggestion


def test_a_bug_after_a_claude_call_names_no_claude_command(env, monkeypatch):
    """The catch-all report for one client knows nothing of the file, so it
    sends the user to the one command that reads it before deciding."""
    install_fake_claude(env)

    def bug(*args, **kwargs):
        raise RuntimeError("a bug nobody predicted")

    monkeypatch.setattr(env.mod, "_record", bug)

    result = setup("--client", "claude-code")

    item = only_client(result)
    assert (item["status"], item["error_code"]) == ("failed", "INTERNAL_ERROR"), item
    assert not CLAUDE_COMMAND.search(item["suggestion"]), item
    assert "re-run `pyrite mcp-setup`" in item["suggestion"]


def test_the_text_report_prints_each_by_hand_command_and_the_entry_in_the_way(env, monkeypatch):
    target = _seed_changeable(env)
    monkeypatch.setenv("FAKE_CLAUDE_OTHER", "add")

    result = target.run("--format", "rich")

    assert result.exit_code == 1, result.output
    lines = [line.strip() for line in result.output.splitlines()]
    assert "run: claude mcp remove -s user pyrite" in lines
    assert f"run: claude mcp add -s user pyrite -- {OLD} mcp --tier admin" in lines
    assert lines.index("run: claude mcp remove -s user pyrite") < lines.index(
        f"run: claude mcp add -s user pyrite -- {OLD} mcp --tier admin"
    )
    assert any(line.startswith("entry:") and "/third/bin/pyrite" in line for line in lines)


# -- the start check: this install's `pyrite mcp`, with its own parser ---------------


@pytest.mark.parametrize(
    "args",
    [
        ("mcp", "--tier", "admin", "bogus"),
        ("mcp", "--tier", "nonsense"),
        ("mcp", "--tier", "admin", "--help"),
    ],
    ids=["stray-positional", "invalid-tier", "help-instead-of-a-server"],
)
@pytest.mark.parametrize("kind", ["config", "claude-code"])
def test_an_entry_this_installs_pyrite_mcp_would_not_start_is_not_written(
    env, monkeypatch, kind, args
):
    """Acceptance 2 of #690. `pyrite mcp <args> --help` exits 0 for all three:
    click prints help before it rejects a leftover argument, and the tier is
    checked in the command body. A client starting any of them gets no server."""
    monkeypatch.setattr(env.mod, "_verify_starts", env.real_verify)
    target = Target(env, kind)
    target.seed(target.client_written(OLD, *args))
    before = snapshot(target.path)

    result = target.run()

    assert result.exit_code == 1, result.output
    item = only_client(result)
    assert (item["status"], item["error_code"]) == ("stopped", "ENTRY_WOULD_NOT_START"), item
    assert shlex.join(args) in item["error"] and "--force" in item["suggestion"]
    assert snapshot(target.path) == before
    assert claude_calls(env) == []


def test_pyrite_mcp_check_reads_its_arguments_and_starts_nothing(started):
    result = runner.invoke(app, ["mcp", "--tier", "admin", "--check"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "pyrite mcp: arguments ok (tier admin)"
    assert started == []


_CLICK_REJECTS = pytest.mark.control(
    reason="passes on dev, where --check is itself an unknown option (exit 2): click "
    "rejects these arguments with or without it. Guards that --check does not relax that"
)


@pytest.mark.parametrize(
    ("args", "code"),
    [
        (["--tier", "nonsense"], 1),
        pytest.param(["--tier", "admin", "bogus"], 2, marks=_CLICK_REJECTS),
        pytest.param(["--no-such-option"], 2, marks=_CLICK_REJECTS),
    ],
)
def test_pyrite_mcp_check_fails_where_starting_would(started, args, code):
    result = runner.invoke(app, ["mcp", *args, "--check"])

    assert result.exit_code == code, result.output
    assert "arguments ok" not in result.output
    assert started == []


@pytest.mark.control(
    reason="passes on dev, where there is no --check: guards that adding it does not "
    "add to the documented CLI surface"
)
def test_pyrite_mcp_check_is_not_a_documented_option():
    """It exists for mcp-setup's start check, not as part of the CLI surface.
    The help is compared as plain text: Rich can put styling between the two
    dashes, and then a raw `not in` would pass whatever the help said."""
    from tests.cli_help import plain

    shown = plain(runner.invoke(app, ["mcp", "--help"]).output)

    assert "--tier" in shown, shown
    assert "--check" not in shown


# -- a `pyrite` entry in another Claude Code scope ----------------------------------

LOCAL = {"type": "stdio", "command": OLD, "args": ["mcp", "--tier", "read"], "env": {"K": SECRET}}
AS_ADDED = {"type": "stdio", "command": NEW, "args": ["mcp", "--tier", "write"], "env": {}}


def seed_claude_json(env, **keys) -> Path:
    """`~/.claude.json` in the client's style, holding `keys` and startup state."""
    config = env.home / ".claude.json"
    data = {"numStartups": 7, **keys}
    config.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return config


@pytest.mark.parametrize("where", ["this-directory", "another-directory"])
def test_a_local_scope_entry_that_differs_is_named_and_never_changed(env, where):
    """Acceptance 4 of #690. A bare `claude mcp add` (the README's wording
    until this change) writes `projects[<dir>].mcpServers`, Claude Code's
    local scope, and in that directory it wins over the user-scope entry
    (`test_real_claude_code_prefers_the_local_scope_entry`). The run still
    says `created`, which is true of the user scope; the report now says what
    that project will start instead."""
    install_fake_claude(env)
    directory = os.path.realpath(env.work) if where == "this-directory" else "/srv/elsewhere"
    projects = {directory: {"allowedTools": [], "mcpServers": {"pyrite": LOCAL}}}
    config = seed_claude_json(env, projects=projects)

    result = setup("--client", "claude-code")

    assert result.exit_code == 0, result.output
    item = only_client(result)
    assert item["status"] == "created", item
    assert item["other_scopes"] == [
        {
            "scope": "local",
            "directory": directory,
            "entry": {"command": OLD, "args": ["mcp", "--tier", "read"], "env_keys": ["K"]},
        }
    ]
    (note,) = [n for n in item["notes"] if "local scope" in n]
    assert directory in note and "takes precedence" in note and OLD in note
    assert ("this directory" in note) == (where == "this-directory")
    assert SECRET not in result.output
    assert json.loads(config.read_text())["projects"] == projects
    assert [call[:4] for call in claude_calls(env)] == [["mcp", "add", "-s", "user"]]


@pytest.mark.control(
    reason="passes on dev, which reports no other scope at all: guards that the new notes "
    "appear only for an entry that differs as a client reads it, and that no shape of "
    '"projects" breaks the run'
)
@pytest.mark.parametrize(
    "keys",
    [
        {},
        {"projects": {}},
        {"projects": {"/srv/a": {"allowedTools": []}}},
        {"projects": {"/srv/a": {"mcpServers": {"filesystem": {"command": "npx"}}}}},
        {"projects": {"/srv/a": {"mcpServers": {"pyrite": AS_ADDED}}}},
        {"projects": {"/srv/a": {"mcpServers": {"pyrite": {"command": NEW, "args": AS_ADDED["args"]}}}}},
        {"projects": []},
        {"projects": {"/srv/a": None, "/srv/b": {"mcpServers": []}}},
    ],
    ids=[
        "no-projects-key", "no-projects", "project-without-servers", "another-server-only",
        "an-equal-entry", "equal-as-a-client-reads-it", "projects-not-an-object", "odd-shapes",
    ],
)  # fmt: skip
def test_no_other_scope_is_reported_when_none_differs(env, keys):
    install_fake_claude(env)
    seed_claude_json(env, **keys)

    result = setup("--client", "claude-code")

    assert result.exit_code == 0, result.output
    item = only_client(result)
    assert item["status"] == "created" and "other_scopes" not in item, item
    assert item["notes"] == []


def test_a_differing_entry_in_this_directorys_mcp_json_is_named(env):
    """`./.mcp.json` is Claude Code's project scope. It is read, never
    written, when the user scope is the target; one that is not valid JSON is
    not this run's business."""
    install_fake_claude(env)
    seed_claude_json(env, mcpServers={"pyrite": AS_ADDED})
    project = env.work / ".mcp.json"
    project.write_text('{"mcpServers": {"pyrite": {"command": "pyrite", "args": ["mcp"]}}}')
    before = snapshot(project)

    result = setup("--client", "claude-code")

    assert result.exit_code == 0, result.output
    item = only_client(result)
    assert item["status"] == "unchanged", item
    assert item["other_scopes"] == [
        {
            "scope": "project",
            "location": str(project),
            "entry": {"command": "pyrite", "args": ["mcp"]},
        }
    ]
    (note,) = item["notes"]
    assert str(project) in note and "may take precedence" in note and "--project" in note
    assert snapshot(project) == before

    project.write_text("{ not json")
    again = only_client(setup("--client", "claude-code"))
    assert again["status"] == "unchanged" and "other_scopes" not in again, again


def test_other_scopes_are_compared_with_the_entry_the_file_holds_at_the_end(env, monkeypatch):
    """A local-scope entry equal to the old user-scope entry differs once the
    user scope is changed, and one equal to the new entry does not."""
    install_fake_claude(env)
    was = {"type": "stdio", "command": OLD, "args": ["mcp", "--tier", "write"], "env": {}}
    projects = {
        "/srv/a": {"mcpServers": {"pyrite": was}},
        "/srv/b": {"mcpServers": {"pyrite": AS_ADDED}},
    }
    seed_claude_json(env, mcpServers={"pyrite": was}, projects=projects)

    item = only_client(setup("--client", "claude-code"))

    assert item["status"] == "changed", item
    assert [other["directory"] for other in item["other_scopes"]] == ["/srv/a"]


@pytest.mark.skipif(REAL_CLAUDE is None, reason="Claude Code is not installed")
@pytest.mark.control(
    reason="a fact about Claude Code, not about Pyrite: it holds on any branch. It is "
    "what lets the note say a local-scope entry takes precedence"
)
def test_real_claude_code_prefers_the_local_scope_entry(env, monkeypatch):
    """Observed with Claude Code 2.1.296 in a temp HOME: `claude mcp get`
    reports the entry under `projects[<cwd>].mcpServers` in that directory,
    and the user-scope entry elsewhere. An unapproved `./.mcp.json` entry did
    not displace the user-scope one, so that note says only "may"."""
    # As test_real_claude_code_reads_back_the_entry: `claude` may be a
    # `#!/usr/bin/env node` shim, which needs its own directory on PATH.
    monkeypatch.setenv("PATH", f"{env.bin}{os.pathsep}{Path(REAL_CLAUDE).parent}")
    user = {"type": "stdio", "command": "/usr/bin/true", "args": ["USER-SCOPE"], "env": {}}
    local = {"type": "stdio", "command": "/usr/bin/true", "args": ["LOCAL-SCOPE"], "env": {}}
    here = os.path.realpath(env.work)
    seed_claude_json(
        env, mcpServers={"pyrite": user}, projects={here: {"mcpServers": {"pyrite": local}}}
    )
    elsewhere = env.tmp / "elsewhere-project"
    elsewhere.mkdir()

    def get(cwd) -> str:
        return subprocess.run(
            [REAL_CLAUDE, "mcp", "get", "pyrite"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=120,
        ).stdout

    assert "LOCAL-SCOPE" in get(here) and "USER-SCOPE" not in get(here)
    assert "USER-SCOPE" in get(elsewhere)


# -- docs/getting-started.md shows a failed report -----------------------------------

GETTING_STARTED = REPO / "docs" / "getting-started.md"


def test_the_failed_report_in_getting_started_is_what_a_failed_run_prints(env, monkeypatch):
    """Acceptance 5 of #690. The page shows what a `failed` client looks like;
    this is that run, with the page's stand-ins for this machine's paths and
    for `claude`'s own words. Following the page (run `by_hand`) puts the
    previous entry back."""
    text = GETTING_STARTED.read_text()
    marker = "<!-- mcp-setup: a failed client -->\n```json\n"
    assert marker in text, "the failed-report example is missing from getting-started.md"
    documented = json.loads(text.split(marker, 1)[1].split("\n```", 1)[0])
    install_fake_claude(env)
    config = seed_claude_json(env, mcpServers={"pyrite": OLD_ENTRY})
    monkeypatch.setenv("FAKE_CLAUDE_FAIL", "add")

    result = setup("--client", "claude-code")

    item = only_client(result)
    printed = json.dumps(item)
    for real, stand_in in (
        (NEW, "/home/you/.venv/bin/pyrite"),
        (str(env.home), "/home/you"),
        ("fake claude: E_REFUSED add", "<what claude printed>"),
    ):
        printed = printed.replace(real, stand_in)
    assert json.loads(printed) == documented
    assert user_scope_entry(config) is ABSENT
    run_by_hand(env, item["by_hand"], OLD_ENTRY)
    assert user_scope_entry(config) == OLD_ENTRY
    assert "`claude` is never run on a `~/.claude.json` it cannot read" in text


# -- the property of the whole command: a client that was touched has a report ---------


def _calls_after_the_clients_are_chosen() -> dict[str, str | None]:
    """Every function `mcp_setup` calls from the loop over the clients to the
    end, read from its source: {name: where to plant a raise}, `None` for the
    printing itself. A call added there appears here by itself."""
    tree = ast.parse(MODULE.read_text())
    func = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "mcp_setup")
    start = next(
        n.lineno for n in func.body if isinstance(n, ast.For) and ast.unparse(n.iter) == "work"
    )
    after = [n for n in func.body if n.lineno >= start]
    local = {a.arg for a in func.args.args}
    imported: dict[str, str] = {}
    called: list[str] = []
    for stmt in after:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                local.add(node.id)
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    imported[alias.asname or alias.name] = (
                        "pyrite." + node.module if node.level == 2 else node.module
                    )
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                called.append(node.func.id)
    # The report itself: a raise here is the printing failing, with nothing
    # left to print with. Its inputs are the strings and numbers built above.
    printing = {"format_output", "Console", "_print_report"}
    found: dict[str, str | None] = {}
    for name in dict.fromkeys(called):
        if name in printing:
            found[name] = None
        elif name in imported:
            found[name] = imported[name]
        elif name in local or hasattr(builtins, name):
            continue
        else:
            found[name] = "pyrite.cli.mcp_setup_command"
    return found


PLANTS = [name for name, where in _calls_after_the_clients_are_chosen().items() if where]


def test_the_plants_are_the_calls_the_loop_and_the_output_make():
    """A guard on the guard: if the source's calls change, so does the list."""
    assert set(PLANTS) >= {
        "_note_rest_of_claude_config",
        "mcp_tool_counts",
        "resolve_config_source",
    }, PLANTS


@pytest.mark.parametrize("fmt", ["json", "rich"])
@pytest.mark.parametrize("name", PLANTS)
def test_a_bug_anywhere_after_a_client_changed_still_prints_every_clients_report(
    env, monkeypatch, name, fmt
):
    """The property of #690 for the whole command, not only the two setup
    functions: once a client has been touched, the command ends with a report
    for it, whatever raises between its change and the output, and the next
    client is still attempted. The points are every call `mcp_setup` makes
    after the clients are chosen (derived from its source)."""
    import importlib

    where = _calls_after_the_clients_are_chosen()[name]
    target = Target(env, "claude-code")
    install_desktop(linux_desktop_config(env.home))
    reached = []

    def planted(*args, **kwargs):
        reached.append(name)
        raise RuntimeError("planted")

    owner = importlib.import_module(where)
    monkeypatch.setattr(owner, name, planted)

    result = runner.invoke(app, ["mcp-setup", "--format", fmt])

    if not reached:
        pytest.skip(f"{name} is not called in the {fmt} format")
    assert result.exit_code == 0, result.output
    if fmt == "json":
        clients = {c["client"]: c for c in report_of(result)["clients"]}
        assert set(clients) == {"claude-code", "claude-desktop"}, clients
        # Read back from the file: the change is there, so the report says so.
        assert clients["claude-code"]["status"] == "created", clients
        assert clients["claude-desktop"]["status"] == "created", clients
        assert clients["claude-code"].get("error_code") is None, clients
        assert target.entry()["command"] == NEW
    else:
        assert "Claude Code (user scope" in result.stdout and "Claude Desktop" in result.stdout
        assert "created" in result.stdout, result.output


@pytest.mark.control(
    reason="a guard on the plants: it passes wherever each call is made, with or without "
    "the fix; the plants themselves are the red tests"
)
def test_every_plant_is_reached_in_at_least_one_format(env, monkeypatch):
    """The parametrized test above skips a point the format never calls; this
    one fails if a point is never called in either, so none is vacuous."""
    import importlib

    never = []
    for name in PLANTS:
        where = _calls_after_the_clients_are_chosen()[name]
        for fmt in ("json", "rich"):
            with monkeypatch.context() as m:
                Target(env, "claude-code")
                linux_desktop_config(env.home).unlink(missing_ok=True)
                install_desktop(linux_desktop_config(env.home))
                (env.home / ".claude.json").unlink(missing_ok=True)
                hit = []
                owner = importlib.import_module(where)
                real = getattr(owner, name)

                def spy(*args, real=real, hit=hit, **kwargs):
                    hit.append(1)
                    return real(*args, **kwargs)

                m.setattr(owner, name, spy)
                runner.invoke(app, ["mcp-setup", "--format", fmt])
                if hit:
                    break
        else:
            never.append(name)
    assert never == []


def test_a_projects_key_nothing_can_resolve_does_not_end_the_command(env):
    """Evidence 1 of the cold read: a NUL in a `projects` key made
    os.path.realpath raise after `claude mcp add` had written the entry."""
    install_fake_claude(env)
    seed_claude_json(
        env,
        mcpServers={"pyrite": OLD_ENTRY},
        projects={"/srv/a\x00b": {"mcpServers": {"pyrite": {"command": "/x", "args": ["mcp"]}}}},
    )

    result = setup("--client", "claude-code")

    item = only_client(result)
    assert item["status"] == "changed", item
    assert item.get("error_code") is None, item
    assert [o["directory"] for o in item["other_scopes"]] == ["/srv/a\x00b"], item


def test_a_deleted_current_directory_does_not_end_the_command(env):
    """Evidence 1 of the cold read: Path.cwd() raised FileNotFoundError after
    the change. The entry is changed, so the report says so."""
    install_fake_claude(env)
    seed_claude_json(env, mcpServers={"pyrite": OLD_ENTRY})
    os.rmdir(env.work)

    result = setup("--client", "claude-code")

    item = only_client(result)
    assert item["status"] == "changed", item
    assert item.get("error_code") is None, item
    assert user_scope_entry(env.home / ".claude.json")["command"] == NEW


@pytest.mark.parametrize("seed", [("--force",), ("--force", "--tier", "admin")])
def test_the_suggestion_after_a_change_that_left_no_entry_says_what_a_re_run_writes(
    env, monkeypatch, seed
):
    """A re-run adds a new entry: the write tier unless --tier is given, and
    no extra arguments. The suggestion says so, so a person who had another
    tier or arguments is not sent to a command that quietly gives them less."""
    install_fake_claude(env)
    seed_claude_json(
        env, mcpServers={"pyrite": {**OLD_ENTRY, "args": ["mcp", "--tier", "read", "-vv"]}}
    )
    monkeypatch.setenv("FAKE_CLAUDE_FAIL", "add")

    item = only_client(setup("--client", "claude-code", *seed))

    assert item["status"] == "failed" and "removed" in item, item
    assert "not what the previous entry held" in item["suggestion"], item["suggestion"]
    assert "write tier" in item["suggestion"], item["suggestion"]
