"""The link writers append one item through ``apply()`` (#955, B6 P4).

ADR-0042 decision 12: one write path. Until P4, ``KBService.add_link`` and
``add_links`` loaded the source, appended a ``Link`` to the model and saved
the whole model (``DocumentManager.save_entry`` → ``Entry.save``): every
other key was re-emitted from the model's reading, so a hand-written
comment, a legacy link item's ``since: 2020`` and the file's own spellings
were lost on the first ``pyrite link``. Now each new link is one
``Append("links", item)`` through ``KBService.apply_operations``: the file's
other bytes are untouched, the item is checked as a link, and the write is
compare-and-replace under the momentary lock like any update.

The surfaces: ``KBService.add_link`` and ``add_links``, the CLI's ``link``
and ``links bulk-create``, MCP ``kb_link``. The oracle is bytes outside the
appended item's span (``file_operations_oracle``).
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from pyrite.cli import app as cli_app
from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.exceptions import ValidationError
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB
from pyrite.storage.file_operations import Append, FrontmatterSpan, Set
from pyrite.storage.index import IndexManager
from pyrite.utils.frontmatter import Frontmatter, split_frontmatter
from pyrite.utils.yaml import load_yaml
from tests.file_operations_oracle import op_spans, outside_identical

KB = "ops"
ID = "ops-entry"
TARGET = "ops-target"

LEGACY_ITEM = (
    "- to: foo          # legacy spelling, hand written\r\n  type: related\r\n  since: 2020\r\n"
)

NOTE = (
    "---\n"
    "# kept by hand\n"
    f"id: {ID}\n"
    "title: Ops   # keep\n"
    "type: note\n"
    "tags: [alpha, beta]  # trailing\n"
    "links:\n"
    "- to: foo          # legacy spelling, hand written\n"
    "  type: related\n"
    "  since: 2020\n"
    "extra: 1\n"
    "---\n"
    "\n"
    "Text.\n"
)

TARGET_NOTE = f"---\nid: {TARGET}\ntitle: Target\ntype: note\n---\n\nTarget.\n"


def _build(tmp_path: Path, text: str = NOTE, *, crlf: bool = True):
    kb_path = tmp_path / "kb"
    (kb_path / "notes").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=kb_path, check=True, capture_output=True, timeout=30)
    path = kb_path / "notes" / f"{ID}.md"
    path.write_bytes((text.replace("\n", "\r\n") if crlf else text).encode("utf-8"))
    (kb_path / "notes" / f"{TARGET}.md").write_text(TARGET_NOTE, encoding="utf-8")
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name=KB, path=kb_path, kb_type="generic")],
        settings=Settings(index_path=tmp_path / "index.db", auto_embed=False),
    )
    db = PyriteDB(config.settings.index_path)
    try:
        IndexManager(db, config).index_all()
    finally:
        db.close()
    return config, path


def _text(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


def _span(text: str) -> FrontmatterSpan:
    split = split_frontmatter(text)
    assert isinstance(split, Frontmatter), split
    return FrontmatterSpan(split.yaml_start, split.close_start, split.close_end)


def _links_of(text: str) -> list[dict[str, Any]]:
    split = split_frontmatter(text)
    assert isinstance(split, Frontmatter), split
    return list(load_yaml(split.text)["links"])


def assert_only_changed(before: str, after: str, ops: list[Any]) -> None:
    span = _span(before)
    spans: list[tuple[int, int]] = []
    for op in ops:
        spans += op_spans(before, span, op)
    assert outside_identical(before, after, spans), f"--- before\n{before!r}\n--- after\n{after!r}"
    if "\r\n" in before:
        assert after.count("\r\n") == after.count("\n"), "a bare LF was written into a CRLF file"


def _with_service(config: PyriteConfig, fn: Callable[[KBService], Any]) -> Any:
    db = PyriteDB(config.settings.index_path)
    try:
        return fn(KBService(config, db))
    finally:
        db.close()


# --------------------------------------------------------------------------
# The surfaces: each adds one ``related_to`` link from ``ID`` to ``TARGET``
# with a note, and raises on a refusal
# --------------------------------------------------------------------------

#: The item as `add_link` has always written it: `kb:` is the target KB even
#: when it is the source's own (the spelling is unchanged by P4).
NEW_ITEM = {"target": TARGET, "relation": "related_to", "note": "why", "kb": KB}


def _service(config):
    return _with_service(config, lambda svc: svc.add_link(ID, KB, TARGET, "related_to", note="why"))


def _bulk(config):
    spec = {"source": ID, "target": TARGET, "relation": "related_to", "note": "why"}
    results = _with_service(config, lambda svc: svc.add_links(KB, [spec]))
    if results[0]["status"] != "created":
        raise ValidationError(str(results))
    return results


def _cli(config):
    args = ["link", ID, TARGET, "-k", KB, "--note", "why", "--format", "json"]
    with (
        patch("pyrite.cli.load_config", return_value=config),
        patch("pyrite.cli.context.load_config", return_value=config),
    ):
        result = CliRunner().invoke(cli_app, args)
    if result.exit_code != 0:
        raise ValidationError(result.output)
    return result.output


def _cli_bulk(config):
    specs = config.knowledge_bases[0].path.parent / "links.yaml"
    specs.write_text(
        f"- source: {ID}\n  target: {TARGET}\n  relation: related_to\n  note: why\n",
        encoding="utf-8",
    )
    args = ["links", "bulk-create", str(specs), "-k", KB, "--format", "json"]
    with (
        patch("pyrite.cli.load_config", return_value=config),
        patch("pyrite.cli.context.load_config", return_value=config),
    ):
        result = CliRunner().invoke(cli_app, args)
    if result.exit_code != 0:
        raise ValidationError(result.output)
    return result.output


def _mcp(config):
    from pyrite.server.mcp_server import PyriteMCPServer

    args = {
        "source_id": ID,
        "source_kb": KB,
        "target_id": TARGET,
        "relation": "related_to",
        "note": "why",
    }
    server = PyriteMCPServer(config, tier="write")
    try:
        result = server._dispatch_tool("kb_link", args)
    finally:
        server.close()
    if result.get("error") or result.get("isError"):
        raise ValidationError(str(result))
    return result


SURFACES = {
    "service": _service,
    "service.bulk": _bulk,
    "cli.link": _cli,
    "cli.links.bulk-create": _cli_bulk,
    "mcp.kb_link": _mcp,
}


@pytest.mark.parametrize("surface", sorted(SURFACES))
def test_a_link_is_one_appended_item_and_the_rest_keeps_its_bytes(tmp_path: Path, surface: str):
    config, path = _build(tmp_path)
    before = _text(path)

    SURFACES[surface](config)

    after = _text(path)
    assert LEGACY_ITEM in after, after
    assert "tags: [alpha, beta]  # trailing" in after, after
    assert _links_of(after)[-1] == NEW_ITEM, after
    assert_only_changed(before, after, [Append("links", NEW_ITEM), Set("updated_at", "x")])


@pytest.mark.control(
    reason="the model's save re-indexed the source too; the control pins that the operation write still indexes the appended link"
)
@pytest.mark.parametrize("surface", sorted(SURFACES))
def test_the_link_is_indexed_as_an_outgoing_link(tmp_path: Path, surface: str):
    config, _path = _build(tmp_path)

    SURFACES[surface](config)

    db = PyriteDB(config.settings.index_path)
    try:
        targets = {(l["id"], l["relation"]) for l in db.get_outlinks(ID, KB, readable_kbs=None)}
    finally:
        db.close()
    assert (TARGET, "related_to") in targets, targets


def test_add_links_appends_each_new_item_to_its_source_once(tmp_path: Path):
    """Two new links on one source are two ``Append`` operations in one write;
    a link the file already records (under ``_norm_relation``, the legacy
    ``related`` is ``related_to``) is skipped and writes nothing."""
    config, path = _build(tmp_path)
    before = _text(path)
    specs = [
        {"source": ID, "target": TARGET},
        {"source": ID, "target": "foo"},  # already there, as `to: foo` / `type: related`
        {"source": ID, "target": "later", "relation": "cites", "note": "n"},
    ]

    results = _with_service(config, lambda svc: svc.add_links(KB, specs))

    assert [r["status"] for r in results] == ["created", "skipped", "created"], results
    after = _text(path)
    assert LEGACY_ITEM in after, after
    first = {"target": TARGET, "relation": "related_to", "kb": KB}
    second = {"target": "later", "relation": "cites", "note": "n", "kb": KB}
    assert _links_of(after)[1:] == [first, second], after
    assert_only_changed(
        before, after, [Append("links", first), Append("links", second), Set("updated_at", "x")]
    )


@pytest.mark.control(
    reason="a three-key file has nothing the model's re-emit could lose, so the bytes were right before; the control pins that Append creates the links: key"
)
def test_a_link_on_a_file_without_a_links_key_adds_one(tmp_path: Path):
    config, path = _build(tmp_path, TARGET_NOTE.replace(TARGET, ID), crlf=False)
    before = _text(path)

    _service(config)

    after = _text(path)
    assert _links_of(after) == [NEW_ITEM], after
    assert_only_changed(before, after, [Append("links", NEW_ITEM)])


@pytest.mark.control(
    reason="the model's save already wrote nothing for a duplicate (add_link returned before it); the control pins the no-op while the write changes"
)
def test_a_duplicate_link_writes_nothing(tmp_path: Path):
    config, path = _build(tmp_path)
    before = path.read_bytes()

    result = _with_service(config, lambda svc: svc.add_link(ID, KB, "foo", "related_to"))

    assert result == {"resolved": False, "created": False, "relation": "related"}, result
    assert path.read_bytes() == before


@pytest.mark.control(
    reason="touch_updated_at never invented the key and Entry.save re-emitted it only where the file had it; the control pins decision 7's bookkeeping while the stamp moves to the update's own rule"
)
def test_a_link_stamps_updated_at_only_where_the_file_carries_it(tmp_path: Path):
    config, path = _build(tmp_path)
    _service(config)
    assert "updated_at" not in _text(path)

    stamped = NOTE.replace("extra: 1\n", "extra: 1\nupdated_at: '2020-01-01T00:00:00Z'\n")
    config, path = _build(tmp_path / "stamped", stamped)
    _service(config)
    after = _text(path)
    assert "updated_at: '2020-01-01T00:00:00Z'" not in after, after
    assert "\r\nupdated_at: '20" in after and "T" in after.split("updated_at: '")[1][:20], after


@pytest.mark.control(
    reason="the stack already sends a link through the one update, whose stamp keeps the spelling since #971 fix round 1; the control pins #990's check on this path"
)
def test_a_links_stamp_keeps_the_files_own_spelling(tmp_path: Path):
    """#990: `add_link` goes through the one update, so its `updated_at`
    stamp keeps the file's spelling (a bare `T…Z` stays `T…Z`, #971 fix
    round 1), where the model's save wrote the house form over it."""
    stamped = NOTE.replace("extra: 1\n", "extra: 1\nupdated_at: 2020-01-01T00:00:00Z\n")
    config, path = _build(tmp_path, stamped, crlf=False)

    _service(config)

    line = next(l for l in _text(path).splitlines() if l.startswith("updated_at:"))
    assert re.fullmatch(r"updated_at: \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", line), line
    assert line != "updated_at: 2020-01-01T00:00:00Z", "the stamp did not move"


def test_a_link_refused_by_the_file_writes_nothing_and_names_the_reason(tmp_path: Path):
    """A file whose frontmatter ``apply()`` cannot model (#760 (b)) refuses
    the link with the reason; the model's save used to rewrite it. (A
    duplicate key never loads, so the tagged key is the shape that reaches
    the write.)"""
    broken = NOTE.replace("extra: 1\n", "extra: 1\n!!str flag: 1\n")
    config, path = _build(tmp_path, broken)
    before = path.read_bytes()

    with pytest.raises(ValidationError, match="tagged key"):
        _service(config)

    assert path.read_bytes() == before
