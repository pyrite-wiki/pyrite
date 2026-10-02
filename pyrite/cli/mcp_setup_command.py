"""`pyrite mcp-setup`: register the Pyrite MCP server where each client reads it (#582).

The property is one sentence: after `mcp-setup`, the named client starts the
Pyrite server, at the tier the user chose, on its next launch. That takes three
things together, and before #582 all three were wrong:

- **the client's own location.** Claude Code: `claude mcp add -s user` (the
  client owns ``~/.claude.json``, a file with much else in it, so Pyrite never
  edits it by hand), or ``./.mcp.json`` with ``--project``. Claude Desktop: its
  per-OS ``claude_desktop_config.json`` (macOS and Windows per
  modelcontextprotocol.io "Connect to local MCP servers"; there is no official
  Linux build, and unofficial ones use the XDG config directory).
- **a command that starts.** The absolute path of the `pyrite` script of the
  install that is running, found from the interpreter's scripts directory and
  never from ``PATH``: a GUI client has no venv on its ``PATH``, and the old
  ``"command": "python"`` fallback started whichever Python came first. It is
  run once (``mcp --help``) before anything is written.
- **the tier, explicitly.** ``["mcp", "--tier", <tier>]``, so the entry depends
  on no default.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import sysconfig
import tempfile
from dataclasses import dataclass
from pathlib import Path

import typer
from rich.console import Console
from rich.text import Text

from ..utils.errors import cli_error

SERVER_NAME = "pyrite"
CLAUDE_CODE = "claude-code"
CLAUDE_DESKTOP = "claude-desktop"
CLIENTS = (CLAUDE_CODE, CLAUDE_DESKTOP)
DESKTOP_CONFIG_NAME = "claude_desktop_config.json"

#: What the removed `pyrite-admin mcp-setup` registered, one server per tier.
STALE_TRIO = ("pyrite-read", "pyrite-write", "pyrite-admin")

#: Environment variables that name the config explicitly (config.py
#: resolve_config_source, step 1). A GUI-launched server never sees the shell's.
CONFIG_ENV_VARS = ("PYRITE_CONFIG_DIR", "PYRITE_DATA_DIR")

_SUBPROCESS_TIMEOUT = 60


def mcp_tool_counts() -> dict[str, int]:
    """Tools served per tier: tool_schemas.py plus plugin tools registered for
    that tier, the way PyriteMCPServer.__init__ assembles them, without
    constructing a server (no DB or config needed)."""
    from ..plugins import get_registry
    from ..server.tool_schemas import ADMIN_TOOLS, READ_TOOLS, WRITE_TOOLS

    registry = get_registry()
    read = len(READ_TOOLS) + len(registry.get_all_mcp_tools("read"))
    write = len(READ_TOOLS) + len(WRITE_TOOLS) + len(registry.get_all_mcp_tools("write"))
    admin = (
        len(READ_TOOLS)
        + len(WRITE_TOOLS)
        + len(ADMIN_TOOLS)
        + len(registry.get_all_mcp_tools("admin"))
    )
    return {"read": read, "write": write, "admin": admin}


def desktop_config_path() -> Path:
    """Where Claude Desktop reads its MCP servers on this OS."""
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Claude" / DESKTOP_CONFIG_NAME
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else home / "AppData" / "Roaming"
        return base / "Claude" / DESKTOP_CONFIG_NAME
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return (Path(xdg) if xdg else home / ".config") / "Claude" / DESKTOP_CONFIG_NAME


def find_claude_code() -> Path | None:
    """The `claude` CLI: on PATH, or where its installers put it."""
    import shutil

    found = shutil.which("claude")
    if found:
        return Path(found)
    home = Path.home()
    for candidate in (home / ".local" / "bin" / "claude", home / ".claude" / "local" / "claude"):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def pyrite_executable() -> Path | None:
    """The `pyrite` script of the running install, as an absolute path.

    The interpreter's own scripts directory first (a venv's bin/, or the
    system Scripts\\ on Windows), then the user scheme's (`pip install
    --user`, e.g. ~/Library/Python/3.12/bin). Never PATH: what a shell finds
    first may be another install, and a GUI client's PATH has neither.
    """
    names = ("pyrite.exe", "pyrite") if sys.platform == "win32" else ("pyrite",)
    dirs = [sysconfig.get_path("scripts")]
    with contextlib.suppress(Exception):
        dirs.append(sysconfig.get_path("scripts", sysconfig.get_preferred_scheme("user")))
    for directory in dirs:
        for name in names:
            candidate = Path(directory) / name
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate.absolute()
    return None


@dataclass
class _FileTarget:
    label: str
    path: Path
    entry: dict
    data: dict
    stale: list[str]


def _is_old_admin_entry(entry: object) -> bool:
    """An entry the removed `pyrite-admin mcp-setup` wrote, and only that: a
    user's own server that happens to share the name is kept."""
    if not isinstance(entry, dict):
        return False
    command = str(entry.get("command", ""))
    args = [str(a) for a in entry.get("args") or []]
    return Path(command).name in ("pyrite-admin", "pyrite-admin.exe") or (
        "pyrite.admin_cli" in args
    )


def _load_config(path: Path) -> dict:
    """The JSON object at `path`, or {} when it does not exist yet. Anything a
    rewrite would lose or mangle is an error, and the file is left alone."""
    if not path.exists():
        return {}
    keep = f"nothing was written; fix {path} or move it aside, then re-run `pyrite mcp-setup`"
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError as exc:
        cli_error(
            f"{path} is not valid JSON (line {exc.lineno}, column {exc.colno}: {exc.msg})",
            error_code="CONFIG_INVALID",
            suggestion=keep,
        )
    except OSError as exc:
        cli_error(f"Cannot read {path}: {exc}", error_code="CONFIG_NOT_READABLE", suggestion=keep)
    if not isinstance(data, dict):
        cli_error(
            f"{path} holds a JSON {type(data).__name__}, not an object",
            error_code="CONFIG_INVALID",
            suggestion=keep,
        )
    if not isinstance(data.get("mcpServers", {}), dict):
        cli_error(
            f'"mcpServers" in {path} is not an object',
            error_code="CONFIG_INVALID",
            suggestion=keep,
        )
    return data


def _refuse_read_only(path: Path) -> None:
    """os.replace() would swap a read-only file out from under its mode bits
    (and root passes os.access), so a file with no write bit is the user
    saying no."""
    if path.exists() and (
        not stat.S_IMODE(path.stat().st_mode) & 0o222 or not os.access(path, os.W_OK)
    ):
        cli_error(
            f"{path} is read-only",
            error_code="CONFIG_NOT_WRITABLE",
            suggestion="make it writable and re-run, or add the entry by hand "
            "(run `pyrite mcp-setup --config <scratch.json>` to see it)",
        )


def _new_file_mode() -> int:
    umask = os.umask(0)
    os.umask(umask)
    return 0o666 & ~umask


def _atomic_write(path: Path, text: str) -> None:
    """Write beside the file, then os.replace(): a reader sees the old file or
    the new one, never half of either, and a failure leaves the old one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else _new_file_mode()
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _prepare_file(label: str, path: Path, entry: dict) -> _FileTarget:
    _refuse_read_only(path)
    data = _load_config(path)
    servers = data.get("mcpServers", {})
    stale = [name for name in STALE_TRIO if _is_old_admin_entry(servers.get(name))]
    return _FileTarget(label, path, entry, data, stale)


def _write_file(target: _FileTarget) -> None:
    servers = target.data.setdefault("mcpServers", {})
    for name in target.stale:
        del servers[name]
    servers[SERVER_NAME] = target.entry
    try:
        _atomic_write(target.path, json.dumps(target.data, indent=2) + "\n")
    except OSError as exc:
        cli_error(
            f"Could not write {target.path}: {exc}",
            error_code="CONFIG_WRITE_FAILED",
            suggestion="the previous file is unchanged; fix the cause and re-run",
        )


def _claude_code_add(claude: Path, args: list[str], manual: str) -> str:
    """Register through Claude Code's own CLI. Returns where it says it wrote.

    `claude mcp add` refuses a name that exists ("already exists in user
    config", exit 1), so a re-run removes the old entry and adds again.
    """

    def run(*cmd: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [str(claude), "mcp", *cmd],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT,
            stdin=subprocess.DEVNULL,
        )

    add = ("add", "-s", "user", SERVER_NAME, "--", *args)
    try:
        proc = run(*add)
        if proc.returncode != 0 and "already exists" in proc.stderr + proc.stdout:
            run("remove", "-s", "user", SERVER_NAME)
            proc = run(*add)
    except (OSError, subprocess.TimeoutExpired) as exc:
        cli_error(
            f"Could not run {claude}: {exc}",
            error_code="CLIENT_COMMAND_FAILED",
            suggestion=f"run it yourself: {manual}",
        )
    if proc.returncode != 0:
        cli_error(
            f"`claude mcp add` failed: {(proc.stderr or proc.stdout).strip()}",
            error_code="CLIENT_COMMAND_FAILED",
            suggestion=f"run it yourself: {manual}",
        )
    match = re.search(r"^File modified: (.+)$", proc.stdout, re.M)
    return match.group(1).strip() if match else "~/.claude.json"


def _verify_starts(command: Path) -> None:
    """Run the command once before any client is told to: a client that cannot
    start it says so only in its own log."""
    try:
        proc = subprocess.run(
            [str(command), "mcp", "--help"],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        proc = None
        detail = str(exc)
    else:
        detail = (proc.stderr or proc.stdout).strip()[-2000:]
    if proc is None or proc.returncode != 0:
        cli_error(
            f"{command} mcp --help failed, so no client could start it: {detail}",
            error_code="MCP_COMMAND_FAILED",
            suggestion="reinstall pyrite into this environment (the MCP server is a "
            "core dependency) and re-run; nothing was written",
        )


def _desktop_env() -> dict[str, str]:
    """PYRITE_CONFIG_DIR / PYRITE_DATA_DIR as this process has them, absolute.

    Claude Desktop starts the server with a limited environment and an
    undefined cwd (`/` on macOS), so without these it loads ~/.pyrite, not the
    config the user just set up with. Only an explicit variable is pinned: a
    repo-local .pyrite/ is untrusted, and pinning it would make it trusted.
    """
    return {
        var: str(Path(os.environ[var]).expanduser().absolute())
        for var in CONFIG_ENV_VARS
        if os.environ.get(var)
    }


def mcp_setup(
    client: str | None = typer.Option(
        None,
        "--client",
        help="claude-code or claude-desktop. Default: every client found on this machine.",
    ),
    tier: str = typer.Option(
        "write",
        "--tier",
        help="Tool tier the client gets: read, write or admin.",
    ),
    project: bool = typer.Option(
        False,
        "--project",
        help="Claude Code project scope: write ./.mcp.json instead of running `claude mcp add`.",
    ),
    config_path: Path | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Write the entry into this mcpServers JSON file instead (any MCP client).",
    ),
):
    """
    Register the Pyrite MCP server with Claude Code and Claude Desktop.

    Claude Code gets it through `claude mcp add -s user` (stored in
    ~/.claude.json), or in ./.mcp.json with --project. Claude Desktop gets it
    in its claude_desktop_config.json (macOS: ~/Library/Application
    Support/Claude/, Windows: %APPDATA%\\Claude\\). The entry runs this
    install's pyrite by absolute path with an explicit --tier (default: write).
    Restart the client afterwards.
    """
    from ..services.access_policy import ROLES

    out = Console(soft_wrap=True, highlight=False)

    if tier not in ROLES:
        cli_error(
            f"Invalid tier: {tier!r}",
            error_code="INVALID_TIER",
            suggestion=f"choose one of: {', '.join(ROLES)}",
        )
    if client is not None and client not in CLIENTS:
        cli_error(
            f"Unknown client: {client!r}",
            error_code="INVALID_OPTION",
            suggestion=f"choose one of: {', '.join(CLIENTS)}",
        )
    if project and client == CLAUDE_DESKTOP:
        cli_error(
            "--project is Claude Code's project scope (.mcp.json); Claude Desktop has none",
            error_code="INVALID_OPTION",
            suggestion="drop --project, or use --client claude-code",
        )
    if config_path is not None and (project or client):
        cli_error(
            "--config writes one named file; it does not combine with --client or --project",
            error_code="INVALID_OPTION",
            suggestion="use --config alone",
        )

    command = pyrite_executable()
    if command is None:
        cli_error(
            f"Cannot find the `pyrite` script of this install (Python: {sys.executable})",
            error_code="PYRITE_EXECUTABLE_NOT_FOUND",
            suggestion='reinstall with pip (`pip install -e ".[cli]"` in a checkout) so the '
            "`pyrite` script exists, then run that script's mcp-setup",
        )
    _verify_starts(command)

    args = [str(command), "mcp", "--tier", tier]
    manual_claude = shlex.join(["claude", "mcp", "add", "-s", "user", SERVER_NAME, "--", *args])
    desktop_env = _desktop_env()
    desktop_entry = {"command": str(command), "args": args[1:], "env": desktop_env}

    # Decide every target before writing any, so a bad file stops the run
    # before another client has been changed.
    files: list[_FileTarget] = []
    claude: Path | None = None
    notes: list[str] = []

    if config_path is not None:
        files.append(_prepare_file("Config file", config_path.expanduser(), desktop_entry))
    elif project:
        mcp_json = Path.cwd() / ".mcp.json"
        entry = {"type": "stdio", "command": str(command), "args": args[1:], "env": {}}
        files.append(_prepare_file("Claude Code (project scope)", mcp_json, entry))
        notes.append(
            f"{mcp_json.name} holds this machine's path to pyrite; commit it only if "
            "everyone who opens the project has pyrite installed at that path."
        )
    else:
        desktop = desktop_config_path()
        if client in (None, CLAUDE_CODE):
            claude = find_claude_code()
        if client == CLAUDE_CODE and claude is None:
            cli_error(
                "Claude Code's `claude` command was not found (on PATH, ~/.local/bin or "
                "~/.claude/local)",
                error_code="CLIENT_NOT_FOUND",
                suggestion="install Claude Code and re-run, or write ./.mcp.json with "
                f"`pyrite mcp-setup --project`, or run: {manual_claude}",
            )
        if client == CLAUDE_DESKTOP or (client is None and desktop.parent.is_dir()):
            if not desktop.parent.is_dir():
                notes.append(
                    f"{desktop.parent} did not exist yet; Claude Desktop reads the entry "
                    "once it is installed."
                )
            if sys.platform not in ("darwin", "win32"):
                notes.append(
                    "Claude Desktop has no official Linux build; this is the path unofficial builds use."
                )
            files.append(_prepare_file("Claude Desktop", desktop, desktop_entry))
        if claude is None and not files:
            snippet = json.dumps({"mcpServers": {SERVER_NAME: desktop_entry}}, indent=2)
            out.print(Text("No MCP client found. To add the Pyrite server by hand, run:"))
            out.print(Text(f"  {manual_claude}"))
            out.print(
                Text(
                    "or put this in your client's MCP config "
                    f"(Claude Desktop: {desktop}; a project: .mcp.json):"
                )
            )
            out.print(Text(snippet))
            cli_error(
                f"No MCP client found: no `claude` command, and no {desktop.parent}",
                error_code="CLIENT_NOT_FOUND",
                suggestion="install Claude Code or Claude Desktop and re-run, or use "
                "--client, --project or --config PATH",
            )

    if desktop_env:
        pinned = ", ".join(f"{k}={v}" for k, v in desktop_env.items())
        if any(f.entry is desktop_entry for f in files):
            notes.append(f"The entry pins {pinned}: a GUI client does not see your shell's.")
    elif any(f.entry is desktop_entry for f in files):
        from ..config import resolve_config_source

        source, trusted = resolve_config_source()
        if not trusted:
            notes.append(
                f"Claude Desktop starts the server outside this directory, so it loads "
                f"~/.pyrite, not {source}."
            )

    configured: list[tuple[str, str]] = []
    if claude is not None:
        where = _claude_code_add(claude, args, manual_claude)
        configured.append(("Claude Code (user scope, via `claude mcp add`)", where))
    for target in files:
        _write_file(target)
        configured.append((target.label, str(target.path)))
        if target.stale:
            notes.append(
                f"Removed {', '.join(target.stale)} from {target.path} "
                "(left by the old `pyrite-admin mcp-setup`)."
            )

    count = mcp_tool_counts()[tier]
    out.print(Text(f"Pyrite MCP server registered ({tier} tier, {count} tools):"))
    for label, where in configured:
        out.print(Text(f"  {label}: {where}"))
    out.print(Text(f"  command: {shlex.join(args)}"))
    for note in notes:
        out.print(Text(f"Note: {note}"))
    out.print(Text("Restart the client to load it. `pyrite mcp --help` lists each tier's tools."))
