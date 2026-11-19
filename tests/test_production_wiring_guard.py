"""Tests run the production wiring, and the exceptions only shrink (#1077).

Two seams let a test skip the code it claims to cover:

* ``get_db`` overridden in a FastAPI app's ``dependency_overrides`` skips the
  production dependency: the per-request ``request_handle()`` session (#131)
  and the ``merge_registered_kbs`` the app's database runs. Every such
  harness also leaked a pooled connection per request (see
  ``tests/_world.py``).
* ``load_config`` patched skips config parsing, the untrusted-config rules,
  ``PYRITE_DATA_DIR`` precedence and KBs registered in the database; four
  config bugs escaped on that seam (#448/#363, #924, #511, #1061).

A new test uses the world (``make_client``, ``rest_api_env``,
``_make_mcp_server``, ``cli_world``; see "Which fixture?" in
``.claude/skills/pyrite-dev/testing.md``) and needs neither.

This test walks the AST of every ``.py`` under ``tests/`` and
``extensions/*/tests`` and counts, per file:

* ``get_db`` -- ``get_db`` (a bare name or ``x.get_db``, or a name bound to
  it by ``from X import get_db as Y``) used as a key: in a subscript
  assignment (``app.dependency_overrides[get_db] = ...``, or a local
  overrides dict), a dict literal (``dependency_overrides={get_db:
  ...}``), or the key slot of ``monkeypatch.setitem(overrides, get_db,
  ...)`` (only the key matters, as for the subscript form);
* ``load_config`` -- a ``patch(...)`` / ``monkeypatch.setattr(...)`` whose
  target string ends in ``load_config``, or a ``patch.object`` /
  ``setattr`` / ``patch.multiple`` naming the attribute ``load_config``.

``tests/production_wiring_baseline.txt`` records the count per file as of
#1077. It can only shrink: a file above its line fails, a file not listed
fails, and a file below its line fails too, so the line is lowered (or
removed) in the same pull request. The baseline file itself is compared with
its copy at the merge base with the base branch (``origin/dev``, or
``origin/$GITHUB_BASE_REF``): a line added or raised there fails, so raising a
count and its line together does not pass.
"""

from __future__ import annotations

import ast
import os
import subprocess
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "tests" / "production_wiring_baseline.txt"
KINDS = ("get_db", "load_config")


def _terminal(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _str(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _names_load_config(target: str | None) -> bool:
    return target is not None and (target == "load_config" or target.endswith(".load_config"))


def _get_db_aliases(tree: ast.AST) -> set[str]:
    """Names bound to ``get_db`` by ``from X import get_db as Y``, at any scope.

    Only the binding matters, not the module: the subscript form already
    counts ``get_db`` under any parent name.
    """
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "get_db" and alias.asname:
                    aliases.add(alias.asname)
    return aliases


def count_sites(source: str) -> Counter:
    """The two kinds of site in one file's source."""
    tree = ast.parse(source)
    get_db_names = {"get_db"} | _get_db_aliases(tree)
    counts: Counter = Counter()
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store):
            if _terminal(node.slice) in get_db_names:
                counts["get_db"] += 1
        elif isinstance(node, ast.Dict):
            counts["get_db"] += sum(
                1 for k in node.keys if k is not None and _terminal(k) in get_db_names
            )
        elif isinstance(node, ast.Call):
            if (
                _terminal(node.func) == "setitem"
                and len(node.args) >= 2
                and _terminal(node.args[1]) in get_db_names
            ):
                counts["get_db"] += 1
            func = _terminal(node.func)
            args = node.args
            if (
                func in ("patch", "setattr", "setitem")
                and args
                and _names_load_config(_str(args[0]))
            ):
                counts["load_config"] += 1
            elif (
                func in ("object", "setattr") and len(args) >= 2 and _str(args[1]) == "load_config"
            ):
                counts["load_config"] += 1
            elif func == "multiple" and any(k.arg == "load_config" for k in node.keywords):
                counts["load_config"] += 1
    return counts


def _test_files() -> list[Path]:
    files = sorted((ROOT / "tests").rglob("*.py"))
    files += sorted(ROOT.glob("extensions/*/tests/**/*.py"))
    return [f for f in files if "__pycache__" not in f.parts]


def current_counts() -> dict[tuple[str, str], int]:
    out: dict[tuple[str, str], int] = {}
    for path in _test_files():
        counts = count_sites(path.read_text(encoding="utf-8"))
        rel = path.relative_to(ROOT).as_posix()
        for kind in KINDS:
            if counts[kind]:
                out[(kind, rel)] = counts[kind]
    return out


def parse_baseline(text: str) -> dict[tuple[str, str], int]:
    out: dict[tuple[str, str], int] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        kind, count, rel = line.split(maxsplit=2)
        assert kind in KINDS, f"unknown kind {kind!r} in {BASELINE.name}: {line}"
        out[(kind, rel)] = int(count)
    return out


def read_baseline() -> dict[tuple[str, str], int]:
    return parse_baseline(BASELINE.read_text(encoding="utf-8"))


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=30, check=False
    )


def base_baseline() -> dict[tuple[str, str], int] | None:
    """The baseline at the merge base with the base branch; None when this
    branch introduced the file. Skips when the base branch is not fetched."""
    base_ref = f"origin/{os.environ.get('GITHUB_BASE_REF') or 'dev'}"
    merge_base = _git("merge-base", "HEAD", base_ref)
    if merge_base.returncode != 0:
        pytest.skip(f"{base_ref} is not fetched here; CI's test job fetches it (fetch-depth: 0)")
    rel = BASELINE.relative_to(ROOT).as_posix()
    shown = _git("show", f"{merge_base.stdout.strip()}:{rel}")
    if shown.returncode != 0:
        return None
    return parse_baseline(shown.stdout)


def raised_lines(base: dict[tuple[str, str], int], now: dict[tuple[str, str], int]) -> list[str]:
    """Lines of ``now`` that are new or higher than in ``base``."""
    return [
        f"{kind} {n} {rel}  (merge base: {base.get((kind, rel), 0)})"
        for (kind, rel), n in sorted(now.items())
        if n > base.get((kind, rel), 0)
    ]


# -- the counter, on sources whose counts are known by hand -----------------


@pytest.mark.parametrize(
    ("source", "get_db", "load_config"),
    [
        ("app.dependency_overrides[get_db] = lambda: db", 1, 0),
        ("app.dependency_overrides[api.get_db] = lambda: db", 1, 0),
        ("make_client(dependency_overrides={get_config: f, get_db: g})", 1, 0),
        ("overrides = {}\noverrides[get_db] = g", 1, 0),
        ("x = app.dependency_overrides[get_db]", 0, 0),  # a read, not an override
        ("app.dependency_overrides[get_config] = f", 0, 0),
        # -- get_db under an alias, and the setitem spelling (#1235) --------
        (
            "from pyrite.server.api import get_db as real\napp.dependency_overrides[real] = f",
            1,
            0,
        ),
        (
            "def test_x():\n    from pyrite.server.api import get_db as real\n"
            "    app.dependency_overrides[real] = f",
            1,
            0,
        ),
        ("monkeypatch.setitem(app.dependency_overrides, get_db, f)", 1, 0),
        ("monkeypatch.setitem(app.dependency_overrides, api.get_db, f)", 1, 0),
        ("mp.setitem(app.dependency_overrides, get_db, f)", 1, 0),
        ("pytest.MonkeyPatch().setitem(app.dependency_overrides, get_db, f)", 1, 0),
        ("monkeypatch.setitem(overrides, get_db, f)", 1, 0),  # only the key matters
        ("x = real", 0, 0),  # a name that is not a get_db alias
        ("from m import get_db as real", 0, 0),  # an import is not an override
        ("monkeypatch.setitem(d, other, f)", 0, 0),
        ("monkeypatch.setitem(app.dependency_overrides, get_config, f)", 0, 0),
        ("del app.dependency_overrides[get_db]", 0, 0),
        ('with patch("pyrite.cli.context.load_config", return_value=c): pass', 0, 1),
        ('@patch("pyrite.cli.load_config")\ndef f(): pass', 0, 1),
        ('monkeypatch.setattr(cli, "load_config", lambda: cfg)', 0, 1),
        ('monkeypatch.setattr("pyrite.config.load_config", lambda: cfg)', 0, 1),
        ('patch.object(mod, "load_config", return_value=c)', 0, 1),
        ('mocker.patch.object(mod, "load_config")', 0, 1),
        ("patch.multiple(mod, load_config=f)", 0, 1),
        ('patch("pyrite.config.load_config_file")', 0, 0),
        ("cfg = load_config()", 0, 0),
        ('monkeypatch.setattr(config_module, "CONFIG_DIR", d)', 0, 0),
    ],
)
def test_count_sites(source, get_db, load_config):
    counts = count_sites(source)
    assert (counts["get_db"], counts["load_config"]) == (get_db, load_config)


# -- the rule -----------------------------------------------------------------


@pytest.mark.guard
def test_no_file_adds_a_site():
    baseline = read_baseline()
    grown = [
        f"{rel}: {n} {kind} site(s), baseline {baseline.get((kind, rel), 0)}"
        for (kind, rel), n in sorted(current_counts().items())
        if n > baseline.get((kind, rel), 0)
    ]
    assert not grown, (
        "These files override get_db or patch load_config more than the baseline "
        "allows. Use the world instead (make_client, rest_api_env, cli_world; "
        "tests/_world.py):\n  " + "\n  ".join(grown)
    )


@pytest.mark.guard
def test_a_file_that_shrank_lowers_its_line():
    current = current_counts()
    shrunk = [
        f"{kind} {current.get((kind, rel), 0)} {rel}  (baseline says {n})"
        for (kind, rel), n in sorted(read_baseline().items())
        if current.get((kind, rel), 0) < n
    ]
    assert not shrunk, (
        f"Lower these lines in {BASELINE.relative_to(ROOT)} (remove a line at 0):\n  "
        + "\n  ".join(shrunk)
    )


def test_raised_lines_on_planted_baselines():
    base = {("get_db", "a.py"): 2, ("load_config", "b.py"): 1}
    assert raised_lines(base, base) == []
    assert raised_lines(base, {("get_db", "a.py"): 1}) == []  # lowered, and b.py removed
    assert raised_lines(base, {("get_db", "a.py"): 3}) == ["get_db 3 a.py  (merge base: 2)"]
    assert raised_lines(base, {**base, ("get_db", "c.py"): 1}) == ["get_db 1 c.py  (merge base: 0)"]


@pytest.mark.guard
def test_the_baseline_only_comes_down_from_the_merge_base():
    base = base_baseline()
    if base is None:
        pytest.skip("the baseline is new on this branch (#1077); nothing to compare yet")
    raised = raised_lines(base, read_baseline())
    assert not raised, (
        f"{BASELINE.relative_to(ROOT)} may only lose or lower lines against the "
        "merge base. Move the file onto the world (tests/_world.py) instead:\n  "
        + "\n  ".join(raised)
    )
