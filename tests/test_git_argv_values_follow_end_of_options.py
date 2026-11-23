"""A variable never reaches git where an option can go.

Property: every git command Pyrite builds passes each value that comes from a
variable (a path, a branch, a ref, a remote, a URL, a key) after ``--`` or
``--end-of-options``, or as the value of an option that takes one (``-m <msg>``,
``--branch <b>``), or after a validation in the same function that refuses a
leading "-". Git reads a leading "-" in any other slot as an option.

The test walks the AST of ``pyrite/`` and ``extensions/*/src``, finds every git
argv (a list or tuple literal whose first element is "git", or that starts by
splicing in one, including ones grown later in the same function with
``.append``, ``.extend``, ``.insert`` or ``+=``) and fails on a non-literal
element in an option slot. A new call site that breaks the rule fails here.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.guard

END_OF_OPTIONS = {"--", "--end-of-options"}

# Options whose next argv element is their value, not a new option. Small and
# explicit on purpose: add one only when git documents that it takes a value.
VALUE_OPTIONS = {"-c", "-m", "-b", "--branch", "--depth"}

# (file, function) -> why the unmarked variable is safe there, or the follow-up.
# An entry that no longer matches a finding fails the test, so this list shrinks.
ALLOWLIST: dict[tuple[str, str], str] = {
    (
        "pyrite/services/git_service.py",
        "GitService.merge_branch",
    ): (
        "`git checkout <into>`: git checkout has no --end-of-options (it reads it as a "
        "pathspec); `into` is refused unless GitService.is_valid_branch_name(into) at the "
        "top of the same function, which refuses a leading '-'."
    ),
    (
        "pyrite/utils/git_hardening.py",
        "_account_values",
    ): (
        "follow-up: git_hardening.py is in flight elsewhere; `*args` are the two literal "
        "`--get`/key calls of the network-home reader; give the key after `--` there."
    ),
}


def _is_str(node: ast.AST, *values: str) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and (not values or node.value in values)
    )


def _items(node: ast.AST, env: dict[str, list]) -> list:
    """The elements of a list or tuple literal, in order: ("lit", text),
    ("var", source) or ("safe", source). A starred name known to be an argv is
    spliced in."""
    items: list = []
    for elt in node.elts:  # type: ignore[attr-defined]
        if isinstance(elt, ast.Starred):
            if isinstance(elt.value, ast.Name) and elt.value.id in env:
                items.extend(env[elt.value.id])
            else:
                items.append(("var", ast.unparse(elt)))
        else:
            items.append(_item(elt))
    return items


def _item(elt: ast.AST) -> tuple[str, str]:
    if _is_str(elt):
        return ("lit", elt.value)  # type: ignore[attr-defined]
    # An f-string that opens with fixed text not starting with "-" (for example
    # f"refs/heads/{name}") cannot be read as an option.
    if (
        isinstance(elt, ast.JoinedStr)
        and elt.values
        and _is_str(elt.values[0])
        and elt.values[0].value  # type: ignore[attr-defined]
        and not elt.values[0].value.startswith("-")  # type: ignore[attr-defined]
    ):
        return ("safe", ast.unparse(elt))
    return ("var", ast.unparse(elt))


def _is_git_argv(items: list) -> bool:
    return bool(items) and items[0] == ("lit", "git")


def _violations(items: list) -> list[str]:
    """Sources of the variable elements that sit where git reads an option."""
    out: list[str] = []
    after_end = False
    expect_value = False
    for kind, text in items:
        if kind == "lit":
            if text in END_OF_OPTIONS:
                after_end = True
            expect_value = (not after_end) and text in VALUE_OPTIONS
            continue
        if kind == "var" and not after_end and not expect_value:
            out.append(text)
        expect_value = False
    return out


class _Scope:
    """One function (or the module body): argv variables, in source order."""

    def __init__(self, body_nodes: list[ast.AST], base_env: dict[str, list]):
        self.nodes = body_nodes
        self.env: dict[str, list] = dict(base_env)
        self.found: list[list] = []  # every finished git argv, as items

    def run(self) -> None:
        argv_vars: dict[str, list] = {}
        consumed: set[int] = set()
        for node in self.nodes:
            if id(node) in consumed:
                continue
            if isinstance(node, ast.Assign) and isinstance(node.value, (ast.List, ast.Tuple)):
                items = _items(node.value, self.env)
                consumed.add(id(node.value))
                targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
                if _is_git_argv(items) and targets:
                    for name in targets:
                        if name in argv_vars:
                            self.found.append(argv_vars[name])
                        argv_vars[name] = items
                        self.env[name] = items
                elif _is_git_argv(items):
                    self.found.append(items)
            elif (
                isinstance(node, ast.AugAssign)
                and isinstance(node.op, ast.Add)
                and isinstance(node.target, ast.Name)
                and node.target.id in argv_vars
                and isinstance(node.value, (ast.List, ast.Tuple))
            ):
                consumed.add(id(node.value))
                argv_vars[node.target.id].extend(_items(node.value, self.env))
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in argv_vars
                and node.func.attr in {"append", "extend", "insert"}
            ):
                items = argv_vars[node.func.value.id]
                args = node.args
                if node.func.attr == "append" and args:
                    items.append(_item(args[0]))
                elif node.func.attr == "extend" and args:
                    if isinstance(args[0], (ast.List, ast.Tuple)):
                        consumed.add(id(args[0]))
                        items.extend(_items(args[0], self.env))
                    else:
                        items.append(("var", ast.unparse(args[0])))
                elif node.func.attr == "insert" and len(args) == 2:
                    if isinstance(args[0], ast.Constant) and isinstance(args[0].value, int):
                        items.insert(args[0].value, _item(args[1]))
                    else:
                        items.append(("var", ast.unparse(args[1])))
            elif isinstance(node, (ast.List, ast.Tuple)):
                items = _items(node, self.env)
                if _is_git_argv(items):
                    self.found.append(items)
        self.found.extend(argv_vars.values())


def _scope_nodes(scope: ast.AST) -> list[ast.AST]:
    """Nodes of `scope` in source order, not descending into nested functions."""
    out: list[ast.AST] = []

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            out.append(child)
            visit(child)

    visit(scope)
    out.sort(key=lambda n: (getattr(n, "lineno", 0), getattr(n, "col_offset", 0)))
    return out


def findings_in_source(source: str) -> tuple[int, list[tuple[str, str]]]:
    """(git argvs found, [(function qualname, offending element source)])."""
    tree = ast.parse(source)
    total = 0
    bad: list[tuple[str, str]] = []

    module_scope = _Scope(_scope_nodes(tree), {})
    module_scope.run()
    module_env = {k: v for k, v in module_scope.env.items() if _is_git_argv(v)}
    total += len(module_scope.found)
    bad += [("<module>", v) for items in module_scope.found for v in _violations(items)]

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                walk(child, f"{prefix}{child.name}.")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{prefix}{child.name}"
                scope = _Scope(_scope_nodes(child), module_env)
                scope.run()
                nonlocal total
                total += len(scope.found)
                bad.extend((name, v) for items in scope.found for v in _violations(items))
                walk(child, f"{name}.")
            else:
                walk(child, prefix)

    walk(tree, "")
    return total, bad


def _source_files() -> list[Path]:
    files = sorted((ROOT / "pyrite").rglob("*.py"))
    for ext in sorted((ROOT / "extensions").glob("*/src")):
        files += sorted(ext.rglob("*.py"))
    return files


def _scan() -> tuple[int, dict[tuple[str, str], list[str]]]:
    total = 0
    bad: dict[tuple[str, str], list[str]] = {}
    for path in _source_files():
        n, found = findings_in_source(path.read_text())
        total += n
        rel = path.relative_to(ROOT).as_posix()
        for func, elt in found:
            bad.setdefault((rel, func), []).append(elt)
    return total, bad


def test_every_git_argv_value_is_after_end_of_options_or_in_a_value_slot():
    total, bad = _scan()
    assert total >= 30, f"the walker found only {total} git argvs; it is no longer looking"
    offenders = {k: v for k, v in bad.items() if k not in ALLOWLIST}
    assert not offenders, (
        "a variable sits where git reads an option; put it after `--` or "
        "`--end-of-options`, or validate it (and add the site to ALLOWLIST with the "
        "validation): "
        + "; ".join(f"{f}:{fn} -> {', '.join(v)}" for (f, fn), v in sorted(offenders.items()))
    )


def test_the_allowlist_names_only_sites_that_still_break_the_rule():
    _, bad = _scan()
    stale = sorted(k for k in ALLOWLIST if k not in bad)
    assert not stale, f"ALLOWLIST entries with nothing to allow (remove them): {stale}"


# --- the walker itself: what it accepts and what it refuses -----------------------


@pytest.mark.parametrize(
    "body",
    [
        'subprocess.run(["git", "rm", "--force", path])',
        'subprocess.run(["git", "add", "-A", p])',
        'cmd = ["git", "worktree", "remove"]\nif force:\n    cmd.append("--force")\ncmd.append(path)',
        'cmd = ["git", "log"]\ncmd.insert(2, rev)',
        'cmd = ["git", "diff"]\ncmd += [a, "--", b]\ncmd.append(c)\ncmd.extend(["--end-of-options", d])\n',
        'subprocess.run(["git", "diff", "--name-only", f"{a}..HEAD"])',
        "subprocess.run([*READ, rev])",
    ],
)
def test_the_walker_refuses_a_variable_where_an_option_can_go(body):
    src = "READ = ('git', 'cat-file')\ndef f():\n" + "".join(
        f"    {l}\n" for l in body.splitlines()
    )
    _, bad = findings_in_source(src)
    assert bad, f"not flagged: {body}"


@pytest.mark.parametrize(
    "body",
    [
        'subprocess.run(["git", "rm", "--quiet", "--", path])',
        'subprocess.run(["git", "commit", "-m", message])',
        'subprocess.run(["git", "-c", "a=b", "clone", "--branch", branch, "--", url, dest])',
        'cmd = ["git", "log"]\nif rev:\n    cmd.extend(["--end-of-options", rev])\ncmd.extend(["--", path])',
        'subprocess.run(["git", "config", "--get", "--", f"branch.{b}.remote"])',
        'subprocess.run(["git", "check-ref-format", f"refs/heads/{name}"])',
        'subprocess.run([*READ, "--end-of-options", rev])',
        'subprocess.run(["not-git", path])',
    ],
)
def test_the_walker_accepts_what_the_property_allows(body):
    src = "READ = ('git', 'cat-file')\ndef f():\n" + "".join(
        f"    {l}\n" for l in body.splitlines()
    )
    _, bad = findings_in_source(src)
    assert not bad, f"flagged: {body}: {bad}"
