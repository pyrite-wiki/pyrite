"""A real subprocess detects foreign core and extension editable imports."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check_checkout_imports.py"


def _checkout(root):
    packages = [
        root / "pyrite",
        root / "extensions/first/src/pyrite_first",
        root / "extensions/second/src/pyrite_second",
    ]
    for package in packages:
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    return [root, root / "extensions/first/src", root / "extensions/second/src"]


@pytest.mark.parametrize("foreign", ["core", "extension", None])
def test_real_import_origins_are_checked_for_every_package(tmp_path, foreign):
    own, other = tmp_path / "own", tmp_path / "other"
    own_paths, other_paths = _checkout(own), _checkout(other)
    paths = list(own_paths)
    if foreign == "core":
        paths[0] = other_paths[0]
    elif foreign == "extension":
        paths[2] = other_paths[2]
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(own)],
        cwd=tmp_path,
        env=dict(os.environ, PYTHONPATH=os.pathsep.join(map(str, paths))),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == (1 if foreign else 0), result.stdout + result.stderr
    if foreign:
        assert ("pyrite_second:" if foreign == "extension" else "pyrite:") in result.stderr
        assert "setup-checkout.sh" in result.stderr
    else:
        assert "every extension" in result.stdout


def test_stamp_cannot_skip_the_import_guard():
    setup = (SCRIPT.parent / "setup-checkout.sh").read_text()
    before_skip = setup.split('echo "setup-checkout:')[0]
    assert '&& .venv/bin/python scripts/check_checkout_imports.py --root "$wt_dir"' in before_skip
    assert setup.rindex(".venv/bin/python scripts/check_checkout_imports.py") < setup.index(
        'echo "$stamp" >'
    )


def test_this_checkout_core_and_extension_imports():
    result = subprocess.run(
        [sys.executable, str(SCRIPT)], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stdout + result.stderr
