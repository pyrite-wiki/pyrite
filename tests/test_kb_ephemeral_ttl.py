"""`kb create --ephemeral --ttl` refuses negative values (#1407).

A negative TTL has no sensible meaning: the KB is born expired, its expiry
5 seconds in the past, so the next garbage collection deletes it, yet the
command reported success. The CLI now refuses it as a usage error (exit 2)
before creating anything. `--ttl 0` still means "never expires" and is
unchanged.
"""

import json

import pytest


@pytest.mark.cli
@pytest.mark.parametrize("ttl", [-5, -1, -3600])
def test_create_ephemeral_rejects_negative_ttl_without_creating(cli_world, tmp_path, ttl):
    world = cli_world()
    result = world.cli("kb", "create", "--ephemeral", "--name", "ttltest", "--ttl", str(ttl))

    assert result.exit_code == 2, result.output
    assert "--ttl" in result.output
    assert "not in the range" in result.output
    # CliRunner mixes the Click usage error with the JSON error envelope;
    # the envelope is the last line.
    envelope = json.loads(result.output.strip().splitlines()[-1])
    assert envelope["error_code"] == "USAGE_ERROR"
    assert not list(tmp_path.rglob("ttltest"))


@pytest.mark.cli
@pytest.mark.control(reason="--ttl 0 still means never expires, not a refusal")
def test_create_ephemeral_accepts_zero_ttl(cli_world):
    world = cli_world()
    result = world.cli("kb", "create", "--ephemeral", "--name", "ttlzero", "--ttl", "0")

    assert result.exit_code == 0, result.output
    assert "Created ephemeral KB" in result.output


@pytest.mark.cli
@pytest.mark.control(reason="a positive --ttl still creates the KB")
def test_create_ephemeral_accepts_positive_ttl(cli_world, tmp_path):
    world = cli_world()
    result = world.cli("kb", "create", "--ephemeral", "--name", "ttlpos", "--ttl", "60")

    assert result.exit_code == 0, result.output
    assert "Created ephemeral KB" in result.output
    assert list(tmp_path.rglob("ttlpos"))
