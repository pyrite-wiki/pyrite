"""An update never loses a frontmatter key it was not asked to change (#557, #455).

The #557 repro: a `draft` entry carried a top-level `provenance:` block of its
own shape (`source:`, `origin_doc:`...). `pyrite update <id> -f draft_status=x`
reported `{"updated": true}` and the block was gone from the file. The cause
was not a reserved-name check: `Entry._base_kwargs` parses `provenance` into
Pyrite's `Provenance` dataclass, which knows ten sub-keys, so the block
serialized to `{}` and the write path dropped the key. Any key a model parses
lossily went the same way (`sources: a book`, `importance: high`, a
`links: [a, b]` shorthand rewritten into dicts).

Policy (plan on PR #558): a key the write was not asked to change is written
back exactly as the file had it. An explicit assignment or an in-place change
of the field still wins. Naming a Pyrite-managed key (`provenance`) is refused
before anything is written, and the message says to rename the file's key.

Every test here runs over a KB on disk with the real service, CLI, REST app
or MCP server -- the bug lives in the load -> save round trip, so a mock
cannot enter it.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager
from pyrite.storage.repository import KBRepository

KB = "drafts"

KB_YAML = """name: drafts
kb_type: generic
types:
  draft:
    description: A draft
    fields:
      draft_status:
        type: text
  note:
    description: A note
"""

#: The #557 shape: a multi-line top-level `provenance:` block whose sub-keys
#: Pyrite's `Provenance` does not know, next to an undeclared key.
DRAFT = """---
id: my-draft
title: My draft
type: draft
draft_status: brief
provenance:
  source: amy-notes
  origin_doc: https://example.org/doc
  history:
  - drafted 2026-09-01
  - edited 2026-09-02
pitch_outlet: The Atlantic
---

Body text.
"""


def _env(tmp_path: Path, files: dict[str, str]) -> tuple[PyriteConfig, Path]:
    kb_path = tmp_path / "kb"
    kb_path.mkdir()
    (kb_path / "kb.yaml").write_text(KB_YAML, encoding="utf-8")
    for name, text in files.items():
        path = kb_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    db_path = tmp_path / "index.db"
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name=KB, path=kb_path, kb_type=KBType.GENERIC)],
        settings=Settings(index_path=db_path),
    )
    db = PyriteDB(db_path)
    try:
        IndexManager(db, config).index_all()
    finally:
        db.close()
    return config, kb_path


def _with_service(config: PyriteConfig, fn):
    db = PyriteDB(config.settings.index_path)
    try:
        return fn(KBService(config, db))
    finally:
        db.close()


# ---------------------------------------------------------------------------
# The three surfaces, one service path
# ---------------------------------------------------------------------------


def _update_cli(config: PyriteConfig, entry_id: str, field: str, value: str) -> None:
    with patch("pyrite.cli.context.load_config", return_value=config):
        result = CliRunner().invoke(app, ["update", entry_id, "-k", KB, "-f", f"{field}={value}"])
    assert result.exit_code == 0, result.output


def _update_rest(config: PyriteConfig, entry_id: str, field: str, value: str) -> None:
    pytest.importorskip("fastapi", reason="fastapi not installed")
    from starlette.testclient import TestClient

    from pyrite.server.api import create_app, get_config, get_db, get_index_worker
    from pyrite.services.index_worker import IndexWorker

    application = create_app(config=config)
    db = PyriteDB(config.settings.index_path)
    application.dependency_overrides[get_config] = lambda: config
    application.dependency_overrides[get_db] = lambda: db
    worker = IndexWorker(db, config)
    application.dependency_overrides[get_index_worker] = lambda: worker
    try:
        resp = TestClient(application).patch(
            f"/api/entries/{entry_id}", json={"kb": KB, "field": field, "value": value}
        )
        assert resp.status_code == 200, resp.text
    finally:
        worker.wait_for_idle(timeout=10)
        db.close()
        state_db = getattr(application.state, "pyrite_db", None)
        if state_db is not None and state_db is not db:
            state_db.close()


def _mcp(config: PyriteConfig):
    from pyrite.server.mcp_server import PyriteMCPServer

    return PyriteMCPServer(config, tier="write")


def _update_mcp(config: PyriteConfig, entry_id: str, field: str, value: str) -> None:
    server = _mcp(config)
    try:
        res = server._dispatch_tool(
            "kb_update", {"entry_id": entry_id, "kb_name": KB, field: value}
        )
    finally:
        server.close()
    assert res.get("updated") is True, res


SURFACES = {"cli": _update_cli, "rest": _update_rest, "mcp": _update_mcp}


@pytest.mark.parametrize("surface", sorted(SURFACES))
def test_update_keeps_a_provenance_block_it_was_not_asked_to_change(tmp_path, surface):
    """#557: `update -f draft_status=ready` leaves the file byte-identical
    outside the changed line -- the `provenance:` block included."""
    config, kb_path = _env(tmp_path, {"drafts/my-draft.md": DRAFT})
    path = kb_path / "drafts" / "my-draft.md"

    SURFACES[surface](config, "my-draft", "draft_status", "ready")

    assert path.read_text(encoding="utf-8") == DRAFT.replace(
        "draft_status: brief", "draft_status: ready"
    )


@pytest.mark.parametrize(
    "block",
    [
        # One sub-key Pyrite knows, one it does not: the known one used to
        # survive and the other vanish.
        "provenance:\n  created_by: mark\n  source: amy-notes\n",
        # Not a mapping at all.
        "provenance: from Amy's notes\n",
        # Other base keys a model reads lossily.
        "sources: a book on the shelf\n",
        "importance: high\n",
        "_schema_version: draft-2\n",
        # A shorthand Pyrite accepts but used to rewrite into dicts.
        "links:\n- alpha\n- beta\n",
    ],
    ids=["provenance-partial", "provenance-scalar", "sources", "importance", "schema", "links"],
)
def test_service_update_keeps_every_key_it_was_not_asked_to_change(tmp_path, block):
    text = f"---\nid: d\ntitle: D\ntype: draft\ndraft_status: brief\n{block}---\n\nBody.\n"
    config, kb_path = _env(tmp_path, {"d.md": text})

    _with_service(config, lambda svc: svc.update("d", KB, {"draft_status": "ready"}))

    assert (kb_path / "d.md").read_text(encoding="utf-8") == text.replace(
        "draft_status: brief", "draft_status: ready"
    )


# ---------------------------------------------------------------------------
# The guards: a deliberate change still lands
# ---------------------------------------------------------------------------


@pytest.mark.control(
    reason="an explicit set always reached the file; this pins that keeping an "
    "unrepresentable source value never overrides a value the caller named"
)
@pytest.mark.parametrize("value", ["5", "7"])
def test_an_explicit_update_of_a_kept_key_still_lands(tmp_path, value):
    """`-f importance=5` over `importance: high`: 5 is also what the model read
    `high` as, so a value comparison alone would keep `high` and report success
    (the mirror of commit 7783335). The assignment itself must count."""
    text = "---\nid: d\ntitle: D\ntype: draft\nimportance: high\n---\n\nBody.\n"
    config, kb_path = _env(tmp_path, {"d.md": text})

    _update_cli(config, "d", "importance", value)

    assert (kb_path / "d.md").read_text(encoding="utf-8") == text.replace(
        "importance: high", f"importance: {value}"
    )


@pytest.mark.control(
    reason="an in-place change always reached the file; this pins that the "
    "kept-source rule notices a field changed without an assignment"
)
def test_an_in_place_change_of_a_kept_key_still_lands(tmp_path):
    """`links: [alpha]` is kept as written until something changes the list;
    `add_link` appends to it in place, without assigning the attribute, and
    that change must still reach the file."""
    text = "---\nid: d\ntitle: D\ntype: note\nlinks:\n- alpha\n---\n\nBody.\n"
    other = "---\nid: beta\ntitle: Beta\ntype: note\n---\n\nBody.\n"
    config, kb_path = _env(tmp_path, {"notes/d.md": text, "notes/beta.md": other})

    _with_service(config, lambda svc: svc.add_link("d", KB, "beta"))

    saved = KBRepository(config.get_kb(KB)).load("d")
    assert [link.target for link in saved.links] == ["alpha", "beta"]
    assert "- alpha" not in (kb_path / "notes" / "d.md").read_text(encoding="utf-8")


def test_naming_a_managed_key_is_refused_before_anything_is_written(tmp_path):
    """`-f provenance=x` is refused, the file byte-identical, and the message
    names the key and says how to keep a key of one's own under that name."""
    config, kb_path = _env(tmp_path, {"drafts/my-draft.md": DRAFT})
    path = kb_path / "drafts" / "my-draft.md"

    with patch("pyrite.cli.context.load_config", return_value=config):
        result = CliRunner().invoke(
            app, ["update", "my-draft", "-k", KB, "-f", "provenance=x", "--format", "json"]
        )

    assert result.exit_code != 0
    text = result.output.strip()
    payload = json.loads(text[text.index("{") :])
    assert payload.get("error_code") == "VALIDATION_FAILED", payload
    message = json.dumps(payload)
    assert "provenance" in message
    assert "rename" in message.lower(), message
    assert path.read_text(encoding="utf-8") == DRAFT


# ---------------------------------------------------------------------------
# MCP kb_update stores undeclared keys (#455)
# ---------------------------------------------------------------------------


def test_mcp_kb_update_stores_an_undeclared_key(tmp_path):
    """#455: `kb_update` filtered its arguments to the type's declared fields,
    so `pitch_outlet` never reached the service and was dropped with
    `updated: true`. CLI and REST store it (#407)."""
    config, kb_path = _env(tmp_path, {"drafts/my-draft.md": DRAFT})
    path = kb_path / "drafts" / "my-draft.md"

    _update_mcp(config, "my-draft", "pitch_outlet", "Harper's")

    assert path.read_text(encoding="utf-8") == DRAFT.replace(
        "pitch_outlet: The Atlantic", "pitch_outlet: Harper's"
    )


def test_mcp_kb_update_stores_a_new_undeclared_key(tmp_path):
    config, kb_path = _env(tmp_path, {"drafts/my-draft.md": DRAFT})

    _update_mcp(config, "my-draft", "editor", "amy")

    entry = KBRepository(config.get_kb(KB)).load("my-draft")
    assert entry.to_frontmatter().get("editor") == "amy"


def test_mcp_kb_update_echoing_a_read_result_writes_only_what_it_can_and_says_so(tmp_path):
    """The filter existed so an agent echoing a `kb_get` result back cannot
    rewrite the id, the path, timestamps or index columns. Those are still not
    written -- and are now listed in `ignored` instead of vanishing."""
    config, kb_path = _env(tmp_path, {"drafts/my-draft.md": DRAFT})
    path = kb_path / "drafts" / "my-draft.md"
    server = _mcp(config)
    try:
        got = server._dispatch_tool("kb_get", {"entry_id": "my-draft", "kb_name": KB})["entry"]
        # An echoed `importance: 5` is a real field set to its default, and
        # was written before this change too (it was always in the declared
        # set): the file grows the line. Not this theme's; see the PR.
        got.pop("importance")
        res = server._dispatch_tool(
            "kb_update", {**got, "entry_id": "my-draft", "draft_status": "ready"}
        )
    finally:
        server.close()

    assert res.get("updated") is True, res
    ignored = set(res.get("ignored", []))
    for key in ("id", "file_path", "indexed_at", "created_at", "updated_at", "backlinks"):
        assert key in ignored, res
    assert "draft_status" not in ignored
    # Nulls the read result carried for fields a draft does not have.
    assert {"assignee", "due_date", "location"} <= ignored, res
    assert path.read_text(encoding="utf-8") == DRAFT.replace(
        "draft_status: brief", "draft_status: ready"
    )


# ---------------------------------------------------------------------------
# qa validate reports a reserved key Pyrite cannot represent
# ---------------------------------------------------------------------------

LINKS_SHORTHAND = "---\nid: plain\ntitle: Plain\ntype: note\nlinks:\n- my-draft\n---\n\nBody.\n"


def _qa_issues(config: PyriteConfig, entry_id: str | None = None) -> list[dict]:
    from pyrite.services.qa_service import QAService
    from pyrite.services.access_policy import UNSCOPED

    db = PyriteDB(config.settings.index_path)
    try:
        qa = QAService(config, db)
        if entry_id:
            return qa.validate_entry(entry_id, KB, readable_kbs=UNSCOPED)["issues"]
        return qa.validate_kb(KB, readable_kbs=UNSCOPED)["issues"]
    finally:
        db.close()


def test_qa_validate_reports_a_reserved_key_collision(tmp_path):
    config, kb_path = _env(
        tmp_path, {"drafts/my-draft.md": DRAFT, "notes/plain.md": LINKS_SHORTHAND}
    )

    for issues in (_qa_issues(config), _qa_issues(config, "my-draft")):
        found = [i for i in issues if i["rule"] == "reserved_key_collision"]
        assert len(found) == 1, issues
        issue = found[0]
        assert issue["entry_id"] == "my-draft"
        assert issue["field"] == "provenance"
        assert "drafts/my-draft.md" in issue["message"], issue
        assert "rename" in issue["message"].lower(), issue


def test_qa_validate_reports_a_scalar_and_an_unparseable_reserved_value(tmp_path):
    config, _ = _env(
        tmp_path,
        {
            "a.md": "---\nid: a\ntitle: A\ntype: note\nprovenance: amy\n---\n\nB.\n",
            "b.md": "---\nid: b\ntitle: B\ntype: note\nimportance: high\n---\n\nB.\n",
        },
    )
    found = {
        (i["entry_id"], i["field"])
        for i in _qa_issues(config)
        if i["rule"] == "reserved_key_collision"
    }
    assert found == {("a", "provenance"), ("b", "importance")}


@pytest.mark.control(
    reason="a negative control: normalizations that lose nothing, and keys "
    "Pyrite fully represents, must not be reported"
)
def test_qa_validate_does_not_report_a_lossless_shorthand_or_a_real_provenance(tmp_path):
    real = (
        "---\nid: real\ntitle: Real\ntype: note\ntags: [a]\nlifecycle: active\n"
        "provenance:\n  created_by: mark\n---\n\nB.\n"
    )
    config, _ = _env(tmp_path, {"plain.md": LINKS_SHORTHAND, "real.md": real})
    assert [i for i in _qa_issues(config) if i["rule"] == "reserved_key_collision"] == []


def test_qa_validate_cli_names_the_file_and_key(tmp_path):
    config, _ = _env(tmp_path, {"drafts/my-draft.md": DRAFT})
    with patch("pyrite.cli.context.load_config", return_value=config):
        result = CliRunner().invoke(app, ["qa", "validate", KB, "--format", "json"])
    text = result.output.strip()
    payload = json.loads(text[text.index("{") :])
    found = [i for i in payload["issues"] if i["rule"] == "reserved_key_collision"]
    assert len(found) == 1, payload
    assert "provenance" in found[0]["message"]
    assert "my-draft.md" in found[0]["message"]
