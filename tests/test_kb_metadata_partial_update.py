"""Omission and explicit null are different KB metadata updates.

Omitted fields stay as they are; ``description: null`` clears the description;
``kb_type: null`` is refused, because a KB always has a type."""

import pytest
from fastapi.testclient import TestClient

from pyrite.config import PyriteConfig, Settings
from pyrite.server import api
from pyrite.server.api import create_app
from pyrite.storage.database import PyriteDB
from pyrite.storage.models import KB


@pytest.mark.parametrize(
    ("body", "expected_description", "expected_type"),
    [
        ({"description": "Changed"}, "Changed", "task"),
        ({"kb_type": "generic"}, "Kept", "generic"),
        ({"description": None}, None, "task"),
        ({}, "Kept", "task"),
        (None, "Kept", "task"),
    ],
)
def test_rest_update_only_changes_fields_present(
    tmp_path, body, expected_description, expected_type
):
    path = tmp_path / "notes"
    path.mkdir()
    config = PyriteConfig(settings=Settings(index_path=tmp_path / "index.db", auto_embed=False))
    db = PyriteDB(config.settings.index_path)
    db.register_kb(name="notes", path=str(path), kb_type="task", description="Kept")
    application = create_app(config=config)
    application.dependency_overrides[api.get_db] = lambda: db
    try:
        response = TestClient(application).put(
            "/api/kbs/notes", **({"json": body} if body is not None else {})
        )
        assert response.status_code == 200, response.text
        db.session.expire_all()
        row = db.session.get(KB, "notes")
        assert row.description == expected_description
        assert row.kb_type == expected_type
    finally:
        db.close()


@pytest.mark.parametrize(
    "body",
    [{"kb_type": None}, {"description": "Changed", "kb_type": None}],
)
def test_rest_update_refuses_an_explicit_null_type_and_changes_nothing(tmp_path, body):
    """A KB's type cannot be cleared: it is refused, naming the field, and the
    same request's other fields are not applied either."""
    path = tmp_path / "notes"
    path.mkdir()
    config = PyriteConfig(settings=Settings(index_path=tmp_path / "index.db", auto_embed=False))
    db = PyriteDB(config.settings.index_path)
    db.register_kb(name="notes", path=str(path), kb_type="task", description="Kept")
    application = create_app(config=config)
    application.dependency_overrides[api.get_db] = lambda: db
    try:
        response = TestClient(application).put("/api/kbs/notes", json=body)
        assert response.status_code == 422, response.text
        detail = response.json()["detail"]
        assert detail["code"] == "VALIDATION_FAILED"
        assert "kb_type" in detail["message"]
        assert "cannot be cleared" in detail["message"]
        db.session.expire_all()
        row = db.session.get(KB, "notes")
        assert (row.description, row.kb_type) == ("Kept", "task")
    finally:
        db.close()
