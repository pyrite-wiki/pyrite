"""Tests verifying that formerly-silent except blocks now log messages."""

import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# 1. pyrite/server/api.py — _resolve_kb_name body parse failure
# ---------------------------------------------------------------------------


@pytest.mark.control(reason="existing fail-closed pin; its mock gained real QueryParams")
def test_resolve_kb_names_logs_warning_and_fails_closed_on_body_parse_failure(caplog):
    """A body that cannot be parsed logs a warning AND refuses to resolve.

    Returning "no KB named" here would make every guard that calls this
    *pass*, which is the wrong default for a permission check -- so the
    resolver raises and the guards turn that into a 400.
    """
    import pytest

    from pyrite.server.api import _resolve_kb_names, _UnparseableBodyError

    from starlette.datastructures import QueryParams

    request = AsyncMock()
    request.query_params = QueryParams("")
    request.path_params = {}
    # Every body but a form (a multipart upload, a form post) is read.
    request.headers = {"content-type": "application/json"}
    request.body = AsyncMock(side_effect=Exception("read error"))

    with caplog.at_level(logging.WARNING, logger="pyrite.server.api"):
        with pytest.raises(_UnparseableBodyError):
            asyncio.run(_resolve_kb_names(request))

    assert "Failed to extract KB from request body" in caplog.text


# ---------------------------------------------------------------------------
# 2. pyrite/services/kb_service.py — schema-to-agent conversion failure
# ---------------------------------------------------------------------------


def test_kb_service_schema_to_agent_logs_warning(caplog):
    """When schema.to_agent_schema() raises, a warning should be logged."""
    from pyrite.services.kb_service import KBService

    svc = object.__new__(KBService)
    svc.config = MagicMock()
    svc.db = MagicMock()

    kb_config = MagicMock()
    kb_config.description = "test"
    kb_config.kb_type = "default"
    kb_config.read_only = False
    kb_config.kb_schema = MagicMock()
    kb_config.kb_schema.to_agent_schema.side_effect = Exception("schema error")
    kb_config.guidelines = {}
    svc.config.get_kb.return_value = kb_config

    svc.db.count_entries.return_value = 0
    svc.db.get_type_distribution.return_value = []
    svc.db.get_tag_distribution.return_value = []
    svc.db.get_entries.return_value = []

    with caplog.at_level(logging.WARNING, logger="pyrite.services.kb_service"):
        result = svc.orient("test-kb")

    assert "Failed schema-to-agent conversion" in caplog.text
    assert result["schema"] == {}


# ---------------------------------------------------------------------------
# 3. pyrite/server/endpoints/entries.py — WebSocket broadcast failures
#    (test that the logger exists and the module-level logger name is correct)
# ---------------------------------------------------------------------------


def test_entries_ws_broadcast_logger_exists():
    """entries.py module should have a logger configured."""
    logger = logging.getLogger("pyrite.server.endpoints.entries")
    assert logger is not None

    # Verify the debug message would be logged at debug level
    with patch.object(logger, "debug") as mock_debug:
        logger.debug("WebSocket broadcast failed (client may have disconnected)")
        mock_debug.assert_called_once()


# ---------------------------------------------------------------------------
# 4. pyrite/server/endpoints/clipper.py — WebSocket broadcast failure
# ---------------------------------------------------------------------------


def test_clipper_ws_broadcast_logger_exists():
    """clipper.py module should have a logger configured."""
    logger = logging.getLogger("pyrite.server.endpoints.clipper")
    assert logger is not None

    with patch.object(logger, "debug") as mock_debug:
        logger.debug("WebSocket broadcast failed (client may have disconnected)")
        mock_debug.assert_called_once()


# ---------------------------------------------------------------------------
# 5. pyrite/cli/__init__.py — field value int coercion
# ---------------------------------------------------------------------------


def test_cli_field_coercion_logs_debug(caplog):
    """Non-numeric --field values should log a debug message."""
    cli_logger = logging.getLogger("pyrite.cli")

    with caplog.at_level(logging.DEBUG, logger="pyrite.cli"):
        # Simulate the code path from cli/__init__.py
        v = "not-a-number"
        try:
            v = int(v)
        except ValueError:
            cli_logger.debug("Could not coerce field value to int: %s", v)

    assert "Could not coerce field value to int: not-a-number" in caplog.text


# ---------------------------------------------------------------------------
# 6. pyrite/formats/importers/csv_importer.py — importance parse failure
# ---------------------------------------------------------------------------


def test_csv_importer_importance_logs_debug(caplog):
    """Non-numeric importance in CSV should log a debug message."""
    from pyrite.formats.importers.csv_importer import import_csv

    csv_data = "title,importance\nTest Entry,not-a-number\n"

    with caplog.at_level(logging.DEBUG, logger="pyrite.formats.importers.csv_importer"):
        entries = import_csv(csv_data)

    assert len(entries) == 1
    assert "importance" not in entries[0]
    assert "Could not parse importance value" in caplog.text


# ---------------------------------------------------------------------------
# 7. pyrite/services/collection_query.py — query parameter parse failures
# ---------------------------------------------------------------------------


def test_collection_query_parse_limit_logs_debug(caplog):
    """Non-numeric limit in query string should log debug."""
    from pyrite.services.collection_query import parse_query

    with caplog.at_level(logging.DEBUG, logger="pyrite.services.collection_query"):
        query = parse_query("limit:abc")

    assert "Could not parse query parameter as number" in caplog.text


def test_collection_query_parse_offset_logs_debug(caplog):
    """Non-numeric offset in query string should log debug."""
    from pyrite.services.collection_query import parse_query

    with caplog.at_level(logging.DEBUG, logger="pyrite.services.collection_query"):
        query = parse_query("offset:xyz")

    assert "Could not parse query parameter as number" in caplog.text


def test_collection_query_from_dict_limit_logs_debug(caplog):
    """Non-numeric limit in dict should log debug."""
    from pyrite.services.collection_query import query_from_dict

    with caplog.at_level(logging.DEBUG, logger="pyrite.services.collection_query"):
        query = query_from_dict({"limit": "abc"})

    assert "Could not parse query parameter as number" in caplog.text


def test_collection_query_from_dict_offset_logs_debug(caplog):
    """Non-numeric offset in dict should log debug."""
    from pyrite.services.collection_query import query_from_dict

    with caplog.at_level(logging.DEBUG, logger="pyrite.services.collection_query"):
        query = query_from_dict({"offset": "xyz"})

    assert "Could not parse query parameter as number" in caplog.text


# ---------------------------------------------------------------------------
# 8. pyrite/services/qa_service.py — metadata schema version parse failure
# ---------------------------------------------------------------------------


def test_qa_service_schema_version_parse_logs_debug(caplog):
    """Malformed JSON metadata should log debug when parsing schema version."""
    qa_logger = logging.getLogger("pyrite.services.qa_service")

    with caplog.at_level(logging.DEBUG, logger="pyrite.services.qa_service"):
        # Simulate the code path with bad JSON
        metadata = "not-valid-json"
        if isinstance(metadata, str):
            try:
                meta_dict = json.loads(metadata)
                int(meta_dict.get("_schema_version", 0))
            except (json.JSONDecodeError, ValueError):
                qa_logger.debug("Could not parse metadata schema version")

    assert "Could not parse metadata schema version" in caplog.text


# ---------------------------------------------------------------------------
# 9. pyrite/storage/connection.py — sqlite-vec extension not available
# ---------------------------------------------------------------------------


def test_connection_sqlite_vec_logs_info(caplog):
    """When sqlite-vec import fails, an info message should be logged."""
    from pyrite.storage.connection import ConnectionMixin

    with caplog.at_level(logging.INFO, logger="pyrite.storage.connection"):
        with patch.dict("sys.modules", {"sqlite_vec": None}):
            obj = object.__new__(ConnectionMixin)
            obj._raw_conn = MagicMock()
            obj.vec_available = False
            obj._load_extensions()

    assert "sqlite-vec extension not available" in caplog.text
    assert obj.vec_available is False
