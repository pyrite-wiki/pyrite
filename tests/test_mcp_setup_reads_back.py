"""`pyrite mcp-setup` writes what the client reads (#582).

Every test reads back the location the client itself loads -- Claude Desktop's
per-OS ``claude_desktop_config.json``, Claude Code's ``~/.claude.json`` (written
by ``claude mcp add -s user``, never by Pyrite) or a project ``.mcp.json`` --
and checks the one property that makes the client start the server: an entry
whose command is an absolute path that exists and whose args name the tier.

Nothing here touches a real client file: each test runs in a temp ``HOME`` with
``PATH`` narrowed to a scratch ``bin/``, where a fake ``claude`` stands in for
Claude Code. The fake copies what Claude Code 2.1.287 was observed to do (PR
#612): ``mcp add -s user`` writes top-level ``mcpServers`` in
``$HOME/.claude.json`` and prints ``File modified: <path>`` on stdout; adding a
name that exists, or removing one that does not, exits 1 with a message on
stderr. ``test_real_claude_code_reads_back_the_entry`` checks the same thing
against the real binary when one is installed.
"""

from __future__ import annotations

import json
import os
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

FAKE_CLAUDE = textwrap.dedent(
    """\
    #!{python}
    import json, os, sys
    from pathlib import Path

    home = Path(os.environ["HOME"])
    cfg = Path(os.environ.get("CLAUDE_CONFIG_DIR") or home) / ".claude.json"
    args = sys.argv[1:]
    with open(home / "claude-calls.jsonl", "a") as log:
        log.write(json.dumps(args) + "\\n")
    data = json.loads(cfg.read_text()) if cfg.exists() else {{}}
    servers = data.setdefault("mcpServers", {{}})
    if args[:2] == ["mcp", "add"]:
        sep = args.index("--")
        opts, (command, *rest) = args[2:sep], args[sep + 1 :]
        name, env, scope, i = opts[-1], {{}}, "local", 0
        while i < len(opts) - 1:
            if opts[i] in ("-s", "--scope"):
                scope = opts[i + 1]
            elif opts[i] in ("-e", "--env"):
                key, value = opts[i + 1].split("=", 1)
                env[key] = value
            i += 2 if opts[i].startswith("-") else 1
        if scope != "user":
            sys.exit(f"fake claude: only user scope is emulated, got {{scope}}")
        if name in servers:
            # Deliberately not Claude Code's wording: mcp-setup must not
            # depend on message text (#612 review).
            sys.exit(f"fake claude: E_DUPLICATE {{name}}")
        servers[name] = {{"type": "stdio", "command": command, "args": rest, "env": env}}
    elif args[:2] == ["mcp", "remove"]:
        if os.environ.get("FAKE_CLAUDE_FAIL") == "remove":
            sys.exit("fake claude: E_LOCKED")
        name = [a for a in args[2:] if not a.startswith("-") and a != "user"][0]
        if name not in servers:
            sys.exit(f"fake claude: E_MISSING {{name}}")
        del servers[name]
    else:
        sys.exit(f"fake claude: unsupported {{args}}")
    cfg.write_text(json.dumps(data, indent=2))
    print(f"File modified: {{cfg}}")
    """
)


def _guard_paths_inside(monkeypatch, root: Path) -> None:
    """Fail the test if mcp-setup computes or writes a client path outside
    `root`. Skipped where the module does not exist yet (dev, for verify-red),
    so a test there still fails on behaviour, not on this import."""
    try:
        import pyrite.cli.mcp_setup_command as mod
    except ImportError:
        return
    root = root.resolve()

    def inside(path, what):
        resolved = Path(os.path.realpath(path))
        assert resolved.is_relative_to(root), f"{what} outside the test's temp dir: {path}"
        return path

    for name in ("desktop_config_path", "claude_code_config_path"):
        real = getattr(mod, name, None)
        if real is not None:
            monkeypatch.setattr(mod, name, lambda real=real, name=name: inside(real(), name))
    real_write = mod._atomic_write
    monkeypatch.setattr(
        mod, "_atomic_write", lambda path, text: real_write(inside(path, "write"), text)
    )


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A temp HOME, a PATH holding only a scratch bin/, and a scratch cwd.

    Every variable a client path is computed from points inside tmp_path:
    HOME (POSIX Path.home()), USERPROFILE (Windows Path.home()), APPDATA
    (Windows Desktop), XDG_CONFIG_HOME (Linux Desktop); CLAUDE_CONFIG_DIR
    is unset so Claude Code's file is $HOME/.claude.json. A guard fails the
    test if a computed or written path still lands outside tmp_path.
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
        "CLAUDE_CONFIG_DIR",
        "HOMEDRIVE",
        "HOMEPATH",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    _guard_paths_inside(monkeypatch, tmp_path)
    # cli_error's console wraps at 80 columns when not on a terminal, inside
    # the long temp paths these tests look for.
    monkeypatch.setenv("COLUMNS", "1000")
    monkeypatch.chdir(work)

    class Env:
        pass

    e = Env()
    e.home, e.bin, e.work, e.tmp = home, bindir, work, tmp_path
    return e


def install_fake_claude(env) -> Path:
    claude = env.bin / "claude"
    claude.write_text(FAKE_CLAUDE.format(python=sys.executable))
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
    return json.loads(path.read_text())["mcpServers"]


def assert_starts_pyrite_at(entry: dict, tier: str) -> None:
    """The property a client needs: an absolute command that exists, and the
    tier named explicitly so the entry depends on no default."""
    command = Path(entry["command"])
    assert command.is_absolute(), entry
    assert command.is_file(), entry
    assert command.name in ("pyrite", "pyrite.exe"), entry
    assert entry["args"] == ["mcp", "--tier", tier], entry


def setup(*args: str):
    return runner.invoke(app, ["mcp-setup", *args])


# -- each client reads back its own location ---------------------------------


def test_macos_desktop_reads_back_the_write_tier(env, monkeypatch):
    """The acceptance block's failing-today test."""
    monkeypatch.setattr(sys, "platform", "darwin")
    config = install_desktop(macos_desktop_config(env.home))

    result = setup()

    assert result.exit_code == 0, result.output
    assert_starts_pyrite_at(servers_in(config)["pyrite"], "write")
    assert "Claude Desktop" in result.output
    assert str(config) in result.output


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


def test_claude_code_user_scope_goes_through_claude_mcp_add(env):
    install_fake_claude(env)

    result = setup()

    assert result.exit_code == 0, result.output
    entry = servers_in(env.home / ".claude.json")["pyrite"]
    assert_starts_pyrite_at(entry, "write")
    add = [c for c in claude_calls(env) if c[:2] == ["mcp", "add"]]
    assert len(add) == 1 and add[0][2:4] == ["-s", "user"], add
    assert "Claude Code" in result.output
    # Where Claude Code itself said it wrote, not a path Pyrite guessed.
    assert str(env.home / ".claude.json") in result.output


def test_both_clients_found_are_both_configured_and_named(env):
    install_fake_claude(env)
    desktop = install_desktop(linux_desktop_config(env.home))

    result = setup("--tier", "read")

    assert result.exit_code == 0, result.output
    assert_starts_pyrite_at(servers_in(desktop)["pyrite"], "read")
    assert_starts_pyrite_at(servers_in(env.home / ".claude.json")["pyrite"], "read")
    assert "Claude Desktop" in result.output and "Claude Code" in result.output


def test_project_writes_mcp_json_without_calling_claude(env):
    install_fake_claude(env)

    result = setup("--project")

    assert result.exit_code == 0, result.output
    assert_starts_pyrite_at(servers_in(env.work / ".mcp.json")["pyrite"], "write")
    assert claude_calls(env) == []
    assert not (env.home / ".claude.json").exists()


def test_config_option_writes_the_named_file(env):
    target = env.tmp / "elsewhere" / "mcp.json"

    result = setup("--config", str(target))

    assert result.exit_code == 0, result.output
    assert_starts_pyrite_at(servers_in(target)["pyrite"], "write")


# -- no client, wrong tier, a command that cannot start -----------------------


def test_no_client_found_exits_nonzero_and_prints_the_manual_entry(env):
    result = setup()

    assert result.exit_code == 1, result.output
    assert "CLIENT_NOT_FOUND" in result.output
    assert "claude mcp add" in result.output
    assert '"mcpServers"' in result.output
    assert '"--tier"' in result.output
    assert sorted(p.name for p in env.home.iterdir()) == []


def test_unknown_tier_is_invalid_tier_and_writes_nothing(env):
    install_fake_claude(env)
    desktop = install_desktop(linux_desktop_config(env.home))

    result = setup("--tier", "root")

    assert result.exit_code == 1, result.output
    assert "INVALID_TIER" in result.output
    assert not desktop.exists()
    assert claude_calls(env) == []


def test_named_client_that_is_not_installed_is_client_not_found(env):
    result = setup("--client", "claude-code")

    assert result.exit_code == 1, result.output
    assert "CLIENT_NOT_FOUND" in result.output
    assert "--project" in result.output
    # About the client named, not every client there is.
    assert "Claude Desktop" not in result.output


@pytest.mark.parametrize(
    "args",
    [
        ["--project", "--client", "claude-desktop"],
        ["--config", "x.json", "--client", "claude-code"],
        ["--config", "x.json", "--project"],
        ["--client", "cursor"],
    ],
    ids=["project-desktop", "config-client", "config-project", "unknown-client"],
)
def test_contradictory_or_unknown_options_are_refused(env, args):
    install_fake_claude(env)

    result = setup(*args)

    assert result.exit_code == 1, result.output
    assert "INVALID_OPTION" in result.output
    assert claude_calls(env) == []
    assert not (env.work / "x.json").exists() and not (env.work / ".mcp.json").exists()


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
    desktop = install_desktop(linux_desktop_config(env.home))

    result = setup()

    assert result.exit_code == 1, result.output
    assert "MCP_COMMAND_FAILED" in result.output
    assert "No module named mcp" in result.output
    assert not desktop.exists()


def test_missing_pyrite_executable_is_a_clear_error(env, monkeypatch):
    empty = env.tmp / "no-scripts"
    empty.mkdir()
    _fake_scripts_dir(monkeypatch, empty)
    desktop = install_desktop(linux_desktop_config(env.home))

    result = setup()

    assert result.exit_code == 1, result.output
    assert "PYRITE_EXECUTABLE_NOT_FOUND" in result.output
    assert not desktop.exists()


# -- an existing config ---------------------------------------------------------


def test_existing_config_keeps_its_other_servers_and_keys(env):
    desktop = install_desktop(linux_desktop_config(env.home))
    desktop.write_text(
        json.dumps(
            {
                "globalShortcut": "Ctrl+Space",
                "mcpServers": {"filesystem": {"command": "npx", "args": ["-y", "fs"]}},
            }
        )
    )

    result = setup()

    assert result.exit_code == 0, result.output
    written = json.loads(desktop.read_text())
    assert written["globalShortcut"] == "Ctrl+Space"
    assert written["mcpServers"]["filesystem"] == {"command": "npx", "args": ["-y", "fs"]}
    assert_starts_pyrite_at(written["mcpServers"]["pyrite"], "write")


@pytest.mark.parametrize(
    "content",
    ['{"mcpServers": {', '["not", "an", "object"]', '{"mcpServers": []}'],
    ids=["invalid-json", "not-an-object", "servers-not-an-object"],
)
def test_unusable_config_is_a_clear_error_and_the_file_is_untouched(env, content):
    desktop = install_desktop(linux_desktop_config(env.home))
    desktop.write_text(content)

    result = setup()

    assert result.exit_code == 1, result.output
    assert "CONFIG_INVALID" in result.output
    assert str(desktop) in result.output
    assert "hint:" in result.output
    assert "Traceback" not in result.output
    assert desktop.read_text() == content


def test_rewrite_keeps_the_config_files_mode(env):
    desktop = install_desktop(linux_desktop_config(env.home))
    desktop.write_text('{"mcpServers": {}}')
    desktop.chmod(0o640)  # not mkstemp's 0o600, so a lost mode shows

    result = setup()

    assert result.exit_code == 0, result.output
    assert_starts_pyrite_at(servers_in(desktop)["pyrite"], "write")
    assert stat.S_IMODE(desktop.stat().st_mode) == 0o640


def test_read_only_config_is_refused_not_overwritten(env):
    desktop = install_desktop(linux_desktop_config(env.home))
    desktop.write_text('{"mcpServers": {}}')
    desktop.chmod(0o444)

    result = setup()

    assert result.exit_code == 1, result.output
    assert "CONFIG_NOT_WRITABLE" in result.output
    assert desktop.read_text() == '{"mcpServers": {}}'
    assert stat.S_IMODE(desktop.stat().st_mode) == 0o444


def test_a_failed_write_leaves_the_old_config_whole(env, monkeypatch):
    desktop = install_desktop(linux_desktop_config(env.home))
    before = json.dumps({"mcpServers": {"other": {"command": "/bin/true"}}})
    desktop.write_text(before)

    def interrupted(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", interrupted)

    result = setup()

    assert result.exit_code == 1, result.output
    assert desktop.read_text() == before
    assert sorted(p.name for p in desktop.parent.iterdir()) == [desktop.name]


def test_rerun_leaves_one_entry_per_client(env):
    install_fake_claude(env)
    desktop = install_desktop(linux_desktop_config(env.home))

    first = setup()
    second = setup("--tier", "admin")

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    for config in (desktop, env.home / ".claude.json"):
        servers = servers_in(config)
        assert [name for name in servers if "pyrite" in name] == ["pyrite"]
        assert_starts_pyrite_at(servers["pyrite"], "admin")


def test_stale_admin_trio_from_the_old_command_is_removed_and_named(env):
    desktop = install_desktop(linux_desktop_config(env.home))
    old = "/old/venv/bin/pyrite-admin"
    desktop.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "pyrite-read": {"command": old, "args": ["mcp", "--tier", "read"]},
                    "pyrite-write": {
                        "command": "python",
                        "args": ["-m", "pyrite.admin_cli", "mcp", "--tier", "write"],
                    },
                    "pyrite-admin": {"command": old, "args": ["mcp", "--tier", "admin"]},
                }
            }
        )
    )

    result = setup()

    assert result.exit_code == 0, result.output
    assert list(servers_in(desktop)) == ["pyrite"]
    for name in ("pyrite-read", "pyrite-write", "pyrite-admin"):
        assert name in result.output


def test_a_users_own_server_named_like_the_trio_is_kept(env):
    desktop = install_desktop(linux_desktop_config(env.home))
    mine = {"command": "/usr/local/bin/my-reader", "args": []}
    desktop.write_text(json.dumps({"mcpServers": {"pyrite-read": mine}}))

    result = setup()

    assert result.exit_code == 0, result.output
    assert servers_in(desktop)["pyrite-read"] == mine
    assert_starts_pyrite_at(servers_in(desktop)["pyrite"], "write")


# -- which config the launched server loads -----------------------------------


def test_desktop_entry_pins_an_explicit_config_dir(env, monkeypatch):
    """A GUI-launched server gets no shell environment, so the config the user
    set up with would otherwise be silently swapped for ~/.pyrite."""
    install_fake_claude(env)
    desktop = install_desktop(linux_desktop_config(env.home))
    monkeypatch.setenv("PYRITE_CONFIG_DIR", str(env.tmp / "my-config"))

    result = setup()

    assert result.exit_code == 0, result.output
    assert servers_in(desktop)["pyrite"]["env"] == {"PYRITE_CONFIG_DIR": str(env.tmp / "my-config")}
    # Claude Code inherits the launching shell and resolves per project.
    assert servers_in(env.home / ".claude.json")["pyrite"]["env"] == {}


def test_desktop_entry_never_pins_a_repo_local_config(env):
    """An explicit config dir is trusted (config.py resolve_config_source), so
    pinning a cloned tree's .pyrite/ would promote it."""
    (env.work / ".pyrite").mkdir()
    (env.work / ".pyrite" / "config.yaml").write_text("knowledge_bases: []\n")
    desktop = install_desktop(linux_desktop_config(env.home))

    result = setup()

    assert result.exit_code == 0, result.output
    assert servers_in(desktop)["pyrite"].get("env", {}) == {}
    assert "~/.pyrite" in result.output


# -- the executable written ----------------------------------------------------


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
    entry must still be the absolute path of the pyrite that ran."""
    script = _fake_install(env.tmp / "My Apps" / "Python" / "3.12")
    desktop = install_desktop(linux_desktop_config(env.home))
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


@pytest.mark.skipif(REAL_CLAUDE is None, reason="Claude Code is not installed")
def test_real_claude_code_reads_back_the_entry(env, monkeypatch):
    """The fake's contract, checked against the real client: `claude mcp get`
    reads the entry from its own config and spawns the command."""
    monkeypatch.setenv("PATH", f"{env.bin}{os.pathsep}{Path(REAL_CLAUDE).parent}")

    result = setup("--client", "claude-code")

    assert result.exit_code == 0, result.output
    assert_starts_pyrite_at(servers_in(env.home / ".claude.json")["pyrite"], "write")
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


# -- pyrite-admin ----------------------------------------------------------------


def test_pyrite_admin_mcp_setup_points_to_pyrite_mcp_setup():
    from pyrite.admin_cli import app as admin_app

    result = runner.invoke(admin_app, ["mcp-setup", "--tier", "read"])

    assert result.exit_code == 1, result.output
    assert "COMMAND_MOVED" in result.output
    assert "pyrite mcp-setup" in result.output


def test_pyrite_admin_mcp_defaults_to_the_write_tier(monkeypatch):
    import pyrite.server.mcp_server as mcp_server
    from pyrite.admin_cli import app as admin_app

    started = []

    class Recorder:
        VALID_TIERS = mcp_server.PyriteMCPServer.VALID_TIERS

        def __init__(self, tier):
            started.append(tier)

        def run_stdio(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(mcp_server, "PyriteMCPServer", Recorder)

    result = runner.invoke(admin_app, ["mcp"])

    assert result.exit_code == 0, result.output
    assert started == ["write"]


def test_pyrite_admin_mcp_unknown_tier_is_invalid_tier():
    from pyrite.admin_cli import app as admin_app

    result = runner.invoke(admin_app, ["mcp", "--tier", "root"])

    assert result.exit_code == 1, result.output
    assert "INVALID_TIER" in result.output


# -- the tutorial on dev's smoke runner ------------------------------------------


@pytest.mark.control(
    reason="passes on dev, where mcp-setup always exits 0; guards that the "
    "tutorial still passes on a runner with no Claude client once "
    "mcp-setup exits 1 when it finds none (the stub claude in run_tutorial.py)"
)
def test_tutorial_mcp_setup_block_passes_on_a_runner_with_no_client(tmp_path):
    doc = tmp_path / "doc.md"
    doc.write_text("```bash\npyrite mcp-setup\n```\n")
    path = os.pathsep.join([str(Path(sys.executable).parent), "/usr/bin", "/bin"])

    proc = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "run_tutorial.py"), str(doc)],
        # PYTHONPATH: the `pyrite` the tutorial runs is this tree's, not the
        # one the editable install points at (verify-red runs another tree).
        env={"PATH": path, "HOME": str(tmp_path), "NO_COLOR": "1", "PYTHONPATH": str(REPO)},
        capture_output=True,
        text=True,
        timeout=300,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr


# -- fix round 1 (#612 cold read) ------------------------------------------------


def test_short_tier_flag_works_like_pyrite_admin_mcp(env):
    desktop = install_desktop(linux_desktop_config(env.home))

    result = setup("-t", "read")

    assert result.exit_code == 0, result.output
    assert_starts_pyrite_at(servers_in(desktop)["pyrite"], "read")


def test_project_help_says_it_writes_the_current_directory():
    result = runner.invoke(app, ["mcp-setup", "--help"], env={"COLUMNS": "1000"})

    assert result.exit_code == 0, result.output
    assert "current directory" in result.output


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_symlinked_desktop_config_is_written_through_the_link(env):
    """A dotfiles setup: the client's path is a link to a file elsewhere. The
    target gets the entry; the link stays a link."""
    target = env.tmp / "dotfiles" / "claude_desktop_config.json"
    target.parent.mkdir()
    target.write_text(json.dumps({"mcpServers": {"other": {"command": "/bin/true"}}}))
    desktop = install_desktop(linux_desktop_config(env.home))
    desktop.symlink_to(target)

    result = setup()

    assert result.exit_code == 0, result.output
    assert desktop.is_symlink()
    assert os.readlink(desktop) == str(target)
    servers = servers_in(target)
    assert servers["other"] == {"command": "/bin/true"}
    assert_starts_pyrite_at(servers["pyrite"], "write")
    assert sorted(p.name for p in target.parent.iterdir()) == [target.name]


def _fail_writes_to(monkeypatch, victim: Path) -> None:
    """The write to `victim` fails the way a read-only directory makes it
    fail (root ignores the mode bits, so the failure is injected)."""
    real = os.replace

    def replace(src, dst, *args, **kwargs):
        if Path(dst) == victim:
            raise PermissionError(13, "Permission denied", str(dst))
        return real(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", replace)


def test_partial_failure_names_what_was_configured_and_what_failed(env, monkeypatch):
    install_fake_claude(env)
    desktop = install_desktop(linux_desktop_config(env.home))
    _fail_writes_to(monkeypatch, desktop)

    result = setup()

    assert result.exit_code == 1, result.output
    assert_starts_pyrite_at(servers_in(env.home / ".claude.json")["pyrite"], "write")
    assert not desktop.exists()
    configured = [line for line in result.output.splitlines() if "Claude Code" in line]
    failed = [line for line in result.output.splitlines() if "Claude Desktop" in line]
    assert any(str(env.home / ".claude.json") in line for line in configured), result.output
    assert any("CONFIG_WRITE_FAILED" in line for line in failed), result.output
    assert "Configured" in result.output and "Failed" in result.output


def test_partial_failure_in_json_lists_each_client(env, monkeypatch):
    install_fake_claude(env)
    desktop = install_desktop(linux_desktop_config(env.home))
    _fail_writes_to(monkeypatch, desktop)

    result = setup("--format", "json")

    assert result.exit_code == 1, result.output
    report = json.loads(result.stdout)
    assert [c["client"] for c in report["configured"]] == ["claude-code"]
    assert report["configured"][0]["location"] == str(env.home / ".claude.json")
    assert [f["client"] for f in report["failed"]] == ["claude-desktop"]
    assert report["failed"][0]["error_code"] == "CONFIG_WRITE_FAILED"
    assert report["failed"][0]["suggestion"]
    assert report["tier"] == "write"


def test_json_success_lists_every_client_and_the_command(env):
    install_fake_claude(env)
    desktop = install_desktop(linux_desktop_config(env.home))

    result = setup("--format", "json")

    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert {c["client"]: c["location"] for c in report["configured"]} == {
        "claude-code": str(env.home / ".claude.json"),
        "claude-desktop": str(desktop),
    }
    assert report["failed"] == []
    assert report["command"][1:] == ["mcp", "--tier", "write"]


def test_json_no_client_is_the_error_shape_with_the_manual_entry(env):
    result = setup("--format", "json")

    assert result.exit_code == 1, result.output
    report = json.loads(result.stdout)
    assert report["error_code"] == "CLIENT_NOT_FOUND"
    assert report["manual"]["mcpServers"]["pyrite"]["args"] == ["mcp", "--tier", "write"]
    assert "claude mcp add" in report["manual_claude_code"]


USERS_OWN = {"command": "/usr/local/bin/my-pyrite-wrapper", "args": ["--serve"]}


def _seed_users_own(env, client: str) -> Path:
    if client == "claude-code":
        install_fake_claude(env)
        config = env.home / ".claude.json"
    else:
        config = install_desktop(linux_desktop_config(env.home))
    config.write_text(json.dumps({"mcpServers": {"pyrite": USERS_OWN}}))
    return config


@pytest.mark.parametrize("client", ["claude-code", "claude-desktop"])
def test_a_users_own_server_named_pyrite_is_not_replaced_without_force(env, client):
    config = _seed_users_own(env, client)
    before = config.read_text()

    result = setup("--client", client)

    assert result.exit_code == 1, result.output
    assert "SERVER_NAME_TAKEN" in result.output
    assert "--force" in result.output
    assert config.read_text() == before
    assert claude_calls(env) == []


@pytest.mark.parametrize("client", ["claude-code", "claude-desktop"])
def test_force_replaces_a_users_own_server_and_says_what_it_replaced(env, client):
    config = _seed_users_own(env, client)

    result = setup("--client", client, "--force")

    assert result.exit_code == 0, result.output
    assert_starts_pyrite_at(servers_in(config)["pyrite"], "write")
    assert "Replaced" in result.output
    assert USERS_OWN["command"] in result.output


@pytest.mark.parametrize("client", ["claude-code", "claude-desktop"])
def test_an_entry_from_the_old_pyrite_mcp_setup_is_replaced_without_force(env, client):
    """The old `pyrite mcp-setup` wrote `pyrite` -> `pyrite-admin mcp` (or
    `python -m pyrite.admin_cli mcp`); that entry is ours to replace."""
    if client == "claude-code":
        install_fake_claude(env)
        config = env.home / ".claude.json"
    else:
        config = install_desktop(linux_desktop_config(env.home))
    old = {"command": "python", "args": ["-m", "pyrite.admin_cli", "mcp"], "env": {}}
    config.write_text(json.dumps({"mcpServers": {"pyrite": old}}))

    result = setup("--client", client)

    assert result.exit_code == 0, result.output
    assert_starts_pyrite_at(servers_in(config)["pyrite"], "write")


def test_config_that_is_not_utf8_is_config_invalid_and_untouched(env):
    desktop = install_desktop(linux_desktop_config(env.home))
    raw = b'{"mcpServers": {"x": {"command": "caf\xe9"}}}'
    desktop.write_bytes(raw)

    result = setup()

    assert result.exit_code == 1, result.output
    assert "CONFIG_INVALID" in result.output
    assert "UTF-8" in result.output
    assert "Traceback" not in result.output
    assert desktop.read_bytes() == raw


def test_untouched_servers_round_trip_with_their_text(env):
    """What can be kept is kept: non-ASCII stays as written (no \\u escapes),
    strings, ints, floats, bools, null, nesting and key order compare equal.
    The JSON module's limit (in _write_file's docstring): a number's
    spelling (1.10, 1e3) and the indentation are not preserved."""
    desktop = install_desktop(linux_desktop_config(env.home))
    other = {
        "command": "/opt/Zürich tools/srv",
        "args": ["--name", "café ☕", "--n", 3, "--ratio", 0.25],
        "env": {"FLAG": True, "EMPTY": None, "NESTED": {"b": [1, {"c": "ü"}], "a": 2}},
    }
    desktop.write_text(json.dumps({"mcpServers": {"other": other}}, ensure_ascii=False))

    result = setup()

    assert result.exit_code == 0, result.output
    assert_starts_pyrite_at(servers_in(desktop)["pyrite"], "write")
    text = desktop.read_text(encoding="utf-8")
    assert "Zürich" in text and "café ☕" in text and "\\u" not in text
    written = json.loads(text)["mcpServers"]["other"]
    assert written == other
    assert list(written["env"]["NESTED"]) == ["b", "a"]


def test_mcp_server_module_entry_defaults_to_write(monkeypatch):
    """`python -m pyrite.server.mcp_server` is the third stdio entry point;
    the write default holds there too (maintainer's decision on #582)."""
    import pyrite.server.mcp_server as mcp_server

    started = []

    class Recorder:
        def __init__(self, tier, **kwargs):
            started.append(tier)

        def run_stdio(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(mcp_server, "PyriteMCPServer", Recorder)
    monkeypatch.setattr(sys, "argv", ["pyrite.server.mcp_server"])

    mcp_server.main()

    assert started == ["write"]


def test_pyrite_mcp_package_serve_defaults_to_write(monkeypatch):
    """The separately published `pyrite-mcp serve` (pyrite-mcp/)."""
    import pyrite.server.mcp_server as mcp_server

    monkeypatch.syspath_prepend(str(REPO / "pyrite-mcp"))
    sys.modules.pop("pyrite_mcp.__main__", None)
    import pyrite_mcp.__main__ as pyrite_mcp_main

    started = []

    class Recorder:
        def __init__(self, tier, **kwargs):
            started.append(tier)

        def run_stdio(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(mcp_server, "PyriteMCPServer", Recorder)
    monkeypatch.setattr(sys, "argv", ["pyrite-mcp", "serve"])

    pyrite_mcp_main.main()

    assert started == ["write"]


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
    exe.write_text(FAKE_CLAUDE.format(python=sys.executable))
    exe.chmod(0o755)

    result = setup("--client", "claude-code")

    assert result.exit_code == 0, result.output
    assert_starts_pyrite_at(servers_in(env.home / ".claude.json")["pyrite"], "write")


def test_a_failed_remove_is_reported_and_no_add_is_tried(env, monkeypatch):
    install_fake_claude(env)
    assert setup("--client", "claude-code").exit_code == 0
    monkeypatch.setenv("FAKE_CLAUDE_FAIL", "remove")

    result = setup("--client", "claude-code", "--tier", "read")

    assert result.exit_code == 1, result.output
    assert "CLIENT_COMMAND_FAILED" in result.output
    assert "claude mcp remove" in result.output
    assert claude_calls(env)[-1][:2] == ["mcp", "remove"]
    assert_starts_pyrite_at(servers_in(env.home / ".claude.json")["pyrite"], "write")


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


def test_claude_code_config_dir_is_where_ownership_is_read(env, monkeypatch):
    """CLAUDE_CONFIG_DIR moves Claude Code's .claude.json; a user's own
    'pyrite' there is found and left alone."""
    install_fake_claude(env)
    alt = env.tmp / "claude-config"
    alt.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(alt))
    (alt / ".claude.json").write_text(json.dumps({"mcpServers": {"pyrite": USERS_OWN}}))

    result = setup("--client", "claude-code")

    assert result.exit_code == 1, result.output
    assert "SERVER_NAME_TAKEN" in result.output
    assert str(alt / ".claude.json") in result.output
    assert claude_calls(env) == []
