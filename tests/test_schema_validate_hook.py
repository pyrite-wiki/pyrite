"""The KB-validation commit hook must run an executable, not the package dir.

#664: with no .venv, `P=pyrite` resolved to this checkout's `./pyrite`
package directory (Git Bash puts `.` on PATH) and exec exited 126 with
"Is a directory".
"""

import subprocess
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent


def _schema_hook_entry() -> str:
    config = yaml.safe_load((REPO / ".pre-commit-config.yaml").read_text())
    for repo in config["repos"]:
        for hook in repo["hooks"]:
            if hook["id"] == "pyrite-schema-validate":
                return hook["entry"]
    raise AssertionError("pyrite-schema-validate hook missing")


def test_schema_validate_hook_refuses_package_directory(tmp_path):
    (tmp_path / "pyrite").mkdir()
    result = subprocess.run(
        ["bash", "-c", _schema_hook_entry()],
        cwd=tmp_path,
        env={"PATH": f".:{tmp_path}", "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert result.returncode != 126
    assert "no pyrite executable" in result.stderr
    assert "Is a directory" not in result.stderr + result.stdout


def test_schema_validate_hook_runs_executable_on_path(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    script = bindir / "pyrite"
    script.write_text(
        "#!/bin/sh\n"
        'echo called > "$PWD/called"\n'
        "printf '%s\\n' \"$@\" >> \"$PWD/called\"\n"
    )
    script.chmod(0o755)
    work = tmp_path / "work"
    work.mkdir()
    result = subprocess.run(
        ["bash", "-c", _schema_hook_entry()],
        cwd=work,
        env={"PATH": f"{bindir}:/usr/bin:/bin", "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    called = (work / "called").read_text()
    assert "schema" in called and "validate" in called and "--changed" in called
