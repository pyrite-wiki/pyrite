"""`pyrite timeline` and `pyrite-read timeline` on a KB with no timeline events (#1947).

An empty result is an ordinary result: the machine formats print the same
shape as a non-empty answer, with an empty list. Only ``--format rich`` prints
the "No timeline events found." sentence.
"""

from __future__ import annotations

import json

import yaml

from pyrite.read_cli import app as read_app

KB = "test-kb"


def _run_pyrite(world, *args: str):
    result = world.cli("timeline", "-k", KB, *args)
    assert result.exit_code == 0, result.output
    return result.stdout


def _run_read(world, *args: str):
    result = world.cli("timeline", *args, app=read_app)
    assert result.exit_code == 0, result.output
    return result.stdout


def test_empty_timeline_json_prints_empty_envelope(cli_world):
    world = cli_world()
    assert json.loads(_run_pyrite(world, "--format", "json")) == {"count": 0, "events": []}


def test_empty_timeline_default_format_is_json(cli_world):
    world = cli_world()
    assert json.loads(_run_pyrite(world)) == {"count": 0, "events": []}


def test_empty_timeline_yaml_prints_empty_envelope(cli_world):
    world = cli_world()
    assert yaml.safe_load(_run_pyrite(world, "--format", "yaml")) == {"count": 0, "events": []}


def test_empty_timeline_rich_keeps_sentence(cli_world):
    world = cli_world()
    assert "No timeline events found." in _run_pyrite(world, "--format", "rich")


def test_read_cli_empty_timeline_json(cli_world):
    world = cli_world()
    assert json.loads(_run_read(world, "--format", "json")) == {"count": 0, "events": []}


def test_read_cli_empty_timeline_default_is_rich(cli_world):
    world = cli_world()
    assert "No timeline events found." in _run_read(world)
