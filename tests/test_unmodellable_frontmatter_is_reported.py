"""Frontmatter Pyrite cannot model is refused for writes and reported, with the line (#760 (b)).

Maintainer decision, 2026-10-15: a file whose frontmatter ``apply()`` cannot
model (a tagged key, a complex key, a duplicate key) gets no write through
Pyrite, body included; the update is refused with ``apply()``'s reason
(`VALIDATION_FAILED`). Such a file must not be invisible: ``index health``
and ``qa validate`` list "frontmatter Pyrite cannot model: N files", each
with its path, the line and the reason.

The oracle for the line is the fixture text: the offending key is on a line
written by hand here, never computed from the module under test.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.exceptions import ValidationError
from pyrite.services.access_policy import UNSCOPED
from pyrite.services.kb_service import KBService
from pyrite.services.qa_service import QAService
from pyrite.storage.database import PyriteDB
from pyrite.storage.file_operations import (
    FrontmatterSpan,
    OperationRefusedError,
    ReplaceBody,
    apply,
)
from pyrite.storage.index import IndexManager
from pyrite.utils.frontmatter import Frontmatter, split_frontmatter

KB = "shapes"

#: name -> (file text, 1-based line of the key Pyrite cannot model, a word of the reason)
SHAPES: dict[str, tuple[str, int, str]] = {
    "tagged-key": (
        "---\nid: tagged\ntitle: Tagged\ntype: note\n!!str flag: 1\n---\n\nText.\n",
        5,
        "tagged key",
    ),
    "complex-key": (
        "---\nid: complex\ntitle: Complex\ntype: note\n? [a, b]\n: 1\n---\n\nText.\n",
        5,
        "complex key",
    ),
    "duplicate-key": (
        "---\nid: dup\ntitle: Dup\ntype: note\nyear: 2025\nyear: 2026\n---\n\nText.\n",
        6,
        "duplicate key",
    ),
}

GOOD = "---\nid: good\ntitle: Good\ntype: note\n---\n\nText.\n"


def _env(tmp_path: Path, names: list[str]) -> tuple[PyriteConfig, dict[str, Path]]:
    kb_path = tmp_path / "kb"
    (kb_path / "notes").mkdir(parents=True)
    (kb_path / "notes" / "good.md").write_text(GOOD, encoding="utf-8")
    paths = {}
    for name in names:
        text, _line, _word = SHAPES[name]
        paths[name] = kb_path / "notes" / f"{name}.md"
        paths[name].write_text(text, encoding="utf-8")
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name=KB, path=kb_path, kb_type="generic")],
        settings=Settings(index_path=tmp_path / "index.db", auto_embed=False),
    )
    db = PyriteDB(config.settings.index_path)
    try:
        IndexManager(db, config).index_all()
    finally:
        db.close()
    return config, paths


@pytest.mark.parametrize("name", sorted(SHAPES))
def test_index_health_lists_the_file_with_its_line_and_reason(tmp_path: Path, name: str):
    config, paths = _env(tmp_path, [name])
    _text, line, word = SHAPES[name]
    db = PyriteDB(config.settings.index_path)
    try:
        health = IndexManager(db, config).check_health()
    finally:
        db.close()

    rows = health["unmodellable_frontmatter"]
    assert [Path(r["path"]).name for r in rows] == [f"{name}.md"], rows
    assert rows[0]["kb"] == KB
    assert rows[0]["line"] == line
    assert word in rows[0]["reason"]


def test_index_health_cli_counts_them_and_warns(tmp_path: Path):
    config, _paths = _env(tmp_path, sorted(SHAPES))
    with (
        patch("pyrite.cli.context.load_config", return_value=config),
        patch("pyrite.cli.load_config", return_value=config),
    ):
        result = CliRunner().invoke(app, ["index", "health", "-k", KB, "--format", "json"])
    payload = json.loads(result.output[result.output.index("{") :])
    assert len(payload["unmodellable_frontmatter"]) == 3, payload
    assert payload["status"] in ("warning", "unhealthy"), payload["status"]
    for row in payload["unmodellable_frontmatter"]:
        assert set(row) >= {"kb", "path", "line", "reason"}, row

    with (
        patch("pyrite.cli.context.load_config", return_value=config),
        patch("pyrite.cli.load_config", return_value=config),
    ):
        rich = CliRunner().invoke(app, ["index", "health", "-k", KB, "--format", "rich"])
    assert "3 file(s) whose frontmatter Pyrite cannot model" in rich.output, rich.output
    assert ":5" in rich.output and ":6" in rich.output, rich.output


@pytest.mark.parametrize("name", sorted(SHAPES))
def test_qa_validate_reports_the_file_with_its_line(tmp_path: Path, name: str):
    config, paths = _env(tmp_path, [name])
    _text, line, word = SHAPES[name]
    db = PyriteDB(config.settings.index_path)
    try:
        report = QAService(config, db).validate_kb(KB, readable_kbs=UNSCOPED)
    finally:
        db.close()

    found = [i for i in report["issues"] if i.get("rule") == "frontmatter_unmodellable"]
    assert len(found) == 1, report["issues"]
    issue = found[0]
    # One severity across the surfaces: `index health` reports these as a
    # warning (the file reads; no write reaches it), so the issue is one too.
    assert issue["severity"] == "warning"
    assert issue["line"] == line
    assert word in issue["message"] and f"line {line}" in issue["message"]
    assert Path(issue["path"]).name == f"{name}.md"


@pytest.mark.control(
    reason="the refusal itself landed with #971 (P3a: apply() refuses, nothing written); "
    "this pins it beside the report so the two halves of #760 (b) are read together"
)
@pytest.mark.parametrize("name", ["tagged-key", "complex-key"])
def test_an_update_of_such_a_file_is_refused_with_the_reason_and_nothing_written(
    tmp_path: Path, name: str
):
    """A duplicate key cannot be addressed by id at all (the loader refuses
    the file), so only the two loadable shapes reach the write."""
    config, paths = _env(tmp_path, [name])
    before = paths[name].read_bytes()
    entry_id = {"tagged-key": "tagged", "complex-key": "complex"}[name]
    db = PyriteDB(config.settings.index_path)
    try:
        with pytest.raises(ValidationError) as info:
            KBService(config, db).update(entry_id, KB, {"body": "new body"})
    finally:
        db.close()
    assert SHAPES[name][2] in str(info.value)
    assert info.value.error_code == "VALIDATION_FAILED"
    assert paths[name].read_bytes() == before


def _span(text: str) -> FrontmatterSpan:
    split = split_frontmatter(text)
    assert isinstance(split, Frontmatter)
    return FrontmatterSpan(split.yaml_start, split.close_start, split.close_end)


def unmodellable_reason(text: str, span: FrontmatterSpan):
    # Imported here, not at the top: the file must collect without the
    # change, so that every other test in it is red for its own reason.
    from pyrite.storage.file_operations import unmodellable_reason as rule

    return rule(text, span)


@pytest.mark.parametrize("name", sorted(SHAPES))
def test_unmodellable_reason_names_the_shape_and_its_line(name: str):
    text, line, word = SHAPES[name]
    got = unmodellable_reason(text, _span(text))
    assert got is not None
    assert word in got[0] and got[1] == line


@pytest.mark.parametrize(
    "text",
    [
        GOOD,
        "\ufeff---\r\nid: g\r\ntitle: G\r\nflag: no\r\n---\r\n\r\nText.\r\n",
        "---\nid: g\nbase: &b 1\nref: *b\n---\n",  # an anchor is a per-key refusal, not a file's
        "---\nid: g\ntitle: 'unterminated\n---\n",  # malformed: the reader's to report
    ],
)
def test_frontmatter_pyrite_can_model_or_cannot_parse_is_not_listed(text: str):
    assert unmodellable_reason(text, _span(text)) is None


def test_a_tagged_key_deeper_in_the_file_is_found_with_its_own_line():
    text = "---\nid: g\ntitle: G\nmeta:\n  !!str inner: 1\n---\n\nText.\n"
    got = unmodellable_reason(text, _span(text))
    assert got is not None and got[1] == 5, got


# --- fix round 1: listed exactly when apply() refuses, same reason, same line ---

#: Every reason ``apply()`` refuses a whole file for. name -> (file text,
#: 1-based line the listing must name, or None when the file is not listed:
#: frontmatter that does not parse is the reader's ``malformed_frontmatter``
#: row, the one stated exception). The line is written by hand (the oracle).
REFUSALS: dict[str, tuple[str, int | None]] = {
    "sequence root": ("---\n- a\n- b\n---\n\nText.\n", 2),
    "scalar root": ("---\njust words\n---\n\nText.\n", 2),
    "flow mapping root": ("---\n{id: flow, title: Flow}\n---\n\nText.\n", 2),
    "complex key": ("---\nid: c\n? [a, b]\n: 1\n---\n\nText.\n", 3),
    "tagged key, foreign tag": ("---\nid: t\n!foo flag: 1\n---\n\nText.\n", 3),
    "tagged key, natural tag": ("---\nid: t\ntitle: T\n!!str flag: 1\n---\n\nText.\n", 4),
    "tagged key beside its plain twin": ("---\nid: t\n!!str a: 1\na: 2\n---\n\nText.\n", 3),
    "tagged key in a set member's value": ("---\nid: t\ns: !!set {? a: {!!str k: 1}}\n---\n", 3),
    "duplicate key": ("---\nid: d\nyear: 2025\nyear: 2026\n---\n\nText.\n", 4),
    "duplicate key by value": ("---\nid: d\n1: a\n1.0: b\n---\n\nText.\n", 4),
    "does not parse: compose": ("---\nid: m\ntitle: 'unterminated\n---\n\nText.\n", None),
    # a plain key ruamel resolves to a timestamp but cannot construct: the
    # document fails, so this is "does not parse" and the reader's row, not
    # a "tagged key" judged on its own (fix round 2)
    "does not parse: a plain key that is no date": ("---\nid: m\n2024-02-30: x\n---\n", None),
    "does not parse: construct": ("---\nid: m\nx: !!float abc\n---\n\nText.\n", None),
    # 800 levels overflow Python's default recursion limit in ruamel's
    # composer (400 do); deeper costs seconds per read, and the reader reads
    # this file at every surface the tests below walk
    "does not parse: nesting": ("---\nid: m\nx: " + "[" * 800 + "]" * 800 + "\n---\n", None),
}

_DOES_NOT_PARSE = "the frontmatter does not parse"


def _apply_refusal(text: str) -> str:
    with pytest.raises(OperationRefusedError) as info:
        apply(text, [ReplaceBody("new body\n")], _span(text))
    return info.value.reason


#: Cases on which the two surfaces agreed before the round (0bbef521): kept
#: as controls, so the property is stated for every reason, not only the
#: ones that failed it.
AGREED_BEFORE = {
    "complex key",
    "tagged key, foreign tag",
    "tagged key, natural tag",
    "duplicate key",
    "duplicate key by value",
    "does not parse: compose",
}


def _case(name: str) -> Any:
    if name in AGREED_BEFORE:
        return pytest.param(name, marks=pytest.mark.control(reason="agreed before the round"))
    return name


@pytest.mark.parametrize("name", [_case(n) for n in REFUSALS])
def test_the_listing_agrees_with_apply_on_every_refusal_reason_and_line(name: str):
    """One rule decides both: what ``apply()`` refuses a file for is what the
    listing says, at the line of the key (or the root) it names. The
    exception is frontmatter that does not parse: apply refuses it, the
    reader lists it as malformed, this listing does not repeat it."""
    text, line = REFUSALS[name]
    reason = _apply_refusal(text)
    listed = unmodellable_reason(text, _span(text))
    if line is None:
        assert reason.startswith(_DOES_NOT_PARSE), reason
        assert listed is None, listed
    else:
        assert not reason.startswith(_DOES_NOT_PARSE), reason
        assert listed == (reason, line), (listed, reason)


#: Plain keys ruamel resolves to a tag of its own, and what the document
#: makes of each (fix round 2: a key is judged by the document, never alone).
#: bool, int, float, null and timestamp keys read as those values (a path
#: names them, ``_key_matches``); a timestamp that is no date fails the whole
#: document (``REFUSALS``, above). The merge key ``<<`` is consumed by the
#: construction (its anchor is decision 6's refusal, per key). The value key
#: ``=`` is the string ``'='`` in the document, though alone it constructs as a
#: ``TaggedScalar``. The yaml tag's ``!``, ``&`` and ``*`` are never plain
#: keys: ``!: 1`` is a non-mapping document, the other two are syntax.
SPECIAL_PLAIN_KEYS = {
    "value key at the top": "---\nid: v\ntitle: V\n=: 1\n---\n\nText.\n",
    "value key nested": "---\nid: v\ntitle: V\nx: {=: 1}\n---\n\nText.\n",
    # no `true` beside `1`: the loader compares keys by value, so those two
    # collide (a duplicate), which is a refusal of its own
    "typed keys": "---\nid: v\ntitle: V\n1: a\n1.5: b\nnull: d\n2024-02-28: e\n---\n",
}


@pytest.mark.control(
    reason="read as the document reads them before round 1 and on the PR's base; "
    "red on dff0ce0d alone, where a key was judged in isolation"
)
@pytest.mark.parametrize("name", list(SPECIAL_PLAIN_KEYS))
def test_a_plain_key_with_a_special_tag_is_read_as_the_document_reads_it(name: str):
    from pyrite.storage.file_operations import Set

    text = SPECIAL_PLAIN_KEYS[name]
    assert unmodellable_reason(text, _span(text)) is None
    after, report = apply(text, [Set("title", "V2")], _span(text))
    assert after == text.replace("title: V", "title: V2"), after
    assert report.changed


def _env_with(tmp_path: Path, files: dict[str, str]) -> PyriteConfig:
    kb_path = tmp_path / "kb"
    (kb_path / "notes").mkdir(parents=True)
    (kb_path / "notes" / "good.md").write_text(GOOD, encoding="utf-8")
    for name, text in files.items():
        (kb_path / "notes" / name).write_text(text, encoding="utf-8")
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


def _health(config: PyriteConfig) -> dict:
    db = PyriteDB(config.settings.index_path)
    try:
        return IndexManager(db, config).check_health(KB)
    finally:
        db.close()


def _validate_kb(config: PyriteConfig) -> dict:
    db = PyriteDB(config.settings.index_path)
    try:
        return QAService(config, db).validate_kb(KB, readable_kbs=UNSCOPED)
    finally:
        db.close()


@pytest.mark.parametrize("name", [_case(n) for n in REFUSALS if REFUSALS[n][1] is None])
def test_a_file_that_does_not_parse_is_the_readers_malformed_row_not_a_crash(
    tmp_path: Path, name: str
):
    """A ``!!float abc`` value raised ``ValueError`` and deep nesting
    ``RecursionError`` out of ``check_health`` and, through it, every
    validate surface (review of #972). The reader already reads around these;
    the listing must too."""
    text, _ = REFUSALS[name]
    config = _env_with(tmp_path, {"broken.md": text})
    health = _health(config)
    assert [Path(r["path"]).name for r in health["malformed_frontmatter"]] == ["broken.md"]
    assert health["unmodellable_frontmatter"] == []
    report = _validate_kb(config)
    assert not [i for i in report["issues"] if i.get("rule") == "frontmatter_unmodellable"]


@pytest.mark.parametrize("name", [n for n in REFUSALS if REFUSALS[n][1] is None])
def test_one_file_that_does_not_parse_does_not_stop_the_title_check(tmp_path: Path, name: str):
    """The dev-to-arch merge (#1026): `dev`'s #784 title check reads every
    file's frontmatter itself and caught only a `FrontmatterError`, so a
    constructor failure (`2024-02-30:` as a key, `!!float abc`, 800 nested
    brackets) aborted the whole KB-wide `validate_kb` over one bad file, and
    a FIFO blocked it. The property: a file whose frontmatter does not parse
    is the reader's malformed row, and the title check still reports the list
    title of the file beside it."""
    text, _ = REFUSALS[name]
    config = _env_with(
        tmp_path,
        {"broken.md": text, "listed.md": "---\nid: l\ntitle: [a, b]\ntype: note\n---\n\nText.\n"},
    )
    notes = config.knowledge_bases[0].path / "notes"
    os.mkfifo(notes / "fifo.md")

    result: dict = {}
    worker = threading.Thread(target=lambda: result.update(_validate_kb(config)), daemon=True)
    worker.start()
    worker.join(timeout=30)
    assert not worker.is_alive(), "validate_kb hung on the FIFO"

    titles = [i for i in result["issues"] if i.get("rule") == "non_string_title"]
    assert [i["message"] for i in titles] and all("listed.md" in i["message"] for i in titles), (
        result["issues"]
    )
    health = _health(config)
    # The reader lists the broken file (and the list-title file, which its
    # model refuses too); nothing crashed or hung on the way.
    assert "broken.md" in [Path(r["path"]).name for r in health["malformed_frontmatter"]]


def test_a_non_regular_or_unreadable_file_is_read_around(tmp_path: Path):
    """A FIFO named ``*.md`` hung ``check_health`` (its open blocks for a
    writer that never comes); a directory, a dangling symlink and bytes that
    are not UTF-8 are skipped as the reconcile skips them."""
    config = _env_with(tmp_path, {"tagged.md": SHAPES["tagged-key"][0]})
    notes = config.knowledge_bases[0].path / "notes"
    os.mkfifo(notes / "fifo.md")
    (notes / "dir.md").mkdir()
    (notes / "dangling.md").symlink_to(notes / "nowhere.md")
    (notes / "latin1.md").write_bytes(b"---\nid: l\ntitle: caf\xe9\n---\n")

    result: dict = {}
    worker = threading.Thread(target=lambda: result.update(_health(config)), daemon=True)
    worker.start()
    worker.join(timeout=30)
    assert not worker.is_alive(), "check_health hung on the FIFO"
    assert [Path(r["path"]).name for r in result["unmodellable_frontmatter"]] == ["tagged.md"]

    report = _validate_kb(config)
    found = [i for i in report["issues"] if i.get("rule") == "frontmatter_unmodellable"]
    assert [Path(i["path"]).name for i in found] == ["tagged.md"]


@pytest.mark.control(reason="the line was right before the round; pinned through the file read")
def test_a_listed_files_line_counts_a_bom_and_crlf_as_the_editor_does(tmp_path: Path):
    text = "﻿---\r\nid: bom\r\ntitle: Bom\r\n!!str flag: 1\r\n---\r\n\r\nText.\r\n"
    config = _env_with(tmp_path, {"bom.md": text})
    rows = _health(config)["unmodellable_frontmatter"]
    assert [(Path(r["path"]).name, r["line"]) for r in rows] == [("bom.md", 4)], rows
    got = unmodellable_reason(text, _span(text))
    assert got is not None and got[1] == 4, got


def test_validate_entry_reports_the_entrys_own_file(tmp_path: Path):
    """``qa validate <entry>`` reads the one file, as ``validate_kb`` reads
    them all; the issue carries the entry's id, not the file's name. A
    flow-style root is the shape the index holds (a tagged or complex key
    is no JSON key, so such a file never indexes; see the next test)."""
    text = "---\n{id: real-id, title: Real, type: note}\n---\n\nText.\n"
    config = _env_with(tmp_path, {"some-file.md": text})
    db = PyriteDB(config.settings.index_path)
    try:
        one = QAService(config, db).validate_entry("real-id", KB, readable_kbs=UNSCOPED)
        good = QAService(config, db).validate_entry("good", KB, readable_kbs=UNSCOPED)
    finally:
        db.close()
    assert not [i for i in one["issues"] if i.get("rule") == "entry_not_found"], one["issues"]
    found = [i for i in one["issues"] if i.get("rule") == "frontmatter_unmodellable"]
    assert len(found) == 1, one["issues"]
    assert found[0]["entry_id"] == "real-id"
    assert found[0]["line"] == 2 and Path(found[0]["path"]).name == "some-file.md"
    assert not [i for i in good["issues"] if i.get("rule") == "frontmatter_unmodellable"]


def test_validate_entry_explains_an_unindexed_entry_whose_file_it_finds(tmp_path: Path):
    """A tagged key keeps the file out of the index, so the entry is "not
    found" there; the one check that says why reads its file."""
    text = "---\nid: real-id\ntitle: Real\ntype: note\n!!str flag: 1\n---\n\nText.\n"
    config = _env_with(tmp_path, {"some-file.md": text})
    db = PyriteDB(config.settings.index_path)
    try:
        one = QAService(config, db).validate_entry("real-id", KB, readable_kbs=UNSCOPED)
        none = QAService(config, db).validate_entry("nobody", KB, readable_kbs=UNSCOPED)
    finally:
        db.close()
    assert [i["rule"] for i in one["issues"]] == ["entry_not_found", "frontmatter_unmodellable"]
    assert one["issues"][1]["entry_id"] == "real-id" and one["issues"][1]["line"] == 5
    assert [i["rule"] for i in none["issues"]] == ["entry_not_found"]


# --- #990: check_health decides from the reconcile's own read which files to parse again ---

#: Every file shape this module knows, with whether the listing names it.
_EVERY_SHAPE: dict[str, tuple[str, bool]] = {
    **{name: (text, True) for name, (text, _line, _word) in SHAPES.items()},
    **{name: (text, line is not None) for name, (text, line) in REFUSALS.items()},
    **{name: (text, False) for name, text in SPECIAL_PLAIN_KEYS.items()},
    "good": (GOOD, False),
    "good with flow values": ("---\nid: g\ntags: [a, b]\nm: {k: 1}\n---\n", False),
}


@pytest.mark.parametrize("name", list(_EVERY_SHAPE))
def test_the_reconciles_reading_says_whether_a_file_may_be_listed(name: str):
    """``may_be_unmodellable`` reads the frontmatter the reconcile already
    loaded (``_source_frontmatter``) and answers False only for a file the
    listing could not name: all keys plain strings, no set, a block root.
    A file it answers True for is parsed again by the listing; one that
    does not load at all is parsed again too (the caller's rule)."""
    from pyrite.storage.file_operations import may_be_unmodellable
    from pyrite.utils.yaml import load_yaml

    text, listed = _EVERY_SHAPE[name]
    split = split_frontmatter(text)
    assert isinstance(split, Frontmatter)
    try:
        loaded = load_yaml(split.text)
    except Exception:
        pytest.skip("does not load: the caller parses it again without asking")
    if listed:
        assert may_be_unmodellable(loaded), name
    elif name in ("good", "good with flow values", "value key at the top", "value key nested"):
        assert not may_be_unmodellable(loaded), name
    # the typed-keys shape is a True the listing then clears: allowed


@pytest.mark.control(
    reason="the listing agreed with the rule before #990; this pins that skipping the "
    "second parse of unsuspicious files changes nothing listed"
)
def test_index_health_lists_exactly_what_the_rule_says_for_every_shape(tmp_path: Path):
    files = {f"{i:02d}.md": text for i, (text, _listed) in enumerate(_EVERY_SHAPE.values())}
    config = _env_with(tmp_path, files)
    notes = config.knowledge_bases[0].path / "notes"
    expected = []
    for file_name in sorted(files):
        text = (notes / file_name).read_bytes().decode("utf-8")
        got = unmodellable_reason(text, _span(text))
        if got is not None:
            expected.append((file_name, got[1], got[0]))
    rows = _health(config)["unmodellable_frontmatter"]
    assert [(Path(r["path"]).name, r["line"], r["reason"]) for r in rows] == expected


def test_validate_entry_on_a_duplicate_key_file_agrees_with_the_kb_report(tmp_path: Path):
    """A duplicate-key file cannot be read, so no lookup finds it by id;
    ``qa validate <kb>`` lists it under the file's stem (the id the listing
    falls back to), and ``qa validate <entry>`` for that id must say the
    same, not only ``entry_not_found`` (fix round 2, before dev)."""
    config = _env_with(tmp_path, {"dup.md": SHAPES["duplicate-key"][0]})
    db = PyriteDB(config.settings.index_path)
    try:
        one = QAService(config, db).validate_entry("dup", KB, readable_kbs=UNSCOPED)
    finally:
        db.close()
    kb_issues = [
        i for i in _validate_kb(config)["issues"] if i.get("rule") == "frontmatter_unmodellable"
    ]
    assert [i["rule"] for i in one["issues"]] == ["entry_not_found", "frontmatter_unmodellable"]
    assert one["issues"][1] == kb_issues[0], (one["issues"][1], kb_issues)


def test_the_listing_names_the_entry_by_its_id_not_its_file_name(tmp_path: Path):
    """The id is the reader's (an explicit ``id:``, else the one derived from
    the title); only a file that cannot be read at all (a duplicate key)
    falls back to the file's stem."""
    tagged = "---\nid: real-id\ntitle: Real\ntype: note\n!!str flag: 1\n---\n\nText.\n"
    dup = "---\ntitle: Dup\ntype: note\nyear: 2025\nyear: 2026\n---\n\nText.\n"
    config = _env_with(tmp_path, {"some-file.md": tagged, "dup-file.md": dup})
    rows = {Path(r["path"]).name: r for r in _health(config)["unmodellable_frontmatter"]}
    assert rows["some-file.md"]["id"] == "real-id"
    assert rows["dup-file.md"]["id"] == "dup-file"
    issues = {
        Path(i["path"]).name: i
        for i in _validate_kb(config)["issues"]
        if i.get("rule") == "frontmatter_unmodellable"
    }
    assert issues["some-file.md"]["entry_id"] == "real-id"
    assert issues["dup-file.md"]["entry_id"] == "dup-file"
