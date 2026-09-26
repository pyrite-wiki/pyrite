"""Writes are validated against the KB schema and plugin rules (#14).

`pyrite update -f status=bogus-value` succeeded and wrote the file; so did
`priority=9999`. The rules existed (the software-kb validator declares the
allowed statuses, kinds, priorities and efforts) but only `index health` and
`schema validate` ran them -- after the fact. Detection in three places,
prevention in none. Create and update now refuse a value the schema rejects,
on every surface, and say what was allowed.
"""

import pytest

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.exceptions import ValidationError
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB
from pyrite.services.access_policy import UNSCOPED

KB_YAML = """\
name: t
kb_type: software
validation:
  enforce: true
types:
  backlog_item:
    description: Feature, bug, or tech debt item
    subdirectory: backlog/
"""


@pytest.fixture
def svc(tmp_path):
    pytest.importorskip("pyrite_software_kb")
    kb = tmp_path / "kb"
    (kb / "backlog").mkdir(parents=True)
    (kb / "kb.yaml").write_text(KB_YAML)
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="t", path=kb, kb_type="software")],
        settings=Settings(index_path=tmp_path / "i.db", auto_embed=False),
    )
    db = PyriteDB(config.settings.index_path)
    yield KBService(config, db)
    db.close()


def test_update_refuses_an_off_enum_status_and_names_the_allowed_values(svc, tmp_path):
    svc.create_entry("t", "item", "Item", "backlog_item", "body", status="proposed")
    path = tmp_path / "kb" / "backlog" / "item.md"
    before = path.read_text()
    with pytest.raises(ValidationError) as exc:
        svc.update_entry("item", "t", status="bogus-value")
    msg = str(exc.value)
    assert "status" in msg and "bogus-value" in msg and "proposed" in msg and "done" in msg
    assert path.read_text() == before, "a refused update must not touch the file"


def test_update_refuses_an_off_enum_priority(svc):
    svc.create_entry("t", "item", "Item", "backlog_item", "body")
    with pytest.raises(ValidationError, match="priority"):
        svc.update_entry("item", "t", priority=9999)


def test_create_refuses_an_off_enum_value(svc, tmp_path):
    with pytest.raises(ValidationError, match="kind"):
        svc.create_entry("t", "item", "Item", "backlog_item", "body", kind="wishful")
    assert not (tmp_path / "kb" / "backlog" / "item.md").exists()


def test_valid_values_still_write(svc):
    svc.create_entry("t", "item", "Item", "backlog_item", "body", kind="bug", priority="high")
    svc.update_entry("item", "t", status="done")
    assert svc.get_entry("item", kb_name="t", readable_kbs=UNSCOPED)["status"] == "done"
