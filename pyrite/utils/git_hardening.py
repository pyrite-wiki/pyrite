"""Settings every git process Pyrite starts runs under.

What is guarded: a repository's own config can name a program for git to run
at ``core.fsmonitor`` and a directory of hooks at ``core.hooksPath`` (or
``.git/hooks``). Pyrite reads and writes files; it runs neither, so each git
process starts with both off, and Pyrite's diffs run no external diff driver
or textconv program (``--no-ext-diff``, ``--no-textconv``).

What is not: Pyrite trusts a repository's local config as git does. Clean and
smudge filters (git-lfs is one), merge drivers, ``gpg.program`` and the like
are the repository's, and run when a git command needs them.

Whose environment: `git_env` decides from the reach (ADR-0052 N2). The local
process keeps its own environment and git setup; a network caller's git gets
an environment built from an allow-list, with every credential store of the
server account closed (global and system config, helpers, askpass, netrc, the
ssh agent and identities).

The settings travel as ``GIT_CONFIG_PARAMETERS``, the variable ``git -c`` sets
for itself, so they apply on every git version that has ``-c`` and rank above
every config file. The variables a caller's environment may already hold for
the same purpose (``GIT_CONFIG_PARAMETERS``, ``GIT_CONFIG_COUNT`` and its
key/value pairs) are dropped, so they cannot add to or override these.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Iterable, Mapping

#: Config git reads from the command line, above repository-local config.
HARDENED_GIT_CONFIG: tuple[tuple[str, str], ...] = (
    ("core.fsmonitor", "false"),
    # A directory that holds no hooks: an empty value is read differently by
    # different git versions.
    ("core.hooksPath", "/dev/null"),
)

_INHERITED_CONFIG_VARS = ("GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT")


def _quote(text: str) -> str:
    return "'" + text.replace("'", "'\\''") + "'"


def git_config_parameters(settings: Iterable[tuple[str, str]]) -> str:
    """The value of ``GIT_CONFIG_PARAMETERS`` for `settings`, in order."""
    return " ".join(f"{_quote(key)}={_quote(value)}" for key, value in settings)


def harden_git_env(
    env: dict[str, str],
    *,
    hooks: bool = False,
    config: Iterable[tuple[str, str]] = (),
) -> dict[str, str]:
    """Replace the config variables in `env` (in place) with the hardened
    settings followed by `config`, and return it.

    ``hooks=True`` leaves the repository's commit hooks in place, for the one
    caller that runs git as the operator's own command (``pyrite kb commit`` at
    their terminal, where a hook that rejects the commit is the operator's own
    rule); ``core.fsmonitor`` stays off.
    """
    for name in list(env):
        if name in _INHERITED_CONFIG_VARS or name.startswith(
            ("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")
        ):
            del env[name]
    settings = [kv for kv in HARDENED_GIT_CONFIG if hooks is False or kv[0] != "core.hooksPath"]
    env["GIT_CONFIG_PARAMETERS"] = git_config_parameters([*settings, *config])
    return env


#: Env vars a parent git process (e.g. a pre-commit hook, itself a child of
#: `git commit`) sets for its own subprocesses. If a call inherits these while
#: operating on a *different* repository (a different `cwd`), a relative
#: GIT_INDEX_FILE resolves against the wrong repo -- corrupting or
#: misdirecting the operation entirely.
_LEAKABLE_GIT_ENV_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_AUTHOR_NAME",
    "GIT_AUTHOR_EMAIL",
    "GIT_AUTHOR_DATE",
    "GIT_COMMITTER_NAME",
    "GIT_COMMITTER_EMAIL",
    "GIT_COMMITTER_DATE",
    "GIT_EDITOR",
)

# --- the network reach (ADR-0052 N2) ------------------------------------------
#
# A git process started for a network caller gets an environment built from
# this allow-list, never the server's environment minus a list. Everything
# else the server account holds (SSH_AUTH_SOCK, GIT_ASKPASS, GIT_SSH*,
# proxy variables, provider keys, HOME) is absent.

#: Variables copied from the server's environment, by exact name. The four
#: certificate-authority variables say which servers to trust (an operator's
#: private CA); they authenticate no one. Client certificates and keys
#: (``GIT_SSL_CERT``, ``GIT_SSL_KEY``), ``GIT_SSL_NO_VERIFY`` and proxies are
#: not carried.
NETWORK_ENV_ALLOWLIST: tuple[str, ...] = (
    "PATH",
    "LANG",
    "TZ",
    "TMPDIR",
    "GIT_SSL_CAINFO",
    "GIT_SSL_CAPATH",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
)
#: ... and by prefix (the locale categories).
NETWORK_ENV_ALLOWED_PREFIXES: tuple[str, ...] = ("LC_",)

#: The ssh command line for a network caller when no operator ssh key is in
#: play. OpenSSH finds ``~/.ssh`` through the passwd entry, not ``$HOME``
#: (verified: overriding HOME leaves ``<passwd home>/.ssh/id_*`` offered), so
#: the command line itself closes every source: no config file (``-F``), no
#: identity file, no agent, no known_hosts of the account, never a prompt.
CLOSED_SSH_OPTIONS: tuple[str, ...] = (
    "-F",
    "/dev/null",
    "-o",
    "IdentitiesOnly=yes",
    "-o",
    "IdentityAgent=none",
    "-o",
    "GlobalKnownHostsFile=/dev/null",
    "-o",
    "StrictHostKeyChecking=yes",
    "-o",
    "BatchMode=yes",
)

#: Config every network-reach git process reads above everything but the
#: command line: no credential helper and no askpass program from any scope.
_NETWORK_CONFIG: tuple[tuple[str, str], ...] = (
    ("credential.helper", ""),
    ("core.askPass", ""),
)

#: (the account config location it was read from, the identity) -> the folder.
_NETWORK_HOMES: dict[tuple, str] = {}


def ssh_command(*options: str) -> str:
    """A ``GIT_SSH_COMMAND`` value: ``ssh`` with `options`, shell-quoted (git
    runs the value through the shell)."""
    return " ".join(["ssh", *(_quote(o) for o in options)])


def _account_git_env() -> dict[str, str]:
    """The server account's own environment, hardened: used only here, to read
    the two values the network home carries over (the identity fallback and
    ``safe.directory``), never for a command a caller asked for."""
    env = os.environ.copy()
    for var in _LEAKABLE_GIT_ENV_VARS:
        env.pop(var, None)
    return harden_git_env(env)


# Bound at import: the network home's one-time read of the account's config is
# Pyrite's own, not a command a test stubs by patching ``subprocess.run``.
_run = subprocess.run


def _account_values(*args: str) -> list[str]:
    try:
        out = _run(
            ["git", "config", *args],
            cwd=os.path.abspath(os.sep),
            capture_output=True,
            text=True,
            env=_account_git_env(),
            timeout=10,
        )
    except (subprocess.SubprocessError, OSError):
        return []
    return [line for line in out.stdout.splitlines() if line] if out.returncode == 0 else []


def _network_gitconfig(identity: tuple[str, str] | None = None) -> str:
    """The global config a network-reach git process reads, in place of the
    account's: only ``user.name``/``user.email`` (the operator's
    ``git_identity``; unset, the account's global identity, until 0.27.0) and
    the account's ``safe.directory`` entries (an ownership exception the
    operator made, not a credential; without it a KB on a volume owned by
    another uid is refused as "dubious ownership"). Nothing that names a
    program, a helper or a URL rewrite is carried over.

    The identity is global config, so a repository's own ``user.*`` wins
    (D-U5), as it does for the operator's own git."""
    lines: list[str] = []
    if identity is not None:
        name, email = [identity[0]], [identity[1]]
    else:
        name = _account_values("--global", "--get", "user.name")
        email = _account_values("--global", "--get", "user.email")
    if name or email:
        lines.append("[user]")
        lines += [f"\tname = {_gitconfig_value(name[0])}"] if name else []
        lines += [f"\temail = {_gitconfig_value(email[0])}"] if email else []
    safe = [
        *_account_values("--system", "--get-all", "safe.directory"),
        *_account_values("--global", "--get-all", "safe.directory"),
    ]
    if safe:
        lines.append("[safe]")
        lines += [f"\tdirectory = {_gitconfig_value(d)}" for d in safe]
    return "\n".join(lines) + ("\n" if lines else "")


def _gitconfig_value(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def _identity_key(identity: object) -> tuple[str, str] | None:
    """(name, email) of a ``GitIdentity`` (duck-typed: this module imports no
    config), or None."""
    if identity is None:
        return None
    return (str(identity.name), str(identity.email))  # type: ignore[attr-defined]


def network_home(identity: object = None) -> str:
    """Pyrite's own HOME for network-reach git: an empty private folder whose
    only file is the global config `_network_gitconfig` writes for
    `identity` (the operator's ``git_identity``). One per account config
    location and identity, built once (the account's config is read at that
    moment and not again) and never removed while the process runs."""
    source = tuple(
        os.environ.get(k)
        for k in ("HOME", "XDG_CONFIG_HOME", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM")
    )
    key = (source, _identity_key(identity))
    home = _NETWORK_HOMES.get(key)
    if home is None or not os.path.isdir(home):
        home = tempfile.mkdtemp(prefix="pyrite-git-home-")
        config = os.path.join(home, "gitconfig")
        fd = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(_network_gitconfig(key[1]))
        _NETWORK_HOMES[key] = home
    return home


def _network_base_env(identity: object = None) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k in NETWORK_ENV_ALLOWLIST or k.startswith(NETWORK_ENV_ALLOWED_PREFIXES)
    }
    home = network_home(identity)
    env.update(
        HOME=home,
        XDG_CONFIG_HOME=home,
        GIT_CONFIG_GLOBAL=os.path.join(home, "gitconfig"),
        GIT_CONFIG_SYSTEM=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        GIT_ASKPASS="",
        SSH_ASKPASS="",
        GIT_TERMINAL_PROMPT="0",
        GIT_SSH_COMMAND=ssh_command(
            *CLOSED_SSH_OPTIONS, "-o", "IdentityFile=none", "-o", "UserKnownHostsFile=/dev/null"
        ),
    )
    return env


def _is_network_reach() -> bool:
    from ..services.acting import is_local_process

    return not is_local_process()


def compose_git_env(
    extra: Mapping[str, str] | None = None,
    config: Iterable[tuple[str, str]] = (),
    *,
    hooks: bool = False,
    identity: object = None,
) -> dict[str, str]:
    """`git_env` with `extra` variables and `config` layered on: for the one
    network-git constructor (``GitService._network_git_env``), which alone
    adds the transport guards and the credential. Not an ``env=`` value
    anywhere else (``tests/test_kb_repo_git_commands_are_hardened.py``)."""
    if _is_network_reach():
        env = _network_base_env(identity)
        settings = [*_NETWORK_CONFIG, *config]
    else:
        env = os.environ.copy()
        for var in _LEAKABLE_GIT_ENV_VARS:
            env.pop(var, None)
        settings = list(config)
    if extra:
        env.update(extra)
    return harden_git_env(env, hooks=hooks, config=settings)


def git_env(*, hooks: bool = False, identity: object = None) -> dict[str, str]:
    """The environment for a git subprocess Pyrite starts, decided by the
    reach (``acting.is_local_process``), never by the caller:

    * the local process (the CLI, stdio MCP): a copy of this process's
      environment, without a parent git process's repo-scoped variables, so
      the operator's own git setup applies;
    * a network caller: only `NETWORK_ENV_ALLOWLIST`, with Pyrite's own empty
      HOME and global config, system config off, no credential helper or
      askpass from any scope, and an ssh command that reads no identity, agent
      or ssh config of the account. `identity` (the operator's
      ``git_identity``) is that config's ``user.*``; a repository's own wins.

    Both are hardened (see the module docstring)."""
    return compose_git_env(hooks=hooks, identity=identity)
