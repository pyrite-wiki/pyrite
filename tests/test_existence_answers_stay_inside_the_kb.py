"""A locator whose answer is "this id (or this file's id) exists" reads only
files the KB would read: inside its root, with one name (``KBFiles``).

A file reached through a link that leaves the KB, or with a second hard link,
is not the KB's. A create, a rename's target check and a create's collision
message must answer the same whether or not such a file holds the id, so none
of them says anything about what that file holds.

``KBRepository.find_file`` without ``inside`` still locates such a file for a
read or a delete by id, so the caller gets the reader's own refusal
(``OUTSIDE_KB_ROOT``) rather than a miss; that is not an existence answer
about outside content, and is covered by
``tests/test_kb_file_reads_stay_inside_the_kb.py``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from pyrite.config import KBConfig
from pyrite.services.kb_service import KBService
from tests._world import build_world

SECRET = "secretoutsidethekb7d41"
KB = "k"
FORMS = ["symlink", "hardlink"]


def _place(form: str, outside: Path, inside: Path, text: str) -> None:
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

    def make():
        w = build_world(tmp_path / "world", kbs=[KBConfig(name=KB, path=kb, kb_type="generic")])
        made.append(w)
        return w

    yield make, kb, tmp_path / "outside"
    for w in made:
        w.close()


@pytest.mark.parametrize("form", FORMS)
def test_create_is_not_refused_by_an_outside_file_holding_the_id(world, form):
    """``KBService`` create's "already exists" check (``KBRepository.exists``):
    an id held only by a file outside the KB is free."""
    make, kb, outside = world
    _place(form, outside / "o.md", kb / "notes" / "elsewhere.md", _note("probe-id"))
    w = make()
    r = w.client.post(
        "/api/entries",
        json={"kb": KB, "entry_type": "note", "title": "Probe Id", "body": "mine"},
    )
    assert r.status_code in (200, 201), r.text
    assert (kb / "notes" / "probe-id.md").read_text().count("mine") == 1
    assert "probe-id" in (outside / "o.md").read_text()  # untouched


@pytest.mark.parametrize("form", FORMS)
def test_create_collision_message_does_not_name_an_outside_files_id(world, form):
    """``holder_of_path`` names, in a create's "would resolve to a file that
    already exists" refusal (a ``file_pattern`` type whose path is taken),
    the id the file at that path holds. A file outside the KB holds nothing
    the KB may name. Module surface: the REST create of a plain note picks a
    free name instead (``probe-id-2.md``), so it never reaches the message."""
    from pyrite.storage.repository import KBRepository

    make, kb, outside = world
    _place(form, outside / "o.md", kb / "notes" / "probe-id.md", _note(SECRET))
    (kb / "notes" / "own.md").write_text(_note("own"))
    w = make()
    repo = KBRepository(w.config.get_kb(KB))
    assert repo.holder_of_path(kb / "notes" / "own.md") == "own"  # control
    assert repo.holder_of_path(kb / "notes" / "probe-id.md") is None


@pytest.mark.parametrize("form", FORMS)
def test_rename_target_is_not_taken_by_an_outside_file_holding_it(world, form):
    """``KBRepository.rename``'s target check: a new id held only by a file
    outside the KB does not refuse the rename."""
    make, kb, outside = world
    _place(form, outside / "o.md", kb / "notes" / "elsewhere.md", _note("new-id"))
    w = make()
    w.index(KB)
    result = KBService(w.config, w.db).rename_entry("inside", "new-id", KB)
    assert result["new_id"] == "new-id"
    assert "id: new-id" in (kb / "notes" / "inside.md").read_text()


def test_rename_of_a_hard_linked_entry_reads_nothing_and_is_not_found(world):
    """A file with a second name is not the KB's: the id it holds matches
    nothing, so the rename answers not-found and nothing is read or
    written (maintainer decision 2026-11-10)."""
    from pyrite.exceptions import EntryNotFoundError

    make, kb, outside = world
    _place("hardlink", outside / "o.md", kb / "notes" / "linked.md", _note("linked"))
    w = make()
    with pytest.raises(EntryNotFoundError):
        KBService(w.config, w.db).rename_entry("linked", "renamed", KB)
    assert "id: linked" in (outside / "o.md").read_text()


# -- text built from a file's content comes only from files the KB would read --


@pytest.mark.parametrize("form", FORMS)
def test_update_not_found_hint_does_not_name_an_outside_files_id(world, form):
    """``not_found_hint``: an update of an id that nothing holds explains when
    the file named like it holds another id. A file outside the KB holds
    nothing the KB may name."""
    make, kb, outside = world
    _place(form, outside / "o.md", kb / "notes" / "probe-id.md", _note(SECRET))
    w = make()
    r = w.client.put("/api/entries/probe-id", json={"kb": KB, "body": "edit"})
    assert r.status_code in (400, 404), r.text
    assert SECRET not in r.text


@pytest.mark.control(reason="the hint still explains a file of the KB's own")
def test_update_not_found_hint_still_names_an_inside_files_id(world):
    make, kb, _outside = world
    (kb / "notes" / "probe-id.md").write_text(_note("other-id"))
    w = make()
    r = w.client.put("/api/entries/probe-id", json={"kb": KB, "body": "edit"})
    assert r.status_code == 404, r.text
    assert "other-id" in r.text


@pytest.mark.parametrize("form", FORMS)
def test_delete_candidates_are_judged_only_from_files_the_kb_reads(world, form):
    """``files_to_delete`` / ``_holds``: whether a candidate holds the id, and
    how, is never read from a file outside the KB."""
    from pyrite.storage.repository import KBRepository

    make, kb, outside = world
    _place(form, outside / "o.md", kb / "notes" / "probe-id.md", _note("probe-id"))
    w = make()
    repo = KBRepository(w.config.get_kb(KB))
    # The same answer whatever the outside file holds: it holds nothing.
    assert repo._holds(kb / "notes" / "probe-id.md", "probe-id") is None
    assert repo._holds(kb / "notes" / "probe-id.md", "anything-else") is None
    assert repo._holds(kb / "notes" / "inside.md", "inside") == "explicit"  # control


@pytest.mark.parametrize(
    "form",
    [
        pytest.param(
            "symlink",
            marks=pytest.mark.control(reason="a link on the path was already refused by name"),
        ),
        "hardlink",
    ],
)
def test_a_new_entrys_path_does_not_depend_on_an_outside_files_id(world, form):
    """``_free_default_path``: a path taken by a file outside the KB stays
    taken whatever that file holds, so the create's answer cannot tell."""
    from pyrite.storage.repository import KBRepository

    make, kb, outside = world
    answers = []
    for held in ("probe-id", SECRET):
        (kb / "notes" / "probe-id.md").unlink(missing_ok=True)
        _place(form, outside / f"{held}.md", kb / "notes" / "probe-id.md", _note(held))
        w = make()
        repo = KBRepository(w.config.get_kb(KB))
        try:
            answers.append(repo._free_default_path("probe-id", "notes").name)
        except Exception as e:  # a link out is refused before anything is read
            answers.append(type(e).__name__)
    assert answers[0] == answers[1]
    assert answers[0] in ("probe-id.md", "KBFileOutsideRootError")


# -- by-id answers do not depend on what a file outside the KB holds (D1) -------

HELD = "held-outside-7c1"  # the id the outside file holds
GUESS = "not-held-7c1"  # an id nothing holds


def _shape(answer: dict, entry_id: str) -> str:
    """An answer with the asked id taken out, so two answers compare as text."""
    return json.dumps(answer, sort_keys=True).replace(entry_id, "<id>")


@pytest.mark.parametrize("form", FORMS)
@pytest.mark.parametrize("op", ["update", "delete"])
def test_rest_by_id_answers_are_the_same_whatever_an_outside_file_holds(world, form, op):
    make, kb, outside = world
    _place(form, outside / "o.md", kb / "notes" / "innocuous.md", _note(HELD))
    w = make()
    answers = []
    for entry_id in (HELD, GUESS):
        if op == "update":
            r = w.client.put(f"/api/entries/{entry_id}", json={"kb": KB, "body": "edit"})
        else:
            r = w.client.delete(f"/api/entries/{entry_id}?kb={KB}")
        answers.append((r.status_code, _shape(r.json(), entry_id)))
    assert answers[0] == answers[1], answers
    assert answers[0][0] == 404
    assert (outside / "o.md").read_text() == _note(HELD)


@pytest.mark.parametrize("form", FORMS)
@pytest.mark.parametrize(
    "tool",
    [
        pytest.param(
            "kb_get",
            marks=pytest.mark.control(reason="a read by id already skipped such a file"),
        ),
        "kb_update",
        "kb_delete",
    ],
)
def test_mcp_by_id_answers_are_the_same_whatever_an_outside_file_holds(world, form, tool):
    make, kb, outside = world
    _place(form, outside / "o.md", kb / "notes" / "innocuous.md", _note(HELD))
    w = make()
    server = w.mcp("admin")
    answers = []
    for entry_id in (HELD, GUESS):
        args = {"entry_id": entry_id, "kb_name": KB}
        if tool == "kb_update":
            args["body"] = "edit"
        answers.append(_shape(server._dispatch_tool(tool, args), entry_id))
    assert answers[0] == answers[1], answers
    assert (outside / "o.md").read_text() == _note(HELD)


@pytest.mark.parametrize("form", FORMS)
def test_rename_answers_the_same_whatever_an_outside_file_holds(world, form):
    from pyrite.exceptions import PyriteError

    make, kb, outside = world
    _place(form, outside / "o.md", kb / "notes" / "innocuous.md", _note(HELD))
    w = make()
    answers = []
    for entry_id in (HELD, GUESS):
        try:
            KBService(w.config, w.db).rename_entry(entry_id, "renamed-7c1", KB)
            answers.append("done")
        except PyriteError as e:
            answers.append((e.error_code, str(e).replace(entry_id, "<id>")))
    assert answers[0] == answers[1], answers
