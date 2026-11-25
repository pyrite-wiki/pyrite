"""Regression tests for `init -t software` -> `sw log` parity (#1762)."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from pyrite_software_kb.preset import SOFTWARE_KB_PRESET

from pyrite.cli.init_command import BUILTIN_TEMPLATES


def _isolated_env(tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    for key, subdir in (
        ("HOME", "home"),
        ("XDG_CONFIG_HOME", "xdg_config"),
        ("XDG_DATA_HOME", "xdg_data"),
        ("PYRITE_CONFIG_DIR", "pyrite_config"),
        ("PYRITE_DATA_DIR", "pyrite_data"),
    ):
        directory = tmp_path / subdir
        directory.mkdir(parents=True, exist_ok=True)
        env[key] = str(directory)
    env["PYRITE_AUTO_EMBED"] = "false"
    env["PYRITE_DISABLE_TELEMETRY"] = "1"
    return env


def _run_pyrite(
    args: list[str],
    env: dict[str, str],
    cwd: Path,
    *,
    block_software_plugin: bool = False,
) -> subprocess.CompletedProcess[str]:
    preamble = "import sys; "
    if block_software_plugin:
        # Simulate `pyrite init -t software` run before `pyrite-software-kb` is installed.
        preamble += "sys.modules['pyrite_software_kb'] = None; "
    code = f"{preamble}from pyrite.cli import main; sys.argv = {['pyrite', *args]!r}; main()"
    return subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=90,
    )


def test_sw_log_succeeds_after_builtin_software_init(tmp_path: Path) -> None:
    """A KB initialized via the built-in software template accepts `sw log` once software-kb is installed (#1762)."""
    env = _isolated_env(tmp_path)
    kb_path = tmp_path / "proj"

    init_res = _run_pyrite(
        ["init", "-t", "software", "-p", str(kb_path)],
        env,
        tmp_path,
        block_software_plugin=True,
    )
    assert init_res.returncode == 0, init_res.stdout + init_res.stderr

    create_res = _run_pyrite(
        [
            "create",
            "-k",
            "proj",
            "-t",
            "backlog_item",
            "--title",
            "Crash on empty invoice",
            "-f",
            "kind=bug",
            "--format",
            "json",
        ],
        env,
        tmp_path,
    )
    assert create_res.returncode == 0, create_res.stdout + create_res.stderr

    log_res = _run_pyrite(
        ["sw", "log", "crash-on-empty-invoice", "-k", "proj", "-s", "investigated"],
        env,
        tmp_path,
    )
    assert log_res.returncode == 0, log_res.stdout + log_res.stderr
    match = re.search(r"Logged: (\S+)", log_res.stdout)
    assert match is not None, log_res.stdout
    log_files = list((kb_path / "work-logs").glob(f"{match.group(1)}.md"))
    assert len(log_files) == 1
    assert "investigated" in log_files[0].read_text(encoding="utf-8")


@pytest.mark.control(reason="plugin preset already declares work_log")
def test_sw_log_succeeds_after_preset_software_init(tmp_path: Path) -> None:
    """A KB initialized via the software-kb plugin preset also accepts `sw log`."""
    env = _isolated_env(tmp_path)
    kb_path = tmp_path / "proj"

    init_res = _run_pyrite(["init", "-t", "software", "-p", str(kb_path)], env, tmp_path)
    assert init_res.returncode == 0, init_res.stdout + init_res.stderr

    create_res = _run_pyrite(
        [
            "create",
            "-k",
            "proj",
            "-t",
            "backlog_item",
            "--title",
            "Crash on empty invoice",
            "-f",
            "kind=bug",
            "--format",
            "json",
        ],
        env,
        tmp_path,
    )
    assert create_res.returncode == 0, create_res.stdout + create_res.stderr

    log_res = _run_pyrite(
        ["sw", "log", "crash-on-empty-invoice", "-k", "proj", "-s", "investigated"],
        env,
        tmp_path,
    )
    assert log_res.returncode == 0, log_res.stdout + log_res.stderr
    match = re.search(r"Logged: (\S+)", log_res.stdout)
    assert match is not None, log_res.stdout
    log_files = list((kb_path / "work-logs").glob(f"{match.group(1)}.md"))
    assert len(log_files) == 1
    assert "investigated" in log_files[0].read_text(encoding="utf-8")


def test_builtin_software_template_matches_plugin_preset_types() -> None:
    """Built-in `software` template declares the same types and directories as `SOFTWARE_KB_PRESET` (#1762)."""
    builtin = BUILTIN_TEMPLATES["software"]
    assert set(builtin["types"]) == set(SOFTWARE_KB_PRESET["types"])
    assert set(builtin["directories"]) == set(SOFTWARE_KB_PRESET["directories"])
    for entry_type, preset_type in SOFTWARE_KB_PRESET["types"].items():
        assert builtin["types"][entry_type].get("fields", {}) == preset_type.get("fields", {}), (
            f"fields mismatch for {entry_type}"
        )
