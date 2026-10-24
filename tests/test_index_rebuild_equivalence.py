"""Files alone must rebuild the index left by a normal incremental session (#1155)."""

import json
import sqlite3
from pathlib import Path

from pyrite.utils.frontmatter import load_frontmatter

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager

# Only volatile bookkeeping is ignored. Content, identity, metadata and file
# identity columns are compared; JSON object order is not semantic.
IGNORE = {"indexed_at"}
BOOKKEEPING_TIMESTAMPS = {"created_at", "updated_at"}
JSON_COLUMNS = {"metadata", "aliases", "coordinates"}


def snapshot(path):
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        result = {}
        for table in (
            "entry",
            "entry_tag",
            "link",
            "source",
            "entry_ref",
            "block",
            "edge_endpoint",
        ):
            columns = [
                row[1]
                for row in connection.execute(f"PRAGMA table_info({table})")
                if row[1] not in IGNORE
                and not (table != "entry" and row[1] in {"id", *BOOKKEEPING_TIMESTAMPS})
            ]
            rows = []
            query = (
                "SELECT et.entry_id, et.kb_name, t.name AS tag FROM entry_tag et JOIN tag t ON t.id = et.tag_id"
                if table == "entry_tag"
                else f"SELECT * FROM {table}"
            )
            if table == "entry_tag":
                columns = ["entry_id", "kb_name", "tag"]
            for row in connection.execute(query):
                values = dict(row)
                for column in JSON_COLUMNS & values.keys():
                    if values[column]:
                        values[column] = json.loads(values[column])
                row_columns = columns
                if table == "entry":
                    # Missing timestamps are model defaults, rebuilt at read time.
                    # Only a timestamp actually carried by the file is content.
                    frontmatter, _ = load_frontmatter(
                        Path(values["file_path"]).read_text(encoding="utf-8")
                    )
                    row_columns = [
                        key
                        for key in columns
                        if key not in BOOKKEEPING_TIMESTAMPS or key in frontmatter
                    ]
                rows.append({key: values[key] for key in row_columns})
            result[table] = sorted(rows, key=lambda row: json.dumps(row, sort_keys=True))
        return result


def test_incremental_session_equals_a_files_only_rebuild(tmp_path):
    root = tmp_path / "kb"
    root.mkdir()
    (root / "kb.yaml").write_text(
        "name: demo\ntypes:\n  event:\n    fields:\n      related: {type: object-ref}\n"
        "  relation:\n    edge_type: true\n    fields:\n      owner: {type: object-ref}\n"
        "    endpoints:\n      source:\n        field: owner\n        accepts: [event]\n",
        encoding="utf-8",
    )
    (root / "source.md").write_text(
        "---\nid: source\ntitle: Source\ntype: event\ntags: [two]\n"
        "date: 2025-02-03\nimportance: 8\nrelated: source\n"
        "created_at: 2025-01-01T00:00:00+00:00\nupdated_at: 2025-01-02T00:00:00+00:00\n"
        "sources:\n  - title: Primary source\n    url: https://example.invalid/source\n"
        "---\nSee [[target]]\n\nEvidence paragraph. ^evidence\n",
        encoding="utf-8",
    )
    path = tmp_path / "index.db"
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="demo", path=root)],
        settings=Settings(index_path=path, auto_embed=False),
    )
    db = PyriteDB(path)
    try:
        IndexManager(db, config).index_all()
        service = KBService(config, db)
        service.create_entry("demo", "target", "Target", "note", body="Destination", tags=["one"])
        service.create_entry("demo", "discard", "Discard", "note", body="Delete me")
        before_source = next(row for row in snapshot(path)["entry"] if row["id"] == "source")
        service.update_entry("source", "demo", title="Updated source")
        service.update_entry(
            "target",
            "demo",
            title="Updated target",
            tags=["one", "changed"],
            metadata={"custom": "kept"},
        )
        service.rename_entry("target", "renamed", "demo")
        service.delete_entry("discard", "demo")
        service.create_entry("demo", "relation", "Relation", "relation", owner="source")
        incremental = snapshot(path)
        assert {row["id"] for row in incremental["entry"]} == {"renamed", "source", "relation"}
        assert incremental["entry_tag"]
        assert incremental["link"]
        for table in ("source", "entry_ref", "block", "edge_endpoint"):
            assert incremental[table], table
        source = next(row for row in incremental["entry"] if row["id"] == "source")
        assert source["date"] == "2025-02-03" and source["importance"] == 8
        assert source["created_at"] == before_source["created_at"]
        assert source["updated_at"] != before_source["updated_at"]
    finally:
        db.close()
    # The fresh database has no access to the previous rows or write history.
    path.unlink()
    fresh = PyriteDB(path)
    try:
        IndexManager(fresh, config).index_all()
        assert snapshot(path) == incremental
    finally:
        fresh.close()
