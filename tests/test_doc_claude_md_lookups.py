"""The documented first lookups work with a fresh or out-of-date index."""

import json
import re
import shlex
import shutil
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from pyrite.cli import app
import pyrite.config as config_module
from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager

ROOT = Path(__file__).resolve().parents[1]


def lookup_commands():
    section = (
        (ROOT / "CLAUDE.md")
        .read_text(encoding="utf-8")
        .split("### Quick Context Lookups", 1)[1]
        .split("### When to Use", 1)[0]
    )
    return [
        shlex.split(line.replace("<topic>", "write").replace("<entry-id>", "design"))[1:]
        for block in re.findall(r"```bash\n(.*?)```", section, re.S)
        for line in block.splitlines()
        if line.startswith(".venv/bin/pyrite ")
    ]


def test_first_lookup_is_incremental_sync_and_entrypoints_read_by_path():
    assert lookup_commands()[0] == ["index", "sync", "-k", "pyrite", "--no-embed"]
    assert "by path" in (ROOT / "CLAUDE.md").read_text(encoding="utf-8").split("## Using", 1)[0]
    start = (ROOT / "AGENTS.md").read_text(encoding="utf-8").split("## Start here", 1)[1]
    assert start.index("kb/design.md") < start.index("pyrite orient")


@pytest.mark.parametrize("stale", [False, True])
def test_documented_commands_find_current_pages(tmp_path, stale, monkeypatch):
    kb_path = tmp_path / "kb"
    kb_path.mkdir()
    # Real repo pages, bounded to what these examples consult. Indexing
    # hundreds of unrelated done backlog files would slow this doc contract.
    for relative in [
        "kb.yaml",
        "design.md",
        "adrs/0042-a-write-changes-what-was-asked-and-nothing-else.md",
        "components/index-manager.md",
    ]:
        target = kb_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / "kb" / relative, target)
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="pyrite", path=kb_path, kb_type="software")],
        settings=Settings(index_path=tmp_path / "index.db", auto_embed=False),
    )
    data = tmp_path / "data"
    data.mkdir()
    config.settings.index_path = data / "index.db"
    (data / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "knowledge_bases": [
                    {"name": "pyrite", "path": str(kb_path), "kb_type": "software"}
                ],
                "settings": {"index_path": str(config.settings.index_path), "auto_embed": False},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PYRITE_DATA_DIR", str(data))
    monkeypatch.setattr(config_module, "CONFIG_DIR", data)
    monkeypatch.setattr(config_module, "CONFIG_FILE", data / "config.yaml")
    if stale:
        missing = [kb_path / "design.md", next((kb_path / "adrs").glob("0042-*.md"))]
        saved = {path: path.read_bytes() for path in missing}
        for path in missing:
            path.unlink()
        db = PyriteDB(config.settings.index_path)
        IndexManager(db, config).index_all()
        assert db.get_entry("design", "pyrite") is None
        assert db.get_entry("adr-0042", "pyrite") is None
        db.close()
        for path, content in saved.items():
            path.write_bytes(content)
    results = []
    for args in lookup_commands():
        result = CliRunner().invoke(app, args)
        assert result.exit_code == 0, (args, result.output)
        if args[:2] != ["index", "sync"]:
            results.append((args, json.loads(result.stdout)))
    assert any(args[:2] == ["get", "design"] and body["id"] == "design" for args, body in results)
    assert any(
        args[:2] == ["get", "adr-0042"] and body["id"] == "adr-0042" for args, body in results
    )
