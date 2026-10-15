"""Tests for ExportService."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pyrite.exceptions import KBNotFoundError, PyriteError
from pyrite.services.export_service import ExportService


@pytest.fixture
def mock_config():
    config = MagicMock()
    return config


@pytest.fixture
def mock_db():
    return MagicMock()


@pytest.fixture
def export_svc(mock_config, mock_db):
    return ExportService(mock_config, mock_db)


class TestExportKBToDirectory:
    def test_exports_entries_as_markdown(self, export_svc, mock_config, mock_db, tmp_path):
        kb_cfg = MagicMock()
        kb_cfg.kb_yaml_path = tmp_path / "kb.yaml"
        kb_cfg.kb_yaml_path.write_text("name: test")
        mock_config.require_kb.return_value = kb_cfg

        mock_db.list_entries.return_value = [
            {
                "id": "entry1",
                "entry_type": "note",
                "title": "Test Entry",
                "body": "Hello world",
                "tags": ["foo", "bar"],
            }
        ]

        target = tmp_path / "export"
        result = export_svc.export_kb_to_directory("test", target)

        assert result["entries_exported"] == 1
        assert result["files_created"] == 1

        exported_file = target / "note" / "entry1.md"
        assert exported_file.exists()
        content = exported_file.read_text()
        assert "title: Test Entry" in content
        assert "Hello world" in content

    def test_raises_on_unknown_kb(self, export_svc, mock_config, tmp_path):
        mock_config.require_kb.side_effect = KBNotFoundError("KB 'nonexistent' not found")
        with pytest.raises(KBNotFoundError):
            export_svc.export_kb_to_directory("nonexistent", tmp_path / "out")

    def test_yaml_special_chars_in_title_are_quoted(
        self, export_svc, mock_config, mock_db, tmp_path
    ):
        """Titles with YAML-special characters must be properly quoted."""
        kb_cfg = MagicMock()
        kb_cfg.kb_yaml_path = tmp_path / "kb.yaml"
        kb_cfg.kb_yaml_path.write_text("name: test")
        mock_config.require_kb.return_value = kb_cfg

        mock_db.list_entries.return_value = [
            {
                "id": "tricky",
                "entry_type": "note",
                "title": 'Value with: colon and "quotes"',
                "body": "Body text",
                "tags": [],
            }
        ]

        target = tmp_path / "export"
        export_svc.export_kb_to_directory("test", target)

        content = (target / "note" / "tricky.md").read_text()
        # The frontmatter should be parseable YAML
        import yaml

        parts = content.split("---")
        fm = yaml.safe_load(parts[1])
        assert fm["title"] == 'Value with: colon and "quotes"'

    def test_title_with_newline_does_not_inject_fields(
        self, export_svc, mock_config, mock_db, tmp_path
    ):
        """Newlines in title must not inject additional YAML fields."""
        kb_cfg = MagicMock()
        kb_cfg.kb_yaml_path = tmp_path / "kb.yaml"
        kb_cfg.kb_yaml_path.write_text("name: test")
        mock_config.require_kb.return_value = kb_cfg

        mock_db.list_entries.return_value = [
            {
                "id": "inject",
                "entry_type": "note",
                "title": "Normal\nevil_field: injected",
                "body": "Body",
                "tags": [],
            }
        ]

        target = tmp_path / "export"
        export_svc.export_kb_to_directory("test", target)

        content = (target / "note" / "inject.md").read_text()
        import yaml

        parts = content.split("---")
        fm = yaml.safe_load(parts[1])
        # The evil_field should NOT appear as a separate key
        assert "evil_field" not in fm
        assert "injected" in fm["title"]


class TestCommitKB:
    @patch("pyrite.services.git_service.GitService")
    def test_delegates_to_git_service(self, mock_git_svc, export_svc, mock_config):
        kb_cfg = MagicMock()
        kb_cfg.path = Path("/fake/kb")
        mock_config.require_kb.return_value = kb_cfg
        mock_git_svc.is_git_repo.return_value = True
        mock_git_svc.commit.return_value = (True, {"commit_hash": "abc123", "files_changed": 2})

        result = export_svc.commit_kb("test", "my commit")

        mock_git_svc.commit.assert_called_once_with(
            Path("/fake/kb"), "my commit", paths=None, sign_off=False
        )
        assert result["success"] is True
        assert result["commit_hash"] == "abc123"

    @patch("pyrite.services.git_service.GitService")
    def test_raises_on_unknown_kb(self, mock_git_svc, export_svc, mock_config):
        mock_config.require_kb.side_effect = KBNotFoundError("KB 'nonexistent' not found")
        with pytest.raises(KBNotFoundError):
            export_svc.commit_kb("nonexistent", "msg")

    @patch("pyrite.services.git_service.GitService")
    def test_raises_on_non_git_repo(self, mock_git_svc, export_svc, mock_config):
        kb_cfg = MagicMock()
        kb_cfg.path = Path("/fake/kb")
        mock_config.require_kb.return_value = kb_cfg
        mock_git_svc.is_git_repo.return_value = False

        with pytest.raises(PyriteError, match="not in a git repository"):
            export_svc.commit_kb("test", "msg")


class TestPathTraversalPrevention:
    """Entry IDs with path traversal components must not write outside target dir."""

    def test_entry_id_with_path_traversal_stays_inside_target(
        self, export_svc, mock_config, mock_db, tmp_path
    ):
        kb_cfg = MagicMock()
        kb_cfg.kb_yaml_path = tmp_path / "kb.yaml"
        kb_cfg.kb_yaml_path.write_text("name: test")
        mock_config.require_kb.return_value = kb_cfg

        mock_db.list_entries.return_value = [
            {
                "id": "../../etc/evil",
                "entry_type": "note",
                "title": "Evil Entry",
                "body": "pwned",
                "tags": [],
            }
        ]

        target = tmp_path / "export"
        result = export_svc.export_kb_to_directory("test", target)

        assert result["files_created"] == 1

        # The file must NOT exist outside the target directory
        evil_path = target / "note" / "../../etc/evil.md"
        # Resolve to see if it actually landed outside
        assert not (tmp_path / "etc" / "evil.md").exists()
        assert not Path("/etc/evil.md").exists()

        # The file MUST exist inside target/note/ with a sanitized name
        note_dir = target / "note"
        files = list(note_dir.iterdir())
        assert len(files) == 1
        # The file must be inside the note_dir (resolved path is under target)
        assert files[0].resolve().is_relative_to(target.resolve())

    def test_entry_id_with_backslash_traversal(self, export_svc, mock_config, mock_db, tmp_path):
        kb_cfg = MagicMock()
        kb_cfg.kb_yaml_path = tmp_path / "kb.yaml"
        kb_cfg.kb_yaml_path.write_text("name: test")
        mock_config.require_kb.return_value = kb_cfg

        mock_db.list_entries.return_value = [
            {
                "id": "..\\..\\etc\\evil",
                "entry_type": "note",
                "title": "Evil Entry",
                "body": "pwned",
                "tags": [],
            }
        ]

        target = tmp_path / "export"
        result = export_svc.export_kb_to_directory("test", target)

        assert result["files_created"] == 1
        # Must not escape the target directory
        note_dir = target / "note"
        files = list(note_dir.iterdir())
        assert len(files) == 1
        assert files[0].resolve().is_relative_to(target.resolve())

    def test_entry_type_absolute_path_stays_inside_target(
        self, export_svc, mock_config, mock_db, tmp_path
    ):
        """CodeQL #16: an entry_type that is an absolute path must not become
        an absolute path join -- it must land inside target_dir."""
        kb_cfg = MagicMock()
        kb_cfg.kb_yaml_path = tmp_path / "kb.yaml"
        kb_cfg.kb_yaml_path.write_text("name: test")
        mock_config.require_kb.return_value = kb_cfg

        outside = tmp_path / "outside"
        mock_db.list_entries.return_value = [
            {
                "id": "evil-entry",
                "entry_type": str(outside),
                "title": "Evil Entry",
                "body": "pwned",
                "tags": [],
            }
        ]

        target = tmp_path / "export"
        result = export_svc.export_kb_to_directory("test", target)

        assert result["files_created"] == 1
        # Nothing must be created at the absolute path the attacker supplied
        assert not outside.exists()

        # The file must exist inside target, under a sanitized type dir
        written = list(target.rglob("*.md"))
        assert len(written) == 1
        assert written[0].resolve().is_relative_to(target.resolve())

    def test_entry_type_traversal_stays_inside_target(
        self, export_svc, mock_config, mock_db, tmp_path
    ):
        kb_cfg = MagicMock()
        kb_cfg.kb_yaml_path = tmp_path / "kb.yaml"
        kb_cfg.kb_yaml_path.write_text("name: test")
        mock_config.require_kb.return_value = kb_cfg

        mock_db.list_entries.return_value = [
            {
                "id": "evil-entry",
                "entry_type": "../../outside",
                "title": "Evil Entry",
                "body": "pwned",
                "tags": [],
            }
        ]

        target = tmp_path / "export"
        result = export_svc.export_kb_to_directory("test", target)

        assert result["files_created"] == 1
        assert not (tmp_path / "outside").exists()

        written = list(target.rglob("*.md"))
        assert len(written) == 1
        assert written[0].resolve().is_relative_to(target.resolve())

    def test_normal_type_still_gets_own_subdir(self, export_svc, mock_config, mock_db, tmp_path):
        """A normal plugin type name must still get its own, unchanged subdirectory."""
        kb_cfg = MagicMock()
        kb_cfg.kb_yaml_path = tmp_path / "kb.yaml"
        kb_cfg.kb_yaml_path.write_text("name: test")
        mock_config.require_kb.return_value = kb_cfg

        mock_db.list_entries.return_value = [
            {
                "id": "widget-1",
                "entry_type": "cascade_event",
                "title": "A Cascade Event",
                "body": "content",
                "tags": [],
            }
        ]

        target = tmp_path / "export"
        export_svc.export_kb_to_directory("test", target)

        assert (target / "cascade_event" / "widget-1.md").exists()

    def test_colliding_ids_produce_two_files(self, export_svc, mock_config, mock_db, tmp_path):
        """Two distinct raw ids that sanitize to the same filename ("a/b" and
        "a_b" both -> note/a_b.md) must not let one entry's export silently
        overwrite the other's (#221 redispatch cold read)."""
        kb_cfg = MagicMock()
        kb_cfg.kb_yaml_path = tmp_path / "kb.yaml"
        kb_cfg.kb_yaml_path.write_text("name: test")
        mock_config.require_kb.return_value = kb_cfg

        mock_db.list_entries.return_value = [
            {"id": "a/b", "entry_type": "note", "title": "One", "body": "one content", "tags": []},
            {"id": "a_b", "entry_type": "note", "title": "Two", "body": "two content", "tags": []},
        ]

        target = tmp_path / "export"
        result = export_svc.export_kb_to_directory("test", target)

        assert result["entries_exported"] == 2
        written = list((target / "note").glob("*.md"))
        assert len(written) == 2
        all_content = "\n".join(p.read_text() for p in written)
        assert "one content" in all_content
        assert "two content" in all_content

    def test_colliding_types_produce_two_dirs(self, export_svc, mock_config, mock_db, tmp_path):
        """Two distinct raw types that sanitize to the same directory
        ("note" and "note_" both -> note/) must not let one type's export
        silently overwrite the other's (#221 redispatch cold read)."""
        kb_cfg = MagicMock()
        kb_cfg.kb_yaml_path = tmp_path / "kb.yaml"
        kb_cfg.kb_yaml_path.write_text("name: test")
        mock_config.require_kb.return_value = kb_cfg

        mock_db.list_entries.return_value = [
            {
                "id": "real-note",
                "entry_type": "note",
                "title": "Real",
                "body": "real content",
                "tags": [],
            },
            {
                "id": "evil-entry",
                "entry_type": "note_",
                "title": "Evil",
                "body": "EVIL",
                "tags": [],
            },
        ]

        target = tmp_path / "export"
        result = export_svc.export_kb_to_directory("test", target)

        assert result["entries_exported"] == 2
        written = list(target.rglob("*.md"))
        assert len(written) == 2
        all_content = "\n".join(p.read_text() for p in written)
        assert "real content" in all_content
        assert "EVIL" in all_content

    def test_frontmatter_type_keeps_raw_value_when_type_is_malicious(
        self, export_svc, mock_config, mock_db, tmp_path
    ):
        """The stored/exported frontmatter `type:` must keep the raw value --
        only the directory name is sanitized."""
        kb_cfg = MagicMock()
        kb_cfg.kb_yaml_path = tmp_path / "kb.yaml"
        kb_cfg.kb_yaml_path.write_text("name: test")
        mock_config.require_kb.return_value = kb_cfg

        raw_type = "../../outside"
        mock_db.list_entries.return_value = [
            {
                "id": "evil-entry",
                "entry_type": raw_type,
                "title": "Evil Entry",
                "body": "pwned",
                "tags": [],
            }
        ]

        target = tmp_path / "export"
        export_svc.export_kb_to_directory("test", target)

        written = list(target.rglob("*.md"))
        assert len(written) == 1
        content = written[0].read_text()
        import yaml

        parts = content.split("---")
        fm = yaml.safe_load(parts[1])
        assert fm["type"] == raw_type


class TestPushKB:
    @patch("pyrite.services.git_service.GitService")
    def test_delegates_to_git_service(self, mock_git_svc, export_svc, mock_config):
        kb_cfg = MagicMock()
        kb_cfg.path = Path("/fake/kb")
        mock_config.require_kb.return_value = kb_cfg
        mock_git_svc.is_git_repo.return_value = True
        mock_git_svc.push.return_value = (True, "pushed successfully")

        result = export_svc.push_kb("test")

        mock_git_svc.push.assert_called_once_with(Path("/fake/kb"), remote="origin", branch=None)
        assert result["success"] is True
        assert result["message"] == "pushed successfully"

    @patch("pyrite.services.git_service.GitService")
    def test_raises_on_unknown_kb(self, mock_git_svc, export_svc, mock_config):
        mock_config.require_kb.side_effect = KBNotFoundError("KB 'nonexistent' not found")
        with pytest.raises(KBNotFoundError):
            export_svc.push_kb("nonexistent")
