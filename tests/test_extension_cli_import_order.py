"""An extension CLI module imported before ``pyrite.cli`` must not vanish (#900).

Importing ``pyrite_software_kb.cli`` first makes ``pyrite.cli``'s plugin loop call
``get_cli_commands`` on a half-initialised module; that raises. Loading order is
not changed here, so the property is: the group is present, whatever was imported
first (#1000, #1109). Subprocesses control import order.
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

PROBE = """
import pyrite_software_kb.cli
from pyrite.cli import app
print("GROUPS=" + ",".join(g.name for g in app.registered_groups))
"""

# The same cycle without an installed extension: a module that imports
# ``pyrite.cli.context`` at the top and only then defines its Typer app, which the
# registry is asked for while ``pyrite.cli`` is still importing.
CYCLE_PROBE = textwrap.dedent(
    """
    import pyrite.plugins as plugins

    class FakeRegistry:
        aggregate_failures = []

        def get_all_mcp_tools(self, tier):
            return {}

        def get_all_cli_commands(self):
            import fake_plugin_cli
            return [("fake", fake_plugin_cli.fake_app)]

    plugins.get_registry = lambda: FakeRegistry()

    import fake_plugin_cli
    from pyrite.cli import app
    print("GROUPS=" + ",".join(g.name for g in app.registered_groups))
    """
)

FAKE_PLUGIN_CLI = textwrap.dedent(
    """
    import typer

    from pyrite.cli.context import require_kb

    fake_app = typer.Typer(help="fake plugin")
    """
)


def _run(code: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)


def test_extension_cli_imported_first_is_registered():
    r = _run(PROBE)
    assert r.returncode == 0, r.stderr
    groups = r.stdout.split("GROUPS=")[1].strip().split(",")
    assert "sw" in groups, (r.stdout, r.stderr)
    assert "unavailable" not in r.stderr, r.stderr


def test_plugin_cli_group_survives_the_import_cycle(tmp_path):
    (tmp_path / "fake_plugin_cli.py").write_text(FAKE_PLUGIN_CLI, encoding="utf-8")
    r = subprocess.run(
        [sys.executable, "-c", CYCLE_PROBE],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=tmp_path,
        env={
            **os.environ,
            "PYTHONPATH": os.pathsep.join([str(tmp_path), str(REPO_ROOT)]),
        },
    )
    assert r.returncode == 0, r.stderr
    groups = r.stdout.split("GROUPS=")[1].strip().split(",")
    assert "fake" in groups, (r.stdout, r.stderr)


def test_every_plugin_cli_group_present_in_normal_order():
    r = _run(
        "from pyrite.cli import app\n"
        "from pyrite.plugins import get_registry\n"
        "names = {n for n, _ in get_registry().get_all_cli_commands()}\n"
        "have = {g.name for g in app.registered_groups}\n"
        "print('MISSING=' + ','.join(sorted(names - have)))\n"
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip().endswith("MISSING="), (r.stdout, r.stderr)
