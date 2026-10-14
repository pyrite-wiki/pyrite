"""An extension CLI module imported before ``pyrite.cli`` must not vanish silently (#900).

Importing ``pyrite_software_kb.cli`` first makes ``pyrite.cli``'s plugin loop call
``get_cli_commands`` on a half-initialised module; that raises. Loading order is
not changed here, so the property is: the failure is reported on stderr, naming
the plugin, or the group is present. Subprocesses control import order.
"""

import subprocess
import sys

PROBE = """
import pyrite_software_kb.cli
from pyrite.cli import app
print("GROUPS=" + ",".join(g.name for g in app.registered_groups))
"""


def _run(code: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)


def test_extension_cli_imported_first_is_registered_or_reported():
    r = _run(PROBE)
    assert r.returncode == 0, r.stderr
    groups = r.stdout.split("GROUPS=")[1].strip().split(",")
    assert "sw" in groups or ("software-kb" in r.stderr and "unavailable" in r.stderr), (
        r.stdout,
        r.stderr,
    )


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
