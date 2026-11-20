"""`pip install pyrite[postgres]` is enough for `ensure_schema` to run (#718).

`ensure_schema` runs the alembic chain on every Postgres startup. Every
third-party module that path imports must therefore come with the core
dependencies or the `postgres` extra. Until #718's round 2, alembic came only
with `server` (fixed in #718's round 2), and CI could not tell: every CI job installs `.[all,...]`.

The modules come from the source itself (an AST walk over the files that
path loads), not from a hand list. The distributions come from the installed
metadata (`importlib.metadata.packages_distributions`). So a new import in a
revision, without its extra, fails here.
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from importlib.metadata import packages_distributions
from pathlib import Path


ROOT = Path(__file__).parents[1]
STORAGE = ROOT / "pyrite" / "storage"

# What ensure_schema loads: the backend module, the chain helpers, alembic's
# env.py and every revision.
_FILES = [
    STORAGE / "backends" / "postgres_backend.py",
    STORAGE / "alembic_chain.py",
    STORAGE / "alembic" / "env.py",
    *sorted((STORAGE / "alembic" / "versions").glob("*.py")),
]


def _top_level_imports(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def _requirement_names(requirements: list[str]) -> set[str]:
    return {
        re.split(r"[\s\[<>=!~;]", req, maxsplit=1)[0].lower().replace("_", "-")
        for req in requirements
    }


def test_every_module_ensure_schema_imports_comes_with_the_postgres_extra():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    provided = _requirement_names(project["dependencies"]) | _requirement_names(
        project["optional-dependencies"]["postgres"]
    )

    third_party = set()
    for path in _FILES:
        for name in _top_level_imports(path):
            if name in sys.stdlib_module_names or name in ("pyrite", "__future__"):
                continue
            third_party.add(name)

    dists = packages_distributions()
    missing = {}
    for module in sorted(third_party):
        names = {d.lower().replace("_", "-") for d in dists.get(module, [module])}
        if not names & provided:
            missing[module] = sorted(names)
    assert not missing, (
        f"ensure_schema imports {missing}, which neither the core dependencies nor the "
        "`postgres` extra in pyproject.toml provide: `pip install pyrite[postgres]` "
        "would fail at startup"
    )
