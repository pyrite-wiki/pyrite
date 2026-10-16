"""Real metadata and fresh-interpreter verification regressions for #792."""

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner
from pyrite.cli.extension_commands import extension_app, _verify_distribution


@pytest.fixture
def distribution(tmp_path, monkeypatch):
    def create(body, plugins=True):
        (tmp_path / "verify_example.py").write_text(body, encoding="utf-8")
        info = tmp_path / "verify_example-0.1.dist-info"
        info.mkdir()
        (info / "METADATA").write_text(
            "Metadata-Version: 2.1\nName: verify-example\nVersion: 0.1\n", encoding="utf-8"
        )
        if plugins:
            (info / "entry_points.txt").write_text(
                "[pyrite.plugins]\nexample = verify_example:Plugin\n", encoding="utf-8"
            )
        import os

        monkeypatch.setenv(
            "PYTHONPATH",
            os.pathsep.join(
                (
                    str(tmp_path),
                    str(Path(__file__).resolve().parents[1]),
                    os.environ.get("PYTHONPATH", ""),
                )
            ),
        )
        project = tmp_path / "project"
        project.mkdir()
        (project / "pyproject.toml").write_text(
            '[project]\nname="verify-example"\nversion="0.1"\n', encoding="utf-8"
        )
        return project

    return create


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("class Plugin:\n    pass\n", "no 'name' attribute"),
        ("raise RuntimeError('broken import')\n", "broken import"),
    ],
)
def test_failed_verify_keeps_stdout_json(distribution, monkeypatch, body, expected):
    path = distribution(body)
    real_run = subprocess.run

    def run(args, **kwargs):
        if args[1:4] == ["-m", "pip", "install"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        return real_run(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    out = CliRunner().invoke(extension_app, ["install", str(path), "--verify", "--format", "json"])
    assert out.exit_code == 3, out.output
    result = json.loads(out.stdout)
    assert result["status"] == "installed"
    assert result["verified"] is False
    assert expected in result["verify_errors"][0]
    assert "Warning: Plugin verification failed:" in out.stderr


@pytest.mark.control(reason="unchanged valid named plugin acceptance")
def test_named_plugin_verifies(distribution):
    distribution("class Plugin:\n    name = 'example'\n")
    assert _verify_distribution("verify-example") == []


@pytest.mark.control(reason="no-entry-point refusal already exists; documentation follows it")
def test_no_entry_points_are_refused(distribution):
    distribution("X = 1\n", plugins=False)
    assert "declares no 'pyrite.plugins' entry points" in _verify_distribution("verify-example")[0]
