"""Nonregular inputs are refused without opening streams or changing files."""

import json
import os
from pathlib import Path

import pytest

from pyrite.exceptions import ValidationError


def test_empty_input_names_no_path():
    from pyrite.cli.context import require_input_file

    with pytest.raises(ValidationError, match="no path given"):
        require_input_file("")


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX named FIFO")
@pytest.mark.parametrize("symlink", [False, True])
def test_fifo_and_symlink_to_fifo_are_refused_without_reading(tmp_path, symlink):
    from pyrite.cli.context import require_input_file

    fifo = tmp_path / "input.fifo"
    os.mkfifo(fifo)
    path = fifo
    if symlink:
        path = tmp_path / "alias"
        path.symlink_to(fifo)
    with pytest.raises(ValidationError, match="FIFO"):
        require_input_file(path)


def test_device_input_is_not_a_regular_file():
    from pyrite.cli.context import require_input_file

    with pytest.raises(ValidationError, match="device|not found"):
        require_input_file(os.devnull)


def test_existing_kb_still_validates_the_supplied_schema_file(tmp_path, cli_world):
    world = cli_world()
    folder = tmp_path / "existing"
    folder.mkdir()
    config = folder / "kb.yaml"
    config.write_text("name: existing\n", encoding="utf-8")
    before = config.read_bytes()
    result = world.cli(
        "init",
        "-t",
        "research",
        "-p",
        str(folder),
        "--schema-file",
        str(tmp_path / "absent.yaml"),
        "--format",
        "json",
    )
    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout)["error_code"] == "NOT_FOUND"
    assert config.read_bytes() == before


@pytest.mark.control(reason="A normal input file remains supported")
def test_regular_file_is_allowed(tmp_path):
    from pyrite.cli.context import require_input_file

    path = tmp_path / "input.txt"
    path.write_text("hello", encoding="utf-8")
    assert require_input_file(path) == path
