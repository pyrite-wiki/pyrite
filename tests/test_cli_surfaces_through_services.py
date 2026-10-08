"""CLI commands that #380 moved off direct DB access, characterized first.

Each test drives the real Typer app against a temporary index and pins
what the command prints or returns, so the move behind services is
checked against behaviour rather than against the diff.
"""

import contextlib
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.models.core_types import NoteEntry
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager

runner = CliRunner()

pytestmark = pytest.mark.control(
    reason="characterization for #380: pins behaviour before it moves behind a service, so it passes with and without the change by design"
)


def _config(tmp_path, *, indexed=True):
    kb_path = tmp_path / "notes"
    kb_path.mkdir()
    for i in range(2):
        NoteEntry(id=f"n{i}", title=f"Note {i}", body=f"body {i}").save(kb_path / f"n{i}.md")
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="notes", path=kb_path, kb_type=KBType.GENERIC)],
        settings=Settings(index_path=tmp_path / "index.db", workspace_path=tmp_path / "ws"),
    )
    if indexed:
        db = PyriteDB(config.settings.index_path)
        IndexManager(db, config).index_all()
        db.close()
    return config


@contextlib.contextmanager
def _patched(config):
    # Import every module that owns one of the patch targets below *before*
    # any patch is entered (#510). `patch("a.b.c", ...)` imports `a.b` to
    # resolve its target if it isn't already in sys.modules. If that import
    # happens while an earlier patch in this same stack has already replaced
    # `pyrite.config.load_config` with a Mock, a module that does
    # `from .config import load_config` binds the Mock at import time, and
    # `patch` then "restores" its attribute to that Mock on exit -- leaking
    # it into every later test in the same worker. Importing up front means
    # every module's `load_config` is already bound to the real function
    # before any Mock exists, so each patch's own restore is the real thing.
    import pyrite.admin_cli  # noqa: F401
    import pyrite.cli  # noqa: F401
    import pyrite.cli.context  # noqa: F401
    import pyrite.cli.repo_commands  # noqa: F401
    import pyrite.cli.search_commands  # noqa: F401
    import pyrite.config  # noqa: F401

    with contextlib.ExitStack() as stack:
        for target in (
            "pyrite.config.load_config",
            "pyrite.cli.load_config",
            "pyrite.cli.context.load_config",
            "pyrite.cli.repo_commands.load_config",
            "pyrite.cli.search_commands.load_config",
            "pyrite.admin_cli.load_config",
        ):
            stack.enter_context(patch(target, return_value=config))
        yield


@pytest.fixture
def config(tmp_path):
    return _config(tmp_path)


@pytest.fixture
def repo_config(config):
    """A subscribed repo holding the `notes` KB."""
    db = PyriteDB(config.settings.index_path)
    repo = db.register_repo("org/notes", str(config.knowledge_bases[0].path))
    db.link_kb_to_repo("notes", repo["id"], "")
    db.close()
    return config


class TestRepoCommands:
    def test_repo_status_rich_lists_kbs_with_entry_counts(self, repo_config):
        from pyrite.cli import app

        with _patched(repo_config):
            result = runner.invoke(app, ["repo", "status", "org/notes", "--format", "rich"])
        assert result.exit_code == 0, result.output
        assert "KBs: 1" in result.output
        assert "- notes (2 entries)" in result.output

    def test_repo_list_rich_counts_kbs(self, repo_config):
        from pyrite.cli import app

        with _patched(repo_config):
            result = runner.invoke(app, ["repo", "list", "--format", "rich"])
        assert result.exit_code == 0, result.output
        row = next(line for line in result.output.splitlines() if "org/notes" in line)
        assert row.rstrip(" │|").split()[-1] == "1"

    def test_repo_status_json_shape(self, repo_config):
        from pyrite.cli import app

        with _patched(repo_config):
            result = runner.invoke(app, ["repo", "status", "org/notes", "--format", "json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["kb_names"] == ["notes"]
        assert data["total_entries"] == 2


class TestQAAndExport:
    def test_qa_checkers_kb_coverage_lists_entry_types(self, config):
        from pyrite.cli import app

        # Every type gets a one-item rubric, so each entry type the KB holds
        # prints a "Type:" header -- which is what the type query decides.
        with (
            _patched(config),
            patch(
                "pyrite.schema.core_types.resolve_type_metadata",
                return_value={"evaluation_rubric": ["judge me"]},
            ),
        ):
            result = runner.invoke(app, ["qa", "checkers", "--kb", "notes"])
        assert result.exit_code == 0, result.output
        assert [line for line in result.output.splitlines() if line.startswith("Type:")] == [
            "Type: note (1 items):"
        ]

    def test_qa_checkers_unknown_kb(self, config):
        from pyrite.cli import app

        with _patched(config):
            result = runner.invoke(app, ["qa", "checkers", "--kb", "nope"])
        assert result.exit_code == 1
        assert "KB 'nope' not found" in result.output

    def test_export_site_writes_every_entry(self, config, tmp_path):
        from pyrite.cli import app

        out = tmp_path / "site"
        with _patched(config):
            result = runner.invoke(app, ["export", "site", "-k", "notes", "-o", str(out)])
        assert result.exit_code == 0, result.output
        written = sorted(p.name for p in out.rglob("*.md"))
        assert any("n0" in n or "note-0" in n for n in written), written
        assert any("n1" in n or "note-1" in n for n in written), written


class TestSearchAndIndex:
    def test_search_on_an_empty_index_builds_it_first(self, tmp_path):
        from pyrite.cli import app

        config = _config(tmp_path, indexed=False)
        with _patched(config):
            result = runner.invoke(app, ["search", "body", "--format", "json"])
        assert result.exit_code == 0, result.output
        db = PyriteDB(config.settings.index_path)
        try:
            assert db.count_entries() == 2
        finally:
            db.close()

    def test_index_embed_refuses_an_empty_index(self, tmp_path):
        from pyrite.services.embedding_service import is_available

        config = _config(tmp_path, indexed=False)
        db = PyriteDB(config.settings.index_path)
        vec = db.vec_available
        db.close()
        if not (is_available() and vec):
            pytest.skip("semantic extras not installed: the empty-index check is not reached")
        from pyrite.cli import app

        with _patched(config):
            result = runner.invoke(app, ["index", "embed"])
        assert result.exit_code != 0
        assert "INDEX_EMPTY" in result.output or "Index is empty" in result.output


class TestAdminCli:
    """`pyrite-admin` (admin_cli.py) index and auth commands."""

    def test_index_build_stats_sync_health(self, tmp_path):
        from pyrite.admin_cli import app

        config = _config(tmp_path, indexed=False)
        with _patched(config):
            build = runner.invoke(app, ["index", "build"])
            stats = runner.invoke(app, ["index", "stats"])
            sync = runner.invoke(app, ["index", "sync"])
            health = runner.invoke(app, ["index", "health"])
            one = runner.invoke(app, ["index", "build", "notes"])
        assert build.exit_code == 0, build.output
        assert "Indexed 2 entries across 1 KBs" in build.output
        assert "Total entries:" in stats.output and "2" in stats.output
        assert sync.exit_code == 0 and "Synced:" in sync.output
        assert health.exit_code == 0 and "Index is healthy." in health.output
        assert "Indexed 2 entries from notes" in one.output

    def test_auth_whoami_local(self, config):
        from pyrite.admin_cli import app

        with _patched(config):
            result = runner.invoke(app, ["auth", "whoami"])
        assert result.exit_code == 0, result.output
        assert "Identity: local" in result.output

    def test_repo_sync_unknown(self, config):
        from pyrite.admin_cli import app

        with _patched(config):
            result = runner.invoke(app, ["repo", "sync", "org/none"])
        # `sync` answers {"success": False} for a repo it has none of; that is now an exit 1 (#526).
        assert result.exit_code == 1, result.output
        assert "org/none" in result.output or "no repos found" in result.output.lower()


def test_patched_restores_the_real_load_config_even_when_admin_cli_is_unimported():
    """#510: _patched() must leave every patched name as the real function.

    ``patch("pyrite.admin_cli.load_config", ...)`` resolves its target by
    importing ``pyrite.admin_cli`` if it is not already in ``sys.modules``.
    If that import happens after ``pyrite.config.load_config`` has already
    been replaced by a Mock (as ``_patched`` does, patching
    ``pyrite.config.load_config`` first), ``admin_cli``'s
    ``from .config import load_config`` binds that Mock. ``patch`` then
    records the Mock as the attribute to restore, so exiting the context
    leaves ``pyrite.admin_cli.load_config`` a Mock forever -- for every test
    that runs afterward in the same worker.

    This must run in a fresh subprocess rather than by evicting
    ``pyrite.admin_cli`` from this process's ``sys.modules``: eviction plus
    re-import leaves a *second* module object in ``sys.modules`` while every
    other already-imported module (and the Typer ``app`` under test) still
    holds a reference to the *original* ``pyrite.admin_cli`` module object
    through its own globals. Asserting against the freshly re-imported
    object would prove nothing about the object the code under test
    actually reads -- the same class of import-identity bug this test
    exists to catch, one layer up (we've been bitten before by
    ``importlib.reload`` breaking ``isinstance`` checks elsewhere). A
    subprocess that imports nothing first sidesteps module-identity
    questions entirely: there is only ever one ``pyrite.admin_cli`` object.
    """
    script = (
        "from tests.test_cli_surfaces_through_services import _config, _patched\n"
        "import tempfile, pathlib\n"
        "with tempfile.TemporaryDirectory() as tmpdir:\n"
        "    config = _config(pathlib.Path(tmpdir))\n"
        "    with _patched(config):\n"
        "        pass\n"
        # Import admin_cli only *after* _patched() has exited -- importing
        # it earlier would make it already-present in sys.modules, which
        # is the safe case _patched()'s own pre-import guard also relies
        # on, and this check would then pass for the wrong reason.
        "import pyrite.admin_cli as admin_cli_module\n"
        "import pyrite.config as config_module\n"
        "assert admin_cli_module.load_config is config_module.load_config, (\n"
        "    admin_cli_module.load_config\n"
        ")\n"
        "assert 'return_value' not in repr(admin_cli_module.load_config)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parent.parent,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
