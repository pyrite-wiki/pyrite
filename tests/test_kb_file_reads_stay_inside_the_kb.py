"""Every KB file read whose bytes, or anything derived from them, reach a caller
is read inside the KB: through ``KBFiles.read_bytes`` or
``KBRepository._read_inside``.

Derived means more than the body: a template's text, a key name and line
number in a refusal, the types a ``kb.yaml`` declares, a board's lanes, the
git blob hash of a file. A file reached through a symbolic link that leaves
the KB, or a file with more than one hard link (its other name may be anywhere
on the filesystem, and the inode cannot say where), is not the KB's: it is
skipped in a list, and a read by name answers not-found or ``OUTSIDE_KB_ROOT``.

``tests/test_indexed_bytes_stay_inside_the_kb.py`` holds the same rule for
the index and the exports; this file holds it for the other readers, each
driven through its own entry point.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from pyrite.config import KBConfig
from tests._world import build_world

SECRET = "secretoutsidethekb7d41"
KB = "k"

#: A file that is a link out of the KB, and a hard link to a file outside it.
FORMS = ["symlink", "hardlink"]


def _place(form: str, outside: Path, inside: Path, text: str) -> None:
    """Put ``text`` outside the KB and make ``inside`` reach it by ``form``."""
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text(text)
    inside.parent.mkdir(parents=True, exist_ok=True)
    if form == "symlink":
        inside.symlink_to(outside)
    else:
        os.link(outside, inside)


def _note(entry_id: str, body: str = "an ordinary note") -> str:
    return f"---\nid: {entry_id}\ntitle: {entry_id.title()}\ntype: note\n---\n{body}\n"


@pytest.fixture
def world(tmp_path):
    kb = tmp_path / "kb"
    (kb / "notes").mkdir(parents=True)
    (kb / "notes" / "inside.md").write_text(_note("inside"))
    made = []

    def make(kb_type: str = "generic"):
        w = build_world(tmp_path / "world", kbs=[KBConfig(name=KB, path=kb, kb_type=kb_type)])
        made.append(w)
        return w

    yield make, kb, tmp_path / "outside"
    for w in made:
        w.close()


# -- templates: GET /api/kbs/{kb}/templates[/{name}] -------------------------

_TEMPLATE = f"---\ndescription: {SECRET}\nentry_type: note\n---\n{SECRET} body\n"


@pytest.mark.parametrize("form", FORMS)
def test_template_list_skips_a_template_outside_the_kb(world, form):
    make, kb, outside = world
    (kb / "_templates").mkdir()
    (kb / "_templates" / "mine.md").write_text("---\ndescription: mine\n---\nmy body\n")
    _place(form, outside / "t.md", kb / "_templates" / "theirs.md", _TEMPLATE)
    w = make()
    r = w.client.get(f"/api/kbs/{KB}/templates")
    assert r.status_code == 200, r.text
    assert [t["name"] for t in r.json()["templates"]] == ["mine"]
    assert SECRET not in r.text


@pytest.mark.parametrize("form", FORMS)
def test_template_get_refuses_a_template_outside_the_kb(world, form):
    make, kb, outside = world
    _place(form, outside / "t.md", kb / "_templates" / "theirs.md", _TEMPLATE)
    w = make()
    r = w.client.get(f"/api/kbs/{KB}/templates/theirs")
    assert SECRET not in r.text
    assert r.status_code in (404, 422), r.text
    assert "OUTSIDE_KB_ROOT" in r.text or "NOT_FOUND" in r.text


@pytest.mark.parametrize("form", FORMS)
def test_daily_note_copies_no_template_from_outside_the_kb(world, form):
    """``POST /api/daily/{date}`` builds the note from ``_templates/daily.md``:
    a daily template that is not the KB's is never copied into the KB."""
    make, kb, outside = world
    _place(form, outside / "daily.md", kb / "_templates" / "daily.md", _TEMPLATE)
    w = make()
    r = w.client.post("/api/daily/2026-01-02", params={"kb": KB})
    assert SECRET not in r.text
    written = "\n".join(
        p.read_text(errors="replace")
        for p in kb.rglob("*.md")
        if p.is_file() and "_templates" not in p.parts
    )
    assert SECRET not in written


# -- update and get by id: the unmodellable-frontmatter refusals ---------------

#: A duplicate key: the refusal names the key and its line.
_DUPLICATE = f"---\nid: dup\ntitle: Dup\n{SECRET}: 1\n{SECRET}: 2\n---\nbody\n"


@pytest.mark.parametrize("form", FORMS)
def test_update_names_no_key_of_a_file_outside_the_kb(world, form):
    make, kb, outside = world
    _place(form, outside / "dup.md", kb / "notes" / "dup.md", _DUPLICATE)
    w = make()
    r = w.client.put("/api/entries/dup", json={"kb": KB, "title": "New"})
    assert SECRET not in r.text
    assert r.status_code >= 400, r.text
    assert SECRET in (outside / "dup.md").read_text()  # untouched


@pytest.mark.control(
    reason="health rereads only the files its own reconcile read, which already "
    "skips a file outside the KB; kept so a health that walks the KB is caught"
)
@pytest.mark.parametrize("form", FORMS)
def test_index_health_names_no_key_of_a_file_outside_the_kb(world, form):
    make, kb, outside = world
    _place(form, outside / "dup.md", kb / "notes" / "dup.md", _DUPLICATE)
    w = make()
    result = w.cli("index", "health", "--format", "json")
    assert SECRET not in result.output


@pytest.mark.parametrize("form", FORMS)
def test_qa_validate_names_no_key_of_a_file_outside_the_kb(world, form):
    make, kb, outside = world
    _place(form, outside / "dup.md", kb / "notes" / "dup.md", _DUPLICATE)
    w = make()
    for r in (
        w.client.get("/api/qa/validate", params={"kb": KB}),
        w.client.get("/api/qa/validate/dup", params={"kb": KB}),
    ):
        assert SECRET not in r.text


# -- kb.yaml: the schema, and the schema service ------------------------------

_KB_YAML = f"name: {KB}\ntypes:\n  {SECRET}:\n    description: {SECRET}\n"


@pytest.mark.parametrize("form", FORMS)
def test_schema_route_reads_no_kb_yaml_from_outside_the_kb(world, form):
    make, kb, outside = world
    _place(form, outside / "kb.yaml", kb / "kb.yaml", _KB_YAML)
    w = make()
    r = w.client.get(f"/api/kbs/{KB}/schema")
    assert SECRET not in r.text
    assert SECRET not in json.dumps(w.config.get_kb(KB).types or {})


@pytest.mark.parametrize("form", FORMS)
def test_schema_service_reads_no_kb_yaml_from_outside_the_kb(world, form):
    """``pyrite schema add-type`` rewrites kb.yaml from what it read: a kb.yaml
    that is not the KB's is refused, never read into the new one."""
    make, kb, outside = world
    _place(form, outside / "kb.yaml", kb / "kb.yaml", _KB_YAML)
    w = make()
    from pyrite.services.schema_service import SchemaService
    from pyrite.utils.kb_files import KBFileOutsideRootError

    with pytest.raises(KBFileOutsideRootError):
        SchemaService(w.config)._load_kb_yaml(w.config.get_kb(KB))


# -- board.yaml: the software KB's board ---------------------------------------


@pytest.mark.parametrize("form", FORMS)
def test_board_reads_no_board_yaml_from_outside_the_kb(world, form):
    make, kb, outside = world
    board = f"lanes:\n  - name: {SECRET}\n    statuses: [todo]\n"
    _place(form, outside / "board.yaml", kb / "board.yaml", board)
    w = make("software")
    result = w.mcp()._dispatch_tool("sw_board", {"kb_name": KB})
    assert SECRET not in json.dumps(result, default=str)


# -- reviews: the blob hash of the entry file ----------------------------------


@pytest.mark.parametrize("form", FORMS)
def test_review_hashes_no_file_outside_the_kb(world, form):
    make, kb, outside = world
    w = make()
    # Indexed while it was the KB's own; then it becomes a link out.
    (kb / "notes" / "later.md").write_text(_note("later"))
    w.index(KB)
    (kb / "notes" / "later.md").unlink()
    _place(form, outside / "later.md", kb / "notes" / "later.md", _note("later", SECRET))
    from pyrite.utils.hashing import git_blob_hash

    secret_hash = git_blob_hash((outside / "later.md").read_bytes())
    r = w.client.post(
        "/api/reviews",
        json={
            "entry_id": "later",
            "kb_name": KB,
            "reviewer": "r",
            "reviewer_type": "user",
            "result": "pass",
        },
    )
    assert secret_hash not in r.text
    rows = w.db.execute_sql("SELECT content_hash FROM review", {})
    assert secret_hash not in repr(rows)


# -- the software KB's ADR numbering --------------------------------------------


@pytest.mark.parametrize("form", FORMS)
def test_adr_numbering_reads_no_file_outside_the_kb(world, form):
    """``sw_create_adr`` numbers from the numbers the KB's files hold; a file
    that is not the KB's holds none (the number would tell what it says)."""
    make, kb, outside = world
    adr = "---\nid: adr-0731\ntitle: Theirs\ntype: adr\nadr_number: 731\n---\nbody\n"
    _place(form, outside / "adr.md", kb / "adrs" / "adr-0731.md", adr)
    w = make("software")
    from pyrite_software_kb.adr_listing import adr_numbers_in_files

    assert 731 not in adr_numbers_in_files(w.config.get_kb(KB))
