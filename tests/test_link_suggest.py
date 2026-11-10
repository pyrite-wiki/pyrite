"""Tests for pyrite links suggest command.

Uses shared fixtures and config patching.
"""

import json
import tempfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager

runner = CliRunner()


@pytest.fixture
def suggest_env():
    """Environment with multiple related entries for suggestion tests."""
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

        db = PyriteDB(db_path)
        svc = KBService(config, db)

        # Create entries with overlapping content for suggestion
        svc.create_entry(
            "test-notes",
            "python-testing",
            "Python Testing Best Practices",
            "note",
            body="How to write good tests in Python using pytest and unittest.",
            tags=["python", "testing"],
        )
        svc.create_entry(
            "test-notes",
            "pytest-fixtures",
            "Pytest Fixtures Guide",
            "note",
            body="Comprehensive guide to pytest fixtures and parameterization.",
            tags=["python", "testing", "pytest"],
        )
        svc.create_entry(
            "test-notes",
            "rust-ownership",
            "Rust Ownership Model",
            "note",
            body="Understanding ownership and borrowing in Rust programming.",
            tags=["rust", "memory"],
        )
        svc.create_entry(
            "test-notes",
            "python-typing",
            "Python Type Hints",
            "note",
            body="Using type hints and mypy for Python static analysis.",
            tags=["python", "typing"],
        )
        svc.create_entry(
            "test-notes",
            "unrelated-cooking",
            "Pasta Recipes",
            "note",
            body="Italian pasta recipes and cooking techniques.",
            tags=["cooking", "food"],
        )
        # Entry with FTS column names and special characters for regression testing (#304)
        svc.create_entry(
            "test-notes",
            "fts-adversarial",
            "Regulatory capture and legalism: cross-link 18:1 v1.2.3",
            "note",
            body="Analysis of regulatory capture, legalism, and cross-link standards.",
            tags=["capture", "legalism", "cross-link", "18:1", "1.2.3"],
        )
        svc.create_entry(
            "test-notes",
            "fts-match",
            "Legalism and Cross-Link Dynamics",
            "note",
            body="Discussion of legalism, regulatory capture, and cross-link connections.",
            tags=["capture", "cross-link"],
        )

        IndexManager(db, config).index_all()
        db.close()

        yield {"config": config, "tmpdir": tmpdir}


@pytest.fixture
def cross_kb_env():
    """Environment with two KBs for cross-KB suggestion tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        db_path = tmpdir / "index.db"

        kb_a_path = tmpdir / "kb-a"
        kb_a_path.mkdir()
        kb_b_path = tmpdir / "kb-b"
        kb_b_path.mkdir()

        kb_a = KBConfig(name="kb-a", path=kb_a_path, kb_type=KBType.GENERIC)
        kb_b = KBConfig(name="kb-b", path=kb_b_path, kb_type=KBType.GENERIC)

        config = PyriteConfig(
            knowledge_bases=[kb_a, kb_b],
            settings=Settings(index_path=db_path),
        )

        db = PyriteDB(db_path)
        svc = KBService(config, db)

        svc.create_entry(
            "kb-a",
            "source-entry",
            "Machine Learning Basics",
            "note",
            body="Introduction to machine learning concepts.",
            tags=["ml", "ai"],
        )
        svc.create_entry(
            "kb-b",
            "target-ml",
            "Deep Learning Frameworks",
            "note",
            body="Overview of machine learning frameworks like PyTorch.",
            tags=["ml", "deep-learning"],
        )
        svc.create_entry(
            "kb-b",
            "target-unrelated",
            "Gardening Tips",
            "note",
            body="How to grow tomatoes in your garden.",
            tags=["gardening"],
        )

        IndexManager(db, config).index_all()
        db.close()

        yield {"config": config, "tmpdir": tmpdir}


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
class TestLinksSuggest:
    def test_basic_suggestion(self, suggest_env):
        """Entry with related content finds matching entries."""
        with _patch_config(suggest_env):
            result = runner.invoke(
                app, ["links", "suggest", "python-testing", "--kb", "test-notes"]
            )
            assert result.exit_code == 0
            # Should find pytest-fixtures and python-typing as related
            assert "pytest-fixtures" in result.output or "python-typing" in result.output

    def test_basic_suggestion_json(self, suggest_env):
        """JSON output contains expected structure."""
        with _patch_config(suggest_env):
            result = runner.invoke(
                app,
                [
                    "links",
                    "suggest",
                    "python-testing",
                    "--kb",
                    "test-notes",
                    "--format",
                    "json",
                ],
            )
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert "entry_id" in data
            assert data["entry_id"] == "python-testing"
            assert data["kb_name"] == "test-notes"
            assert "suggestions" in data
            assert "count" in data
            assert isinstance(data["suggestions"], list)
            # Each suggestion should have required fields
            if data["suggestions"]:
                s = data["suggestions"][0]
                assert "id" in s
                assert "title" in s
                assert "score" in s

    def test_self_excluded(self, suggest_env):
        """Entry does not suggest itself."""
        with _patch_config(suggest_env):
            result = runner.invoke(
                app,
                [
                    "links",
                    "suggest",
                    "python-testing",
                    "--kb",
                    "test-notes",
                    "--format",
                    "json",
                ],
            )
            assert result.exit_code == 0
            data = json.loads(result.output)
            ids = [s["id"] for s in data["suggestions"]]
            assert "python-testing" not in ids

    def test_limit_parameter(self, suggest_env):
        """--limit restricts number of suggestions."""
        with _patch_config(suggest_env):
            result = runner.invoke(
                app,
                [
                    "links",
                    "suggest",
                    "python-testing",
                    "--kb",
                    "test-notes",
                    "--limit",
                    "1",
                    "--format",
                    "json",
                ],
            )
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert len(data["suggestions"]) <= 1

    @pytest.mark.parametrize("bad_limit", [["--limit", "0"], ["--limit", "-1"], ["-n", "-1"]])
    def test_limit_below_one_is_usage_error(self, suggest_env, bad_limit):
        """--limit/-n below 1 is refused instead of returning one suggestion."""
        with _patch_config(suggest_env):
            result = runner.invoke(
                app,
                ["links", "suggest", "python-testing", "--kb", "test-notes", *bad_limit],
            )
            assert result.exit_code == 2
            assert "Invalid value for '--limit'" in result.output

    def test_entry_not_found(self, suggest_env):
        """Non-existent entry returns error."""
        with _patch_config(suggest_env):
            result = runner.invoke(
                app,
                ["links", "suggest", "nonexistent-entry", "--kb", "test-notes"],
            )
            assert result.exit_code == 1
            assert "not found" in result.output.lower()

    def test_cross_kb_suggestion(self, cross_kb_env):
        """--target-kb searches a different KB."""
        with _patch_config(cross_kb_env):
            result = runner.invoke(
                app,
                [
                    "links",
                    "suggest",
                    "source-entry",
                    "--kb",
                    "kb-a",
                    "--target-kb",
                    "kb-b",
                    "--format",
                    "json",
                ],
            )
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["target_kb"] == "kb-b"
            # All suggestions should be from kb-b
            for s in data["suggestions"]:
                assert s["kb_name"] == "kb-b"

    def test_json_output_format(self, suggest_env):
        """JSON format is valid and machine-parseable."""
        with _patch_config(suggest_env):
            result = runner.invoke(
                app,
                [
                    "links",
                    "suggest",
                    "python-testing",
                    "--kb",
                    "test-notes",
                    "--format",
                    "json",
                ],
            )
            assert result.exit_code == 0
            # Must be valid JSON
            data = json.loads(result.output)
            assert data["count"] == len(data["suggestions"])

    def test_rich_output_no_results(self, suggest_env):
        """Rich output handles no-suggestions gracefully."""
        with _patch_config(suggest_env):
            result = runner.invoke(
                app,
                [
                    "links",
                    "suggest",
                    "unrelated-cooking",
                    "--kb",
                    "test-notes",
                ],
            )
            assert result.exit_code == 0

    def test_suggest_fts_quoting_regression(self, suggest_env):
        """Regression test for #304: entries with FTS-column-colliding vocabulary
        (capture, legalism) or special characters (cross-link, 18:1, 1.2.3) in titles
        or tags must be quoted so FTS5 does not raise OperationalError / QuerySyntaxError.
        """
        with _patch_config(suggest_env):
            result = runner.invoke(
                app,
                [
                    "links",
                    "suggest",
                    "fts-adversarial",
                    "--kb",
                    "test-notes",
                    "--format",
                    "json",
                ],
            )
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["entry_id"] == "fts-adversarial"
            assert isinstance(data["suggestions"], list)
            assert any(s["id"] == "fts-match" for s in data["suggestions"])
