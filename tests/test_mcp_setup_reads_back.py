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
to fail, hang or stop being runnable. ``test_real_claude_code_reads_back_the_entry``
checks the same contract against the real binary when one is installed.
"""

from __future__ import annotations

import json
import os
import random
import re
import shutil
import stat
import subprocess
import sys
import sysconfig
import textwrap
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
    data = json.loads(cfg.read_text(encoding="utf-8")) if cfg.exists() else {}
    servers = data.setdefault("mcpServers", {})
    if args[:2] == ["mcp", "add"]:
        sep = args.index("--")
        opts, (command, *rest) = args[2:sep], args[sep + 1 :]
        name, scope, i = opts[-1], "local", 0
        while i < len(opts) - 1:
            if opts[i] in ("-s", "--scope"):
                scope = opts[i + 1]
            i += 2 if opts[i].startswith("-") else 1
        if scope != "user":
            sys.exit(f"fake claude: only user scope is emulated, got {scope}")
        if name in servers:
            # Deliberately not Claude Code's wording: mcp-setup must not
            # depend on message text.
            sys.exit(f"fake claude: E_DUPLICATE {name}")
        servers[name] = {"type": "stdio", "command": command, "args": rest, "env": {}}
    elif args[:2] == ["mcp", "remove"]:
        name = [a for a in args[2:] if not a.startswith("-") and a != "user"][0]
        if name not in servers:
            sys.exit(f"fake claude: E_MISSING {name}")
        del servers[name]
    else:
        sys.exit(f"fake claude: unsupported {args}")
    cfg.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"File modified: {cfg}")
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
        monkeypatch.setattr(e.mod, "_verify_starts", lambda command: None)
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
        (["mcp", "-t", "admin"], ["mcp", "-t", "read"]),
        (["mcp", "--tier=admin"], ["mcp", "--tier=read"]),
        (["mcp"], ["mcp", "--tier", "read"]),
        (["mcp", "--tier", "admin", "-vv"], ["mcp", "--tier", "read", "-vv"]),
        (["mcp", "-vv"], ["mcp", "--tier", "read", "-vv"]),
        # `pyrite mcp` has no attached `-tX` form; an argument that merely
        # starts with -t is someone's, and is kept as it is.
        (["mcp", "-trace"], ["mcp", "--tier", "read", "-trace"]),
    ],
    ids=["long", "short", "equals", "absent", "args-after", "absent-with-args", "not-a-tier"],
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


def test_a_failed_call_that_leaves_the_config_unreadable_claims_nothing(env, monkeypatch):
    """When the file cannot be read back, the report does not say the entry is
    as it was or that it is gone: it says it could not tell."""
    target = _seed_changeable(env)
    monkeypatch.setenv("FAKE_CLAUDE_CORRUPT", "remove")

    result = target.run()

    assert result.exit_code == 1, result.output
    item = only_client(result)
    assert item["status"] == "failed" and item["error_code"] == "CLIENT_COMMAND_FAILED", item
    assert "could not be read back" in item["error"]
    assert [c[:2] for c in claude_calls(env)] == [["mcp", "remove"]]


def test_a_failed_add_after_remove_restores_the_previous_entry(env, monkeypatch):
    target = _seed_changeable(env)
    monkeypatch.setenv("FAKE_CLAUDE_FAIL", "add#1")

    result = target.run()

    assert result.exit_code == 1, result.output
    item = only_client(result)
    assert item["status"] == "stopped" and item["error_code"] == "CLIENT_COMMAND_FAILED", item
    assert "restored" in item["error"]
    assert target.entry() == OLD_ENTRY


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


def test_claude_not_runnable_after_remove_reports_exactly_what_was_removed(env, monkeypatch):
    """`claude` removed the entry and then could not be run at all."""
    target = _seed_changeable(env)
    monkeypatch.setenv("FAKE_CLAUDE_VANISH", "remove")

    result = target.run()

    assert result.exit_code == 1, result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)
    item = only_client(result)
    assert item["status"] == "failed", item
    assert item["removed"] == {"command": OLD, "args": ["mcp", "--tier", "admin"]}
    assert f"claude mcp add -s user pyrite -- {OLD} mcp --tier admin" in "\n".join(item["by_hand"])


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


def test_an_argument_claude_cannot_be_given_after_remove_is_reported(env):
    """A NUL in the entry's own args (valid JSON: "\\u0000") makes every call
    that passes it back raise ValueError, the restore included."""
    target = Target(env, "claude-code")
    target.seed(member(cmd_text(OLD), args_text("mcp", "a\\u0000b")))

    result = target.run()

    assert result.exit_code == 1, result.output
    item = only_client(result)
    assert item["status"] == "failed" and item["error_code"] == "CLIENT_COMMAND_FAILED", item
    assert item["removed"] == {"command": OLD, "args": ["mcp", "a\x00b"]}
    # A call that cannot be made is a failed call: the restore is still tried.
    assert "`claude mcp add` failed: could not be run (ValueError" in item["error"]
    assert "restoring the previous entry also failed" in item["error"], item


def test_an_unexpected_error_after_remove_still_reports_what_was_removed(env, monkeypatch):
    """Whatever goes wrong once `remove` has succeeded, never neither: the
    report holds what the entry was and the command that puts it back."""
    target = _seed_changeable(env)
    monkeypatch.setenv("FAKE_CLAUDE_FAIL", "add")
    if env.mod is not None:
        real, reads = env.mod._read_claude_config, []

        def read_then_break(config):
            reads.append(config)
            if len(reads) > 2:  # the read and the re-read before any call
                raise RuntimeError("a bug nobody predicted")
            return real(config)

        monkeypatch.setattr(env.mod, "_read_claude_config", read_then_break)

    result = target.run()

    assert result.exit_code == 1, result.output
    item = only_client(result)
    assert item["status"] == "failed", item
    assert item["removed"] == {"command": OLD, "args": ["mcp", "--tier", "admin"]}
    assert f"claude mcp add -s user pyrite -- {OLD} mcp --tier admin" == item["by_hand"][0]


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
    assert "ENTRY_NOT_MCP" in result.output and "--force" in result.output


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


def _tutorial(tmp_path: Path, doc_text: str) -> subprocess.CompletedProcess:
    doc = tmp_path / "doc.md"
    doc.write_text(doc_text)
    path = os.pathsep.join([str(Path(sys.executable).parent), "/usr/bin", "/bin"])
    return subprocess.run(
        [sys.executable, str(REPO / "scripts" / "run_tutorial.py"), str(doc)],
        # PYTHONPATH: the `pyrite` the tutorial runs is this tree's, not the
        # one the editable install points at (verify-red runs another tree).
        env={"PATH": path, "HOME": str(tmp_path), "NO_COLOR": "1", "PYTHONPATH": str(REPO)},
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
