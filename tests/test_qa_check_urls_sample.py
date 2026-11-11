"""Tests for `qa check-urls --sample` bounds (#1399).

The #1399 groom pins the acceptance for a negative sample: refused at parse
time with exit 2 and one JSON document whose `error_code` is `USAGE_ERROR`
and whose message names `--sample`, before any URL is checked.
"""

import json

import pytest
from click import unstyle

KB = "test-kb"


@pytest.mark.cli
@pytest.mark.parametrize("sample", ["-1", "-99"])
def test_negative_sample_is_a_usage_error(cli_world, sample):
    """A negative `--sample` is a usage error, not a `random.sample` crash."""
    world = cli_world()

    result = world.cli("qa", "check-urls", KB, "--format", "json", "--sample", sample)
    output = unstyle(result.output)

    assert result.exit_code == 2, result.output
    assert "Traceback" not in output
    # A usage error is a JSON refusal (ADR-0046, amendment 2026-10-15); the
    # runner mixes in the one plain stderr line, so read the document's line.
    document = json.loads(next(line for line in output.splitlines() if line.startswith("{")))
    assert document["error_code"] == "USAGE_ERROR"
    assert "--sample" in document["error"]
    assert sample in document["error"]


@pytest.mark.cli
@pytest.mark.parametrize("sample", ["0", "2"])
def test_nonnegative_sample_is_accepted(cli_world, sample):
    """Zero (all URLs) and a positive sample still parse and run."""
    world = cli_world()

    result = world.cli("qa", "check-urls", KB, "--format", "json", "--sample", sample)

    assert result.exit_code == 0, result.output
    document = json.loads(result.output)
    assert document["total_urls"] == 0
