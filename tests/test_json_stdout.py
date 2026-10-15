"""Machine-readable CLI output must survive pipes and terminal styling."""

import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from rich.console import Console
from typer.testing import CliRunner

from pyrite import admin_cli
from pyrite.cli import qa_commands
from pyrite.services.url_checker import URLChecker, URLCheckResult

runner = CliRunner()


@contextmanager
def _fake_cli_context():
    """Same shape as cli_context(): a context manager yielding (config, db, svc)."""
    yield None, None, None


@pytest.fixture(params=[False, True])
def json_console(request, monkeypatch):
    if request.param:
        monkeypatch.setenv("FORCE_COLOR", "1")
    else:
        monkeypatch.delenv("FORCE_COLOR", raising=False)
    return Console(width=40)


def test_admin_schema_json_preserves_long_values(monkeypatch, json_console):
    expected = {"description": "[bold]schema[/bold] " + "description " * 20}
    kb = SimpleNamespace(kb_schema=SimpleNamespace(to_agent_schema=lambda: expected))
    monkeypatch.setattr(admin_cli, "load_config", lambda: SimpleNamespace(get_kb=lambda _: kb))
    monkeypatch.setattr(admin_cli, "console", json_console)

    result = runner.invoke(admin_cli.app, ["schema", "test"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == expected
    assert "\x1b" not in result.stdout


@pytest.mark.parametrize("has_urls", [False, True])
def test_check_urls_json_is_one_document(monkeypatch, json_console, has_urls):
    url = "https://example.invalid/" + "long-path/" * 20
    entries = {url: ["source"]} if has_urls else {}
    monkeypatch.setattr(qa_commands, "console", json_console)
    monkeypatch.setattr(qa_commands, "cli_context", _fake_cli_context)
    monkeypatch.setattr(URLChecker, "collect_urls", lambda self, kb: entries)
    monkeypatch.setattr(
        URLChecker,
        "check_url",
        lambda self, url: URLCheckResult(url=url, status_code=404, ok=False),
    )

    result = runner.invoke(qa_commands.qa_app, ["check-urls", "test", "--format", "json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["total_urls"] == int(has_urls)
    assert data["broken"] == int(has_urls)
    assert data["ok"] == 0
    assert data["broken_details"] == (
        [{"url": url, "status_code": 404, "error": "", "entry_ids": ["source"]}] if has_urls else []
    )
    assert "\x1b" not in result.stdout


@pytest.mark.parametrize(
    ("command", "count_key"), [("stale", "stale_count"), ("compact", "candidate_count")]
)
@pytest.mark.parametrize("output_format", ["json", "yaml", "csv", "markdown"])
def test_qa_reports_json_preserves_stale_entry_title(
    monkeypatch, json_console, pyrite_config, pyrite_db, command, count_key, output_format
):
    from pyrite.cli import app
    from pyrite.cli import context as cli_context_module

    title = "[bold]literal title[/bold] " + "long title " * 30
    kb = pyrite_config.get_kb("test-research")
    pyrite_db.register_kb(kb.name, kb.kb_type, str(kb.path))
    pyrite_db.upsert_entry(
        {
            "id": "old-note",
            "kb_name": "test-research",
            "entry_type": "note",
            "title": title,
            "body": "Old orphan note",
            "updated_at": "2000-01-01T00:00:00+00:00",
            "importance": 1,
        }
    )
    monkeypatch.setattr(cli_context_module, "load_config", lambda: pyrite_config)
    monkeypatch.setattr(qa_commands, "console", json_console)

    result = runner.invoke(app, ["qa", command, "test-research", "--format", output_format])

    assert result.exit_code == 0, result.output
    if output_format in {"json", "yaml"}:
        import yaml

        data = (
            json.loads(result.stdout) if output_format == "json" else yaml.safe_load(result.stdout)
        )
        assert data[count_key] == 1
        assert data["entries"][0]["entry_id"] == "old-note"
        assert data["entries"][0]["title"] == title
    else:
        assert title in result.stdout
    assert "\x1b" not in result.stdout


@pytest.mark.parametrize("output_format", ["json", "rich"])
@pytest.mark.parametrize(
    "error_kind", ["sqlite", "sqlalchemy", "storage", "locked", "wrapped_locked"]
)
def test_check_urls_reports_database_failure(
    monkeypatch, pyrite_config, pyrite_db, output_format, error_kind
):
    import sqlite3

    from sqlalchemy.exc import OperationalError

    from pyrite.cli import app
    from pyrite.cli import context as cli_context_module
    from pyrite.exceptions import StorageBusyError, StorageError
    from pyrite.storage.database import PyriteDB

    errors = {
        "sqlite": sqlite3.OperationalError("private database detail"),
        "sqlalchemy": OperationalError("SELECT", {}, Exception("private database detail")),
        "storage": StorageError("private database detail"),
        "locked": sqlite3.OperationalError("database is locked"),
        "wrapped_locked": OperationalError(
            "SELECT", {}, sqlite3.OperationalError("database is locked")
        ),
    }

    def fail_list_entries(self, **kwargs):
        raise errors[error_kind]

    monkeypatch.setattr(cli_context_module, "load_config", lambda: pyrite_config)
    monkeypatch.setattr(PyriteDB, "get_distinct_types", fail_list_entries)

    result = runner.invoke(app, ["qa", "check-urls", "test-research", "--format", output_format])

    assert result.exit_code == 1, result.output
    assert "No source URLs found" not in result.stdout
    assert "private database detail" not in result.stdout
    if output_format == "json":
        data = json.loads(result.stdout)
        busy = error_kind in {"locked", "wrapped_locked"}
        assert data["error_code"] == "STORAGE_ERROR"
        assert data["error"] == (
            StorageBusyError.public_message if busy else StorageError.public_message
        )
        assert data["retryable"] is busy
    else:
        assert "STORAGE_ERROR" in result.stdout


@pytest.mark.parametrize("bare_sources", [False, True])
def test_check_urls_finds_sources_on_document_entries(
    monkeypatch, pyrite_config, pyrite_db, bare_sources
):
    from pyrite.cli import app
    from pyrite.cli import context as cli_context_module
    from pyrite.storage.database import PyriteDB

    url = "https://example.invalid/document"
    kb = pyrite_config.get_kb("test-research")
    pyrite_db.register_kb(kb.name, kb.kb_type, str(kb.path))
    pyrite_db.upsert_entry(
        {
            "id": "document-source",
            "kb_name": "test-research",
            "entry_type": "document",
            "title": "Document source",
            "sources": [{"url": url}],
        }
    )
    monkeypatch.setattr(cli_context_module, "load_config", lambda: pyrite_config)
    if bare_sources:
        real_get_entry = PyriteDB.get_entry

        def get_with_bare_sources(self, entry_id, kb_name):
            entry = real_get_entry(self, entry_id, kb_name)
            entry["sources"] = [url]
            return entry

        monkeypatch.setattr(PyriteDB, "get_entry", get_with_bare_sources)
    monkeypatch.setattr(
        URLChecker,
        "check_url",
        lambda self, url: URLCheckResult(url=url, status_code=404, ok=False),
    )

    result = runner.invoke(app, ["qa", "check-urls", "test-research", "--format", "json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["total_urls"] == 1
    assert data["broken_details"][0]["url"] == url
    assert data["broken_details"][0]["entry_ids"] == ["document-source"]


@pytest.mark.control
def test_url_collection_preserves_other_types_when_documents_fill_query_limit():
    from unittest.mock import MagicMock

    db = MagicMock()
    documents = [{"id": f"document-{i}"} for i in range(10000)]
    db.get_distinct_types.return_value = ["document", "note"]

    def list_entries(*, kb_name, entry_type=None, limit):
        # Documents are newer than the note, so an unfiltered query hides it.
        entries = documents if entry_type in {None, "document"} else [{"id": "old-note"}]
        return entries[:limit]

    db.list_entries.side_effect = list_entries
    url = "https://example.invalid/older-note"
    db.get_entry.side_effect = lambda entry_id, kb_name: {
        "sources": [url] if entry_id == "old-note" else []
    }

    assert URLChecker(db).collect_urls("test") == {url: ["old-note"]}
