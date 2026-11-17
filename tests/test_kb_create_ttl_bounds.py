"""Tests for kb create --ttl numeric range (#1407, #1541).

A negative TTL has no sensible meaning for an ephemeral KB.
Values < 0 must be refused as a usage error (exit 2) before anything is created.
--ttl 0 means never expires and remains accepted.
"""

from __future__ import annotations

import json

import pytest
from typer.main import get_command
from typer.testing import CliRunner

from pyrite.cli import app


def test_kb_create_ttl_declared_min_is_zero() -> None:
    """The --ttl option declares min=0 so Click rejects negative numbers at parse time."""
    click_app = get_command(app)
    kb_cmd = click_app.commands["kb"]
    create_cmd = kb_cmd.commands["create"]
    param = next(p for p in create_cmd.params if "--ttl" in p.opts)
    assert param.type.min == 0
    assert param.type.max is None


@pytest.mark.parametrize("bad_ttl", [-1, -5])
def test_kb_create_rejects_negative_ttl(bad_ttl: int) -> None:
    """Negative TTLs are refused before command execution with usage error exit 2."""
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["kb", "create", "--ephemeral", "--name", "test-neg", "--ttl", str(bad_ttl)],
    )
    assert result.exit_code == 2, result.output
    assert "Invalid value for '--ttl'" in result.output
    assert f"{bad_ttl} is not in the range x>=0" in result.output
    assert "USAGE_ERROR" in result.output


def test_refused_negative_ttl_creates_no_kb_directory(cli_world) -> None:
    """After a refusal for negative TTL, no directory is created and kb list does not list it."""
    world = cli_world()
    result = world.cli("kb", "create", "--ephemeral", "--name", "ttlrefused", "--ttl", "-5")
    assert result.exit_code == 2

    list_res = world.cli("kb", "list", "--format", "json")
    assert list_res.exit_code == 0
    data = json.loads(list_res.output)
    names = [kb["name"] for kb in data.get("kbs", [])]
    assert "ttlrefused" not in names


def test_ttl_zero_creates_kb_that_never_expires(cli_world) -> None:
    """--ttl 0 is valid and creates an ephemeral KB that does not expire."""
    world = cli_world()
    result = world.cli("kb", "create", "--ephemeral", "--name", "ttlzero", "--ttl", "0")
    assert result.exit_code == 0, result.output

    list_res = world.cli("kb", "list", "--format", "json")
    assert list_res.exit_code == 0
    data = json.loads(list_res.output)
    names = [kb["name"] for kb in data.get("kbs", [])]
    assert "ttlzero" in names

    # Run gc: a ttl=0 KB is not removed
    gc_res = world.cli("kb", "gc")
    assert gc_res.exit_code == 0
    list_after_gc = world.cli("kb", "list", "--format", "json")
    data_after = json.loads(list_after_gc.output)
    names_after = [kb["name"] for kb in data_after.get("kbs", [])]
    assert "ttlzero" in names_after
