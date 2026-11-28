"""`pyrite init -t software` says how to get `pyrite sw` when it is missing (#1457).

The software template is built in, so it works without the software-kb
extension, but the `sw` commands it is meant for do not. Hallway runs found two
users who made the KB and then met `No such command 'sw'`. The line goes to
stderr, so the document on stdout is unchanged. Whether the extension is
installed is decided by ``importlib.util.find_spec``; each case sets that
answer itself, so they hold in any environment.
"""

import importlib.util
import json
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _init(world, tmp_path, template="software"):
    return world.cli(
        "init", "--template", template, "--path", str(tmp_path / "kb-x"), "--name", "kb-x",
        "--no-examples",
    )  # fmt: skip


def _is_installed(monkeypatch, installed: bool):
    real = importlib.util.find_spec

    def find_spec(name, *args, **kwargs):
        if name == "pyrite_software_kb":
            return object() if installed else None
        return real(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", find_spec)


def test_the_software_template_without_the_extension_names_the_install(
    cli_world, tmp_path, monkeypatch
):
    version = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["version"]
    _is_installed(monkeypatch, False)
    result = _init(cli_world(), tmp_path)
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["status"] == "created"  # stdout is still one document
    note = [line for line in result.stderr.splitlines() if "pyrite sw" in line]
    assert len(note) == 1, result.stderr
    assert "software-kb extension" in note[0]
    assert (
        f'pip install "pyrite-software-kb @ git+https://github.com/pyrite-wiki/pyrite@v{version}'
        '#subdirectory=extensions/software-kb"' in note[0]
    )


def test_the_software_template_with_the_extension_says_nothing_extra(
    cli_world, tmp_path, monkeypatch
):
    _is_installed(monkeypatch, True)
    result = _init(cli_world(), tmp_path)
    assert result.exit_code == 0, result.output
    assert "pip install" not in result.stderr
    assert "pyrite sw" not in result.stderr


def test_another_template_says_nothing_about_the_extension(cli_world, tmp_path, monkeypatch):
    _is_installed(monkeypatch, False)
    result = _init(cli_world(), tmp_path, template="research-queue")
    assert result.exit_code == 0, result.output
    assert "pip install" not in result.stderr
