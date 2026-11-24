"""A kb.yaml rubric pattern cannot stall the process.

Property: ``body_has_pattern`` gives up on a pattern that runs past a fixed
time instead of running to completion, and says so as the checker error the
caller already handles; an ordinary pattern behaves as before.
"""

from __future__ import annotations

import time

import pytest

from pyrite.services import rubric_checkers
from pyrite.services.rubric_checkers import check_body_has_pattern

ENTRY = {"id": "e1", "kb_name": "kb", "body": "a" * 60 + "!"}


@pytest.mark.control(reason="round 1 already bounded the pattern; kept as a guard")
@pytest.mark.timeout(30)
def test_a_pathological_pattern_is_cut_off(monkeypatch):
    monkeypatch.setattr(rubric_checkers, "_timed_out_patterns", {}, raising=False)
    monkeypatch.setattr(rubric_checkers, "PATTERN_TIMEOUT_SECONDS", 0.5, raising=False)
    started = time.monotonic()
    with pytest.raises(ValueError, match="too long"):
        check_body_has_pattern(ENTRY, None, {"pattern": r"^(a|aa)+$"})
    assert time.monotonic() - started < 10


@pytest.mark.control(reason="an ordinary pattern keeps its verdicts")
def test_an_ordinary_pattern_keeps_its_verdicts():
    entry = {"id": "e1", "kb_name": "kb", "body": "intro\nSources: x\n"}
    assert check_body_has_pattern(entry, None, {"pattern": r"sources?:"}) is None
    missing = check_body_has_pattern(entry, None, {"pattern": r"^## Risks"})
    assert missing and missing["rule"] == "rubric_violation"


@pytest.mark.control(reason="an invalid pattern was already reported as ValueError")
def test_an_invalid_pattern_is_still_a_value_error():
    with pytest.raises(ValueError, match="invalid regex"):
        check_body_has_pattern(ENTRY, None, {"pattern": "("})


@pytest.mark.timeout(30)
def test_a_pattern_that_timed_out_is_skipped_for_the_rest_of_the_run(monkeypatch):
    monkeypatch.setattr(rubric_checkers, "PATTERN_TIMEOUT_SECONDS", 0.5, raising=False)
    monkeypatch.setattr(rubric_checkers, "_timed_out_patterns", {}, raising=False)
    params = {"pattern": r"^(a|aa)+$"}
    with pytest.raises(ValueError, match="too long"):  # reported once
        check_body_has_pattern(ENTRY, None, params)
    started = time.monotonic()
    for _ in range(20):  # the remaining entries cost nothing
        assert check_body_has_pattern(ENTRY, None, params) is None
    assert time.monotonic() - started < 0.4
