"""Tests for `pyrite links bulk-create` command."""

import tempfile
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.models import NoteEntry
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager
from pyrite.storage.repository import KBRepository

runner = CliRunner()


@pytest.fixture
def bulk_env():
    """Environment with two entries for bulk link tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        db_path = tmpdir / "index.db"

        notes_path = tmpdir / "notes"
        notes_path.mkdir()

        notes_kb = KBConfig(
            name="test-notes",
            path=notes_path,
            kb_type=KBType.GENERIC,
        )

        config = PyriteConfig(
            knowledge_bases=[notes_kb],
            settings=Settings(index_path=db_path),
        )

        repo = KBRepository(notes_kb)
        for eid in ("entry-a", "entry-b", "entry-c"):
            entry = NoteEntry(
                id=eid,
                title=eid.replace("-", " ").title(),
                body=f"Body for {eid}.",
            )
            repo.save(entry)

        db = PyriteDB(db_path)
        IndexManager(db, config).index_all()
        db.close()

        yield {"config": config, "tmpdir": tmpdir, "notes_path": notes_path}


def _patch_config(env):
    """Patch load_config to return test config."""
    import contextlib
    from unittest.mock import patch

    @contextlib.contextmanager
    def _multi_patch():
        with patch("pyrite.cli.load_config", return_value=env["config"]):
            with patch("pyrite.cli.context.load_config", return_value=env["config"]):
                yield

    return _multi_patch()


@pytest.mark.cli
class TestBulkCreate:
    def test_basic_bulk_create(self, bulk_env):
        """Create multiple links from a YAML file."""
        links = [
            {"source": "entry-a", "target": "entry-b", "relation": "related_to"},
            {"source": "entry-a", "target": "entry-c", "relation": "subtask_of", "note": "test"},
        ]
        yaml_file = bulk_env["tmpdir"] / "links.yaml"
        yaml_file.write_text(yaml.dump(links), encoding="utf-8")

        with _patch_config(bulk_env):
            result = runner.invoke(
                app, ["links", "bulk-create", str(yaml_file), "--kb", "test-notes"]
            )
            assert result.exit_code == 0
            assert "2 created" in result.output
            assert "0 skipped" in result.output
            assert "0 failed" in result.output

        # Verify links were actually written
        repo = KBRepository(bulk_env["config"].knowledge_bases[0])
        entry = repo.load("entry-a")
        assert entry is not None
        targets = {lnk.target for lnk in entry.links}
        assert "entry-b" in targets
        assert "entry-c" in targets

    def test_dry_run(self, bulk_env):
        """Dry run shows what would be created without writing."""
        links = [
            {"source": "entry-a", "target": "entry-b", "relation": "related_to"},
        ]
        yaml_file = bulk_env["tmpdir"] / "links.yaml"
        yaml_file.write_text(yaml.dump(links), encoding="utf-8")

        with _patch_config(bulk_env):
            result = runner.invoke(
                app, ["links", "bulk-create", str(yaml_file), "--kb", "test-notes", "--dry-run"]
            )
            assert result.exit_code == 0
            assert "1 created" in result.output
            assert "create" in result.output

        # Verify no links actually written
        repo = KBRepository(bulk_env["config"].knowledge_bases[0])
        entry = repo.load("entry-a")
        assert entry is not None
        assert len(entry.links) == 0

    def test_duplicate_skipped(self, bulk_env):
        """Existing links are skipped."""
        # Pre-create a link
        repo = KBRepository(bulk_env["config"].knowledge_bases[0])
        entry = repo.load("entry-a")
        entry.add_link(target="entry-b", relation="related_to", kb="test-notes")
        repo.save(entry)

        links = [
            {"source": "entry-a", "target": "entry-b", "relation": "related_to"},
            {"source": "entry-a", "target": "entry-c", "relation": "related_to"},
        ]
        yaml_file = bulk_env["tmpdir"] / "links.yaml"
        yaml_file.write_text(yaml.dump(links), encoding="utf-8")

        with _patch_config(bulk_env):
            result = runner.invoke(
                app, ["links", "bulk-create", str(yaml_file), "--kb", "test-notes"]
            )
            assert result.exit_code == 0
            assert "1 created" in result.output
            assert "1 skipped" in result.output

    def test_distinct_relation_is_created_not_skipped(self, bulk_env):
        """A different relation between the same (source, target, kb) is a
        new link, not a duplicate (#396): the duplicate key is
        (target, kb, relation)."""
        repo = KBRepository(bulk_env["config"].knowledge_bases[0])
        entry = repo.load("entry-a")
        entry.add_link(target="entry-b", relation="related_to", kb="test-notes")
        repo.save(entry)

        links = [
            {"source": "entry-a", "target": "entry-b", "relation": "implements"},
            {"source": "entry-a", "target": "entry-b", "relation": "related_to"},
        ]
        yaml_file = bulk_env["tmpdir"] / "links.yaml"
        yaml_file.write_text(yaml.dump(links), encoding="utf-8")

        with _patch_config(bulk_env):
            result = runner.invoke(
                app, ["links", "bulk-create", str(yaml_file), "--kb", "test-notes"]
            )
            assert result.exit_code == 0
            assert "1 created" in result.output
            assert "1 skipped" in result.output

        repo = KBRepository(bulk_env["config"].knowledge_bases[0])
        entry = repo.load("entry-a")
        relations = {lnk.relation for lnk in entry.links}
        assert relations == {"related_to", "implements"}, relations

    def test_missing_source_entry(self, bulk_env):
        """Missing source entry is reported as failed."""
        links = [
            {"source": "nonexistent", "target": "entry-b", "relation": "related_to"},
        ]
        yaml_file = bulk_env["tmpdir"] / "links.yaml"
        yaml_file.write_text(yaml.dump(links), encoding="utf-8")

        with _patch_config(bulk_env):
            result = runner.invoke(
                app, ["links", "bulk-create", str(yaml_file), "--kb", "test-notes"]
            )
            assert result.exit_code == 1  # nothing created or skipped (#526)
            assert "0 created" in result.output
            assert "1 failed" in result.output
            assert "source entry not found" in result.output

    def test_invalid_yaml(self, bulk_env):
        """Invalid YAML input produces an error."""
        yaml_file = bulk_env["tmpdir"] / "bad.yaml"
        yaml_file.write_text("not: a: valid: yaml: [", encoding="utf-8")

        with _patch_config(bulk_env):
            result = runner.invoke(
                app, ["links", "bulk-create", str(yaml_file), "--kb", "test-notes"]
            )
            assert result.exit_code == 1

    def test_non_list_yaml(self, bulk_env):
        """YAML that's not a list produces an error."""
        yaml_file = bulk_env["tmpdir"] / "notlist.yaml"
        yaml_file.write_text("source: a\ntarget: b\n", encoding="utf-8")

        with _patch_config(bulk_env):
            result = runner.invoke(
                app, ["links", "bulk-create", str(yaml_file), "--kb", "test-notes"]
            )
            assert result.exit_code == 1

    def test_missing_required_fields(self, bulk_env):
        """Specs missing source or target produce validation errors."""
        links = [
            {"source": "entry-a"},  # missing target
            {"target": "entry-b"},  # missing source
        ]
        yaml_file = bulk_env["tmpdir"] / "links.yaml"
        yaml_file.write_text(yaml.dump(links), encoding="utf-8")

        with _patch_config(bulk_env):
            result = runner.invoke(
                app, ["links", "bulk-create", str(yaml_file), "--kb", "test-notes"]
            )
            assert result.exit_code == 1
            assert "missing required field 'target'" in result.output
            assert "missing required field 'source'" in result.output

    def test_stdin_support(self, bulk_env):
        """Reading link specs from stdin via '-' argument."""
        links = [
            {"source": "entry-a", "target": "entry-b", "relation": "related_to"},
        ]
        yaml_input = yaml.dump(links)

        with _patch_config(bulk_env):
            result = runner.invoke(
                app,
                ["links", "bulk-create", "-", "--kb", "test-notes"],
                input=yaml_input,
            )
            assert result.exit_code == 0
            assert "1 created" in result.output

    def test_file_option_flag(self, bulk_env):
        """--file option works as alternative to positional argument."""
        links = [
            {"source": "entry-a", "target": "entry-b", "relation": "related_to"},
        ]
        yaml_file = bulk_env["tmpdir"] / "links.yaml"
        yaml_file.write_text(yaml.dump(links), encoding="utf-8")

        with _patch_config(bulk_env):
            result = runner.invoke(
                app, ["links", "bulk-create", "--file", str(yaml_file), "--kb", "test-notes"]
            )
            assert result.exit_code == 0
            assert "1 created" in result.output

    def test_default_relation(self, bulk_env):
        """Relation defaults to 'related_to' when omitted."""
        links = [
            {"source": "entry-a", "target": "entry-b"},
        ]
        yaml_file = bulk_env["tmpdir"] / "links.yaml"
        yaml_file.write_text(yaml.dump(links), encoding="utf-8")

        with _patch_config(bulk_env):
            result = runner.invoke(
                app, ["links", "bulk-create", str(yaml_file), "--kb", "test-notes"]
            )
            assert result.exit_code == 0

        repo = KBRepository(bulk_env["config"].knowledge_bases[0])
        entry = repo.load("entry-a")
        assert entry is not None
        assert entry.links[0].relation == "related_to"

    def test_target_kb_field(self, bulk_env):
        """target_kb is set on the link when provided."""
        links = [
            {"source": "entry-a", "target": "ext-entry", "target_kb": "other-kb"},
        ]
        yaml_file = bulk_env["tmpdir"] / "links.yaml"
        yaml_file.write_text(yaml.dump(links), encoding="utf-8")

        with _patch_config(bulk_env):
            result = runner.invoke(
                app, ["links", "bulk-create", str(yaml_file), "--kb", "test-notes"]
            )
            assert result.exit_code == 0
            assert "1 created" in result.output

        repo = KBRepository(bulk_env["config"].knowledge_bases[0])
        entry = repo.load("entry-a")
        assert entry is not None
        assert entry.links[0].kb == "other-kb"

    def test_file_not_found(self, bulk_env):
        """Non-existent file path produces an error."""
        with _patch_config(bulk_env):
            result = runner.invoke(
                app, ["links", "bulk-create", "/tmp/nonexistent-links.yaml", "--kb", "test-notes"]
            )
            assert result.exit_code == 1
            assert "File not found" in result.output

    def test_kb_not_found(self, bulk_env):
        """Non-existent KB produces an error."""
        links = [{"source": "a", "target": "b"}]
        yaml_file = bulk_env["tmpdir"] / "links.yaml"
        yaml_file.write_text(yaml.dump(links), encoding="utf-8")

        with _patch_config(bulk_env):
            result = runner.invoke(
                app, ["links", "bulk-create", str(yaml_file), "--kb", "no-such-kb"]
            )
            assert result.exit_code == 1
            assert "KB not found" in result.output


@pytest.mark.cli
def test_bulk_create_edits_an_entry_kept_outside_its_type_directory_in_place(bulk_env):
    """#375: the source is edited where it lives; no second copy appears.

    `links bulk-create` used to `repo.save` the source, which re-derived its
    path from the type. An `epic` kept under `notes/` was written again at the
    KB root, carrying the link, while the original stayed unchanged -- two
    files with one id. It now saves through KBService like `pyrite link`.
    """
    kb_path = bulk_env["notes_path"]
    (kb_path / "notes").mkdir(exist_ok=True)
    original = kb_path / "notes" / "epic-two.md"
    original.write_text("---\nid: epic-two\ntitle: Epic two\ntype: epic\n---\n\nbody\n")
    before = sorted(str(p.relative_to(kb_path)) for p in kb_path.rglob("*.md"))

    with _patch_config(bulk_env):
        result = runner.invoke(
            app,
            ["links", "bulk-create", "--kb", "test-notes", "-"],
            input="- source: epic-two\n  target: entry-a\n",
        )
    assert result.exit_code == 0, result.output
    assert "1 created" in result.output, result.output

    after = sorted(str(p.relative_to(kb_path)) for p in kb_path.rglob("*.md"))
    assert after == before, f"files changed: {set(after) ^ set(before)}"
    assert "entry-a" in original.read_text()
