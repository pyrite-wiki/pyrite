"""Settings every git process Pyrite starts runs under.

What is guarded: a repository's own config can name a program for git to run
at ``core.fsmonitor`` and a directory of hooks at ``core.hooksPath`` (or
``.git/hooks``). Pyrite reads and writes files; it runs neither, so each git
process starts with both off, and Pyrite's diffs run no external diff driver
or textconv program (``--no-ext-diff``, ``--no-textconv``).

What is not: Pyrite trusts a repository's local config as git does. Clean and
smudge filters (git-lfs is one), merge drivers, ``gpg.program`` and the like
are the repository's, and run when a git command needs them.

The settings travel as ``GIT_CONFIG_PARAMETERS``, the variable ``git -c`` sets
for itself, so they apply on every git version that has ``-c`` and rank above
every config file. The variables a caller's environment may already hold for
the same purpose (``GIT_CONFIG_PARAMETERS``, ``GIT_CONFIG_COUNT`` and its
key/value pairs) are dropped, so they cannot add to or override these.
"""

from __future__ import annotations

import os
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


def git_env(
    extra: Mapping[str, str] | None = None,
    *,
    hooks: bool = False,
    config: Iterable[tuple[str, str]] = (),
) -> dict[str, str]:
    """The environment for a git subprocess Pyrite starts: a copy of this
    process's, without a parent git process's repo-scoped variables, with
    `extra` layered in, and hardened (see the module docstring)."""
    env = os.environ.copy()
    for var in _LEAKABLE_GIT_ENV_VARS:
        env.pop(var, None)
    if extra:
        env.update(extra)
    return harden_git_env(env, hooks=hooks, config=config)
