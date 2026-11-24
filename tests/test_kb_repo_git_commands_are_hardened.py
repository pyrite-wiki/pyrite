"""Git commands Pyrite runs in a KB repository never run that repository's programs.

Property: a repository's own config (``core.fsmonitor``) and hooks
(``.git/hooks``, ``core.hooksPath``) execute nothing when Pyrite runs git in it
-- through every entry point that starts a git process, found by search and
checked structurally so a new call site cannot skip the rule.
"""

from __future__ import annotations

import ast
import os
import subprocess
from pathlib import Path

import pytest

from pyrite.services.git_service import GitService

REPO_ROOT = Path(__file__).resolve().parent.parent


def _git(cwd: Path, *args: str) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        env=env,
        timeout=60,
    )


@pytest.fixture
def booby_trapped(tmp_path):
    """A repository whose own config and hooks would write marker files."""
    repo = tmp_path / "kb"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "a.md").write_text("a\n")
    _git(repo, "add", "a.md")
    _git(repo, "commit", "-q", "-m", "init")

    markers = tmp_path / "markers"
    markers.mkdir()
    fsmonitor = tmp_path / "fsmonitor.sh"
    fsmonitor.write_text(f"#!/bin/sh\ntouch {markers}/fsmonitor\nexit 1\n")
    fsmonitor.chmod(0o755)
    _git(repo, "config", "core.fsmonitor", str(fsmonitor))

    hooks = tmp_path / "hooks"
    hooks.mkdir()
    for name in (
        "pre-commit",
        "post-commit",
        "commit-msg",
        "post-checkout",
        "reference-transaction",
    ):
        hook = hooks / name
        hook.write_text(f"#!/bin/sh\ntouch {markers}/{name}\n")
        hook.chmod(0o755)
    _git(repo, "config", "core.hooksPath", str(hooks))
    legacy = repo / ".git" / "hooks" / "pre-commit"
    legacy.write_text(f"#!/bin/sh\ntouch {markers}/legacy-pre-commit\n")
    legacy.chmod(0o755)
    return repo, markers


def _fired(markers: Path) -> list[str]:
    return sorted(p.name for p in markers.iterdir())


def test_status_runs_no_fsmonitor(booby_trapped):
    repo, markers = booby_trapped
    (repo / "b.md").write_text("b\n")
    assert GitService.get_status(repo)["untracked"] == ["b.md"]
    assert _fired(markers) == []


def test_commit_runs_no_hook_and_no_fsmonitor(booby_trapped):
    repo, markers = booby_trapped
    (repo / "b.md").write_text("b\n")
    ok, result = GitService.commit(repo, "add b")
    assert ok, result
    assert _fired(markers) == []


def test_the_operators_own_commit_runs_their_hooks_but_not_fsmonitor(booby_trapped):
    """`pyrite kb commit` at the operator's terminal keeps git's commit-hook
    contract (a hook that rejects the commit is the operator's rule); the
    repository's fsmonitor program still does not run."""
    repo, markers = booby_trapped
    (repo / "b.md").write_text("b\n")
    ok, result = GitService.commit(repo, "add b", run_hooks=True)
    assert ok, result
    fired = _fired(markers)
    assert "pre-commit" in fired
    assert "fsmonitor" not in fired


def test_a_remote_callers_commit_path_runs_no_hooks(booby_trapped):
    """The service every REST and MCP commit goes through defaults to no hooks."""
    import inspect

    from pyrite.services.export_service import ExportService

    default = inspect.signature(ExportService.commit_kb).parameters["run_hooks"].default
    assert default is False


@pytest.mark.control(
    reason="cat-file and rev-parse never consult fsmonitor or hooks; kept as a guard against that changing"
)
def test_reading_a_version_runs_nothing(booby_trapped):
    repo, markers = booby_trapped
    assert GitService.show(repo, "a.md") == "a\n"
    assert GitService.get_kb_prefix(repo) == ""
    assert _fired(markers) == []


def test_document_manager_git_rm_runs_nothing(booby_trapped):
    from pyrite.storage.document_manager import DocumentManager

    repo, markers = booby_trapped
    DocumentManager._remove_old_file(object.__new__(DocumentManager), repo / "a.md", repo)
    assert not (repo / "a.md").exists()
    assert _fired(markers) == []


def test_changed_files_listing_runs_nothing(booby_trapped, monkeypatch):
    from pyrite.cli.schema_commands import _get_git_changed_md_files

    repo, markers = booby_trapped
    (repo / "b.md").write_text("b\n")
    monkeypatch.chdir(repo)
    _get_git_changed_md_files(None)
    assert _fired(markers) == []


def test_an_inherited_git_config_environment_cannot_undo_the_hardening(booby_trapped, monkeypatch):
    """GIT_CONFIG_PARAMETERS and GIT_CONFIG_COUNT in Pyrite's own environment
    are dropped, so they can neither re-enable hooks nor add programs."""
    repo, markers = booby_trapped
    hooks = repo.parent / "hooks"
    monkeypatch.setenv("GIT_CONFIG_COUNT", "2")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.hooksPath")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(hooks))
    monkeypatch.setenv("GIT_CONFIG_KEY_1", "core.fsmonitor")
    monkeypatch.setenv("GIT_CONFIG_VALUE_1", str(repo.parent / "fsmonitor.sh"))
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", f"'core.hooksPath'='{hooks}'")
    (repo / "b.md").write_text("b\n")
    GitService.get_status(repo)
    ok, result = GitService.commit(repo, "add b")
    assert ok, result
    assert _fired(markers) == []


def test_diffs_run_no_external_diff_driver_or_textconv(booby_trapped):
    repo, markers = booby_trapped
    driver = repo.parent / "driver.sh"
    driver.write_text(f"#!/bin/sh\ntouch {markers}/ext-diff\n")
    driver.chmod(0o755)
    textconv = repo.parent / "textconv.sh"
    textconv.write_text(f'#!/bin/sh\ntouch {markers}/textconv\ncat "$1"\n')
    textconv.chmod(0o755)
    _git(repo, "config", "diff.external", str(driver))
    _git(repo, "config", "diff.t.textconv", str(textconv))
    (repo / ".gitattributes").write_text("*.md diff=t\n")
    _git(repo, "checkout", "-q", "-b", "other")
    (repo / "a.md").write_text("changed\n")
    _git(repo, "-c", "core.hooksPath=/dev/null", "commit", "-q", "-am", "change")
    _git(repo, "checkout", "-q", "main")
    for fired in markers.iterdir():  # the setup above ran the traps itself
        fired.unlink()
    GitService.diff_branches(repo, "main", "other")
    assert _fired(markers) == []


@pytest.mark.control(
    reason="already held; git_env no longer takes extra variables, so the inherited one is set in the environment"
)
def test_the_environment_carries_the_settings_as_command_line_config(monkeypatch):
    from pyrite.utils.git_hardening import git_env

    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    env = git_env()
    assert "GIT_CONFIG_COUNT" not in env
    params = env["GIT_CONFIG_PARAMETERS"]
    assert "'core.fsmonitor'='false'" in params and "'core.hooksPath'='/dev/null'" in params
    assert "core.hooksPath" not in git_env(hooks=True)["GIT_CONFIG_PARAMETERS"]


# --- every call site, by search ------------------------------------------------
#
# ADR-0052 N2: the environment of every git process is built by one
# constructor that decides from the reach (``git_hardening.git_env``), and every
# network command's by the one network-git constructor
# (``git_service._network_git_env`` of a ``GitService._network_plan``). So an
# ``env=`` value is a bare call to one of them and nothing else: no extra
# variables passed in, no copy of ``os.environ``, no dict built at the call site.

_RUNNERS = {"run", "Popen", "check_output", "check_call", "call", "getoutput", "getstatusoutput"}
#: Constructors an ``env=`` may call, with the keywords each accepts.
_CONSTRUCTORS = {"_git_env": {"hooks", "identity"}, "git_env": {"hooks", "identity"}}
#: Only in git_service.py, with one positional name (the plan).
_NETWORK_CONSTRUCTOR = "_network_git_env"
#: Only in git_hardening.py, bare: the one read of the account's own config.
_ACCOUNT_CONSTRUCTOR = "_account_git_env"

# What the scan sees, and its exact limit. A process start is any call spelled
# as one of:
#   subprocess.<run|Popen|check_output|check_call|call|getoutput|getstatusoutput>
#     (``subprocess`` under any ``import subprocess as x`` alias, or the
#     function imported by name, or a module-level ``x = subprocess.run``);
#   asyncio.create_subprocess_exec / create_subprocess_shell (attribute or
#     imported name);
#   os.system, os.popen, os.exec*, os.spawn*, os.posix_spawn* (attribute or
#     imported name); pty.spawn.
# Its program is read from the argument: a list or tuple literal whose first
# element is a string (git when its basename is ``git``, an absolute path to
# git included), a name bound to such a literal in the same function or at
# module level, or a shell string (git when the word ``git`` appears in it).
# A git program needs a constructor environment. A program the scan cannot
# read (an expression, a name it cannot resolve, ``*args``) is treated as git
# unless `_REVIEWED_NOT_GIT` names its call site with the reason it is not;
# a shell or os.exec/spawn start that may run git is refused outright (it
# takes no ``env=`` from the constructor). Not seen: a process started through
# ``getattr``/``importlib``, ctypes or a library that runs git itself (none is
# imported, `test_no_library_runs_git_for_pyrite`), and a non-git program that
# runs git in turn (pip installing a VCS URL; the CLI's own, local process).
_REVIEWED_NOT_GIT: dict[tuple[str, str], str] = {
    ("pyrite/cli/extension_commands.py", "_verify_distribution"): (
        "sys.executable: this Python, importing the installed extension (local CLI)"
    ),
    ("pyrite/cli/extension_commands.py", "extension_install"): (
        "sys.executable -m pip install -e <local path> (local CLI)"
    ),
    ("pyrite/cli/extension_commands.py", "extension_uninstall"): (
        "sys.executable -m pip uninstall (local CLI)"
    ),
    ("pyrite/cli/mcp_setup_command.py", "run"): "the `claude` CLI found on PATH (local CLI)",
    ("pyrite/cli/mcp_setup_command.py", "_verify_starts"): (
        "argv is an MCP server command the user configured, run with --check (local CLI)"
    ),
}

_OS_STARTS = {"system", "popen", "posix_spawn", "posix_spawnp"}
_OS_PREFIXES = ("exec", "spawn")


def _func_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _env_violation(env: ast.expr | None, filename: str) -> str | None:
    """Why `env` (the ``env=`` value of a git subprocess call) is not a bare
    constructor call, or None when it is."""
    if env is None:
        return "no env="
    if not isinstance(env, ast.Call):
        return f"env={ast.unparse(env)} is not a constructor call"
    name = _func_name(env.func)
    if name in _CONSTRUCTORS:
        extra = [k.arg for k in env.keywords if k.arg not in _CONSTRUCTORS[name]]
        if env.args or extra:
            return f"env={ast.unparse(env)} passes more than hooks and identity"
        return None
    if name == _NETWORK_CONSTRUCTOR:
        if filename != "git_service.py":
            return f"env={ast.unparse(env)} outside the git service"
        if len(env.args) != 1 or not isinstance(env.args[0], ast.Name) or env.keywords:
            return f"env={ast.unparse(env)} is not one plan"
        return None
    if name == _ACCOUNT_CONSTRUCTOR and filename == "git_hardening.py":
        return None if not (env.args or env.keywords) else f"env={ast.unparse(env)} has arguments"
    return f"env={ast.unparse(env)} is not a constructor"


def _environ_reads(tree: ast.AST) -> list[int]:
    """Lines that read the process environment directly."""
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in ("environ", "getenv", "environb"):
            if isinstance(node.value, ast.Name) and node.value.id == "os":
                lines.append(node.lineno)
        elif isinstance(node, ast.ImportFrom) and node.module == "os":
            if any(a.name in ("environ", "getenv", "environb") for a in node.names):
                lines.append(node.lineno)
    return lines


def _aliases(tree: ast.Module) -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
    """(module alias -> module, bare name -> (module, function)) for the
    modules that start processes."""
    modules = {"subprocess": "subprocess", "os": "os", "asyncio": "asyncio", "pty": "pty"}
    names: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name in modules and a.asname:
                    modules[a.asname] = a.name
        elif isinstance(node, ast.ImportFrom) and node.module in modules:
            for a in node.names:
                names[a.asname or a.name] = (node.module, a.name)
    for node in tree.body:  # x = subprocess.run
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Attribute)
            and isinstance(node.value.value, ast.Name)
            and modules.get(node.value.value.id)
        ):
            names[node.targets[0].id] = (modules[node.value.value.id], node.value.attr)
    return modules, names


def _start_kind(call: ast.Call, modules, names) -> str | None:
    """'exec' (argv), 'shell' (a command string), 'os' (exec/spawn) or None."""
    func = call.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        module, attr = modules.get(func.value.id), func.attr
    elif isinstance(func, ast.Name) and func.id in names:
        module, attr = names[func.id]
    else:
        return None
    if module == "subprocess" and attr in _RUNNERS:
        shell = any(
            k.arg == "shell" and not (isinstance(k.value, ast.Constant) and not k.value.value)
            for k in call.keywords
        )
        return "shell" if shell or attr in ("getoutput", "getstatusoutput") else "exec"
    if module == "asyncio" and attr == "create_subprocess_exec":
        return "aexec"
    if module == "asyncio" and attr == "create_subprocess_shell":
        return "shell"
    if module == "os" and attr in ("system", "popen"):
        return "shell"
    if module == "os" and (attr in _OS_STARTS or attr.startswith(_OS_PREFIXES)):
        return "os"
    if module == "pty" and attr == "spawn":
        return "os"
    return None


def _is_git_program(text: str) -> bool:
    name = text.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return name in ("git", "git.exe")


def _bindings(scope: ast.AST) -> dict[str, ast.expr]:
    """name -> the one value it is assigned in `scope` (not nested scopes)."""
    found: dict[str, list[ast.expr]] = {}
    for node in ast.walk(scope):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    found.setdefault(t.id, []).append(node.value)
    return {k: v[0] for k, v in found.items() if len(v) == 1}


def _program(arg: ast.expr | None, kind: str, scopes: list[dict[str, ast.expr]]) -> str:
    """'git', 'other' or 'unknown' for the program a start runs."""
    for _ in range(3):  # follow a name to its literal
        if isinstance(arg, ast.Name):
            arg = next((s[arg.id] for s in scopes if arg.id in s), None)
    if kind == "shell":
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return "git" if any(_is_git_program(w) for w in arg.value.split()) else "other"
        return "unknown"
    if kind == "aexec":
        head = arg
    elif isinstance(arg, (ast.List, ast.Tuple)) and arg.elts:
        head = arg.elts[0]
    else:
        return "unknown"
    if isinstance(head, ast.Constant) and isinstance(head.value, str):
        return "git" if _is_git_program(head.value) else "other"
    return "unknown"


def _starts(tree: ast.Module):
    """(call, kind, program, enclosing function) for every process start."""
    modules, names = _aliases(tree)
    # Module level: top-level assignments only (a function's own are its scope's).
    module_scope = _bindings(
        ast.Module(body=[n for n in tree.body if isinstance(n, ast.Assign)], type_ignores=[])
    )

    def visit(node, func_name, scopes):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                yield from visit(child, child.name, [_bindings(child), *scopes])
                continue
            if isinstance(child, ast.Call):
                kind = _start_kind(child, modules, names)
                if kind is not None:
                    arg = child.args[0] if child.args else None
                    if kind == "os":
                        program = "unknown"
                    else:
                        program = _program(arg, kind, scopes)
                    yield child, kind, program, func_name
            yield from visit(child, func_name, scopes)

    yield from visit(tree, "<module>", [module_scope])


def _judge(source: str, filename: str, relpath: str | None = None) -> list[tuple[int, str]]:
    """(line, why) of each process start in `source` that may run git and
    does not take a bare constructor environment, and, in the git service,
    each direct read of the process environment. In the git service every
    start is a git command, whatever its argv looks like."""
    tree = ast.parse(source)
    found = []
    for call, kind, program, func in _starts(tree):
        if program == "other" and filename != "git_service.py":
            continue
        if program == "unknown" and (relpath, func) in _REVIEWED_NOT_GIT:
            continue
        if kind in ("shell", "os"):
            found.append((call.lineno, f"a {kind} start that may run git takes no constructor env"))
            continue
        env = next((k.value for k in call.keywords if k.arg == "env"), None)
        why = _env_violation(env, filename)
        if why:
            label = "" if program == "git" else " (a program the scan cannot read)"
            found.append((call.lineno, why + label))
    if filename == "git_service.py":
        found += [(line, "reads os.environ") for line in _environ_reads(tree)]
    return found


def _source_files() -> list[Path]:
    files = []
    for root in ("pyrite", "extensions"):
        for path in (REPO_ROOT / root).rglob("*.py"):
            rel = path.relative_to(REPO_ROOT)
            if "tests" in rel.parts or ".venv" in rel.parts or "node_modules" in rel.parts:
                continue
            files.append(path)
    return files


def _git_call_sites() -> list[tuple[str, int, str | None]]:
    """(file, line, env source) of every process start that runs git."""
    sites = []
    for path in _source_files():
        for call, _kind, program, _func in _starts(ast.parse(path.read_text())):
            if program == "git" or path.name == "git_service.py":
                env = next((ast.unparse(k.value) for k in call.keywords if k.arg == "env"), None)
                sites.append((str(path.relative_to(REPO_ROOT)), call.lineno, env))
    return sites


@pytest.mark.control(reason="the scan itself: it must keep finding the sites the next test judges")
def test_the_search_finds_the_known_git_call_sites():
    files = {f for f, _, _ in _git_call_sites()}
    assert "pyrite/services/git_service.py" in files
    assert "pyrite/storage/document_manager.py" in files
    assert "pyrite/cli/schema_commands.py" in files
    assert "pyrite/utils/git_hardening.py" in files  # the account read, via `_run`


def test_every_git_subprocess_takes_the_hardened_environment():
    bad = [
        f"{path.relative_to(REPO_ROOT)}:{line}: {why}"
        for path in _source_files()
        for line, why in _judge(path.read_text(), path.name, str(path.relative_to(REPO_ROOT)))
    ]
    assert bad == []


# One planted violation per environment shape the code had before N2, and per
# way around the rule: each must be found.
_PLANTED = {
    "extra variables": "subprocess.run(['git', 'x'], env=_git_env({'X': '1'}))",
    "isolation passed in": "subprocess.run(['git', 'x'], env=_git_env(_account_isolation_env()))",
    "subprocess_env": "subprocess.run(['git', 'x'], env=GitService.subprocess_env())",
    "subprocess_env(extra)": "subprocess.run(['git', 'x'], env=GitService.subprocess_env({'A': 'b'}))",
    "guard and creds merged": (
        "subprocess.run(['git', 'x'], "
        "env=_git_env({**_NETWORK_GUARD_ENV, **creds.env}, config=creds.config))"
    ),
    "extra merged": "subprocess.run(['git', 'x'], env=_git_env({**extra, **creds.env}))",
    "git_env positional": "subprocess.run(['git', 'x'], env=git_env(extra))",
    "git_env config=": "subprocess.run(['git', 'x'], env=git_env(config=[('a', 'b')]))",
    "os.environ": "subprocess.run(['git', 'x'], env=os.environ)",
    "a copy of os.environ": "subprocess.run(['git', 'x'], env=os.environ.copy())",
    "a name": "subprocess.run(['git', 'x'], env=env)",
    "a dict around the constructor": "subprocess.run(['git', 'x'], env={**_git_env(), 'X': '1'})",
    "no env": "subprocess.run(['git', 'x'])",
    "a bare run": "_run = subprocess.run\n_run(['git', 'x'], env=dict(os.environ))",
    "two plans": "subprocess.run(['git', 'x'], env=_network_git_env(plan, other))",
    "a plan built inline": "subprocess.run(['git', 'x'], env=_network_git_env(_NetworkPlan({}, (), 'x')))",
}


@pytest.mark.control(
    reason="exercises the checker on planted source; the tree check is the red test"
)
@pytest.mark.parametrize("source", list(_PLANTED.values()), ids=list(_PLANTED))
def test_a_planted_environment_is_found(source):
    assert _judge(source, "git_service.py") != []
    assert _judge(source, "worktree_service.py") != []


@pytest.mark.control(
    reason="exercises the checker on planted source; the tree check is the red test"
)
@pytest.mark.parametrize(
    "source",
    [
        "import os\nx = os.environ.get('HOME')",
        "import os\nx = os.getenv('HOME')",
        "from os import environ",
    ],
    ids=["os.environ", "os.getenv", "from os import environ"],
)
def test_a_read_of_the_environment_in_the_git_service_is_found(source):
    assert _judge(source, "git_service.py") != []


@pytest.mark.control(
    reason="exercises the checker on planted source; the tree check is the red test"
)
def test_the_network_constructor_is_the_git_services_alone():
    source = "subprocess.run(['git', 'push'], env=_network_git_env(plan))"
    assert _judge(source, "git_service.py") == []
    assert _judge(source, "export_service.py") != []


@pytest.mark.control(reason="the shapes the rule allows: it must not refuse them")
@pytest.mark.parametrize(
    "source",
    [
        "subprocess.run(['git', 'x'], env=_git_env())",
        "subprocess.run(['git', 'x'], env=_git_env(hooks=run_hooks))",
        "subprocess.run(['git', 'x'], env=git_env())",
    ],
)
def test_a_bare_constructor_call_is_allowed(source):
    assert _judge(source, "git_service.py") == []


# Spellings the scan must see outside the git service (where only a start
# whose program it reads as git, or cannot read, is judged).
_PLANTED_OUTSIDE = {
    "argv in a variable": "def f():\n    argv = ['git', 'push']\n    subprocess.run(argv)",
    "argv at module level": "ARGV = ('git', 'push')\nsubprocess.run(ARGV, env=os.environ)",
    "an absolute git path": "subprocess.run(['/usr/bin/git', 'push'])",
    "asyncio exec": "async def f():\n    await asyncio.create_subprocess_exec('git', 'push')",
    "asyncio exec, imported": (
        "from asyncio import create_subprocess_exec\n"
        "async def f():\n    await create_subprocess_exec('git', 'push', env=os.environ)"
    ),
    "asyncio shell": "async def f():\n    await asyncio.create_subprocess_shell('git push')",
    "os.system": "os.system('git push')",
    "os.popen": "os.popen('git fetch')",
    "os.execvp": "os.execvp('git', ['git', 'push'])",
    "shell=True": "subprocess.run('git push', shell=True, env=git_env())",
    "a module alias": "import subprocess as sp\nsp.run(['git', 'x'])",
    "an imported runner": "from subprocess import Popen as P\nP(['git', 'x'])",
    "a program the scan cannot read": "subprocess.run([prog, 'x'], env=os.environ)",
    "a starred argv": "subprocess.run([*cmd])",
}


@pytest.mark.control(
    reason="exercises the checker on planted source; the tree check is the red test"
)
@pytest.mark.parametrize("source", list(_PLANTED_OUTSIDE.values()), ids=list(_PLANTED_OUTSIDE))
def test_a_planted_start_outside_the_git_service_is_found(source):
    assert _judge(source, "worktree_service.py", "pyrite/services/worktree_service.py") != []


@pytest.mark.control(reason="the shapes the rule allows outside the git service")
@pytest.mark.parametrize(
    "source",
    [
        "subprocess.run(['npm', 'run', 'build'])",
        "def f():\n    argv = ['ls', '-l']\n    subprocess.run(argv)",
        "os.system('ls')",
        "asyncio.run(main())",
        "def _run(q):\n    return q\n_run(['git', 'x'])",
        "def f():\n    argv = ['git', 'status']\n    subprocess.run(argv, env=git_env())",
    ],
)
def test_a_start_that_cannot_run_git_or_is_hardened_is_allowed(source):
    assert _judge(source, "worktree_service.py", "pyrite/services/worktree_service.py") == []


@pytest.mark.control(reason="keeps the reviewed list honest; the tree had no stale entry before")
def test_every_reviewed_non_git_start_still_exists():
    """`_REVIEWED_NOT_GIT` names live call sites only: an entry whose site is
    gone (or now reads as a program) is removed."""
    live = set()
    for path in _source_files():
        rel = str(path.relative_to(REPO_ROOT))
        for _call, _kind, program, func in _starts(ast.parse(path.read_text())):
            if program == "unknown":
                live.add((rel, func))
    assert set(_REVIEWED_NOT_GIT) - live == set()


@pytest.mark.control(reason="states the scan's limit; no such library was imported before either")
def test_no_library_runs_git_for_pyrite():
    """The scan reads process starts; a library that runs git (or speaks its
    protocol) itself would be outside it."""
    banned = {"git", "dulwich", "pygit2", "sh", "plumbum"}
    found = []
    for path in _source_files():
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module.split(".")[0]]
            found += [f"{path.relative_to(REPO_ROOT)}:{node.lineno}" for n in names if n in banned]
    assert found == []


# --- only the plan constructors build a plan ------------------------------------
#
# `_network_git_env` refuses a plan that lacks the seal only `_network_plan`
# and `_exempt_plan` hold. That holds only while no other code names the seal,
# builds a `_NetworkPlan`, or copies one with changes (`_replace`, `_make`).

_PLAN_CONSTRUCTORS = {"_network_plan", "_exempt_plan"}


def _plan_builders(source: str) -> list[tuple[int, str]]:
    """(line, what) for each use of the seal or of a plan's constructors
    outside `_network_plan` and `_exempt_plan` (the seal's own definition and
    `_network_git_env`'s check excepted)."""
    tree = ast.parse(source)
    found = []

    def visit(node, func):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, child.name)
                continue
            if func not in _PLAN_CONSTRUCTORS:
                if (
                    isinstance(child, ast.Name)
                    and child.id == "_PLAN_SEAL"
                    and not (isinstance(child.ctx, ast.Store) and func is None)
                    and func != "_network_git_env"
                ):
                    found.append((child.lineno, "names _PLAN_SEAL"))
                elif isinstance(child, ast.Call) and _func_name(child.func) == "_NetworkPlan":
                    found.append((child.lineno, "builds a _NetworkPlan"))
                elif isinstance(child, ast.Attribute) and child.attr in ("_replace", "_make"):
                    found.append((child.lineno, f"copies a plan with .{child.attr}"))
            visit(child, func)

    visit(tree, None)
    return found


@pytest.mark.control(
    reason="the tree built no plan by hand before either; the gap was the checker (planted cases and test_a_plan_built_by_hand_is_refused carry the evidence)"
)
def test_only_the_plan_constructors_build_a_plan():
    bad = [
        f"{path.relative_to(REPO_ROOT)}:{line}: {why}"
        for path in _source_files()
        for line, why in _plan_builders(path.read_text())
    ]
    assert bad == []


@pytest.mark.control(
    reason="exercises the checker on planted source; the tree check is the red test"
)
@pytest.mark.parametrize(
    "source",
    [
        "def f():\n    return _NetworkPlan({}, (), 'none', _PLAN_SEAL)",
        "def f():\n    return _NetworkPlan({}, (), 'none')",
        "def f(plan):\n    return plan._replace(env={'GIT_ALLOW_PROTOCOL': 'file'})",
        "def f():\n    return _NetworkPlan._make(({}, (), 'none', None))",
        "SEAL = _PLAN_SEAL",
    ],
)
def test_a_planted_plan_builder_is_found(source):
    assert _plan_builders(source) != []


def test_a_plan_built_by_hand_is_refused():
    from pyrite.services import git_service

    with pytest.raises(TypeError):
        git_service._network_git_env(git_service._NetworkPlan({}, (), "operator-token"))
