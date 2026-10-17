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
from pyrite.utils.frontmatter import Frontmatter, split_frontmatter
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
