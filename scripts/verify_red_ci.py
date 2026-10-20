#!/usr/bin/env python3
"""verify-red: do a pull request's new tests notice its change?

    python scripts/verify_red_ci.py [--base origin/dev] [--json out.json]

It never modifies the tree it is started in. It checks the merge base out into
a throwaway ``git worktree`` under $TMPDIR, copies in the PR's test-side files
from the working tree (committed or not) and runs the tests the PR adds or
edits: the run *without the fix*. It then copies in the rest of the change, so
the throwaway tree equals the working tree, and runs them again *with the
fix*. The throwaway tree is removed. Nothing is restored, because nothing the
developer owns was changed; a run killed outright leaves a stale directory in
$TMPDIR, and once that is gone the next run prunes its registration.

Verdicts, for each new or edited test:

- **red**: passes with the fix, fails without it -- the evidence a review wants.
- **import-only**: fails without the fix only because the code it needs is
  absent: its file does not collect, or it raises ImportError, AttributeError
  or NameError naming an identifier the PR adds (an AST diff). Weak.
- **unexpected pass**: passes without the fix; it does not test the change.
  ``@pytest.mark.control(reason=...)`` declares a deliberate negative control
  instead.
- **inherited**: passes without the fix and carries a control marker that was
  already on that node id at the merge base. Not a failure, and not evidence.
- **n/a**: no claim (did not pass with the fix, skipped, not run, timed out).

Test-side files are those under ``tests/`` or ``extensions/*/tests/``, and any
``conftest.py``; everything else the PR changes is the fix. Tests the PR did not
add or edit (compared by AST, so formatting does not count) are a count.

ADVISORY (#1074): CI neither gates on this nor passes ``--enforce-fix``; gating
waits for #1104's model. The claim rule is sound only for module-level tables on the
test function itself. A test cell's identity is its test function and its parameter
values, recorded at collection at the base and at the head -- never the generated
node id, which an inserted row shifts and an edited row keeps. A cell is the PR's
claim when that pair is absent at the base. A test selected only because a
module-level table or helper in its decorators changed is graded on those cells
alone. Inline ``parametrize`` lists change the function, so the whole function is
graded; class-level, fixture-params, generate-hook and imported tables change no
test function and select nothing. A test file that names a changed data file (a
golden) has every test claimed; if none names it, that is the cause.

A control marker counts when it is written on the test function or one of its
parameters (``pytest.param(marks=...)``), however it was built. One that came from
anywhere else -- a class or module ``pytestmark`` (a constant too), a collection
hook, an autouse fixture's ``request.applymarker``, ``applymarker`` or
``add_marker`` in the test body or at teardown -- is refused with an error naming
the file and the origin; one the base already had on the same cell is *inherited*.
Not caught: a hook that rewrites the function's own ``pytestmark`` before the items
are built (it looks like a decorator).

Exit 0 whatever the verdicts, except: 1 for a ``@pytest.mark.control`` the PR adds
that the test did not declare on itself or a parameter; 2 when the check itself
could not run. With ``--enforce-fix`` (local use only; CI does not pass it) also 1
for a fix-typed PR that changes ``pyrite/`` or ``extensions/`` code and has no test
added or edited, no red test, every selected test n/a, or an unexpected pass.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_fix_commit_has_tests import FIX_SUBJECT  # noqa: E402  one definition of "a fix"

RED, IMPORT_ONLY, UNEXPECTED, NA, CONTROL, INHERITED = (
    "red",
    "import-only",
    "unexpected pass",
    "n/a",
    "control",
    "inherited",
)
VERDICTS = (RED, IMPORT_ONLY, UNEXPECTED, NA, CONTROL, INHERITED)
PLUGIN = Path(__file__).resolve().parent / "verify_red_record.py"
_MISSING_NAME = {"ImportError", "AttributeError", "NameError"}
_QUOTED = re.compile(r"'([A-Za-z_][\w.]*)'")
_IDENT = re.compile(r"[A-Za-z_]\w*\Z")


class InfraError(Exception):
    """The check could not run; says nothing about the pull request."""


# ---------------------------------------------------------------------------
# Decisions (no I/O)
# ---------------------------------------------------------------------------


def is_test_side(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return (
        parts[0] == "tests"
        or (len(parts) > 3 and parts[0] == "extensions" and parts[2] == "tests")
        or parts[-1] == "conftest.py"
    )


def is_inert(path: str) -> bool:
    """A change no test can observe: a changelog fragment, a KB entry, Markdown.

    Still left out of the run without the fix (a test may read one), but it
    alone does not make a PR something to verify (B1).
    """
    parts = PurePosixPath(path).parts
    return parts[0] in ("changelog.d", "kb") or path.endswith(".md")


def is_test_file(path: str) -> bool:
    p = PurePosixPath(path)
    return is_test_side(path) and p.suffix == ".py" and p.name.startswith("test_")


def _parse(src: str | None) -> ast.Module | None:
    try:
        return ast.parse(src) if src is not None else None
    except SyntaxError:
        return None


def _test_defs(src: str | None) -> dict[tuple[str, ...], str]:
    """Test functions by (class..., name) -> an AST dump (no line numbers or comments)."""
    out: dict[tuple[str, ...], str] = {}

    def walk(body: list[ast.stmt], prefix: tuple[str, ...]) -> None:
        for node in body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                if node.name.startswith("test"):
                    out[prefix + (node.name,)] = ast.dump(node)
            elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                walk(node.body, prefix + (node.name,))

    tree = _parse(src)
    walk(tree.body if tree else [], ())
    return out


def touched_tests(base_src: str | None, head_src: str) -> set[tuple[str, ...]]:
    """The tests the PR added or edited: absent at the base, or a different AST."""
    base, head = _test_defs(base_src), _test_defs(head_src)
    return {k for k, dump in head.items() if base.get(k) != dump}


def is_fix_typed(title: str, subjects: list[str]) -> bool:
    """The PR title or any commit subject is a conventional ``fix``."""
    return any(FIX_SUBJECT.match(s.strip()) for s in [title, *subjects])


def failures(
    counts: dict[str, int],
    *,
    fix_typed: bool,
    touches_code: bool,
    enforce: bool,
    refused: list[str],
    selected: int = 1,
    data_files: tuple[str, ...] = (),
) -> list[str]:
    """Why this run must fail ``gate``; empty when it may pass.

    Each reason names its cause: nothing selected, every cell n/a, or no red among
    the cells that ran are three different things to fix."""
    out = list(refused)
    if not (enforce and fix_typed and touches_code):
        return out
    if selected == 0:
        if data_files:
            out.append(
                "a fix needs a test that fails without it; no test reads "
                + ", ".join(data_files[:3])
            )
        else:
            out.append(
                "no test added or edited: a fix that changes pyrite/ or extensions/ needs a "
                "test that fails without it"
            )
    elif counts[RED] == 0:
        ran = {v: counts.get(v, 0) for v in VERDICTS if counts.get(v)}
        if not ran:
            out.append(
                "every selected cell was already at the merge base (a changed table or helper "
                "selected the test): add a case that fails without the fix"
            )
        elif set(ran) == {NA}:
            out.append(
                f"every one of the {ran[NA]} selected tests is n/a (did not pass with the fix, "
                "was skipped or was not run), so none could show red"
            )
        else:
            parts = ", ".join(f"{n} {v}" for v, n in ran.items())
            out.append(
                f"0 red of {sum(ran.values())} selected tests ({parts}): none fails without the fix"
            )
    if counts[UNEXPECTED]:
        out.append(
            f"{counts[UNEXPECTED]} unexpected pass: a test that passes without the fix "
            "does not test it; make it fail without the fix, or mark it "
            "@pytest.mark.control(reason=...) in this PR"
        )
    return out


def _module_defs(tree: ast.Module) -> dict[str, ast.stmt]:
    """Module-level names -> the statement that defines them."""
    out: dict[str, ast.stmt] = {}
    for stmt in tree.body:
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            out[stmt.name] = stmt
        else:
            targets = stmt.targets if isinstance(stmt, ast.Assign) else []
            targets = [stmt.target] if isinstance(stmt, ast.AnnAssign) else targets
            out.update({t.id: stmt for t in targets if isinstance(t, ast.Name)})
    return out


def _decorator_inputs(tree: ast.Module) -> dict[tuple[str, ...], str]:
    """Test key -> a dump of every module-level definition its decorators reach.

    A ``parametrize`` table, or the helper that builds one, is part of the test it
    decorates: the names in the decorators, then the names in what those define,
    and so on. Nothing the body reaches (a fixture, a helper it calls): that would
    select half the suite for one changed helper."""
    defs = _module_defs(tree)

    def reach(nodes: list[ast.expr]) -> str:
        seen: dict[str, ast.stmt] = {}
        todo = [n.id for node in nodes for n in ast.walk(node) if isinstance(n, ast.Name)]
        while todo:
            name = todo.pop()
            if name in defs and name not in seen:
                seen[name] = defs[name]
                todo += [n.id for n in ast.walk(defs[name]) if isinstance(n, ast.Name)]
        return "\n".join(ast.dump(seen[n]) for n in sorted(seen))

    out: dict[tuple[str, ...], str] = {}

    def walk(body: list[ast.stmt], prefix: tuple[str, ...]) -> None:
        for node in body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                if node.name.startswith("test"):
                    out[prefix + (node.name,)] = reach(node.decorator_list)
            elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                walk(node.body, prefix + (node.name,))

    walk(tree.body, ())
    return out


def table_touched_tests(base_src: str | None, head_src: str) -> set[tuple[str, ...]]:
    """Tests whose own AST did not change but whose decorators' tables or helpers did.

    The function-level selection (``touched_tests``) misses these: adding a row to a
    module-level ``parametrize`` table changes no test function (#1074 R6)."""
    base_tree, head_tree = _parse(base_src), _parse(head_src)
    if base_tree is None or head_tree is None:
        return set()
    base_inputs, head_inputs = _decorator_inputs(base_tree), _decorator_inputs(head_tree)
    own = touched_tests(base_src, head_src)
    return {
        k
        for k, dump in head_inputs.items()
        if k in base_inputs and base_inputs[k] != dump and k not in own
    }


def defined_names(src: str | None) -> set[str]:
    """The functions, classes and variables a source defines at module or class level.

    Not inside function bodies: a local helper the PR adds must not turn an
    unrelated AttributeError on the same name into "import-only" (B3).
    """
    names: set[str] = set()

    def walk(body: list[ast.stmt]) -> None:
        for stmt in body:
            if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                names.add(stmt.name)
                if isinstance(stmt, ast.ClassDef):
                    walk(stmt.body)
                continue
            targets = stmt.targets if isinstance(stmt, ast.Assign) else []
            targets = [stmt.target] if isinstance(stmt, ast.AnnAssign) else targets
            names.update(t.id for t in targets if isinstance(t, ast.Name))
            for child in ast.iter_child_nodes(stmt):  # if / try / with blocks
                if isinstance(child, ast.stmt):
                    walk([child])
                elif isinstance(child, ast.ExceptHandler):
                    walk(child.body)

    tree = _parse(src)
    walk(tree.body if tree else [])
    return names


def module_names(path: str) -> set[str]:
    """The dotted module a source file is imported as, and its last part."""
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if parts[0] == "extensions" and len(parts) > 3 and parts[2] == "src":
        parts = parts[3:]
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return {".".join(parts), parts[-1]} if parts else set()


def _named(exc: dict) -> set[str]:
    """The identifiers an exception's own arguments name (quoted, or a bare name)."""
    found: set[str] = set()
    for arg in exc.get("args", []):
        found |= set(_QUOTED.findall(arg))
        if _IDENT.match(arg):
            found.add(arg)  # monkeypatch.delattr raises AttributeError(name)
    return found


def classify(
    with_fix: dict | None,
    without: dict | None,
    *,
    collect_failed: bool,
    control: str | None,
    added: frozenset[str] | set[str],
    inherited: bool = False,
) -> tuple[str, str]:
    """(verdict, detail) for one test, from its record in each run.

    `control` is the reason a ``@pytest.mark.control`` gives (its ``reason=`` or
    the test's docstring): None without the marker, "" for a bare one. `inherited`:
    the base already had that marker on this node id.
    """
    if with_fix is None or with_fix["outcome"] != "passed":
        return NA, f"{with_fix['outcome'] if with_fix else 'not run'} with the fix"
    if collect_failed:
        return IMPORT_ONLY, "its file does not collect without the fix"
    if without is None or without["outcome"] == "skipped":
        return NA, f"{without['outcome'] if without else 'not run'} without the fix"
    if without["outcome"] == "passed":
        if control is not None and inherited:
            return INHERITED, "control marker already at the merge base"
        if control:
            return CONTROL, ""
        if control == "":
            return UNEXPECTED, "@pytest.mark.control needs a reason: reason=... or a docstring"
        return UNEXPECTED, ""
    exc = without.get("exc") or {}
    missing = _named(exc) & added if _MISSING_NAME & set(exc.get("types", [])) else set()
    if missing:
        return IMPORT_ONLY, f"{', '.join(sorted(missing))}: added by the PR"
    return RED, ""


def summary_line(counts: dict[str, int]) -> str:
    line = " · ".join(f"{counts[v]} {v}" for v in (RED, IMPORT_ONLY, UNEXPECTED, NA))
    extra = "".join(f" · {counts[v]} {v}" for v in (CONTROL, INHERITED) if counts.get(v))
    return f"verify-red: {line}{extra}"


def _key(nodeid: str) -> tuple[str, ...]:
    return tuple(nodeid.split("[", 1)[0].split("::"))  # a parameter id may hold "::"


# ---------------------------------------------------------------------------
# Reading the developer's repository (read-only)
# ---------------------------------------------------------------------------


def git(*args: str, cwd: Path) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise InfraError(f"git {' '.join(args)}: {result.stderr.strip()}")
    return result.stdout


def _show(top: Path, rev: str, path: str) -> str | None:
    r = subprocess.run(["git", "show", f"{rev}:{path}"], cwd=top, capture_output=True)
    return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else None


@dataclass
class Change:
    test_side: list[str]  # copied in for both runs
    code: list[str]  # copied in for the run with the fix
    selected: list[tuple[str, ...]]  # (file, class..., function) of each new or edited test
    pre_existing: int
    added: frozenset[str]  # identifiers the code change defines
    dep_only: frozenset[tuple[str, ...]] = frozenset()  # selected for a changed table, not a body
    data_files: tuple[str, ...] = ()  # test-side files that are not Python: goldens, fixtures
    renamed_from: dict[str, str] = field(default_factory=dict)  # test file -> its path at the base


def read_change(top: Path, mb: str) -> Change:
    tracked = git("diff", "--name-only", "--no-renames", "-z", mb, cwd=top).split("\0")
    untracked = git("ls-files", "--others", "--exclude-standard", "-z", cwd=top).split("\0")
    paths = sorted({p for p in tracked + untracked if p})
    status = git("diff", "-M", "--diff-filter=R", "--name-status", "-z", mb, cwd=top).split("\0")
    renamed_from = {status[i + 2]: status[i + 1] for i in range(0, len(status) - 2, 3)}
    test_side = [p for p in paths if is_test_side(p)]
    code = [p for p in paths if not is_test_side(p)]
    added: set[str] = set()
    for p in (p for p in code if p.endswith(".py") and (top / p).is_file()):
        before = _show(top, mb, p)
        added |= defined_names((top / p).read_text(errors="replace")) - defined_names(before)
        added |= module_names(p) if before is None else set()
    data_files = tuple(p for p in test_side if not p.endswith(".py"))
    readers = _tests_reading(top, [PurePosixPath(p).name for p in data_files])
    selected, pre_existing, dep_only = [], 0, set()
    for f in sorted({p for p in test_side if p.endswith(".py")} | readers):
        if not (top / f).is_file() or not is_test_file(f):
            continue
        head = (top / f).read_text(errors="replace")
        base_src = _show(top, mb, renamed_from.get(f, f))
        touched = touched_tests(base_src, head)
        via_table = table_touched_tests(base_src, head) - touched
        if f in readers:  # the data it reads changed: every test in it is claimed
            touched = set(_test_defs(head))
            via_table = set()
        selected += [(f, *k) for k in sorted(touched | via_table)]
        dep_only |= {(f, *k) for k in via_table}
        pre_existing += len(_test_defs(head)) - len(touched | via_table)
    return Change(
        test_side,
        code,
        selected,
        pre_existing,
        frozenset(added),
        frozenset(dep_only),
        data_files,
        {f: b for f, b in renamed_from.items() if f in test_side},
    )


def _tests_reading(top: Path, names: list[str]) -> set[str]:
    """The test files whose source mentions one of these data files by name."""
    if not names:
        return set()
    listed = git("ls-files", "--cached", "--others", "--exclude-standard", "-z", cwd=top)
    found = set()
    for p in (p for p in listed.split("\0") if p and is_test_file(p) and (top / p).is_file()):
        text = (top / p).read_text(errors="replace")
        if any(n in text for n in names):
            found.add(p)
    return found


# ---------------------------------------------------------------------------
# The throwaway tree
# ---------------------------------------------------------------------------


def overlay(src: Path, dst: Path, paths: list[str]) -> None:
    """Make each path in `dst` what it is in `src`: a copy, or absent."""
    for p in paths:
        s, d = src / p, dst / p
        if d.is_symlink() or d.is_file():
            d.unlink()
        if s.is_symlink() or s.is_file():
            d.parent.mkdir(parents=True, exist_ok=True)
            d.symlink_to(os.readlink(s)) if s.is_symlink() else shutil.copy2(s, d)


def import_roots(tree: Path) -> list[Path]:
    return [tree, *sorted(tree.glob("extensions/*/src"))]


def packages(root: Path) -> set[str]:
    """The top-level packages a checkout provides (its root, and extensions/*/src)."""
    return {p.parent.name for r in import_roots(root) for p in r.glob("*/__init__.py")}


def check_imports(
    python: str, tree: Path, stubs: Path, env: dict[str, str], head_pkgs: set[str]
) -> None:
    """#189: the tests must import the tree under test and nothing else.

    A package in the tree must resolve into it. A package only the PR's head has
    (a new extension, whose editable install is the head's) must not import at
    all: it resolves to a stub in `stubs` that raises ModuleNotFoundError (B2).
    """
    here = packages(tree)
    shutil.rmtree(stubs, ignore_errors=True)
    for name in head_pkgs - here:
        (stubs / name).mkdir(parents=True)
        (stubs / name / "__init__.py").write_text(
            f'raise ModuleNotFoundError("No module named {name!r}", name={name!r})\n'
        )
    code = (
        "import importlib.util as u, json, sys\n"
        "print(json.dumps({n: getattr(u.find_spec(n), 'origin', None) for n in sys.argv[1:]}))"
    )
    names = sorted(here | head_pkgs)
    r = subprocess.run(
        [python, "-c", code, *names], cwd=tree, env=env, capture_output=True, text=True
    )
    if r.returncode != 0:
        raise InfraError(f"could not locate the packages under test: {r.stderr.strip()}")
    for name, origin in json.loads(r.stdout).items():
        where = tree if name in here else stubs
        if not origin or not Path(origin).resolve().is_relative_to(where.resolve()):
            raise InfraError(
                f"{name} resolves outside the tree under test ({origin}): a verdict about "
                "code the interpreter is not importing is not evidence"
            )


@dataclass
class Run:
    results: dict[str, dict]  # nodeid -> the record that decides its outcome
    collect_failed: set[str]  # test files that did not collect
    timed_out: bool


def scratch_env(home: Path) -> dict[str, str]:
    """HOME, Pyrite's config and data, and the XDG dirs, all in `home`: new tests
    run against buggy code, and the developer's own config is not theirs to
    write (the 2026-09-23 incident)."""
    env = {"HOME": str(home), "PYRITE_CONFIG_DIR": str(home), "PYRITE_DATA_DIR": str(home)}
    for var, sub in (
        ("XDG_CONFIG_HOME", ".config"),
        ("XDG_DATA_HOME", ".local/share"),
        ("XDG_CACHE_HOME", ".cache"),
        ("XDG_STATE_HOME", ".local/state"),
    ):
        (home / sub).mkdir(parents=True, exist_ok=True)
        env[var] = str(home / sub)
    # Model downloads are a read-mostly cache, not config: keep the real one.
    env["HF_HOME"] = os.environ.get("HF_HOME") or str(Path.home() / ".cache" / "huggingface")
    return env


def pytest_env(tree: Path, work: Path, record: Path) -> dict[str, str]:
    roots = [*map(str, import_roots(tree)), str(work / "stubs"), str(work / "plugin")]
    return {
        **os.environ,
        **scratch_env(work / "home"),
        # Both runs import one tree whose sources change in between; a .pyc the
        # first wrote (same size, same mtime second) would serve it to the second.
        "PYTHONDONTWRITEBYTECODE": "1",
        # A cell's identity includes the repr of its values; a set of strings
        # reprs in another order in another process unless hashing is fixed.
        "PYTHONHASHSEED": "0",
        "PYTHONPATH": os.pathsep.join([*roots, os.environ.get("PYTHONPATH", "")]),
        "VERIFY_RED_RECORD": str(record),
        "VERIFY_RED_SELECT": str(work / "select.json"),
    }


BASE_COLLECT_TIMEOUT = 60  # seconds: nothing runs, so a file that needs longer is not collecting


Cell = tuple[tuple[str, ...], str]  # (file, class..., function), the parameter values


@dataclass
class Base:
    """What the merge base's test files collect to, keyed by a cell's identity: the
    test function and its parameter values, never the generated node id."""

    controls: dict[Cell, str | None]  # the control reason a cell carried there (any origin)
    bad: dict[Cell, list[str]]  # where a refusable mark on it came from
    failed: set[str]  # test files (head paths) that did not collect at the base
    timed_out: bool = False

    def values_of(self, key: tuple[str, ...]) -> set[str]:
        return {v for k, v in self.controls if k == key}


def collect_base(
    python: str,
    tree: Path,
    work: Path,
    files: dict[str, str],
    timeout: float,
    head_pkgs: set[str],
) -> Base:
    """Collect-only run of the merge base's copy of each test file (`files`: head
    path -> base path). Nothing runs. A file absent at the base is all new; one that
    does not collect there is reported, and its cells count as new."""
    present = {h: b for h, b in files.items() if (tree / b).is_file()}
    if not present:
        return Base({}, {}, set())
    back = {b: h for h, b in present.items()}
    record = work / "base.jsonl"
    env = {**pytest_env(tree, work, record), "VERIFY_RED_BASE_COLLECT": "1"}
    check_imports(python, tree, work / "stubs", env, head_pkgs)
    cmd = [python, "-m", "pytest", *sorted(back), "-p", "verify_red_record"]
    timed_out = False
    try:
        with open(work / "base.log", "w") as out:
            subprocess.run(
                [*cmd, "-p", "no:cacheprovider", "--collect-only", "-q"]
                + ["--continue-on-collection-errors"],
                cwd=tree,
                env=env,
                stdout=out,
                stderr=subprocess.STDOUT,
                timeout=min(timeout, BASE_COLLECT_TIMEOUT),
            )
    except subprocess.TimeoutExpired:
        timed_out = True
    base = Base({}, {}, set(), timed_out)
    for rec in map(json.loads, record.read_text().splitlines() if record.exists() else []):
        if "collect" in rec:
            base.failed.add(back.get(rec["collect"], rec["collect"]))
        elif "base" in rec:
            parts = _key(rec["base"])
            cell = ((back.get(parts[0], parts[0]), *parts[1:]), rec["values"])
            base.controls[cell] = rec["control"]
            base.bad[cell] = rec["bad"]
    return base


def run_pytest(
    python: str, tree: Path, work: Path, name: str, timeout: float, head_pkgs: set[str]
) -> Run:
    record = work / f"{name}.jsonl"
    env = pytest_env(tree, work, record)
    check_imports(python, tree, work / "stubs", env, head_pkgs)
    files = sorted({k[0] for k in json.loads((work / "select.json").read_text())})
    cmd = [python, "-m", "pytest", *files, "-p", "verify_red_record", "-p", "no:cacheprovider"]
    log = work / f"{name}.log"
    try:
        with open(log, "w") as out:
            subprocess.run(
                [*cmd, "--continue-on-collection-errors"],
                cwd=tree,
                env=env,
                stdout=out,
                stderr=subprocess.STDOUT,
                timeout=timeout,
            )
        timed_out = False
    except subprocess.TimeoutExpired:
        timed_out = True
    phases: dict[str, list[dict]] = {}
    collect_failed = set()
    collected = False
    for rec in map(json.loads, record.read_text().splitlines() if record.exists() else []):
        if "collect" in rec:
            collect_failed.add(rec["collect"])
        elif "collected" in rec:
            collected = True
        else:
            phases.setdefault(rec["nodeid"], []).append(rec)
    if not phases and not collect_failed and not collected and not timed_out:
        tail = "\n  ".join(log.read_text().splitlines()[-15:])
        if name == "with":  # pytest itself failed (a conftest, a plugin): no verdict is honest
            raise InfraError(f"the run with the fix recorded no test; pytest said:\n  {tail}")
        print(f"verify-red: the {name} run recorded no test:\n  {tail}", file=sys.stderr)
    results = {}
    for nodeid, recs in phases.items():
        # A failed or skipped phase decides; otherwise it passed only once its
        # call phase finished (a run killed mid-test has recorded setup alone).
        final = next((r for r in recs if r["outcome"] == "failed"), None)
        final = final or next((r for r in recs if r["outcome"] == "skipped"), None)
        final = final or next((r for r in recs if r["when"] == "call"), None)
        if final:
            # Where a refused marker came from can show in any phase, not only the deciding one.
            bad = sorted({b for r in recs for b in r.get("bad", [])})
            results[nodeid] = {**final, "bad": bad}
    return Run(results, collect_failed, timed_out)


def forget_stale_trees(top: Path) -> None:
    """Drop the registration of this tool's own throwaway trees whose directory is
    gone (a killed run, once $TMPDIR was cleared). `git worktree prune` would
    drop anyone's; this reads each registration's gitdir and touches only ours."""
    common = Path(git("rev-parse", "--git-common-dir", cwd=top).strip())
    admin_dirs = (top / common / "worktrees").glob("*/gitdir")
    for gitdir in admin_dirs:
        tree = Path(gitdir.read_text().strip()).parent
        if tree.name == "tree" and tree.parent.name.startswith("verify-red-") and not tree.exists():
            shutil.rmtree(gitdir.parent)


def verify(
    python: str, top: Path, mb: str, change: Change, timeout: float
) -> tuple[Run, Run, Base]:
    """(run without the fix, run with it, the base's own cells), in one throwaway tree."""
    forget_stale_trees(top)
    head_pkgs = packages(top)
    work = Path(tempfile.mkdtemp(prefix="verify-red-"))
    tree = work / "tree"
    try:
        git("worktree", "add", "--detach", "-q", str(tree), mb, cwd=top)
        (work / "plugin").mkdir()
        shutil.copy(PLUGIN, work / "plugin")
        (work / "select.json").write_text(json.dumps(change.selected))
        files = {k[0]: change.renamed_from.get(k[0], k[0]) for k in change.selected}
        base = collect_base(python, tree, work, files, timeout, head_pkgs)
        overlay(top, tree, change.test_side)
        without = run_pytest(python, tree, work, "without", timeout, head_pkgs)
        overlay(top, tree, change.code)
        return without, run_pytest(python, tree, work, "with", timeout, head_pkgs), base
    finally:
        remove = ["git", "worktree", "remove", "--force", str(tree)]
        subprocess.run(remove, cwd=top, capture_output=True)
        shutil.rmtree(work, ignore_errors=True)


def _cell(nodeid: str, *records: dict | None) -> Cell:
    """A cell's identity: its test function and its parameter values."""
    rec = next((r for r in records if r), {})
    return _key(nodeid), rec.get("values", "")


def verdicts(
    change: Change, without: Run, with_fix: Run, base: Base | None = None
) -> list[tuple[str, str, str]]:
    """(nodeid, verdict, detail) for every claimed cell, parameters expanded.

    A cell is this PR's claim when its (test function, parameter values) pair is
    absent at the merge base: a new function, a new row, a row whose values changed.
    A test selected only because a table or helper changed is graded on those
    cells alone; every cell of one whose body or data changed is claimed. The node
    id is only a label (an inserted row shifts index ids; an edited row keeps its
    explicit one). A control the base already carried on the same cell is
    *inherited*."""
    base = base or Base({}, {}, set())
    rows = []
    seen: set[tuple[str, ...]] = set()
    for nodeid in sorted(set(with_fix.results) | set(without.results)):
        w, wo = with_fix.results.get(nodeid), without.results.get(nodeid)
        cell = _cell(nodeid, w, wo)
        seen.add(cell[0])
        if cell[0] in change.dep_only and cell[1] in base.values_of(cell[0]):
            continue  # a row the base had, untouched: not this PR's claim
        control, inherited = (w or wo or {}).get("control"), cell in base.controls
        if control is None and base.controls.get(cell) is not None:
            control = base.controls[cell]  # refusable now, but the base had it too: inherited
        verdict, detail = classify(
            w,
            wo,
            collect_failed=nodeid.split("::")[0] in without.collect_failed,
            control=control,
            inherited=inherited and base.controls.get(cell) is not None,
            added=change.added,
        )
        if verdict == NA and (w is None and with_fix.timed_out or wo is None and without.timed_out):
            detail = "not reached: the run timed out"
        rows.append((nodeid, verdict, detail))
    for k in (k for k in change.selected if k not in seen):
        if k[0] in with_fix.collect_failed:
            detail = "its file does not collect with the fix"
        elif with_fix.timed_out or without.timed_out:
            detail = "not reached: the run timed out"
        else:
            detail = "not collected"
        rows.append(("::".join(k), NA, detail))
    return rows


def refusals(without: Run, with_fix: Run, base: Base) -> list[str]:
    """Control markers a PR adds that the test did not declare on itself or on one of
    its parameters, each with where it came from. One the base already had on the
    same cell is not this PR's to answer for."""
    out: set[str] = set()
    for nodeid in set(with_fix.results) | set(without.results):
        w, wo = with_fix.results.get(nodeid), without.results.get(nodeid)
        cell = _cell(nodeid, w, wo)
        bad = {b for r in (w, wo) if r for b in r.get("bad", [])}
        for origin in sorted(bad - set(base.bad.get(cell, []))):
            out.add(
                f"{nodeid.split('::')[0]}: control marker not declared on the test or one of "
                f"its parameters: {origin} ({'::'.join(cell[0][1:])})"
            )
    return sorted(out)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

LEGEND = (
    "*red* (not listed): fails without the fix -- the evidence a review wants. *import-only*: "
    "fails only because a name the PR adds is missing -- weak. *unexpected pass*: passes "
    "without the fix, so it does not test the change (`@pytest.mark.control(reason=...)` "
    "marks a deliberate negative control). *inherited*: passes without the fix and its "
    "control marker was already on that test at the merge base -- not a failure, not "
    "evidence. *n/a*: no claim."
)


def _default_base(top: Path) -> str:
    probe = ["git", "rev-parse", "-q", "--verify", "origin/dev"]
    return (
        "origin/dev"
        if subprocess.run(probe, cwd=top, capture_output=True).returncode == 0
        else "dev"
    )


def report(args: argparse.Namespace) -> tuple[dict, str]:
    """(the evidence, the markdown for stdout and the step summary)."""
    top = Path(git("rev-parse", "--show-toplevel", cwd=Path.cwd()).strip())
    mb = git("merge-base", args.base or _default_base(top), "HEAD", cwd=top).strip()
    change = read_change(top, mb)
    subjects = git("log", "--format=%s", f"{mb}..HEAD", cwd=top).splitlines()
    checked_out = git("rev-parse", "HEAD", cwd=top).strip()
    fix_typed = is_fix_typed(args.title or "", subjects)
    evidence: dict = {
        "pr": args.pr,
        "fix_typed": fix_typed,
        # CI checks out the PR's merge commit, which joins to nothing: the PR's
        # own head is passed in (--head-sha), the merge commit kept apart (B5).
        "head": args.head_sha or checked_out,
        "merge_commit": checked_out,
        "merge_base": mb,
        "fix_commits": sum(bool(FIX_SUBJECT.match(s)) for s in subjects),
        "verify_red": None,
        "diff_coverage": None,
    }
    out = ["## verify-red: do this PR's new tests fail without its change?", ""]
    ci = os.environ.get("GITHUB_ACTIONS") == "true"
    code = [p for p in change.code if not is_inert(p)]
    counts = dict.fromkeys(VERDICTS, 0)
    refused: list[str] = []
    if not code or not change.selected:
        if code and change.data_files:
            named = ", ".join(change.data_files[:3])
            why = f"no test selected: no test reads {named}"
        elif code:
            why = "no new or edited test"
        elif not change.test_side and not change.code:
            why = "empty diff: no changed files"
        else:
            test_files, inert_files = len(change.test_side), len(change.code)
            if test_files and inert_files:
                kind = "tests and inert files only"
            else:
                kind = "tests-only change" if test_files else "inert-only change"
            why = (
                f"{kind}: {test_files} test-side file{'s' * (test_files != 1)}, "
                f"{inert_files} inert file{'s' * (inert_files != 1)}, no fix to grade against"
            )
        out.append(f"verify-red: nothing to verify ({why}).")
        if code and ci:
            print(f"::warning title=verify-red::this PR changes code but has {why}", flush=True)
    else:
        without, with_fix, base = verify(args.python, top, mb, change, args.timeout)
        rows = verdicts(change, without, with_fix, base)
        refused = refusals(without, with_fix, base)
        counts = {v: sum(r[1] == v for r in rows) for v in VERDICTS}
        n = change.pre_existing
        tail = f"({n} pre-existing test{'s' * (n != 1)} in these files not run)"
        out += [f"{summary_line(counts)} {tail}", ""]
        out += [f"base collect failed for {f}: its cells count as new" for f in sorted(base.failed)]
        if base.timed_out:
            out.append("base collect timed out: every cell counts as new")
        out += [""] if base.failed or base.timed_out else []
        listed = [r for r in rows if r[1] != RED]
        if listed:
            out += ["| Test | Verdict | Detail |", "|---|---|---|"]
            out += [f"| `{t}` | {v} | {d.replace('|', '/')[:160]} |" for t, v, d in listed]
            out.append("")
        out.append(LEGEND)
        for t, v, _ in listed:
            if ci and v == UNEXPECTED:
                print(
                    f"::warning file={t.split('::')[0]},title=verify-red::{t} passes without the fix"
                )
        evidence["verify_red"] = {**counts, "pre-existing": n}
    touches_code = any(PurePosixPath(p).parts[0] in ("pyrite", "extensions") for p in code)
    evidence["failures"] = failures(
        counts,
        fix_typed=fix_typed,
        touches_code=touches_code,
        enforce=args.enforce_fix,
        refused=refused,
        selected=len(change.selected),
        data_files=change.data_files,
    )
    if evidence["failures"]:
        out += ["", "**verify-red fails this PR:**", ""]
        out += [f"- {f}" for f in evidence["failures"]]
    if args.diff_cover_json and Path(args.diff_cover_json).is_file():
        dc = json.loads(Path(args.diff_cover_json).read_text())
        d = evidence["diff_coverage"] = {
            # diff-cover says 100% when no measured line changed: that is no data (B5).
            "percent": dc.get("total_percent_covered") if dc.get("total_num_lines") else None,
            "lines": dc.get("total_num_lines"),
            "uncovered": dc.get("total_num_violations"),
        }
        if d["percent"] is None:
            out += ["", "diff coverage: n/a (no changed line is measured)"]
        else:
            out += ["", f"diff coverage: {d['percent']}% of {d['lines']} changed lines (advisory)"]
    return evidence, "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--base", help="the integration ref (default origin/dev, else dev)")
    parser.add_argument("--python", default=os.environ.get("VERIFY_RED_PYTHON", sys.executable))
    parser.add_argument("--summary", default=os.environ.get("GITHUB_STEP_SUMMARY"))
    parser.add_argument("--timeout", type=float, default=600, help="seconds per pytest run")
    parser.add_argument("--json", help="write the evidence (verdict counts, diff coverage) here")
    parser.add_argument("--diff-cover-json", help="diff-cover's JSON report, carried into --json")
    parser.add_argument("--pr", type=int, help="the pull request number, for --json")
    parser.add_argument("--head-sha", help="the PR's head commit, for --json (CI: not the merge)")
    parser.add_argument("--title", help="the pull request title, for --enforce-fix")
    parser.add_argument(
        "--enforce-fix",
        action="store_true",
        help="local use only (CI does not pass it, #1074): exit 1 for a fix-typed PR that "
        "changes pyrite/ or extensions/ and has 0 red "
        "tests or an unexpected pass",
    )
    args = parser.parse_args(argv)
    try:
        evidence, text = report(args)
    except InfraError as exc:
        print(f"verify-red: could not run: {exc}", file=sys.stderr)
        if args.summary:
            with open(args.summary, "a", encoding="utf-8") as fh:
                fh.write(f"## verify-red: could not run\n\n```\n{exc}\n```\n")
        return 2
    print(text, flush=True)
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")
    if args.json:
        Path(args.json).write_text(json.dumps(evidence, indent=2) + "\n")
    for reason in evidence["failures"]:
        print(f"::error title=verify-red::{reason}", file=sys.stdout, flush=True)
        print(f"verify-red: {reason}", file=sys.stderr)
    return 1 if evidence["failures"] else 0


if __name__ == "__main__":
    sys.exit(main())
