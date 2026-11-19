"""Standard-library hooks work from a shell with python3 but no python alias."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("hook_id", ["import-cycle-check", "fix-commit-has-tests"])
@pytest.mark.parametrize(
    "layout",
    [
        "python3",
        "broken-venv",
        "none",
        pytest.param("venv", marks=pytest.mark.control(reason="the checkout venv stays preferred")),
        pytest.param(
            "python", marks=pytest.mark.control(reason="the python fallback stays usable")
        ),
    ],
)
def test_stdlib_hook_selects_an_available_interpreter(tmp_path, hook_id, layout):
    config = yaml.safe_load((REPO / ".pre-commit-config.yaml").read_text())
    hook = next(h for r in config["repos"] for h in r["hooks"] if h["id"] == hook_id)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "bash").symlink_to(shutil.which("bash"))
    if layout in {"venv", "python3", "broken-venv"}:
        (bindir / "python3").symlink_to(sys.executable)
    if layout in {"venv", "python"}:
        (bindir / "python").symlink_to(sys.executable)
    if layout in {"venv", "broken-venv"}:
        venv = tmp_path / ".venv/bin"
        venv.mkdir(parents=True)
        (venv / "python").symlink_to(sys.executable if layout == "venv" else venv / "missing")

    scripts = tmp_path / "scripts"
    scripts.mkdir()
    script = (
        "check_import_cycles.py"
        if hook_id == "import-cycle-check"
        else "check_fix_commit_has_tests.py"
    )
    (scripts / script).write_text(
        "import json, sys\nprint(json.dumps({'python': sys.executable, 'args': sys.argv[1:]}))\n"
    )
    env = {**os.environ, "PATH": str(bindir)}
    result = subprocess.run(
        [str(bindir / "bash"), "-c", hook["entry"] + " message.txt"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if layout == "none":
        assert result.returncode == 1, result.stderr
        assert hook_id in result.stderr
        assert "scripts/setup-checkout.sh" in result.stderr
        assert "scripts/new-worktree.sh" in result.stderr
        return

    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    selected = (
        ".venv/bin/python"
        if layout == "venv"
        else f"bin/{'python' if layout == 'python' else 'python3'}"
    )
    assert body["python"] == str(tmp_path / selected)
    assert body["args"] == (["message.txt"] if hook_id == "fix-commit-has-tests" else [])
