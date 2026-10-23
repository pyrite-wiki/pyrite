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
def _fake_cli_context(*args, **kwargs):
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


def _note(entry_id, title, body="Old orphan note", sources=()):
    lines = ["---", f"id: {entry_id}", "type: note", f"title: {json.dumps(title)}"]
    lines += [
        "importance: 1",
        "created_at: 2000-01-01T00:00:00+00:00",
        "updated_at: 2000-01-01T00:00:00+00:00",
    ]
    if sources:
        lines.append("sources:")
        lines += [f"  - url: {url}" for url in sources]
    return "\n".join(lines) + f"\n---\n{body}\n"


@pytest.mark.parametrize(
    ("command", "count_key"), [("stale", "stale_count"), ("compact", "candidate_count")]
)
@pytest.mark.parametrize("output_format", ["json", "yaml", "csv", "markdown"])
def test_qa_reports_preserve_entry_title(
    monkeypatch, cli_world, json_console, command, count_key, output_format
):
    title = "[bold]literal title[/bold] " + "long title " * 30
    world = cli_world()
    world.add_entry("test-kb", _note("old-note", title))
    monkeypatch.setattr(qa_commands, "console", json_console)

    result = world.cli("qa", command, "test-kb", "--format", output_format)

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


@pytest.mark.control(
    reason="an existing guard; #1260 only moved its list_entries fault to a generated "
    "column, since the open now repairs a dropped one"
)
@pytest.mark.parametrize("output_format", ["json", "rich"])
@pytest.mark.parametrize("failure_target", ["list_entries", "get_entry"])
def test_check_urls_reports_database_failure(cli_world, output_format, failure_target, caplog):
    import sqlite3

    from pyrite.exceptions import StorageError

    world = cli_world()
    world.add_entry("test-kb", _note("source", "Source", sources=["https://example.invalid/a"]))
    index_path = world.config.settings.index_path
    world.db.close()
    # Real faults. list_entries selects every entry column; get_entry also reads `source`.
    broken = sqlite3.connect(index_path, isolation_level=None)
    if failure_target == "list_entries":
        # A dropped column is repaired when the index opens (#1260), so the
        # fault is in a value: a generated column whose expression overflows
        # when the select reads it.
        broken.execute("ALTER TABLE entry DROP COLUMN aliases")
        broken.execute(
            "ALTER TABLE entry ADD COLUMN aliases TEXT GENERATED ALWAYS AS "
            "(abs(-9223372036854775807 - 1)) VIRTUAL"
        )
    else:
        broken.execute("DROP TABLE source")
        broken.execute("CREATE VIEW source AS SELECT abs(-9223372036854775807 - 1) AS id")
    broken.close()

    result = world.cli("qa", "check-urls", "test-kb", "--format", output_format)

    assert result.exit_code == 1, result.output
    assert "No source URLs found" not in result.stdout
    assert "no such" not in result.stdout
    if output_format == "json":
        data = json.loads(result.stdout)
        assert data["error_code"] == "STORAGE_ERROR"
        assert data["error"] == StorageError.public_message
        assert data["retryable"] is False
    else:
        assert "STORAGE_ERROR" in result.stdout
    assert "URL collection failed" in caplog.text


@pytest.mark.parametrize("wrapped", [False, True])
def test_url_collection_keeps_lock_failures_retryable(wrapped):
    import sqlite3

    from sqlalchemy.exc import OperationalError

    from pyrite.exceptions import StorageBusyError

    locked = sqlite3.OperationalError("database is locked")
    error = OperationalError("SELECT", {}, locked) if wrapped else locked

    class LockedDB:
        def list_entries(self, **kwargs):
            raise error

    with pytest.raises(StorageBusyError) as raised:
        URLChecker(LockedDB()).collect_urls("test")

    assert raised.value.retryable is True
    assert "database is locked" not in str(raised.value.public_message)


@pytest.mark.control(reason="dev already scans every entry type (#805); guards the carried rewrite")
def test_check_urls_finds_sources_on_document_entries(cli_world):
    url = "https://example.invalid/document"
    world = cli_world()
    world.add_entry(
        "test-kb",
        _note("document-source", "Document source", sources=[url]).replace(
            "type: note", "type: document"
        ),
    )
    checked = []

    def not_found(self, req):
        import urllib.error

        checked.append(req.full_url)
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

    URLChecker._urlopen, saved = not_found, URLChecker._urlopen
    try:
        result = world.cli("qa", "check-urls", "test-kb", "--format", "json")
    finally:
        URLChecker._urlopen = saved

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["total_urls"] == 1
    assert data["broken_details"][0]["url"] == url
    assert data["broken_details"][0]["entry_ids"] == ["document-source"]


@pytest.mark.parametrize("command", ["stale", "compact"])
def test_qa_reports_empty_csv_outputs_formatted_empty(cli_world, command):
    world = cli_world()

    result = world.cli("qa", command, "test-kb", "--format", "csv")

    assert result.exit_code == 0, result.output
    assert "No stale entries" not in result.stdout
    assert "No archival candidates" not in result.stdout
    assert "\x1b" not in result.stdout
