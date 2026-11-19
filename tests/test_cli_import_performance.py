"""Fresh-process CLI startup boundaries, with real installed extensions (#1046)."""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
_EXTENSIONS = (
    "pyrite_encyclopedia",
    "pyrite_journalism_investigation",
    "pyrite_social",
    "pyrite_software_kb",
    "pyrite_zettelkasten",
)


def _probe(code, *args):
    result = subprocess.run(
        [sys.executable, "-c", code, *args],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result


@pytest.mark.parametrize(
    ("module", "args"),
    [
        ("pyrite.cli", None),
        ("pyrite.cli", ["--help"]),
        ("pyrite.cli", ["kb", "--help"]),
        ("pyrite.cli", ["sw", "--help"]),
        ("pyrite.read_cli", ["--help"]),
        ("pyrite.admin_cli", ["--help"]),
    ],
)
def test_cli_entry_points_stay_light(module, args):
    import json

    for extension in _EXTENSIONS:
        assert importlib.util.find_spec(extension) is not None, f"Install {extension} for this test"
    _probe(
        """
import importlib, json, sys
module = importlib.import_module(sys.argv[1])
args = json.loads(sys.argv[2])
if args is not None:
    sys.argv = ['pyrite', *args]
    try:
        module.main()
    except SystemExit as exc:
        assert exc.code in (None, 0), exc.code
for name in ('sqlalchemy', 'httpx'):
    assert name not in sys.modules, f'{name} loaded for {args}'
""",
        module,
        json.dumps(args),
    )


def test_cli_context_exposes_real_pyrite_db_class_via_getattr():
    import pyrite.cli.context as cli_context

    real_db_class = cli_context.PyriteDB

    class TrackingDB(real_db_class):
        pass

    assert issubclass(TrackingDB, real_db_class)


def test_storage_error_helpers_do_not_import_database():
    _probe("import sys; import pyrite.storage.errors; assert 'sqlalchemy' not in sys.modules")


@pytest.fixture
def startup_world(tmp_path):
    from pyrite.config import KBConfig
    from tests._world import build_world

    world = build_world(
        tmp_path / "world", kbs=[KBConfig(name="demo", path=tmp_path / "kb")], rest=False
    )
    try:
        world.add_entry(
            "demo",
            "---\nid: startup-entry\ntitle: StartupNeedle\ntype: note\n---\nStartupNeedle body.\n",
        )
        with world.pointed():
            yield world
    finally:
        world.close()


def test_search_opens_real_index_and_returns_results(startup_world):
    result = _probe("""
import sys
from pyrite.cli import main
sys.argv = ['pyrite', 'search', 'StartupNeedle', '-k', 'demo', '--format', 'json']
try:
    main()
except SystemExit as exc:
    assert exc.code in (None, 0), exc.code
assert 'sqlalchemy' in sys.modules
""")
    assert "startup-entry" in result.stdout
    assert "StartupNeedle" in result.stdout


@pytest.mark.parametrize(
    "helper",
    [
        "open_index_db",
        "index_db_context",
        "get_config_and_db",
        "open_index_for_validation",
        "get_config_with_registered_kbs",
        "cli_context",
        "cli_registry_context",
    ],
)
def test_context_helpers_work_from_a_cold_process(helper, startup_world):
    _probe(
        """
import sys
from pyrite.cli import context
from pyrite.config import load_config
config = load_config()
name = sys.argv[1]
if name in ('cli_context', 'cli_registry_context'):
    with getattr(context, name)(kbs=('demo',)) as values:
        assert values[1].get_entry('startup-entry', 'demo')
elif name == 'index_db_context':
    with context.index_db_context(config) as db:
        assert db.get_entry('startup-entry', 'demo')
elif name == 'get_config_with_registered_kbs':
    assert context.get_config_with_registered_kbs(config, name='registry-probe').get_kb('demo')
else:
    result = getattr(context, name)(config)
    db = result[1] if isinstance(result, tuple) else result
    try:
        assert db.get_entry('startup-entry', 'demo')
    finally:
        db.close()
""",
        helper,
    )
