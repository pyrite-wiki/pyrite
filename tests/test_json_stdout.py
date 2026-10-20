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
