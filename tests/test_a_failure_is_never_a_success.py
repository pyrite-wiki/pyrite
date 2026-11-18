"""A failure is never a success-shaped answer: four shrink-only baselines (T4-1, #1531).

The invariant (epic #1531, ``kb/design.md`` principles 2 and 8): a handler
returns a result or raises a ``PyriteError``; three transport boundaries turn
an exception into the one envelope. This test stops the side doors from
growing while later T4 slices close them. Adding one site of any of the four
shapes below fails here; removing one fails here too until its baseline line
is lowered (a line that stays high would leave room for a new site to hide in).

The baselines are in ``tests/failure_shapes_baseline.txt``, one line per
file (or per handler group), so two pull requests lowering different files
never touch the same line (#1608). Every count is what ``dev`` has today.

1. ``HTTPException(`` calls outside ``server/errors.py`` and
   ``server/endpoints/write_refusal.py``, which build the envelope (target 0).
   Count: AST calls named ``HTTPException`` in ``pyrite/*.py``, 163 in 24
   files (``git grep -c 'HTTPException(' -- 'pyrite/*.py'`` says 166 lines, which
   includes two docstring mentions and the builder's own call). Extensions have none.
2. A dict literal with an ``"error"`` key and no ``"error_code"`` key
   (target 0 in ``pyrite/``, shrink-only in ``extensions/``). A dict that
   spreads ``**other`` is counted too: it cannot show a code. Counted here by
   AST over ``pyrite/`` and ``extensions/*/src``; 134 sites in 25 files.
   Blind spots, stated: ``result["error"] = ...`` and ``dict(error=...)``.
3. An upper-case code literal passed to an error builder (``_error(CODE, ...)``,
   ``build_error(msg, CODE)``, any ``error_code="CODE"`` keyword, a
   ``"error_code": "CODE"`` dict entry) that no exception class carries. The
   code table is ``error_code`` on every ``PyriteError`` subclass in
   ``pyrite/exceptions.py`` (100 sites in 16 files) until T4-2 lands ``pyrite/error_codes.py``: swap
   ``_code_table`` then and baseline 3 goes to zero.
4. A broad handler (``except Exception`` / ``BaseException``), counted by ruff,
   not by a second walk: ``ruff check --select BLE001 --ignore-noqa --isolated
   --output-format json pyrite extensions/*/src`` (243 handlers in 76 files).
   ``--ignore-noqa`` so the dead ``# noqa: BLE001`` markers cannot hide a new
   handler; ``--isolated`` so the fence does not move with ``pyproject.toml``.
   ``tests/``, ``scripts/`` and ``extensions/*/tests`` are out of scope (T4-8
   decides). T4-8 ends with ruff itself as the judge and deletes this shape.

Shape 4 also carries the classification: every handler, by (file, function),
is ``swallow-to-default`` (the failure becomes a default and the caller goes
on), ``logs-and-re-raises`` (the handler contains a ``raise``; checked
against the AST both ways) or ``boundary-backstop`` (the failure is turned into a
reported one: an error result, an error list, the CLI error path, or a
transport root). Swallow-to-default is the debt T4-8 clears: no row of that
class may remain without an issue number. The ``boundary-backstop`` class is
wide on purpose (see the PR report, "Unsure"). A handler ruff does not flag
(it re-raises, or ruff judges it logged) is not in the table.

A function's rows must sum to the handlers ruff finds in it. A new handler in a
listed function raises that sum and fails; a handler in a new function has no row.
"""

from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import pytest

_control = pytest.mark.control(
    reason="a ratchet over code this PR does not change: it guards the next failure-shaped site"
)

REPO = Path(__file__).resolve().parent.parent
BASELINE_FILE = Path(__file__).with_name("failure_shapes_baseline.txt")
ROOTS = [REPO / "pyrite", *sorted((REPO / "extensions").glob("*/src"))]

#: The two modules that build the REST envelope; shape 1 does not count them.
HTTP_BUILDERS = {"pyrite/server/errors.py", "pyrite/server/endpoints/write_refusal.py"}

SHAPES = ("http_exception", "error_dict", "unknown_code")
SWALLOW = "swallow-to-default"
RERAISE = "logs-and-re-raises"
BACKSTOP = "boundary-backstop"
CLASSES = (SWALLOW, RERAISE, BACKSTOP)

_CODE = re.compile(r"^[A-Z][A-Z0-9_]+$")


# --- the baseline file --------------------------------------------------------


def _read_baseline() -> tuple[
    dict[str, dict[str, int]], dict[tuple[str, str, str], tuple[int, str]]
]:
    """(shape -> path -> count, (path, function, class) -> (count, reason))."""
    counts: dict[str, dict[str, int]] = {shape: {} for shape in SHAPES}
    handlers: dict[tuple[str, str, str], tuple[int, str]] = {}
    for number, line in enumerate(BASELINE_FILE.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("|")]
        kind = parts[0]
        where = f"{BASELINE_FILE.name}:{number}"
        if kind in SHAPES:
            assert len(parts) == 3, f"{where}: want '{kind} | <count> | <path>'"
            path = parts[2]
            assert path not in counts[kind], f"{where}: {path} is listed twice for {kind}"
            counts[kind][path] = int(parts[1])
        elif kind == "broad_except":
            assert len(parts) == 6, (
                f"{where}: want 'broad_except | path | function | class | n | reason'"
            )
            _, path, function, klass, n, reason = parts
            assert klass in CLASSES, f"{where}: class {klass!r} is not one of {CLASSES}"
            key = (path, function, klass)
            assert key not in handlers, f"{where}: {key} is listed twice"
            handlers[key] = (int(n), reason)
        else:
            raise AssertionError(f"{where}: unknown line kind {kind!r}")
    return counts, handlers


def _compare(shape: str, observed: dict[str, int], baseline: dict[str, int]) -> list[str]:
    """What is wrong between a scan and its baseline: growth and stale room."""
    problems = []
    for path in sorted(set(observed) | set(baseline)):
        now, allowed = observed.get(path, 0), baseline.get(path, 0)
        if now > allowed:
            problems.append(
                f"{path}: {now} site(s), baseline {allowed}. A new {shape} site: use the one "
                "envelope (raise a PyriteError); do not raise the baseline."
            )
        elif now < allowed:
            problems.append(
                f"{path}: {now} site(s), baseline {allowed}. Lower its line in "
                f"{BASELINE_FILE.name} to '{shape} | {now} | {path}'"
                + (" (delete it)" if now == 0 else "")
                + "; room left over would let a new site hide."
            )
    return problems


# --- scanners over source text ------------------------------------------------


def _is_str(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def http_exception_sites(source: str) -> int:
    """Shape 1: calls of ``HTTPException`` (bare or attribute)."""
    count = 0
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            count += name == "HTTPException"
    return count


def error_dict_sites(source: str) -> int:
    """Shape 2: a dict literal with an ``"error"`` key and no ``"error_code"`` key."""
    count = 0
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Dict):
            keys = {key.value for key in node.keys if _is_str(key)}
            count += "error" in keys and "error_code" not in keys
    return count


def code_literals(source: str) -> list[str]:
    """Shape 3's raw material: every upper-case code literal handed to a builder."""
    found: list[str] = []

    def take(node: ast.AST | None) -> None:
        if _is_str(node) and _CODE.match(node.value):  # type: ignore[attr-defined]
            found.append(node.value)  # type: ignore[attr-defined]

    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=True):
                if _is_str(key) and key.value == "error_code":  # type: ignore[union-attr]
                    take(value)
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            for keyword in node.keywords:
                if keyword.arg == "error_code":
                    take(keyword.value)
            if name in {"_error", "_cli_error"} and node.args:
                take(node.args[0])
            elif name == "build_error" and len(node.args) > 1:
                take(node.args[1])
    return found


def _code_table() -> set[str]:
    """The codes that exist: ``error_code`` on every ``PyriteError`` subclass.

    Read from the classes themselves, not from a scan of the source. T4-2
    replaces this with the table in ``pyrite/error_codes.py``.
    """
    from pyrite import exceptions

    seen: set[type] = set()
    stack: list[type] = [exceptions.PyriteError]
    while stack:
        cls = stack.pop()
        if cls not in seen:
            seen.add(cls)
            stack.extend(cls.__subclasses__())
    return {cls.error_code for cls in seen if isinstance(getattr(cls, "error_code", None), str)}


def unknown_code_sites(source: str, table: set[str]) -> int:
    return sum(1 for code in code_literals(source) if code not in table)


# --- scanning the tree --------------------------------------------------------


def _modules() -> list[tuple[str, Path]]:
    found = []
    for root in ROOTS:
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" not in path.parts:
                found.append((str(path.relative_to(REPO)), path))
    return found


def _scan_tree() -> dict[str, dict[str, int]]:
    table = _code_table()
    observed: dict[str, dict[str, int]] = {shape: {} for shape in SHAPES}
    for rel, path in _modules():
        source = path.read_text(encoding="utf-8")
        counts = {
            "http_exception": 0 if rel in HTTP_BUILDERS else http_exception_sites(source),
            "error_dict": error_dict_sites(source),
            "unknown_code": unknown_code_sites(source, table),
        }
        for shape, n in counts.items():
            if n:
                observed[shape][rel] = n
    return observed


def _ruff() -> list[str]:
    """The ruff in this interpreter's environment, else the one on PATH."""
    beside = Path(sys.executable).with_name("ruff")
    if beside.exists():
        return [str(beside)]
    found = shutil.which("ruff")
    return [found] if found else [sys.executable, "-m", "ruff"]


def broad_handlers(targets: list[str], cwd: Path) -> dict[tuple[str, str], list[bool]]:
    """(file, enclosing function) -> one flag per handler ruff flags, in source order.

    The flag is whether the handler contains a ``raise``. ``targets`` are paths
    relative to ``cwd``. Ruff finds the handlers; the AST only names the
    function each sits in.
    """
    run = subprocess.run(
        [
            *_ruff(),
            "check",
            "--select",
            "BLE001",
            "--ignore-noqa",
            "--isolated",
            "--no-cache",
            "--output-format",
            "json",
            *targets,
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert run.returncode in (0, 1), f"ruff failed ({run.returncode}): {run.stderr[:500]}"
    rows: dict[str, list[int]] = defaultdict(list)
    for diagnostic in json.loads(run.stdout or "[]"):
        assert diagnostic["code"] == "BLE001", diagnostic
        rel = str(Path(diagnostic["filename"]).resolve().relative_to(cwd.resolve()))
        rows[rel].append(diagnostic["location"]["row"])
    found: dict[tuple[str, str], list[bool]] = defaultdict(list)
    for rel, lines in sorted(rows.items()):
        tree = ast.parse((cwd / rel).read_text(encoding="utf-8"))
        handlers: dict[int, tuple[bool, str]] = {}

        def walk(node: ast.AST, stack: list[str], handlers=handlers) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                    walk(child, [*stack, child.name])
                else:
                    if isinstance(child, ast.ExceptHandler) and child.type is not None:
                        raises = any(
                            isinstance(n, ast.Raise) for b in child.body for n in ast.walk(b)
                        )
                        handlers[child.type.lineno] = (raises, ".".join(stack) or "<module>")
                    walk(child, stack)

        walk(tree, [])
        for line in sorted(lines):
            raises, function = handlers[line]
            found[(rel, function)].append(raises)
    return found


def _classification_problems(
    observed: dict[tuple[str, str], list[bool]],
    table: dict[tuple[str, str, str], tuple[int, str]],
) -> list[str]:
    """Every handler ruff finds is classified, to the count, and no row is stale."""
    problems = []
    rows: dict[tuple[str, str], dict[str, tuple[int, str]]] = defaultdict(dict)
    for (path, function, klass), value in table.items():
        rows[(path, function)][klass] = value
    for key in sorted(set(observed) | set(rows)):
        path, function = key
        flags = observed.get(key, [])
        classes = rows.get(key, {})
        have = sum(n for n, _ in classes.values())
        if len(flags) > have:
            problems.append(
                f"{path}: {function} has {len(flags)} broad handler(s), the table {have}. A new "
                "handler: catch what you mean. If it must stay, classify it in "
                f"{BASELINE_FILE.name} (class and a reason)."
            )
        elif len(flags) < have:
            problems.append(
                f"{path}: {function} has {len(flags)} broad handler(s), the table {have}. "
                f"Lower or delete its 'broad_except' row(s) in {BASELINE_FILE.name}."
            )
        else:
            raises = sum(flags)
            claimed = classes.get(RERAISE, (0, ""))[0]
            if raises != claimed:
                problems.append(
                    f"{path}: {function} has {raises} handler(s) that raise, the table "
                    f"{claimed} as {RERAISE}."
                )
        for klass, (_n, reason) in classes.items():
            if klass != SWALLOW and not reason:
                problems.append(f"{path}: {function} is {klass} and gives no reason.")
    return problems


# --- the four baselines, on the real tree -------------------------------------

_OBSERVED: dict[str, dict[str, int]] | None = None


def _observed() -> dict[str, dict[str, int]]:
    global _OBSERVED
    if _OBSERVED is None:
        _OBSERVED = _scan_tree()
    return _OBSERVED


@_control
def test_the_scan_sees_the_tree() -> None:
    """A scanner that silently walks nothing would pass every baseline."""
    assert len(_modules()) > 250
    observed = _observed()
    assert sum(observed["http_exception"].values()) > 100
    assert sum(observed["error_dict"].values()) > 50
    assert _code_table() >= {"ENTRY_NOT_FOUND", "KB_NOT_FOUND", "VALIDATION_FAILED"}


@pytest.mark.parametrize("shape", SHAPES)
@_control
def test_a_shape_is_at_or_below_its_baseline(shape: str) -> None:
    counts, _ = _read_baseline()
    problems = _compare(shape, _observed()[shape], counts[shape])
    assert not problems, "\n".join(problems)


@_control
def test_every_broad_handler_is_classified() -> None:
    _, table = _read_baseline()
    observed = broad_handlers(["pyrite", *[str(r.relative_to(REPO)) for r in ROOTS[1:]]], REPO)
    assert sum(len(v) for v in observed.values()) > 200, "ruff found too few handlers"
    problems = _classification_problems(observed, table)
    assert not problems, "\n".join(problems)


@_control
def test_a_swallow_row_never_claims_a_handler_that_raises() -> None:
    """The class names are checked, not only the counts: re-raise rows match the AST."""
    _, table = _read_baseline()
    observed = broad_handlers(["pyrite", *[str(r.relative_to(REPO)) for r in ROOTS[1:]]], REPO)
    wrong = [
        f"{path}: {function}"
        for (path, function), flags in sorted(observed.items())
        if sum(flags) != table.get((path, function, RERAISE), (0, ""))[0]
    ]
    assert not wrong, wrong


@_control
def test_the_baseline_file_holds_no_zero_or_negative_count() -> None:
    counts, table = _read_baseline()
    assert all(n > 0 for per in counts.values() for n in per.values())
    assert all(n > 0 for n, _ in table.values())


# --- planted violations and controls: the scanners see what they claim to ------


@_control
def test_planted_http_exception_is_seen() -> None:
    assert http_exception_sites("raise HTTPException(status_code=400, detail='x')") == 1
    assert http_exception_sites("raise fastapi.HTTPException(404, detail={'a': 1})") == 1
    assert (
        http_exception_sites("def f():\n  raise HTTPException(500)\n  raise HTTPException(500)")
        == 2
    )


@_control
def test_http_exception_controls_are_not_counted() -> None:
    assert http_exception_sites("try:\n  pass\nexcept HTTPException:\n  pass") == 0
    assert http_exception_sites("raise PyriteError('x')") == 0
    assert http_exception_sites("from fastapi import HTTPException") == 0


@_control
def test_planted_error_dict_is_seen() -> None:
    assert error_dict_sites("def f():\n  return {'error': 'nope'}") == 1
    assert error_dict_sites("x = {'error': 'nope', 'count': 0, 'results': []}") == 1
    assert error_dict_sites("x = {'error': e, **rest}") == 1
    assert error_dict_sites("x = [{'error': 'a'}, {'error': 'b'}]") == 2


@_control
def test_error_dict_controls_are_not_counted() -> None:
    assert (
        error_dict_sites("x = {'error': 'm', 'error_code': 'KB_NOT_FOUND', 'retryable': False}")
        == 0
    )
    assert error_dict_sites("x = {'errors': []}") == 0
    assert error_dict_sites("x = {'error_code': 'X'}") == 0
    assert error_dict_sites("x = {}") == 0


@pytest.mark.parametrize(
    "source",
    [
        "_error('MADE_UP_CODE', 'm')",
        "self._error('MADE_UP_CODE', 'm')",
        "build_error('m', 'MADE_UP_CODE')",
        "cli_error('m', error_code='MADE_UP_CODE')",
        "x = {'error': 'm', 'error_code': 'MADE_UP_CODE'}",
    ],
)
@_control
def test_planted_unknown_code_is_seen(source: str) -> None:
    table = {"ENTRY_NOT_FOUND"}
    assert code_literals(source) == ["MADE_UP_CODE"]
    assert unknown_code_sites(source, table) == 1


@pytest.mark.parametrize(
    "source",
    [
        "_error('ENTRY_NOT_FOUND', 'm')",
        "build_error('m', 'ENTRY_NOT_FOUND')",
        "x = {'error': 'm', 'error_code': 'ENTRY_NOT_FOUND'}",
        "_error(code, 'm')",
        "cli_error('m', error_code=exc.error_code)",
        "other('MADE_UP_CODE')",
        "x = {'status': 'MADE_UP_CODE'}",
        "_error('lower_case', 'm')",
    ],
)
@_control
def test_unknown_code_controls_are_not_counted(source: str) -> None:
    assert unknown_code_sites(source, {"ENTRY_NOT_FOUND"}) == 0


@_control
def test_the_code_table_is_the_classes_not_the_source() -> None:
    from pyrite.exceptions import EntryNotFoundError

    assert EntryNotFoundError.error_code in _code_table()
    assert "MADE_UP_CODE" not in _code_table()


def _plant(tmp_path: Path, source: str) -> dict[tuple[str, str], list[bool]]:
    (tmp_path / "pkg").mkdir(exist_ok=True)
    (tmp_path / "pkg" / "mod.py").write_text(source, encoding="utf-8")
    return broad_handlers(["pkg"], tmp_path)


@_control
def test_planted_broad_handlers_are_seen(tmp_path: Path) -> None:
    found = _plant(
        tmp_path,
        "def swallow():\n"
        "    try:\n        pass\n    except Exception:\n        return None\n"
        "def marked():\n"
        "    try:\n        pass\n    except Exception:  # noqa: BLE001\n        pass\n"
        "class C:\n"
        "    def base(self):\n"
        "        try:\n            pass\n        except BaseException:\n            pass\n"
        "def converts():\n"
        "    try:\n        pass\n    except Exception as e:\n        raise ValueError('x')\n",
    )
    assert found == {
        ("pkg/mod.py", "swallow"): [False],
        ("pkg/mod.py", "marked"): [False],  # a noqa marker does not hide it
        ("pkg/mod.py", "C.base"): [False],
        ("pkg/mod.py", "converts"): [True],
    }


@_control
def test_broad_handler_controls_are_not_counted(tmp_path: Path) -> None:
    found = _plant(
        tmp_path,
        "def narrow():\n"
        "    try:\n        pass\n    except (OSError, ValueError):\n        return None\n"
        "def reraise():\n"
        "    try:\n        pass\n    except Exception:\n        raise\n"
        "def reraise_from():\n"
        "    try:\n        pass\n    except Exception as e:\n        raise RuntimeError('x') from e\n",
    )
    assert found == {}


@_control
def test_a_new_handler_and_a_stale_row_both_fail() -> None:
    table = {("pkg/mod.py", "f", SWALLOW): (1, "")}
    assert _classification_problems({("pkg/mod.py", "f"): [False]}, table) == []
    grew = _classification_problems({("pkg/mod.py", "f"): [False, False]}, table)
    assert len(grew) == 1 and "new handler" in grew[0]
    new_function = _classification_problems(
        {("pkg/mod.py", "f"): [False], ("pkg/mod.py", "g"): [False]}, table
    )
    assert len(new_function) == 1 and "g has 1" in new_function[0]
    stale = _classification_problems({}, table)
    assert len(stale) == 1 and "Lower or delete" in stale[0]


@_control
def test_a_reraise_row_must_match_the_ast_and_give_a_reason() -> None:
    key = ("pkg/mod.py", "f")
    assert _classification_problems({key: [True]}, {(*key, RERAISE): (1, "re-raises as X")}) == []
    wrong = _classification_problems({key: [False]}, {(*key, RERAISE): (1, "re-raises as X")})
    assert len(wrong) == 1 and "that raise" in wrong[0]
    assert _classification_problems({key: [False]}, {(*key, SWALLOW): (1, "")}) == []
    unexplained = _classification_problems({key: [False]}, {(*key, BACKSTOP): (1, "")})
    assert len(unexplained) == 1 and "no reason" in unexplained[0]


@_control
def test_compare_flags_growth_and_stale_room_and_passes_equal() -> None:
    assert _compare("error_dict", {"a.py": 2}, {"a.py": 2}) == []
    assert _compare("error_dict", {}, {}) == []
    grew = _compare("error_dict", {"a.py": 3}, {"a.py": 2})
    assert len(grew) == 1 and "new error_dict site" in grew[0]
    unlisted = _compare("error_dict", {"new.py": 1}, {})
    assert len(unlisted) == 1 and "new.py" in unlisted[0]
    shrank = _compare("error_dict", {"a.py": 1}, {"a.py": 2})
    assert len(shrank) == 1 and "error_dict | 1 | a.py" in shrank[0]
    gone = _compare("error_dict", {}, {"a.py": 2})
    assert len(gone) == 1 and "(delete it)" in gone[0]


@_control
def test_every_baseline_path_exists() -> None:
    counts, table = _read_baseline()
    paths = {p for per in counts.values() for p in per} | {p for p, _f, _c in table}
    missing = sorted(p for p in paths if not (REPO / p).is_file())
    assert not missing, missing
