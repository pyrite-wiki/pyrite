"""A read-tier call's cost is bounded by the index, not by the KB's size, and a read changes no state.

Reports (KB health, QA validate and status) are reused inside the cache
window. The oracle is a change the index has not seen: a file dropped into the
KB behind the index's back changes a freshly computed report, so a report that
does not change was reused. A write through Pyrite (an index change) must show.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pyrite.config import KBConfig
from tests._world import build_world


@pytest.fixture
def world(tmp_path: Path):
    w = build_world(
        tmp_path / "w", kbs=[KBConfig(name="k", path=tmp_path / "w" / "k", kb_type="generic")]
    )
    w.add_entry("k", "---\nid: a\ntitle: A\ntype: note\n---\n\nBody.\n")
    w.index("k")
    yield w
    w.close()


# --- KB health ------------------------------------------------------------------


STRAY = "---\nid: stray\ntitle: Stray\ntype: note\n---\n\nNot yet indexed.\n"


@pytest.mark.control(
    reason="control: held at the round-1 head; the oracle is now an effect, not a count"
)
def test_repeated_health_reads_reuse_one_report(world):
    first = world.client.get("/api/kbs/k/health").json()
    assert first["healthy"] is True
    (world.kb_path("k") / "stray.md").write_text(STRAY)  # the index has not seen it
    for _ in range(4):
        assert world.client.get("/api/kbs/k/health").json() == first


@pytest.mark.control(reason="control: held at the round-1 head")
def test_health_read_merges_nothing_into_the_config(world, tmp_path):
    """A database-registered KB is not merged into the shared config by a read."""
    extra = tmp_path / "extra"
    extra.mkdir()
    world.db.register_kb(name="extra", kb_type="generic", path=str(extra))
    config = world.app.state.pyrite_config
    assert config.get_kb("extra") is None
    assert world.client.get("/api/kbs/k/health").status_code == 200
    assert config.get_kb("extra") is None
    assert [kb.name for kb in config.knowledge_bases] == ["k"]


@pytest.mark.control(
    reason="control: a database-registered KB was already checked; the merge is gone"
)
def test_health_of_a_kb_registered_in_the_database_still_works(world, tmp_path):
    """The reason the merge was there (#1343): a ``kb add`` KB is not in config."""
    extra = tmp_path / "extra"
    extra.mkdir()
    (extra / "x.md").write_text("---\nid: x\ntitle: X\ntype: note\n---\n\nB.\n")
    world.db.register_kb(name="extra", kb_type="generic", path=str(extra))
    config = world.app.state.pyrite_config
    names_before = [kb.name for kb in config.knowledge_bases]
    resp = world.client.get("/api/kbs/extra/health")
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "extra" and resp.json()["path_exists"] is True
    assert [kb.name for kb in config.knowledge_bases] == names_before


@pytest.mark.control(reason="control: the default (CLI, admin tool) was always fresh")
def test_the_cli_and_service_default_stays_fresh(world):
    from pyrite.services.kb_registry_service import KBRegistryService

    reg = KBRegistryService(world.config, world.db)
    assert reg.health_kb("k")["healthy"] is True
    (world.kb_path("k") / "stray.md").write_text(STRAY)
    assert reg.health_kb("k")["healthy"] is False


# --- a missing entry id ---------------------------------------------------------------


@pytest.fixture
def big(tmp_path: Path):
    """60 indexed files, plus helpers to add files the index has not seen."""
    w = build_world(
        tmp_path / "w", kbs=[KBConfig(name="k", path=tmp_path / "w" / "k", kb_type="generic")]
    )
    for i in range(60):
        w.add_entry("k", f"---\nid: e{i}\ntitle: E{i}\ntype: note\n---\n\nBody {i}.\n")
    w.index("k")
    yield w
    w.close()


def _drop(w, filename: str, text: str) -> None:
    """A file written behind the index's back, as a hand edit or a git pull does."""
    (w.kb_path("k") / filename).write_text(text)


def _same_size_edit(w, filename: str, old: str, new: str) -> None:
    """Change a file's text without moving its size or mtime, so the index (which
    judges a file by those two) takes it for unchanged."""
    import os

    path = w.kb_path("k") / filename
    before = path.stat()
    path.write_text(path.read_text().replace(old, new))
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert path.stat().st_size == before.st_size


@pytest.mark.control(reason="control: held at the round-1 head")
def test_an_indexed_unchanged_file_is_not_read_for_a_missing_id(big):
    """The index is the answer for a file it knows and sees unchanged: a file
    whose text now holds another id (size and mtime kept) is not opened."""
    _same_size_edit(big, "e7.md", "id: e7", "id: zz")
    assert big.client.get("/api/entries/zz?kb=k").status_code == 404


@pytest.mark.control(reason="control: held at the round-1 head")
def test_a_renamed_unindexed_file_is_still_found(big):
    """The correctness #1106 kept: a file the index has not seen answers its
    frontmatter id."""
    _drop(big, "renamed.md", "---\nid: fresh-one\ntitle: Fresh\ntype: note\n---\n\nNew.\n")
    resp = big.client.get("/api/entries/fresh-one?kb=k")
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == "fresh-one"


@pytest.mark.control(reason="control: held at the round-1 head")
def test_a_file_edited_to_a_new_id_since_indexing_is_still_found(big):
    target = big.kb_path("k") / "e7.md"
    target.write_text("---\nid: renamed-by-hand\ntitle: E7\ntype: note\n---\n\nChanged body.\n")
    resp = big.client.get("/api/entries/renamed-by-hand?kb=k")
    assert resp.status_code == 200, resp.text


def test_a_miss_reads_only_the_newest_files_when_the_index_is_far_behind(big):
    import os

    bound = 200  # the most files a miss may read, written out
    for i in range(bound + 20):
        _drop(big, f"n{i:04d}.md", f"---\nid: new{i}\ntitle: N\ntype: note\n---\n\nB.\n")
        os.utime(big.kb_path("k") / f"n{i:04d}.md", (1_700_000_000, 1_700_000_000 + i))
    newest = big.client.get(f"/api/entries/new{bound + 19}?kb=k")
    oldest = big.client.get("/api/entries/new0?kb=k")
    assert newest.status_code == 200, newest.text
    assert oldest.status_code == 404  # beyond the bound until `pyrite index sync`


# --- QA validate and status ---------------------------------------------------------------


_QA_ROUTES = ["/api/qa/validate?kb=k", "/api/qa/validate", "/api/qa/status", "/api/qa/status?kb=k"]
_BROKEN = "---\nid: dup\nid: dup2\n---\n\nB.\n"


@pytest.mark.control(reason="control: held at the round-1 head")
@pytest.mark.parametrize("path", _QA_ROUTES)
def test_repeated_qa_reads_reuse_one_report(world, path):
    first = world.client.get(path)
    assert first.status_code == 200, first.text
    # A file the index has not seen would change a freshly built report.
    (world.kb_path("k") / "broken.md").write_text(_BROKEN)
    for _ in range(4):
        assert world.client.get(path).json() == first.json()


@pytest.mark.control(
    reason="control: before the cache every read was fresh; pins that a write still shows"
)
def test_the_qa_report_follows_a_change_to_the_index(world):
    """A cached report is not served across a write: the next read sees it."""
    before = world.client.get("/api/qa/validate?kb=k").json()
    world.add_entry("k", "---\nid: second\ntitle: Second\ntype: note\n---\n\nBody.\n")
    after = world.client.get("/api/qa/validate?kb=k").json()
    assert after["total"] == before["total"] + 1


# --- the MCP QA tools are bounded as the REST routes are ----------------------------------


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("kb_qa_validate", {"kb_name": "k"}),
        ("kb_qa_validate", {}),
        ("kb_qa_status", {"kb_name": "k"}),
        ("kb_qa_status", {}),
    ],
)
def test_repeated_mcp_qa_reads_reuse_one_report(world, tool, args):
    server = world.mcp("read")
    first = server._dispatch_tool(tool, args)
    assert "error_code" not in first, first
    (world.kb_path("k") / "broken.md").write_text(_BROKEN)
    for _ in range(4):
        assert server._dispatch_tool(tool, args) == first


@pytest.mark.control(
    reason="control: QA reports were always fresh; pins that the cache never serves across a write"
)
def test_the_mcp_qa_report_follows_a_change_to_the_index(world):
    server = world.mcp("read")
    before = server._dispatch_tool("kb_qa_status", {"kb_name": "k"})
    world.add_entry("k", "---\nid: third\ntitle: Third\ntype: note\n---\n\nBody.\n")
    after = server._dispatch_tool("kb_qa_status", {"kb_name": "k"})
    assert after["total_entries"] == before["total_entries"] + 1


def test_files_that_are_never_indexed_do_not_crowd_out_a_new_one(big, caplog):
    """250 non-entry .md files (no row, ever) are older than a file just added
    by hand: the new file is still found, and the miss logs no warning."""
    import logging
    import os

    for i in range(250):
        path = big.kb_path("k") / f"notes{i:04d}.md"
        path.write_text("Just prose, no frontmatter.\n")
        os.utime(path, (1_600_000_000, 1_600_000_000 + i))
    _drop(big, "zz-fresh.md", "---\nid: just-added\ntitle: Fresh\ntype: note\n---\n\nNew.\n")
    with caplog.at_level(logging.INFO):
        found = big.client.get("/api/entries/just-added?kb=k")
        missing = big.client.get("/api/entries/not-there?kb=k")
    assert found.status_code == 200, found.text
    assert missing.status_code == 404
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


# --- QA validate of one id ------------------------------------------------------

#: A note whose file name is not its id: a lookup of the stem misses the index.
_HELD = "---\nid: held\ntitle: Held\ntype: note\n---\n\nBody.\n"


def _validate(w, via: str, entry_id: str) -> list[dict]:
    if via == "rest":
        r = w.client.get(f"/api/qa/validate/{entry_id}", params={"kb": "k"})
        assert r.status_code == 200, r.text
        return r.json()["issues"]
    result = w.mcp("read")._dispatch_tool(
        "kb_qa_validate", {"kb_name": "k", "entry_id": entry_id, "severity": "info"}
    )
    assert "error_code" not in result, result
    return result["issues"]


def _unmodellable(issues: list[dict]) -> list[dict]:
    return [i for i in issues if i.get("rule") == "frontmatter_unmodellable"]


@pytest.mark.parametrize("via", ["rest", "mcp"])
def test_qa_validate_of_a_missing_id_reads_no_indexed_unchanged_file(big, via):
    """A read-tier validate of one id costs what the index allows: a file the
    index holds and sees unchanged is its row, never opened again. Its text
    now has a duplicate key (size and mtime kept), so a validate that walks
    the KB reports it under its stem; one bounded by the index does not."""
    _drop(big, "ghost.md", _HELD)
    big.index("k")
    _same_size_edit(big, "ghost.md", "title: Held\ntype: note", "title: Held\ntitle: Hel")
    assert _unmodellable(_validate(big, via, "ghost")) == []


@pytest.mark.control(reason="a file the index has not seen was read before and still is")
@pytest.mark.parametrize("via", ["rest", "mcp"])
def test_qa_validate_still_explains_a_file_the_index_has_not_seen(big, via):
    """The correctness the walk was for: a file no lookup can read (a
    duplicate key), never indexed, is reported for its stem with its line."""
    _drop(big, "fresh.md", "---\ntitle: A\ntitle: B\ntype: note\n---\n\nBody.\n")
    rows = _unmodellable(_validate(big, via, "fresh"))
    assert [r["entry_id"] for r in rows] == ["fresh"], rows
