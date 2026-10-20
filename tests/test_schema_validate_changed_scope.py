"""Regression test: `pyrite schema validate --changed` must only validate
files that live inside a configured KB, not every changed .md file in the
repo.

Found while fixing ci-make-green-and-load-bearing's pyrite-schema-validate
pre-commit hook: once the hook's `python -m pyrite` invocation bug was
fixed and it could actually run, it immediately flagged non-KB markdown
(e.g. .claude/skills/*.md) as invalid KB entries for lacking frontmatter.
_get_git_changed_md_files() collected every changed .md path repo-wide
with no KB-path filtering.

GitHub #346: with *zero* KBs configured (`config.all_kbs() == []`), the
production call site `_get_git_changed_md_files(config.all_kbs())` hit the
same `if not kbs:` short-circuit as an explicit `None`, so it fell back to
the unfiltered, repo-wide behavior again -- every changed .md file
(CHANGELOG.md, README.md, docs/**) was treated as a KB entry and failed
frontmatter validation. An empty list of KBs means "scope to nothing", not
"no scoping requested" -- only `kbs=None` (no filter argument at all) keeps
the legacy unfiltered behavior for other callers.
"""

import subprocess
import tempfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.cli.schema_commands import _get_git_changed_md_files
from pyrite.config import KBConfig, PyriteConfig, Settings

runner = CliRunner()


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, check=True, timeout=120)


@pytest.fixture
def repo_with_kb_and_non_kb_changes(monkeypatch):
    """A git repo with a KB directory and a non-KB directory, each with an
    uncommitted (untracked) .md file -- the "changed files" scenario the
    pre-commit hook actually hits."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        _git(["init"], repo)
        _git(["config", "user.email", "t@t.com"], repo)
        _git(["config", "user.name", "T"], repo)
        (repo / "README.md").write_text("# repo\n")
        _git(["add", "."], repo)
        _git(["commit", "-m", "init"], repo)

        kb_path = repo / "kb"
        kb_path.mkdir()
        (kb_path / "entry.md").write_text("---\ntype: note\ntitle: Entry\n---\nBody\n")

        non_kb_path = repo / "docs"
        non_kb_path.mkdir()
        (non_kb_path / "guide.md").write_text("# Just a doc, no frontmatter\n")

        monkeypatch.chdir(repo)

        config = PyriteConfig(
            knowledge_bases=[KBConfig(name="test-kb", path=kb_path, kb_type="generic")],
            settings=Settings(index_path=repo / "index.db"),
        )
        yield {"repo": repo, "kb_path": kb_path, "non_kb_path": non_kb_path, "config": config}


class TestGitChangedMdFilesKBScoping:
    def test_filters_to_kb_paths_only(self, repo_with_kb_and_non_kb_changes):
        env = repo_with_kb_and_non_kb_changes
        result = _get_git_changed_md_files(env["config"].all_kbs())

        result_names = {p.name for p in result}
        assert "entry.md" in result_names, "the KB-scoped file must still be included"
        assert "guide.md" not in result_names, (
            f"a changed .md file outside every configured KB must be excluded -- got {result_names}"
        )

    def test_none_arg_preserves_old_unfiltered_behavior(self, repo_with_kb_and_non_kb_changes):
        """Backward compatibility: calling with `kbs=None` (no filter
        argument at all) returns every changed .md file, same as before
        this fix."""
        env = repo_with_kb_and_non_kb_changes
        result = _get_git_changed_md_files(None)

        result_names = {p.name for p in result}
        assert "entry.md" in result_names
        assert "guide.md" in result_names

    def test_empty_kb_list_filters_to_nothing(self, repo_with_kb_and_non_kb_changes):
        """#346: an empty list of KBs (what `config.all_kbs()` returns when
        no KB is configured) means "scope to zero KBs", not "no filter" --
        it must exclude every changed .md file, not fall back to the
        unfiltered repo-wide behavior."""
        env = repo_with_kb_and_non_kb_changes
        result = _get_git_changed_md_files([])

        assert result == [], f"expected no files with zero configured KBs, got {result}"


class TestSchemaValidateChangedNoKBConfigured:
    """#346: `pyrite schema validate --changed` with no KB configured must
    validate nothing and exit 0, instead of treating every changed .md file
    (CHANGELOG.md, README.md, docs/**) as a KB entry."""

    def test_changed_changelog_with_no_kb_configured_exits_zero(self, monkeypatch, tmp_path):
        repo = tmp_path
        _git(["init"], repo)
        _git(["config", "user.email", "t@t.com"], repo)
        _git(["config", "user.name", "T"], repo)
        (repo / "README.md").write_text("# repo\n")
        _git(["add", "."], repo)
        _git(["commit", "-m", "init"], repo)

        # Simulate the release checkout: CHANGELOG.md changed, no
        # frontmatter, and no KB configured at all.
        (repo / "CHANGELOG.md").write_text("# Changelog\n\n## [Unreleased]\n")

        monkeypatch.chdir(repo)

        config = PyriteConfig(knowledge_bases=[], settings=Settings(index_path=repo / "index.db"))

        from unittest.mock import patch

        with patch("pyrite.config.load_config", return_value=config):
            result = runner.invoke(app, ["schema", "validate", "--changed"])

        assert result.exit_code == 0, (
            f"expected exit 0 with no KB configured; got {result.exit_code}. "
            f"Output: {result.output}"
        )
