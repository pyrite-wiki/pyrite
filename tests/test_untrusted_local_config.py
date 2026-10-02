"""A repo-local `.pyrite/config.yaml` is untrusted.

Property: configuration that comes from content the user did not create
cannot change what code runs, or where Pyrite reads and writes outside that
content.

A `.pyrite/config.yaml` found by walking up from the cwd may belong to a
cloned or downloaded tree. Pyrite still uses it -- the ADR-0032 worktree flow
depends on it -- but only for keys that stay inside that tree: the KB
registry (KB paths inside the tree), `index_path`/`workspace_path` inside the
tree, and a few harmless switches. Every other key is ignored, with a
warning. `~/.pyrite` and an explicit `PYRITE_CONFIG_DIR`/`PYRITE_DATA_DIR`
stay trusted: that is how a user opts a directory in.

Separately, the embedding model loader never runs code shipped with a model,
and never takes a bare model name as a path relative to the cwd.
"""

import logging
import os
import re
import sys
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

import pyrite.config as config_module

REPO_ROOT = Path(__file__).resolve().parent.parent
MARKER_MODULE = "pyrite_untrusted_marker_mod"


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A HOME with its own ~/.pyrite, and an untrusted tree beside it with a
    `.pyrite/config.yaml`, a KB inside it, and the cwd inside it."""
    home = tmp_path / "home"
    (home / ".pyrite").mkdir(parents=True)
    (home / ".pyrite" / "config.yaml").write_text("knowledge_bases: []\n")
    tree = tmp_path / "downloaded"
    (tree / ".pyrite").mkdir(parents=True)
    (tree / "notes").mkdir()
    (tree / "notes" / "hello.md").write_text(
        "---\nid: hello\ntitle: Hello\ntype: note\n---\n\nSome body text.\n"
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("PYRITE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("PYRITE_DATA_DIR", raising=False)
    # The conftest isolation fixture pins CONFIG_DIR to a tmp dir; point it
    # back at the default location so the cwd lookup is what decides.
    monkeypatch.setattr(config_module, "CONFIG_DIR", home / ".pyrite")
    monkeypatch.setattr(config_module, "CONFIG_FILE", home / ".pyrite" / "config.yaml")
    monkeypatch.setattr(config_module, "_UNTRUSTED_IMPORT_DIR", None, raising=False)
    monkeypatch.chdir(tree)
    return home, tree.resolve()


def _write(tree: Path, data: dict) -> None:
    (tree / ".pyrite" / "config.yaml").write_text(yaml.safe_dump(data))


def _inside(path: Path, root: Path) -> bool:
    return Path(path).resolve().is_relative_to(root)


def _evil_model_dir(tree: Path, marker: Path) -> Path:
    """A model directory whose modules.json names a module shipped in it."""
    model = tree / "evil-model"
    model.mkdir()
    (model / f"{MARKER_MODULE}.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('imported')\n"
        "class Evil:\n    pass\n"
    )
    (model / "modules.json").write_text(
        f'[{{"idx": 0, "name": "0", "path": "", "type": "{MARKER_MODULE}.Evil"}}]'
    )
    (model / "config.json").write_text("{}")
    return model


# ── 1. index sync never loads a model the untrusted tree names ───────────


class TestIndexSyncEmbeddingModel:
    def test_untrusted_embedding_model_path_never_reaches_the_loader(
        self, world, monkeypatch, tmp_path
    ):
        from pyrite.cli import app
        from pyrite.services import embedding_service

        home, tree = world
        marker = tmp_path / "marker.txt"
        model = _evil_model_dir(tree, marker)
        _write(
            tree,
            {
                "knowledge_bases": [
                    {"name": "notes", "path": str(tree / "notes"), "kb_type": "generic"}
                ],
                "settings": {"embedding_model": str(model), "auto_embed": True},
            },
        )

        constructed: list[str] = []
        loaded: list[str] = []
        real_init = embedding_service.EmbeddingService.__init__

        def spy_init(self, db, model_name="all-MiniLM-L6-v2", *a, **k):
            constructed.append(model_name)
            real_init(self, db, model_name, *a, **k)

        def refuse_load(name):
            loaded.append(name)
            raise RuntimeError("test: no model loading")

        monkeypatch.setattr(embedding_service.EmbeddingService, "__init__", spy_init)
        monkeypatch.setattr(embedding_service, "_load_model", refuse_load)
        monkeypatch.setattr(embedding_service, "is_available", lambda: True)

        result = CliRunner().invoke(app, ["index", "sync"])
        assert result.exit_code == 0, result.output

        # The embedding step ran (the regime this test is about) ...
        assert constructed, "index sync never reached the embedding step"
        # ... with a model name that does not point into the untrusted tree.
        for name in constructed + loaded:
            assert name == "all-MiniLM-L6-v2", name
        assert not marker.exists()
        assert MARKER_MODULE not in sys.modules


# ── 2. untrusted config cannot move paths or change server/auth keys ─────


class TestUntrustedKeysAreRefused:
    def test_paths_outside_the_tree_and_security_keys_are_ignored(self, world, tmp_path, caplog):
        home, tree = world
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        _write(
            tree,
            {
                "knowledge_bases": [
                    {"name": "inside", "path": str(tree / "notes"), "kb_type": "generic"},
                    {"name": "outside", "path": str(outside), "kb_type": "generic"},
                ],
                "settings": {
                    "index_path": str(outside / "index.db"),
                    "workspace_path": str(outside / "repos"),
                    "host": "0.0.0.0",
                    "api_key": "planted-key",
                    "api_keys": [{"key_hash": "x", "role": "admin"}],
                    "auth": {"enabled": True, "anonymous_tier": "write"},
                    "embedding_model": str(tree / "evil-model"),
                    "ai_provider": "openai",
                    "default_editor": "planted-editor",
                    "cors_origins": ["https://example.invalid"],
                },
            },
        )
        with caplog.at_level(logging.WARNING, logger="pyrite.config"):
            cfg = config_module.load_config()

        assert [kb.name for kb in cfg.knowledge_bases] == ["inside"]
        s = cfg.settings
        assert _inside(s.index_path, tree)
        assert _inside(s.workspace_path, tree)
        assert s.host == "127.0.0.1"
        assert s.api_key == ""
        assert s.api_keys == []
        assert s.auth.enabled is False
        assert s.auth.anonymous_tier is None
        assert s.embedding_model == "all-MiniLM-L6-v2"
        assert s.ai_provider == "stub"
        assert s.default_editor != "planted-editor"
        assert "https://example.invalid" not in s.cors_origins
        warned = caplog.text
        for key in ("host", "api_key", "auth", "embedding_model", "outside"):
            assert key in warned, key

    def test_repositories_subscriptions_and_kb_remotes_are_ignored(self, world, tmp_path, caplog):
        home, tree = world
        _write(
            tree,
            {
                "knowledge_bases": [
                    {
                        "name": "inside",
                        "path": str(tree / "notes"),
                        "remote": "https://example.invalid/r.git",
                        "repo": "planted-repo",
                        "ephemeral": True,
                        "ttl": 5,
                    }
                ],
                "repositories": [{"name": "planted-repo", "path": str(tmp_path / "r")}],
                "subscriptions": [
                    {"url": "https://example.invalid/s.git", "local_path": str(tmp_path / "s")}
                ],
            },
        )
        with caplog.at_level(logging.WARNING, logger="pyrite.config"):
            cfg = config_module.load_config()
        assert cfg.repositories == [] and cfg.subscriptions == []
        (kb,) = cfg.knowledge_bases
        assert (kb.remote, kb.repo, kb.ephemeral, kb.ttl) == (None, None, False, None)
        for key in ("repositories", "subscriptions", "remote"):
            assert key in caplog.text, key

    def test_a_kb_path_that_escapes_through_a_symlink_is_refused(self, world, tmp_path):
        home, tree = world
        outside = tmp_path / "private"
        outside.mkdir()
        (tree / "link").symlink_to(outside)
        _write(
            tree,
            {
                "knowledge_bases": [{"name": "sneaky", "path": str(tree / "link")}],
                "settings": {"index_path": str(tree / "link" / "index.db")},
            },
        )
        cfg = config_module.load_config()
        assert cfg.knowledge_bases == []
        assert _inside(cfg.settings.index_path, tree)
        assert not _inside(cfg.settings.index_path, outside.resolve())

    def test_a_relative_kb_path_that_climbs_out_is_refused(self, world):
        home, tree = world
        _write(tree, {"knowledge_bases": [{"name": "up", "path": "../../"}]})
        assert config_module.load_config().knowledge_bases == []

    def test_secrets_are_not_read_from_the_untrusted_tree(self, world, monkeypatch):
        """A process started inside the tree resolves CONFIG_DIR to it at
        import; its GitHub credentials file still comes from ~/.pyrite."""
        from pyrite import github_auth

        home, tree = world
        monkeypatch.setattr(config_module, "CONFIG_DIR", tree / ".pyrite")
        monkeypatch.setattr(config_module, "CONFIG_FILE", tree / ".pyrite" / "config.yaml")
        monkeypatch.setattr(config_module, "_UNTRUSTED_IMPORT_DIR", tree / ".pyrite")
        _write(tree, {"knowledge_bases": []})
        (tree / ".pyrite" / "github_auth.yaml").write_text(
            yaml.safe_dump({"access_token": "planted-token", "token_type": "bearer"})
        )
        cfg = config_module.load_config()
        assert cfg.github_auth is None
        assert not _inside(github_auth.get_auth_file_path(), tree)

    def test_process_started_inside_the_tree_is_untrusted_too(self, world, monkeypatch):
        """CONFIG_DIR resolved at import from the cwd is the untrusted tree."""
        home, tree = world
        monkeypatch.setattr(config_module, "CONFIG_DIR", tree / ".pyrite")
        monkeypatch.setattr(config_module, "CONFIG_FILE", tree / ".pyrite" / "config.yaml")
        monkeypatch.setattr(config_module, "_UNTRUSTED_IMPORT_DIR", tree / ".pyrite")
        _write(tree, {"settings": {"host": "0.0.0.0", "embedding_model": "/x/y"}})
        cfg = config_module.load_config()
        assert cfg.settings.host == "127.0.0.1"
        assert cfg.settings.embedding_model == "all-MiniLM-L6-v2"


# ── 3. the ADR-0032 worktree flow keeps working ──────────────────────────


def _worktree_config(wt_dir: Path, branch: str) -> str:
    """The config scripts/setup-checkout.sh writes (for new-worktree.sh and a
    cloud session alike), rendered for `wt_dir`."""
    script = (REPO_ROOT / "scripts" / "setup-checkout.sh").read_text()
    body = re.search(r"cat > \.pyrite/config\.yaml <<CFG\n(.*?)\nCFG\n", script, re.S)
    assert body, "setup-checkout.sh no longer writes .pyrite/config.yaml with a heredoc"
    return body.group(1).replace("$wt_dir", str(wt_dir)).replace("${label:-detached}", branch)


class TestWorktreeFlow:
    @pytest.mark.control(reason="the worktree config loaded on dev too; pins that it still does")
    def test_the_config_new_worktree_writes_loads_whole_and_quietly(self, world, caplog):
        home, tree = world
        (tree / "kb").mkdir()
        (tree / ".pyrite" / "config.yaml").write_text(_worktree_config(tree, "fix/x"))
        with caplog.at_level(logging.WARNING, logger="pyrite.config"):
            cfg = config_module.load_config()
        assert [kb.name for kb in cfg.knowledge_bases] == ["pyrite"]
        assert os.path.samefile(cfg.knowledge_bases[0].path, tree / "kb")
        assert cfg.settings.index_path == (tree / ".pyrite" / "index.db")
        assert cfg.settings.auto_embed is False
        assert "untrusted" not in caplog.text

    @pytest.mark.control(reason="dev never warned; pins that saved defaults are not noise")
    def test_a_config_saved_back_by_pyrite_reloads_quietly(self, world, caplog):
        """`pyrite kb add` in a worktree rewrites the file with every default;
        defaults are not the tree changing anything, so no warning."""
        home, tree = world
        (tree / "kb").mkdir()
        (tree / ".pyrite" / "config.yaml").write_text(_worktree_config(tree, "fix/x"))
        config_module.save_config(config_module.load_config())
        with caplog.at_level(logging.WARNING, logger="pyrite.config"):
            cfg = config_module.load_config()
        assert [kb.name for kb in cfg.knowledge_bases] == ["pyrite"]
        assert "untrusted" not in caplog.text

    @pytest.mark.control(reason="explicit PYRITE_CONFIG_DIR was always honoured in full")
    def test_an_explicit_config_dir_is_trusted_in_full(self, world, monkeypatch):
        home, tree = world
        _write(tree, {"settings": {"host": "0.0.0.0", "embedding_model": "/models/mine"}})
        monkeypatch.setenv("PYRITE_CONFIG_DIR", str(tree / ".pyrite"))
        monkeypatch.setattr(config_module, "CONFIG_DIR", tree / ".pyrite")
        monkeypatch.setattr(config_module, "CONFIG_FILE", tree / ".pyrite" / "config.yaml")
        cfg = config_module.load_config()
        assert cfg.settings.host == "0.0.0.0"
        assert cfg.settings.embedding_model == "/models/mine"

    @pytest.mark.control(reason="~/.pyrite was always trusted")
    def test_home_config_is_trusted_in_full(self, world, monkeypatch, tmp_path):
        home, tree = world
        elsewhere = tmp_path / "plain"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        (home / ".pyrite" / "config.yaml").write_text(
            yaml.safe_dump({"settings": {"host": "0.0.0.0"}})
        )
        assert config_module.load_config().settings.host == "0.0.0.0"


# ── the model loader itself ──────────────────────────────────────────────


class TestModelLoader:
    @pytest.fixture
    def fake_sentence_transformer(self, monkeypatch):
        """Record what reaches sentence-transformers instead of loading it."""
        sentence_transformers = pytest.importorskip("sentence_transformers")
        calls: list[str] = []

        class FakeModel:
            def __init__(self, name, **kwargs):
                calls.append(name)

        monkeypatch.setattr(sentence_transformers, "SentenceTransformer", FakeModel)
        return calls

    def test_a_bare_name_that_is_also_a_directory_under_the_cwd_is_refused(
        self, tmp_path, monkeypatch, fake_sentence_transformer
    ):
        from pyrite.services import embedding_service

        marker = tmp_path / "marker.txt"
        monkeypatch.chdir(tmp_path)
        shadow = _evil_model_dir(tmp_path, marker)
        shadow.rename(tmp_path / "all-MiniLM-L6-v2")
        with pytest.raises(RuntimeError):
            embedding_service._load_model("all-MiniLM-L6-v2")
        assert fake_sentence_transformer == []
        assert not marker.exists()
        assert MARKER_MODULE not in sys.modules

    def test_a_model_directory_that_ships_code_is_refused(
        self, tmp_path, fake_sentence_transformer
    ):
        """Holds whatever sentence-transformers version is installed: before
        6.0 it imported a local model's modules without asking."""
        from pyrite.services import embedding_service

        marker = tmp_path / "marker.txt"
        model = _evil_model_dir(tmp_path, marker)
        with pytest.raises(RuntimeError):
            embedding_service._load_model(str(model))
        assert fake_sentence_transformer == []
        assert not marker.exists()
        assert MARKER_MODULE not in sys.modules

    def test_the_loader_never_enables_custom_code(self, monkeypatch):
        sentence_transformers = pytest.importorskip("sentence_transformers")
        from pyrite.services import embedding_service

        seen: list[dict] = []

        class FakeModel:
            def __init__(self, name, **kwargs):
                seen.append({"name": name, **kwargs})

        monkeypatch.setattr(sentence_transformers, "SentenceTransformer", FakeModel)
        embedding_service._load_model("all-MiniLM-L6-v2")
        assert seen and seen[0].get("trust_remote_code") is False


# ── round 2: the index registry, saving, and exposure settings ──────────


class TestUntrustedRegistryAndSave:
    def test_db_registered_kbs_outside_the_tree_are_not_loaded(self, world, tmp_path, caplog):
        """The tree's own index.db can list KBs too; they are confined the
        same way as the yaml ones."""
        from pyrite.storage.database import PyriteDB

        home, tree = world
        outside = tmp_path / "elsewhere-kb"
        outside.mkdir()
        (tree / "inner").mkdir()
        _write(tree, {"knowledge_bases": []})
        cfg = config_module.load_config()
        assert _inside(cfg.settings.index_path, tree)
        with PyriteDB(cfg.settings.index_path) as db:
            db.register_kb("far", "generic", str(outside), default_role="read")
            db.register_kb("near", "generic", str(tree / "inner"))
            with caplog.at_level(logging.WARNING, logger="pyrite.config"):
                db.merge_registered_kbs(cfg)
        names = {kb.name for kb in cfg.all_kbs()}
        assert "far" not in names and cfg.get_kb("far") is None
        assert "near" in names
        assert "far" in caplog.text

    @pytest.mark.control(reason="a trusted config merged registry KBs anywhere on dev too")
    def test_a_trusted_config_still_merges_registry_kbs_anywhere(
        self, world, tmp_path, monkeypatch
    ):
        from pyrite.storage.database import PyriteDB

        home, tree = world
        elsewhere = tmp_path / "plain"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        outside = tmp_path / "far-kb"
        outside.mkdir()
        cfg = config_module.load_config()
        with PyriteDB(tmp_path / "trusted.db") as db:
            db.register_kb("far", "generic", str(outside))
            db.merge_registered_kbs(cfg)
        assert cfg.get_kb("far") is not None

    def test_saving_an_untrusted_config_writes_only_allowlisted_keys(self, world, monkeypatch):
        home, tree = world
        (tree / "kb").mkdir()
        (tree / ".pyrite" / "config.yaml").write_text(_worktree_config(tree, "fix/x"))
        monkeypatch.setenv("PYRITE_API_KEY", "planted-env-key")
        monkeypatch.setenv("PYRITE_AI_PROVIDER", "openai")
        monkeypatch.setenv("PYRITE_HOST", "0.0.0.0")
        monkeypatch.setenv("PYRITE_AUTO_EMBED", "true")
        cfg = config_module.load_config()
        config_module.save_config(cfg)
        text = (tree / ".pyrite" / "config.yaml").read_text()
        assert "planted-env-key" not in text
        saved = yaml.safe_load(text)
        assert set(saved) <= {"version", "knowledge_bases", "settings"}
        assert set(saved.get("settings") or {}) <= {
            "index_path",
            "auto_embed",
            "search_mode",
            "summary_length",
        }
        # The file's own allowlisted values survive; the environment's do not.
        assert saved["settings"]["auto_embed"] is False
        assert [kb["name"] for kb in saved["knowledge_bases"]] == ["pyrite"]

    def test_default_role_from_an_untrusted_config_can_only_close_a_kb(self, world):
        home, tree = world
        (tree / "a").mkdir()
        (tree / "b").mkdir()
        _write(
            tree,
            {
                "knowledge_bases": [
                    {"name": "opened", "path": str(tree / "a"), "default_role": "write"},
                    {"name": "closed", "path": str(tree / "b"), "default_role": "none"},
                ]
            },
        )
        cfg = config_module.load_config()
        assert cfg.get_kb("opened").default_role is None
        assert cfg.get_kb("closed").default_role == "none"


# ── round 3: default_role shipped in the tree's index, and kb add ────────


def _shipped_index(tree: Path, cfg, *, default_role="read"):
    """The tree's own index.db, carrying a KB row whose default_role came
    with the content."""
    from pyrite.storage.database import PyriteDB

    (tree / "shipped").mkdir(exist_ok=True)
    with PyriteDB(cfg.settings.index_path) as db:
        db.register_kb("shipped", "generic", str(tree / "shipped"), default_role=default_role)


class TestShippedDefaultRole:
    def test_the_resolver_ignores_a_default_role_shipped_in_the_index(self, world):
        from pyrite.server.api import resolve_kb_default_role
        from pyrite.storage.database import PyriteDB

        home, tree = world
        _write(tree, {"knowledge_bases": []})
        cfg = config_module.load_config()
        _shipped_index(tree, cfg)
        with PyriteDB(cfg.settings.index_path) as db:
            db.merge_registered_kbs(cfg)
            assert resolve_kb_default_role(cfg, db, "shipped") is None

    @pytest.mark.control(reason="a shipped 'none' could always only close a KB")
    def test_a_shipped_none_still_closes_the_kb(self, world):
        from pyrite.server.api import resolve_kb_default_role
        from pyrite.storage.database import PyriteDB

        home, tree = world
        _write(tree, {"knowledge_bases": []})
        cfg = config_module.load_config()
        _shipped_index(tree, cfg, default_role="none")
        with PyriteDB(cfg.settings.index_path) as db:
            assert resolve_kb_default_role(cfg, db, "shipped") == "none"

    def test_a_self_registered_user_does_not_see_a_kb_the_index_made_public(
        self, world, monkeypatch
    ):
        from fastapi.testclient import TestClient

        from pyrite.server.api import create_app
        from pyrite.storage.database import PyriteDB
        from tests.auth_seed import seed_user

        home, tree = world
        _write(tree, {"knowledge_bases": []})
        monkeypatch.setenv("PYRITE_AUTH_ENABLED", "true")  # the operator's own switch
        cfg = config_module.load_config()
        assert cfg.settings.auth.enabled
        _shipped_index(tree, cfg)
        with PyriteDB(cfg.settings.index_path) as db:
            seed_user(db, "root", role="admin")
        app = create_app(config=cfg)
        client = TestClient(app)
        r = client.post("/auth/register", json={"username": "stranger", "password": "password123"})
        assert r.status_code == 200, r.text
        r = client.get("/api/kbs")
        assert r.status_code == 200, r.text
        assert "shipped" not in {kb["name"] for kb in r.json()["kbs"]}
        state_db = getattr(app.state, "pyrite_db", None)
        if state_db is not None:
            state_db.close()

    def test_an_admin_cannot_publish_a_kb_under_an_untrusted_config(self, world):
        from pyrite.exceptions import ConfigError
        from pyrite.services.kb_registry_service import KBRegistryService
        from pyrite.storage.database import PyriteDB
        from pyrite.storage.index import IndexManager

        home, tree = world
        _write(tree, {"knowledge_bases": []})
        cfg = config_module.load_config()
        _shipped_index(tree, cfg, default_role=None)
        with PyriteDB(cfg.settings.index_path) as db:
            registry = KBRegistryService(cfg, db, IndexManager(db, cfg))
            with pytest.raises(ConfigError, match="trusted"):
                registry.update_kb("shipped", default_role="read")
            registry.update_kb("shipped", default_role="none")  # closing is allowed
            assert db.execute_sql("SELECT default_role FROM kb WHERE name = 'shipped'") == [
                {"default_role": "none"}
            ]


class TestKbAddUnderAnUntrustedConfig:
    def test_an_out_of_tree_path_is_refused_before_any_side_effect(self, world, tmp_path):
        from pyrite.exceptions import ConfigError
        from pyrite.services.kb_registry_service import KBRegistryService
        from pyrite.storage.database import PyriteDB
        from pyrite.storage.index import IndexManager

        home, tree = world
        _write(tree, {"knowledge_bases": []})
        cfg = config_module.load_config()
        target = tmp_path / "outside" / "new-kb"
        with PyriteDB(cfg.settings.index_path) as db:
            registry = KBRegistryService(cfg, db, IndexManager(db, cfg))
            with pytest.raises(ConfigError, match="outside"):
                registry.add_kb("far", str(target), kb_type="generic")
            assert db.execute_sql("SELECT name FROM kb WHERE name = 'far'") == []
        assert not target.exists()
        assert not target.parent.exists()

    @pytest.mark.control(reason="an in-tree kb add always worked")
    def test_an_in_tree_path_is_still_added(self, world):
        from pyrite.services.kb_registry_service import KBRegistryService
        from pyrite.storage.database import PyriteDB
        from pyrite.storage.index import IndexManager

        home, tree = world
        _write(tree, {"knowledge_bases": []})
        cfg = config_module.load_config()
        with PyriteDB(cfg.settings.index_path) as db:
            KBRegistryService(cfg, db, IndexManager(db, cfg)).add_kb("near", str(tree / "near"))
        assert (tree / "near").is_dir()


# ── round 4: every index row -> KBConfig path is confined ────────────────


def _far_row(tmp_path: Path, cfg) -> Path:
    """An index row naming a KB outside the tree, with a note in it."""
    from pyrite.storage.database import PyriteDB

    far = tmp_path / "far-away-kb"
    far.mkdir()
    (far / "secret.md").write_text("---\nid: secret\ntitle: Secret\ntype: note\n---\n\nFar.\n")
    with PyriteDB(cfg.settings.index_path) as db:
        db.register_kb("far", "generic", str(far), default_role="read")
    return far


def _registry(cfg, db):
    from pyrite.services.kb_registry_service import KBRegistryService
    from pyrite.storage.index import IndexManager

    return KBRegistryService(cfg, db, IndexManager(db, cfg))


class TestRegistryRowsAreConfinedEverywhere:
    def test_reindex_of_an_out_of_tree_row_is_not_found_and_indexes_nothing(self, world, tmp_path):
        from pyrite.exceptions import KBNotFoundError
        from pyrite.storage.database import PyriteDB

        home, tree = world
        _write(tree, {"knowledge_bases": []})
        cfg = config_module.load_config()
        _far_row(tmp_path, cfg)
        with PyriteDB(cfg.settings.index_path) as db:
            registry = _registry(cfg, db)
            assert registry.get_kb_config("far") is None
            with pytest.raises(KBNotFoundError):
                registry.reindex_kb("far")
            assert db.execute_sql("SELECT COUNT(*) AS n FROM entry WHERE kb_name = 'far'") == [
                {"n": 0}
            ]

    def test_kb_service_does_not_resolve_an_out_of_tree_row(self, world, tmp_path):
        from pyrite.services.kb_service import KBService
        from pyrite.storage.database import PyriteDB

        home, tree = world
        _write(tree, {"knowledge_bases": []})
        cfg = config_module.load_config()
        _far_row(tmp_path, cfg)
        with PyriteDB(cfg.settings.index_path) as db:
            svc = KBService(cfg, db)
            svc._registry = _registry(cfg, db)
            assert svc.get_kb("far") is None

    def test_an_in_tree_row_keeps_only_a_closing_default_role(self, world):
        from pyrite.storage.database import PyriteDB

        home, tree = world
        _write(tree, {"knowledge_bases": []})
        cfg = config_module.load_config()
        (tree / "near").mkdir()
        with PyriteDB(cfg.settings.index_path) as db:
            db.register_kb("near", "generic", str(tree / "near"), default_role="write")
            kb = _registry(cfg, db).get_kb_config("near")
        assert kb is not None and kb.default_role is None

    @pytest.mark.control(reason="a trusted config always reindexed registry KBs anywhere")
    def test_a_trusted_config_still_reindexes_a_registry_kb_anywhere(
        self, world, tmp_path, monkeypatch
    ):
        from pyrite.storage.database import PyriteDB

        home, tree = world
        elsewhere = tmp_path / "plain"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        cfg = config_module.load_config()
        cfg.settings.index_path = tmp_path / "trusted.db"
        _far_row(tmp_path, cfg)
        with PyriteDB(cfg.settings.index_path) as db:
            registry = _registry(cfg, db)
            assert registry.get_kb_config("far").default_role == "read"
            registry.reindex_kb("far")
            assert db.execute_sql("SELECT COUNT(*) AS n FROM entry WHERE kb_name = 'far'") == [
                {"n": 1}
            ]


@pytest.mark.control(reason="the refusal landed in the previous round; this pins it over HTTP")
def test_publishing_under_an_untrusted_config_is_409_over_http(world):
    from fastapi.testclient import TestClient

    from pyrite.server.api import create_app
    from pyrite.storage.database import PyriteDB

    home, tree = world
    _write(tree, {"knowledge_bases": []})
    cfg = config_module.load_config()
    _shipped_index(tree, cfg, default_role=None)
    app = create_app(config=cfg)  # auth off: the local caller is admin
    client = TestClient(app)
    r = client.put("/api/kbs/shipped/default-role", json={"role": "read"})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "CONFIG_CONFLICT"
    with PyriteDB(cfg.settings.index_path) as db:
        assert db.execute_sql("SELECT default_role FROM kb WHERE name = 'shipped'") == [
            {"default_role": None}
        ]
    state_db = getattr(app.state, "pyrite_db", None)
    if state_db is not None:
        state_db.close()
