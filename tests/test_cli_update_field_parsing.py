"""`pyrite update -f` must parse values according to their field types (#231).

`-f tags=alpha,beta` used to store the raw string ``alpha,beta`` because the
update path coerced ints only while the create path used the full value parser.
The reader then iterated the string as a sequence, so the entry came back tagged
with the characters of the value and silently dropped out of tag lookups.
"""

import json
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.storage.database import PyriteDB
from pyrite.utils.yaml import load_yaml
from pyrite.utils.frontmatter import split_frontmatter, Frontmatter

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


def test_update_field_writes_a_comma_separated_value_as_a_list(tmp_path):
    config, kb_path = _make_env(tmp_path)
    _create_note(config)
    entry_id = _entry_id(kb_path)

    result = _invoke(
        config, ["update", entry_id, "-k", "notes", "-f", "tags=alpha,beta", "--format", "json"]
    )
    assert result.exit_code == 0, result.output

    # The file must not carry the raw string: that is what got read back as the
    # characters a, l, p, h, ...
    text = list(kb_path.rglob("*.md"))[0].read_text(encoding="utf-8")
    assert "alpha,beta" not in text, text

    payload = _json_payload(_invoke(config, ["get", entry_id, "-k", "notes", "--format", "json"]))
    assert payload["tags"] == ["alpha", "beta"], payload


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


def test_cli_update_tags_preserves_a_body_that_starts_like_frontmatter(tmp_path):
    config, kb_path = _make_env(tmp_path)
    result = _invoke(
        config,
        [
            "create",
            "-k",
            "notes",
            "-t",
            "note",
            "--title",
            "Probe",
            "-b",
            "title: this line is body text\nsecond line",
        ],
    )
    assert result.exit_code == 0, result.output
    path = next(kb_path.rglob("*.md"))
    before = path.read_bytes()
    entry_id = path.stem

    result = _invoke(config, ["update", entry_id, "-k", "notes", "--tags", "x"])
    assert result.exit_code == 0, result.output
    after = path.read_bytes()
    assert isinstance(split_frontmatter(after.decode()), Frontmatter)
    assert split_frontmatter(after.decode()).body == split_frontmatter(before.decode()).body


def test_index_sync_then_get_preserves_a_single_frontmatter_looking_body_line(tmp_path):
    config, kb_path = _make_env(tmp_path)
    result = _invoke(
        config,
        [
            "create",
            "-k",
            "notes",
            "-t",
            "note",
            "--title",
            "Probe",
            "-b",
            "title: this line is body text",
        ],
    )
    assert result.exit_code == 0, result.output
    path = next(kb_path.rglob("*.md"))
    entry_id = path.stem
    result = _invoke(config, ["index", "sync", "-k", "notes"])
    assert result.exit_code == 0, result.output
    payload = _json_payload(_invoke(config, ["get", entry_id, "-k", "notes", "--format", "json"]))
    assert payload["body"] == "title: this line is body text"


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
