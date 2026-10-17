"""A create writes its values onto a skeleton with apply(); nothing else emits entry bytes (#955, B6 C2).

ADR-0042 decision 13 keeps create as the one place Pyrite chooses spelling.
C2 (the #730 spike, §1 and §8) builds that file the same way an update is
built: ``---\\nid: <id>\\n---\\n`` plus the body, then one ``Set`` per key of
the type's ``to_frontmatter()``, in order, through ``file_operations.apply``,
published with ``expect=ABSENT`` so two creates of one id give one file and
one refusal. The type still decides *which* values a new file gets
(ADR-0045 Q4: ``to_frontmatter`` is a create and index helper); it no longer
decides the bytes (no ``to_markdown``, no YAML emitter).

Oracles: the file's parsed values against the entry's own ``to_frontmatter()``
(plain-compared, so a date is a date on both sides); a watch on both YAML
emitters that must see no document during the create; the file's key order.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from pyrite.cli import app as cli_app
from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager
from pyrite.utils.frontmatter import load_frontmatter
from tests.test_doc_write_as_patch import _watch_emitter

KB = "made"


def _config(tmp_path: Path, *, git: bool = True) -> PyriteConfig:
    kb_path = tmp_path / "kb"
    kb_path.mkdir()
    (kb_path / "kb.yaml").write_text(f"name: {KB}\nkb_type: generic\n", encoding="utf-8")
    if git:
        subprocess.run(
            ["git", "init", "-q"], cwd=kb_path, check=True, capture_output=True, timeout=30
        )
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name=KB, path=kb_path, kb_type="generic")],
        settings=Settings(index_path=tmp_path / "index.db", auto_embed=False),
    )
    db = PyriteDB(config.settings.index_path)
    try:
        IndexManager(db, config).index_all()
    finally:
        db.close()
    return config


def _plain(value: Any) -> Any:
    """Loaded values as plain Python, so a ruamel scalar equals a str and a
    TimeStamp a datetime (both sides are compared the same way)."""
    from datetime import datetime

    if isinstance(value, datetime):
        return datetime.fromisoformat(value.isoformat())
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    if isinstance(value, str):
        return str(value)
    return value


def _file_of(config: PyriteConfig, entry_id: str) -> Path:
    found = sorted(config.knowledge_bases[0].path.rglob(f"{entry_id}.md"))
    assert len(found) == 1, found
    return found[0]


def assert_file_holds_the_types_values(config: PyriteConfig, entry) -> Path:
    path = _file_of(config, entry.id)
    parsed = load_frontmatter(path.read_text(encoding="utf-8"))
    assert parsed is not None
    fm, body = parsed
    # The file's values are `_frontmatter_for_file()`: `to_frontmatter()` less
    # the default-valued keys a *loaded* source lacked (#46), which is what a
    # file-based create (`add`) must not add; for a fresh create the two are
    # one dict.
    expected = _plain(entry._frontmatter_for_file())
    got = _plain(fm)
    assert got == expected, f"file: {got}\ntype: {expected}"
    assert list(fm)[0] == "id", f"id is not the first key: {list(fm)}"
    assert body == (entry.body or "").strip()
    return path


SPEC = {
    "entry_type": "event",
    "title": "It's the #1 hit: yes",
    "body": "Text.\n\nMore.",
    "date": "2026-01-15",
    "tags": ["alpha", "beta"],
    "participants": ["[[jane]]"],
    "importance": 7,
    "custom_key": {"nested": [1, "two"]},
}


def _with_service(config, fn):
    db = PyriteDB(config.settings.index_path)
    try:
        return fn(KBService(config, db))
    finally:
        db.close()


# --------------------------------------------------------------------------
# Every create entry point writes the type's values
# --------------------------------------------------------------------------


@pytest.mark.control(
    reason="the old whole-file emit wrote the same values (that is what the spike measured on 1,323 files); the control pins them while the bytes come from apply() instead, which the emitter watch proves"
)
def test_service_create_writes_the_types_values(tmp_path: Path):
    config = _config(tmp_path)
    written = _with_service(config, lambda svc: svc.create(KB, dict(SPEC)))
    assert_file_holds_the_types_values(config, written.entry)


@pytest.mark.control(
    reason="the old whole-file emit wrote the same values (that is what the spike measured on 1,323 files); the control pins them while the bytes come from apply() instead, which the emitter watch proves"
)
def test_create_entry_writes_the_types_values(tmp_path: Path):
    config = _config(tmp_path)
    entry = _with_service(
        config,
        lambda svc: svc.create_entry(
            KB, "made-by-kwargs", "Made", "note", "Body.", tags=["a"], custom_key="v"
        ),
    )
    assert_file_holds_the_types_values(config, entry)


@pytest.mark.control(
    reason="the old whole-file emit wrote the same values (that is what the spike measured on 1,323 files); the control pins them while the bytes come from apply() instead, which the emitter watch proves"
)
def test_bulk_create_writes_the_types_values(tmp_path: Path):
    from pyrite.storage.repository import KBRepository

    config = _config(tmp_path)
    results = _with_service(
        config,
        lambda svc: svc.bulk_create_entries(
            KB, [dict(SPEC), {"entry_type": "note", "title": "Second", "body": "b"}]
        ),
    )
    assert all(r["created"] for r in results), results
    repo = KBRepository(config.knowledge_bases[0])
    for item in results:
        assert_file_holds_the_types_values(config, repo.load(item["entry_id"]))


def test_add_entry_from_file_writes_the_types_values(tmp_path: Path):
    config = _config(tmp_path)
    src = tmp_path / "incoming.md"
    src.write_text(
        "---\nid: added\ntitle: Added\ntype: note\ntags: [x]\nnote: kept\n---\n\nFrom a file.\n",
        encoding="utf-8",
    )
    entry, _info = _with_service(config, lambda svc: svc.add_entry_from_file(KB, src))
    assert_file_holds_the_types_values(config, entry)


@pytest.mark.control(
    reason="the old whole-file emit wrote the same values (that is what the spike measured on 1,323 files); the control pins them while the bytes come from apply() instead, which the emitter watch proves"
)
def test_mcp_kb_create_writes_the_types_values(tmp_path: Path):
    from pyrite.server.mcp_server import PyriteMCPServer
    from pyrite.storage.repository import KBRepository

    config = _config(tmp_path)
    server = PyriteMCPServer(config, tier="write")
    try:
        result = server._dispatch_tool("kb_create", {"kb_name": KB, **SPEC})
    finally:
        server.close()
    assert result.get("created"), result
    entry = KBRepository(config.knowledge_bases[0]).load(result["entry_id"])
    assert_file_holds_the_types_values(config, entry)


@pytest.mark.control(
    reason="the old whole-file emit wrote the same values (that is what the spike measured on 1,323 files); the control pins them while the bytes come from apply() instead, which the emitter watch proves"
)
def test_rest_post_entries_writes_the_types_values(tmp_path: Path):
    pytest.importorskip("fastapi", reason="fastapi not installed")
    from fastapi.testclient import TestClient

    from pyrite.server.api import create_app, get_config, get_db, get_index_mgr
    from pyrite.storage.repository import KBRepository

    config = _config(tmp_path)
    db = PyriteDB(config.settings.index_path)
    app = create_app(config)
    app.dependency_overrides[get_config] = lambda: config
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_index_mgr] = lambda: IndexManager(db, config)
    client = TestClient(app)
    try:
        res = client.post("/api/entries", json={"kb": KB, **SPEC, "allow_undeclared": True})
    finally:
        client.close()
        db.close()
    assert res.status_code in (200, 201), res.text
    entry = KBRepository(config.knowledge_bases[0]).load(res.json()["id"])
    assert_file_holds_the_types_values(config, entry)


@pytest.mark.control(
    reason="the old whole-file emit wrote the same values (that is what the spike measured on 1,323 files); the control pins them while the bytes come from apply() instead, which the emitter watch proves"
)
def test_cli_create_writes_the_types_values(tmp_path: Path):
    from pyrite.storage.repository import KBRepository

    config = _config(tmp_path)
    args = [
        "create",
        "-k",
        KB,
        "-t",
        "event",
        "--title",
        SPEC["title"],
        "-b",
        "Text.",
        "--tags",
        "alpha,beta",
        "-f",
        "date=2026-01-15",
        "-f",
        'participants=["[[jane]]"]',
        "-f",
        "custom_key=1",
        "--format",
        "json",
    ]
    with (
        patch("pyrite.cli.load_config", return_value=config),
        patch("pyrite.cli.context.load_config", return_value=config),
    ):
        result = CliRunner().invoke(cli_app, args)
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output[result.output.index("{") :])
    entry = KBRepository(config.knowledge_bases[0]).load(payload["entry_id"])
    assert_file_holds_the_types_values(config, entry)


@pytest.mark.control(
    reason="the old whole-file emit wrote the same values (that is what the spike measured on 1,323 files); the control pins them while the bytes come from apply() instead, which the emitter watch proves"
)
def test_task_create_writes_the_types_values(tmp_path: Path):
    from pyrite.services.task_service import TaskService
    from pyrite.storage.repository import KBRepository

    kb_path = tmp_path / "tasks"
    (kb_path / "tasks").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=kb_path, check=True, capture_output=True, timeout=30)
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="tasks", path=kb_path, kb_type="task")],
        settings=Settings(index_path=tmp_path / "index.db", auto_embed=False),
    )
    db = PyriteDB(config.settings.index_path)
    try:
        result = TaskService(config, db).create_task(
            "tasks", "Do it", body="Steps.", priority=2, dependencies=["t0"]
        )
    finally:
        db.close()
    entry = KBRepository(config.knowledge_bases[0]).load(result["entry_id"])
    assert_file_holds_the_types_values(config, entry)


# --------------------------------------------------------------------------
# The bytes come from apply(), not from a YAML emitter; a create never overwrites
# --------------------------------------------------------------------------


def test_a_create_hands_no_document_to_the_yaml_emitter(tmp_path: Path, monkeypatch):
    """The emitter watch of ``tests/test_doc_write_as_patch.py``: a create
    that rendered the entry with ``to_markdown`` (``dump_yaml``) would hand
    the emitter a document carrying every key; through ``apply()`` the
    values are written by its own small emitter and no document is seen."""
    config = _config(tmp_path)
    with pytest.MonkeyPatch.context() as spy_patch:
        watched = _watch_emitter(spy_patch)
        _with_service(config, lambda svc: svc.create(KB, dict(SPEC)))
    assert not watched.unwatched, watched.unwatched
    assert watched.docs == [], f"the YAML emitter was handed {watched.keys()}"


@pytest.mark.control(
    reason="exclusivity held before (#391, #477); the control pins it for the new publish (expect=ABSENT, and the no-git fallback)"
)
def test_two_creates_of_one_id_give_one_file_and_one_refusal(tmp_path: Path):
    from pyrite.exceptions import EntryExistsError

    config = _config(tmp_path)
    spec = {"entry_type": "note", "id": "twice", "title": "Once", "body": "first"}
    _with_service(config, lambda svc: svc.create(KB, dict(spec)))
    before = _file_of(config, "twice").read_bytes()

    with pytest.raises(EntryExistsError):
        _with_service(config, lambda svc: svc.create(KB, {**spec, "body": "second"}))

    assert _file_of(config, "twice").read_bytes() == before
    assert len(list(config.knowledge_bases[0].path.rglob("*.md"))) == 1


@pytest.mark.control(
    reason="exclusivity held before (#391, #477); the control pins it for the new publish (expect=ABSENT, and the no-git fallback)"
)
def test_a_create_outside_git_still_refuses_an_existing_file(tmp_path: Path):
    from pyrite.exceptions import EntryExistsError

    config = _config(tmp_path, git=False)
    spec = {"entry_type": "note", "id": "twice", "title": "Once", "body": "first"}
    _with_service(config, lambda svc: svc.create(KB, dict(spec)))
    with pytest.raises(EntryExistsError):
        _with_service(config, lambda svc: svc.create(KB, {**spec, "body": "second"}))
    assert len(list(config.knowledge_bases[0].path.rglob("*.md"))) == 1
