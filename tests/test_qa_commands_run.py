"""`qa` commands must run end to end through the real cli_context (#574).

`cli_context()` is a context manager that closes the database on exit. A bare
`ctx = cli_context()` crashed (`'_GeneratorContextManager' object has no
attribute 'db'`), and nothing noticed because tests replaced `cli_context` with
a stand-in. Here only the HTTP layer is replaced.
"""

import json
import tempfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.cli import context as cli_context_module
from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.models.core_types import NoteEntry
from pyrite.services.url_checker import URLChecker, URLCheckResult
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager
from pyrite.storage.repository import KBRepository

from tests.cli_help import plain

runner = CliRunner()

KB = "qa-run"


def _make_env(tmpdir: Path, urls: list[str]) -> PyriteConfig:
    kb_path = tmpdir / "kb"
    kb_path.mkdir()
    kb = KBConfig(name=KB, path=kb_path, kb_type=KBType.GENERIC)
    config = PyriteConfig(knowledge_bases=[kb], settings=Settings(index_path=tmpdir / "index.db"))
    entry = NoteEntry(id="source-note", title="Source note", body="A note.")
    for i, url in enumerate(urls):
        entry.add_source(title=f"Source {i}", url=url)
    KBRepository(kb).save(entry)
    db = PyriteDB(config.settings.index_path)
    IndexManager(db, config).index_all()
    db.close()
    return config


@pytest.fixture
def real_context(monkeypatch):
    """Patch only where config comes from; track that the real db is closed.

    Returns a factory (urls) -> list of "closed"/"checked" events in order.
    """
    with tempfile.TemporaryDirectory() as tmp:
        events: list[str] = []

        def build(urls):
            config = _make_env(Path(tmp), urls)
            real_db_class = cli_context_module.PyriteDB

            class TrackingDB(real_db_class):
                def close(self):
                    events.append("closed")
                    super().close()

            monkeypatch.setattr(cli_context_module, "PyriteDB", TrackingDB)
            monkeypatch.setattr(cli_context_module, "load_config", lambda: config)
            monkeypatch.setattr(
                URLChecker,
                "check_url",
                lambda self, u: (
                    events.append("checked"),
                    URLCheckResult(url=u, status_code=404, ok=False),
                )[1],
            )
            return events

        yield build


@pytest.mark.parametrize("output_format", ["rich", "json"])
@pytest.mark.parametrize("sample", [0, 1, 99])
def test_check_urls_runs_and_closes_db(real_context, output_format, sample):
    urls = ["https://example.invalid/a", "https://example.invalid/b"]
    events = real_context(urls)

    result = runner.invoke(
        app,
        ["qa", "check-urls", KB, "--format", output_format, "--sample", str(sample)],
    )

    assert result.exit_code == 0, result.output
    assert events.count("closed") == 1
    expected = 1 if sample == 1 else 2
    assert events.count("checked") == expected
    if output_format == "json":
        report = json.loads(result.output)
        assert report["total_urls"] == expected
        assert report["broken"] == expected
    else:
        assert "404" in result.output or "Broken" in result.output


@pytest.mark.parametrize("output_format", ["rich", "json"])
def test_check_urls_with_no_source_urls_returns_inside_the_block(real_context, output_format):
    events = real_context([])

    result = runner.invoke(app, ["qa", "check-urls", KB, "--format", output_format])

    assert result.exit_code == 0, result.output
    assert events == ["closed"]
    if output_format == "json":
        assert json.loads(result.output)["total_urls"] == 0
    else:
        assert "No source URLs found" in result.output


def test_check_urls_reads_the_database_before_it_is_closed(real_context):
    """collect_urls needs the open database; closing first is the bug #577 fixes.

    A closed PyriteDB does not raise on a later read, so record the order
    instead: the URL list must have been read before close().
    """
    events = real_context(["https://example.invalid/a"])
    real_collect = URLChecker.collect_urls

    def recording_collect(self, kb_name):
        events.append("collected")
        return real_collect(self, kb_name)

    URLChecker.collect_urls = recording_collect
    try:
        result = runner.invoke(app, ["qa", "check-urls", KB, "--format", "json"])
    finally:
        URLChecker.collect_urls = real_collect

    assert result.exit_code == 0, result.output
    assert events.index("collected") < events.index("closed")


def test_check_urls_refuses_a_negative_sample() -> None:
    from pyrite.cli.qa_commands import qa_app

    result = runner.invoke(qa_app, ["check-urls", KB, "--sample", "-1"])

    assert result.exit_code == 2, result.output
    assert "Invalid value" in plain(result.output)
