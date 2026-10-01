"""Frontmatter quotes what a YAML 1.1 reader would misread (#568).

PyYAML's ``safe_load`` implements YAML 1.1: bare ``yes``/``no``/``on``/``off``
(any case) resolve to a bool, ``~``/``null``/empty resolve to ``None``, and
1.1-only numeric forms -- sexagesimal (``1:30``), underscore-grouped digits
(``1_000``), and leading-zero / ``0o``-prefixed octal (``0777``, ``0o17``) --
resolve to a number where ruamel (YAML 1.2, what Pyrite itself reads) does
not. PyYAML is the most common Python YAML reader, and Knowledge-as-Code
promises every reader the same value a Pyrite *string* was written with, not
just ruamel's.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml as pyyaml  # the YAML 1.1 reader this guards against (safe_load)
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager
from pyrite.storage.repository import KBRepository
from pyrite.utils.yaml import dump_yaml, load_yaml

KB = "work"

#: Every string PyYAML's `safe_load` (YAML 1.1) would resolve to something
#: other than a string if Pyrite wrote it bare -- #568's acceptance list.
AMBIGUOUS_STRINGS = [
    "y",
    "Y",
    "yes",
    "Yes",
    "YES",
    "n",
    "N",
    "no",
    "No",
    "NO",
    "on",
    "On",
    "ON",
    "off",
    "Off",
    "OFF",
    "true",
    "True",
    "TRUE",
    "false",
    "False",
    "FALSE",
    "~",
    "null",
    "Null",
    "NULL",
    "",
    "1:30",
    "0777",
    "0o17",
    "1_000",
]


def _note(nid: str) -> str:
    return f"---\nid: {nid}\ntitle: N\ntype: note\n---\n\nBody.\n"


def _env(tmp_path: Path, text: str | None = None):
    kb_path = tmp_path / "kb"
    kb_path.mkdir()
    (kb_path / "kb.yaml").write_text("name: work\nkb_type: generic\n", encoding="utf-8")
    (kb_path / "notes").mkdir()
    (kb_path / "notes" / "n.md").write_text(text or _note("n"), encoding="utf-8")
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


def _with_db(config: PyriteConfig, fn):
    db = PyriteDB(config.settings.index_path)
    try:
        return fn(db)
    finally:
        db.close()


def _frontmatter(text: str) -> str:
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    assert m, text
    return m.group(1)


# ---------------------------------------------------------------------------
# dump_yaml / load_yaml directly: the representer / pre-dump pass itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", AMBIGUOUS_STRINGS)
def test_an_ambiguous_string_dumps_as_something_pyyaml_reads_back_identically(value):
    dumped = dump_yaml({"decision": value})
    assert pyyaml.safe_load(dumped)["decision"] == value, dumped
    # Pyrite's own (YAML 1.2 / ruamel) reading matches too.
    assert load_yaml(dumped)["decision"] == value, dumped


@pytest.mark.parametrize("value", AMBIGUOUS_STRINGS)
def test_an_ambiguous_string_is_quoted_at_any_nesting_depth_and_in_a_list(value):
    dumped = dump_yaml({"meta": {"decision": value}, "tags": [value, "plain"]})
    reloaded = pyyaml.safe_load(dumped)
    assert reloaded["meta"]["decision"] == value, dumped
    assert reloaded["tags"][0] == value, dumped
    assert reloaded["tags"][1] == "plain", dumped


def test_a_real_bool_is_still_written_unquoted():
    dumped = dump_yaml({"flag": True, "other": False})
    lines = dumped.split("\n")
    assert "flag: true" in lines, dumped
    assert "other: false" in lines, dumped


def test_a_real_int_is_still_written_unquoted():
    assert dump_yaml({"n": 7}) == "n: 7"


def test_an_already_quoted_ambiguous_value_round_trips_byte_identical():
    """A no-op load -> save of a file that already quotes these values makes
    no change: existing quoting style (single vs double) is kept."""
    src = 'a: "yes"\nb: \'no\'\nc: "~"\nd: \'1:30\'\ne: "0777"\n'
    assert dump_yaml(load_yaml(src)) + "\n" == src


# ---------------------------------------------------------------------------
# Mapping KEYS, not only values (conductor round 1: a key is user-controlled
# too, #407 stores an unknown frontmatter key as-is -- `on: x` collides with
# `on` written any other way once PyYAML reads both as the bool True)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["yes", "on", "Off", "~", "1:30"])
def test_an_ambiguous_mapping_key_dumps_as_something_pyyaml_reads_back_identically(key):
    dumped = dump_yaml({key: "x", "plain": 1})
    reloaded = pyyaml.safe_load(dumped)
    assert key in reloaded, dumped
    assert reloaded[key] == "x", dumped
    assert reloaded["plain"] == 1, dumped
    assert load_yaml(dumped)[key] == "x", dumped


def test_two_ambiguous_keys_that_pyyaml_would_collide_both_survive():
    """`yes` and `on` both resolve to the bool `True` under PyYAML; written
    bare, the second key silently overwrites the first entry."""
    dumped = dump_yaml({"yes": "a", "on": "b"})
    reloaded = pyyaml.safe_load(dumped)
    assert reloaded == {"yes": "a", "on": "b"}, dumped


def test_an_ambiguous_key_is_quoted_at_any_nesting_depth():
    dumped = dump_yaml({"meta": {"on": "x"}})
    assert pyyaml.safe_load(dumped)["meta"]["on"] == "x", dumped


def test_an_ambiguous_key_rename_keeps_the_mappings_key_order():
    dumped = dump_yaml({"a": 1, "on": "x", "z": 3})
    lines = [line.split(":")[0].strip().strip('"').strip("'") for line in dumped.split("\n")]
    assert lines == ["a", "on", "z"], dumped


def test_a_service_update_writes_an_ambiguous_key_pyyaml_reads_back_identically(tmp_path):
    config, kb_path = _env(tmp_path)
    path = kb_path / "notes" / "n.md"

    _with_db(config, lambda db: KBService(config, db).update("n", KB, {"on": "x"}))

    text = path.read_text(encoding="utf-8")
    fm = pyyaml.safe_load(_frontmatter(text))
    assert fm["metadata"]["on"] == "x", text


# ---------------------------------------------------------------------------
# Empty string: ruamel already quotes it (its own resolver, not #568's YAML
# 1.1 pass) -- excluded from this pass so its existing quote style is kept
# ---------------------------------------------------------------------------


def test_an_empty_string_keeps_ruamels_own_single_quote_style():
    assert dump_yaml({"a": ""}) == "a: ''"


# ---------------------------------------------------------------------------
# The migration: a save that touches an unrelated field also requotes any
# ambiguous value already bare in the file -- the whole document is one
# dump, not a line patch. The string's value is unchanged; only the bytes
# that represent it change.
# ---------------------------------------------------------------------------


def test_an_unrelated_update_requotes_an_existing_bare_ambiguous_value(tmp_path):
    text = "---\nid: n\ntitle: N\ntype: note\ndecision: yes\n---\n\nBody.\n"
    config, kb_path = _env(tmp_path, text)
    path = kb_path / "notes" / "n.md"

    _with_db(config, lambda db: KBService(config, db).update("n", KB, {"title": "Renamed"}))

    text = path.read_text(encoding="utf-8")
    assert 'decision: "yes"' in text, text
    assert pyyaml.safe_load(_frontmatter(text))["decision"] == "yes", text


# ---------------------------------------------------------------------------
# Through the service update path (#568 acceptance #2)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", AMBIGUOUS_STRINGS)
def test_a_service_update_writes_an_ambiguous_string_pyyaml_reads_back_identically(tmp_path, value):
    config, kb_path = _env(tmp_path)
    path = kb_path / "notes" / "n.md"

    _with_db(config, lambda db: KBService(config, db).update("n", KB, {"decision": value}))

    text = path.read_text(encoding="utf-8")
    fm = pyyaml.safe_load(_frontmatter(text))
    # `decision` is not a declared field on `note` (#407): KBService.update
    # merges it into the `metadata` dict, written as a nested frontmatter
    # key -- exercising the "at any depth" part of #568's acceptance.
    assert fm["metadata"]["decision"] == value, text
    # Pyrite's own read returns the same string too.
    entry = KBRepository(config.get_kb(KB)).load("n")
    assert entry.metadata.get("decision") == value, text


def test_a_service_update_writing_a_real_bool_stays_unquoted(tmp_path):
    config, kb_path = _env(tmp_path)
    path = kb_path / "notes" / "n.md"

    _with_db(config, lambda db: KBService(config, db).update("n", KB, {"decision": True}))

    text = path.read_text(encoding="utf-8")
    assert "decision: true" in text, text
    assert pyyaml.safe_load(_frontmatter(text))["metadata"]["decision"] is True, text


#: Values that survive the CLI's own `-f` type guesser (int/float/bool/JSON
#: tried before falling back to a plain string --
#: `pyrite/cli/entry_commands.py:_parse_field_value`) as a *string*.
#: `true`/`False`/`0777`/`1_000` are CLI-level booleans/ints by design (the
#: user typed `-f decision=true` meaning the bool) and exercise a different
#: concern than this bug.
CLI_SURVIVES_AS_STRING = ["yes", "no", "on", "off", "y", "n", "~", "null", "1:30", "0o17"]


@pytest.mark.parametrize("value", CLI_SURVIVES_AS_STRING)
def test_cli_update_writes_an_ambiguous_string_pyyaml_reads_back_identically(tmp_path, value):
    config, kb_path = _env(tmp_path)
    path = kb_path / "notes" / "n.md"

    with patch("pyrite.cli.context.load_config", return_value=config):
        result = CliRunner().invoke(
            app, ["update", "n", "-k", KB, "-f", f"decision={value}", "--format", "json"]
        )
    assert result.exit_code == 0, result.output

    text = path.read_text(encoding="utf-8")
    assert pyyaml.safe_load(_frontmatter(text))["metadata"]["decision"] == value, text
