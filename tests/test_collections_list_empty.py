"""`pyrite collections list` on a KB with no collections (#1577).

An empty result is an ordinary result: the machine formats print the same
shape as a non-empty answer, with an empty list. Only ``--format rich`` prints
the "No collections found." sentence.
"""

from __future__ import annotations

import json

import yaml

KB = "test-kb"


def _run(world, *args: str):
    result = world.cli("collections", "list", "-k", KB, *args)
    assert result.exit_code == 0, result.output
    return result.stdout


def test_empty_kb_json_prints_empty_envelope(cli_world):
    world = cli_world()
    assert json.loads(_run(world, "--format", "json")) == {"collections": [], "total": 0}


def test_empty_kb_default_format_is_json(cli_world):
    world = cli_world()
    assert json.loads(_run(world)) == {"collections": [], "total": 0}


def test_empty_kb_yaml_prints_empty_envelope(cli_world):
    world = cli_world()
    assert yaml.safe_load(_run(world, "--format", "yaml")) == {"collections": [], "total": 0}


def test_empty_kb_rich_keeps_sentence(cli_world):
    world = cli_world()
    assert "No collections found." in _run(world, "--format", "rich")
