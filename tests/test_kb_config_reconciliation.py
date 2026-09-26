"""Reconcile config-owned registry rows when a KB leaves config.yaml."""

import pytest

from pyrite.config import PyriteConfig, save_config
from pyrite.exceptions import KBProtectedError
from pyrite.services.kb_registry_service import KBRegistryService
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager


def make_registry(config, db):
    return KBRegistryService(config, db, IndexManager(db, config))


def test_removed_config_kb_becomes_removable(pyrite_config, pyrite_db):
    registry = make_registry(pyrite_config, pyrite_db)
    registry.seed_from_config()
    name = pyrite_config.knowledge_bases[0].name

    # A new process has read config.yaml after its last KB was removed.
    new_config = PyriteConfig(settings=pyrite_config.settings)
    registry = make_registry(new_config, pyrite_db)
    assert registry.seed_from_config() == 0
    assert registry.get_kb(name)["source"] == "user"
    assert registry.remove_kb(name) is True
    assert registry.get_kb(name) is None


def test_reconciliation_preserves_data_and_persists(indexed_test_env):
    config = indexed_test_env["config"]
    db = indexed_test_env["db"]
    registry = make_registry(config, db)
    registry.seed_from_config()
    name = indexed_test_env["events_kb"].name
    db.update_kb_default_role(name, "none")
    before = registry.get_kb(name)
    stats_before = db.get_kb_stats(name)
    assert stats_before["actual_count"] > 0

    new_config = PyriteConfig(
        knowledge_bases=[indexed_test_env["research_kb"]], settings=config.settings
    )
    assert make_registry(new_config, db).seed_from_config() == 1

    # A separately opened DB must observe the committed demotion, with all
    # indexed entries, metadata and the access-control default intact.
    reopened = PyriteDB(config.settings.index_path)
    try:
        registry = make_registry(new_config, reopened)
        # No longer in config.yaml: user-managed, so its default_role is
        # editable through the API now.
        assert before["default_role_editable"] is False
        assert registry.get_kb(name) == {
            **before,
            "source": "user",
            "default_role_editable": True,
        }
        assert reopened.get_kb_stats(name) == {**stats_before, "source": "user"}
        assert reopened.merge_registered_kbs(new_config) == 1
        assert new_config.get_kb(name).default_role == "none"
    finally:
        reopened.close()


def test_reconciliation_keeps_current_config_and_user_kbs(pyrite_config, pyrite_db, tmp_path):
    registry = make_registry(pyrite_config, pyrite_db)
    registry.seed_from_config()
    removed, retained = pyrite_config.knowledge_bases
    user = registry.add_kb("user-kb", str(tmp_path / "user-kb"))
    new_config = PyriteConfig(knowledge_bases=[retained], settings=pyrite_config.settings)
    registry = make_registry(new_config, pyrite_db)

    assert registry.seed_from_config() == 1
    assert registry.seed_from_config() == 1
    assert registry.get_kb(removed.name)["source"] == "user"
    assert registry.get_kb(retained.name)["source"] == "config"
    assert registry.get_kb("user-kb") == user
    with pytest.raises(KBProtectedError, match="config.yaml"):
        registry.remove_kb(retained.name)


def test_reintroduced_config_kb_is_protected_again(pyrite_config, pyrite_db):
    registry = make_registry(pyrite_config, pyrite_db)
    registry.seed_from_config()
    name = pyrite_config.knowledge_bases[0].name
    make_registry(PyriteConfig(settings=pyrite_config.settings), pyrite_db).seed_from_config()
    assert registry.get_kb(name)["source"] == "user"

    registry.seed_from_config()
    assert registry.get_kb(name)["source"] == "config"
    with pytest.raises(KBProtectedError, match="config.yaml"):
        registry.remove_kb(name)


def test_reconciliation_updates_config_in_current_process(pyrite_config, pyrite_db):
    make_registry(pyrite_config, pyrite_db).seed_from_config()
    removed = pyrite_config.knowledge_bases[0]
    new_config = PyriteConfig(settings=pyrite_config.settings)

    # CLI, REST and MCP merge user KBs before the registry is seeded.
    assert pyrite_db.merge_registered_kbs(new_config) == 0
    assert new_config.get_kb(removed.name) is None
    make_registry(new_config, pyrite_db).seed_from_config()

    assert new_config.get_kb(removed.name).path == removed.path
    assert removed.name in {kb.name for kb in new_config.all_kbs()}


def test_cli_remove_after_editing_config_yaml(pyrite_config):
    from typer.testing import CliRunner

    from pyrite.cli import app

    runner = CliRunner()
    kb = pyrite_config.knowledge_bases[0]
    content_file = kb.path / "keep.md"
    content_file.write_text("Keep this file.\n", encoding="utf-8")
    save_config(pyrite_config)
    listed = runner.invoke(app, ["kb", "list"])
    assert listed.exit_code == 0, listed.output

    # The user hand-edits config.yaml down to no KBs: an intended empty write.
    save_config(PyriteConfig(settings=pyrite_config.settings), allow_drop=True)
    listed = runner.invoke(app, ["kb", "list"])
    assert listed.exit_code == 0, listed.output
    removed = runner.invoke(app, ["kb", "remove", kb.name, "--force"])
    assert removed.exit_code == 0, removed.output
    assert "Removed:" in removed.output
    assert content_file.read_text(encoding="utf-8") == "Keep this file.\n"
