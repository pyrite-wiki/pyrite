"""The last whole-file writers write through ``apply()`` (#955, B6 P4, the rest).

ADR-0042 decision 12: one write path. After the link writers (#975) four
writers still emitted or rewrote whole entry files:

- ``KBRepository.rename``: the renamed file was the model re-emitted under
  the new id (a comment, a legacy link item, the file's own spellings lost),
  and every file whose body held a ``[[old]]`` wikilink was read and
  written with newline translation (a CRLF file came back LF), without
  compare-and-replace;
- ``pyrite schema migrate``: a migrated entry was the model re-emitted;
- ``pyrite ids pin``: its own no-follow write, outside the momentary lock
  every other writer takes, so a pin and an update could interleave;
- software-kb's ``migrate-standards``: a regex over the text that missed a
  value with a trailing comment and translated newlines.

Now each sends operations (``Set``, ``Unset``, ``ReplaceBody``) through
``file_operations.apply`` and publishes through ``storage/entry_writer.py``
(compare-and-replace under the lock; ``create_entry_text`` for a new path),
and ``ids pin`` takes the lock around its write. The oracle is bytes outside
the named operations' spans (``file_operations_oracle``).
"""

from __future__ import annotations

import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from pyrite.cli import app as cli_app
from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.storage.database import PyriteDB
from pyrite.storage.file_operations import FrontmatterSpan, Set, Unset
from pyrite.storage.index import IndexManager
from pyrite.storage.repository import KBRepository
from pyrite.utils.frontmatter import Frontmatter, load_frontmatter, split_frontmatter
from tests.file_operations_oracle import op_spans, outside_identical

KB = "rw"

SOURCE = (
    "---\n"
    "# kept by hand\n"
    "id: old-one\n"
    "title: Old   # keep\n"
    "type: note\n"
    "tags: [alpha, beta]  # trailing\n"
    "links:\n"
    "- to: foo          # legacy spelling\n"
    "  type: related\n"
    "  since: 2020\n"
    "---\n"
    "\n"
    "I am the old one.\n"
)

LINKER = (
    "---\n"
    "id: linker\n"
    "title: Linker   # keep\n"
    "type: note\n"
    "summary: see [[old-one]] in the frontmatter too\n"
    "---\n"
    "\n"
    "See [[old-one]] and [[old-one|the old one]], not [[old-one-two]].\n"
)


def _kb(
    tmp_path: Path,
    files: dict[str, str],
    *,
    kb_type: str = "generic",
    kb_yaml: str | None = None,
    git: bool = True,
    crlf: set[str] = frozenset(),
) -> tuple[PyriteConfig, Path]:
    kb_path = tmp_path / "kb"
    kb_path.mkdir(parents=True, exist_ok=True)
    if git:
        subprocess.run(
            ["git", "init", "-q"], cwd=kb_path, check=True, capture_output=True, timeout=30
        )
    if kb_yaml:
        (kb_path / "kb.yaml").write_text(kb_yaml, encoding="utf-8")
    for rel, text in files.items():
        p = kb_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes((text.replace("\n", "\r\n") if rel in crlf else text).encode("utf-8"))
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name=KB, path=kb_path, kb_type=kb_type)],
        settings=Settings(index_path=tmp_path / "index.db", auto_embed=False),
    )
    db = PyriteDB(config.settings.index_path)
    try:
        IndexManager(db, config).index_all()
    finally:
        db.close()
    return config, kb_path


def _text(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


def _span(text: str) -> FrontmatterSpan:
    split = split_frontmatter(text)
    assert isinstance(split, Frontmatter), split
    return FrontmatterSpan(split.yaml_start, split.close_start, split.close_end)


def assert_only_changed(before: str, after: str, ops: list[Any]) -> None:
    span = _span(before)
    spans: list[tuple[int, int]] = []
    for op in ops:
        spans += op_spans(before, span, op)
    assert outside_identical(before, after, spans), f"--- before\n{before!r}\n--- after\n{after!r}"
    if "\r\n" in before:
        assert after.count("\r\n") == after.count("\n"), "a bare LF was written into a CRLF file"


def _cli(config: PyriteConfig, args: list[str]) -> str:
    with (
        patch("pyrite.cli.load_config", return_value=config),
        patch("pyrite.cli.context.load_config", return_value=config),
    ):
        result = CliRunner().invoke(cli_app, args)
    assert result.exit_code == 0, result.output
    return result.output


# --------------------------------------------------------------------------
# rename
# --------------------------------------------------------------------------


def test_a_rename_sets_the_id_and_leaves_the_rest_of_the_file_as_written(tmp_path: Path):
    config, kb_path = _kb(tmp_path, {"notes/old-one.md": SOURCE, "notes/linker.md": LINKER})
    before = _text(kb_path / "notes" / "old-one.md")

    result = KBRepository(config.knowledge_bases[0]).rename("old-one", "new-one")

    assert result["renamed"] and result["links_rewritten"] == 3, result
    assert not (kb_path / "notes" / "old-one.md").exists()
    after = _text(kb_path / "notes" / "new-one.md")
    assert "id: new-one\n" in after, after
    assert "- to: foo          # legacy spelling\n  type: related\n  since: 2020\n" in after
    assert_only_changed(before, after, [Set("id", "new-one")])


@pytest.mark.control(
    reason="rename's stamp already goes through updated_at_stamp with the file's scalar; the control pins #990's check on this path"
)
def test_a_renames_stamp_keeps_the_files_own_spelling(tmp_path: Path):
    """#990: the renamed file's `updated_at` keeps its spelling (a bare
    `T…Z` stays `T…Z`), as an update's does (#971 fix round 1)."""
    stamped = SOURCE.replace(
        "tags: [alpha, beta]  # trailing\n",
        "tags: [alpha, beta]\nupdated_at: 2020-01-01T00:00:00Z\n",
    )
    config, kb_path = _kb(tmp_path, {"notes/old-one.md": stamped})

    KBRepository(config.knowledge_bases[0]).rename("old-one", "new-one")

    after = _text(kb_path / "notes" / "new-one.md")
    line = next(l for l in after.splitlines() if l.startswith("updated_at:"))
    assert re.fullmatch(r"updated_at: \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", line), line
    assert line != "updated_at: 2020-01-01T00:00:00Z", "the stamp did not move"


def test_a_rename_rewrites_only_the_wikilinks_in_a_linking_file(tmp_path: Path):
    """The linking file changes only where a wikilink is: its body (a
    ``ReplaceBody``) and a frontmatter value that holds one (a ``Set`` of
    that value, the comment and the other keys untouched); a CRLF file stays
    CRLF."""
    config, kb_path = _kb(
        tmp_path,
        {"notes/old-one.md": SOURCE, "notes/linker.md": LINKER},
        crlf={"notes/linker.md"},
    )
    linker = kb_path / "notes" / "linker.md"
    before = _text(linker)

    KBRepository(config.knowledge_bases[0]).rename("old-one", "new-one")

    after = _text(linker)
    assert after == before.replace("[[old-one]]", "[[new-one]]").replace(
        "[[old-one|", "[[new-one|"
    ), after
    assert "[[old-one-two]]" in after and after.count("\r\n") == after.count("\n")


def test_the_cli_rename_goes_the_same_way(tmp_path: Path):
    config, kb_path = _kb(tmp_path, {"notes/old-one.md": SOURCE, "notes/linker.md": LINKER})
    before = _text(kb_path / "notes" / "old-one.md")

    _cli(config, ["rename", "old-one", "new-one", "-k", KB, "--format", "json"])

    after = _text(kb_path / "notes" / "new-one.md")
    assert_only_changed(before, after, [Set("id", "new-one")])


def test_a_rename_of_a_file_without_an_id_key_adds_one(tmp_path: Path):
    """A title-derived id (the file holds no ``id:``; the title ``Old`` makes
    it ``old``) is pinned by the rename: the new id is one added line, as
    ``ids pin`` would add it."""
    source = SOURCE.replace("id: old-one\n", "")
    config, kb_path = _kb(tmp_path, {"notes/old-one.md": source})
    before = _text(kb_path / "notes" / "old-one.md")

    KBRepository(config.knowledge_bases[0]).rename("old", "new-one")

    after = _text(kb_path / "notes" / "new-one.md")
    assert "\nid: new-one\n" in after, after
    assert_only_changed(before, after, [Set("id", "new-one")])


# --------------------------------------------------------------------------
# schema migrate
# --------------------------------------------------------------------------

THING_KB_YAML = "name: rw\nkb_type: generic\ntypes:\n  thing:\n    version: 2\n"

THING = (
    "---\n"
    "# hand\n"
    "id: t\n"
    "title: T   # keep\n"
    "type: thing\n"
    "old: 1   # moves to new\n"
    "tags: [a, b]  # t\n"
    "---\n"
    "\n"
    "Body.\n"
)


@pytest.fixture
def thing_migration():
    """A registered migration for ``thing`` 0 -> 2: ``old`` becomes ``new``."""
    from pyrite.migrations import Migration, get_migration_registry

    registry = get_migration_registry()

    def rename_old(data: dict) -> dict:
        data = dict(data)
        data["new"] = data.pop("old")
        return data

    registry.add(Migration("thing", 0, 2, rename_old, "old -> new"))
    try:
        yield
    finally:
        registry._migrations.pop("thing", None)


def test_schema_migrate_writes_what_the_migration_changed_and_nothing_else(
    tmp_path: Path, thing_migration
):
    config, kb_path = _kb(tmp_path, {"things/t.md": THING}, kb_yaml=THING_KB_YAML)
    path = kb_path / "things" / "t.md"
    before = _text(path)

    out = _cli(config, ["schema", "migrate", "--kb", KB])

    assert "Migrated: 1" in out, out
    after = _text(path)
    assert "old:" not in after and "new: 1\n" in after and "_schema_version: 2\n" in after, after
    assert "title: T   # keep\n" in after and "tags: [a, b]  # t\n" in after, after
    assert_only_changed(before, after, [Unset("old"), Set("new", 1), Set("_schema_version", 2)])


@pytest.mark.control(
    reason="the old command also wrote nothing for an up-to-date file; the control pins it while the write changes"
)
def test_schema_migrate_leaves_an_up_to_date_file_alone(tmp_path: Path, thing_migration):
    text = THING.replace("old: 1   # moves to new\n", "new: 1\n_schema_version: 2\n")
    config, kb_path = _kb(tmp_path, {"things/t.md": text}, kb_yaml=THING_KB_YAML)
    path = kb_path / "things" / "t.md"
    before = path.read_bytes()

    out = _cli(config, ["schema", "migrate", "--kb", KB])

    assert "Migrated: 0" in out, out
    assert path.read_bytes() == before


# --------------------------------------------------------------------------
# ids pin, under the lock
# --------------------------------------------------------------------------

UNPINNED = "---\ntitle: No Id   # keep\ntype: note\n---\n\nBody.\n"


def _pin(config: PyriteConfig) -> dict[str, Any]:
    from pyrite.services import id_pin_service as pins

    found = pins.scan(config.knowledge_bases[0])
    return pins.apply(found, pins.plan(found, {}))


def test_a_pin_waits_for_the_momentary_lock(tmp_path: Path):
    """While another writer holds the entry's lock the pin does not write;
    once it is released the pin lands."""
    from pyrite.utils.file_lock import file_lock

    config, kb_path = _kb(tmp_path, {"notes/no-id.md": UNPINNED})
    path = kb_path / "notes" / "no-id.md"
    before = path.read_bytes()
    results: list[dict[str, Any]] = []
    worker = threading.Thread(target=lambda: results.append(_pin(config)))

    with file_lock(path):
        worker.start()
        time.sleep(0.5)
        assert worker.is_alive(), "the pin did not wait for the lock"
        assert path.read_bytes() == before, "the pin wrote while the lock was held"
    worker.join(timeout=10)

    assert not worker.is_alive()
    assert [item["path"] for item in results[0]["pinned"]] == ["notes/no-id.md"], results
    assert "\nid: no-id\n" in _text(path), _text(path)


@pytest.mark.control(
    reason="outside git there is no lock directory and the pin never took one; the control pins D1 (b): the pin still writes, with its own compare-before-replace"
)
def test_a_pin_outside_git_writes_without_a_lock(tmp_path: Path):
    config, kb_path = _kb(tmp_path, {"notes/no-id.md": UNPINNED}, git=False)
    path = kb_path / "notes" / "no-id.md"

    result = _pin(config)

    assert [item["path"] for item in result["pinned"]] == ["notes/no-id.md"], result
    assert "\nid: no-id\n" in _text(path)


# --------------------------------------------------------------------------
# software-kb migrate-standards
# --------------------------------------------------------------------------

STANDARD = (
    "---\n"
    "# hand\n"
    "id: s\n"
    "title: S   # keep\n"
    "type: standard   # legacy type\n"
    "enforced: true   # decides the new type\n"
    "tags: [a, b]  # t\n"
    "---\n"
    "\n"
    "Body.\n"
)


@pytest.fixture
def software_plugin():
    pytest.importorskip("pyrite_software_kb.cli")
    from pyrite.plugins.registry import PluginRegistry
    from pyrite_software_kb.plugin import SoftwareKBPlugin

    PluginRegistry().register(SoftwareKBPlugin())


def _migrate_standards(config: PyriteConfig) -> str:
    with patch("pyrite_software_kb.cli.load_config", return_value=config):
        result = CliRunner().invoke(cli_app, ["sw", "migrate-standards", "--apply", "-k", KB])
    assert result.exit_code == 0, result.output
    return result.output


def test_migrate_standards_sets_the_type_unsets_enforced_and_keeps_the_rest(
    tmp_path: Path, software_plugin
):
    config, kb_path = _kb(
        tmp_path, {"standards/s.md": STANDARD}, kb_type="software", crlf={"standards/s.md"}
    )
    before = _text(kb_path / "standards" / "s.md")

    out = _migrate_standards(config)

    assert "1" in out and "migrated" in out, out
    assert not (kb_path / "standards" / "s.md").exists()
    after = _text(kb_path / "validations" / "s.md")
    assert "type: programmatic_validation   # legacy type\r\n" in after, after
    assert "enforced" not in after, after
    assert_only_changed(before, after, [Set("type", "programmatic_validation"), Unset("enforced")])


# --------------------------------------------------------------------------
# #978 cold read (2026-10-19): a rename is refused only for the entry renamed;
# the migrate version is stamped only on a written result; a move holds one
# lock for both paths and compares bytes; the model's whole-file save cannot
# reach an existing entry (the maintainer's decision 4)
# --------------------------------------------------------------------------

MALFORMED_LINKER = "---\nid: bad\ntitle: [unclosed\ntype: note\n---\n\nSee [[old-one]].\n"
ANCHORED_LINKER = (
    "---\nid: anchored\ntitle: A\ntype: note\ndefaults: &d\n  x: 1\nmore: *d\n---\n\n"
    "See [[old-one]].\n"
)


@pytest.mark.parametrize(
    ("linker_text", "name"),
    [
        pytest.param(MALFORMED_LINKER, "bad", id="malformed-frontmatter"),
        pytest.param(
            ANCHORED_LINKER,
            "anchored",
            id="anchored-frontmatter",
            marks=pytest.mark.control(
                reason="apply refuses an operation on the anchored key alone, so the body rewrite "
                "already went through on 9f87739; pinned beside the malformed case, which is the "
                "evidence"
            ),
        ),
    ],
)
def test_a_rename_is_refused_only_for_the_entry_renamed(
    tmp_path: Path, linker_text: str, name: str
):
    """Item 1, the property: a linking file Pyrite cannot patch as values (a
    frontmatter that does not parse, an anchor) never fails the rename, dry
    run included. Its body's wikilinks are rewritten and its frontmatter
    bytes stay, or the file is skipped and listed in the result."""
    config, kb_path = _kb(tmp_path, {"notes/old-one.md": SOURCE, f"notes/{name}.md": linker_text})
    linker = kb_path / "notes" / f"{name}.md"
    before = _text(linker)
    repo = KBRepository(config.knowledge_bases[0])

    plan = repo.rename("old-one", "new-one", dry_run=True)
    assert plan["renamed"] and plan["dry_run"], plan
    assert _text(linker) == before

    result = repo.rename("old-one", "new-one")

    assert result["renamed"], result
    assert (kb_path / "notes" / "new-one.md").exists()
    assert not (kb_path / "notes" / "old-one.md").exists()
    after = _text(linker)
    split_before, split_after = split_frontmatter(before), split_frontmatter(after)
    assert isinstance(split_before, Frontmatter) and isinstance(split_after, Frontmatter)
    assert split_after.text == split_before.text, "the frontmatter bytes changed"
    skipped = [s["path"] for s in result.get("files_skipped", [])]
    if "[[new-one]]" in after:
        assert "[[old-one]]" not in after and skipped == [], (after, result)
    else:
        assert after == before and skipped == [f"notes/{name}.md"], (after, result)


#: A linker whose frontmatter does not parse and holds a wikilink of its own:
#: the body can be rewritten, the frontmatter cannot (#978 delta review, round 1).
MALFORMED_PARTIAL_LINKER = (
    '---\nid: bad\ntitle: [unclosed\nrelated: "[[old-one]]"\ntype: note\n---\n\nSee [[old-one]].\n'
)
#: The wikilink sits inside the anchored mapping, where `apply` refuses the
#: Set (an anchor elsewhere in the file leaves a plain key patchable).
ANCHORED_PARTIAL_LINKER = (
    '---\nid: anchored\ntitle: A\ntype: note\ndefaults: &d\n  related: "[[old-one]]"\n'
    "more: *d\n---\n\nSee [[old-one]].\n"
)
UNDECODABLE_LINKER = b"---\nid: raw\ntype: note\n---\n\n\xff\xfe See [[old-one]].\n"


def _healthy_linker(i: int) -> str:
    return f"---\nid: ok-{i}\ntitle: Ok {i}\ntype: note\n---\n\nSee [[old-one]] here.\n"


def _plant_fault(kind: str, kb_path: Path, path: Path, tmp_path: Path) -> None:
    """Write the faulty linker at ``path``."""
    if kind == "hard-linked":
        path.write_text(_healthy_linker(9), encoding="utf-8")
        import os

        os.link(path, tmp_path / "another-name-for-the-linker.md")
    elif kind == "undecodable":
        path.write_bytes(UNDECODABLE_LINKER)
    elif kind == "malformed-frontmatter":
        path.write_text(MALFORMED_PARTIAL_LINKER, encoding="utf-8")
    elif kind == "anchored-frontmatter":
        path.write_text(ANCHORED_PARTIAL_LINKER, encoding="utf-8")
    else:  # pragma: no cover - the grid names every kind
        raise AssertionError(kind)


_FAULT_KINDS = ["hard-linked", "undecodable", "malformed-frontmatter", "anchored-frontmatter"]
_POSITIONS = ["first", "middle", "last"]


@pytest.mark.parametrize(
    ("fault", "position"),
    [
        pytest.param(
            fault,
            position,
            id=f"{fault}-{position}",
            marks=pytest.mark.control(
                reason="the planning loop listed a file it could not decode on 3e9f68b already; "
                "pinned beside the faults that failed the rename halfway or went unreported"
            ),
        )
        if fault == "undecodable"
        else pytest.param(fault, position, id=f"{fault}-{position}")
        for fault in _FAULT_KINDS
        for position in _POSITIONS
    ],
)
def test_a_rename_completes_and_lists_every_linker_it_could_not_patch(
    tmp_path: Path, fault: str, position: str
):
    """#978 delta review, round 1, the property: a rename either completes,
    with every linker it could not patch listed in the result with the
    reason, or changes nothing. A per-linker fault (a hard-linked file, bytes
    that do not decode, a frontmatter that does not parse or holds an
    anchor) never fails the rename halfway, whatever its position among the
    linkers; a file whose body was rewritten while its frontmatter's wikilink
    stayed is reported as partial, not counted as done."""
    files = {"notes/old-one.md": SOURCE}
    names = ["0-first", "1-middle", "2-last"]
    at = _POSITIONS.index(position)
    for i, name in enumerate(names):
        if i != at:
            files[f"notes/{name}.md"] = _healthy_linker(i)
    config, kb_path = _kb(tmp_path, files)
    faulty = kb_path / "notes" / f"{names[at]}.md"
    _plant_fault(fault, kb_path, faulty, tmp_path)
    before = faulty.read_bytes()
    healthy = [kb_path / "notes" / f"{n}.md" for i, n in enumerate(names) if i != at]
    repo = KBRepository(config.knowledge_bases[0])

    plan = repo.rename("old-one", "new-one", dry_run=True)
    assert plan["renamed"] and plan["dry_run"], plan
    assert faulty.read_bytes() == before and all("[[old-one]]" in _text(p) for p in healthy)

    result = repo.rename("old-one", "new-one")

    assert result["renamed"], result
    assert (kb_path / "notes" / "new-one.md").exists()
    assert not (kb_path / "notes" / "old-one.md").exists()
    for p in healthy:
        assert "[[new-one]]" in _text(p) and "[[old-one]]" not in _text(p), p
    skipped = {s["path"]: s["reason"] for s in result["files_skipped"]}
    partial = {s["path"]: s["reason"] for s in result["files_partial"]}
    rel = f"notes/{names[at]}.md"
    if fault in ("hard-linked", "undecodable"):
        assert faulty.read_bytes() == before, "a file the rename could not patch was written"
        assert rel in skipped and rel not in partial, result
        assert result["files_rewritten"] == 2, result
        if fault == "hard-linked":
            assert "hard link" in skipped[rel], skipped
    else:
        after = _text(faulty)
        split_before, split_after = (
            split_frontmatter(before.decode("utf-8")),
            split_frontmatter(after),
        )
        assert isinstance(split_before, Frontmatter) and isinstance(split_after, Frontmatter)
        assert split_after.text == split_before.text, "the frontmatter bytes changed"
        assert "[[new-one]]" in split_after.body and "[[old-one]]" not in split_after.body
        assert rel in partial and rel not in skipped, result
        assert "1 wikilink" in partial[rel] and "old-one" in partial[rel], partial
        assert result["files_rewritten"] == 3, result
    for key in ("files_skipped", "files_partial"):
        assert plan[key] == result[key], (key, plan, result)


def test_the_cli_rename_says_what_it_skipped_and_exits_partial(tmp_path: Path):
    """The default output names every file the rename skipped or rewrote
    partly, with the reason, and the outcome is `partial` (exit 3; the
    machine formats carry `outcome: partial`)."""
    import os

    config, kb_path = _kb(
        tmp_path,
        {
            "notes/old-one.md": SOURCE,
            "notes/linker.md": LINKER,
            "notes/bad.md": MALFORMED_PARTIAL_LINKER,
            "notes/twin.md": _healthy_linker(7),
        },
    )
    os.link(kb_path / "notes" / "twin.md", tmp_path / "another-name.md")

    with (
        patch("pyrite.cli.load_config", return_value=config),
        patch("pyrite.cli.context.load_config", return_value=config),
    ):
        shown = CliRunner().invoke(cli_app, ["rename", "old-one", "new-one", "-k", KB])
    assert shown.exit_code == 3, shown.output
    assert "notes/bad.md" in shown.output and "notes/twin.md" in shown.output, shown.output
    assert "hard link" in shown.output and "1 wikilink" in shown.output, shown.output

    # The rename went through; a second one is refused (no old-one), so the
    # machine form is read on a fresh KB of the same shape.
    config2, kb_path2 = _kb(
        tmp_path / "second",
        {"notes/old-one.md": SOURCE, "notes/bad.md": MALFORMED_PARTIAL_LINKER},
    )
    with (
        patch("pyrite.cli.load_config", return_value=config2),
        patch("pyrite.cli.context.load_config", return_value=config2),
    ):
        machine = CliRunner().invoke(
            cli_app, ["rename", "old-one", "new-one", "-k", KB, "--format", "json"]
        )
    assert machine.exit_code == 3, machine.output
    import json

    payload = json.loads(machine.output)
    assert payload["outcome"] == "partial", payload
    assert [p["path"] for p in payload["files_partial"]] == ["notes/bad.md"], payload
    assert payload["files_skipped"] == [], payload


@pytest.mark.parametrize("new_id", ["y", "Y", "n", "N"])
def test_a_rename_to_an_id_a_yaml_1_1_reader_takes_for_a_boolean_quotes_it(
    tmp_path: Path, new_id: str
):
    """Ride-along from #974 (the delta review of #978, round 1): `y`, `Y`, `n`
    and `N` are booleans to a YAML 1.1 reader (the spec; PyYAML's own tables
    let them through), so an id spelled so is written quoted, by a rename as
    by a create."""
    config, kb_path = _kb(tmp_path, {"notes/old-one.md": SOURCE})

    KBRepository(config.knowledge_bases[0]).rename("old-one", new_id)

    after = _text(kb_path / "notes" / f"{new_id}.md")
    assert f'id: "{new_id}"\n' in after, after
    fm, _body = load_frontmatter(after)
    assert fm["id"] == new_id and isinstance(fm["id"], str), fm


@pytest.fixture
def raising_migration():
    """A registered migration for ``thing`` 0 -> 2 that raises."""
    from pyrite.migrations import Migration, get_migration_registry

    registry = get_migration_registry()

    def boom(data: dict) -> dict:
        raise ValueError("this migration cannot run")

    registry.add(Migration("thing", 0, 2, boom, "raises"))
    try:
        yield
    finally:
        registry._migrations.pop("thing", None)


@pytest.fixture
def mutating_migration():
    """A registered migration for ``thing`` 0 -> 2 that changes its input in
    place (a list appended to) and returns it."""
    from pyrite.migrations import Migration, get_migration_registry

    registry = get_migration_registry()

    def mutate(data: dict) -> dict:
        data["tags"].append("migrated")
        return data

    registry.add(Migration("thing", 0, 2, mutate, "appends a tag in place"))
    try:
        yield
    finally:
        registry._migrations.pop("thing", None)


def test_schema_migrate_stamps_nothing_when_the_migration_raises(tmp_path: Path, raising_migration):
    """Item 2, the property: the version is stamped only when the migration
    ran and its result was written. A migration that raises is an error in
    the result and the file keeps its bytes, so the migration is retried
    next time instead of being recorded as done."""
    config, kb_path = _kb(tmp_path, {"things/t.md": THING}, kb_yaml=THING_KB_YAML)
    path = kb_path / "things" / "t.md"
    before = path.read_bytes()

    with (
        patch("pyrite.cli.load_config", return_value=config),
        patch("pyrite.cli.context.load_config", return_value=config),
    ):
        result = CliRunner().invoke(cli_app, ["schema", "migrate", "--kb", KB])

    assert result.exit_code != 0, result.output
    assert "Migrated: 0" in result.output and "Errors: 1" in result.output, result.output
    assert path.read_bytes() == before, "a raising migration wrote"


def test_schema_migrate_writes_a_migrations_in_place_change(tmp_path: Path, mutating_migration):
    """Item 2, the other half: a migration that mutates its input in place
    changed the `before` values too through the shallow copy, so only the
    version was stamped and the change was lost. The diff is taken against
    a deep copy: the change and the version are both written, and the
    result says to run `index sync`."""
    config, kb_path = _kb(tmp_path, {"things/t.md": THING}, kb_yaml=THING_KB_YAML)
    path = kb_path / "things" / "t.md"
    before = _text(path)

    out = _cli(config, ["schema", "migrate", "--kb", KB])

    assert "Migrated: 1" in out and "index sync" in out, out
    after = _text(path)
    assert "tags: [a, b, migrated]" in after and "_schema_version: 2\n" in after, after
    assert "old: 1   # moves to new\n" in after, after
    assert_only_changed(
        before, after, [Set("tags", ["a", "b", "migrated"]), Set("_schema_version", 2)]
    )


def test_a_move_refuses_when_the_source_changed_since_it_was_read(tmp_path: Path):
    """Item 3, the property: a move removes the source only if its bytes are
    the ones it read; otherwise it refuses and leaves both files. In a git
    work tree and outside one."""
    from pyrite.storage.entry_writer import FileChanged, move_entry_text

    for git in (True, False):
        root = tmp_path / ("git" if git else "plain")
        (root / "notes").mkdir(parents=True)
        if git:
            subprocess.run(
                ["git", "init", "-q"], cwd=root, check=True, capture_output=True, timeout=30
            )
        src = root / "notes" / "a.md"
        dst = root / "notes" / "b.md"
        src.write_text("one\n", encoding="utf-8")
        raw = src.read_bytes()
        src.write_text("two\n", encoding="utf-8")  # edited after the read
        with pytest.raises(FileChanged):
            move_entry_text(src, raw, dst, "moved\n")
        assert src.read_text(encoding="utf-8") == "two\n" and not dst.exists()
        assert sorted(p.name for p in (root / "notes").iterdir()) == ["a.md"], "a temp file stayed"


def test_a_move_holds_one_lock_for_both_paths(tmp_path: Path):
    from pyrite.storage.entry_writer import move_entry_text
    from pyrite.utils.file_lock import file_lock

    root = tmp_path / "kb"
    (root / "notes").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True, timeout=30)
    src = root / "notes" / "a.md"
    dst = root / "notes" / "b.md"
    src.write_text("one\n", encoding="utf-8")
    raw = src.read_bytes()
    worker = threading.Thread(target=lambda: move_entry_text(src, raw, dst, "moved\n"))

    with file_lock(src, dst):
        worker.start()
        time.sleep(0.5)
        assert worker.is_alive(), "the move did not wait for the lock"
        assert src.exists() and not dst.exists(), "the move wrote while the lock was held"
    worker.join(timeout=10)

    assert not worker.is_alive()
    assert not src.exists() and dst.read_text(encoding="utf-8") == "moved\n"


def test_the_models_whole_file_save_cannot_reach_an_existing_entry(tmp_path: Path):
    """Decision 4 (maintainer): the non-exclusive `repo.save` was still a
    public whole-file overwrite (`DocumentManager.save_entry(is_create=False)`
    reached it); nothing in the tree called it so, but a plugin could. A
    save over an existing entry is refused with the bytes untouched; an
    entry is changed through `KBService.apply_operations` alone."""
    from pyrite.exceptions import EntryExistsError
    from pyrite.storage.document_manager import DocumentManager

    config, kb_path = _kb(tmp_path, {"notes/old-one.md": SOURCE})
    kb_config = config.knowledge_bases[0]
    path = kb_path / "notes" / "old-one.md"
    before = path.read_bytes()
    repo = KBRepository(kb_config)
    entry = repo.load("old-one")
    assert entry is not None
    entry.title = "Changed through the model"

    with pytest.raises(EntryExistsError):
        repo.save(entry, keep_filename=True)
    assert path.read_bytes() == before

    db = PyriteDB(config.settings.index_path)
    try:
        with pytest.raises(EntryExistsError):
            DocumentManager(db, IndexManager(db, config)).save_entry(entry, KB, kb_config)
    finally:
        db.close()
    assert path.read_bytes() == before
