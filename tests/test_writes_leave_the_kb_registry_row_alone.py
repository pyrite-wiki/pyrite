"""A write leaves a registered KB's registry row alone.

The property: only the operator's registry operations (``seed_from_config``,
``add_kb``, ``update_kb``) change a KB row's ``source``, ``path`` or
``description``. An entry write, a delete or the index sync makes the row
when it is missing and otherwise leaves it as it was, so a ``config.yaml`` KB
stays protected from removal through the registry.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.exceptions import KBProtectedError
from pyrite.services.kb_registry_service import KBRegistryService
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager

KB_NAME = "configured"


def _row(db: PyriteDB) -> tuple:
    from pyrite.storage.models import KB

    db.session.expire_all()
    kb = db.session.get(KB, KB_NAME)
    return (kb.source, kb.path, kb.description)


@pytest.fixture
def env(tmp_path: Path):
    (tmp_path / KB_NAME).mkdir()
    config = PyriteConfig(
        knowledge_bases=[
            KBConfig(
                name=KB_NAME,
                path=tmp_path / KB_NAME,
                kb_type="generic",
                description="the operator's words",
            )
        ],
        settings=Settings(index_path=tmp_path / "index.db"),
    )
    db = PyriteDB(config.settings.index_path)
    KBRegistryService(config, db).seed_from_config()
    svc = KBService(config, db)
    svc.create_entry(KB_NAME, "first", "First", "note", "body")
    # The row as the operator's registry operation wrote it, by hand, so the
    # writes below are measured against what seeding means, not what it does.
    from pyrite.storage.models import KB

    kb = db.session.get(KB, KB_NAME)
    kb.source, kb.path, kb.description = "config", str(tmp_path / KB_NAME), "the operator's words"
    db.session.commit()
    try:
        yield config, db, svc
    finally:
        db.close()


WRITES = {
    "create": lambda config, db, svc: svc.create_entry(KB_NAME, "new", "New", "note", "b"),
    "update": lambda config, db, svc: svc.update_entry("first", KB_NAME, body="changed"),
    "delete": lambda config, db, svc: svc.delete_entry("first", KB_NAME, readable_kbs=None),
    "index sync": lambda config, db, svc: IndexManager(db, config).index_kb(KB_NAME),
}


@pytest.mark.parametrize(
    "write",
    [
        pytest.param(
            w,
            marks=[pytest.mark.control(reason="a delete never registered the KB")]
            if w == "delete"
            else [],
            id=w,
        )
        for w in WRITES
    ],
)
def test_a_write_leaves_the_registry_row_alone(env, write):
    config, db, svc = env
    before = _row(db)
    assert before[0] == "config"
    WRITES[write](config, db, svc)
    assert _row(db) == before
    with pytest.raises(KBProtectedError):
        KBRegistryService(config, db).remove_kb(KB_NAME)


@pytest.mark.control(reason="a write still makes the row of a KB that has none")
def test_a_write_to_a_kb_with_no_row_makes_one(tmp_path):
    (tmp_path / "fresh").mkdir()
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="fresh", path=tmp_path / "fresh", kb_type="generic")],
        settings=Settings(index_path=tmp_path / "index.db"),
    )
    with PyriteDB(config.settings.index_path) as db:
        KBService(config, db).create_entry("fresh", "a", "A", "note", "b")
        from pyrite.storage.models import KB

        assert db.session.get(KB, "fresh") is not None
