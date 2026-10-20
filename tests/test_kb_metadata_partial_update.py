"""Omission and explicit null are different KB metadata updates.

Omitted fields stay as they are; ``description: null`` clears the description;
``kb_type: null`` is refused, because a KB always has a type.
"""

import pytest

from pyrite.storage.models import KB


def _seeded_world(make_client, tmp_path):
    """The REST world with one registry KB, ``notes``, of type ``task``."""
    client, _config, db = make_client()
    path = tmp_path / "notes"
    path.mkdir()
    db.register_kb(name="notes", path=str(path), kb_type="task", description="Kept")
    return client, db


def _row(db):
    db.session.expire_all()
    row = db.session.get(KB, "notes")
    return row.description, row.kb_type


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
    make_client, tmp_path, body, expected_description, expected_type
):
    client, db = _seeded_world(make_client, tmp_path)
    response = client.put("/api/kbs/notes", **({"json": body} if body is not None else {}))
    assert response.status_code == 200, response.text
    assert _row(db) == (expected_description, expected_type)


@pytest.mark.parametrize(
    "body",
    [{"kb_type": None}, {"description": "Changed", "kb_type": None}],
)
def test_rest_update_refuses_an_explicit_null_type_and_changes_nothing(make_client, tmp_path, body):
    """A KB's type cannot be cleared: it is refused, naming the field, and the
    same request's other fields are not applied either."""
    client, db = _seeded_world(make_client, tmp_path)
    response = client.put("/api/kbs/notes", json=body)
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "VALIDATION_FAILED"
    assert "kb_type" in detail["message"]
    assert "cannot be cleared" in detail["message"]
    assert _row(db) == ("Kept", "task")
