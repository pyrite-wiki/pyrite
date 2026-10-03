"""`pyrite mcp-setup`: point each MCP client's `pyrite` entry at this install (#582).

Pyrite is a guest in the files this command touches (see
kb/standards/pyrite-is-a-guest-in-state-it-does-not-own.md). Running it is the
user asking for one thing: make the `pyrite` entry start this install. What to
do is decided from the file as it is. No record is kept of what Pyrite wrote,
and nothing is inferred from what an entry looks like.

    no `pyrite` entry                 write the default entry
    equal in what was asked           write nothing (the command path; the
                                      tier when --tier is given)
    differs in what was asked         change exactly that; report old and new
    carries anything else             keep it (env, extra args, a tier nobody
                                      asked to change, unknown keys)
    args do not begin with `mcp`      stop: a new command path cannot be
                                      swapped into it; --force is named
    cannot be read or written back    stop, file untouched

`--force` means one thing: discard the entry and write the default one.

Where each client reads its entry:

- Claude Code, user scope: ``~/.claude.json``. Claude Code rewrites that file
  continually, so Pyrite only reads it and changes it through ``claude mcp``.
  There is no update subcommand: a change is ``remove`` then ``add``, done
  only for an entry ``add`` can reproduce; after any failed call the file is
  read back, so the report says what it holds, not what was hoped.
- Claude Code, project scope (``--project``): ``./.mcp.json``.
- Claude Desktop: its per-OS ``claude_desktop_config.json``.
- ``--config PATH``: any client's ``mcpServers`` file.

The three files Pyrite writes itself are parsed, changed in the one entry and
written back in the file's own style (`_detect_style`). Before the replace the
output is parsed again and compared with the original, value for value and in
order, outside the `pyrite` entry (`_check_preserved`); a difference stops the
run with the file untouched. A file a client wrote comes back byte for byte.
A hand-formatted file keeps every value but may have some respelled:
one-line arrays and objects open out, mixed indentation or line endings
become uniform, ``\\/`` becomes ``/``, a ``\\u`` escape may become its
character, and a number is spelled Python's way (``1.10`` as ``1.1``). ``tests/test_mcp_setup_reads_back.py`` pins both.
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
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from functools import partial
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.text import Text

from ..utils.atomic_write import atomic_write_text
from ..utils.errors import cli_error
from .output import format_output, validate_output_format

SERVER_NAME = "pyrite"
CLAUDE_CODE = "claude-code"
CLAUDE_DESKTOP = "claude-desktop"
CLIENTS = (CLAUDE_CODE, CLAUDE_DESKTOP)
DESKTOP_CONFIG_NAME = "claude_desktop_config.json"

#: The names the removed `pyrite-admin mcp-setup` registered, one per tier.
#: Reported when present; never removed, whatever they hold.
STALE_TRIO = ("pyrite-read", "pyrite-write", "pyrite-admin")

#: Environment variables that name the config explicitly (config.py
#: resolve_config_source, step 1). A GUI-launched server never sees the shell's.
CONFIG_ENV_VARS = ("PYRITE_CONFIG_DIR", "PYRITE_DATA_DIR")

_SUBPROCESS_TIMEOUT = 60
_ABSENT = object()  # no `pyrite` member (a member holding null is not absent)
_UNKNOWN = object()  # Claude Code's file could not be read back
_JSON_WHITESPACE = " \t\r\n"
_REMOVE = f"claude mcp remove -s user {SERVER_NAME}"


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
    2.1.287). Read only, never written: `claude mcp` writes it."""
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


class _CallFailedError(Exception):
    """A `claude mcp` call that did not exit 0, timed out or could not run."""


class _StopError(Exception):
    """One client was not changed (``stopped``: it is as it was) or was left
    part-way (``failed``: `extra` says exactly how). Carries ADR-0037's shape."""

    def __init__(
        self,
        error_code: str,
        message: str,
        suggestion: str,
        *,
        status: str = "stopped",
        **extra: object,
    ):
        super().__init__(message)
        self.error_code, self.message, self.suggestion = error_code, message, suggestion
        self.status, self.extra = status, extra


# -- reading an entry and deciding what was asked -----------------------------------

_BY_HAND = "edit the entry by hand, or re-run with --force to discard it and write the default one"


def _json_type(value: Any) -> str:
    names = {dict: "an object", list: "an array", str: "a string", bool: "a boolean"}
    return "null" if value is None else names.get(type(value), "a number")


def _command_and_args(existing: Any) -> tuple[str, list[str]]:
    """(command, args) of an entry shaped like a stdio server: an object with
    a string command and a list of string args."""
    if not isinstance(existing, dict):
        raise _StopError(
            "ENTRY_MALFORMED",
            f"the existing {SERVER_NAME!r} entry is {_json_type(existing)}, not an object",
            _BY_HAND,
        )
    command, args = existing.get("command"), existing.get("args")
    if not isinstance(command, str) or not command:
        raise _StopError(
            "ENTRY_MALFORMED",
            f'the existing {SERVER_NAME!r} entry has no "command" string',
            _BY_HAND,
        )
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise _StopError(
            "ENTRY_MALFORMED",
            f'"args" of the existing {SERVER_NAME!r} entry is not a list of strings',
            _BY_HAND,
        )
    return command, args


def _read_entry(existing: Any) -> tuple[str, list[str]]:
    """(command, args) of an entry whose command path can be swapped: its args
    begin with `mcp`. This reads the entry in order to change it; it does not
    decide whose it is."""
    command, args = _command_and_args(existing)
    if args[:1] != ["mcp"]:
        raise _StopError(
            "ENTRY_NOT_MCP",
            f"the existing {SERVER_NAME!r} entry runs `{shlex.join([command, *args])}`; its "
            "arguments do not begin with `mcp`, so they would not start the server under "
            "this install's command path",
            _BY_HAND,
        )
    return command, args


def _tier_slot(args: list[str]) -> tuple[int, str] | None:
    """Where the tier value sits in `args` and the prefix it is written with:
    ``--tier X`` or ``--tier=X``, and ``-t X``, which `pyrite-admin mcp` reads
    and `pyrite mcp` does not (`_decide` rewrites it). No attached ``-tX``."""
    for i, arg in enumerate(args[1:], 1):
        if arg in ("--tier", "-t"):
            if i + 1 >= len(args):
                raise _StopError(
                    "ENTRY_MALFORMED",
                    f"the existing {SERVER_NAME!r} entry ends with `{arg}` and no tier",
                    _BY_HAND,
                )
            return i + 1, ""
        if arg.startswith("--tier="):
            return i, "--tier="
    return None


def _with_tier(args: list[str], tier: str) -> list[str]:
    """`args` serving `tier`, in the form the entry already uses; everything
    else in place."""
    slot = _tier_slot(args)
    if slot is None:
        return [args[0], "--tier", tier, *args[1:]]
    index, prefix = slot
    return [*args[:index], prefix + tier, *args[index + 1 :]]


def _normal(entry: Any) -> Any:
    """An entry under the clients' normal form: `"type": "stdio"` and an empty
    `env` say nothing (Claude Code writes both; a hand-written entry has neither)."""
    if not isinstance(entry, dict):
        return entry
    return {
        k: v
        for k, v in entry.items()
        if not (k == "type" and v == "stdio") and not (k == "env" and v == {})
    }


def _public(entry: Any) -> dict:
    """An entry for the report: env and unknown keys by name, never by value."""
    if not isinstance(entry, dict):
        return {"value": _json_type(entry)}
    shown = {k: entry[k] for k in ("command", "args") if k in entry}
    if entry.get("type") not in (None, "stdio"):
        shown["type"] = entry["type"]
    env = entry.get("env")
    if isinstance(env, dict) and env:
        shown["env_keys"] = list(env)
    other = [k for k in entry if k not in ("command", "args", "env", "type")]
    if other:
        shown["other_keys"] = other
    return shown


def _carried(entry: dict, args: list[str], tier_asked: bool) -> list[str]:
    """What an entry holds beyond a command path and `mcp`: kept on the
    ordinary path, discarded by --force."""
    carried = []
    slot = None
    with contextlib.suppress(_StopError):
        slot = _tier_slot(args)
    used = {0}
    if slot is not None:
        index, prefix = slot
        used |= {index} if prefix else {index - 1, index}
        if not tier_asked:
            carried.append(f"tier {args[index][len(prefix) :]}")
    extra = [a for i, a in enumerate(args) if i not in used]
    if extra:
        carried.append("args " + shlex.join(extra))
    env = entry.get("env")
    if isinstance(env, dict) and env:
        carried.append("env " + ", ".join(env))
    elif env not in (None, {}):
        carried.append("env (not an object)")
    other = [k for k in entry if k not in ("command", "args", "env", "type")]
    if entry.get("type") not in (None, "stdio"):
        other.append("type")
    if other:
        carried.append("keys " + ", ".join(other))
    return carried


def _held(existing: Any) -> list[str]:
    """Everything an entry holds, for the report of what --force discarded."""
    if not isinstance(existing, dict):
        return [f"the entry ({_json_type(existing)})"]
    held = [f"command {existing.get('command')}"]
    try:
        held.append("args " + shlex.join(_command_and_args({**existing, "command": "x"})[1]))
    except _StopError:
        held.append("args (not a list of strings)")
    return held + _carried(existing, [], True)


@dataclass
class _Plan:
    status: str  # created | unchanged | changed
    entry: dict | None = None  # what the member holds afterwards; None: no write
    merged: bool = False  # `entry` is the existing one with fields changed
    changes: list[dict] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    discarded: list[str] = field(default_factory=list)


def _decide(existing: Any, default: dict, tier: str | None, force: bool) -> _Plan:
    """The rule in the module docstring. `tier` is None when --tier was not
    given: the entry's own tier is then not this run's to change."""
    if existing is _ABSENT:
        return _Plan("created", default)
    if force:
        if _normal(existing) == _normal(default):
            return _Plan("unchanged")
        old = _public(existing)
        discarded = _held(existing)
        change = {"field": "entry", "old": old, "new": _public(default)}
        return _Plan("changed", default, changes=[change], discarded=discarded)
    command, args = _read_entry(existing)
    new_args = _with_tier(args, tier) if tier else args
    slot = _tier_slot(new_args)
    if (command != default["command"] or new_args != args) and slot and slot[1] == "":
        # `pyrite-admin mcp` reads `-t X`, `pyrite mcp` only `--tier X`. The
        # entry is changing anyway, so the tier is written the way it reads.
        flag = slot[0] - 1
        new_args = [*new_args[:flag], "--tier", *new_args[flag + 1 :]]
    changes = []
    if command != default["command"]:
        changes.append({"field": "command", "old": command, "new": default["command"]})
    if new_args != args:
        changes.append({"field": "args", "old": args, "new": new_args})
    kept = _carried(existing, args, tier is not None)
    if not changes:
        return _Plan("unchanged", kept=kept)
    entry = {**existing, "command": default["command"], "args": new_args}
    return _Plan("changed", entry, merged=True, changes=changes, kept=kept)


# -- a file Pyrite writes: parse, change one entry, write back in its own style ------


@dataclass(frozen=True)
class _Style:
    """How a file is laid out. The default is what the clients write: 2
    spaces, LF, no trailing newline, non-ASCII raw (#582 spike)."""

    bom: bool = False
    indent: str | None = "  "  # None: the whole document on one line
    eol: str = "\n"
    head: str = ""  # whitespace before the opening brace
    tail: str = ""  # whitespace after the closing brace
    ascii_only: bool = False
    spaced: bool = True  # one-line documents: `, ` and `: `, or `,` and `:`


def _detect_style(text: str, bom: bool, empty: bool) -> _Style:
    """The style of `text`, read off the file: the first indented line gives
    the indent unit. A file with mixed indentation is laid out again in that
    unit (its values are unchanged). An `empty` document (`{}`) has no layout
    to follow and gets the clients'."""
    body = text.strip(_JSON_WHITESPACE)
    if not body:
        return _Style(bom=bom)
    head = text[: len(text) - len(text.lstrip(_JSON_WHITESPACE))]
    tail = text[len(text.rstrip(_JSON_WHITESPACE)) :]
    eol = "\r\n" if "\r\n" in text else "\n"
    if empty:
        return _Style(bom=bom, eol=eol, head=head, tail=tail)
    indent: str | None = None
    if "\n" in body:
        indent = "  "
        for line in body.splitlines()[1:]:
            stripped = line.lstrip(" \t")
            if stripped and stripped != line:
                indent = line[: len(line) - len(stripped)]
                break
    return _Style(
        bom=bom,
        indent=indent,
        eol=eol,
        head=head,
        tail=tail,
        ascii_only=text.isascii(),
        spaced='": ' in body or ", " in body,
    )


def _serialize(data: dict, style: _Style) -> str:
    if style.indent is None:
        separators = (", ", ": ") if style.spaced else (",", ":")
        out = json.dumps(
            data, ensure_ascii=style.ascii_only, allow_nan=False, separators=separators
        )
    else:
        out = json.dumps(data, indent=style.indent, ensure_ascii=style.ascii_only, allow_nan=False)
    # A JSON string cannot hold a raw newline, so every "\n" here is layout.
    if style.eol != "\n":
        out = out.replace("\n", style.eol)
    return ("\ufeff" if style.bom else "") + style.head + out + style.tail


class _DuplicateKeyError(ValueError):
    pass


def _no_duplicates(pairs: list) -> dict:
    seen: dict = {}
    for key, value in pairs:
        if key in seen:
            raise _DuplicateKeyError(key)
        seen[key] = value
    return seen


def _not_json(name: str):
    raise ValueError(f"{name} is not JSON")


def _parse(text: str, path: Path, *, exact: bool = False) -> dict:
    """The JSON object in `text`. Strict: no NaN or Infinity, and no duplicate
    key anywhere (a rewrite would silently keep only the last). `exact` reads
    numbers as Decimal, so a comparison sees every digit the file holds."""
    keep = f"nothing was written; fix {path} or move it aside, then re-run `pyrite mcp-setup`"
    try:
        data = json.loads(
            text if text.strip(_JSON_WHITESPACE) else "{}",
            object_pairs_hook=_no_duplicates,
            parse_constant=_not_json,
            parse_float=Decimal if exact else None,
        )
    except RecursionError:
        raise _StopError("CONFIG_INVALID", f"{path} is nested too deeply to read", keep) from None
    except _DuplicateKeyError as dup:
        raise _StopError(
            "CONFIG_INVALID",
            f"{path} has a duplicate key {dup.args[0]!r}; a rewrite would keep only the last",
            keep,
        ) from None
    except json.JSONDecodeError as exc:
        raise _StopError(
            "CONFIG_INVALID",
            f"{path} is not valid JSON (line {exc.lineno}, column {exc.colno}: {exc.msg})",
            keep,
        ) from None
    except ValueError as exc:
        raise _StopError("CONFIG_INVALID", f"{path} is not valid JSON ({exc})", keep) from None
    if not isinstance(data, dict):
        raise _StopError("CONFIG_INVALID", f"{path} holds {_json_type(data)}, not an object", keep)
    if not isinstance(data.get("mcpServers", {}), dict):
        raise _StopError("CONFIG_INVALID", f'"mcpServers" in {path} is not an object', keep)
    return data


@dataclass
class _Loaded:
    raw: bytes | None  # None: no file yet
    text: str
    data: dict
    style: _Style


def _load(path: Path) -> _Loaded:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return _Loaded(None, "", {}, _Style())
    except OSError as exc:
        raise _StopError(
            "CONFIG_NOT_READABLE", f"Cannot read {path}: {exc}", "nothing was written"
        ) from None
    bom = raw.startswith(b"\xef\xbb\xbf")
    try:
        text = raw[3 if bom else 0 :].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _StopError(
            "CONFIG_INVALID",
            f"{path} is not valid UTF-8 (byte {exc.start}: {exc.reason})",
            f"nothing was written; fix {path} or move it aside, then re-run",
        ) from None
    data = _parse(text, path)
    return _Loaded(raw, text, data, _detect_style(text, bom, empty=not data))


def _same(a: Any, b: Any) -> bool:
    """Equal in value, type and key order."""
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return list(a) == list(b) and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b, strict=True))
    return a == b


def _outside(data: dict) -> list:
    """Everything in a config except the `pyrite` entry, order kept."""
    servers = data.get("mcpServers", {})
    return [
        [[k, v] for k, v in data.items() if k != "mcpServers"],
        [[k, v] for k, v in servers.items() if k != SERVER_NAME],
    ]


def _not_preserved(path: Path) -> _StopError:
    return _StopError(
        "CONFIG_NOT_PRESERVED",
        f"{path} cannot be written back with every other value intact (a number with more "
        "digits or range than a float holds, or text that cannot be stored as UTF-8)",
        "nothing was written; add the entry by hand "
        "(`pyrite mcp-setup --config <scratch.json>` shows it)",
    )


def _check_preserved(loaded: _Loaded, new_text: str, plan: _Plan, path: Path) -> None:
    """The guarantee, checked on the bytes about to be written: parsed again,
    they hold every other value the file held, in the same order (numbers
    compared digit for digit as Decimal), and the `pyrite` entry is the
    intended one. Anything else writes nothing."""
    lost = _not_preserved(path)
    try:
        new_text.encode("utf-8")
        new = _parse(new_text.removeprefix("\ufeff"), path, exact=True)
    except (UnicodeEncodeError, _StopError):
        raise lost from None
    old = _parse(loaded.text, path, exact=True)
    wanted: Any = plan.entry
    if plan.merged:
        was = old["mcpServers"][SERVER_NAME]
        wanted = {**was, "command": plan.entry["command"], "args": plan.entry["args"]}
    if not _same(_outside(old), _outside(new)) or not _same(
        new["mcpServers"].get(SERVER_NAME), wanted
    ):
        raise lost


def _check_replaceable(real: Path) -> None:
    """Stop where `atomic_write_text` would write in place instead of
    replacing (a hard-linked file, a directory it cannot create a file in, a
    file another user owns): in place is not all-or-nothing, and for a guest
    file that is the property. A read-only file is the user saying no. Not
    caught: a file whose group this user cannot give a new file (also written
    in place)."""
    try:
        st = os.stat(real)
    except FileNotFoundError:
        return
    why = None
    if not stat.S_IMODE(st.st_mode) & 0o222 or not os.access(real, os.W_OK):
        why = "is read-only"
    elif st.st_nlink > 1:
        why = f"has {st.st_nlink} hard links, so it cannot be replaced atomically under every name"
    elif hasattr(os, "geteuid") and os.geteuid() not in (0, st.st_uid):
        why = "belongs to another user, so it cannot be replaced atomically"
    elif not os.access(real.parent, os.W_OK | os.X_OK):
        why = "is in a directory this user cannot write, so it cannot be replaced atomically"
    if why:
        raise _StopError(
            "CONFIG_NOT_WRITABLE",
            f"{real} {why}",
            "nothing was written; fix that and re-run, or add the entry by hand "
            "(`pyrite mcp-setup --config <scratch.json>` shows it)",
        )


def _record(item: dict, plan: _Plan, existing: Any) -> None:
    item["status"] = plan.status
    for key in ("changes", "kept", "discarded"):
        if getattr(plan, key):
            item[key] = getattr(plan, key)
    final = plan.entry if plan.entry is not None else existing
    item["entry"] = _public(final)
    # The tier the entry serves, read from the entry: not --tier, which an
    # existing entry keeps unless asked. With no tier argument the server
    # starts at its default, write (ADR-0006).
    with contextlib.suppress(_StopError):
        args = _read_entry(final)[1]
        slot = _tier_slot(args)
        item["tier"] = args[slot[0]][len(slot[1]) :] if slot else "write"
        item["tier_named"] = slot is not None


def _env_notes(entry: Any, pins: dict[str, str]) -> list[str]:
    """Decision 1 on #582: env is never added to an entry that exists."""
    env = entry.get("env") if isinstance(entry, dict) else None
    env = env if isinstance(env, dict) else {}
    notes = []
    for var, value in pins.items():
        if env.get(var) == value:
            continue
        has = "names a different one, which the server will use" if var in env else "does not"
        notes.append(
            f"Your shell sets {var}={value} and the existing entry {has}. A server this client "
            "starts never sees your shell's value, and mcp-setup never adds env to an entry "
            f'that exists. To pin it, add by hand: "env": {{{json.dumps(var)}: '
            f"{json.dumps(value, ensure_ascii=False)}}}"
        )
    return notes


def _setup_file(
    item: dict,
    path: Path,
    default: dict,
    tier: str | None,
    force: bool,
    pins: dict[str, str],
    private_if_new: bool,
) -> None:
    """Change the `pyrite` entry of one file, or leave the file untouched."""
    real = Path(os.path.realpath(path))
    loaded = _load(real)
    servers = loaded.data.get("mcpServers", {})
    stale = [name for name in STALE_TRIO if name in servers]
    if stale:
        item["stale"] = stale
        item["notes"].append(
            f"{', '.join(stale)} in {path}: the names the removed `pyrite-admin mcp-setup` "
            "used. mcp-setup never removes them; if they are not yours, delete those members "
            "from the file by hand."
        )
    existing = servers.get(SERVER_NAME, _ABSENT)
    plan = _decide(existing, default, tier, force)
    if plan.merged or plan.status == "unchanged":
        item["notes"] += _env_notes(existing, pins)
    if plan.entry is None:
        _record(item, plan, existing)
        return

    if plan.merged:
        _verify_starts(Path(plan.entry["command"]), plan.entry["args"], entry=True)
    data = {**loaded.data}
    data["mcpServers"] = {**servers, SERVER_NAME: plan.entry}
    try:
        text = _serialize(data, loaded.style)
        _check_preserved(loaded, text, plan, path)
    except RecursionError:
        # json.loads reads deeper than the encoder or the comparison can walk.
        raise _StopError(
            "CONFIG_INVALID",
            f"{path} is nested too deeply to write back and check",
            "nothing was written; add the entry by hand "
            "(`pyrite mcp-setup --config <scratch.json>` shows it)",
        ) from None
    except ValueError:  # 1e400 parses to infinity, which JSON cannot hold
        raise _not_preserved(path) from None
    if loaded.data and _serialize(loaded.data, loaded.style) != (
        ("\ufeff" if loaded.style.bom else "") + loaded.text
    ):
        item["notes"].append(
            f"{path} was not laid out the way a client writes it, so some values outside "
            "the entry were respelled (one-line arrays opened out, mixed indentation made "
            "uniform, escapes or number spellings normalised). Every value is equal to what "
            "it was."
        )
    _check_replaceable(real)
    # The narrowest window this command can give another writer: read again
    # right before the replace, and do not write over a file that moved.
    try:
        now: bytes | None = real.read_bytes()
    except FileNotFoundError:
        now = None
    if now != loaded.raw:
        raise _StopError(
            "CONFIG_CHANGED",
            f"{path} changed while mcp-setup was running",
            "nothing was written; re-run `pyrite mcp-setup`",
        )
    made = [p for p in (real.parent, *real.parent.parents) if not p.exists()]
    try:
        real.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(real, text)
    except BaseException as exc:
        for directory in made:
            with contextlib.suppress(OSError):
                directory.rmdir()
        if not isinstance(exc, OSError):
            raise
        raise _StopError(
            "CONFIG_WRITE_FAILED",
            f"Could not write {real}: {exc}",
            "the previous file is unchanged; fix the cause and re-run",
        ) from None
    if loaded.raw is None and private_if_new:
        with contextlib.suppress(OSError):
            os.chmod(real, 0o600)  # as Claude Desktop creates its own config
    if Path(path).is_symlink():
        item["written_to"] = str(real)
    _record(item, plan, existing)
    if not plan.merged and pins:
        item["pinned"] = pins


# -- Claude Code user scope: read its file, change it only through `claude` ----------


def _read_claude_config(config: Path) -> tuple[Any, list[str]]:
    """(the user-scope `pyrite` entry or _ABSENT, stale trio names), read the
    way the client reads it. Stops when the file exists and the client would
    not accept it: Claude Code 2.1.287 declares an empty or unparseable
    ``~/.claude.json`` corrupted and replaces it with fresh state on any
    `claude mcp` call, a failed one included (#582 spike)."""
    try:
        raw = config.read_bytes()
    except FileNotFoundError:
        return _ABSENT, []
    except OSError as exc:
        raise _StopError(
            "CONFIG_NOT_READABLE", f"Cannot read {config}: {exc}", "`claude` was not run"
        ) from None
    data: Any = None
    with contextlib.suppress(OSError, ValueError, RecursionError):
        data = json.loads(raw.decode("utf-8-sig"), parse_constant=_not_json)
    if not isinstance(data, dict) or not isinstance(data.get("mcpServers", {}), dict):
        raise _StopError(
            "CLIENT_CONFIG_UNSAFE",
            f"{config} is empty or is not the JSON object Claude Code expects. Claude Code "
            "replaces such a file with fresh state on any `claude mcp` call, so `claude` was "
            "not run",
            "repair or restore the file (Claude Code keeps backups in ~/.claude/backups/), "
            "or move it aside and start Claude Code once; then re-run `pyrite mcp-setup`",
        )
    servers = data.get("mcpServers", {})
    return servers.get(SERVER_NAME, _ABSENT), [name for name in STALE_TRIO if name in servers]


def _add_command(entry: dict, env_keys: tuple = ()) -> str:
    env = [part for key in env_keys for part in ("-e", f"{key}=<its value>")]
    argv = ["claude", "mcp", "add", "-s", "user", SERVER_NAME, *env, "--"]
    return shlex.join([*argv, entry["command"], *entry["args"]])


def _add_reproduces(entry: Any) -> bool:
    """Whether `claude mcp add -- command args` writes this entry back whole:
    nothing but a stdio command and args. Env would need its values on a
    command line, and unknown keys cannot be passed at all."""
    try:
        _command_and_args(entry)
    except _StopError:
        return False
    return set(_normal(entry)) <= {"command", "args"}


def _setup_claude_code(
    item: dict, claude: Path, default: dict, tier: str | None, force: bool
) -> None:
    """Change the user-scope entry through `claude mcp`, all or nothing: each
    call is judged by its exit code and, when that is not 0, by reading the
    file back. Never by message text."""
    config = Path(item["location"])
    existing, stale = _read_claude_config(config)
    if stale:
        item["stale"] = stale
        item["notes"].append(
            f"{', '.join(stale)} in Claude Code's user config: the names the removed "
            "`pyrite-admin mcp-setup` used. mcp-setup never removes them; if they are not "
            "yours, run `claude mcp remove -s user <name>` for each."
        )
    plan = _decide(existing, default, tier, force)
    if plan.entry is None:
        _record(item, plan, existing)
        return
    wanted = plan.entry
    manual = _add_command(wanted)
    if existing is not _ABSENT and not force and not _add_reproduces(existing):
        env = existing.get("env") if isinstance(existing.get("env"), dict) else {}
        other = [k for k in _normal(existing) if k not in ("command", "args", "env")]
        by_hand = [_REMOVE, _add_command(wanted, tuple(env))]
        if other:
            by_hand.append(f"then put back in {config} by hand: {', '.join(other)}")
        raise _StopError(
            "CLIENT_ENTRY_NEEDS_HAND_EDIT",
            f"the user-scope {SERVER_NAME!r} entry carries {'; '.join(plan.kept)}. Claude Code "
            "has no command that changes an entry: it must be removed and added again, and "
            "`claude mcp add` would need env values on a command line and drops other keys. "
            "`claude` was not run",
            "run the commands in by_hand yourself, or edit the command path in "
            f"{config} (--force discards what the entry carries)",
            by_hand=by_hand,
            entry=_public(existing),
        )
    if plan.merged:
        _verify_starts(Path(wanted["command"]), wanted["args"], entry=True)
    # Claude Code rewrites this file all the time, so what must not have moved
    # since the decision is the entry, not the bytes.
    again, _ = _read_claude_config(config)
    if (again is _ABSENT) != (existing is _ABSENT) or (again is not _ABSENT and again != existing):
        raise _StopError(
            "CONFIG_CHANGED",
            f"the {SERVER_NAME!r} entry in {config} changed while mcp-setup was running",
            "`claude` was not run; re-run `pyrite mcp-setup`",
        )

    def run(*cmd: str) -> tuple[bool, str]:
        """One `claude mcp` call: (exit 0, its last words). Never raises: a
        call that cannot be made is a failed call, judged like any other."""
        item["_called"] = True
        try:
            proc = subprocess.run(
                [str(claude), "mcp", *cmd],
                capture_output=True,
                # Not the locale's codec: Windows would read Node's UTF-8 as
                # cp1252, and one undecodable byte must not end the sequence.
                encoding="utf-8",
                errors="replace",
                timeout=_SUBPROCESS_TIMEOUT,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            return False, f"timed out after {_SUBPROCESS_TIMEOUT}s"
        except Exception as exc:  # noqa: BLE001 - OSError; ValueError for a NUL in an entry's args
            return False, f"could not be run ({type(exc).__name__}: {exc})"
        return proc.returncode == 0, (proc.stderr or proc.stdout).strip()[-500:]

    def holds() -> Any:
        """What the file holds now. Never raises: unreadable is _UNKNOWN."""
        try:
            return _read_claude_config(config)[0]
        except Exception:  # noqa: BLE001
            return _UNKNOWN

    def is_entry(now: Any, entry: Any) -> bool:
        return isinstance(now, dict) and _normal(now) == _normal(entry)

    def call(*cmd: str) -> None:
        ok, detail = run(*cmd)
        if not ok:
            raise _CallFailedError(f"`claude mcp {cmd[0]}` failed: {detail}")

    def why(exc: Exception) -> str:
        return str(exc) if isinstance(exc, _CallFailedError) else f"{type(exc).__name__}: {exc}"

    add_new = ["add", "-s", "user", SERVER_NAME, "--", wanted["command"], *wanted["args"]]
    if existing is _ABSENT:
        try:
            call(*add_new)
        except Exception as exc:  # noqa: BLE001 - nothing was removed; say what the file holds
            failure, now = why(exc), holds()
            if now is _ABSENT:
                raise _StopError(
                    "CLIENT_COMMAND_FAILED",
                    f"{failure}; nothing was added",
                    f"run it yourself: {manual}",
                ) from None
            if not is_entry(now, wanted):
                raise _StopError(
                    "CLIENT_COMMAND_FAILED",
                    f"{failure}, and {config} could not be read back to see what it holds",
                    f"check with `claude mcp get {SERVER_NAME}`; to add it: {manual}",
                    status="failed",
                ) from None
            item["notes"].append(f"{failure}, but {config} holds the new entry.")
        _record(item, plan, existing)
        return

    def settle(failure: str) -> None:
        """The one way out of a failed remove-then-add, for any failure at any
        step (a call, its output, a read-back, the restore, a bug): returns
        only when the file holds the new entry; otherwise the entry is as it
        was (restored if need be), or the report holds what was removed and
        the commands that put it back. Criterion 5: never neither."""
        # holds() and run() never raise, and the rest is plain logic over
        # values already checked, so nothing below can escape this function.
        now = holds()
        if is_entry(now, wanted):
            item["notes"].append(f"{failure}, but {config} holds the new entry.")
            return
        if is_entry(now, existing):
            raise _StopError(
                "CLIENT_COMMAND_FAILED",
                f"{failure}; the previous {SERVER_NAME!r} entry is in place",
                f"fix the cause and re-run, or run `{_REMOVE}` and then: {manual}",
            )
        if now is _UNKNOWN:
            # Claude Code replaces a file it cannot read, so it is not called
            # again: the report carries what to put back.
            failure += f"; {config} could not be read back, so `claude` was not run again"
        elif _add_reproduces(existing):
            old = ["add", "-s", "user", SERVER_NAME, "--", existing["command"]]
            restored, detail = run(*old, *existing["args"])
            if restored or is_entry(holds(), existing):
                raise _StopError(
                    "CLIENT_COMMAND_FAILED",
                    f"{failure}; the previous {SERVER_NAME!r} entry was restored",
                    f"fix the cause and re-run, or run `{_REMOVE}` and then: {manual}",
                )
            failure += f"; restoring the previous entry also failed: {detail}"
        by_hand = [manual]
        if _add_reproduces(existing):
            by_hand.insert(0, _add_command(existing))
        raise _StopError(
            "CLIENT_COMMAND_FAILED",
            f"{failure}. The previous {SERVER_NAME!r} entry is not back; `removed` is what it held",
            "run the first command in by_hand to put it back as it was, or the last to "
            "point it at this install",
            status="failed",
            removed=_public(existing),
            by_hand=by_hand,
        )

    # From the moment `remove` is attempted, settle() is the only exit that is
    # not success. Not covered: KeyboardInterrupt and signals.
    try:
        call("remove", "-s", "user", SERVER_NAME)
        call(*add_new)
    except Exception as exc:  # noqa: BLE001
        settle(why(exc))
    _record(item, plan, existing)


# -- the command ---------------------------------------------------------------------


def _verify_starts(command: Path, args: list[str], entry: bool = False) -> None:
    """Run `command args --help` before any client is told to start it: a
    client that cannot start a server says so only in its own log. With
    `entry`, these are an existing entry's own arguments as they will be
    written, so any option this install's pyrite rejects stops the write."""
    argv = [str(command), *args, "--help"]
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=_SUBPROCESS_TIMEOUT,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        proc = None
        detail = str(exc)
    else:
        detail = (proc.stderr or proc.stdout).strip()[-2000:]
    if proc is not None and proc.returncode == 0:
        return
    if entry:
        raise _StopError(
            "ENTRY_WOULD_NOT_START",
            f"with this install's path the {SERVER_NAME!r} entry would run "
            f"`{shlex.join(argv[:-1])}`, which fails: {detail}",
            "nothing was written; edit the entry's arguments by hand, or re-run with "
            "--force to write the default entry",
        )
    raise _StopError(
        "MCP_COMMAND_FAILED",
        f"{shlex.join(argv)} failed, so no client could start it: {detail}",
        "reinstall pyrite into this environment (the MCP server is a core "
        "dependency) and re-run; nothing was written",
    )


def _explicit_config_env() -> dict[str, str]:
    """PYRITE_CONFIG_DIR / PYRITE_DATA_DIR as this process has them, absolute.

    Claude Desktop starts the server with a limited environment and an
    undefined cwd (`/` on macOS), so without these it loads ~/.pyrite, not
    the config the user just set up with. Only an explicit variable is
    pinned: a repo-local .pyrite/ is untrusted, and pinning it would make it
    trusted. Pinned in a new entry only, never added to one that exists.
    """
    return {
        var: str(Path(os.environ[var]).expanduser().absolute())
        for var in CONFIG_ENV_VARS
        if os.environ.get(var)
    }


def _print_report(out: Console, report: dict) -> None:
    def line(text: str) -> None:
        out.print(Text(text))

    new = report["new_entry"]
    line(
        f"A new entry runs: {shlex.join(new['command'])} "
        f"({new['tools']} tools at the {new['tier']} tier)"
    )
    for item in report["clients"]:
        code = f" [{item['error_code']}]" if "error_code" in item else ""
        serves = ""
        if "tier" in item:
            default = "" if item["tier_named"] else ", the server's default"
            count = f", {item['tools']} tools" if "tools" in item else ""
            serves = f"  ({item['tier']} tier{default}{count})"
        line(f"{item['label']}: {item['status']}{code}{serves}  {item['location']}")
        if "error" in item:
            line(f"    {item['error']}")
            line(f"    hint: {item['suggestion']}")
        for change in item.get("changes", []):
            old, new = (
                shlex.join(v) if isinstance(v, list) else v for v in (change["old"], change["new"])
            )
            line(f"    {change['field']}: {old} -> {new}")
        for key in ("kept", "discarded"):
            if item.get(key):
                line(f"    {key}: {'; '.join(item[key])}")
        if "pinned" in item:
            line("    pinned: " + ", ".join(f"{k}={v}" for k, v in item["pinned"].items()))
        if "removed" in item:
            line(f"    removed: {item['removed']}")
        for command in item.get("by_hand", []):
            line(f"    run: {command}")
        for note in item["notes"]:
            line(f"    note: {note}")
    for note in report["notes"]:
        line(f"Note: {note}")
    if any(item["status"] in ("created", "changed") for item in report["clients"]):
        line("Restart the client to load it. `pyrite mcp --help` lists each tier's tools.")


def mcp_setup(
    client: str | None = typer.Option(
        None,
        "--client",
        help="claude-code or claude-desktop. Default: every client found on this machine.",
    ),
    tier: str | None = typer.Option(
        None,
        "--tier",
        "-t",
        help="Tool tier: read, write or admin. A new entry gets write unless you say. An "
        "existing entry keeps its own tier unless you name one here.",
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
        help="Discard the existing 'pyrite' entry, with everything it carries, and write "
        "the default one. The output names what was discarded. The ordinary path never "
        "needs this.",
    ),
    # Declared here until the shared option of #303 lands: JSON by default,
    # PYRITE_FORMAT honoured, and no -f.
    output_format: str = typer.Option(
        "json",
        "--format",
        envvar="PYRITE_FORMAT",
        callback=validate_output_format,
        help="Output format: json (default), rich for text, or another registered format.",
    ),
):
    """
    Point the 'pyrite' MCP server entry of Claude Code and Claude Desktop at this install.

    Claude Code: `claude mcp add -s user` (stored in ~/.claude.json), or
    ./.mcp.json with --project. Claude Desktop: its claude_desktop_config.json
    (macOS: ~/Library/Application Support/Claude/, Windows: %APPDATA%\\Claude\\).
    A new entry runs this install's pyrite by absolute path with an explicit
    --tier (default: write).

    An entry that is already there is changed only in what you asked: its
    command path, and its tier when you pass --tier. Its env, extra arguments,
    other keys and (without --tier) its tier are kept; an entry that already
    matches is not rewritten. Other servers and keys are never touched. If
    that cannot be done safely the command stops, leaves that client as it
    was and says why. Each client is reported as created, unchanged, changed,
    stopped or failed; the exit code is 1 unless every client ended up
    pointing at this install. Restart the client afterwards.

    A file a client wrote comes back byte for byte outside the entry. A
    hand-formatted file keeps every value, but one-line arrays open out and
    escapes and numbers may be respelled (1.10 becomes 1.1).
    """
    from ..services.access_policy import ROLES

    fmt = output_format
    new_tier = tier or "write"

    def stop(failure: _StopError, extra: dict | None = None) -> None:
        cli_error(
            failure.message,
            fmt,
            error_code=failure.error_code,
            suggestion=failure.suggestion,
            extra=extra,
        )

    if client is not None and client not in CLIENTS:
        raise typer.BadParameter(
            f"unknown client {client!r}; choose one of: {', '.join(CLIENTS)}",
            param_hint="--client",
        )
    if project and client == CLAUDE_DESKTOP:
        raise typer.BadParameter(
            "is Claude Code's project scope (.mcp.json); Claude Desktop has none",
            param_hint="--project",
        )
    if config_path is not None and (project or client):
        raise typer.BadParameter(
            "writes one named file; it does not combine with --client or --project",
            param_hint="--config",
        )
    if new_tier not in ROLES:
        stop(
            _StopError(
                "INVALID_TIER", f"Invalid tier: {tier!r}", f"choose one of: {', '.join(ROLES)}"
            )
        )

    command = pyrite_executable()
    if command is None:
        stop(
            _StopError(
                "PYRITE_EXECUTABLE_NOT_FOUND",
                f"Cannot find the `pyrite` script of this install (Python: {sys.executable})",
                'reinstall with pip (`pip install -e ".[cli]"` in a checkout) so the '
                "`pyrite` script exists, then run that script's mcp-setup",
            )
        )
    try:
        _verify_starts(command, ["mcp", "--tier", new_tier])
    except _StopError as failure:
        stop(failure)

    args = [str(command), "mcp", "--tier", new_tier]
    pins = _explicit_config_env()
    plain = {"command": str(command), "args": args[1:], **({"env": pins} if pins else {})}
    as_claude_writes = {"type": "stdio", "command": str(command), "args": args[1:], "env": {}}
    notes: list[str] = []
    # (item, how to set it up). Each client is all or nothing on its own: one
    # that stops does not stop the next, and the report names each outcome.
    work: list[tuple[dict, Callable[[], None]]] = []

    def file_target(name: str, label: str, path: Path, default: dict, **kwargs) -> None:
        item = {"client": name, "label": label, "location": str(path), "notes": []}
        entry_pins = pins if default is plain else {}
        private = kwargs.get("private", False)
        work.append(
            (item, partial(_setup_file, item, path, default, tier, force, entry_pins, private))
        )

    if config_path is not None:
        file_target("config", "Config file", config_path.expanduser(), plain)
    elif project:
        mcp_json = Path.cwd() / ".mcp.json"
        file_target(CLAUDE_CODE, "Claude Code (project scope)", mcp_json, as_claude_writes)
        work[-1][0]["scope"] = "project"
        notes.append(
            f"{mcp_json} holds this machine's path to pyrite; commit it only if everyone "
            "who opens the project has pyrite installed at that path."
        )
    else:
        desktop = desktop_config_path()
        claude = find_claude_code() if client in (None, CLAUDE_CODE) else None
        manual_claude = _add_command(as_claude_writes)
        if client == CLAUDE_CODE and claude is None:
            stop(
                _StopError(
                    "CLIENT_NOT_FOUND",
                    "Claude Code's `claude` command was not found (on PATH, ~/.local/bin "
                    "or ~/.claude/local)",
                    "install Claude Code and re-run, or write ./.mcp.json with "
                    f"`pyrite mcp-setup --project`, or run: {manual_claude}",
                )
            )
        if claude is not None:
            item = {
                "client": CLAUDE_CODE,
                "scope": "user",
                "label": "Claude Code (user scope, via `claude mcp`)",
                "location": str(claude_code_config_path()),
                "notes": [],
            }
            work.append(
                (item, partial(_setup_claude_code, item, claude, as_claude_writes, tier, force))
            )
        if client == CLAUDE_DESKTOP or (client is None and desktop.parent.is_dir()):
            if not desktop.parent.is_dir():
                notes.append(
                    f"{desktop.parent} did not exist yet; Claude Desktop reads the entry "
                    "once it is installed."
                )
            if sys.platform not in ("darwin", "win32"):
                notes.append(
                    "Claude Desktop has no official Linux build; this is the path "
                    "unofficial builds use."
                )
            file_target(CLAUDE_DESKTOP, "Claude Desktop", desktop, plain, private=True)
        if not work:
            manual = {"mcpServers": {SERVER_NAME: plain}}
            if fmt == "rich":
                out = Console(soft_wrap=True, highlight=False)
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
                _StopError(
                    "CLIENT_NOT_FOUND",
                    f"No MCP client found: no `claude` command, and no {desktop.parent}",
                    "install Claude Code or Claude Desktop and re-run, or use "
                    "--client, --project or --config PATH",
                ),
                extra={"manual": manual, "manual_claude_code": manual_claude},
            )

    for item, set_up in work:
        try:
            set_up()
        except _StopError as failure:
            item.update(
                status=failure.status,
                error_code=failure.error_code,
                error=failure.message,
                suggestion=failure.suggestion,
                **failure.extra,
            )
        except Exception as exc:  # noqa: BLE001 - one client's failure is its report line
            # Whatever went wrong, the next client is still attempted and the
            # output keeps its shape. A file is replaced in one step or not at
            # all, so it is as it was; a `claude` call already made may not be.
            called = item.get("_called")
            item.update(
                status="failed" if called else "stopped",
                error_code="INTERNAL_ERROR",
                error=f"{type(exc).__name__}: {exc}",
                suggestion=(
                    f"check with `claude mcp get {SERVER_NAME}`; "
                    if called
                    else "this client was not changed; "
                )
                + "please report this at https://github.com/pyrite-wiki/pyrite/issues",
            )
        item.pop("_called", None)

    if any(i["status"] == "created" and i["client"] != CLAUDE_CODE for i, _ in work) and not pins:
        from ..config import resolve_config_source

        source, trusted = resolve_config_source()
        if not trusted:
            notes.append(
                "A GUI client starts the server outside this directory, so it loads "
                f"~/.pyrite, not {source}."
            )

    counts = mcp_tool_counts()
    for item, _ in work:
        if item.get("tier") in counts:
            item["tools"] = counts[item["tier"]]
    report = {
        "ok": all(item["status"] in ("created", "unchanged", "changed") for item, _ in work),
        # What a new entry gets. Each client's own `tier` and `tools` say what
        # its entry serves, which for a kept entry may be another tier.
        "new_entry": {"command": args, "tier": new_tier, "tools": counts[new_tier]},
        "clients": [item for item, _ in work],
        "notes": notes,
    }
    if fmt == "rich":
        _print_report(Console(soft_wrap=True, highlight=False), report)
    else:
        typer.echo(format_output(report, fmt))
    if not report["ok"]:
        raise typer.Exit(1)
