import json
from contextlib import contextmanager
from unittest.mock import MagicMock
import pytest
from typer.testing import CliRunner
from pyrite.cli import qa_commands
from pyrite.services.url_checker import URLChecker, URLCheckResult
from pyrite.storage.database import PyriteDB


@pytest.fixture
def db(tmp_path):
    with PyriteDB(tmp_path / "index.db") as database:
        database.register_kb("test", "generic", str(tmp_path / "test"))
        database.register_kb("other-kb", "generic", str(tmp_path / "other"))
        yield database


def add(db, entry_type, kb="test"):
    db.upsert_entry(
        {
            "id": entry_type,
            "kb_name": kb,
            "entry_type": entry_type,
            "title": entry_type,
            "body": "",
            "sources": [{"title": "source", "url": "https://example.invalid/" + entry_type}],
        }
    )


@pytest.mark.parametrize("entry_type", ["event", "article", "custom_plugin_type"])
def test_default_collection_covers_any_indexed_type(db, entry_type):
    add(db, entry_type)
    add(db, "other", kb="other-kb")
    assert URLChecker(db).collect_urls("test") == {
        "https://example.invalid/" + entry_type: [entry_type]
    }


@pytest.mark.control(
    reason="explicit type filters and an empty selection keep their existing meaning"
)
def test_explicit_type_filters_are_preserved(db):
    add(db, "event")
    add(db, "article")
    checker = URLChecker(db)
    assert checker.collect_urls("test", ["event"]) == {"https://example.invalid/event": ["event"]}
    assert checker.collect_urls("test", []) == {}


def test_default_collection_has_no_aggregate_page_cutoff():
    db = MagicMock()

    def page(*, entry_type, limit, offset=0, **kwargs):
        if entry_type is not None:
            return []
        return [{"id": str(i)} for i in range(offset, min(offset + limit, 10001))]

    db.list_entries.side_effect = page
    db.get_entry.side_effect = lambda entry_id, kb: (
        {"sources": ["https://example.invalid/last"]} if entry_id == "10000" else {"sources": []}
    )
    assert URLChecker(db).collect_urls("test") == {"https://example.invalid/last": ["10000"]}


def test_check_urls_cli_includes_core_event_sources(db, monkeypatch):
    add(db, "event")

    @contextmanager
    def context():
        yield None, db, None

    monkeypatch.setattr(qa_commands, "cli_context", context)
    monkeypatch.setattr(URLChecker, "check_url", lambda self, url: URLCheckResult(url, 404, False))
    result = CliRunner().invoke(qa_commands.qa_app, ["check-urls", "test", "--format", "json"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["total_urls"] == 1
    assert report["broken_details"][0]["entry_ids"] == ["event"]
