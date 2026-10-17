"""An update changes only the spans of the keys it names, on every surface (#955, B6 P3a).

The invariant (the spike on #730, §4): a write to an existing entry file
changes only the spans of the keys it names. It writes nothing on a no-op. It
never moves the file, except for a pinned templated move. It never publishes
bytes it cannot index. The walk here is the fixture shapes the spike ran ×
every entry point an update has: the surfaces (CLI ``update``, MCP
``kb_update``, REST ``PUT`` and ``PATCH``, ``KBService.update`` and
``update_entry``) and the in-process callers (``TaskService``,
``QAFixService``, software-kb ``sw_prioritize``).

The oracle is bytes, before against after, with the spans of the named keys
computed by ``tests/file_operations_oracle.py`` from the *input* text by its
own rule (never by the code under test): everything outside those spans is
identical, so comments, blank lines, key order, quoting, indentation, the BOM
and the line endings are kept by construction. A no-op is a second identical
request: the bytes and the mtime do not change. "Never moves" is the file's
path and the KB's file list.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from pyrite.cli import app as cli_app
from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.exceptions import PyriteError, ValidationError, public_text
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB
from pyrite.storage.file_operations import FrontmatterSpan, ReplaceBody, Set
from pyrite.storage.index import IndexManager
from pyrite.utils.frontmatter import Frontmatter, split_frontmatter
from tests.file_operations_oracle import op_spans, outside_identical

KB = "walk"
ID = "walk-entry"

BOM = "﻿"

# --------------------------------------------------------------------------
# The fixture shapes (the spike's §4), each one file holding the entry ``walk-entry``
# --------------------------------------------------------------------------

#: name -> (file text, flags). ``crlf`` writes CRLF; ``root`` puts the file at
#: the KB root (a note's folder is ``notes/``, #488); ``event`` makes the entry
#: an event (the alias shape).
SHAPES: dict[str, tuple[str, frozenset[str]]] = {
    "crlf-comments": (
        "---\n"
        "# reviewed by hand, do not reorder\n"
        f"id: {ID}\n"
        "title: Walk   # working title\n"
        "type: note\n"
        "\n"
        "tags: [alpha, beta]  # trailing\n"
        "---\n"
        "\n"
        "    an indented first line\n"
        "\n"
        "Text.\n",
        frozenset({"crlf"}),
    ),
    "bom": (
        f"{BOM}---\nid: {ID}\ntitle: Walk\ntype: note\nnote: bom\n---\n\nText.\n",
        frozenset(),
    ),
    "yaml11": (
        f"---\nid: {ID}\ntitle: Walk\ntype: note\nflag: no\nnil: null\nother: yes\n---\n\nText.\n",
        frozenset(),
    ),
    "hugo": (
        "---\n"
        f"id: {ID}\n"
        "title: Walk\n"
        "type: note\n"
        "date: 2026-01-15T10:00:00+01:00\n"
        "params:\n"
        "    social:\n"
        "        x: 1\n"
        "---\n"
        "\n"
        '{{< figure src="a.png" >}}\n'
        "\n"
        "Text.\n",
        frozenset(),
    ),
    "legacy-link": (
        "---\n"
        f"id: {ID}\n"
        "title: Walk\n"
        "type: note\n"
        "links:\n"
        "- to: foo          # legacy spelling, hand written\n"
        "  type: related\n"
        "  since: 2020\n"
        "---\n"
        "\n"
        "Text.\n",
        frozenset(),
    ),
    "alias": (
        f"---\nid: {ID}\ntitle: Walk\ntype: event\ndate: 2026-01-01\nparticipants:\n- a\n---\n\nText.\n",
        frozenset({"event"}),
    ),
    "metadata-block": (
        f"---\nid: {ID}\ntitle: Walk\ntype: note\nmetadata:\n  k: 1\n---\n\nText.\n",
        frozenset(),
    ),
    "quoted-updated-at": (
        f"---\nid: {ID}\ntitle: Walk\ntype: note\nupdated_at: '2026-01-01T00:00:00Z'\n---\n\nText.\n",
        frozenset(),
    ),
    "bare-updated-at": (
        f"---\nid: {ID}\ntitle: Walk\ntype: note\nupdated_at: 2026-01-01 00:00:00+00:00\n---\n\nText.\n",
        frozenset(),
    ),
    "anchor-elsewhere": (
        f"---\nid: {ID}\ntitle: Walk\ntype: note\nbase: &b 1\nref: *b\n---\n\nText.\n",
        frozenset(),
    ),
    "root-file": (
        f"---\nid: {ID}\ntitle: Walk\ntype: note\n---\n\nText.\n",
        frozenset({"root"}),
    ),
}

#: Refused with nothing written: a tagged value cannot be indexed (the row's
#: JSON), and the frontmatter cannot be modelled (#760 (b)).
TAGGED_VALUE = f"---\nid: {ID}\ntitle: Walk\ntype: note\nv: !custom 1\n---\n\nText.\n"

#: Keys an update of the title may also change: the bookkeeping stamp, only
#: where the file already carries it (ADR-0042 decision 7).
TITLE_EDIT_KEYS = ("title", "updated_at")


def _git_init(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True, capture_output=True, timeout=30)


def _write(path: Path, text: str, flags: frozenset[str]) -> None:
    if "crlf" in flags:
        text = text.replace("\n", "\r\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def _build(tmp_path: Path, text: str, flags: frozenset[str] = frozenset(), *, git: bool = True):
    """A generic KB holding the one file, indexed; ``(config, path)``."""
    kb_path = tmp_path / "kb"
    kb_path.mkdir()
    (kb_path / "kb.yaml").write_text(f"name: {KB}\nkb_type: generic\n", encoding="utf-8")
    if git:
        _git_init(kb_path)
    rel = f"{ID}.md" if "root" in flags else f"notes/{ID}.md"
    path = kb_path / rel
    _write(path, text, flags)
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


def _span(text: str) -> FrontmatterSpan:
    split = split_frontmatter(text)
    assert isinstance(split, Frontmatter), split
    return FrontmatterSpan(split.yaml_start, split.close_start, split.close_end)


def _text(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


def assert_only_these_keys_changed(before: str, after: str, keys: tuple[str, ...]) -> None:
    """Bytes outside the spans of ``keys`` (as the input text holds them) are
    identical. A key the file does not hold owns only the end of the
    frontmatter, where a new key is written (``op_spans`` of a Set)."""
    span = _span(before)
    spans: list[tuple[int, int]] = []
    for key in keys:
        op = ReplaceBody("x") if key == "body" else Set(key, "x")
        spans += op_spans(before, span, op)
    assert outside_identical(before, after, spans), (
        f"bytes outside {keys} changed:\n--- before\n{before!r}\n--- after\n{after!r}"
    )


def _with_service(config: PyriteConfig, fn: Callable[[KBService], Any]) -> Any:
    db = PyriteDB(config.settings.index_path)
    try:
        return fn(KBService(config, db))
    finally:
        db.close()


# --------------------------------------------------------------------------
# The surfaces: each one sends ``updates`` for ``ID`` and raises on a refusal
# --------------------------------------------------------------------------


def _service_update(config, updates):
    return _with_service(config, lambda svc: svc.update(ID, KB, dict(updates)))


def _update_entry(config, updates):
    return _with_service(config, lambda svc: svc.update_entry(ID, KB, **updates))


def _mcp(config, updates):
    from pyrite.server.mcp_server import PyriteMCPServer

    server = PyriteMCPServer(config, tier="write")
    try:
        result = server._dispatch_tool("kb_update", {"kb_name": KB, "entry_id": ID, **updates})
    finally:
        server.close()
    if result.get("error") or result.get("isError"):
        raise ValidationError(str(result))
    return result


def _cli(config, updates):
    args = ["update", ID, "-k", KB, "--format", "json"]
    for key, value in updates.items():
        if key == "title":
            args += ["--title", value]
        elif key == "body":
            args += ["--body", value]
        else:
            # `-f k=v` parses a JSON object or array (`_parse_field_value`), the
            # one way the CLI sends a mapping such as `metadata`.
            spelled = json.dumps(value) if isinstance(value, dict | list) else value
            args += ["-f", f"{key}={spelled}"]
    with (
        patch("pyrite.cli.load_config", return_value=config),
        patch("pyrite.cli.context.load_config", return_value=config),
    ):
        result = CliRunner().invoke(cli_app, args)
    if result.exit_code != 0:
        raise ValidationError(result.output)
    return result.output


def _rest_client(config):
    pytest.importorskip("fastapi", reason="fastapi not installed")
    from fastapi.testclient import TestClient

    from pyrite.server.api import create_app, get_config, get_db, get_index_mgr

    db = PyriteDB(config.settings.index_path)
    index_mgr = IndexManager(db, config)
    app = create_app(config)
    app.dependency_overrides[get_config] = lambda: config
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_index_mgr] = lambda: index_mgr
    # A refusal is a response, whatever raised it: the read a PUT makes
    # before the write can fail on a file Pyrite cannot model (#760), and the
    # property here is that nothing was written.
    return TestClient(app, raise_server_exceptions=False), db


def _rest_put(config, updates):
    client, db = _rest_client(config)
    try:
        res = client.put(f"/api/entries/{ID}", json={"kb": KB, **updates})
    finally:
        client.close()
        db.close()
    if res.status_code != 200:
        raise ValidationError(res.text)
    return res.json()


def _rest_patch(config, updates):
    client, db = _rest_client(config)
    try:
        results = []
        for key, value in updates.items():
            res = client.patch(
                f"/api/entries/{ID}", json={"kb": KB, "field": key, "value": str(value)}
            )
            if res.status_code != 200:
                raise ValidationError(res.text)
            results.append(res.json())
    finally:
        client.close()
        db.close()
    return results


SURFACES: dict[str, Callable[[PyriteConfig, dict[str, Any]], Any]] = {
    "service.update": _service_update,
    "service.update_entry": _update_entry,
    "mcp.kb_update": _mcp,
    "cli.update": _cli,
    "rest.put": _rest_put,
    "rest.patch": _rest_patch,
}


# --------------------------------------------------------------------------
# The walk: every shape × every surface, one title edit, then the same edit again
# --------------------------------------------------------------------------


@pytest.mark.parametrize("surface", sorted(SURFACES))
@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_a_title_edit_changes_only_the_titles_span(tmp_path: Path, shape: str, surface: str):
    text, flags = SHAPES[shape]
    config, path = _build(tmp_path, text, flags)
    files_before = (
        sorted(
            p.relative_to(config.knowledge_bases[0].path) for p in path.parent.parent.rglob("*.md")
        )
        if "root" not in flags
        else sorted(p.relative_to(path.parent) for p in path.parent.rglob("*.md"))
    )
    before = _text(path)

    SURFACES[surface](config, {"title": "Walk, second draft"})

    assert path.exists(), "the file moved or vanished"
    after = _text(path)
    assert after != before, "the edit was not written"
    assert "title: Walk, second draft" in after
    assert_only_these_keys_changed(before, after, TITLE_EDIT_KEYS)
    assert after.startswith(BOM) == before.startswith(BOM)
    assert ("\r\n" in after) == ("\r\n" in before), "the line ending changed"
    if "\r\n" in before:
        assert after.count("\r\n") == after.count("\n"), "a bare LF was written into a CRLF file"
    root = config.knowledge_bases[0].path
    files_after = sorted(p.relative_to(root) for p in root.rglob("*.md"))
    expected_files = sorted(p.relative_to(root) for p in [path])
    assert files_after == expected_files, f"the KB's files changed: {files_before} -> {files_after}"

    # A request that changes nothing writes nothing (ADR-0042 decision 3).
    stat = path.stat()
    SURFACES[surface](config, {"title": "Walk, second draft"})
    assert path.read_bytes() == after.encode("utf-8"), "a no-op rewrote the file"
    assert path.stat().st_mtime_ns == stat.st_mtime_ns, "a no-op touched the file"


@pytest.mark.parametrize(
    "surface",
    [
        pytest.param(
            name,
            marks=[
                pytest.mark.control(
                    reason="today's path already refused a tagged value on this surface, by a 500 rather than apply()'s reason; the property is the same on all six and the service and PATCH are red"
                )
            ]
            if name in ("cli.update", "mcp.kb_update", "rest.put")
            else [],
        )
        for name in sorted(SURFACES)
    ],
)
def test_a_tagged_value_is_refused_with_nothing_written(tmp_path: Path, surface: str):
    """Frontmatter Pyrite cannot model is refused with apply()'s reason
    (#760 (b)), before anything is written; the file is byte-identical."""
    config, path = _build(tmp_path, TAGGED_VALUE)
    before = path.read_bytes()

    with pytest.raises(PyriteError):
        SURFACES[surface](config, {"title": "Walk, second draft"})

    assert path.read_bytes() == before


# --------------------------------------------------------------------------
# The request's keys, resolved to the file's (acceptance 1)
# --------------------------------------------------------------------------


def test_an_alias_is_written_under_the_spelling_the_file_uses(tmp_path: Path):
    text, flags = SHAPES["alias"]
    config, path = _build(tmp_path, text, flags)
    before = _text(path)

    _service_update(config, {"actors": ["a", "b"]})

    after = _text(path)
    assert "actors:" not in after, after
    assert "participants:\n- a\n- b\n" in after, after
    assert_only_these_keys_changed(before, after, ("participants", "updated_at"))


@pytest.mark.control(
    reason="today's path also writes the declared target when the file has neither spelling; the control pins the rule beside the file's-spelling case"
)
def test_an_alias_the_file_holds_neither_way_is_written_as_the_declared_target(tmp_path: Path):
    text = f"---\nid: {ID}\ntitle: Walk\ntype: event\ndate: 2026-01-01\n---\n\nText.\n"
    config, path = _build(tmp_path, text, frozenset({"event"}))

    _service_update(config, {"participants": ["a"]})

    after = _text(path)
    # `participants` is the alias, `actors` the declared target of an event.
    assert "actors:\n- a\n" in after, after
    assert "participants:" not in after


def test_two_spellings_with_equal_values_make_one_operation(tmp_path: Path):
    text, flags = SHAPES["alias"]
    config, path = _build(tmp_path, text, flags)

    _service_update(config, {"actors": ["z"], "participants": ["z"]})

    after = _text(path)
    assert after.count("participants:") == 1 and "actors:" not in after, after
    assert "participants:\n- z\n" in after


def test_a_metadata_key_the_file_nests_stays_nested_and_a_new_one_goes_to_the_top(
    tmp_path: Path,
):
    text, _ = SHAPES["metadata-block"]
    config, path = _build(tmp_path, text)
    before = _text(path)

    _service_update(config, {"metadata": {"k": 2, "fresh": "v"}})

    after = _text(path)
    assert "metadata:\n  k: 2\n" in after, after
    assert "\nfresh: v\n" in after, after
    assert "  fresh:" not in after, "a key the file does not nest was nested under metadata:"
    assert_only_these_keys_changed(before, after, ("metadata", "fresh", "updated_at"))


def test_an_undeclared_key_is_a_top_level_key_never_nested_under_metadata(tmp_path: Path):
    """Decision 6: update never moves a key into ``metadata:`` (#178)."""
    text, _ = SHAPES["bom"]
    config, path = _build(tmp_path, text)
    before = _text(path)

    _service_update(config, {"decision": "yes"})

    after = _text(path)
    assert "metadata:" not in after, after
    assert re.search(r'\ndecision: "yes"\n', after), (
        after
    )  # quoted: a YAML 1.1 reader would misread
    assert_only_these_keys_changed(before, after, ("decision",))


@pytest.mark.control(
    reason="today's path merges an undeclared key into metadata too; the control pins where the key goes now that the top level is the default"
)
def test_a_lifted_metadata_key_the_file_holds_under_metadata_resolves_there(tmp_path: Path):
    """Spike surprise 4: a read lifts ``rank`` out of ``metadata:``, so a caller
    that sets ``rank`` means the key it read; it goes where the file holds it."""
    text = f"---\nid: {ID}\ntitle: Walk\ntype: note\nmetadata:\n  rank: 3\n---\n\nText.\n"
    config, path = _build(tmp_path, text)
    before = _text(path)

    _update_entry(config, {"rank": 4})

    after = _text(path)
    assert "metadata:\n  rank: 4\n" in after, after
    assert "\nrank:" not in after, "a second, top-level rank was added beside metadata.rank"
    assert_only_these_keys_changed(before, after, ("metadata",))


# --------------------------------------------------------------------------
# Bookkeeping in the file's own form (acceptance 4)
# --------------------------------------------------------------------------


def test_a_quoted_updated_at_is_restamped_as_a_quoted_string_in_the_same_layout(tmp_path: Path):
    text, _ = SHAPES["quoted-updated-at"]
    config, path = _build(tmp_path, text)

    _service_update(config, {"title": "Walk, second draft"})

    line = next(l for l in _text(path).splitlines() if l.startswith("updated_at:"))
    assert re.fullmatch(r"updated_at: '\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z'", line), line
    assert line != "updated_at: '2026-01-01T00:00:00Z'", "the stamp did not move"


@pytest.mark.control(
    reason="today's dump already wrote a bare timestamp in this form; the control pins the house spelling the new emitter must keep"
)
def test_a_bare_updated_at_is_restamped_bare_in_the_house_spelling(tmp_path: Path):
    text, _ = SHAPES["bare-updated-at"]
    config, path = _build(tmp_path, text)

    _service_update(config, {"title": "Walk, second draft"})

    line = next(l for l in _text(path).splitlines() if l.startswith("updated_at:"))
    assert re.fullmatch(r"updated_at: \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\+00:00", line), line
    assert line != "updated_at: 2026-01-01 00:00:00+00:00", "the stamp did not move"


@pytest.mark.parametrize("spelled", ["2026-01-02T10:00:00", "2026-01-02T10:00:00Z", "2026-01-02"])
def test_a_timestamp_read_and_written_back_unchanged_leaves_the_bytes_unchanged(
    tmp_path: Path, spelled: str
):
    """#969 review: a read reports the loader's spelling of a bare timestamp
    (`T` kept, `Z` read as `+00:00`); sending it back is the same instant
    (ADR-0042 decision 3's equality), so nothing is written."""
    text = f"---\nid: {ID}\ntitle: Walk\ntype: event\ndate: {spelled}\n---\n\nText.\n"
    config, path = _build(tmp_path, text, frozenset({"event"}))
    before = path.read_bytes()
    read = _with_service(config, lambda svc: svc.get_entry(ID, KB, readable_kbs=None))
    assert read["date"] is not None

    _service_update(config, {"date": read["date"]})

    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "spelled",
    [
        "2026-01-15",
        "2026-01-15T10:00:00",
        pytest.param(
            "2026-01-15 10:00:00Z",
            marks=pytest.mark.control(
                reason="a timestamp with a zone already compared equal through apply's own rule; the control keeps the shape beside the two that were written"
            ),
        ),
    ],
)
def test_a_created_at_read_and_written_back_is_the_same_instant_and_writes_nothing(
    tmp_path: Path, spelled: str
):
    """The round-trip gate's `created_as_date.md` (#955 P4): `created_at` and
    `updated_at` are parsed as instants on the way in (`_value_for_file`), so
    an echo of a bare date arrived as the datetime at its midnight and was
    written as `2026-01-15 00:00:00+00:00`, with `updated_at` stamped for
    it. The same instant in another spelling is the file's value (decision
    3): nothing is written."""
    text = (
        f"---\nid: {ID}\ntitle: Walk\ntype: note\ncreated_at: {spelled}\n"
        f"updated_at: {spelled}\n---\n\nText.\n"
    )
    config, path = _build(tmp_path, text)
    before = path.read_bytes()
    read = _with_service(config, lambda svc: svc.get_entry(ID, KB, readable_kbs=None))

    _service_update(config, {"created_at": read["created_at"], "updated_at": read["updated_at"]})

    assert path.read_bytes() == before


@pytest.mark.control(
    reason="#46 already keeps a file from growing updated_at; the control guards decision 7 against a stamp written as a new key"
)
def test_a_file_without_updated_at_never_gains_it(tmp_path: Path):
    text, _ = SHAPES["bom"]
    config, path = _build(tmp_path, text)

    _service_update(config, {"title": "Walk, second draft"})

    assert "updated_at" not in _text(path)


def test_updated_at_is_not_stamped_when_nothing_changed(tmp_path: Path):
    text, _ = SHAPES["bare-updated-at"]
    config, path = _build(tmp_path, text)
    before = path.read_bytes()

    _service_update(config, {"title": "Walk"})

    assert path.read_bytes() == before


# --------------------------------------------------------------------------
# Publishing (acceptance 5): the compare, the retry, the conflict, no git
# --------------------------------------------------------------------------


def _hook_that_edits_the_file(path: Path, times: int) -> Callable[..., Any]:
    """A before_save hook standing in for another writer between the read
    and the replace: it appends a key to the file by hand, ``times`` times."""
    calls = {"n": 0}

    def hook(entry, _ctx):
        if calls["n"] < times:
            calls["n"] += 1
            text = path.read_text(encoding="utf-8")
            path.write_text(
                text.replace("\n---\n\n", f"\nhand{calls['n']}: {calls['n']}\n---\n\n", 1),
                encoding="utf-8",
            )
        return None

    return hook


def test_a_change_made_between_the_read_and_the_replace_is_kept(tmp_path: Path):
    """Decision 10 and Q2: on FileChanged the file is re-read and the
    operations re-applied; the other writer's key and this one's both land."""
    text, _ = SHAPES["bom"]
    config, path = _build(tmp_path, text)
    db = PyriteDB(config.settings.index_path)
    try:
        svc = KBService(config, db)
        svc.hook_runner.register_core_hook("before_save", _hook_that_edits_the_file(path, 1))
        svc.update(ID, KB, {"title": "Walk, second draft"})
    finally:
        db.close()

    after = _text(path)
    assert "title: Walk, second draft" in after
    assert "\nhand1: 1\n" in after, "the other writer's change was lost"


def test_a_file_that_keeps_changing_is_a_conflict_after_five_retries(tmp_path: Path):
    from pyrite.exceptions import ConflictError

    text, _ = SHAPES["bom"]
    config, path = _build(tmp_path, text)
    db = PyriteDB(config.settings.index_path)
    try:
        svc = KBService(config, db)
        svc.hook_runner.register_core_hook("before_save", _hook_that_edits_the_file(path, 99))
        with pytest.raises(ConflictError) as info:
            svc.update(ID, KB, {"title": "Walk, second draft"})
    finally:
        db.close()
    assert info.value.error_code == "CONFLICT"
    assert "title: Walk, second draft" not in _text(path)


def test_a_kb_outside_git_is_written_without_a_lock_and_the_result_says_so(tmp_path: Path):
    """D1 (b): compare-before-replace with no lock; ``locked: false``."""
    text, _ = SHAPES["crlf-comments"]
    config, path = _build(tmp_path, text, frozenset({"crlf"}), git=False)
    before = _text(path)

    written = _service_update(config, {"title": "Walk, second draft"})

    after = _text(path)
    assert_only_these_keys_changed(before, after, TITLE_EDIT_KEYS)
    assert written.locked is False
    assert written.changed is True


def test_a_kb_inside_git_reports_the_write_as_locked(tmp_path: Path):
    text, _ = SHAPES["bom"]
    config, path = _build(tmp_path, text)
    before = _text(path)

    written = _service_update(config, {"title": "Walk, second draft"})

    assert_only_these_keys_changed(before, _text(path), TITLE_EDIT_KEYS)
    assert written.locked is True


def test_a_no_op_result_says_unchanged(tmp_path: Path):
    text, _ = SHAPES["bom"]
    config, path = _build(tmp_path, text)
    before, stat = path.read_bytes(), path.stat()

    written = _service_update(config, {"title": "Walk"})

    assert path.read_bytes() == before and path.stat().st_mtime_ns == stat.st_mtime_ns
    assert written.changed is False


def test_a_result_the_index_cannot_store_is_refused_with_nothing_written(tmp_path: Path):
    """Spike surprise 2: the row is built from the new bytes before the
    publish, so a write is never reported as failed after it is published."""
    text, _ = SHAPES["bom"]
    config, path = _build(tmp_path, text)
    before = path.read_bytes()

    with pytest.raises(PyriteError):
        _update_entry(config, {"title": ["a", "list", "is", "no", "title"]})

    assert path.read_bytes() == before


# --------------------------------------------------------------------------
# A refusal is decided on the keys the update will write (#971 fix round 1)
# --------------------------------------------------------------------------

#: A key refused at the top level is refused under `metadata` too: the mapping
#: (decision 6) would write it at the top level, where the base kept it nested
#: and harmless. value, and the surfaces whose rule it is: `type` and `body`
#: are refused for every caller; `links` and `id` are managed fields, refused
#: for a surface (`restrict`) and open to the in-process `update_entry`, as at
#: the top level.
NESTED_REFUSALS: dict[str, tuple[Any, frozenset[str]]] = {
    "type": ("hacked", frozenset(SURFACES)),
    "body": ("a stray body key", frozenset(SURFACES)),
    "links": (
        [{"target": "foo", "relation": "related_to"}],
        frozenset(SURFACES) - {"service.update_entry"},
    ),
    "id": ("other", frozenset(SURFACES) - {"service.update_entry"}),
}


@pytest.mark.parametrize("surface", sorted(SURFACES))
@pytest.mark.parametrize("key", sorted(NESTED_REFUSALS))
def test_a_refused_key_under_metadata_is_refused_with_nothing_written(
    tmp_path: Path, key: str, surface: str
):
    """The review of #971: `metadata.links`, `metadata.type`, `metadata.id`
    and `metadata.body` escaped the refusals and were written at the top
    level (the id case left a stale index row). The refusal is decided on
    the operations the request maps to, after the `metadata` mapping."""
    value, where = NESTED_REFUSALS[key]
    if surface == "rest.patch":
        pytest.skip("PATCH's value is a string: it cannot carry a mapping")
    if surface not in where:
        pytest.skip(f"{key} is open to the in-process update_entry, as at the top level")
    text, _ = SHAPES["yaml11"]
    config, path = _build(tmp_path, text)
    before = path.read_bytes()

    with pytest.raises(PyriteError):
        SURFACES[surface](config, {"metadata": {key: value}})

    assert path.read_bytes() == before
    db = PyriteDB(config.settings.index_path)
    try:
        assert db.get_entry(ID, KB) is not None, "the index row moved"
    finally:
        db.close()


# --------------------------------------------------------------------------
# A timestamp keeps its spelling (#971 fix round 1)
# --------------------------------------------------------------------------

#: `updated_at` spellings a file may carry, each with the pattern a refreshed
#: stamp must match: the separator, the zone form and the fraction's digits are
#: the file's, bare or quoted. The review of #971: a bare `2026-10-01T12:00:00Z`
#: became `2026-10-16 00:32:06+00:00` on any edit.
UPDATED_AT_SPELLINGS: dict[str, tuple[str, str]] = {
    "bare-T-Z": ("2026-01-01T00:00:00Z", r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ"),
    "bare-space-offset": ("2026-01-01 00:00:00+00:00", r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\+00:00"),
    "bare-T-fraction-offset": (
        "2026-01-01T00:00:00.123+02:00",
        r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}\+02:00",
    ),
    "bare-T-naive": ("2026-01-01T00:00:00", r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d"),
    "bare-date": ("2026-01-01", r"\d{4}-\d\d-\d\d"),
    "quoted-T-Z": ("'2026-01-01T00:00:00Z'", r"'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ'"),
    "quoted-space": ('"2026-01-01 00:00:00"', r'"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d"'),
}

#: The spellings the round's base already kept: the house form (the bare
#: fixture the review named) and the quoted ones, which `_updated_at_stamp`
#: handled from the start.
_SPELLINGS_ALREADY_KEPT = frozenset({"bare-space-offset", "quoted-T-Z", "quoted-space"})

#: #990: a zone spelled `+02` or `+0530`, and a fraction in a CRLF file. The
#: offset regex knew only `±HH:MM` (a bare `+02` value became naive UTC, a
#: quoted one lost its zone), and the raw scalar was not found on a CRLF line
#: (so `.5Z` came back `.000000Z`).
UPDATED_AT_SPELLINGS_990: dict[str, tuple[str, str, bool]] = {
    "bare-T-hours-only-zone": (
        "2026-01-01T00:00:00+02",
        r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+02",
        False,
    ),
    "quoted-T-compact-zone": (
        "'2026-01-01T00:00:00+0530'",
        r"'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+0530'",
        False,
    ),
    "bare-T-fraction-Z-crlf": (
        "2026-01-01T00:00:00.5Z",
        r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\dZ",
        True,
    ),
}


@pytest.mark.parametrize("spelling", sorted(UPDATED_AT_SPELLINGS_990))
def test_a_refreshed_updated_at_keeps_an_unusual_zone_and_a_crlf_fraction(
    tmp_path: Path, spelling: str
):
    old, pattern, crlf = UPDATED_AT_SPELLINGS_990[spelling]
    text = f"---\nid: {ID}\ntitle: Walk\ntype: note\nupdated_at: {old}\n---\n\nText.\n"
    config, path = _build(tmp_path, text, frozenset({"crlf"}) if crlf else frozenset())
    before = _text(path)

    _service_update(config, {"title": "Walk, second draft"})

    after = _text(path)
    line = next(l for l in after.splitlines() if l.startswith("updated_at:"))
    assert re.fullmatch("updated_at: " + pattern, line), line
    assert line != f"updated_at: {old}", "the stamp did not move"
    assert_only_these_keys_changed(before, after, TITLE_EDIT_KEYS)


@pytest.mark.parametrize("surface", sorted(SURFACES))
@pytest.mark.parametrize(
    "spelling",
    [
        pytest.param(
            name,
            marks=[
                pytest.mark.control(
                    reason="the house spelling and a quoted value were already kept; the control pins them beside the forms the round fixes"
                )
            ]
            if name in _SPELLINGS_ALREADY_KEPT
            else [],
        )
        for name in sorted(UPDATED_AT_SPELLINGS)
    ],
)
def test_a_refreshed_updated_at_keeps_the_files_spelling(
    tmp_path: Path, spelling: str, surface: str
):
    old, pattern = UPDATED_AT_SPELLINGS[spelling]
    text = f"---\nid: {ID}\ntitle: Walk\ntype: note\nupdated_at: {old}\n---\n\nText.\n"
    config, path = _build(tmp_path, text)

    SURFACES[surface](config, {"title": "Walk, second draft"})

    after = _text(path)
    line = next(l for l in after.splitlines() if l.startswith("updated_at:"))
    assert re.fullmatch("updated_at: " + pattern, line), line
    assert line != f"updated_at: {old}", "the stamp did not move"
    assert_only_these_keys_changed(text, after, TITLE_EDIT_KEYS)


#: (the file's `created_at`, what the caller sends, the line the file holds
#: after). A timestamp the caller sends is written as sent: its own separator,
#: zone form and fraction digits; bare where the file's value was bare, quoted
#: where it was quoted.
SENT_TIMESTAMPS = [
    ("2026-01-01 00:00:00+00:00", "2026-10-02T12:00:00Z", "2026-10-02T12:00:00Z"),
    pytest.param(
        "2026-01-01T00:00:00Z",
        "2026-10-02 12:00:00+00:00",
        "2026-10-02 12:00:00+00:00",
        marks=pytest.mark.control(
            reason="the value sent is spelled the way the old emitter respelled every timestamp, so the base writes it by coincidence; the control pins that the house spelling is written as sent too"
        ),
    ),
    ("2026-01-01T00:00:00Z", "2026-10-02T12:00:00.500+01:00", "2026-10-02T12:00:00.500+01:00"),
    ("'2026-01-01T00:00:00Z'", "2026-10-02T12:00:00Z", "'2026-10-02T12:00:00Z'"),
]


@pytest.mark.parametrize("surface", sorted(SURFACES))
@pytest.mark.parametrize(("old", "sent", "expected"), SENT_TIMESTAMPS)
def test_a_timestamp_the_caller_sends_is_written_as_sent(
    tmp_path: Path, surface: str, old: str, sent: str, expected: str
):
    if surface == "mcp.kb_update":
        pytest.skip("kb_update sets a timestamp aside as `ignored` (#455): it never writes one")
    text = f"---\nid: {ID}\ntitle: Walk\ntype: note\ncreated_at: {old}\n---\n\nText.\n"
    config, path = _build(tmp_path, text)
    request: dict[str, Any] = {"created_at": sent}
    if surface == "rest.put":
        # PUT's model has no timestamp field; a key under `metadata` is a
        # top-level key of the file (decision 6).
        request = {"metadata": request}

    SURFACES[surface](config, request)

    after = _text(path)
    assert f"created_at: {expected}\n" in after, after
    assert_only_these_keys_changed(text, after, ("created_at",))


#: What a caller may send for a timestamp key: YAML's timestamp form (#990).
#: `datetime.fromisoformat` accepts more (a week date, an hour-only time, a
#: compact date), and those were written as plain strings or ints.
NOT_A_YAML_TIMESTAMP = [
    pytest.param(
        "yesterday",
        marks=pytest.mark.control(
            reason="refused since #971 fix round 1 (no instant at all); the control pins it beside the forms fromisoformat let through"
        ),
    ),
    "2026-W40-1",
    "2026-10-02T12",
    "20261002",
    "2026-10-02 12:00",
]


@pytest.mark.parametrize("surface", sorted(SURFACES))
@pytest.mark.parametrize("sent", NOT_A_YAML_TIMESTAMP)
def test_a_timestamp_the_caller_sends_that_is_no_instant_is_refused(
    tmp_path: Path, surface: str, sent: str
):
    """Written as sent means refused when what was sent is not a YAML
    timestamp (`YYYY-MM-DD`, then optionally `T` or a space, `HH:MM:SS`, a
    fraction, `Z` or an offset): `parse_datetime` used to read `yesterday`
    as now and write that, and `fromisoformat` let a week date through."""
    if surface == "mcp.kb_update":
        pytest.skip("kb_update sets a timestamp aside as `ignored` (#455): it never writes one")
    text = (
        f"---\nid: {ID}\ntitle: Walk\ntype: note\ncreated_at: 2026-01-01T00:00:00Z\n---\n\nText.\n"
    )
    config, path = _build(tmp_path, text)
    before = path.read_bytes()
    request: dict[str, Any] = {"created_at": sent}
    if surface == "rest.put":
        request = {"metadata": request}

    with pytest.raises(PyriteError):
        SURFACES[surface](config, request)

    assert path.read_bytes() == before


# --------------------------------------------------------------------------
# The templated move (decision 5) when the target is taken (#990)
# --------------------------------------------------------------------------

#: A kb.yaml type whose folder is templated by a field it declares: a
#: generic entry keeps `status` in `metadata`, where the template reads it.
TEMPLATED_KB_YAML = (
    "name: walk\nkb_type: generic\ntypes:\n  thing:\n"
    "    subdirectory: things/{status}\n    fields:\n      status:\n        type: text\n"
)


def test_a_templated_move_whose_target_exists_keeps_the_file_and_says_so(tmp_path: Path):
    """The update is published at the old path before the move; when the
    target already holds a file the move is refused as `not_moved` with a
    reason naming the KB-relative target, never a raw `FileExistsError`
    with an absolute path, and the index row points at the file's real
    place (it stayed stale until the next sync before)."""
    text = f"---\nid: {ID}\ntitle: Walk\ntype: thing\nstatus: open\n---\n\nText.\n"
    config, path = _build(tmp_path, text)
    kb_root = config.knowledge_bases[0].path
    (kb_root / "kb.yaml").write_text(TEMPLATED_KB_YAML, encoding="utf-8")
    # A fresh KBConfig: the one `_build` made read kb.yaml before the type
    # was templated and keeps that schema.
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name=KB, path=kb_root, kb_type="generic")],
        settings=config.settings,
    )
    moved_to = kb_root / "things" / "done" / f"{ID}.md"
    moved_to.parent.mkdir(parents=True)
    moved_to.write_text(
        "---\nid: other\ntitle: Squatter\ntype: thing\nstatus: done\n---\n\nTaken.\n"
    )
    # `_build` wrote the file under notes/; the templated folder for `open`
    # is things/open, so give the file its place first.
    open_dir = kb_root / "things" / "open"
    open_dir.mkdir(parents=True)
    path = path.rename(open_dir / path.name)
    db = PyriteDB(config.settings.index_path)
    try:
        IndexManager(db, config).index_all()
        written = KBService(config, db).update(ID, KB, {"status": "done"})
        row = db.get_entry(ID, KB)
    finally:
        db.close()

    assert written.changed is True
    assert written.not_moved is not None and "things/done" in written.not_moved, written.not_moved
    assert str(tmp_path) not in written.not_moved
    assert path.exists() and "status: done" in _text(path)
    assert _text(moved_to).startswith("---\nid: other"), "the squatter was overwritten"
    assert row is not None and Path(row["file_path"]) == path


# --------------------------------------------------------------------------
# A storage fault is a typed refusal (#971 fix round 1, before dev)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("surface", sorted(SURFACES))
def test_a_hard_linked_file_is_refused_as_a_storage_error(tmp_path: Path, surface: str):
    """`atomic_write_text` refuses an `expect=` write to a file with two names
    (`HardLinked`, ADR-0042 §10a A2). The update reports it as a
    `StorageError` (`STORAGE_ERROR`), whose public text names no path
    (ADR-0037 §3), and writes nothing. The base wrote the file."""
    from pyrite.exceptions import StorageError

    text, flags = SHAPES["root-file"]
    config, path = _build(tmp_path, text, flags)
    os.link(path, path.with_name("another-name.bak"))
    before = path.read_bytes()

    with pytest.raises(PyriteError) as info:
        SURFACES[surface](config, {"title": "Walk, second draft"})

    if surface.startswith("service."):
        # The service's own exception carries the detail for the log; what
        # a transport may show is `public_text`.
        assert isinstance(info.value, StorageError)
        assert info.value.retryable is False
        assert str(tmp_path) not in public_text(info.value)
    else:
        assert str(tmp_path) not in str(info.value), str(info.value)
        assert "STORAGE_ERROR" in str(info.value), str(info.value)
    assert path.read_bytes() == before


def test_mcp_kb_update_carries_its_own_old_code_for_a_storage_fault(tmp_path: Path):
    """`kb_update` answered every failure it did not name with `UPDATE_FAILED`
    before ADR-0037, so that is its `legacy_error_code` for a storage fault
    too; `REQUEST_REFUSED` (the module-wide fallback a plain `_refusal` would
    carry) is a code `kb_update` never gave (#990, the #992 merge review)."""
    from pyrite.server.mcp_server import PyriteMCPServer

    text, flags = SHAPES["root-file"]
    config, path = _build(tmp_path, text, flags)
    os.link(path, path.with_name("another-name.bak"))
    server = PyriteMCPServer(config, tier="write")
    try:
        result = server._dispatch_tool(
            "kb_update", {"kb_name": KB, "entry_id": ID, "title": "Walk, second draft"}
        )
    finally:
        server.close()
    assert result["error_code"] == "STORAGE_ERROR", result
    assert result["legacy_error_code"] == "UPDATE_FAILED", result
    assert result["retryable"] is False


def test_a_lock_held_too_long_is_a_busy_refusal(tmp_path: Path, monkeypatch):
    """`LockTimeout` is `StorageBusyError` (`retryable`): the same call can
    succeed once the other writer lets go. The lock is real, held by another
    thread; the wait is the service's own bound, shortened for the test."""
    from pyrite.exceptions import StorageBusyError
    from pyrite.services import kb_service
    from pyrite.utils.file_lock import file_lock

    text, _ = SHAPES["bom"]
    config, path = _build(tmp_path, text)
    before = path.read_bytes()
    # `raising=False`: without the bound (the base) the update takes no lock
    # at all and the test is red for that, not for a missing name.
    monkeypatch.setattr(kb_service, "_LOCK_TIMEOUT", 0.2, raising=False)
    held = threading.Event()
    release = threading.Event()

    def hold():
        with file_lock(path):
            held.set()
            release.wait(5)

    other = threading.Thread(target=hold)
    other.start()
    try:
        assert held.wait(5)
        with pytest.raises(StorageBusyError):
            _service_update(config, {"title": "Walk, second draft"})
    finally:
        release.set()
        other.join(5)
    assert path.read_bytes() == before


# --------------------------------------------------------------------------
# The in-process callers (the spike's §4), on hand-made files
# --------------------------------------------------------------------------

TASK = (
    "---\n"
    "# a task kept by hand\n"
    "id: t1\n"
    "title: Task one   # keep\n"
    "type: task\n"
    "status: open\n"
    "priority: 3\n"
    "\n"
    "dependencies: [t0]\n"
    "notes: |\n"
    "  two lines\n"
    "  of notes\n"
    "---\n"
    "\n"
    "Body of the task.\n"
)

PARENT = "---\nid: t0\ntitle: Parent\ntype: task\nstatus: done\nevidence: [e0]\n---\n\nParent.\n"


def _task_kb(tmp_path: Path, files: dict[str, str]):
    kb_path = tmp_path / "tasks"
    (kb_path / "tasks").mkdir(parents=True)
    _git_init(kb_path)
    for name, text in files.items():
        (kb_path / "tasks" / name).write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="tasks", path=kb_path, kb_type="task")],
        settings=Settings(index_path=tmp_path / "index.db", auto_embed=False),
    )
    db = PyriteDB(config.settings.index_path)
    IndexManager(db, config).index_all()
    return config, db, kb_path / "tasks"


def _crlf_kept(after: str) -> None:
    assert after.count("\r\n") == after.count("\n"), "a bare LF was written into a CRLF file"


def test_task_service_update_task_changes_only_what_it_names(tmp_path: Path):
    from pyrite.services.task_service import TaskService

    config, db, folder = _task_kb(tmp_path, {"t1.md": TASK, "t0.md": PARENT})
    path = folder / "t1.md"
    before = _text(path)
    try:
        TaskService(config, db).update_task(
            "t1", "tasks", status="claimed", comment="mine", by="me"
        )
    finally:
        db.close()
    after = _text(path)
    assert "status: claimed" in after
    assert "status_change_log:" in after
    assert_only_these_keys_changed(before, after, ("status", "status_change_log", "updated_at"))
    _crlf_kept(after)


def test_task_service_reset_task_changes_only_what_it_names(tmp_path: Path):
    from pyrite.services.task_service import TaskService

    text = TASK.replace("status: open", "status: in_progress").replace(
        "priority: 3", "priority: 3\nassignee: bot"
    )
    config, db, folder = _task_kb(tmp_path, {"t1.md": text, "t0.md": PARENT})
    path = folder / "t1.md"
    before = _text(path)
    try:
        TaskService(config, db).reset_task("t1", "tasks", reason="stale")
    finally:
        db.close()
    after = _text(path)
    assert "status: open" in after and "## Reset" in after
    assert_only_these_keys_changed(
        before, after, ("status", "assignee", "status_reason", "body", "updated_at")
    )
    _crlf_kept(after)


def test_task_service_checkpoint_task_changes_only_what_it_names(tmp_path: Path):
    from pyrite.services.task_service import TaskService

    config, db, folder = _task_kb(tmp_path, {"t1.md": TASK, "t0.md": PARENT})
    path = folder / "t1.md"
    before = _text(path)
    try:
        TaskService(config, db).checkpoint_task(
            "t1", "tasks", "halfway", confidence=0.5, partial_evidence=["e1"]
        )
    finally:
        db.close()
    after = _text(path)
    assert "## Checkpoint" in after and "agent_context:" in after
    assert_only_these_keys_changed(
        before, after, ("body", "agent_context", "evidence", "updated_at")
    )
    _crlf_kept(after)


def test_task_service_migrate_relaxed_mode_changes_only_status_reason(tmp_path: Path):
    from pyrite.services.task_service import TaskService

    config, db, folder = _task_kb(tmp_path, {"t1.md": TASK, "t0.md": PARENT})
    (folder.parent / "kb.yaml").write_text(
        "name: tasks\nkb_type: task\ntypes:\n  task:\n    state_machine:\n"
        "      enforce_transitions: false\n      require_reason_on_transition: true\n"
        "      states: [open, in_progress, done]\n",
        encoding="utf-8",
    )
    config.get_kb("tasks").invalidate_schema_cache()
    path = folder / "t1.md"
    before = _text(path)
    try:
        result = TaskService(config, db).migrate_relaxed_mode("tasks")
    finally:
        db.close()
    after = _text(path)
    assert result["migrated"] >= 1, result
    assert "status_reason: pre-relaxed-mode" in after
    assert_only_these_keys_changed(before, after, ("status_reason", "updated_at"))
    _crlf_kept(after)


def test_task_service_unblock_dependents_changes_only_status(tmp_path: Path):
    from pyrite.services.task_service import TaskService

    text = TASK.replace("status: open", "status: blocked")
    config, db, folder = _task_kb(tmp_path, {"t1.md": text, "t0.md": PARENT})
    path = folder / "t1.md"
    before = _text(path)
    try:
        unblocked = TaskService(config, db).unblock_dependents("t0", "tasks")
    finally:
        db.close()
    after = _text(path)
    assert [u["id"] for u in unblocked] == ["t1"], unblocked
    assert "status: in_progress" in after
    assert_only_these_keys_changed(before, after, ("status", "updated_at"))
    _crlf_kept(after)


def test_task_service_aggregate_evidence_changes_only_the_parents_evidence(tmp_path: Path):
    from pyrite.services.task_service import TaskService

    child = TASK.replace("dependencies: [t0]", "parent: t0\nevidence: [e1]")
    config, db, folder = _task_kb(tmp_path, {"t1.md": child, "t0.md": PARENT})
    path = folder / "t0.md"
    before = _text(path)
    try:
        result = TaskService(config, db).aggregate_evidence_to_parent("t1", "tasks")
    finally:
        db.close()
    after = _text(path)
    assert result and result["evidence_added"] == 1, result
    assert "e1" in after
    assert_only_these_keys_changed(before, after, ("evidence", "updated_at"))
    _crlf_kept(after)


def test_task_service_link_qa_assessment_changes_only_evidence(tmp_path: Path):
    from pyrite.services.task_service import TaskService

    config, db, folder = _task_kb(tmp_path, {"t1.md": TASK, "t0.md": PARENT})
    path = folder / "t1.md"
    before = _text(path)
    try:
        result = TaskService(config, db).link_qa_assessment("t1", "qa-1", "tasks")
    finally:
        db.close()
    after = _text(path)
    assert result["linked"] is True
    assert "qa-1" in after
    assert_only_these_keys_changed(before, after, ("evidence", "updated_at"))
    _crlf_kept(after)


def test_qa_fix_tag_normalisation_changes_only_tags(tmp_path: Path):
    from pyrite.services.access_policy import UNSCOPED
    from pyrite.services.qa_service import QAService

    text = (
        "---\n# hand\nid: n1\ntitle: N one   # keep\ntype: note\ntags: [Foo, bar]  # trailing\n"
        "extra: 1\n---\n\nText.\n"
    )
    config, path = _build(tmp_path, text.replace(f"id: {ID}", "id: n1"))
    path = path.parent / "n1.md"
    _write(path, text, frozenset({"crlf"}))
    db = PyriteDB(config.settings.index_path)
    try:
        IndexManager(db, config).index_all()
        before = _text(path)
        result = QAService(config, db).fix_kb(
            KB, dry_run=False, fix_rules=["tag_case"], readable_kbs=UNSCOPED
        )
    finally:
        db.close()
    after = _text(path)
    assert result["fixed_count"] == 1, result
    assert "tags: [foo, bar]  # trailing" in after, after
    assert_only_these_keys_changed(before, after, ("tags", "updated_at"))
    _crlf_kept(after)


def test_qa_fix_broken_link_changes_only_the_links(tmp_path: Path):
    from pyrite.services.access_policy import UNSCOPED
    from pyrite.services.qa_service import QAService

    target = f"---\nid: {ID}\ntitle: Walk\ntype: note\n---\n\nText.\n"
    linker = (
        "---\n# hand\nid: n1\ntitle: N one   # keep\ntype: note\nlinks:\n"
        f"- target: {ID[:-1]}x\n  relation: related_to\nextra: 1\n---\n\nText.\n"
    )
    config, path = _build(tmp_path, target)
    link_path = path.parent / "n1.md"
    _write(link_path, linker, frozenset({"crlf"}))
    db = PyriteDB(config.settings.index_path)
    try:
        IndexManager(db, config).index_all()
        before = _text(link_path)
        result = QAService(config, db).fix_kb(
            KB, dry_run=False, fix_rules=["broken_link"], readable_kbs=UNSCOPED
        )
    finally:
        db.close()
    after = _text(link_path)
    assert result["fixed_count"] == 1, result
    assert f"- target: {ID}\r\n" in after, after
    assert_only_these_keys_changed(before, after, ("links", "updated_at"))
    _crlf_kept(after)


def test_sw_prioritize_changes_only_rank(tmp_path: Path):
    pytest.importorskip("pyrite_software_kb.entry_types")
    from pyrite.plugins.registry import PluginRegistry
    from pyrite_software_kb.plugin import SoftwareKBPlugin

    plugin = SoftwareKBPlugin()
    PluginRegistry().register(plugin)
    kb_path = tmp_path / "sw"
    (kb_path / "backlog").mkdir(parents=True)
    _git_init(kb_path)
    item = (
        "---\n# hand\nid: b1\ntitle: Item one   # keep\ntype: backlog_item\nkind: feature\n"
        "status: proposed\npriority: medium\neffort: small\nrank: 100\n---\n\nText.\n"
    )
    (kb_path / "backlog" / "b1.md").write_bytes(item.replace("\n", "\r\n").encode("utf-8"))
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="sw", path=kb_path, kb_type="software")],
        settings=Settings(index_path=tmp_path / "index.db", auto_embed=False),
    )
    db = PyriteDB(config.settings.index_path)
    IndexManager(db, config).index_all()
    db.close()
    path = kb_path / "backlog" / "b1.md"
    before = _text(path)
    from pyrite.plugins.context import PluginContext

    db = PyriteDB(config.settings.index_path)
    # The plugin takes its database from the context the dispatcher injects.
    plugin.ctx = PluginContext(
        config=config, db=db, kb_name="sw", user="", operation="update", kb_type="software"
    )
    try:
        with patch("pyrite.config.load_config", return_value=config):
            result = plugin._mcp_prioritize({"kb_name": "sw", "item_ids": ["b1"]})
    finally:
        plugin.ctx = None
        db.close()
    after = _text(path)
    assert (
        result["updated"] == [{"id": "b1", "rank": 100}] or "error" not in result["updated"][0]
    ), result
    assert "rank: 100" in after
    assert_only_these_keys_changed(before, after, ("rank", "updated_at"))
    _crlf_kept(after)
