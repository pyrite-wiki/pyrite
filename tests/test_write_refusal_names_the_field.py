"""A write refusal names the refused field, on every surface (#1284).

Before this, `VALIDATION_FAILED` carried only a sentence: an event without a
date answered `"Event must have a date"` with no `field` and no `suggestion`,
while `UNDECLARED_TYPE` listed its valid types. A caller had to read prose to
learn which field to fix, and REST, MCP and the CLI had no machine-readable
answer at all.

The entry model now answers `validation_errors()` (one row per refused field,
`{field, rule, message, expected?}`) and `validate()` stays the string view the
loader, `pyrite qa` and the plugins already read. `_prepare` raises
`ValidationError(..., errors=rows)` and `errors` travels through
`refusal_fields`, so the three surfaces answer the same rows.

CLI, MCP (`kb_create`) and REST (`POST /api/entries`) are pinned here; the
schema path's own rows (`SCHEMA_VIOLATION`, `{field, rule, expected, got}`) are
covered by the tests that already assert on `errors`.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.exceptions import ValidationError, refusal_fields
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB
from tests.characterization.world import build_world
from tests.link_scope_seed import READABLE

runner = CliRunner()

NO_DATE = {"entry_type": "event", "title": "No date event"}


@pytest.fixture
def event_kb(tmp_path):
    notes = tmp_path / "events"
    notes.mkdir()
    kb = KBConfig(name="ev", path=notes, kb_type=KBType.GENERIC)
    config = PyriteConfig(knowledge_bases=[kb], settings=Settings(index_path=tmp_path / "index.db"))
    db = PyriteDB(config.settings.index_path)
    try:
        yield config, db
    finally:
        db.close()


def _cli_config(config):
    """Point both `load_config` bindings at the fixture's config, as other CLI tests do."""
    import contextlib

    @contextlib.contextmanager
    def _patched():
        with patch("pyrite.cli.load_config", return_value=config):
            with patch("pyrite.cli.context.load_config", return_value=config):
                yield

    return _patched()


class TestServiceRefusal:
    def test_a_missing_field_names_itself_and_suggests_the_value(self, event_kb):
        config, db = event_kb
        service = KBService(config, db)

        with pytest.raises(ValidationError) as caught:
            service.create_entry("ev", "no-date", "No date event", "event", "x")

        exc = caught.value
        assert exc.error_code == "VALIDATION_FAILED"
        assert exc.errors[0]["field"] == "date"
        assert exc.errors[0]["rule"] == "required"
        assert exc.errors[0]["expected"] == "YYYY-MM-DD"
        assert exc.suggestion == "set date (YYYY-MM-DD)"
        # The sentence every existing caller reads is unchanged.
        assert "Event must have a date" in str(exc)

    def test_every_refused_field_gets_its_own_row(self, event_kb):
        config, db = event_kb
        service = KBService(config, db)

        with pytest.raises(ValidationError) as caught:
            service.create_entry("ev", "bad-event", "Bad event", "event", "x", importance=15)

        fields = [row["field"] for row in caught.value.errors]
        assert fields == ["date", "importance"]
        assert caught.value.errors[1]["rule"] == "range"
        assert caught.value.errors[1]["expected"] == "1-10"
        # The first mechanical fix is the one suggested.
        assert caught.value.suggestion == "set date (YYYY-MM-DD)"


class TestCliRefusal:
    def test_create_prints_the_field_in_json(self, event_kb):
        config, _db = event_kb
        with _cli_config(config):
            result = runner.invoke(
                app,
                [
                    "create",
                    "-k",
                    "ev",
                    "-t",
                    "event",
                    "--title",
                    "No date event",
                    "--format",
                    "json",
                ],
            )

        assert result.exit_code == 1, result.output
        body = json.loads(result.stdout)
        assert body["error_code"] == "VALIDATION_FAILED"
        assert body["errors"][0]["field"] == "date"
        assert body["suggestion"] == "set date (YYYY-MM-DD)"


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    built = build_world(tmp_path_factory, label="write-refusal-field")
    try:
        yield built
    finally:
        built.close()


class TestRestRefusal:
    def test_post_names_the_field(self, world):
        principal = world.principals["admin_key"]
        response = world.client.post(
            "/api/entries",
            json={"kb": READABLE, **NO_DATE},
            headers=principal.rest_headers,
            cookies=principal.rest_cookies,
        )

        assert response.status_code == 400, response.text
        detail = response.json()["detail"]
        assert detail["code"] == "VALIDATION_FAILED"
        assert detail["errors"][0]["field"] == "date"


class TestMcpRefusal:
    def test_kb_create_names_the_field(self, world):
        result = world.dispatch_tool(
            "kb_create",
            {"kb_name": READABLE, **NO_DATE},
            client_kind="local",
            readable_kbs=None,
        )

        assert result["error_code"] == "VALIDATION_FAILED", result
        assert result["errors"][0]["field"] == "date"


class TestNonFieldRefusal:
    def test_a_refusal_that_is_not_about_a_field_carries_no_errors_key(self, event_kb):
        """`errors` stays None, so `refusal_fields` leaves the key out entirely."""
        config, db = event_kb
        service = KBService(config, db)
        service.create_entry("ev", "dated", "Dated", "event", "x", date="2026-01-01")

        with pytest.raises(ValidationError) as caught:
            service.create_entry("ev", "dated", "Dated again", "event", "x", date="2026-01-01")

        assert caught.value.errors is None
        assert "errors" not in refusal_fields(caught.value)


def test_the_public_contract_documents_the_key():
    """docs/json-contracts.md is the table agents read; it names `errors`."""
    contracts = (Path(__file__).resolve().parent.parent / "docs" / "json-contracts.md").read_text(
        encoding="utf-8"
    )

    assert "A field refusal names the field" in contracts
    assert '"errors": [' in contracts
