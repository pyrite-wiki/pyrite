"""A single-entry read returns what the entry's file holds now (#753).

ADR-0042 decision 9: ``get`` reads the file's current bytes and names them
(``content_hash``, the SHA-256 the index already uses); lists and search stay
index reads. Before this, every single-entry read returned the body the index
stored at the last sync, so an edit made since -- by hand, by another tool, by
``git pull`` -- was missing from the read, and ``get`` then ``update
--body-file`` wrote the old body over it with ``updated: true``.

One rule, one place: ``KBService.get_entry``. The acceptance test runs the same
hand edit through every surface that calls it -- CLI ``get``, ``pyrite-read
get``, MCP ``kb_get`` and ``kb_read_body``, REST ``GET /api/entries/{id}`` with
and without ``fields`` -- on the real entry points (Typer's ``CliRunner``, the
MCP dispatcher, a FastAPI ``TestClient``). The rest pins each regime the groom
named, at the service or through the surface that enters it.

Reproduction and the round-trip regression idea: @sb123sb123, PR #754.
"""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")

from typer.testing import CliRunner

from pyrite.cli import app as cli_app
from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager

runner = CliRunner()

KB = "demo"
ENTRY = "multi"

ORIGINAL = """\
---
id: multi
title: Multi
type: note
tags:
- before
---

Intro.

### Section A

text a
"""

#: The same entry after an edit Pyrite did not make: a new title and tag, and
#: a section appended. No index sync follows.
HAND_EDITED = """\
---
id: multi
title: Multi, edited by hand
type: note
tags:
- after
---

Intro.

### Section A

text a

### Added by hand

hand text
"""


def _config(tmp_path: Path, *kbs: KBConfig) -> PyriteConfig:
    return PyriteConfig(
        knowledge_bases=list(kbs),
        settings=Settings(index_path=tmp_path / "index.db", auto_embed=False),
    )


def _index(config: PyriteConfig) -> None:
    db = PyriteDB(config.settings.index_path)
    try:
        IndexManager(db, config).index_all()
    finally:
        db.close()


@pytest.fixture
def env(tmp_path):
    """One KB, one entry indexed as ORIGINAL, then edited by hand with no sync."""
    kb_path = tmp_path / KB
    (kb_path / "notes").mkdir(parents=True)
    path = kb_path / "notes" / f"{ENTRY}.md"
    path.write_text(ORIGINAL)
    config = _config(tmp_path, KBConfig(name=KB, path=kb_path, kb_type="generic"))
    _index(config)
    path.write_text(HAND_EDITED)
    return {"config": config, "kb_path": kb_path, "path": path, "tmp_path": tmp_path}


@pytest.fixture
def svc(env):
    db = PyriteDB(env["config"].settings.index_path)
    try:
        yield KBService(env["config"], db)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# One reader per surface. Each returns the read as the surface returned it.
# ---------------------------------------------------------------------------


def _cli(config, args, stdin=None):
    with (
        patch("pyrite.cli.load_config", return_value=config),
        patch("pyrite.cli.context.load_config", return_value=config),
    ):
        return runner.invoke(cli_app, args, input=stdin)


def cli_get(env, entry_id=ENTRY):
    result = _cli(env["config"], ["get", entry_id, "-k", KB, "--format", "json"])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def read_cli_get(env, entry_id=ENTRY):
    from pyrite.read_cli import app as read_app

    with patch("pyrite.read_cli.load_config", return_value=env["config"]):
        result = runner.invoke(read_app, ["get", entry_id, "--kb", KB, "--format", "json"])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def _mcp(env, tool, args):
    from pyrite.server.mcp_server import PyriteMCPServer

    server = PyriteMCPServer(env["config"], tier="admin")
    try:
        return server._dispatch_tool(tool, args)
    finally:
        server.close()


def mcp_kb_get(env, entry_id=ENTRY):
    res = _mcp(env, "kb_get", {"entry_id": entry_id, "kb_name": KB})
    assert "entry" in res, res
    return res["entry"]


def mcp_kb_read_body(env, entry_id=ENTRY):
    res = _mcp(env, "kb_read_body", {"entry_id": entry_id, "kb_name": KB})
    assert "body" in res, res
    return {"body": res["body"]}


def _rest(env, url, **params):
    from starlette.testclient import TestClient

    from pyrite.server.api import create_app, get_config, get_db

    config = env["config"]
    application = create_app(config=config)
    db = PyriteDB(config.settings.index_path)
    application.dependency_overrides[get_config] = lambda: config
    application.dependency_overrides[get_db] = lambda: db
    try:
        return TestClient(application).get(url, params=params)
    finally:
        db.close()
        state_db = getattr(application.state, "pyrite_db", None)
        if state_db is not None and state_db is not db:
            state_db.close()


def rest_get(env, entry_id=ENTRY):
    resp = _rest(env, f"/api/entries/{entry_id}", kb=KB)
    assert resp.status_code == 200, resp.text
    return resp.json()


def rest_get_fields(env, entry_id=ENTRY):
    resp = _rest(env, f"/api/entries/{entry_id}", kb=KB, fields="title,body,tags,content_hash")
    assert resp.status_code == 200, resp.text
    return resp.json()


SURFACES = [cli_get, read_cli_get, mcp_kb_get, mcp_kb_read_body, rest_get, rest_get_fields]


# ---------------------------------------------------------------------------
# Acceptance
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("read", SURFACES, ids=lambda f: f.__name__)
def test_every_single_entry_read_returns_the_file_as_it_is_now(env, read):
    got = read(env)
    assert "### Added by hand" in got["body"] and got["body"].endswith("hand text")
    if read is not mcp_kb_read_body:  # a body chunk carries nothing else
        assert got["title"] == "Multi, edited by hand"
        assert got["tags"] == ["after"]


@pytest.mark.parametrize("read", [cli_get, read_cli_get, mcp_kb_get, rest_get_fields])
def test_the_read_names_the_bytes_it_read(env, read):
    expected = hashlib.sha256(env["path"].read_bytes()).hexdigest()
    assert read(env)["content_hash"] == expected


def test_cli_get_then_update_body_file_keeps_the_hand_edit(env):
    """The reported loss (#753): get, append a section, update --body-file."""
    body = cli_get(env)["body"] + "\n\n### Agent section\n\nagent text\n"
    body_file = env["tmp_path"] / "b.txt"
    body_file.write_text(body)
    result = _cli(env["config"], ["update", ENTRY, "-k", KB, "--body-file", str(body_file)])
    assert result.exit_code == 0, result.output
    text = env["path"].read_text()
    for line in ("### Added by hand", "hand text", "### Agent section", "agent text"):
        assert line in text, text


def test_mcp_get_then_update_keeps_the_hand_edit(env):
    body = mcp_kb_get(env)["body"] + "\n\n### Agent section\n\nagent text"
    res = _mcp(env, "kb_update", {"entry_id": ENTRY, "kb_name": KB, "body": body})
    assert "error" not in res, res
    text = env["path"].read_text()
    for line in ("### Added by hand", "hand text", "### Agent section", "agent text"):
        assert line in text, text


# ---------------------------------------------------------------------------
# Hand-written files: the body is the loader's body of the file as it is now
# ---------------------------------------------------------------------------

SHAPES = {
    # The loader reads text with universal newlines, as the index did: the
    # body's lines come back LF. The hash is of the CRLF bytes.
    "crlf": (
        b"---\r\nid: multi\r\ntitle: Multi\r\ntype: note\r\n---\r\n\r\nLine one.\r\nLine two.\r\n",
        "Line one.\nLine two.",
    ),
    "bom": (
        "﻿---\nid: multi\ntitle: Multi\ntype: note\n---\n\nAfter a BOM.\n".encode(),
        "After a BOM.",
    ),
    "no_final_newline": (
        b"---\nid: multi\ntitle: Multi\ntype: note\n---\n\nNo newline at the end",
        "No newline at the end",
    ),
    "dashes_in_body": (
        b"---\nid: multi\ntitle: Multi\ntype: note\n---\n\nAbove.\n\n---\n\nBelow.\n",
        "Above.\n\n---\n\nBelow.",
    ),
    "key_value_body": (
        b"---\nid: multi\ntitle: Multi\ntype: note\n---\n\nstatus: open\n",
        "status: open",
    ),
}


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_hand_written_shapes_read_as_written(env, svc, shape):
    raw, body = SHAPES[shape]
    env["path"].write_bytes(raw)
    got = svc.get_entry(ENTRY, KB, readable_kbs=None)
    assert got["body"] == body
    assert got["content_hash"] == hashlib.sha256(raw).hexdigest()


# ---------------------------------------------------------------------------
# Regimes
# ---------------------------------------------------------------------------


@contextmanager
def _files_opened():
    """Every file opened, as a Path: ``Path.read_bytes``/``read_text``/``open``
    go through ``io.open``, a bare ``open()`` through ``builtins.open``; each
    open is recorded once."""
    import builtins
    import io

    opened: list[Path] = []
    real = io.open

    def spy(file, *a, **k):
        if isinstance(file, (str, os.PathLike)):
            opened.append(Path(file))
        return real(file, *a, **k)

    with patch.object(io, "open", spy), patch.object(builtins, "open", spy):
        yield opened


@contextmanager
def _directory_walks():
    """Every directory listing or walk started (``rglob``, ``glob``,
    ``iterdir``, ``Path.walk``, ``os.walk``, ``os.scandir``, ``os.listdir``,
    ``glob.glob``): the cost a lookup by name pays on a large KB."""
    import glob as glob_mod

    walks: list[str] = []
    targets = [
        (Path, "rglob"),
        (Path, "glob"),
        (Path, "iterdir"),
        (os, "walk"),
        (os, "scandir"),
        (os, "listdir"),
        (glob_mod, "glob"),
        (glob_mod, "iglob"),
    ]
    if hasattr(Path, "walk"):
        targets.append((Path, "walk"))
    patches = []
    for owner, name in targets:
        real = getattr(owner, name)

        def spy(*a, _real=real, _name=name, **k):
            # Path methods get the path as self; os/glob functions as the
            # first argument. ruamel globs its own plugin directory on every
            # YAML(); only a walk in the KB is the cost this pins.
            where = a[0] if a else k.get("path", k.get("pathname", ""))
            walks.append(f"{_name}({where})")
            return _real(*a, **k)

        patches.append(patch.object(owner, name, spy))
    for p in patches:
        p.start()
    try:
        yield walks
    finally:
        for p in reversed(patches):
            p.stop()


@pytest.mark.parametrize("surface", ["named", "kb_omitted", "mcp_kb_get"])
def test_a_read_the_index_can_answer_walks_no_directory(env, svc, surface):
    """Property A (round 1): the row's file still holds the id, so the read
    opens that one file and lists no directory. Only a miss looks by name:
    ``export`` and ``ai_ep`` call get_entry once per entry, and a walk per
    call costs milliseconds per entry on a large KB."""
    server = None
    if surface == "mcp_kb_get":
        from pyrite.server.mcp_server import PyriteMCPServer

        server = PyriteMCPServer(env["config"], tier="admin")
    try:
        with _directory_walks() as walks:
            if surface == "named":
                got = svc.get_entry(ENTRY, KB, readable_kbs=None)
            elif surface == "kb_omitted":
                got = svc.get_entry(ENTRY, None, readable_kbs=None)
            else:
                got = server._kb_get({"entry_id": ENTRY, "kb_name": KB})["entry"]
    finally:
        if server is not None:
            server.close()
    assert "### Added by hand" in got["body"]
    in_kb = [w for w in walks if str(env["kb_path"]) in w]
    assert in_kb == [], in_kb


def test_the_entry_is_parsed_from_the_bytes_it_hashed(env, svc):
    """Property B (round 1): one read of the file. The hash and the entry come
    from the same bytes, so the read is one version of the file."""
    with _files_opened() as opened:
        got = svc.get_entry(ENTRY, KB, readable_kbs=None)
    assert opened.count(env["path"]) == 1, opened
    assert got["content_hash"] == hashlib.sha256(env["path"].read_bytes()).hexdigest()


def test_no_row_but_a_file_is_found(env, svc):
    """A file added by hand and never indexed answers by its id."""
    added = env["kb_path"] / "notes" / "hand-added.md"
    added.write_text("---\nid: hand-added\ntitle: Hand added\ntype: note\n---\n\nNew.\n")
    with _directory_walks() as walks:
        got = svc.get_entry("hand-added", KB, readable_kbs=None)
    assert [w for w in walks if str(env["kb_path"]) in w], "a miss looks for the file by name"
    assert got["body"] == "New." and got["title"] == "Hand added"
    assert got["indexed_at"] is None and got["links"] == []


@pytest.mark.control(reason="dev never reads a file on a miss; guards the no-scan rule")
def test_no_row_and_no_file_named_like_the_id_is_a_miss_without_a_scan(env, svc):
    """A miss reads no file: an id with no row is looked for under its own name
    only (a scan of every file costs seconds on a large KB). A file whose id is
    not its filename is found once the index has it."""
    (env["kb_path"] / "notes" / "other-name.md").write_text(
        "---\nid: renamed-id\ntitle: R\ntype: note\n---\n\nx\n"
    )
    with _files_opened() as opened:
        assert svc.get_entry("renamed-id", KB, readable_kbs=None) is None
    assert opened == []


def test_a_row_but_no_file_is_not_found(env, svc):
    env["path"].unlink()
    assert svc.get_entry(ENTRY, KB, readable_kbs=None) is None


def test_a_row_with_no_file_path_is_returned_as_before(env, svc):
    """#92: experimental journalism tools create rows with no file. Unchanged."""
    svc.db.upsert_entry(
        {"id": "row-only", "kb_name": KB, "entry_type": "note", "title": "Row", "body": "b"}
    )
    got = svc.get_entry("row-only", KB, readable_kbs=None)
    assert got["title"] == "Row" and got["body"] == "b"
    assert got["content_hash"] is None


@pytest.mark.parametrize(
    "raw",
    [
        b"---\nid: multi\ntitle: [unclosed\ntype: note\n---\n\nbody\n",
        b"---\nid: multi\ntitle: Multi\n",
        b"\xff\xfe not utf-8",
    ],
    ids=["bad_yaml", "unterminated", "not_utf8"],
)
def test_an_unparseable_file_is_not_found_never_a_traceback(env, svc, raw):
    """NOT_FOUND until #777 gives it its own error (``_unreadable_entry_file``)."""
    env["path"].write_bytes(raw)
    assert svc.get_entry(ENTRY, KB, readable_kbs=None) is None
    assert cli_get_status(env) == (1, "NOT_FOUND")


def cli_get_status(env):
    result = _cli(env["config"], ["get", ENTRY, "-k", KB, "--format", "json"])
    return result.exit_code, json.loads(result.output).get("error_code")


def test_a_file_moved_by_hand_is_read_where_it_is(env, svc):
    moved = env["kb_path"] / "archive" / f"{ENTRY}.md"
    moved.parent.mkdir()
    env["path"].rename(moved)
    got = svc.get_entry(ENTRY, KB, readable_kbs=None)
    assert got["file_path"] == str(moved)
    assert "### Added by hand" in got["body"]


def test_a_file_whose_id_was_changed_by_hand_no_longer_answers(env, svc):
    """ADR-0038 I8: a file answers for an id only if it holds that id."""
    env["path"].write_text(HAND_EDITED.replace("id: multi", "id: something-else"))
    assert svc.get_entry(ENTRY, KB, readable_kbs=None) is None


def test_two_files_hold_the_id_the_index_winner_is_read(env, svc):
    """The row names the reconcile's winner (first KB-relative path); a second
    copy elsewhere does not replace it. Refusing an ambiguous id is #765's."""
    copy = env["kb_path"] / "zz" / f"{ENTRY}.md"
    copy.parent.mkdir()
    copy.write_text(HAND_EDITED.replace("hand text", "the copy"))
    got = svc.get_entry(ENTRY, KB, readable_kbs=None)
    assert got["file_path"] == str(env["path"])
    assert got["body"].endswith("hand text")


def test_a_symlinked_entry_reads_its_target_now(env, svc, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text(ORIGINAL)
    link = env["kb_path"] / "notes" / "linked.md"
    os.symlink(outside, link)
    outside.write_text(ORIGINAL.replace("id: multi", "id: linked"))
    _index(env["config"])
    outside.write_text(HAND_EDITED.replace("id: multi", "id: linked"))
    got = svc.get_entry("linked", KB, readable_kbs=None)
    assert "### Added by hand" in got["body"]
    assert got["content_hash"] == hashlib.sha256(outside.read_bytes()).hexdigest()


@pytest.mark.control(
    reason="dev's read writes nothing either; guards that the file read stays a read"
)
def test_a_read_writes_nothing(env, svc):
    before = svc.db.get_entry(ENTRY, KB)
    file_before = env["path"].read_bytes()
    svc.get_entry(ENTRY, KB, readable_kbs=None)
    assert svc.db.get_entry(ENTRY, KB) == before
    assert env["path"].read_bytes() == file_before


@pytest.mark.parametrize("surface", ["service", "mcp"])
def test_kb_omitted_reads_only_readable_kbs_and_opens_no_private_file(tmp_path, surface):
    pub, priv = tmp_path / "pub", tmp_path / "priv"
    pub.mkdir()
    priv.mkdir()
    (priv / "secret.md").write_text("---\nid: secret\ntitle: S\ntype: note\n---\n\nprivate\n")
    (pub / "open.md").write_text("---\nid: open\ntitle: O\ntype: note\n---\n\nold\n")
    config = _config(tmp_path, KBConfig(name="priv", path=priv), KBConfig(name="pub", path=pub))
    _index(config)
    (pub / "open.md").write_text("---\nid: open\ntitle: O\ntype: note\n---\n\nnew\n")

    def read(entry_id):
        if surface == "service":
            db = PyriteDB(config.settings.index_path)
            try:
                return KBService(config, db).get_entry(entry_id, readable_kbs={"pub"})
            finally:
                db.close()
        from pyrite.server.mcp_server import PyriteMCPServer

        server = PyriteMCPServer(config, tier="admin")
        try:
            res = server._kb_get({"entry_id": entry_id}, readable_kbs={"pub"})
            return res.get("entry")
        finally:
            server.close()

    with _files_opened() as opened:
        assert read("secret") is None
        assert read("open")["body"] == "new"
    assert not [p for p in opened if priv in p.parents], opened


def test_kb_omitted_an_indexed_id_answers_from_its_file(env, svc):
    got = svc.get_entry(ENTRY, None, readable_kbs=None)
    assert got["kb_name"] == KB and "### Added by hand" in got["body"]


def test_mcp_body_bounds_hold_on_the_file_body(env):
    """ADR-0034: the chunk is cut from the file's body, not the index's."""
    env["path"].write_text(HAND_EDITED + "\n" + "x" * 5000 + "\n")
    res = _mcp(env, "kb_get", {"entry_id": ENTRY, "kb_name": KB, "body_limit": 100})
    entry = res["entry"]
    assert entry["body_truncated"] is True and len(entry["body"]) == 100
    full = _mcp(env, "kb_read_body", {"entry_id": ENTRY, "kb_name": KB, "body_offset": 0})
    assert full["body_length"] > 5000


def test_fields_projection_comes_from_the_file(env):
    got = _rest(env, f"/api/entries/{ENTRY}", kb=KB, fields="title").json()
    assert got == {"id": ENTRY, "kb_name": KB, "title": "Multi, edited by hand"}
    result = _cli(
        env["config"], ["get", ENTRY, "-k", KB, "--fields", "title,tags", "--format", "json"]
    )
    assert json.loads(result.output)["title"] == "Multi, edited by hand"


def test_an_echo_of_a_lagging_read_changes_no_value_in_the_file(env):
    """split_echoed_update compares the echo with the same file read a get
    returns, not the lagging row. The index read ``importance: 3``; the file
    now says ``high``, which a read normalises to a number. Compared with the
    row, the echoed number looked like a change and was written over the
    file's own spelling (#561's loss, reached through the lag)."""
    env["path"].write_text(ORIGINAL.replace("type: note\n", "type: note\nimportance: 3\n"))
    _index(env["config"])
    env["path"].write_text(HAND_EDITED.replace("type: note\n", "type: note\nimportance: high\n"))
    read = mcp_kb_get(env)
    assert read["importance"] != 3, "the read did not come from the file"
    res = _mcp(env, "kb_update", {**read, "kb_name": KB, "entry_id": ENTRY})
    assert "error" not in res, res
    assert "importance: high" in env["path"].read_text()


# ---------------------------------------------------------------------------
# The two projections cannot drift: on a fresh index the file read IS the row
# ---------------------------------------------------------------------------

KB_YAML = """\
name: pin
types:
  finding:
    fields:
      severity:
        type: select
        options: [low, high]
"""

PIN_FILES = {
    "notes/plain.md": "---\nid: plain\ntitle: Plain\ntype: note\n---\n\nBody [[event-one]].\n",
    "notes/rich.md": (
        "---\nid: rich\ntitle: Rich\ntype: note\ntags: [b, a, b]\nimportance: 7\n"
        "created_at: 2026-01-15T09:00:00Z\nupdated_at: 2026-02-01T10:00:00Z\n"
        "custom_key: kept\nrank: 3\neffort: M\nkind: feature\nstatus_reason: why\n"
        "sources:\n- title: A source\n  url: https://example.org/a\n  outlet: Ex\n"
        "  date: '2026-01-01'\n  verified: true\n"
        "links:\n- target: plain\n  relation: related_to\n---\n\nRich body.\n"
    ),
    "events/event-one.md": (
        "---\nid: event-one\ntitle: Event one\ntype: event\ndate: '2026-03-04'\n"
        "importance: 8\nlocation: Here\nparticipants: [Ann]\n---\n\nIt happened.\n"
    ),
    "people/ann.md": "---\nid: ann\ntitle: Ann\ntype: person\nrole: editor\n---\n\nA person.\n",
    "tasks/do-it.md": (
        "---\nid: do-it\ntitle: Do it\ntype: task\nstatus: in_progress\npriority: 2\n"
        "assignee: ann\ndue_date: '2026-05-01'\n---\n\nA task.\n"
    ),
    "findings/f1.md": "---\nid: f1\ntitle: F1\ntype: finding\nseverity: high\n---\n\nFound.\n",
    "no-timestamps.md": "---\nid: no-timestamps\ntitle: No timestamps\ntype: note\n---\n\nx\n",
    # YAML's own scalar types, unquoted, in the typed columns and in metadata:
    # what the index stores for each is what the file read must return.
    "events/unquoted.md": (
        "---\nid: unquoted\ntitle: Unquoted\ntype: event\ndate: 2026-03-04\n"
        "importance: 7.9\nlocation: null\n---\n\nx\n"
    ),
    "events/whole-float.md": (
        "---\nid: whole-float\ntitle: Whole float\ntype: event\ndate: 2026-03-05\n"
        "importance: 7.0\n---\n\nx\n"
    ),
    "tasks/typed.md": (
        "---\nid: typed\ntitle: Typed\ntype: task\nstatus: true\npriority: 2.5\n"
        "assignee: null\ndue_date: 2026-05-01\nstart_date: 2026-01-02T10:00:00\n"
        "end_date: false\n---\n\nx\n"
    ),
    "tasks/int-priority.md": (
        "---\nid: int-priority\ntitle: Int priority\ntype: task\npriority: 3\n"
        "assignee: 7\n---\n\nx\n"
    ),
    "notes/scalars.md": (
        "---\nid: scalars\ntitle: Scalars\ntype: note\nimportance: true\n"
        "custom_flag: yes\ncustom_date: 2026-01-01\ncustom_when: 2026-01-01T08:00:00Z\n"
        "custom_null: null\ncustom_float: 0.1\ntags: [1, true, 2026-01-01]\n---\n\nx\n"
    ),
}


def test_on_a_fresh_index_the_file_read_equals_the_row(tmp_path):
    """``IndexManager.read_fields`` mirrors the backend's row shape; this is
    the test that fails when one changes without the other (a finding for
    #730 P5: the row's shape has no projection both can share)."""
    kb_path = tmp_path / "pin"
    for rel, text in PIN_FILES.items():
        (kb_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (kb_path / rel).write_text(text)
    (kb_path / "kb.yaml").write_text(KB_YAML)
    coll = kb_path / "shelf"
    coll.mkdir()
    (coll / "__collection.yaml").write_text("title: Shelf\ndescription: A shelf\n")
    config = _config(tmp_path, KBConfig(name="pin", path=kb_path, kb_type="generic"))
    _index(config)
    db = PyriteDB(config.settings.index_path)
    try:
        svc = KBService(config, db)
        ids = [r["id"] for r in db.list_entries(kb_name="pin", limit=100)]
        assert len(ids) == len(PIN_FILES) + 1, ids
        for entry_id in ids:
            row = db.get_entry(entry_id, "pin")
            read = svc.get_entry(entry_id, "pin", readable_kbs=None)
            assert (
                read["content_hash"]
                == hashlib.sha256(Path(row["file_path"]).read_bytes()).hexdigest()
            )
            got = {k: v for k, v in read.items() if k not in ("outlinks", "backlinks")}
            got.pop("content_hash")
            assert list(got) == list(row), entry_id
            assert got == row, entry_id
    finally:
        db.close()


# ---------------------------------------------------------------------------
# The overlay: a signed-in user's read service (REST, WorktreeResolver)
# ---------------------------------------------------------------------------


def test_the_overlay_reads_the_users_own_file_never_mains_in_its_place(tmp_path):
    """REST reads for a signed-in user go through ``get_read_service``: main's
    config with an overlay database whose rows point into the user's worktree,
    outside main's KB root. The read follows the row there."""
    import subprocess

    from pyrite.server.worktree_resolver import WorktreeResolver
    from tests.test_worktree_service import _clean_git_env, _init_git_repo

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    kb_path = repo / "kb"
    (kb_path / "notes").mkdir(parents=True)
    main_file = kb_path / "notes" / f"{ENTRY}.md"
    main_file.write_text(ORIGINAL)
    for cmd in (["git", "add", "."], ["git", "commit", "-qm", "kb"]):
        subprocess.run(
            cmd, cwd=repo, env=_clean_git_env(), check=True, capture_output=True, timeout=120
        )
    config = _config(tmp_path, KBConfig(name=KB, path=kb_path, kb_type="generic"))
    _index(config)
    db = PyriteDB(config.settings.index_path)
    cache: dict = {}
    ann, bob = {"id": 7, "username": "ann"}, {"id": 8, "username": "bob"}
    try:
        resolver = WorktreeResolver(config, db, cache)
        resolver.get_write_service(KB, ann).update(ENTRY, KB, {"body": "Ann's pending body."})
        main_file.write_text(HAND_EDITED)  # main moves on by hand

        def read(user):
            return resolver.get_read_service(KB, user).get_entry(ENTRY, KB, readable_kbs=None)

        mine = read(ann)
        assert mine["body"] == "Ann's pending body."
        ann_file = Path(mine["file_path"])
        assert kb_path not in ann_file.parents, "the read is not the worktree's file"

        ann_file.write_text(ann_file.read_text().replace("pending body", "pending body, by hand"))
        assert read(ann)["body"] == "Ann's pending body, by hand."
        assert "### Added by hand" in read(bob)["body"]  # no worktree: main's file

        ann_file.unlink()
        assert read(ann) is None, "main's file answered in place of the user's"
    finally:
        for d in cache.values():
            d.close()
        db.close()
