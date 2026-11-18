"""`pyrite update -f` must never store tags as a raw string (#231, #1502).

`-f tags=alpha,beta` used to store the raw string ``alpha,beta`` because the
update path coerced ints only while the create path used the full value parser.
The reader then iterated the string as a sequence, so the entry came back tagged
with the characters of the value and silently dropped out of tag lookups. The
CLI then split it on commas; since #1502 it splits nothing, and `-f tags=...`
is a usage error pointing at `--tags` (the maintainer, 2026-11-18), so it can
never store the one tag ``alpha,beta``.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.storage.database import PyriteDB
from pyrite.utils.yaml import load_yaml

runner = CliRunner()

KB_YAML = """name: notes
kb_type: generic
types:
  note:
    description: A note
"""


def _make_env(tmp_path: Path) -> tuple[PyriteConfig, Path]:
    kb_path = tmp_path / "kb"
    kb_path.mkdir()
    (kb_path / "kb.yaml").write_text(KB_YAML, encoding="utf-8")
    db_path = tmp_path / "index.db"
    PyriteDB(db_path).close()
    kb = KBConfig(name="notes", path=kb_path, kb_type=KBType.GENERIC)
    config = PyriteConfig(knowledge_bases=[kb], settings=Settings(index_path=db_path))
    return config, kb_path


def _invoke(config: PyriteConfig, args: list[str]):
    with patch("pyrite.cli.context.load_config", return_value=config):
        return runner.invoke(app, args)


def _json_payload(result) -> dict:
    text = result.output.strip()
    return json.loads(text[text.index("{") :])


def _entry_id(kb_path: Path) -> str:
    files = list(kb_path.rglob("*.md"))
    assert len(files) == 1, files
    return files[0].stem


def _create_note(config: PyriteConfig) -> None:
    result = _invoke(
        config,
        ["create", "-k", "notes", "-t", "note", "--title", "Tag probe", "-b", "body"],
    )
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize("key", ["tags", "metadata.tags"])
def test_update_field_tags_is_refused_pointing_at_the_tags_flag(tmp_path, key):
    config, kb_path = _make_env(tmp_path)
    _create_note(config)
    entry_id = _entry_id(kb_path)
    path = list(kb_path.rglob("*.md"))[0]
    before = path.read_bytes()

    result = _invoke(
        config, ["update", entry_id, "-k", "notes", "-f", f"{key}=alpha,beta", "--format", "json"]
    )
    assert result.exit_code == 2, result.output
    payload = _json_payload(result)
    assert payload["outcome"] == "error", payload
    assert payload["error_code"] == "USAGE_ERROR", payload
    assert "--tags" in payload["error"], payload
    # Nothing was written: no raw string, no one tag "alpha,beta".
    assert path.read_bytes() == before


@pytest.mark.parametrize("key", ["tags", "metadata.tags"])
def test_create_field_tags_is_refused_pointing_at_the_tags_flag(tmp_path, key):
    config, kb_path = _make_env(tmp_path)
    result = _invoke(
        config,
        ["create", "-k", "notes", "-t", "note", "--title", "Tag probe", "-b", "body",
         "-f", f"{key}=alpha", "--format", "json"],
    )  # fmt: skip
    assert result.exit_code == 2, result.output
    payload = _json_payload(result)
    assert payload["outcome"] == "error", payload
    assert payload["error_code"] == "USAGE_ERROR", payload
    assert "--tags" in payload["error"], payload
    assert not list(kb_path.rglob("*.md"))


def test_update_field_matches_the_dedicated_tags_flag(tmp_path):
    config, kb_path = _make_env(tmp_path)
    _create_note(config)
    entry_id = _entry_id(kb_path)

    result = _invoke(
        config, ["update", entry_id, "-k", "notes", "--tags", "alpha,beta", "--format", "json"]
    )
    assert result.exit_code == 0, result.output

    payload = _json_payload(_invoke(config, ["get", entry_id, "-k", "notes", "--format", "json"]))
    assert payload["tags"] == ["alpha", "beta"], payload


def test_update_field_still_parses_scalars_and_json(tmp_path):
    config, kb_path = _make_env(tmp_path)
    _create_note(config)
    entry_id = _entry_id(kb_path)

    result = _invoke(
        config,
        [
            "update",
            entry_id,
            "-k",
            "notes",
            "-f",
            "importance=7",
            "-f",
            'aliases=["x","y"]',
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output

    payload = _json_payload(_invoke(config, ["get", entry_id, "-k", "notes", "--format", "json"]))
    assert payload["importance"] == 7, payload

    frontmatter = list(kb_path.rglob("*.md"))[0].read_text(encoding="utf-8").split("---", 2)[1]
    assert load_yaml(frontmatter)["aliases"] == ["x", "y"]


def test_update_field_preserves_a_comma_in_an_undeclared_value(tmp_path):
    config, kb_path = _make_env(tmp_path)
    _create_note(config)
    entry_id = _entry_id(kb_path)

    result = _invoke(
        config,
        [
            "update",
            entry_id,
            "-k",
            "notes",
            "-f",
            "note=narrow the scope, then expand",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = _json_payload(_invoke(config, ["get", entry_id, "-k", "notes", "--format", "json"]))
    assert payload["metadata"]["note"] == "narrow the scope, then expand"


def test_update_field_reads_a_json_string_literal(tmp_path):
    config, kb_path = _make_env(tmp_path)
    _create_note(config)
    entry_id = _entry_id(kb_path)

    result = _invoke(
        config,
        ["update", entry_id, "-k", "notes", "-f", 'question="a, b"', "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    payload = _json_payload(_invoke(config, ["get", entry_id, "-k", "notes", "--format", "json"]))
    assert payload["metadata"]["question"] == "a, b"


@pytest.mark.control(
    reason="--tags a,b always wrote two tags; the -f tags refusal must not touch it"
)
def test_create_dedicated_tags_flag_writes_two_tags(tmp_path):
    config, kb_path = _make_env(tmp_path)
    result = _invoke(
        config,
        ["create", "-k", "notes", "-t", "note", "--title", "Tag probe", "-b", "body",
         "--tags", "a,b", "--format", "json"],
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    (path,) = list(kb_path.rglob("*.md"))
    fm = load_yaml(path.read_text(encoding="utf-8").split("---")[1])
    assert fm["tags"] == ["a", "b"], fm


# --append / --remove tags=...: a comma is refused as -f tags= is (#1502, the
# maintainer's item B applied to both flags). Since the CLI splits nothing,
# `--append tags=c,d` would store the one tag "c,d" and `--remove tags=a,b`
# would remove nothing and exit 0.


def _tagged_note(config: PyriteConfig, kb_path: Path) -> tuple[str, Path]:
    result = _invoke(
        config,
        ["create", "-k", "notes", "-t", "note", "--title", "Tag probe", "-b", "body",
         "--tags", "x,a,b"],
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    (path,) = list(kb_path.rglob("*.md"))
    return path.stem, path


@pytest.mark.parametrize(
    ("flag", "value", "key"),
    [
        ("--append", "tags=c,d", "tags"),
        ("--append", "metadata.tags=c,d", "metadata.tags"),
        ("--remove", "tags=a,b", "tags"),
        ("--remove", "metadata.tags=a,b", "metadata.tags"),
    ],
)
def test_a_comma_in_append_or_remove_tags_is_refused(tmp_path, flag, value, key):
    config, kb_path = _make_env(tmp_path)
    entry_id, path = _tagged_note(config, kb_path)
    before = path.read_bytes()

    result = _invoke(config, ["update", entry_id, "-k", "notes", flag, value, "--format", "json"])
    assert result.exit_code == 2, result.output
    payload = _json_payload(result)
    assert payload["error_code"] == "USAGE_ERROR", payload
    assert f"{flag} {key}=" in payload["error"] and "JSON" in payload["error"], payload
    assert path.read_bytes() == before


@pytest.mark.control(reason="one tag per --append/--remove, or a JSON list, always worked")
@pytest.mark.parametrize(
    ("flag", "value", "expected"),
    [
        ("--append", "tags=c", ["x", "a", "b", "c"]),
        ("--append", 'tags=["c", "d"]', ["x", "a", "b", "c", "d"]),
        ("--remove", "tags=a", ["x", "b"]),
        ("--remove", 'tags=["a", "b"]', ["x"]),
    ],
)
def test_one_tag_or_a_json_list_in_append_or_remove_works(tmp_path, flag, value, expected):
    config, kb_path = _make_env(tmp_path)
    entry_id, path = _tagged_note(config, kb_path)
    result = _invoke(config, ["update", entry_id, "-k", "notes", flag, value, "--format", "json"])
    assert result.exit_code == 0, result.output
    fm = load_yaml(path.read_text(encoding="utf-8").split("---")[1])
    assert fm["tags"] == expected, fm
