"""`pyrite mcp-setup`: register the Pyrite MCP server where each client reads it (#582).

The property is one sentence: after `mcp-setup`, the named client starts the
Pyrite server, at the tier the user chose, on its next launch. That takes three
things together, and before #582 all three were wrong:

- **the client's own location.** Claude Code: `claude mcp add -s user` (the
  client owns ``~/.claude.json``, a file with much else in it, so Pyrite never
  writes it; it only reads it to see whether a ``pyrite`` server is already
  there and whose it is), or ``./.mcp.json`` with ``--project``. Claude
  Desktop: its per-OS ``claude_desktop_config.json`` (macOS and Windows per
  modelcontextprotocol.io "Connect to local MCP servers"; there is no official
  Linux build, and unofficial ones use the XDG config directory).
- **a command that starts.** The absolute path of the `pyrite` script of the
  install that is running, found from the interpreter's scripts directory and
  never from ``PATH``: a GUI client has no venv on its ``PATH``, and the old
  ``"command": "python"`` fallback started whichever Python came first. It is
  run once (``mcp --help``) before anything is written (#582 groom, open
  question 2: a client that cannot start a server says so only in its log).
- **the tier, explicitly.** ``["mcp", "--tier", <tier>]``, so the entry depends
  on no default.

A server named ``pyrite`` that mcp-setup did not write is never replaced
silently, in a file or in Claude Code: it is refused unless ``--force``, and
with ``--force`` the output says what was replaced.
"""

from __future__ import annotations

import contextlib
import json
import os
import shlex
import stat
import subprocess
import sys
import sysconfig
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import typer
from rich.console import Console
from rich.text import Text

from ..utils.errors import cli_error
from .output import format_output, validate_output_format

SERVER_NAME = "pyrite"
CLAUDE_CODE = "claude-code"
CLAUDE_DESKTOP = "claude-desktop"
CLIENTS = (CLAUDE_CODE, CLAUDE_DESKTOP)
DESKTOP_CONFIG_NAME = "claude_desktop_config.json"

#: What the removed `pyrite-admin mcp-setup` registered, one server per tier.
STALE_TRIO = ("pyrite-read", "pyrite-write", "pyrite-admin")

#: Executables an entry this command (or its predecessors) wrote runs.
_PYRITE_COMMANDS = ("pyrite", "pyrite.exe", "pyrite-admin", "pyrite-admin.exe")

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


def claude_code_config_path() -> Path:
    """Where Claude Code keeps user-scope servers: ``~/.claude.json``, or
    ``$CLAUDE_CONFIG_DIR/.claude.json`` (both observed with Claude Code
    2.1.287). Read only, never written: `claude mcp add` writes it."""
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    return (Path(base) if base else Path.home()) / ".claude.json"


def find_claude_code() -> Path | None:
    """The `claude` CLI: on PATH, or where its installers put it."""
    import shutil

    found = shutil.which("claude")
    if found:
        return Path(found)
    home = Path.home()
    name = "claude.exe" if sys.platform == "win32" else "claude"
    for candidate in (home / ".local" / "bin" / name, home / ".claude" / "local" / name):
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


class _SetupError(Exception):
    """One client could not be checked or changed; carries ADR-0037's shape."""

    def __init__(self, error_code: str, message: str, suggestion: str):
        super().__init__(message)
        self.error_code, self.message, self.suggestion = error_code, message, suggestion


def _is_pyrite_entry(entry: object) -> bool:
    """An entry mcp-setup, the old `pyrite mcp-setup` or the removed
    `pyrite-admin mcp-setup` wrote: it runs pyrite's own MCP server. Anything
    else under the name is the user's."""
    if not isinstance(entry, dict):
        return False
    command = Path(str(entry.get("command", ""))).name
    args = [str(a) for a in entry.get("args") or []]
    if command in _PYRITE_COMMANDS and args[:1] == ["mcp"]:
        return True
    return any(module in args for module in ("pyrite.admin_cli", "pyrite.cli"))


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


def _describe(entry: object) -> str:
    if isinstance(entry, dict):
        return shlex.join([str(entry.get("command", "")), *map(str, entry.get("args") or [])])
    return json.dumps(entry, ensure_ascii=False)


def _load_config(path: Path) -> dict:
    """The JSON object at `path`, or {} when it does not exist yet. Anything a
    rewrite would lose or mangle is a _SetupError, and the file is left alone."""
    if not path.exists():
        return {}
    keep = f"nothing was written; fix {path} or move it aside, then re-run `pyrite mcp-setup`"
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise _SetupError("CONFIG_NOT_READABLE", f"Cannot read {path}: {exc}", keep) from None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _SetupError(
            "CONFIG_INVALID", f"{path} is not valid UTF-8 (byte {exc.start}: {exc.reason})", keep
        ) from None
    try:
        data = json.loads(text or "{}")
    except json.JSONDecodeError as exc:
        raise _SetupError(
            "CONFIG_INVALID",
            f"{path} is not valid JSON (line {exc.lineno}, column {exc.colno}: {exc.msg})",
            keep,
        ) from None
    if not isinstance(data, dict):
        raise _SetupError(
            "CONFIG_INVALID", f"{path} holds a JSON {type(data).__name__}, not an object", keep
        )
    if not isinstance(data.get("mcpServers", {}), dict):
        raise _SetupError("CONFIG_INVALID", f'"mcpServers" in {path} is not an object', keep)
    return data


def _refuse_read_only(path: Path) -> None:
    """os.replace() would swap a read-only file out from under its mode bits
    (and root passes os.access), so a file with no write bit is the user
    saying no."""
    if path.exists() and (
        not stat.S_IMODE(path.stat().st_mode) & 0o222 or not os.access(path, os.W_OK)
    ):
        raise _SetupError(
            "CONFIG_NOT_WRITABLE",
            f"{path} is read-only",
            "make it writable and re-run, or add the entry by hand "
            "(run `pyrite mcp-setup --config <scratch.json>` to see it)",
        )


def _taken(where: str, existing: object) -> _SetupError:
    return _SetupError(
        "SERVER_NAME_TAKEN",
        f"{where} already has an MCP server named {SERVER_NAME!r} that mcp-setup did not "
        f"write: {_describe(existing)}",
        "re-run with --force to replace it (the output says what it replaced), "
        "or rename or remove yours first",
    )


def _new_file_mode() -> int:
    umask = os.umask(0)
    os.umask(umask)
    return 0o666 & ~umask


def _atomic_write(path: Path, text: str) -> None:
    """Write beside the file, then os.replace(): a reader sees the old file or
    the new one, never half of either, and a failure leaves the old one.
    `path` is the real file; a symlink's caller resolves it first, so the link
    stays a link."""
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


@dataclass
class _FileTarget:
    client: str
    label: str
    path: Path  # where the client reads it, as reported
    real: Path  # what gets written: `path` with symlinks resolved
    entry: dict
    data: dict
    stale: list[str]
    replaced: object = None


@dataclass
class _ClaudeTarget:
    claude: Path
    config: Path
    existing: object = None  # the user-scope `pyrite` entry, when one was read
    replaced: object = None


@dataclass
class _Result:
    configured: list[dict] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _prepare_file(client: str, label: str, path: Path, entry: dict, force: bool) -> _FileTarget:
    real = Path(os.path.realpath(path))
    _refuse_read_only(real)
    data = _load_config(real)
    servers = data.get("mcpServers", {})
    existing = servers.get(SERVER_NAME)
    replaced = None
    if existing is not None and not _is_pyrite_entry(existing):
        if not force:
            raise _taken(str(path), existing)
        replaced = existing
    stale = [name for name in STALE_TRIO if _is_old_admin_entry(servers.get(name))]
    return _FileTarget(client, label, path, real, entry, data, stale, replaced)


def _write_file(target: _FileTarget) -> None:
    """Rewrite the file with our entry and the stale trio gone.

    Everything else is kept as closely as the json module allows: key order,
    every value (equal after a round trip), and non-ASCII text as written
    (ensure_ascii=False). What it cannot keep is spelling: a number is
    re-spelled the way Python prints it (1.10 -> 1.1, 1e3 -> 1000.0), \\u
    escapes become the characters they name, duplicate keys collapse to the
    last, and the indentation becomes two spaces.
    """
    servers = target.data.setdefault("mcpServers", {})
    for name in target.stale:
        del servers[name]
    servers[SERVER_NAME] = target.entry
    try:
        _atomic_write(target.real, json.dumps(target.data, indent=2, ensure_ascii=False) + "\n")
    except OSError as exc:
        raise _SetupError(
            "CONFIG_WRITE_FAILED",
            f"Could not write {target.real}: {exc}",
            "the previous file is unchanged; fix the cause and re-run",
        ) from None


def _prepare_claude_code(claude: Path, force: bool) -> _ClaudeTarget:
    """Read Claude Code's own config to learn whether a user-scope `pyrite`
    exists and whose it is. The decision never depends on `claude`'s message
    text; an unreadable file means "unknown", and `claude mcp add` then
    decides by its exit code."""
    config = claude_code_config_path()
    target = _ClaudeTarget(claude, config)
    with contextlib.suppress(OSError, ValueError, _SetupError):
        servers = _load_config(config).get("mcpServers", {})
        target.existing = servers.get(SERVER_NAME)
    if target.existing is not None and not _is_pyrite_entry(target.existing):
        if not force:
            raise _taken(f"Claude Code's user config ({config})", target.existing)
        target.replaced = target.existing
    return target


def _claude_code_apply(target: _ClaudeTarget, args: list[str], manual: str) -> None:
    """`claude mcp remove` (when a `pyrite` is there) then `claude mcp add`,
    each judged by its exit code alone."""

    def run(*cmd: str) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                [str(target.claude), "mcp", *cmd],
                capture_output=True,
                text=True,
                timeout=_SUBPROCESS_TIMEOUT,
                stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise _SetupError(
                "CLIENT_COMMAND_FAILED",
                f"Could not run {target.claude}: {exc}",
                f"run it yourself: {manual}",
            ) from None

    if target.existing is not None:
        proc = run("remove", "-s", "user", SERVER_NAME)
        if proc.returncode != 0:
            raise _SetupError(
                "CLIENT_COMMAND_FAILED",
                f"`claude mcp remove -s user {SERVER_NAME}` failed: "
                f"{(proc.stderr or proc.stdout).strip()}",
                f"remove it yourself, then run: {manual}",
            )
    proc = run("add", "-s", "user", SERVER_NAME, "--", *args)
    if proc.returncode != 0:
        raise _SetupError(
            "CLIENT_COMMAND_FAILED",
            f"`claude mcp add` failed: {(proc.stderr or proc.stdout).strip()}",
            f"if a user-scope {SERVER_NAME!r} is already there, `claude mcp remove -s user "
            f"{SERVER_NAME}` first; then run: {manual}",
        )


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
        raise _SetupError(
            "MCP_COMMAND_FAILED",
            f"{command} mcp --help failed, so no client could start it: {detail}",
            "reinstall pyrite into this environment (the MCP server is a core "
            "dependency) and re-run; nothing was written",
        )


def _desktop_env() -> dict[str, str]:
    """PYRITE_CONFIG_DIR / PYRITE_DATA_DIR as this process has them, absolute.

    #582 groom, open question 3. Claude Desktop starts the server with a
    limited environment and an undefined cwd (`/` on macOS), so without these
    it loads ~/.pyrite, not the config the user just set up with. Only an
    explicit variable is pinned: a repo-local .pyrite/ is untrusted, and
    pinning it would make it trusted.
    """
    return {
        var: str(Path(os.environ[var]).expanduser().absolute())
        for var in CONFIG_ENV_VARS
        if os.environ.get(var)
    }


def _print_report(out: Console, tier: str, args: list[str], result: _Result) -> None:
    out.print(
        Text(
            f"Pyrite MCP server, {tier} tier ({mcp_tool_counts()[tier]} tools): {shlex.join(args)}"
        )
    )
    if result.configured:
        out.print(Text("Configured:"))
        for item in result.configured:
            out.print(Text(f"  {item['label']}: {item['location']}"))
    if result.failed:
        out.print(Text("Failed:"))
        for item in result.failed:
            out.print(Text(f"  {item['label']}: [{item['error_code']}] {item['error']}"))
            out.print(Text(f"    hint: {item['suggestion']}"))
    for note in result.notes:
        out.print(Text(f"Note: {note}"))
    if result.configured:
        out.print(
            Text("Restart the client to load it. `pyrite mcp --help` lists each tier's tools.")
        )


def mcp_setup(
    client: str | None = typer.Option(
        None,
        "--client",
        help="claude-code or claude-desktop. Default: every client found on this machine.",
    ),
    tier: str = typer.Option(
        "write",
        "--tier",
        "-t",
        help="Tool tier the client gets: read, write or admin.",
    ),
    project: bool = typer.Option(
        False,
        "--project",
        help="Claude Code project scope: write .mcp.json in the current directory (run it "
        "where you start `claude`; it does not look for a repository root) instead of "
        "running `claude mcp add`.",
    ),
    config_path: Path | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Write the entry into this mcpServers JSON file instead (any MCP client).",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Replace a server named 'pyrite' that mcp-setup did not write (it says what "
        "it replaced). Without it, such a server is left alone and the command stops.",
    ),
    output_format: str = typer.Option(
        "rich",
        "--format",
        "-f",
        callback=validate_output_format,
        help="Output format: rich (default) or json and the other registered formats.",
    ),
):
    """
    Register the Pyrite MCP server with Claude Code and Claude Desktop.

    Claude Code gets it through `claude mcp add -s user` (stored in
    ~/.claude.json), or in ./.mcp.json with --project. Claude Desktop gets it
    in its claude_desktop_config.json (macOS: ~/Library/Application
    Support/Claude/, Windows: %APPDATA%\\Claude\\). The entry runs this
    install's pyrite by absolute path with an explicit --tier (default: write).
    Each client is reported as configured or failed; the exit code is 1 if
    any failed. Restart the client afterwards.
    """
    from ..services.access_policy import ROLES

    fmt = output_format
    out = Console(soft_wrap=True, highlight=False)

    def stop(failure: _SetupError, extra: dict | None = None) -> None:
        cli_error(
            failure.message,
            fmt,
            error_code=failure.error_code,
            suggestion=failure.suggestion,
            extra=extra,
        )

    if tier not in ROLES:
        stop(
            _SetupError(
                "INVALID_TIER", f"Invalid tier: {tier!r}", f"choose one of: {', '.join(ROLES)}"
            )
        )
    if client is not None and client not in CLIENTS:
        stop(
            _SetupError(
                "INVALID_OPTION",
                f"Unknown client: {client!r}",
                f"choose one of: {', '.join(CLIENTS)}",
            )
        )
    if project and client == CLAUDE_DESKTOP:
        stop(
            _SetupError(
                "INVALID_OPTION",
                "--project is Claude Code's project scope (.mcp.json); Claude Desktop has none",
                "drop --project, or use --client claude-code",
            )
        )
    if config_path is not None and (project or client):
        stop(
            _SetupError(
                "INVALID_OPTION",
                "--config writes one named file; it does not combine with --client or --project",
                "use --config alone",
            )
        )

    command = pyrite_executable()
    if command is None:
        stop(
            _SetupError(
                "PYRITE_EXECUTABLE_NOT_FOUND",
                f"Cannot find the `pyrite` script of this install (Python: {sys.executable})",
                'reinstall with pip (`pip install -e ".[cli]"` in a checkout) so the '
                "`pyrite` script exists, then run that script's mcp-setup",
            )
        )
    try:
        _verify_starts(command)
    except _SetupError as failure:
        stop(failure)

    args = [str(command), "mcp", "--tier", tier]
    manual_claude = shlex.join(["claude", "mcp", "add", "-s", "user", SERVER_NAME, "--", *args])
    desktop_env = _desktop_env()
    desktop_entry = {"command": str(command), "args": args[1:], "env": desktop_env}
    result = _Result()

    # Phase 1, nothing changed yet: find the clients and read and check every
    # file (read-only, not JSON, not UTF-8, a 'pyrite' that is not ours). Any
    # problem here stops the run before a client has been touched.
    files: list[_FileTarget] = []
    claude_target: _ClaudeTarget | None = None
    try:
        if config_path is not None:
            files.append(
                _prepare_file(
                    "config", "Config file", config_path.expanduser(), desktop_entry, force
                )
            )
        elif project:
            mcp_json = Path.cwd() / ".mcp.json"
            entry = {"type": "stdio", "command": str(command), "args": args[1:], "env": {}}
            files.append(
                _prepare_file(CLAUDE_CODE, "Claude Code (project scope)", mcp_json, entry, force)
            )
            result.notes.append(
                f"{mcp_json} holds this machine's path to pyrite; commit it only if everyone "
                "who opens the project has pyrite installed at that path."
            )
        else:
            desktop = desktop_config_path()
            claude = find_claude_code() if client in (None, CLAUDE_CODE) else None
            if client == CLAUDE_CODE and claude is None:
                stop(
                    _SetupError(
                        "CLIENT_NOT_FOUND",
                        "Claude Code's `claude` command was not found (on PATH, ~/.local/bin "
                        "or ~/.claude/local)",
                        "install Claude Code and re-run, or write ./.mcp.json with "
                        f"`pyrite mcp-setup --project`, or run: {manual_claude}",
                    )
                )
            if claude is not None:
                claude_target = _prepare_claude_code(claude, force)
            if client == CLAUDE_DESKTOP or (client is None and desktop.parent.is_dir()):
                if not desktop.parent.is_dir():
                    result.notes.append(
                        f"{desktop.parent} did not exist yet; Claude Desktop reads the entry "
                        "once it is installed."
                    )
                if sys.platform not in ("darwin", "win32"):
                    result.notes.append(
                        "Claude Desktop has no official Linux build; this is the path "
                        "unofficial builds use."
                    )
                files.append(
                    _prepare_file(CLAUDE_DESKTOP, "Claude Desktop", desktop, desktop_entry, force)
                )
            if claude_target is None and not files:
                manual = {"mcpServers": {SERVER_NAME: desktop_entry}}
                if fmt == "rich":
                    out.print(Text("No MCP client found. To add the Pyrite server by hand, run:"))
                    out.print(Text(f"  {manual_claude}"))
                    out.print(
                        Text(
                            "or put this in your client's MCP config "
                            f"(Claude Desktop: {desktop}; a project: .mcp.json):"
                        )
                    )
                    out.print(Text(json.dumps(manual, indent=2, ensure_ascii=False)))
                stop(
                    _SetupError(
                        "CLIENT_NOT_FOUND",
                        f"No MCP client found: no `claude` command, and no {desktop.parent}",
                        "install Claude Code or Claude Desktop and re-run, or use "
                        "--client, --project or --config PATH",
                    ),
                    extra={"manual": manual, "manual_claude_code": manual_claude},
                )
    except _SetupError as failure:
        stop(failure)

    if any(f.entry is desktop_entry for f in files):
        if desktop_env:
            pinned = ", ".join(f"{k}={v}" for k, v in desktop_env.items())
            result.notes.append(f"The entry pins {pinned}: a GUI client does not see your shell's.")
        else:
            from ..config import resolve_config_source

            source, trusted = resolve_config_source()
            if not trusted:
                result.notes.append(
                    f"Claude Desktop starts the server outside this directory, so it loads "
                    f"~/.pyrite, not {source}."
                )

    # Phase 2: change each client. A failure here does not undo a client
    # already changed and does not stop the next one: each is reported as
    # configured or failed, and the exit code is 1 if any failed.
    def record_replaced(where: str, replaced: object) -> None:
        if replaced is not None:
            result.notes.append(
                f"Replaced your {SERVER_NAME!r} server in {where} (--force): {_describe(replaced)}"
            )

    if claude_target is not None:
        label = "Claude Code (user scope, via `claude mcp add`)"
        location = str(claude_target.config)
        try:
            _claude_code_apply(claude_target, args, manual_claude)
        except _SetupError as failure:
            result.failed.append(
                {
                    "client": CLAUDE_CODE,
                    "scope": "user",
                    "label": label,
                    "location": location,
                    "error_code": failure.error_code,
                    "error": failure.message,
                    "suggestion": failure.suggestion,
                }
            )
        else:
            result.configured.append(
                {"client": CLAUDE_CODE, "scope": "user", "label": label, "location": location}
            )
            record_replaced(location, claude_target.replaced)
    for target in files:
        item = {"client": target.client, "label": target.label, "location": str(target.path)}
        if target.client == CLAUDE_CODE:
            item["scope"] = "project"
        try:
            _write_file(target)
        except _SetupError as failure:
            result.failed.append(
                {
                    **item,
                    "error_code": failure.error_code,
                    "error": failure.message,
                    "suggestion": failure.suggestion,
                }
            )
            continue
        if target.path.is_symlink():
            item["written_to"] = str(target.real)
        result.configured.append(item)
        record_replaced(str(target.path), target.replaced)
        if target.stale:
            result.notes.append(
                f"Removed {', '.join(target.stale)} from {target.path} "
                "(left by the old `pyrite-admin mcp-setup`)."
            )

    if fmt == "rich":
        _print_report(out, tier, args, result)
    else:
        typer.echo(
            format_output(
                {
                    "tier": tier,
                    "tools": mcp_tool_counts()[tier],
                    "command": args,
                    "configured": result.configured,
                    "failed": result.failed,
                    "notes": result.notes,
                },
                fmt,
            )
        )
    if result.failed:
        raise typer.Exit(1)
