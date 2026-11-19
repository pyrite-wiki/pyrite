"""User troubleshooting and curl examples exercise actual files, CLI and REST."""

import json
import os
from pathlib import Path
import re
import shlex
from urllib.parse import urlsplit

import pytest

from pyrite.config import KBConfig
from pyrite.storage.index import IndexManager

ROOT = Path(__file__).resolve().parents[1]


def _seed(config, db, name="test-kb"):
    folder = config.get_kb(name).path
    path = folder / "hello.md"
    path.write_text(
        "---\nid: hello\ntitle: Hello\ntype: note\n---\nInitial body\n", encoding="utf-8"
    )
    IndexManager(db, config).index_all()
    return path


@pytest.mark.skipif(os.name == "nt", reason="REST create example uses POSIX file publication")
def test_every_documented_curl_call_runs_against_real_app(make_client):
    client, config, db = make_client(api_key="my-key")
    _seed(config, db)
    page = (ROOT / "docs/rest-api.md").read_text(encoding="utf-8")
    calls = re.findall(r"^curl .+$", page, re.M)
    assert len(calls) == 6  # the Bearer example joined the key examples (#1754)
    for line in calls:
        tokens = shlex.split(line)[1:]
        method, headers, body, url = "GET", {}, None, None
        while tokens:
            token = tokens.pop(0)
            if token == "-X":
                method = tokens.pop(0)
            elif token == "-H":
                key, value = tokens.pop(0).split(":", 1)
                headers[key] = value.strip()
            elif token == "-d":
                body = json.loads(tokens.pop(0))
            else:
                url = token
        parts = urlsplit(url)
        response = client.request(
            method,
            parts.path + ("?" + parts.query if parts.query else ""),
            headers=headers,
            json=body,
        )
        assert response.status_code in {200, 201}, (line, response.text)
        if parts.path.endswith("/hello"):
            assert response.json()["id"] == "hello"
        if parts.path.endswith("/search"):
            assert any(row["id"] == "hello" for row in response.json()["results"])


def test_stale_external_edit_and_documented_checks(cli_world):
    world = cli_world()
    path = _seed(world.config, world.db)
    path.write_text(
        path.read_text(encoding="utf-8").replace("Hello", "Changed title"), encoding="utf-8"
    )
    result = world.cli("get", "hello", "-k", "test-kb")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["title"] == "Changed title"
    assert world.db.get_entry("hello", "test-kb")["title"] == "Hello"
    page = (ROOT / "docs/troubleshooting.md").read_text(encoding="utf-8")
    commands = re.findall(r"^pyrite .+$", page, re.M)
    assert len(commands) == 3
    for command in commands:
        result = world.cli(*shlex.split(command)[1:])
        assert result.exit_code == (1 if command == "pyrite index health" else 0), (
            command,
            result.output,
        )


def test_shortname_and_missing_yaml_diagnostics(cli_world):
    world = cli_world()
    path = _seed(world.config, world.db)
    path.write_text("---\ntitle: [broken\n---\n", encoding="utf-8")
    result = world.cli("get", "hello", "-k", "test-kb")
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error_code"] == "NOT_FOUND"
    world.config.knowledge_bases[0].shortname = "short"
    from pyrite.config import save_config

    save_config(world.config)
    result = world.cli("list-entries", "-k", "short")
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error_code"] == "KB_NOT_FOUND"


def test_ambiguous_id_requires_full_kb_name(cli_world, tmp_path):
    world = cli_world(
        kbs=[
            KBConfig(name=name, path=tmp_path / name, kb_type="generic") for name in ("one", "two")
        ]
    )
    for name in ("one", "two"):
        _seed(world.config, world.db, name)
    result = world.cli("get", "hello")
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error_code"] == "AMBIGUOUS"
    assert world.cli("get", "hello", "-k", "one").exit_code == 0


def test_new_pages_are_linked_and_nonportable_cases_are_explicit():
    index = (ROOT / "docs/README.md").read_text(encoding="utf-8")
    for name in ("rest-api.md", "web-ui.md", "troubleshooting.md"):
        assert f"({name})" in index
    page = (ROOT / "docs/troubleshooting.md").read_text(encoding="utf-8")
    assert page.count("not-run") >= 4
    for term in (
        "double_spellings",
        "unmodellable_frontmatter",
        "links_stale_kb",
        "CONFLICT",
        "STORAGE_ERROR",
    ):
        assert term in page


def test_health_names_double_spellings_and_tagged_keys(cli_world):
    world = cli_world()
    folder = world.config.get_kb("test-kb").path
    (folder / "double.md").write_text(
        "---\nid: double\ntitle: Double\ntype: note\nimportance: 7\nmetadata:\n  importance: 8\n---\n",
        encoding="utf-8",
    )
    (folder / "tagged.md").write_text(
        "---\nid: tagged\ntitle: Tagged\ntype: note\n!!str flag: 1\n---\n", encoding="utf-8"
    )
    result = world.cli("index", "health")
    assert result.exit_code == 1
    body = json.loads(result.stdout)
    assert any(row["field"] == "importance" for row in body["double_spellings"])
    assert any(Path(row["path"]).name == "tagged.md" for row in body["unmodellable_frontmatter"])


def test_reserved_name_is_refused_before_creating_kb(cli_world, tmp_path):
    from pyrite.services.kb_names import RESERVED_KB_NAMES

    world = cli_world()
    folder = tmp_path / "new-kb"
    result = world.cli(
        "init", "-t", "research", "-n", sorted(RESERVED_KB_NAMES)[0], "-p", str(folder)
    )
    assert result.exit_code == 1
    assert "INVALID_KB_NAME" in result.output
    assert not (folder / "kb.yaml").exists()


def test_web_ui_landing_mode_documentation():
    web_ui = (ROOT / "docs/web-ui.md").read_text(encoding="utf-8")
    assert "configuration.md#where-a-users-write-lands-landing" in web_ui
    assert "never writes to the main KB" not in web_ui
