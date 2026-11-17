"""A write never places an entry where the index walk does not list it (#1552).

The property: a file a write places as an entry is a file the walk lists.
One predicate decides "would the walk list this path"
(``pyrite/storage/entry_paths.py``); the walk applies it, and every writer of
a new entry-file name asks it before anything is written.

- Where Pyrite chose the file name (the default ``<id>.md``), it chooses a
  listed one: an id whose file name the walk skips (``readme`` in any case)
  is written under the next free name by the existing ``-N`` rule
  (``readme-2.md``); the id stays what the caller named (maintainer, 2026-11-17).
- Where the operator's ``kb.yaml`` (a static ``subdirectory``, a
  ``file_pattern``) or the entry's own field value (a templated
  ``subdirectory``) chose the path, the write is refused with
  ``VALIDATION_FAILED`` and nothing is written: no file, no folder, no row. A
  dry run answers as the real write.
- A move toward an excluded folder (the templated move on update,
  ``index reconcile --apply``) moves nothing and says why.
- ``create_entry_text`` and ``move_entry_text`` refuse an excluded path when
  called directly: the backstop.

The reserved-name decision is built on the folded name (``entry_paths.fold``:
case, Unicode case folding and normalisation, NTFS's trailing dots and
spaces), never on a probe of the disk, so these tests pass on a
case-insensitive filesystem (macOS) and a case-sensitive one (CI) alike.

Before this, ``pyrite create --title README`` wrote ``notes/readme.md``, the
next ``index sync`` retired its row, and the entry was gone while its file
stayed (the issue's four steps, ``test_the_issues_four_steps``).
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.exceptions import ValidationError
from pyrite.services.acting import local_process
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager
from pyrite.storage.repository import KBRepository

KB = "k"

#: Every kind of path the walk skips below the KB root, as the groom counted
#: them from ``_lexically_listed`` plus ``rglob("*.md")``.
PATH_CLASSES = ("readme", "hidden", "templates", "not_markdown")

KB_YAML = f"""\
name: {KB}
kb_type: generic
types:
  note:
    description: a plain note
  hid:
    subdirectory: .private
  tpl:
    subdirectory: _templates/x
  st:
    subdirectory: "st/{{phase}}"
  pat_title:
    file_pattern: "{{title}}.md"
  pat_dot:
    file_pattern: ".{{id}}.md"
  pat_txt:
    file_pattern: "{{id}}.txt"
  pat_upper:
    file_pattern: "{{id}}.MD"
  tpl_case:
    subdirectory: memos/_TeMpLaTeS
  tpl_upper:
    subdirectory: _Templates
  tpl_nested:
    subdirectory: notes/_TEMPLATES
  stc:
    subdirectory: "stc/_Templates/{{phase}}"
"""

#: A hand-kept template folder (#1552 round 1, E1): on a case-insensitive disk
#: `memos/_TeMpLaTeS/x.md` IS `memos/_templates/x.md`. Every KB here has it.
HAND_TEMPLATE = ("memos/_templates/memo.md", "---\ntitle: '{{title}}'\n---\n\n")

#: A path the operator's config or the entry's data chose, which the walk
#: skips: (entry type, extra fields, title, the path class, the field to blame
#: or None when kb.yaml alone chose it). Every name builder that can produce
#: an excluded path that Pyrite did not choose is here: a static
#: subdirectory, a templated one, and a file_pattern (its literal text or a
#: value it takes).
REFUSED: dict[str, tuple[str, dict, str, str, str | None]] = {
    "static-subdir-hidden": ("hid", {}, "H1", "hidden", None),
    "static-subdir-templates": ("tpl", {}, "T1", "templates", None),
    "templated-subdir-hidden": ("st", {"phase": ".draft"}, "S1", "hidden", "phase"),
    "templated-subdir-templates": ("st", {"phase": "_templates"}, "S2", "templates", "phase"),
    "templated-subdir-templates-case": ("st", {"phase": "_Templates"}, "S3", "templates", "phase"),
    "pattern-value-readme": ("pat_title", {}, "README", "readme", None),
    "pattern-literal-dot": ("pat_dot", {}, "D1", "hidden", None),
    "pattern-suffix-txt": ("pat_txt", {}, "X1", "not_markdown", None),
    "pattern-suffix-upper": ("pat_upper", {}, "U1", "not_markdown", None),
    # Round 1 (E1): a static spelling that folds to `_templates`. On APFS or
    # NTFS it is the hand-kept `memos/_templates/` folder; on ext4 it is a
    # folder the walk now skips too. Refused on both.
    "static-subdir-templates-mixed-case": ("tpl_case", {}, "Quarter Plan", "templates", None),
    "static-subdir-templates-capital": ("tpl_upper", {}, "T2", "templates", None),
    "static-subdir-templates-upper-nested": ("tpl_nested", {}, "T3", "templates", None),
    "templated-subdir-literal-templates-case": ("stc", {"phase": "open"}, "S4", "templates", None),
}

#: Ids whose default file name the walk skips: Pyrite chose the name, so it
#: chooses around it. Three spellings, and the title that derives one.
README_IDS = {
    "title README": ({"title": "README"}, "readme"),
    "explicit README": ({"title": "Doc one", "id": "README"}, "README"),
    "explicit readme": ({"title": "Doc two", "id": "readme"}, "readme"),
    "explicit ReadMe": ({"title": "Doc three", "id": "ReadMe"}, "ReadMe"),
}


def _note(id_: str | None, title: str, *, etype: str = "note", extra: str = "") -> str:
    id_line = f"id: {id_}\n" if id_ else ""
    return f"---\n{id_line}type: {etype}\ntitle: {title}\n{extra}---\n\nbody\n"


def _tree(root: Path) -> dict[str, bytes | str]:
    """Every name under ``root`` (folders included) except the index's own
    files: a folder as ``<dir>``, a file as its bytes."""
    out: dict[str, bytes | str] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        if ".git" in dirnames:
            dirnames.remove(".git")
        for name in sorted(dirnames):
            out[str((Path(dirpath) / name).relative_to(root))] = "<dir>"
        for name in sorted(filenames):
            p = Path(dirpath) / name
            out[str(p.relative_to(root))] = p.read_bytes()
    return out


def _write_hand_template(root: Path) -> None:
    rel, text = HAND_TEMPLATE
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(text)


class Env:
    """A KB on disk with an index, driven through ``KBService``."""

    def __init__(self, tmp_path: Path, *, git: bool = False, kb_yaml: str = KB_YAML):
        self.tmp = tmp_path
        self.root = tmp_path / KB
        self.root.mkdir(parents=True, exist_ok=True)
        if git:
            subprocess.run(
                ["git", "init", "-q"], cwd=self.root, check=True, capture_output=True, timeout=30
            )
        (self.root / "kb.yaml").write_text(kb_yaml)
        _write_hand_template(self.root)
        self.config = PyriteConfig(
            knowledge_bases=[KBConfig(name=KB, path=self.root, kb_type="generic")],
            settings=Settings(index_path=tmp_path / "index.db", auto_embed=False),
        )
        self.db = PyriteDB(tmp_path / "index.db")
        self.im = IndexManager(self.db, self.config)
        self.im.index_all()

    @property
    def svc(self):
        from pyrite.services.kb_service import KBService

        return KBService(self.config, self.db)

    @property
    def repo(self) -> KBRepository:
        return KBRepository(self.config.get_kb(KB))

    def rows(self) -> dict[str, str]:
        return {
            i: Path(r["file_path"]).relative_to(self.root).as_posix()
            for i, r in self.im._load_indexed_state(KB).items()
        }

    def sync(self) -> dict:
        return self.im.sync_incremental(KB)

    def close(self) -> None:
        self.db.close()


@pytest.fixture(params=[False, True], ids=["plain", "git"])
def env(request, tmp_path):
    e = Env(tmp_path, git=request.param)
    yield e
    e.close()


@pytest.fixture
def plain(tmp_path):
    e = Env(tmp_path)
    yield e
    e.close()


def _assert_found_by_id(env: Env, entry_id: str) -> None:
    """get, update and delete by id all reach the entry, and its file is one
    the walk lists."""
    path = env.repo.find_file(entry_id)
    assert path is not None, f"{entry_id!r} not found by id"
    assert env.repo._listed(path), path
    assert env.svc.get_entry(entry_id, KB, readable_kbs={KB}) is not None


# --------------------------------------------------------------------------
# The issue's four steps
# --------------------------------------------------------------------------


def test_the_issues_four_steps(tmp_path, cli_world):
    """``create --title README``, ``index sync``, ``get readme``: found, and
    update and delete by id work before and after the sync."""
    world = cli_world(kbs=[KBConfig(name=KB, path=tmp_path / "kb4", kb_type="generic")])
    created = world.cli(
        "create", "-k", KB, "-t", "note", "--title", "README", "-b", "body", "--format", "json"
    )
    assert created.exit_code == 0, created.output
    assert world.cli("get", "readme", "-k", KB).exit_code == 0

    synced = world.cli("index", "sync", "-k", KB, "--no-embed")
    assert synced.exit_code == 0, synced.output

    got = world.cli("get", "readme", "-k", KB, "--format", "json")
    assert got.exit_code == 0, got.output
    files = sorted(
        p.relative_to(tmp_path / "kb4").as_posix() for p in (tmp_path / "kb4").rglob("*.md")
    )
    assert files == ["notes/readme-2.md"], files

    updated = world.cli("update", "readme", "-k", KB, "-b", "changed")
    assert updated.exit_code == 0, updated.output
    assert world.cli("index", "sync", "-k", KB, "--no-embed").exit_code == 0
    assert "changed" in (tmp_path / "kb4" / "notes/readme-2.md").read_text()
    deleted = world.cli("delete", "readme", "-k", KB, "--force")
    assert deleted.exit_code == 0, deleted.output
    assert not list((tmp_path / "kb4").rglob("*.md"))


# --------------------------------------------------------------------------
# C1: where Pyrite chose the name, it chooses a listed one
# --------------------------------------------------------------------------


def _writer_create(env: Env, spec: dict) -> None:
    env.svc.create(KB, {"entry_type": "note", "body": "b", **spec})


def _writer_create_entry(env: Env, spec: dict) -> None:
    from pyrite.schema import generate_entry_id

    eid = spec.get("id") or generate_entry_id(spec["title"])
    env.svc.create_entry(KB, eid, spec["title"], "note", "b")


def _writer_bulk(env: Env, spec: dict) -> None:
    [result] = env.svc.bulk_create_entries(KB, [{"entry_type": "note", "body": "b", **spec}])
    assert result.get("created"), result


def _writer_add(env: Env, spec: dict) -> None:
    src = env.tmp / "src" / "in.md"
    src.parent.mkdir(exist_ok=True)
    src.write_text(_note(spec.get("id"), spec["title"]))
    with local_process(True):  # `pyrite add` is the local process's own
        env.svc.add_entry_from_file(KB, src)


#: Every service writer of a new entry; the surfaces (CLI, REST, MCP) reach
#: these and are run below.
CREATE_WRITERS: dict[str, Callable[[Env, dict], None]] = {
    "create": _writer_create,
    "create_entry": _writer_create_entry,
    "bulk": _writer_bulk,
    "add": _writer_add,
}


@pytest.mark.parametrize("writer", list(CREATE_WRITERS))
@pytest.mark.parametrize("case", list(README_IDS))
def test_an_id_whose_file_name_the_walk_skips_is_written_under_a_listed_name(env, case, writer):
    spec, entry_id = README_IDS[case]
    CREATE_WRITERS[writer](env, spec)

    files = [
        p for p in env.root.rglob("*.md") if p.relative_to(env.root).as_posix() != HAND_TEMPLATE[0]
    ]
    assert len(files) == 1, files
    [path] = files
    assert path.name.lower() != "readme.md", path
    assert path.name == f"{entry_id}-2.md", path
    assert f"id: {entry_id}\n" in path.read_text()
    _assert_found_by_id(env, entry_id)

    env.sync()
    assert env.rows() == {entry_id: f"notes/{entry_id}-2.md"}
    _assert_found_by_id(env, entry_id)
    env.svc.update(entry_id, KB, {"body": "changed"})
    assert "changed" in path.read_text()
    env.sync()
    _assert_found_by_id(env, entry_id)
    env.svc.delete_entry(entry_id, KB, readable_kbs={KB})
    assert not path.exists()
    env.sync()
    assert env.rows() == {}


def test_an_unlisted_file_from_an_earlier_version_does_not_stop_the_create(plain):
    """An earlier version wrote ``notes/readme.md`` holding ``readme``. It is
    not listed, so the id is free; the create picks the next listed name and
    leaves the old file as it was (theme 2 reports it)."""
    old = plain.root / "notes" / "readme.md"
    old.parent.mkdir()
    old.write_text(_note("readme", "README"))
    before = old.read_bytes()

    plain.svc.create(KB, {"entry_type": "note", "title": "README", "body": "b"})

    assert old.read_bytes() == before
    assert (plain.root / "notes" / "readme-2.md").is_file()
    plain.sync()
    assert plain.rows() == {"readme": "notes/readme-2.md"}


def test_the_path_a_plugin_recomputes_after_a_create_is_the_file_written(plain):
    """``_free_default_path`` accepts a ``-N`` candidate that already holds
    the entry's own id, so a caller that recomputes the path after the create
    (software-kb's ``_saved_filename``, zettelkasten's and social's CLIs)
    gets ``readme-2.md``, not ``readme-3.md``."""
    written = plain.svc.create(KB, {"entry_type": "note", "title": "README", "body": "b"})
    repo = plain.repo
    entry = written.entry
    assert repo._resolve_file_path(entry, repo._infer_subdir(entry)) == (
        plain.root / "notes" / "readme-2.md"
    )


def test_the_reserved_name_is_decided_on_the_folded_name_not_the_disk(tmp_path):
    """No filesystem probe: the same answer for a root that does not exist,
    on any filesystem's case rules."""
    from pyrite.storage.entry_paths import unlisted_reason

    root = tmp_path / "nowhere"
    for name in ("README.md", "readme.md", "ReadMe.md", "rEaDmE.md"):
        assert unlisted_reason(root, root / "a" / name) == "readme", name
    assert unlisted_reason(root, root / "readme-2.md") is None
    assert unlisted_reason(root, root / ".x" / "a.md") == "hidden"
    assert unlisted_reason(root, root / "a" / ".a.md") == "hidden"
    assert unlisted_reason(root, root / "_templates" / "a.md") == "templates"
    assert unlisted_reason(root, root / "a.MD") == "not_markdown"
    assert unlisted_reason(root, root / "a.txt") == "not_markdown"
    assert unlisted_reason(root, root / "a" / "b.md") is None
    # A folder named like a README or ending in .md is no reason by itself.
    assert unlisted_reason(root, root / "readme.md" / "b.md") is None
    assert not os.path.exists(root)


#: Spellings a filesystem makes one name with `_templates` or `readme.md`
#: (round 1, F1). Case: APFS and NTFS are case-insensitive by default. Unicode
#: case folding: U+017F LATIN SMALL LETTER LONG S folds to `s`
#: (CaseFolding.txt, status C, which a case-insensitive filesystem applies).
#: NTFS through the Win32 API strips trailing dots and spaces from a name
#: ("Naming Files, Paths, and Namespaces"). NFC/NFD (APFS) cannot make a
#: different spelling of these all-ASCII names: no canonical decomposition
#: or composition yields only their letters.
FOLDED_SPELLINGS = {
    "templates": [
        "_Templates",
        "_TEMPLATES",
        "_TeMpLaTeS",
        "_template\u017f",
        "_templates.",
        "_templates ",
        "_TEMPLATES. .",
    ],
    "readme": ["README.MD", "readme.md.", "README.md ", "ReadMe.md. "],
}


@pytest.mark.parametrize(
    ("reason", "spelling"),
    [(r, s) for r, spellings in FOLDED_SPELLINGS.items() for s in spellings],
)
def test_a_spelling_the_filesystem_folds_to_a_reserved_name_is_that_name(
    tmp_path, reason, spelling
):
    """The predicate decides on the folded name, the way the reserved README
    name always did: the walk and every writer see the same answer whatever
    the disk's case rules, and nothing on disk is probed."""
    from pyrite.storage.entry_paths import unlisted_reason

    root = tmp_path / "nowhere"
    if reason == "templates":
        path = root / "notes" / spelling / "a.md"
    else:
        path = root / "notes" / spelling
    assert unlisted_reason(root, path) == reason, spelling
    assert not os.path.exists(root)


@pytest.mark.control(reason="names that only resemble a reserved one are listed, as before")
def test_a_name_that_does_not_fold_to_a_reserved_one_is_listed(tmp_path):
    from pyrite.storage.entry_paths import unlisted_reason

    root = tmp_path / "nowhere"
    for rel in (
        "_template/a.md",
        "templates/a.md",
        "my_templates/a.md",
        "_templates2/a.md",
        "readme2.md",
        "notes/readme-2.md",
        "caf\u00e9/a.md",
        "cafe\u0301/a.md",
    ):
        assert unlisted_reason(root, root / rel) is None, rel


# --------------------------------------------------------------------------
# C2: where kb.yaml or the entry's data chose the path, the write is refused
# --------------------------------------------------------------------------


def _spec(case: str) -> dict:
    etype, extra, title, _reason, _field = REFUSED[case]
    return {"entry_type": etype, "title": title, "body": "b", **extra}


def _assert_refusal(exc_or_result, case: str) -> None:
    """VALIDATION_FAILED, a message naming the reserved part, and an
    ``errors`` row exactly when a field value caused it."""
    _etype, extra, _title, _reason, field = REFUSED[case]
    if isinstance(exc_or_result, dict):
        code = exc_or_result.get("error_code") or exc_or_result.get("code")
        rows = exc_or_result.get("errors")
        message = exc_or_result.get("error") or exc_or_result.get("message") or ""
    else:
        code = exc_or_result.error_code
        rows = exc_or_result.errors
        message = str(exc_or_result)
    assert code == "VALIDATION_FAILED", exc_or_result
    assert "walk" in message or "index" in message, message
    if field is None:
        assert not rows, rows
    else:
        assert rows and rows[0]["field"] == field, rows
        assert rows[0]["rule"] == "entry_path", rows
        assert rows[0]["got"] == extra[field], rows


def _refused_create(env: Env, case: str):
    with pytest.raises(ValidationError) as info:
        env.svc.create(KB, _spec(case))
    return info.value


def _refused_bulk(env: Env, case: str):
    [result] = env.svc.bulk_create_entries(KB, [_spec(case)])
    assert not result.get("created"), result
    return result


def _refused_bulk_dry(env: Env, case: str):
    [result] = env.svc.bulk_create_entries(KB, [_spec(case)], validate_only=True)
    assert result.get("valid") is False, result
    return result


def _refused_add(env: Env, case: str, *, validate_only: bool = False):
    spec = _spec(case)
    extra = "".join(
        f"{k}: '{v}'\n" for k, v in spec.items() if k not in ("entry_type", "title", "body")
    )
    src = env.tmp / "src" / "in.md"
    src.parent.mkdir(exist_ok=True)
    src.write_text(_note(None, spec["title"], etype=spec["entry_type"], extra=extra))
    if validate_only:
        with local_process(True):
            _entry, result = env.svc.add_entry_from_file(KB, src, validate_only=True)
        assert result["errors"], result
        rows = [r for r in result["errors"] if isinstance(r, dict)]
        return {"error_code": "VALIDATION_FAILED", "errors": rows or None, "message": "walk"}
    with local_process(True), pytest.raises(ValidationError) as info:
        env.svc.add_entry_from_file(KB, src)
    return info.value


REFUSING_WRITERS: dict[str, Callable] = {
    "create": _refused_create,
    "bulk": _refused_bulk,
    "bulk dry run": _refused_bulk_dry,
    "add": _refused_add,
    "add dry run": lambda env, case: _refused_add(env, case, validate_only=True),
}


@pytest.mark.parametrize("writer", list(REFUSING_WRITERS))
@pytest.mark.parametrize("case", list(REFUSED))
def test_a_path_config_or_data_chose_that_the_walk_skips_is_refused_and_nothing_is_written(
    env, case, writer
):
    before = _tree(env.root)
    rows_before = env.rows()

    refusal = REFUSING_WRITERS[writer](env, case)

    if writer == "add dry run":
        # add's dry run reports rows, not the message; the code is the same
        # refusal the real add raises (pinned by the "add" cell).
        pass
    else:
        _assert_refusal(refusal, case)
    assert _tree(env.root) == before
    assert env.rows() == rows_before
    env.sync()
    assert env.rows() == rows_before


@pytest.mark.parametrize(
    ("case", "says"),
    [
        ("pattern-value-readme", "change the field values it uses (such as the title)"),
        ("pattern-literal-dot", "cannot place any entry; change it in kb.yaml"),
        ("pattern-suffix-txt", "cannot place any entry; change it in kb.yaml"),
        ("static-subdir-hidden", "Change the type's `subdirectory` in kb.yaml."),
        ("templated-subdir-hidden", "Give `phase` another value."),
    ],
)
def test_the_refusal_says_what_the_caller_can_change(plain, case, says):
    """Round 1, T2: a caller whose title a `file_pattern` turned into README
    chose neither the folder nor the name; the refusal says what they can
    change instead of "choose a folder and file name"."""
    refusal = _refused_create(plain, case)
    assert says in str(refusal), str(refusal)
    assert "Choose a folder and file name" not in str(refusal)


SURFACES = ("cli", "rest", "mcp")


def _surface_create(world, surface: str, case: str) -> tuple[str, dict]:
    """Run one create on a surface; return (error code, the error body)."""
    spec = _spec(case)
    if surface == "cli":
        args = ["create", "-k", KB, "-t", spec["entry_type"], "--title", spec["title"]]
        for key, value in spec.items():
            if key not in ("entry_type", "title", "body"):
                args += ["-f", f"{key}={value}"]
        result = world.cli(*args, "--format", "json")
        assert result.exit_code != 0, result.output
        body = json.loads(result.output[result.output.index("{") :])
        return body.get("error_code") or body.get("code"), body
    if surface == "rest":
        metadata = {k: v for k, v in spec.items() if k not in ("entry_type", "title", "body")}
        res = world.client.post(
            "/api/entries",
            json={
                "kb": KB,
                "entry_type": spec["entry_type"],
                "title": spec["title"],
                "metadata": metadata,
            },
        )
        assert res.status_code == 400, res.text
        detail = res.json()["detail"]
        return detail["code"], detail
    result = world.mcp(tier="write")._dispatch_tool("kb_create", {"kb_name": KB, **spec})
    return result.get("error_code"), result


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("case", list(REFUSED))
def test_every_surface_answers_validation_failed_and_writes_nothing(tmp_path, surface, case):
    if surface == "rest":
        pytest.importorskip("fastapi", reason="fastapi not installed")
    from tests._world import build_world

    kb_root = tmp_path / "kbroot"
    kb_root.mkdir()
    (kb_root / "kb.yaml").write_text(KB_YAML)
    _write_hand_template(kb_root)
    world = build_world(
        tmp_path / "w",
        kbs=[KBConfig(name=KB, path=kb_root, kb_type="generic")],
        rest=surface == "rest",
    )
    try:
        before = _tree(kb_root)
        code, body = _surface_create(world, surface, case)
        assert code == "VALIDATION_FAILED", body
        field = REFUSED[case][4]
        if field is not None:
            assert body.get("errors") and body["errors"][0]["field"] == field, body
        assert _tree(kb_root) == before
    finally:
        world.close()


def test_cli_import_refuses_the_record_and_writes_nothing(tmp_path, cli_world):
    kb_root = tmp_path / "kbi"
    kb_root.mkdir()
    (kb_root / "kb.yaml").write_text(KB_YAML)
    world = cli_world(kbs=[KBConfig(name=KB, path=kb_root, kb_type="generic")])
    src = tmp_path / "in.json"
    src.write_text(
        json.dumps([_spec("templated-subdir-hidden"), {"title": "README", "entry_type": "note"}])
    )
    before = _tree(kb_root)
    for args in (("--dry-run",), ()):
        result = world.cli("import", str(src), "-k", KB, *args)
        assert result.exit_code != 0, result.output
        assert "VALIDATION_FAILED" in result.output and "st/.draft/s1.md" in result.output
    after = _tree(kb_root)
    # The README record is written under a listed name; the refused one left nothing.
    assert set(after) - set(before) == {"notes", "notes/readme-2.md"}, sorted(after)


# --------------------------------------------------------------------------
# C3: a move toward an excluded folder keeps the file where it is
# --------------------------------------------------------------------------

MOVE_VALUES = {"hidden": ".archived", "templates": "_templates"}


@pytest.mark.parametrize("value", list(MOVE_VALUES.values()), ids=list(MOVE_VALUES))
def test_a_templated_move_toward_an_excluded_folder_keeps_the_file(env, value):
    env.svc.create(
        KB, {"entry_type": "st", "title": "R1", "id": "r1", "body": "b", "phase": "open"}
    )
    path = env.root / "st" / "open" / "r1.md"
    assert path.is_file()

    written = env.svc.update("r1", KB, {"phase": value})

    assert written.not_moved is not None and "walk" in written.not_moved, written.not_moved
    assert f"phase: {value}" in path.read_text() or f"phase: '{value}'" in path.read_text()
    assert not (env.root / "st" / value).exists()
    env.sync()
    assert env.rows() == {"r1": "st/open/r1.md"}
    _assert_found_by_id(env, "r1")


@pytest.mark.skipif(os.name == "nt", reason="symlinks are not reliable on Windows")
def test_an_update_whose_move_target_leaves_the_kb_is_not_reported_failed(plain):
    """Surprise 3: the move's target was computed outside the guard, so a
    refusal there (a templated folder that is a link out of the KB) failed
    an update whose bytes were already published. Now the update succeeds and
    ``not_moved`` says why the file stayed."""
    plain.svc.create(
        KB, {"entry_type": "st", "title": "R1", "id": "r1", "body": "b", "phase": "open"}
    )
    outside = plain.tmp / "outside"
    outside.mkdir()
    (plain.root / "st" / "out").symlink_to(outside)
    path = plain.root / "st" / "open" / "r1.md"

    written = plain.svc.update("r1", KB, {"phase": "out"})

    assert written.not_moved is not None, written
    assert "phase: out" in path.read_text()
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("value", list(MOVE_VALUES.values()), ids=list(MOVE_VALUES))
def test_the_reconcile_toward_an_excluded_folder_moves_nothing_and_says_why(env, value):
    target = env.root / "st" / "open" / "r1.md"
    target.parent.mkdir(parents=True)
    target.write_text(_note("r1", "R1", etype="st", extra=f"phase: '{value}'\n"))
    env.sync()
    before = _tree(env.root)

    for apply in (False, True):
        result = env.svc.reconcile_templated_subdirectories(KB, apply=apply)
        assert result.moved == 0 and result.moves == [], result
        assert [eid for eid, _ in result.errors] == ["r1"], result.errors
        assert "walk" in result.errors[0][1], result.errors

    assert _tree(env.root) == before
    env.sync()
    _assert_found_by_id(env, "r1")


@pytest.mark.parametrize("value", ["_TEMPLATES", "_Templates"])
def test_a_case_variant_field_value_is_refused_by_the_move_and_the_reconcile(env, value):
    """A case-variant field value is refused by the templated move and by the
    reconcile. Red on dev, which had no rule for a move into `_templates`
    (`not_moved` was None). It does not separate round 1 from round 0:
    `expand_subdirectory_template` lowercases a field value, so `_TEMPLATES`
    already reached the predicate as `_templates`."""
    env.svc.create(
        KB, {"entry_type": "st", "title": "R1", "id": "r1", "body": "b", "phase": "open"}
    )
    written = env.svc.update("r1", KB, {"phase": value})
    assert written.not_moved is not None and "walk" in written.not_moved, written.not_moved
    other = env.root / "st" / "open" / "r2.md"
    other.write_text(_note("r2", "R2", etype="st", extra=f"phase: '{value}'\n"))
    env.sync()
    result = env.svc.reconcile_templated_subdirectories(KB, apply=True)
    assert result.moved == 0 and {eid for eid, _ in result.errors} == {"r1", "r2"}, result
    assert not [
        p for p in env.root.rglob("*") if "templates" in p.name.lower() and p.parent.name == "st"
    ]


def test_a_templated_move_into_a_case_variant_literal_folder_keeps_the_file(env):
    """Round 1 (E1, the templated form): `stc/_Templates/{phase}` keeps the
    literal's case, so the target folds to a `_templates` part."""
    path = env.root / "elsewhere" / "r1.md"
    path.parent.mkdir()
    path.write_text(_note("r1", "R1", etype="stc", extra="phase: open\n"))
    env.sync()

    written = env.svc.update("r1", KB, {"phase": "done"})

    assert written.not_moved is not None and "walk" in written.not_moved, written.not_moved
    assert path.is_file() and "phase: done" in path.read_text()
    assert not (env.root / "stc").exists()
    env.sync()
    _assert_found_by_id(env, "r1")


def test_the_reconcile_into_a_case_variant_literal_folder_moves_nothing(env):
    path = env.root / "elsewhere" / "r2.md"
    path.parent.mkdir()
    path.write_text(_note("r2", "R2", etype="stc", extra="phase: done\n"))
    env.sync()
    before = _tree(env.root)

    for apply in (False, True):
        result = env.svc.reconcile_templated_subdirectories(KB, apply=apply)
        assert result.moved == 0 and result.moves == [], result
        assert [eid for eid, _ in result.errors] == ["r2"], result.errors

    assert _tree(env.root) == before
    _assert_found_by_id(env, "r2")


# --------------------------------------------------------------------------
# Round 1, F2: a dry run answers what the real write answers, batch by batch
# --------------------------------------------------------------------------

#: Batches whose names the real write chooses for itself (`-N` for a reserved
#: id, `-N` for a path an earlier item took). The expected answer of each item
#: is the real batch's own result, never a model of it.
PARITY_BATCHES = {
    "readme then readme-2": [{"id": "readme"}, {"id": "readme-2"}],
    "readme-2 then readme": [{"id": "readme-2"}, {"id": "readme"}],
    "ReadMe then readme-2": [{"id": "ReadMe"}, {"id": "readme-2"}],
    "a chain of -N": [{"id": "readme"}, {"id": "readme-2"}, {"id": "readme-2-2"}],
    "README twice": [{"id": "README"}, {"id": "README"}],
    "pattern then default": [
        {"entry_type": "pat_title", "id": "x1", "title": "Pat B"},
        {"entry_type": "pat_title", "id": "x2", "title": "Pat B"},
    ],
}


def _spec_of(item: dict) -> dict:
    return {"entry_type": "note", "title": item.get("id", "T"), "body": "b", **item}


def _verdict(result: dict, *, dry: bool) -> tuple:
    """An item's verdict: created/valid, or refused with its code. Only the
    verdict is compared, never a path: no dry run reports one today (a bulk
    item carries `entry_id`, `add --validate-only` answers `valid`), so the
    parity these tests pin is the answer, not the file name the write will
    use (round 2, T6)."""
    if dry:
        return ("ok",) if result.get("valid") else ("refused", result.get("error_code"))
    return ("ok",) if result.get("created") else ("refused", result.get("error_code"))


@pytest.mark.control(
    reason="these batches agree on dev too: dev wrote id readme as readme.md (its -N rule "
    "steps only for a file held by another id), so no item took the name a later one "
    "wanted; red on this PR's round-0 head 03225dd (E2), where readme took readme-2.md"
)
@pytest.mark.parametrize("batch", list(PARITY_BATCHES))
def test_bulk_validate_answers_each_item_as_the_real_batch_does(tmp_path, batch):
    specs = [_spec_of(i) for i in PARITY_BATCHES[batch]]
    real_env = Env(tmp_path / "real")
    dry_env = Env(tmp_path / "dry")
    try:
        real = real_env.svc.bulk_create_entries(KB, [dict(s) for s in specs])
        before = _tree(dry_env.root)
        dry = dry_env.svc.bulk_create_entries(KB, [dict(s) for s in specs], validate_only=True)
        assert _tree(dry_env.root) == before  # a dry run writes nothing
        assert [_verdict(r, dry=True) for r in dry] == [_verdict(r, dry=False) for r in real], (
            dry,
            real,
        )
    finally:
        real_env.close()
        dry_env.close()


@pytest.mark.control(
    reason="these batches agree on dev too: dev wrote id readme as readme.md (its -N rule "
    "steps only for a file held by another id), so no item took the name a later one "
    "wanted; red on this PR's round-0 head 03225dd (E2), where readme took readme-2.md"
)
@pytest.mark.parametrize("batch", list(PARITY_BATCHES))
def test_cli_import_dry_run_answers_as_the_import(tmp_path, cli_world, batch):
    """The same parity on the CLI's `import --dry-run`: the exit code and the
    count of refused records match the real import's."""
    specs = [_spec_of(i) for i in PARITY_BATCHES[batch]]
    outcomes = {}
    for mode in ("dry", "real"):
        root = tmp_path / f"kb-{mode}"
        root.mkdir()
        (root / "kb.yaml").write_text(KB_YAML)
        world = cli_world(kbs=[KBConfig(name=KB, path=root, kb_type="generic")])
        src = tmp_path / f"in-{mode}.json"
        src.write_text(json.dumps(specs))
        args = ("import", str(src), "-k", KB, "--format", "json")
        result = world.cli(*args, *(("--dry-run",) if mode == "dry" else ()))
        outcomes[mode] = (result.exit_code != 0, result.output.count("ENTRY_EXISTS"))
    assert outcomes["dry"] == outcomes["real"], outcomes


# --------------------------------------------------------------------------
# C4: the backstop
# --------------------------------------------------------------------------

EXCLUDED_PATHS = {
    "readme": "notes/README.md",
    "readme-lower": "notes/readme.md",
    "hidden-folder": ".private/a.md",
    "hidden-file": "notes/.a.md",
    "templates": "_templates/a.md",
    "suffix-txt": "notes/a.txt",
    "suffix-upper": "notes/a.MD",
}


@pytest.mark.parametrize("rel", list(EXCLUDED_PATHS.values()), ids=list(EXCLUDED_PATHS))
def test_create_entry_text_refuses_an_excluded_path(tmp_path, rel):
    from pyrite.storage.entry_writer import create_entry_text

    root = tmp_path / "kb"
    root.mkdir()
    with pytest.raises(ValidationError):
        create_entry_text(root / rel, _note("a", "A"), root=root)
    assert _tree(root) == {}


@pytest.mark.parametrize("rel", list(EXCLUDED_PATHS.values()), ids=list(EXCLUDED_PATHS))
def test_move_entry_text_refuses_an_excluded_destination(tmp_path, rel):
    from pyrite.storage.entry_writer import move_entry_text

    root = tmp_path / "kb"
    (root / "notes").mkdir(parents=True)
    src = root / "notes" / "a.md"
    src.write_text(_note("a", "A"))
    before = _tree(root)
    with pytest.raises(ValidationError):
        move_entry_text(src, src.read_bytes(), root / rel, _note("a", "A"), root=root)
    assert _tree(root) == before


@pytest.mark.control(reason="a listed destination is written as before; guards the refusal's reach")
def test_create_entry_text_writes_a_listed_path(tmp_path):
    from pyrite.storage.entry_writer import create_entry_text

    root = tmp_path / "kb"
    root.mkdir()
    create_entry_text(root / "notes" / "readme-2.md", _note("readme", "R"), root=root)
    assert (root / "notes" / "readme-2.md").is_file()
