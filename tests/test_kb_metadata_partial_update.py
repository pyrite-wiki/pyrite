"""Omission and explicit null are different KB metadata updates.

Omitted fields stay as they are; ``description: null`` clears the description.
Every field the caller sends is applied, or the whole request is refused with
``VALIDATION_FAILED`` and nothing is written: a ``kb_type`` that is null or blank
(a KB always has a type), a key the route does not take (a typo, or
``default_role``, which has its own route), a value of the wrong type, or a body
that is not an object.
"""

import pytest

from pyrite.exceptions import ValidationError
from pyrite.services.kb_registry_service import KBRegistryService
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
    [
        {"kb_type": None},
        {"kb_type": ""},
        {"kb_type": "   "},
        {"description": "Changed", "kb_type": None},
        {"description": "Changed", "kb_type": ""},
    ],
)
def test_rest_update_refuses_a_null_or_blank_type_and_changes_nothing(make_client, tmp_path, body):
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


@pytest.mark.parametrize(
    ("body", "named"),
    [
        ({"desciption": "typo"}, ["desciption"]),
        ({"description": "Changed", "desciption": "typo", "colour": 1}, ["colour", "desciption"]),
        ({"default_role": "write"}, ["default_role"]),
        ({"description": "Changed", "default_role": "write"}, ["default_role"]),
    ],
)
def test_rest_update_refuses_keys_the_route_does_not_take_and_changes_nothing(
    make_client, tmp_path, body, named
):
    """An unknown key is named in a VALIDATION_FAILED refusal, not dropped with
    ``updated: true``; the same request's valid fields are not applied."""
    client, db = _seeded_world(make_client, tmp_path)
    response = client.put("/api/kbs/notes", json=body)
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "VALIDATION_FAILED"
    assert all(f"'{key}'" in detail["message"] for key in named), detail["message"]
    assert _row(db) == ("Kept", "task")


@pytest.mark.parametrize(
    ("body", "named"),
    [
        ({"kb_type": 5}, "kb_type"),
        ({"description": 3}, "description"),
        ({"description": ["a"]}, "description"),
        ([1], "body"),
        ("text", "body"),
    ],
)
def test_rest_update_refuses_a_wrong_shape_in_the_documented_error_shape(
    make_client, tmp_path, body, named
):
    """A wrong-typed value or a body that is not an object is refused as
    VALIDATION_FAILED, not as FastAPI's list-shaped 422, and writes nothing."""
    client, db = _seeded_world(make_client, tmp_path)
    response = client.put("/api/kbs/notes", json=body)
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert isinstance(detail, dict), detail
    assert detail["code"] == "VALIDATION_FAILED"
    assert named in detail["message"]
    assert _row(db) == ("Kept", "task")


@pytest.mark.parametrize(
    "updates",
    [{"kb_type": None}, {"kb_type": ""}, {"kb_type": " "}, {"desciption": "typo"}],
)
def test_the_registry_service_refuses_a_bad_update_too(make_client, tmp_path, updates):
    """The rule lives in ``KBRegistryService.update_kb`` too, so a caller other
    than the REST route gets it: a refusal, and the row is untouched."""
    _client, config, db = make_client()
    path = tmp_path / "notes"
    path.mkdir()
    db.register_kb(name="notes", path=str(path), kb_type="task", description="Kept")
    with pytest.raises(ValidationError) as refused:
        KBRegistryService(config, db).update_kb("notes", description="Changed", **updates)
    assert refused.value.error_code == "VALIDATION_FAILED"
    assert _row(db) == ("Kept", "task")


def test_rest_refuses_a_metadata_edit_on_a_hand_defined_config_kb(make_client, tmp_path):
    """config.yaml, not the registry row, is the source of truth (#1116).

    Every entry save and sync rewrites a config-defined KB's row from the
    file, so an accepted description edit vanished on the next reindex. The
    route refuses it with the code the default_role route already uses.
    """
    from pyrite.config import KBConfig, save_config

    client, config, db = make_client()
    path = tmp_path / "hand"
    path.mkdir()
    db.register_kb(name="hand", path=str(path), kb_type="generic", description="From file")
    config.add_kb(KBConfig(name="hand", path=path, kb_type="generic", description="From file"))
    save_config(config)

    response = client.put("/api/kbs/hand", json={"description": "Changed"})

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "KB_DEFINED_IN_CONFIG"
    assert "config.yaml" in detail["message"]
    assert "description" in detail["message"]
    db.session.expire_all()
    row = db.session.get(KB, "hand")
    assert row is not None and row.description == "From file"
