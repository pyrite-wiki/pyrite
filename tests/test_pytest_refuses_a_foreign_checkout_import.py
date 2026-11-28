"""Pytest must not run against packages installed from another checkout."""

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


REPO = Path(__file__).resolve().parents[1]
CHECKER = REPO / "scripts" / "check_checkout_imports.py"
ROOT_CONFTEST = REPO / "conftest.py"


def _checkout(root: Path) -> dict[str, Path]:
    (root / "extensions" / "sample" / "src" / "pyrite_sample").mkdir(parents=True)
    (root / "pyrite").mkdir()
    (root / "extensions" / "sample" / "src" / "pyrite_sample" / "__init__.py").write_text(
        "VALUE = 1\n", encoding="utf-8"
    )
    (root / "pyrite" / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "scripts").mkdir()
    shutil.copy2(CHECKER, root / "scripts" / CHECKER.name)
    shutil.copy2(ROOT_CONFTEST, root / "conftest.py")
    (root / "tests").mkdir()
    (root / "tests" / "__init__.py").write_text("", encoding="utf-8")
    (root / "tests" / "experimental_surface.py").write_text(
        "def is_experimental(path):\n"
        "    return False\n"
        "\n"
        "def wholly_experimental_file(path):\n"
        "    return False\n",
        encoding="utf-8",
    )
    test = root / "test_marker.py"
    test.write_text(
        "from pathlib import Path\n"
        "\n"
        "def test_marker():\n"
        "    Path(__file__).with_name('ran.txt').write_text('ran', encoding='utf-8')\n",
        encoding="utf-8",
    )
    return {"core": root, "extension": root / "extensions" / "sample" / "src"}


def _run_pytest(
    root: Path, paths: list[Path], **extra_env: str
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.pop("PYRITE_SKIP_CHECKOUT_IMPORTS", None)
    env.update(extra_env)
    env["PYTHONPATH"] = os.pathsep.join(str(path) for path in paths)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--rootdir",
            str(root),
            str(root / "test_marker.py"),
        ],
        cwd=root.parent,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.mark.parametrize("foreign", ["core", "extension", None])
def test_pytest_checks_core_and_extension_import_origins(tmp_path, foreign):
    own, other = tmp_path / "own", tmp_path / "other"
    own_paths, other_paths = _checkout(own), _checkout(other)
    paths = [own_paths["core"], own_paths["extension"]]
    if foreign == "core":
        paths[0] = other_paths["core"]
        (own / "pyrite" / "__init__.py").unlink()
    elif foreign == "extension":
        paths[1] = other_paths["extension"]

    result = _run_pytest(own, paths)

    if foreign:
        assert result.returncode != 0, result.stdout + result.stderr
        package = "pyrite_sample" if foreign == "extension" else "pyrite"
        assert package in result.stderr
        assert str(other) in result.stderr
        assert str(own) in result.stderr
        assert "scripts/setup-checkout.sh" in result.stderr
        assert not (own / "ran.txt").exists()
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert (own / "ran.txt").read_text(encoding="utf-8") == "ran"


def test_pytest_import_check_has_explicit_opt_out(tmp_path):
    own, other = tmp_path / "own", tmp_path / "other"
    own_paths, other_paths = _checkout(own), _checkout(other)
    (own / "pyrite" / "__init__.py").unlink()
    paths = [other_paths["core"], own_paths["extension"]]

    blocked = _run_pytest(own, paths)
    assert blocked.returncode != 0, blocked.stdout + blocked.stderr
    assert str(other) in blocked.stderr
    assert not (own / "ran.txt").exists()

    result = _run_pytest(own, paths, PYRITE_SKIP_CHECKOUT_IMPORTS="1")

    assert result.returncode == 0, result.stdout + result.stderr
    assert (own / "ran.txt").exists()
