"""Every link row says whether its target exists for this caller (#64).

Reproduced from the 2026-10-02/10-19 grooms: `pyrite get` returns an outlink
to `[[nowhere-entry]]` as `{"id": "nowhere-entry", "title": null, ...}` with
no way to tell "does not exist" from "exists somewhere I did not name", while
the write half already reports `resolved` on `kb_link` (ADR-0042).

The invariant under test: a link says whether its target exists for this
caller, with the same field name on write and on read --

- every outlink/backlink row carries ``resolved: true|false``;
- an unresolved row whose id exists in another readable KB names them in
  ``resolved_in_kbs`` (a list: an id can live in two other KBs);
- a target in a KB the caller cannot read reads exactly as a missing one
  (concealment, ADR-0037 section 4);
- `pyrite link` accepts ``--allow-dangling`` and reports ``resolved``, matching
  what MCP `kb_link` already does.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pyrite.config import KBConfig
from pyrite.services.kb_service import KBService
from tests._world import build_world

KB = "test-kb"
OTHER = "other-kb"


def _seed(w, kb_name: str, text: str) -> None:
    """Write one entry file with LF endings and index it.

    ``Path.write_text`` without ``newline`` would give the file CRLF endings
    on Windows, and the in-place write path refuses a `---\\r` delimiter line
    -- the same Windows-local artefact the CRLF fixtures in the suite carry.
    """
    import yaml

    front = text.split("---", 2)[1]
    entry_id = str(yaml.safe_load(front)["id"])
    path = w.kb_path(kb_name) / f"{entry_id}.md"
    path.write_text(text, newline="\n")
    w.index(kb_name)


def _link_world(tmp_path: Path):
    """Two general KBs; `t` is the reader's own KB, `u` the other one."""
    w = build_world(
        tmp_path / "w",
        kbs=[
            KBConfig(name=KB, path=tmp_path / "w" / KB, kb_type="generic"),
            KBConfig(name=OTHER, path=tmp_path / "w" / OTHER, kb_type="generic"),
        ],
    )
    _seed(w, KB, "---\nid: src\ntype: note\ntitle: Source\n---\n\nThe source entry.\n")
    _seed(
        w, OTHER, "---\nid: widget\ntype: note\ntitle: Widget\n---\n\nLives only in the other KB.\n"
    )
    yield w
    w.close()


@pytest.fixture
def link_world(tmp_path: Path):
    yield from _link_world(tmp_path)


def _outlinks(w, entry_id, kb_name, readable_kbs=None):
    return KBService(w.config, w.db).get_entry(entry_id, kb_name, readable_kbs=readable_kbs)[
        "outlinks"
    ]


def test_a_dangling_outlink_reads_as_unresolved(link_world):
    """A target that exists nowhere is `resolved: false`, candidates empty."""
    svc = KBService(link_world.config, link_world.db)
    svc.add_link("src", KB, "nowhere-entry", allow_dangling=True)

    outlinks = _outlinks(link_world, "src", KB)
    row = next(r for r in outlinks if r["id"] == "nowhere-entry")
    assert row["resolved"] is False
    assert row["resolved_in_kbs"] == []


def test_an_unresolved_outlink_whose_id_lives_in_another_readable_kb_names_them(
    link_world, tmp_path
):
    """The id exists in a KB the caller can read: name them, stay unresolved."""
    svc = KBService(link_world.config, link_world.db)
    # The prefix is part of the row (written [[t:widget]]); a target in `t`
    # that is not there is dangling, while `u` does hold the id.
    svc.add_link("src", KB, "widget", target_kb=KB, allow_dangling=True)

    outlinks = _outlinks(link_world, "src", KB, readable_kbs={KB, OTHER})
    row = next(r for r in outlinks if r["id"] == "widget")
    assert row["resolved"] is False
    assert row["resolved_in_kbs"] == [OTHER]


def test_a_target_in_a_kb_the_caller_cannot_read_reads_as_missing(link_world, tmp_path):
    """Concealment: an unreadable KB's copy is never named (ADR-0037 section 4)."""
    svc = KBService(link_world.config, link_world.db)
    svc.add_link("src", KB, "widget", target_kb=KB, allow_dangling=True)

    outlinks = _outlinks(link_world, "src", KB, readable_kbs={KB})
    row = next(r for r in outlinks if r["id"] == "widget")
    assert row["resolved"] is False
    assert row["resolved_in_kbs"] == []


def test_a_resolved_outlink_is_resolved_and_names_nothing(link_world, tmp_path):
    """A target that exists where the link says: `resolved: true`."""
    svc = KBService(link_world.config, link_world.db)
    svc.add_link("src", KB, "widget", target_kb=OTHER)

    outlinks = _outlinks(link_world, "src", KB)
    row = next(r for r in outlinks if r["id"] == "widget")
    assert row["resolved"] is True
    assert "resolved_in_kbs" not in row


def test_a_target_created_later_flips_to_resolved_without_a_rewrite(link_world, tmp_path):
    """Resolution is computed on read: create the target, re-read, `true`."""
    svc = KBService(link_world.config, link_world.db)
    svc.add_link("src", KB, "widget", target_kb=KB, allow_dangling=True)
    assert (
        next(r for r in _outlinks(link_world, "src", KB) if r["id"] == "widget")["resolved"]
        is False
    )

    _seed(
        link_world,
        KB,
        "---\nid: widget\ntype: note\ntitle: Widget in t\n---\n\nCreated after the link.\n",
    )
    row = next(r for r in _outlinks(link_world, "src", KB) if r["id"] == "widget")
    assert row["resolved"] is True
    assert "resolved_in_kbs" not in row


def test_backlink_rows_carry_the_same_resolved_flag(link_world, tmp_path):
    """Both ends of a backlink row exist by construction: `resolved: true`."""
    svc = KBService(link_world.config, link_world.db)
    _seed(
        link_world,
        KB,
        "---\nid: widget\ntype: note\ntitle: Widget in t\n---\n\nCreated for the incoming link.\n",
    )
    svc.add_link("src", KB, "widget")

    entry = KBService(link_world.config, link_world.db).get_entry("widget", KB, readable_kbs=None)
    assert entry["backlinks"], entry
    assert all(r["resolved"] is True for r in entry["backlinks"])


def test_pyrite_link_accepts_allow_dangling_and_reports_resolved(link_world, tmp_path):
    """`pyrite link --allow-dangling` writes the forward reference and says so."""
    result = link_world.cli(
        "link", "src", "nowhere-entry", "-k", KB, "--allow-dangling", "--format", "json"
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    row = data["links"][0]
    assert row["resolved"] is False

    result = link_world.cli(
        "link", "src", "widget", "-k", KB, "--target-kb", OTHER, "--format", "json"
    )
    assert result.exit_code == 0, result.output
    row = json.loads(result.output)["links"][0]
    assert row["resolved"] is True


def test_rest_entry_read_carries_the_same_fields(link_world, tmp_path):
    """The REST entry endpoint surfaces the flags with the relation fields."""
    svc = KBService(link_world.config, link_world.db)
    svc.add_link("src", KB, "widget", target_kb=KB, allow_dangling=True)

    resp = link_world.client.get(f"/api/entries/src?kb={KB}")
    assert resp.status_code == 200, resp.text
    outlinks = resp.json()["outlinks"]
    row = next(r for r in outlinks if r["id"] == "widget")
    assert row["resolved"] is False
    assert row["resolved_in_kbs"] == [OTHER]


def test_a_backlink_is_resolved_even_when_its_source_title_is_null():
    from pyrite.storage.backends.base_backend import _with_resolved

    # The backlink JOIN proves existence; title nullability cannot undo it.
    rows = _with_resolved([{"id": "source", "title": None}], None, None)
    assert rows == [{"id": "source", "title": None, "resolved": True}]
