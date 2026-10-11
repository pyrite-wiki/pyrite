"""Omission and explicit null are different KB metadata updates."""

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
        ({"kb_type": None}, "Kept", "generic"),
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
