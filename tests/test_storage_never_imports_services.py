"""``pyrite/storage/`` never imports ``pyrite/services/`` (#901).

Services depend on storage, not the reverse. ``test_layer_boundaries`` walks
the surfaces only and exempts both inner layers, so #899's round 1 added the
first storage -> services import and nothing failed. This is the same AST
walk (``_absolute`` resolves relative imports) pointed at the inner layer.

Every import statement counts, wherever it sits: module level, inside a
function body, under ``if TYPE_CHECKING``. ``from pyrite import services``
counts too. ``ALLOWLIST`` starts empty and can only shrink: an entry is
``"file:line"`` -> the ticket that removes the import, and a stale entry fails.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests._surface_inventory import REPO_ROOT
from tests.test_layer_boundaries import _absolute, _within

STORAGE = REPO_ROOT / "pyrite" / "storage"
SERVICES = "pyrite.services"

# "pyrite/storage/x.py:LINE" -> the ticket that removes the import. Never grow.
ALLOWLIST: dict[str, str] = {}
ALLOWLIST_SIZE = 0

_SELF_TEST = pytest.mark.control(
    reason="tests the rule's own scanner and list bookkeeping, added with it: "
    "true on either side of the change"
)


def services_imports(source: str, rel: str) -> list[str]:
    """``rel:line`` for every import in ``source`` that resolves into pyrite.services."""
    hits: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            bad = any(_within(a.name, SERVICES) for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = _absolute(rel, node)
            bad = _within(module, SERVICES) or any(
                _within(f"{module}.{a.name}", SERVICES) for a in node.names
            )
        else:
            continue
        if bad:
            hits.append(f"{rel}:{node.lineno}")
    return sorted(hits)


def scan_storage() -> list[str]:
    hits: list[str] = []
    for path in sorted(STORAGE.rglob("*.py")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        hits += services_imports(path.read_text(), rel)
    return hits


def test_storage_never_imports_services():
    new = [h for h in scan_storage() if h not in ALLOWLIST]
    assert not new, (
        "pyrite/storage imports pyrite/services (services depend on storage, never the "
        "reverse); move the code down into storage or invert the dependency:\n  " + "\n  ".join(new)
    )


def test_allowlist_entries_are_live():
    found = set(scan_storage())
    stale = sorted(k for k in ALLOWLIST if k not in found)
    assert not stale, "delete these entries (and lower ALLOWLIST_SIZE):\n  " + "\n  ".join(stale)


@_SELF_TEST
def test_the_allowlist_only_shrinks():
    assert len(ALLOWLIST) == ALLOWLIST_SIZE
    assert ALLOWLIST_SIZE <= 0, "ALLOWLIST_SIZE starts at 0 and only goes down"
    assert all(v.strip() for v in ALLOWLIST.values())


@_SELF_TEST
def test_the_walk_sees_storage():
    assert len(list(STORAGE.rglob("*.py"))) > 5


_REL = "pyrite/storage/sub/mod.py"


@_SELF_TEST
@pytest.mark.parametrize(
    "source",
    [
        "from pyrite.services.kb_service import KBService\n",
        "import pyrite.services.kb_service\n",
        "import pyrite.services\n",
        "from pyrite import services\n",
        "from pyrite.services import KBService as K\n",
        "def f():\n    from pyrite.services.kb_service import KBService\n",
        "def f():\n    import pyrite.services.kb_service as s\n",
        "class C:\n    def m(self):\n        from pyrite.services import x\n",
        "from ...services.kb_service import KBService\n",  # pyrite.services via sub/
        "def f():\n    from ...services import x\n",
        "from ... import services\n",
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from pyrite.services import x\n",
    ],
)
def test_a_planted_import_fails_the_walk(source):
    assert services_imports(source, _REL)


@_SELF_TEST
@pytest.mark.parametrize(
    "source",
    [
        "from pyrite.storage.index import IndexManager\n",
        "from ..index import IndexManager\n",
        "from pyrite.services_like import x\n",  # starts like services, is not
        "from pyrite import storage\n",
        "x = 'from pyrite.services import y'\n",
    ],
)
def test_what_the_walk_does_not_flag(source):
    assert services_imports(source, _REL) == []


@_SELF_TEST
def test_the_report_names_file_and_line():
    assert services_imports("x = 1\n\ndef f():\n    from pyrite.services import y\n", _REL) == [
        f"{_REL}:4"
    ]


@_SELF_TEST
def test_a_planted_import_in_a_storage_file_fails_the_rule(tmp_path, monkeypatch):
    rel = "pyrite/storage/planted.py"
    monkeypatch.setattr(
        f"{__name__}.scan_storage",
        lambda: services_imports("def f():\n    from pyrite.services import x\n", rel),
    )
    with pytest.raises(AssertionError, match=r"planted\.py:2"):
        test_storage_never_imports_services()
