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
import logging
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


# -- the id locators: no answer differs by what lies outside the KB -----------
#
# ADR-0052 N1/N4. ``read_id``, ``id_of_file``, ``find_file``, ``exists``,
# ``holder_of_path`` and ``not_found_hint`` answer whether an id exists, which
# file holds it, and (the hint) which id a file holds. Each shape below builds
# the KB twice, identical inside, with two different things outside (A and B).
# Every answer must be the same in both worlds, and none may name an id that
# only the outside holds.

OUTSIDE_ID = "outsideid7d41"

#: Every shape a KB name can take that leads out of the KB, or that a reader
#: could mistake for a file of the KB.
SHAPES = [
    "symlink",  # a link out; A: the target holds the id, B: no target
    "hardlink",  # a second name of a file outside; A: holds the id, B: another id
    "dangling",  # a link out; A: a target that holds another id, B: none
    "fifo",  # a link out; A: a FIFO, B: a regular file holding the id
    "dir-link",  # a folder that is a link out; A: holds the file, B: empty
    "non-utf8-name",  # a link out under a name that is not UTF-8
    "collection",  # __collection.yaml as a link out
    "another-id",  # the hint: the file named like the id holds another id
]


def _outside_world(tmp_path: Path, shape: str, variant: str):
    """Build ``kb`` and ``outside`` under ``tmp_path`` for ``shape``;
    ``variant`` is "A" or "B". Returns (repo, the KB name the shape put there,
    the id looked up)."""
    from pyrite.storage.repository import KBRepository

    kb = tmp_path / variant / "kb"
    outside = tmp_path / variant / "outside"
    (kb / "notes").mkdir(parents=True)
    outside.mkdir(parents=True)
    (kb / "notes" / "inside.md").write_text(_note("inside"))
    looked_up = OUTSIDE_ID
    name = kb / "notes" / f"{OUTSIDE_ID}.md"
    target = outside / "t.md"
    holding = _note(OUTSIDE_ID, SECRET)
    other = _note("other-" + OUTSIDE_ID, SECRET)
    if shape == "symlink":
        if variant == "A":
            target.write_text(holding)
        name.symlink_to(target)
    elif shape == "hardlink":
        target.write_text(holding if variant == "A" else other)
        os.link(target, name)
    elif shape == "dangling":
        if variant == "A":
            target.write_text(other)
        name.symlink_to(target)
    elif shape == "fifo":
        if not hasattr(os, "mkfifo"):
            pytest.skip("no FIFOs here")
        if variant == "A":
            os.mkfifo(target)
        else:
            target.write_text(holding)
        name.symlink_to(target)
    elif shape == "dir-link":
        (outside / "d").mkdir()
        if variant == "A":
            (outside / "d" / f"{OUTSIDE_ID}.md").write_text(holding)
        (kb / "linked").symlink_to(outside / "d", target_is_directory=True)
        name = kb / "linked" / f"{OUTSIDE_ID}.md"
    elif shape == "non-utf8-name":
        if variant == "A":
            target.write_text(holding)
        raw_name = os.fsencode(kb / "notes") + b"/\xff" + OUTSIDE_ID.encode() + b".md"
        try:
            os.symlink(os.fsencode(target), raw_name)
        except (OSError, UnicodeError):
            pytest.skip("the filesystem takes no name that is not UTF-8")
        name = Path(os.fsdecode(raw_name))
    elif shape == "collection":
        (kb / OUTSIDE_ID).mkdir()
        if variant == "A":
            target.write_text(f"id: {OUTSIDE_ID}\ntitle: {SECRET}\n")
        name = kb / OUTSIDE_ID / "__collection.yaml"
        name.symlink_to(target)
    elif shape == "another-id":
        target.write_text(other if variant == "A" else _note("plain", "plain"))
        name.symlink_to(target)
    else:  # pragma: no cover
        raise AssertionError(shape)
    return KBRepository(KBConfig(name=KB, path=kb, kb_type="generic")), name, looked_up


def _locator_answers(repo, name: Path, looked_up: str) -> dict:
    """What each locator says about ``name`` and ``looked_up``, with the
    variant's own folder taken out so two worlds compare."""
    root = str(repo.path.parent)

    def clean(value):
        return None if value is None else str(value).replace(root, "<world>")

    return {
        "id_of_file": clean(repo.id_of_file(name)),
        "read_id": tuple(clean(v) for v in repo.read_id(name)),
        "find_file": clean(repo.find_file(looked_up)),
        "find_other": clean(repo.find_file("other-" + looked_up)),
        "exists": repo.exists(looked_up),
        "holder_of_path": clean(repo.holder_of_path(name)),
        "not_found_hint": clean(repo.not_found_hint(looked_up)),
        "find_inside": clean(repo.find_file("inside")),
    }


@pytest.mark.parametrize("shape", SHAPES)
def test_the_id_locators_answer_nothing_from_outside_the_kb(tmp_path, shape, caplog):
    # The refusals are logged with the path; a name that is not UTF-8 cannot
    # cross to the xdist controller in a captured log, so nothing is logged.
    caplog.set_level(logging.CRITICAL)
    a = _locator_answers(*_outside_world(tmp_path, shape, "A"))
    b = _locator_answers(*_outside_world(tmp_path, shape, "B"))
    assert a == b, f"{shape}: an answer differs by what lies outside the KB"
    assert OUTSIDE_ID not in json.dumps(a).replace(f"{OUTSIDE_ID}.md", "").replace(
        f"/{OUTSIDE_ID}/", "/"
    ), a
    assert SECRET not in json.dumps(a), a
    assert a["exists"] is False and a["find_file"] is None, a
    assert a["find_inside"] is not None, "the KB's own entry is still found"


def test_the_raw_read_modes_are_gone():
    """``inside=`` was a raw read mode on the locators (ADR-0052 decision 3,
    N1): there is one read path, and no caller can ask for another."""
    import inspect

    from pyrite.storage import repository

    for name in ("read_id", "find_file", "_files_holding"):
        params = inspect.signature(getattr(repository.KBRepository, name)).parameters
        assert "inside" not in params, name
    assert not hasattr(repository, "_regular_file_text")


def test_rename_loads_its_source_from_the_bytes_read_inside(tmp_path, monkeypatch):
    """The strict load before a rename parses the bytes ``_read_inside`` gave,
    never a second, raw read of the path."""
    from pyrite.storage.repository import KBRepository

    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "a.md").write_text(_note("a"))
    repo = KBRepository(KBConfig(name=KB, path=kb, kb_type="generic"))

    def no_raw_read(self, *args, **kwargs):
        raise AssertionError(f"raw read of {self}")

    monkeypatch.setattr(Path, "read_text", no_raw_read)
    monkeypatch.setattr(Path, "read_bytes", no_raw_read)
    result = repo.rename("a", "b", dry_run=True)
    assert result["new_id"] == "b"


# -- probes of a KB file: a link out answers the same, whatever it points at ---
#
# A probe (``exists``, ``is_file``) follows a link: in front of a read through
# ``KBFiles`` it made a link out whose target exists answer ``OUTSIDE_KB_ROOT``
# and one whose target is missing answer "absent". The answer must not depend
# on that (ADR-0052 N4).

PROBED = [
    ("board.yaml", "software"),
    ("_templates/theirs.md", "generic"),
    ("kb.yaml", "generic"),
    pytest.param(
        "notes/new-one.md",
        "generic",
        marks=pytest.mark.control(
            reason="a create onto a link out already answered OUTSIDE_KB_ROOT either way; "
            "kept so the create's collision probe stays on the KB's side"
        ),
    ),
]


def _probe_answer(w, probed: str) -> str:
    if probed == "board.yaml":
        result = w.mcp()._dispatch_tool("sw_board", {"kb_name": KB})
        return json.dumps(result, default=str, sort_keys=True)
    if probed.startswith("_templates/"):
        r = w.client.get(f"/api/kbs/{KB}/templates/theirs")
        return f"{r.status_code} {r.text}"
    if probed == "kb.yaml":
        r = w.client.post(
            "/api/entries",
            json={"kb": KB, "entry_type": "note", "title": "Fresh", "allow_undeclared": True},
        )
        code = r.json() if r.status_code >= 400 else None
        return f"{r.status_code} {code}"
    r = w.client.post(
        "/api/entries",
        json={"kb": KB, "entry_type": "note", "title": "New One", "allow_undeclared": True},
    )
    code = r.json() if r.status_code >= 400 else None
    return f"{r.status_code} {code}"


@pytest.mark.parametrize(("probed", "kb_type"), PROBED)
def test_a_probe_answers_the_same_whatever_a_link_out_points_at(tmp_path, probed, kb_type):
    answers = []
    for variant in ("A", "B"):
        kb = tmp_path / variant / "kb"
        (kb / "notes").mkdir(parents=True)
        (kb / "notes" / "inside.md").write_text(_note("inside"))
        target = tmp_path / variant / "outside" / "t"
        target.parent.mkdir(parents=True)
        if variant == "A":
            target.write_text(f"name: {KB}\n" if probed == "kb.yaml" else _TEMPLATE)
        link = kb / probed
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(target)
        w = build_world(
            tmp_path / variant / "world", kbs=[KBConfig(name=KB, path=kb, kb_type=kb_type)]
        )
        try:
            answers.append(_probe_answer(w, probed).replace(str(tmp_path / variant), "<w>"))
        finally:
            w.close()
    assert answers[0] == answers[1], answers
    assert SECRET not in answers[0]


# -- a component's ``path:``: entry data that names a place on disk ------------
#
# The software KB warns when a component's ``path:`` does not exist. The path
# comes from entry data and may name anything on the server's disk (absolute,
# or ``..`` out of the KB). A network caller's validation answers the same
# whatever exists there (ADR-0052 N4); the local operator keeps the check.

COMPONENT_PATHS = [
    "../outside/thing.py",  # `..` out of the KB
    "ABSOLUTE",  # an absolute path, filled in per world
    "../../outside/thing.py",  # further up than the repo the KB sits in
    "sub/../../outside/thing.py",  # `..` after a name
]


def _software_kb_yaml() -> str:
    import yaml
    from pyrite_software_kb.preset import SOFTWARE_KB_PRESET

    return yaml.safe_dump({**SOFTWARE_KB_PRESET, "name": KB}, sort_keys=False)


def _component_world(tmp_path: Path, variant: str, path_value: str):
    root = tmp_path / variant
    kb = root / "kb"
    kb.mkdir(parents=True)
    (kb / "kb.yaml").write_text(_software_kb_yaml())
    thing = root / "outside" / "thing.py"
    if path_value == "../../outside/thing.py":
        kb = root / "repo" / "kb"
        kb.mkdir(parents=True)
        (kb / "kb.yaml").write_text(_software_kb_yaml())
    if path_value == "ABSOLUTE":
        path_value = str(tmp_path / "shared-outside" / "thing.py")
        thing = Path(path_value)
    if variant == "A":
        thing.parent.mkdir(parents=True, exist_ok=True)
        thing.write_text("x = 1\n")
    else:
        thing.unlink(missing_ok=True)
    (kb / "components").mkdir()
    (kb / "components" / "c.md").write_text(
        f"---\nid: c\ntitle: C\ntype: component\nkind: module\npath: {path_value}\n---\nbody\n"
    )
    return kb


@pytest.mark.parametrize("path_value", COMPONENT_PATHS)
def test_component_path_validation_answers_a_network_caller_the_same(tmp_path, path_value):
    answers = []
    for variant in ("A", "B"):
        kb = _component_world(tmp_path, variant, path_value)
        w = build_world(
            tmp_path / variant / "world", kbs=[KBConfig(name=KB, path=kb, kb_type="software")]
        )
        try:
            # The single-entry check is the one that passes the KB's path.
            r = w.client.get("/api/qa/validate/c", params={"kb": KB})
            assert r.status_code == 200, r.text
            answers.append(r.text.replace(str(tmp_path / variant), "<w>"))
        finally:
            w.close()
    assert answers[0] == answers[1], answers
    assert "path_not_found" not in answers[0], "a network caller gets no path finding"


@pytest.mark.control(reason="the local operator keeps the path check: what must not change")
def test_component_path_validation_keeps_the_check_for_the_local_process(tmp_path):
    """The operator's own CLI still warns about a component path that does not
    exist, and not about one that does."""
    from pyrite.services.acting import local_process
    from pyrite_software_kb.validators import validate_software_kb

    (tmp_path / "pyrite").mkdir()
    (tmp_path / "pyrite" / "x.py").write_text("")
    kb = tmp_path / "kb"
    kb.mkdir()
    context = {"kb_path": str(kb)}
    with local_process():
        present = validate_software_kb(
            "component", {"kind": "module", "path": "pyrite/x.py"}, context
        )
        missing = validate_software_kb(
            "component", {"kind": "module", "path": "pyrite/y.py"}, context
        )
    assert not any(e.get("rule") == "path_not_found" for e in present)
    assert any(e.get("rule") == "path_not_found" for e in missing)


# -- the two local-only readers ------------------------------------------------


def test_add_entry_from_file_is_for_the_local_process_only(tmp_path):
    """``KBService.add_entry_from_file`` reads a path the caller names, outside
    any KB: the CLI's ``pyrite add``. The server holds the service, so the
    method refuses unless the call is the local process, before a byte is
    read."""
    from pyrite.exceptions import PyriteError
    from pyrite.services.acting import local_process

    make, kb, outside = world_fixture_paths(tmp_path)
    source = outside / "source.md"
    source.parent.mkdir(parents=True)
    source.write_text(f"---\ntitle: {SECRET}\ntype: note\n---\n{SECRET}\n")
    w = build_world(tmp_path / "w", kbs=[KBConfig(name=KB, path=kb, kb_type="generic")])
    try:
        from pyrite.services.kb_service import KBService

        svc = KBService(w.config, w.db)
        with pytest.raises(PyriteError) as refused:
            svc.add_entry_from_file(KB, source, validate_only=True)
        assert SECRET not in str(refused.value)
        with local_process():
            entry, _ = svc.add_entry_from_file(KB, source, validate_only=True)
        assert entry.title == SECRET
    finally:
        w.close()


def world_fixture_paths(tmp_path: Path):
    kb = tmp_path / "kb"
    (kb / "notes").mkdir(parents=True)
    return None, kb, tmp_path / "outside"


def test_entry_has_no_raw_file_loader():
    """``Entry.load(path)`` read any path it was given and had no caller: it is
    gone, and an entry file is loaded by ``KBRepository``, inside its KB."""
    from pyrite.models.base import Entry

    assert "load" not in vars(Entry)


def test_qa_reads_no_error_from_a_kb_yaml_outside_the_kb(tmp_path):
    """QA's folder check opened kb.yaml through a link out of the KB: the
    error from what lies outside (a folder there, a file it cannot open)
    reached the caller, and a dangling link said nothing. Both answer the same."""
    answers = []
    for variant in ("A", "B"):
        kb = tmp_path / variant / "kb"
        (kb / "notes").mkdir(parents=True)
        (kb / "notes" / "inside.md").write_text(_note("inside"))
        target = tmp_path / variant / "outside" / "kb.yaml"
        target.parent.mkdir(parents=True)
        if variant == "A":
            target.mkdir()  # opening it fails with an OS error
        (kb / "kb.yaml").symlink_to(target)
        w = build_world(tmp_path / variant / "world", kbs=[KBConfig(name=KB, path=kb)])
        try:
            r = w.client.get("/api/qa/validate", params={"kb": KB})
            answers.append(f"{r.status_code} {r.text}".replace(str(tmp_path / variant), "<w>"))
        finally:
            w.close()
    assert answers[0] == answers[1], answers


def test_registering_a_folder_writes_nothing_through_a_kb_yaml_link_out(tmp_path):
    """A preset is written only into a folder with no kb.yaml; a kb.yaml that
    is a dangling link out of the folder is there, so nothing is written
    through it (or through a template that is one)."""
    from pyrite.services.kb_registry_service import KBRegistryService

    kb = tmp_path / "kb"
    kb.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (kb / "kb.yaml").symlink_to(outside / "kb.yaml")
    w = build_world(tmp_path / "w", kbs=[])
    try:
        KBRegistryService(w.config, w.db)._apply_preset(kb, KB, "software", "d")
    finally:
        w.close()
    assert list(outside.iterdir()) == []


# -- fix round 1: list, probe and create sites -----------------------------------

#: A folder the software preset makes, as a link out of the new KB.
PRESET_LINKS = ["_templates", "adrs"]


@pytest.mark.parametrize("linked", PRESET_LINKS)
def test_registering_answers_the_same_whatever_a_preset_folder_link_points_at(tmp_path, linked):
    """Registering a software KB whose ``_templates`` or a preset folder is a
    link out of it: the same refusal whether the outside target exists or
    not, and nothing written (no kb.yaml, no registry row)."""
    from pyrite.services.kb_registry_service import KBRegistryService
    from pyrite.storage.models import KB as KB_ROW

    answers = []
    for variant in ("A", "B"):
        root = tmp_path / variant
        kb = root / "roots" / "newkb"
        kb.mkdir(parents=True)
        target = root / "outside" / "d"
        if variant == "A":
            target.mkdir(parents=True)
        (kb / linked).symlink_to(target, target_is_directory=True)
        w = build_world(root / "w", kbs=[], settings={"kb_roots": [str(root / "roots")]})
        try:
            try:
                KBRegistryService(w.config, w.db).add_kb(
                    name="newkb", path=str(kb), kb_type="software"
                )
                answers.append("registered")
            except Exception as e:  # the refusal is the answer
                answers.append(type(e).__name__)
            assert not (kb / "kb.yaml").exists(), "kb.yaml written before the refusal"
            assert w.db.session.get(KB_ROW, "newkb") is None, "a registry row was written"
        finally:
            w.close()
        if target.exists():
            assert list(target.iterdir()) == [], "a template was written outside"
    assert answers[0] == answers[1], answers
    assert answers[0] == "KBFileOutsideRootError"


def test_a_collection_link_out_is_listed_by_its_name_on_every_python(tmp_path):
    """``list_all_files``, ``count`` and the by-name lookup list a
    ``__collection.yaml`` that is a link out by its name, whether its target
    exists or not. Python 3.11's ``rglob`` with a literal name listed it only
    when the target existed (3.12 and later always do), so on 3.11 the sync's
    ``skipped``, health and QA differed by what lies outside."""
    from pyrite.storage.repository import KBRepository

    seen = []
    for variant in ("A", "B"):
        kb = tmp_path / variant / "kb"
        (kb / "coll").mkdir(parents=True)
        (kb / "notes.md").write_text(_note("notes"))
        target = tmp_path / variant / "outside" / "c.yaml"
        target.parent.mkdir(parents=True)
        if variant == "A":
            target.write_text("title: x\n")
        (kb / "coll" / "__collection.yaml").symlink_to(target)
        repo = KBRepository(KBConfig(name=KB, path=kb, kb_type="generic"))
        listed = sorted(str(p.relative_to(kb)) for p in repo.list_all_files())
        candidates = [str(p.relative_to(kb)) for p in repo._filename_candidates("collection-coll")]
        seen.append((listed, repo.count(), candidates))
    assert seen[0] == seen[1], seen
    assert "coll/__collection.yaml" in seen[0][0]


def test_hard_link_fault_counts_the_names_own_links(tmp_path):
    """``_hard_link_fault`` reports the name's link count (``lstat``): a link
    out of the KB to a file with two names outside has one name here."""
    from pyrite.storage.repository import _hard_link_fault

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "t.md").write_text("x")
    os.link(outside / "t.md", outside / "second.md")
    link = tmp_path / "kb" / "l.md"
    link.parent.mkdir()
    link.symlink_to(outside / "t.md")
    assert _hard_link_fault(link) is None


@pytest.mark.control(
    reason="bulk_create_entries already answered OUTSIDE_KB_ROOT both ways at 0a2ff42; "
    "_prepare now refuses a path that leads out before its collision probe"
)
def test_a_dry_run_create_and_a_create_answer_alike_onto_a_link_out(tmp_path):
    """A create whose path is a link out of the KB is OUTSIDE_KB_ROOT, dry
    run or not, whatever the link points at."""
    from pyrite.services.kb_service import KBService

    for variant in ("A", "B"):
        kb = tmp_path / variant / "kb"
        kb.mkdir(parents=True)
        target = tmp_path / variant / "outside" / "t.md"
        target.parent.mkdir(parents=True)
        if variant == "A":
            target.write_text(_note("other"))
        w = build_world(tmp_path / variant / "w", kbs=[KBConfig(name=KB, path=kb)])
        try:
            svc = KBService(w.config, w.db)
            entry = {"entry_type": "note", "title": "Linked Out"}
            # Where a create of this title lands, made a link out.
            from pyrite.models.core_types import entry_from_frontmatter
            from pyrite.storage.repository import KBRepository

            repo = KBRepository(w.config.get_kb(KB))
            built = entry_from_frontmatter(
                {"id": "linked-out", "title": "Linked Out", "type": "note"}, ""
            )
            path = repo._resolve_file_path(built, repo._infer_subdir(built))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.symlink_to(target)
            dry = svc.bulk_create_entries(KB, [entry], validate_only=True)[0]
            real = svc.bulk_create_entries(KB, [entry])[0]
            codes = {r.get("error_code") for r in (dry, real)}
            assert codes == {"OUTSIDE_KB_ROOT"}, (variant, dry, real)
        finally:
            w.close()


def test_mcp_kb_discover_searches_only_kb_roots_for_a_network_caller(tmp_path, monkeypatch):
    """``kb_manage discover`` walked the server's working directory for any
    caller. A network caller now searches only ``settings.kb_roots``; with
    none configured, it finds nothing, whatever the server's folders hold."""
    served = tmp_path / "served"
    (served / "private-kb").mkdir(parents=True)
    (served / "private-kb" / "kb.yaml").write_text(f"name: {SECRET}\n")
    monkeypatch.chdir(served)
    w = build_world(tmp_path / "w", kbs=[])
    try:
        result = w.mcp()._dispatch_tool("kb_manage", {"action": "discover"})
    finally:
        w.close()
    assert SECRET not in json.dumps(result, default=str)
    assert result.get("discovered") == 0, result
