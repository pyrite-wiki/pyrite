"""Tests for incremental link sync optimization."""

import pytest

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.storage.database import PyriteDB
from pyrite.services.access_policy import UNSCOPED
from pyrite.services.kb_service import KBService
from pyrite.storage.models import Link
from pyrite.storage.repository import KBRepository


@pytest.fixture
def db_with_entry(tmp_path):
    """DB with a single entry for link testing."""
    kb_path = tmp_path / "test-kb"
    kb_path.mkdir()
    kb = KBConfig(name="test", path=kb_path, kb_type="standard")
    config = PyriteConfig(
        knowledge_bases=[kb],
        settings=Settings(index_path=tmp_path / "index.db"),
    )
    db = PyriteDB(tmp_path / "index.db")
    db.register_kb("test", "standard", str(kb_path))

    # Create source and target entries
    db.upsert_entry(
        {
            "id": "source",
            "kb_name": "test",
            "title": "Source Entry",
            "entry_type": "note",
        }
    )
    for i in range(5):
        db.upsert_entry(
            {
                "id": f"target-{i}",
                "kb_name": "test",
                "title": f"Target {i}",
                "entry_type": "note",
            }
        )

    yield db
    db.close()


@pytest.fixture
def hand_link_env(tmp_path):
    kb_path = tmp_path / "notes"
    kb_path.mkdir()
    kb = KBConfig(name="hand-links", path=kb_path, kb_type="standard")
    config = PyriteConfig(knowledge_bases=[kb], settings=Settings(index_path=tmp_path / "index.db"))
    db = PyriteDB(config.settings.index_path)
    yield db, kb, KBService(config, db)
    db.close()


def _write_hand_links(kb, targets, body="Original body", relation="related_to"):
    links = "".join(f"  - target: {target}\n    relation: {relation}\n" for target in targets)
    path = kb.path / "source.md"
    path.write_text(
        f"---\nid: source\ntitle: Source\ntype: note\nlinks:\n{links}---\n{body}\n",
        encoding="utf-8",
    )
    return path


def _stored_links(db, kb):
    return db.session.query(Link).filter_by(source_id="source", source_kb=kb.name).all()


@pytest.mark.parametrize(
    ("target", "stored"),
    [
        ("true", "1"),
        ("2024", "2024"),
        ("2024-01-01", "2024-01-01"),
        ("1.5", "1.5"),
        ("1.2345678901234567", "1.23456789012346"),
    ],
)
@pytest.mark.parametrize("with_string_twin", [False, True])
def test_hand_link_sync_is_idempotent(hand_link_env, target, stored, with_string_twin):
    db, kb, service = hand_link_env
    targets = [target, f"'{stored}'"] if with_string_twin else [target]
    path = _write_hand_links(kb, targets)

    service.sync_index(kb.name)
    first = _stored_links(db, kb)
    assert [(row.target_id, row.relation) for row in first] == [(stored, "related_to")]
    row_id = first[0].id

    _write_hand_links(kb, targets, body="Hand-edited body")
    service.sync_index(kb.name)
    frontmatter = path.read_text(encoding="utf-8").split("---", 2)[1]
    service.update_entry("source", kb.name, body="Updated through the service")

    rows = _stored_links(db, kb)
    assert [(row.id, row.target_id, row.relation) for row in rows] == [
        (row_id, stored, "related_to")
    ]
    updated_path = KBRepository(kb).find_file("source")
    assert updated_path is not None
    assert updated_path.read_text(encoding="utf-8").split("---", 2)[1] == frontmatter


def test_non_string_link_relation_change_and_removal(hand_link_env):
    db, kb, service = hand_link_env
    path = _write_hand_links(kb, ["2024", "'2024'"])
    service.sync_index(kb.name)

    _write_hand_links(kb, ["2024"], relation="depends_on")
    service.sync_index(kb.name)
    assert [(row.target_id, row.relation) for row in _stored_links(db, kb)] == [
        ("2024", "depends_on")
    ]

    path.write_text(
        "---\nid: source\ntitle: Source\ntype: note\nlinks: []\n---\nBody\n", encoding="utf-8"
    )
    service.sync_index(kb.name)
    assert _stored_links(db, kb) == []


def test_sync_removes_preexisting_duplicate_link_rows(hand_link_env):
    db, kb, service = hand_link_env
    _write_hand_links(kb, ["2024"])
    service.sync_index(kb.name)
    row = _stored_links(db, kb)[0]
    db.session.add(
        Link(
            source_id="source",
            source_kb=kb.name,
            target_id=row.target_id,
            target_kb=kb.name,
            relation=row.relation,
            inverse_relation=row.inverse_relation,
        )
    )
    db.session.commit()
    assert len(_stored_links(db, kb)) == 2

    service.update_entry("source", kb.name, body="Reindex the existing links")
    rows = _stored_links(db, kb)
    assert [(link.id, link.target_id) for link in rows] == [(row.id, "2024")]


class TestIncrementalLinkSync:
    """Test that _sync_links correctly handles add/remove/update diffs."""

    def test_add_new_link(self, db_with_entry):
        """Adding a link to an entry with no links."""
        db_with_entry.upsert_entry(
            {
                "id": "source",
                "kb_name": "test",
                "title": "Source Entry",
                "entry_type": "note",
                "links": [{"target": "target-0", "relation": "related_to", "kb": "test"}],
            }
        )
        outlinks = db_with_entry.get_outlinks("source", "test", readable_kbs=UNSCOPED)
        assert len(outlinks) == 1
        assert outlinks[0]["id"] == "target-0"

    def test_add_second_link_preserves_first(self, db_with_entry):
        """Adding a second link keeps the first."""
        # First save with 1 link
        db_with_entry.upsert_entry(
            {
                "id": "source",
                "kb_name": "test",
                "title": "Source Entry",
                "entry_type": "note",
                "links": [{"target": "target-0", "relation": "related_to", "kb": "test"}],
            }
        )
        # Second save with 2 links
        db_with_entry.upsert_entry(
            {
                "id": "source",
                "kb_name": "test",
                "title": "Source Entry",
                "entry_type": "note",
                "links": [
                    {"target": "target-0", "relation": "related_to", "kb": "test"},
                    {"target": "target-1", "relation": "related_to", "kb": "test"},
                ],
            }
        )
        outlinks = db_with_entry.get_outlinks("source", "test", readable_kbs=UNSCOPED)
        assert len(outlinks) == 2
        targets = {l["id"] for l in outlinks}
        assert targets == {"target-0", "target-1"}

    def test_remove_link(self, db_with_entry):
        """Removing a link from the list removes it from DB."""
        # Save with 2 links
        db_with_entry.upsert_entry(
            {
                "id": "source",
                "kb_name": "test",
                "title": "Source Entry",
                "entry_type": "note",
                "links": [
                    {"target": "target-0", "relation": "related_to", "kb": "test"},
                    {"target": "target-1", "relation": "related_to", "kb": "test"},
                ],
            }
        )
        # Save with 1 link (removed target-1)
        db_with_entry.upsert_entry(
            {
                "id": "source",
                "kb_name": "test",
                "title": "Source Entry",
                "entry_type": "note",
                "links": [
                    {"target": "target-0", "relation": "related_to", "kb": "test"},
                ],
            }
        )
        outlinks = db_with_entry.get_outlinks("source", "test", readable_kbs=UNSCOPED)
        assert len(outlinks) == 1
        assert outlinks[0]["id"] == "target-0"

    def test_change_relation(self, db_with_entry):
        """Changing a link's relation updates it."""
        db_with_entry.upsert_entry(
            {
                "id": "source",
                "kb_name": "test",
                "title": "Source Entry",
                "entry_type": "note",
                "links": [{"target": "target-0", "relation": "related_to", "kb": "test"}],
            }
        )
        db_with_entry.upsert_entry(
            {
                "id": "source",
                "kb_name": "test",
                "title": "Source Entry",
                "entry_type": "note",
                "links": [{"target": "target-0", "relation": "depends_on", "kb": "test"}],
            }
        )
        outlinks = db_with_entry.get_outlinks("source", "test", readable_kbs=UNSCOPED)
        assert len(outlinks) == 1
        assert outlinks[0]["relation"] == "depends_on"

    def test_empty_links_clears_all(self, db_with_entry):
        """Empty link list removes all links."""
        db_with_entry.upsert_entry(
            {
                "id": "source",
                "kb_name": "test",
                "title": "Source Entry",
                "entry_type": "note",
                "links": [
                    {"target": "target-0", "relation": "related_to", "kb": "test"},
                    {"target": "target-1", "relation": "related_to", "kb": "test"},
                ],
            }
        )
        db_with_entry.upsert_entry(
            {
                "id": "source",
                "kb_name": "test",
                "title": "Source Entry",
                "entry_type": "note",
                "links": [],
            }
        )
        outlinks = db_with_entry.get_outlinks("source", "test", readable_kbs=UNSCOPED)
        assert len(outlinks) == 0

    def test_no_change_is_idempotent(self, db_with_entry):
        """Re-saving identical links doesn't create duplicates."""
        links = [
            {"target": "target-0", "relation": "related_to", "kb": "test"},
            {"target": "target-1", "relation": "depends_on", "kb": "test"},
        ]
        for _ in range(3):
            db_with_entry.upsert_entry(
                {
                    "id": "source",
                    "kb_name": "test",
                    "title": "Source Entry",
                    "entry_type": "note",
                    "links": links,
                }
            )
        outlinks = db_with_entry.get_outlinks("source", "test", readable_kbs=UNSCOPED)
        assert len(outlinks) == 2

    def test_many_links_add_one(self, db_with_entry):
        """Adding 1 link to an entry with many existing links."""
        # Create 50 target entries
        for i in range(5, 55):
            db_with_entry.upsert_entry(
                {
                    "id": f"target-{i}",
                    "kb_name": "test",
                    "title": f"Target {i}",
                    "entry_type": "note",
                }
            )

        # Save with 50 links
        links = [
            {"target": f"target-{i}", "relation": "related_to", "kb": "test"} for i in range(50)
        ]
        db_with_entry.upsert_entry(
            {
                "id": "source",
                "kb_name": "test",
                "title": "Source Entry",
                "entry_type": "note",
                "links": links,
            }
        )

        # Add 1 more link
        links.append({"target": "target-50", "relation": "related_to", "kb": "test"})
        db_with_entry.upsert_entry(
            {
                "id": "source",
                "kb_name": "test",
                "title": "Source Entry",
                "entry_type": "note",
                "links": links,
            }
        )

        outlinks = db_with_entry.get_outlinks("source", "test", readable_kbs=UNSCOPED)
        assert len(outlinks) == 51

    def test_inverse_relation_set(self, db_with_entry):
        """Inverse relation should be computed for new links."""
        db_with_entry.upsert_entry(
            {
                "id": "source",
                "kb_name": "test",
                "title": "Source Entry",
                "entry_type": "note",
                "links": [{"target": "target-0", "relation": "depends_on", "kb": "test"}],
            }
        )
        backlinks = db_with_entry.get_backlinks("target-0", "test", readable_kbs=UNSCOPED)
        assert len(backlinks) == 1
        # Backlink should use inverse relation
        assert backlinks[0]["relation"] in ("depended_on_by", "blocks", "dependency_of")

    def test_note_preserved_on_unchanged_link(self, db_with_entry):
        """Link notes should be preserved when link is unchanged."""
        db_with_entry.upsert_entry(
            {
                "id": "source",
                "kb_name": "test",
                "title": "Source Entry",
                "entry_type": "note",
                "links": [
                    {
                        "target": "target-0",
                        "relation": "related_to",
                        "kb": "test",
                        "note": "important",
                    },
                    {"target": "target-1", "relation": "related_to", "kb": "test"},
                ],
            }
        )
        # Re-save with same links
        db_with_entry.upsert_entry(
            {
                "id": "source",
                "kb_name": "test",
                "title": "Source Entry",
                "entry_type": "note",
                "links": [
                    {
                        "target": "target-0",
                        "relation": "related_to",
                        "kb": "test",
                        "note": "important",
                    },
                    {"target": "target-1", "relation": "related_to", "kb": "test"},
                ],
            }
        )
        outlinks = db_with_entry.get_outlinks("source", "test", readable_kbs=UNSCOPED)
        noted = [l for l in outlinks if l.get("note") == "important"]
        assert len(noted) == 1
