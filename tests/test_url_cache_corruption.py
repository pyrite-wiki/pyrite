"""The disposable URL cache must not abort QA or turn corrupt data into success."""

import json

import pytest

from pyrite.services.url_checker import URLChecker, URLCheckResult


@pytest.mark.parametrize("payload", [[], None, 3, "text", {"u": None}, {"u": []}])
def test_unusable_cache_is_a_miss(tmp_path, payload):
    path = tmp_path / "cache.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert URLChecker(None, cache_path=path).load_cache() == {}


@pytest.mark.parametrize(
    "bad",
    [
        {"url": "other", "ok": True},
        {"url": "bad", "ok": "false"},
        {"url": "bad", "status_code": "200"},
        {"url": "bad", "status_code": True},
        {"url": "bad", "error": []},
    ],
)
def test_one_bad_record_does_not_discard_a_valid_sibling(tmp_path, bad):
    path = tmp_path / "cache.json"
    valid = {"url": "good", "status_code": 200, "ok": True}
    path.write_text(json.dumps({"bad": bad, "good": valid}), encoding="utf-8")
    loaded = URLChecker(None, cache_path=path).load_cache()
    assert loaded == {"good": URLCheckResult(url="good", status_code=200, ok=True)}


def test_non_utf8_cache_is_a_miss(tmp_path):
    path = tmp_path / "cache.json"
    path.write_bytes(b"\xff")
    assert URLChecker(None, cache_path=path).load_cache() == {}


def test_valid_cache_roundtrip_is_still_used_without_network(tmp_path):
    checker = URLChecker(None, cache_path=tmp_path / "cache.json")
    result = URLCheckResult(url="https://example.invalid", status_code=404, ok=False)
    checker.save_cache({result.url: result})
    assert checker.check_urls([result.url]) == [result]


def test_missing_and_truncated_cache_are_misses(tmp_path):
    path = tmp_path / "cache.json"
    checker = URLChecker(None, cache_path=path)
    assert checker.load_cache() == {}
    path.write_text('{"interrupted":', encoding="utf-8")
    assert checker.load_cache() == {}
