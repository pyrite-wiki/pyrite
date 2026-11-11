"""Every YAML load of KB or user content goes through ``pyrite.utils.yaml``.

The alias bounds (``test_yaml_alias_bound.py``) hold only where the loader
applies them. This file pins the loaders that read content other than the
entry loader: templates (read-tier ``GET``, daily creation, render) and the bulk
link-spec reader; and fails when a new
module loads YAML directly: any PyYAML loader (``safe_load``, ``full_load``,
``unsafe_load``, ``compose`` and the rest, called on the module or imported by
name), or a ruamel ``YAML`` object's ``load``/``compose`` outside the few
allowed probes and bounded composes in ``storage/file_operations.py``.
"""

from __future__ import annotations

import ast
import time
from pathlib import Path

import pytest

from pyrite.exceptions import FrontmatterError, ValidationError

ROOT = Path(__file__).resolve().parent.parent


def _bomb_yaml(levels: int = 7, fan: int = 9) -> str:
    lines = ["l0: &l0 [" + ", ".join(["x"] * fan) + "]"]
    for i in range(1, levels):
        lines.append(f"l{i}: &l{i} [" + ", ".join([f"*l{i - 1}"] * fan) + "]")
    return "\n".join(lines)


#: PyYAML's functions that build a document from text.
_PYYAML_LOADERS = {
    "safe_load",
    "safe_load_all",
    "load",
    "load_all",
    "full_load",
    "full_load_all",
    "unsafe_load",
    "unsafe_load_all",
    "compose",
    "compose_all",
}

#: A ruamel ``YAML`` object: what these calls return.
_RUAMEL_MAKERS = {"YAML", "_yaml", "_get_yaml"}
_RUAMEL_LOADS = {"load", "load_all", "compose", "compose_all"}

#: ruamel loads outside ``pyrite/utils/yaml.py`` that are allowed, each with
#: why. "bounded": the function runs ``check_yaml_limits`` on the text first
#: (checked below). "probe": a one-key document the module builds around a
#: single-line value, possibly aliased, to see how YAML reads it; what it
#: loads is never walked (only a scalar is compared or converted:
#: ``tests/test_yaml_probes_read_one_scalar.py``).
_RUAMEL_ALLOWED = {
    ("pyrite/storage/file_operations.py", "unmodellable_reason"): "bounded",
    ("pyrite/storage/file_operations.py", "_parse"): "bounded",
    ("pyrite/storage/file_operations.py", "_plain_ok"): "probe",
    ("pyrite/storage/file_operations.py", "_scalar_of"): "probe",
    ("pyrite/storage/file_operations.py", "_emit_key"): "probe",
    ("pyrite/storage/file_operations.py", "_temporal_equals_text"): "probe",
}


def _direct_loads(source: str) -> list[tuple[int, str, str]]:
    """``(line, enclosing function, form)`` for every YAML load in ``source``
    that does not go through ``pyrite.utils.yaml``: a PyYAML loader called on
    the module (under any alias) or imported by name, and a ``load`` or
    ``compose`` on a ruamel ``YAML`` object (made in the call, or held in a
    name assigned from one)."""
    tree = ast.parse(source)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}

    def enclosing(node: ast.AST) -> ast.AST:
        while node in parents:
            node = parents[node]
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                return node
        return tree

    def maker(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name | ast.Attribute)
            and (node.func.id if isinstance(node.func, ast.Name) else node.func.attr)
            in _RUAMEL_MAKERS
        )

    yaml_names = {"yaml"}
    ruamel_held: dict[ast.AST, set[str]] = {}
    found: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yaml_names |= {a.asname or a.name for a in node.names if a.name == "yaml"}
        elif isinstance(node, ast.ImportFrom) and node.module == "yaml":
            for a in node.names:
                if a.name in _PYYAML_LOADERS:
                    found.append((node.lineno, getattr(enclosing(node), "name", ""), a.name))
        elif isinstance(node, ast.Assign) and maker(node.value):
            scope = ruamel_held.setdefault(enclosing(node), set())
            scope |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        recv, attr = node.func.value, node.func.attr
        scope = enclosing(node)
        name = getattr(scope, "name", "")
        if attr in _PYYAML_LOADERS and isinstance(recv, ast.Name) and recv.id in yaml_names:
            found.append((node.lineno, name, f"yaml.{attr}"))
        elif attr in _RUAMEL_LOADS and (
            maker(recv) or (isinstance(recv, ast.Name) and recv.id in ruamel_held.get(scope, set()))
        ):
            found.append((node.lineno, name, f"ruamel {attr}"))
    return found


def _scanned() -> list[Path]:
    roots = [ROOT / "pyrite", *sorted((ROOT / "extensions").glob("*/src"))]
    return [
        p
        for root in roots
        for p in sorted(root.rglob("*.py"))
        if p != ROOT / "pyrite" / "utils" / "yaml.py"
    ]


@pytest.mark.control(reason="no module loads YAML directly today; this holds a new one to it")
def test_no_module_loads_yaml_content_directly():
    """A YAML load outside ``pyrite/utils/yaml.py`` is a loader the bounds do
    not reach, unless it is one of the allowed ruamel probes or bounded
    composes."""
    found = []
    for path in _scanned():
        rel = path.relative_to(ROOT).as_posix()
        for line, func, form in _direct_loads(path.read_text(encoding="utf-8")):
            if form.startswith("ruamel") and (rel, func) in _RUAMEL_ALLOWED:
                continue
            found.append(f"{rel}:{line} {func}: {form}")
    assert found == []


@pytest.mark.control(reason="the allowed composes are bounded today; this keeps them so")
def test_an_allowed_bounded_compose_checks_the_bound_first():
    for (rel, func), why in _RUAMEL_ALLOWED.items():
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        [fn] = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == func]
        calls = {
            n.func.id
            for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        if why == "bounded":
            assert "check_yaml_limits" in calls, f"{rel} {func}"


@pytest.mark.parametrize(
    "source",
    [
        "import yaml\nyaml.safe_load(t)\n",
        "import yaml\nyaml.full_load(t)\n",
        "import yaml\nyaml.unsafe_load(t)\n",
        "import yaml as y\ny.load(t)\n",
        "from yaml import safe_load\n",
        "from yaml import full_load as fl\n",
        "from ruamel.yaml import YAML\nYAML().load(t)\n",
        "from ruamel.yaml import YAML\ndef f(t):\n    y = YAML()\n    return y.compose(t)\n",
        "def f(t):\n    y = _yaml()\n    return y.load(t)\n",
    ],
)
def test_the_scan_finds_each_form_of_a_direct_load(source):
    assert _direct_loads(source), source


@pytest.mark.parametrize(
    "source",
    [
        "from pyrite.utils.yaml import safe_load_bounded\nsafe_load_bounded(t)\n",
        "import json\njson.load(f)\n",
        "repo.load(entry_id)\n",
        "from yaml import YAMLError\n",
    ],
)
def test_the_scan_leaves_other_loads_alone(source):
    assert _direct_loads(source) == []


def _template_kb(tmp_path: Path) -> Path:
    kb = tmp_path / "kb"
    (kb / "_templates").mkdir(parents=True)
    (kb / "_templates" / "bomb.md").write_text(
        f"---\n{_bomb_yaml()}\nentry_type: note\n---\nbody\n", encoding="utf-8"
    )
    return kb


@pytest.mark.timeout(60)
def test_a_template_with_an_alias_bomb_is_refused_on_read(tmp_path: Path):
    from pyrite.config import KBConfig
    from tests._world import build_world

    kb = _template_kb(tmp_path)
    w = build_world(tmp_path / "w", kbs=[KBConfig(name="k", path=kb, kb_type="generic")])
    try:
        started = time.monotonic()
        resp = w.client.get("/api/kbs/k/templates/bomb")
        assert time.monotonic() - started < 5
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"]["code"] == "INVALID_FRONTMATTER"
    finally:
        w.close()


@pytest.mark.timeout(60)
def test_the_template_service_refuses_it_for_render_and_daily_creation(tmp_path: Path):
    from pyrite.config import KBConfig, PyriteConfig
    from pyrite.services.template_service import TemplateService

    kb = _template_kb(tmp_path)
    cfg = PyriteConfig(knowledge_bases=[KBConfig(name="k", path=kb, kb_type="generic")])
    svc = TemplateService(cfg)
    with pytest.raises(FrontmatterError):
        svc.get_template("k", "bomb")


def test_bulk_link_specs_with_a_bomb_are_refused():
    """A list of link specs whose later items alias earlier ones."""
    from pyrite.cli.link_commands import _parse_link_specs

    levels = ["- &l0 [" + ", ".join(["x"] * 9) + "]"]
    for i in range(1, 8):
        levels.append(f"- &l{i} [" + ", ".join([f"*l{i - 1}"] * 9) + "]")
    started = time.monotonic()
    with pytest.raises(FrontmatterError):
        _parse_link_specs("\n".join(levels))
    assert time.monotonic() - started < 5
