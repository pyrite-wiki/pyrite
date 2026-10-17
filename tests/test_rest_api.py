"""
Tests for Pyrite REST API.
"""

import tempfile
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi", reason="fastapi not installed")
from fastapi.testclient import TestClient

import pyrite
from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.models import EventEntry, PersonEntry
from pyrite.server.api import create_app
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager
from pyrite.storage.repository import KBRepository


@pytest.fixture
def test_env():
    """Create test environment with sample data."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        tmpdir = Path(tmpdir)
        db_path = tmpdir / "index.db"

        events_path = tmpdir / "events"
        events_path.mkdir()

        research_path = tmpdir / "research"
        research_path.mkdir()
        (research_path / "actors").mkdir()

        events_kb = KBConfig(
            name="test-events",
            path=events_path,
            kb_type=KBType.EVENTS,
        )

        research_kb = KBConfig(
            name="test-research",
            path=research_path,
            kb_type=KBType.RESEARCH,
        )

        config = PyriteConfig(
            knowledge_bases=[events_kb, research_kb], settings=Settings(index_path=db_path)
        )

        # Create sample entries
        events_repo = KBRepository(events_kb)
        for i in range(3):
            event = EventEntry.create(
                date=f"2025-01-{10 + i:02d}",
                title=f"Test Event {i}",
                body=f"Body for event {i} about immigration policy.",
                importance=5 + i,
            )
            event.tags = ["test", "immigration"]
            event.actors = ["Stephen Miller", "Tom Homan"]
            events_repo.save(event)

        research_repo = KBRepository(research_kb)
        actor = PersonEntry.create(
            name="Stephen Miller", role="Immigration policy architect", importance=9
        )
        actor.body = "Stephen Miller biography."
        actor.tags = ["trump-admin", "immigration"]
        research_repo.save(actor)

        db = PyriteDB(db_path)
        index_mgr = IndexManager(db, config)
        index_mgr.index_all()

        # Create a fresh app for testing (no static files)
        from pyrite.server.api import get_config, get_db, get_index_mgr

        app = create_app(config)
        app.dependency_overrides[get_config] = lambda: config
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_index_mgr] = lambda: index_mgr
        client = TestClient(app)
        try:
            yield {
                "client": client,
                "config": config,
                "db": db,
                "events_kb": events_kb,
                "research_kb": research_kb,
            }
        finally:
            db.close()
            client.close()


class TestCentralExceptionHandler:
    """register_pyrite_exception_handler maps every PyriteError to a clean HTTP
    status + ``{"detail": {"code", "message", "retryable", "hint"?}}`` body,
    instead of leaking a raw 500 traceback.

    ADR-0037 theme 2, decision 1 (maintainer, 2026-09-25): REST keeps the
    ``detail``-wrapped shape (matching what ``HTTPException(detail={...})``
    sites, e.g. ``write_refusal.refusal_http``, already answer) rather than
    the flat ``{"code", "message"}`` this handler used to emit -- so the
    wire shape is the same regardless of which of REST's paths produced it.

    Tested on a minimal app wired with the same registration helper create_app
    uses, so it exercises the real mapping without the full app's static-mount
    and auth routing getting in the way.
    """

    @pytest.fixture
    def error_client(self):
        from fastapi import FastAPI

        from pyrite.exceptions import (
            ConfigError,
            EntryNotFoundError,
            FrontmatterError,
            KBAlreadyExistsError,
            KBNotFoundError,
            KBProtectedError,
            LastAdminError,
            PluginError,
            PyriteError,
            StorageError,
            ValidationError,
        )
        from pyrite.server.api import register_pyrite_exception_handler

        app = FastAPI()
        register_pyrite_exception_handler(app)

        raisers = {
            "entry_not_found": EntryNotFoundError("no entry here"),
            "kb_not_found": KBNotFoundError("no kb here"),
            "protected": KBProtectedError("kb is protected"),
            "validation": ValidationError("bad field"),
            "last_admin": LastAdminError("cannot demote the last admin"),
            "frontmatter": FrontmatterError("bad yaml"),
            "config": ConfigError("dup kb"),
            "kb_already_exists": KBAlreadyExistsError("KB 'x' already exists"),
            "plugin": PluginError("missing sdk"),
            "storage": StorageError("disk gone"),
            "base": PyriteError("generic domain error"),
        }
        for name, exc in raisers.items():

            def _route(_exc=exc):
                raise _exc

            app.add_api_route(f"/probe/{name}", _route, methods=["GET"])
        # raise_server_exceptions=False so unhandled cases surface as responses;
        # our handler should mean none are actually unhandled.
        return TestClient(app, raise_server_exceptions=False)

    def test_conflict_code_has_its_own_status_by_code_row_not_just_the_fallback(self):
        """#509 round 1: KBAlreadyExistsError's CONFLICT code answered 409
        only via _BASE_CLASS_FALLBACK's ConfigError row (a class whose code
        isn't in _STATUS_BY_CODE falls back to its base's status) -- correct
        today, since ConfigError's own fallback is also 409, but coincidental:
        nothing pinned CONFLICT's status to the table itself. A future
        CONFLICT-coded class that DIDN'T inherit ConfigError (or a change to
        ConfigError's fallback status) would silently answer a different
        code from a table lookup that was never actually populated for it."""
        from pyrite.server.errors import _STATUS_BY_CODE

        assert _STATUS_BY_CODE.get("CONFLICT") == 409

    @pytest.mark.parametrize(
        ("name", "status", "code"),
        [
            ("entry_not_found", 404, "ENTRY_NOT_FOUND"),
            ("kb_not_found", 404, "KB_NOT_FOUND"),
            ("protected", 403, "KB_PROTECTED"),
            ("frontmatter", 422, "INVALID_FRONTMATTER"),
            ("validation", 422, "VALIDATION_FAILED"),
            ("last_admin", 409, "LAST_ADMIN"),
            ("config", 409, "CONFIG_CONFLICT"),
            ("kb_already_exists", 409, "CONFLICT"),
            ("plugin", 502, "PLUGIN_ERROR"),
            ("storage", 500, "STORAGE_ERROR"),
            ("base", 500, "INTERNAL_ERROR"),
        ],
    )
    def test_domain_error_maps_to_status_and_shape(self, error_client, name, status, code):
        resp = error_client.get(f"/probe/{name}")
        assert resp.status_code == status
        body = resp.json()
        detail = body["detail"]
        assert detail["code"] == code
        assert isinstance(detail["message"], str) and detail["message"]
        assert detail["retryable"] is False
        # No traceback / internals leaked
        assert "Traceback" not in detail["message"]

    def test_an_unlisted_validation_subclass_falls_back_to_the_base_status(self):
        """Item 6 (conductor cold read of #501): a future ValidationError
        subclass that narrows its own error_code, without anyone adding a row
        to server/errors.py's _STATUS_BY_CODE, must still answer 422 like its
        base -- not a silent 500. Exercises a throwaway subclass, not one of
        the real ones (which do have rows, for speed and clarity)."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from pyrite.exceptions import ValidationError
        from pyrite.server.api import register_pyrite_exception_handler

        class _HypotheticalFutureValidationError(ValidationError):
            error_code = "SOME_CODE_NOBODY_ADDED_TO_THE_TABLE_YET"

        app = FastAPI()
        register_pyrite_exception_handler(app)

        def _route():
            raise _HypotheticalFutureValidationError("not in the table")

        app.add_api_route("/probe/unlisted-validation", _route, methods=["GET"])
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/probe/unlisted-validation")
        assert resp.status_code == 422, resp.json()
        assert resp.json()["detail"]["code"] == "SOME_CODE_NOBODY_ADDED_TO_THE_TABLE_YET"

    def test_an_unlisted_config_subclass_falls_back_to_the_base_status(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from pyrite.exceptions import ConfigError
        from pyrite.server.api import register_pyrite_exception_handler

        class _HypotheticalFutureConfigError(ConfigError):
            error_code = "ANOTHER_CODE_NOBODY_ADDED"

        app = FastAPI()
        register_pyrite_exception_handler(app)

        def _route():
            raise _HypotheticalFutureConfigError("not in the table")

        app.add_api_route("/probe/unlisted-config", _route, methods=["GET"])
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/probe/unlisted-config")
        assert resp.status_code == 409, resp.json()

    def _fallback_probe(self, base, code):
        """A throwaway subclass of ``base`` with a code no one added to
        ``_STATUS_BY_CODE``, run through the real registered handler."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from pyrite.server.api import register_pyrite_exception_handler

        subclass = type(f"_Unlisted{base.__name__}", (base,), {"error_code": code})

        app = FastAPI()
        register_pyrite_exception_handler(app)

        def _route():
            raise subclass("not in the table")

        app.add_api_route("/probe/fallback", _route, methods=["GET"])
        return TestClient(app, raise_server_exceptions=False).get("/probe/fallback")

    def test_an_unlisted_plugin_error_subclass_falls_back_to_the_base_status(self):
        """Item 2 (conductor cold read of 5d65caa7): _BASE_CLASS_FALLBACK
        only covered ValidationError/ConfigError/StorageError. A future
        PluginError subclass with its own code needs the same safety net."""
        from pyrite.exceptions import PluginError

        resp = self._fallback_probe(PluginError, "SOME_PLUGIN_CODE_NOBODY_ADDED")
        assert resp.status_code == 502, resp.json()

    def test_an_unlisted_entry_not_found_subclass_falls_back_to_the_base_status(self):
        from pyrite.exceptions import EntryNotFoundError

        resp = self._fallback_probe(EntryNotFoundError, "SOME_ENTRY_CODE_NOBODY_ADDED")
        assert resp.status_code == 404, resp.json()

    def test_an_unlisted_kb_not_found_subclass_falls_back_to_the_base_status(self):
        from pyrite.exceptions import KBNotFoundError

        resp = self._fallback_probe(KBNotFoundError, "SOME_KB_CODE_NOBODY_ADDED")
        assert resp.status_code == 404, resp.json()

    def test_an_unlisted_kb_read_only_subclass_falls_back_to_the_base_status(self):
        from pyrite.exceptions import KBReadOnlyError

        resp = self._fallback_probe(KBReadOnlyError, "SOME_READ_ONLY_CODE_NOBODY_ADDED")
        assert resp.status_code == 403, resp.json()

    def test_an_unlisted_kb_protected_subclass_falls_back_to_the_base_status(self):
        from pyrite.exceptions import KBProtectedError

        resp = self._fallback_probe(KBProtectedError, "SOME_PROTECTED_CODE_NOBODY_ADDED")
        assert resp.status_code == 403, resp.json()

    def test_a_class_inheriting_two_base_class_fallback_bases_takes_the_first_in_tuple_order(
        self,
    ):
        """#506 item 4: `_BASE_CLASS_FALLBACK` is a tuple walked in order, not
        the class's MRO -- for a class inheriting from two of its eight
        listed bases, which one wins is a decision, not an accident. Today's
        order lists `ValidationError` (422) before `ConfigError` (409), so a
        class inheriting both -- and whose own code is not in
        `_STATUS_BY_CODE` -- resolves to `ValidationError`'s 422, not
        `ConfigError`'s 409. Pinned here so re-ordering the tuple is a
        reviewed change to this test, not a silent behaviour flip."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from pyrite.exceptions import ConfigError, ValidationError
        from pyrite.server.api import register_pyrite_exception_handler

        class _BothBasesError(ValidationError, ConfigError):
            error_code = "SOME_DUAL_BASE_CODE_NOBODY_ADDED"

        app = FastAPI()
        register_pyrite_exception_handler(app)

        def _route():
            raise _BothBasesError("not in the table")

        app.add_api_route("/probe/dual-base", _route, methods=["GET"])
        resp = TestClient(app, raise_server_exceptions=False).get("/probe/dual-base")
        assert resp.status_code == 422, resp.json()

    def test_body_has_no_top_level_code_or_message(self, error_client):
        """The old flat shape is gone: everything lives under detail."""
        resp = error_client.get("/probe/entry_not_found")
        body = resp.json()
        assert set(body) == {"detail"}
        assert set(body["detail"]) >= {"code", "message", "retryable"}

    def test_a_retryable_storage_error_says_so(self, error_client):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from pyrite.exceptions import StorageBusyError
        from pyrite.server.api import register_pyrite_exception_handler

        app = FastAPI()
        register_pyrite_exception_handler(app)

        def _route():
            raise StorageBusyError("database is locked")

        app.add_api_route("/probe/busy", _route, methods=["GET"])
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/probe/busy")
        assert resp.status_code == 500
        assert resp.json()["detail"]["retryable"] is True

    def test_public_message_replaces_str_exc_when_set(self, error_client):
        """A class-level public_message (ConfigSaveRefusedError, #377) is
        what the caller sees; str(exc)'s operator detail never reaches the
        response body."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from pyrite.exceptions import ConfigSaveRefusedError
        from pyrite.server.api import register_pyrite_exception_handler

        app = FastAPI()
        register_pyrite_exception_handler(app)

        def _route():
            raise ConfigSaveRefusedError("/real/secret/path.yaml leaked here", dropped=["x"])

        app.add_api_route("/probe/config_save", _route, methods=["GET"])
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/probe/config_save")
        detail = resp.json()["detail"]
        assert detail["code"] == "CONFIG_SAVE_REFUSED"
        assert "/real/secret/path.yaml" not in detail["message"]

    def test_a_suggestion_becomes_hint(self, error_client):
        """ValidationError.suggestion, when set, appears as detail.hint --
        matching write_refusal.refusal_http's own key name."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from pyrite.exceptions import ValidationError
        from pyrite.server.api import register_pyrite_exception_handler

        app = FastAPI()
        register_pyrite_exception_handler(app)

        def _route():
            exc = ValidationError("bad field")
            exc.suggestion = "try again with a valid field"
            raise exc

        app.add_api_route("/probe/hinted", _route, methods=["GET"])
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/probe/hinted")
        assert resp.json()["detail"]["hint"] == "try again with a valid field"

    def test_no_hint_key_when_no_suggestion(self, error_client):
        resp = error_client.get("/probe/entry_not_found")
        assert "hint" not in resp.json()["detail"]


@pytest.mark.core
class TestKBEndpoints:
    """Test KB listing endpoint."""

    def test_list_kbs(self, test_env):
        client = test_env["client"]
        response = client.get("/api/kbs")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert len(data["kbs"]) == 2

    def test_health_check(self, test_env):
        client = test_env["client"]
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

    def test_openapi_version_matches_package(self, test_env):
        response = test_env["client"].get("/openapi.json")
        assert response.status_code == 200
        assert response.json()["info"]["version"] == pyrite.__version__


class TestSearchEndpoints:
    """Test search functionality."""

    def test_basic_search(self, test_env):
        client = test_env["client"]
        response = client.get("/api/search?q=immigration")
        assert response.status_code == 200
        data = response.json()
        assert data["query"] == "immigration"
        assert data["count"] >= 1

    def test_search_with_kb_filter(self, test_env):
        client = test_env["client"]
        response = client.get("/api/search?q=Test&kb=test-events")
        assert response.status_code == 200
        data = response.json()
        # Should only return events
        for result in data["results"]:
            assert result["kb_name"] == "test-events"

    def test_search_with_limit(self, test_env):
        client = test_env["client"]
        response = client.get("/api/search?q=Test&limit=2")
        assert response.status_code == 200
        data = response.json()
        assert len(data["results"]) <= 2

    def test_search_with_fields_keeps_identity_fields(self, test_env):
        client = test_env["client"]
        response = client.get("/api/search?q=immigration&fields=title")

        assert response.status_code == 200
        results = response.json()["results"]
        assert results
        for result in results:
            assert set(result) == {"id", "kb_name", "title"}

    def test_search_with_unknown_field_returns_identity_fields(self, test_env):
        client = test_env["client"]
        response = client.get("/api/search?q=immigration&fields=nope")

        assert response.status_code == 200
        results = response.json()["results"]
        assert results
        for result in results:
            assert set(result) == {"id", "kb_name"}


@pytest.mark.core
class TestEntryEndpoints:
    """Test entry CRUD operations."""

    def test_get_entry_not_found(self, test_env):
        client = test_env["client"]
        response = client.get("/api/entries/nonexistent-entry")
        assert response.status_code == 404
        data = response.json()
        assert data["detail"]["code"] == "NOT_FOUND"

    def test_get_entry(self, test_env):
        client = test_env["client"]
        # First search to find an entry
        search_response = client.get("/api/search?q=Stephen+Miller&kb=test-research")
        if search_response.json()["count"] > 0:
            entry_id = search_response.json()["results"][0]["id"]
            response = client.get(f"/api/entries/{entry_id}?kb=test-research")
            assert response.status_code == 200
            data = response.json()
            assert "title" in data
            assert "body" in data

    def test_get_entry_with_fields_keeps_identity_fields(self, test_env):
        client = test_env["client"]
        entry_id = client.get("/api/search?q=Stephen+Miller&kb=test-research").json()["results"][0][
            "id"
        ]

        response = client.get(f"/api/entries/{entry_id}?kb=test-research&fields=title")

        assert response.status_code == 200
        assert set(response.json()) == {"id", "kb_name", "title"}

    def test_list_entries(self, test_env):
        client = test_env["client"]
        response = client.get("/api/entries?kb=test-events&limit=10")
        assert response.status_code == 200
        data = response.json()
        assert "entries" in data
        assert "total" in data
        assert data["total"] >= 1
        assert len(data["entries"]) <= 10

    def test_create_entry_json(self, test_env):
        client = test_env["client"]
        response = client.post(
            "/api/entries",
            json={
                "kb": "test-events",
                "entry_type": "event",
                "title": "New API Event",
                "body": "Created via JSON body",
                "date": "2025-06-01",
                "tags": ["api-test"],
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["created"] is True
        assert "id" in data

    def test_update_entry_json(self, test_env):
        client = test_env["client"]
        # Create then update
        create_resp = client.post(
            "/api/entries",
            json={
                "kb": "test-events",
                "entry_type": "event",
                "title": "Update Test Event",
                "body": "Original body",
                "date": "2025-07-01",
            },
        )
        entry_id = create_resp.json()["id"]
        response = client.put(
            f"/api/entries/{entry_id}",
            json={"kb": "test-events", "body": "Updated body"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["updated"] is True


class TestTimelineEndpoints:
    """Test timeline queries."""

    def test_timeline_basic(self, test_env):
        client = test_env["client"]
        response = client.get("/api/timeline?date_from=2025-01-01&date_to=2025-12-31")
        assert response.status_code == 200
        data = response.json()
        assert "events" in data
        assert "count" in data

    def test_timeline_with_limit(self, test_env):
        client = test_env["client"]
        response = client.get("/api/timeline?limit=2")
        assert response.status_code == 200
        data = response.json()
        assert len(data["events"]) <= 2


class TestTagsAndActors:
    """Test tags and actors endpoints."""

    def test_get_tags(self, test_env):
        client = test_env["client"]
        response = client.get("/api/tags")
        assert response.status_code == 200
        data = response.json()
        assert "tags" in data
        assert "count" in data


class TestAdminEndpoints:
    """Test admin endpoints."""

    def test_get_stats(self, test_env):
        client = test_env["client"]
        response = client.get("/api/stats")
        assert response.status_code == 200
        data = response.json()
        assert "total_entries" in data


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
