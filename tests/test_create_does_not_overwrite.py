"""Creating an entry must never silently replace an existing one.

`pyrite create --title "Same Title"` twice printed "Created" twice and left only
the second body on disk. The id is derived from the title, so two entries that
happen to share a title is an ordinary event, not an edge case -- and the first
one was simply gone. The guard belongs in the service so the CLI, REST, MCP and
the importers all get it.
"""

import pytest

from pyrite.exceptions import ValidationError
from pyrite.services.access_policy import UNSCOPED


@pytest.fixture
def kb_name(kb_configs):
    return next(c.name for c in kb_configs.values() if not c.read_only)


def test_second_create_with_same_id_is_refused(kb_service, kb_name):
    kb_service.create_entry(kb_name, "same-title", "Same Title", "note", "first body")
    with pytest.raises(ValidationError, match="already exists"):
        kb_service.create_entry(kb_name, "same-title", "Same Title", "note", "second body")
    assert (
        "first body"
        in kb_service.get_entry("same-title", kb_name=kb_name, readable_kbs=UNSCOPED)["body"]
    )


def test_collision_is_detected_across_entry_types(kb_service, kb_name):
    # One id namespace per KB: a person must not clobber a note of the same id.
    kb_service.create_entry(kb_name, "shared-id", "Shared", "note", "the note")
    with pytest.raises(ValidationError, match="already exists"):
        kb_service.create_entry(kb_name, "shared-id", "Shared", "person", "the person")
    assert (
        "the note"
        in kb_service.get_entry("shared-id", kb_name=kb_name, readable_kbs=UNSCOPED)["body"]
    )


def test_update_still_replaces_content(kb_service, kb_name):
    kb_service.create_entry(kb_name, "editable", "Editable", "note", "v1")
    kb_service.update_entry("editable", kb_name, body="v2")
    assert "v2" in kb_service.get_entry("editable", kb_name=kb_name, readable_kbs=UNSCOPED)["body"]
