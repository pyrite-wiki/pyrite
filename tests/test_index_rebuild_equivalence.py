"""Files alone must rebuild the index left by a normal incremental session (#1155)."""

import json
import sqlite3
from pathlib import Path

import pytest

from pyrite.utils.frontmatter import load_frontmatter

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager

# What is not compared, and why. Every other column of every table is.
#
# - ``indexed_at``: the wall-clock moment of the index write, in no file.
# - ``id`` of every table but ``entry``: a surrogate key handed out in the order
#   rows were written, which a rebuild is free to change; ``entry.id`` is the
#   entry's own id and is compared.
# - ``created_at`` / ``updated_at`` of every table but ``entry``: bookkeeping of
#   the row, not of the file.
# - ``entry.created_at`` / ``entry.updated_at`` when the file does not carry
#   them: the model's read-time default (now), derivable from no file. A
#   timestamp the file does carry is compared.
# - ``entry_tag.tag_id``: allocated in write order; the tag *name* is compared.
# - JSON object key order inside ``metadata``, ``aliases`` and ``coordinates``
#   is not semantic.
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
        result["fts"] = _fts_content(connection)
        return result


def _fts_content(connection) -> dict[str, list[str]]:
    """What the full-text index holds: each indexed term -> the ids of the
    entries it finds, with its document and occurrence counts.

    ``entry_fts`` reads its columns from ``entry`` (an external-content table),
    so a SELECT of them only repeats ``entry``. The inverted index is what a
    search reads: it is walked through ``fts5vocab`` and answered by MATCH, so a
    stale term, a missing one, or a rowid that points at another entry shows.
    """
    connection.execute("DROP TABLE IF EXISTS temp.entry_fts_vocab")
    connection.execute(
        "CREATE VIRTUAL TABLE temp.entry_fts_vocab USING fts5vocab(main, entry_fts, 'row')"
    )
    content = {}
    for term, docs, count in connection.execute("SELECT term, doc, cnt FROM temp.entry_fts_vocab"):
        ids = [
            row[0]
            for row in connection.execute(
                "SELECT e.kb_name || ':' || e.id FROM entry_fts "
                "JOIN entry e ON e.rowid = entry_fts.rowid WHERE entry_fts MATCH ?",
                (f'"{term}"',),
            )
        ]
        content[term] = [f"docs={docs} count={count}", *sorted(ids)]
    connection.execute("DROP TABLE temp.entry_fts_vocab")
    return content


def differences(incremental: dict, rebuilt: dict) -> str:
    """The rows (and FTS terms) found on one side only, per table."""
    lines = []
    for table in sorted(incremental.keys() | rebuilt.keys()):
        left, right = incremental.get(table), rebuilt.get(table)
        if left == right:
            continue
        if table == "fts":
            for term in sorted(left.keys() | right.keys()):
                if left.get(term) != right.get(term):
                    lines.append(
                        f"fts {term!r}: incremental={left.get(term)} rebuilt={right.get(term)}"
                    )
            continue
        only_left = [row for row in left if row not in right]
        only_right = [row for row in right if row not in left]
        lines.extend(f"{table} only in the incremental index: {row}" for row in only_left)
        lines.extend(f"{table} only in the rebuilt index:    {row}" for row in only_right)
    return "\n".join(lines)


def _hand_edit(root: Path) -> None:
    """The files a person changes without Pyrite: one edited, one added (a
    type-less entry with ``status:`` and ``aliases:``, links in the kb and
    heading spellings), one removed. ``index sync`` is the writer that sees them."""
    source = root / "source.md"
    source.write_text(
        source.read_text(encoding="utf-8").replace(
            "Evidence paragraph.", "Evidence paragraph, edited by hand."
        ),
        encoding="utf-8",
    )
    (root / "loose.md").write_text(
        "---\nid: loose\ntitle: Loose\nstatus: active\naliases: [Alt Name, Other]\n"
        "tags: [hand]\n---\nPoints at [[demo:renamed]] and [[renamed#Details]] "
        "and [[source|the source]].\n",
        encoding="utf-8",
    )
    (root / "gone.md").write_text(
        "---\nid: gone\ntitle: Gone\ntype: note\n---\nRemoved below.\n", encoding="utf-8"
    )


@pytest.mark.control(
    reason="the property holds today: no divergence was found; the test guards the next one"
)
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
        service.delete_entry("discard", "demo", readable_kbs=None)
        service.create_entry("demo", "relation", "Relation", "relation", owner="source")
        # add_link: how most links reach a file; it writes through the update path.
        service.add_link("source", "demo", "renamed", relation="cites", note="added by link")
        # What a person does without Pyrite, seen only by `index sync`.
        _hand_edit(root)
        (root / "gone.md").unlink()
        IndexManager(db, config).sync_incremental("demo")

        incremental = snapshot(path)
        assert {row["id"] for row in incremental["entry"]} == {
            "renamed",
            "source",
            "relation",
            "loose",
        }
        assert incremental["entry_tag"]
        assert incremental["link"]
        for table in ("source", "entry_ref", "block", "edge_endpoint", "fts"):
            assert incremental[table], table
        source = next(row for row in incremental["entry"] if row["id"] == "source")
        assert source["date"] == "2025-02-03" and source["importance"] == 8
        assert source["created_at"] == before_source["created_at"]
        assert source["updated_at"] != before_source["updated_at"]
        # The hand-written shapes reached the index: a type-less entry's status,
        # its aliases, and a link in each spelling.
        loose = next(row for row in incremental["entry"] if row["id"] == "loose")
        assert loose["metadata"] == {"status": "active"} and loose["aliases"] == [
            "Alt Name",
            "Other",
        ], loose
        assert {row["target_id"] for row in incremental["link"] if row["source_id"] == "loose"} >= {
            "renamed",
            "source",
        }, incremental["link"]
        # porter stems "edited" to "edit"
        assert "demo:source" in incremental["fts"]["edit"], "the hand edit is not in the FTS"
    finally:
        db.close()
    # The fresh database has no access to the previous rows or write history.
    path.unlink()
    fresh = PyriteDB(path)
    try:
        IndexManager(fresh, config).index_all()
        rebuilt = snapshot(path)
    finally:
        fresh.close()
    difference = differences(incremental, rebuilt)
    assert not difference, "\n" + difference
