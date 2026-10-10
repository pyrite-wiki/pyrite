"""The real root pytest hook must refuse regeneration before tests can write."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ENV = "PYRITE_CHARACTERIZATION_REGENERATE"


def run_pytest(tmp_path, args, *, regenerate="1", addopts="", probe=None, xdist=True):
    shutil.copyfile(ROOT / "conftest.py", tmp_path / "conftest.py")
    (tmp_path / "pytest.ini").write_text("[pytest]\naddopts = " + addopts + "\n")
    (tmp_path / "test_probe.py").write_text(
        "from pathlib import Path\nimport pytest\n"
        "@pytest.fixture(autouse=True)\ndef _no_auto_embed_unless_marked(): pass\n"
        "@pytest.fixture(autouse=True)\ndef _allow_testclient_host(): pass\n"
        + (probe or "def test_probe():\n    Path('would-write-golden').write_text('changed')\n")
    )
    env = os.environ.copy()
    env[ENV] = regenerate
    env.pop("PYTEST_ADDOPTS", None)
    env.pop("PYTEST_PLUGINS", None)
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env["PYTHONPATH"] = str(ROOT)
    # Pin automatic worker discovery to one: even one xdist worker is not serial.
    env["PYTEST_XDIST_AUTO_NUM_WORKERS"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "pytest", *(["-p", "xdist.plugin"] if xdist else []), "-q", *args],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


@pytest.mark.parametrize(
    "args",
    [
        ["-n", "1"],
        ["-n", "2"],
        ["-n", "auto"],
        ["-n", "logical"],
        ["--dist", "load", "--tx", "popen"],
    ],
)
def test_parallel_regeneration_refused_before_any_test_write(tmp_path, args):
    result = run_pytest(tmp_path, args)
    assert result.returncode == pytest.ExitCode.USAGE_ERROR, result.stdout + result.stderr
    assert "run serially" in result.stderr
    assert not (tmp_path / "would-write-golden").exists()


def test_parallel_regeneration_from_addopts_is_refused(tmp_path):
    result = run_pytest(tmp_path, [], addopts="-n 2")
    assert result.returncode == pytest.ExitCode.USAGE_ERROR, result.stdout + result.stderr
    assert "run serially" in result.stderr
    assert not (tmp_path / "would-write-golden").exists()


@pytest.mark.control(reason="Serial regeneration remains permitted with and without xdist options")
@pytest.mark.parametrize(("args", "addopts"), [([], ""), (["-n", "0"], "-n 2")])
def test_serial_regeneration_can_reach_write(tmp_path, args, addopts):
    result = run_pytest(tmp_path, args, addopts=addopts)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "would-write-golden").read_text() == "changed"


@pytest.mark.control(
    reason="Only the exact opt-in value enables regeneration; parallel comparison is unchanged"
)
@pytest.mark.parametrize("regenerate", ["", "0", "true"])
def test_normal_parallel_runs_still_execute(tmp_path, regenerate):
    result = run_pytest(tmp_path, ["-n", "1"], regenerate=regenerate)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "would-write-golden").exists()


@pytest.mark.control(
    reason="The real POSIX golden I/O remains writable serially and readable in parallel"
)
@pytest.mark.skipif(os.name != "posix", reason="golden_io requires fcntl POSIX file locks")
def test_actual_serial_save_and_parallel_comparison(tmp_path):
    probe = (
        "from tests.characterization import golden_io as g\n"
        "g.GOLDEN_DIR = Path('private-goldens')\n"
        "def test_probe():\n"
        "    golden = g.load('probe')\n"
        "    g.assert_matches('probe', 'key', {'value': 1}, golden)\n"
        "    if g.regenerating(): g.save('probe', golden)\n"
    )
    result = run_pytest(tmp_path, ["-n", "0"], probe=probe)
    assert result.returncode == 0, result.stdout + result.stderr
    golden_path = tmp_path / "private-goldens/probe.json"
    before = golden_path.read_bytes()
    result = run_pytest(tmp_path, ["-n", "2"], regenerate="", probe=probe)
    assert result.returncode == 0, result.stdout + result.stderr
    assert golden_path.read_bytes() == before


@pytest.mark.control(reason="Serial regeneration also works when xdist is not loaded")
def test_serial_without_xdist_plugin(tmp_path):
    result = run_pytest(tmp_path, [], xdist=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "would-write-golden").exists()
