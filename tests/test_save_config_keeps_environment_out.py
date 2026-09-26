"""A config save never writes a value the environment supplied (private #73).

`save_config` serialised the whole in-memory config, environment overrides
included: `PYRITE_API_KEY`, an OAuth client secret, the port, a data-dir
index path all landed in config.yaml the first time anything saved -- an
ephemeral KB, a repo subscription, or an admin's default-role change on one.

The property: a save writes the file's own content plus the change
requested, for every environment-overridable setting.
"""

from __future__ import annotations

import pytest

from pyrite import config as config_module
from pyrite.config import KBConfig
from pyrite.utils.yaml import dump_yaml_file, load_yaml_file

FILE_SETTINGS = {"host": "file-host.example"}

# env var -> value. Distinctive strings, so their absence from the file is
# checkable as text as well as by the settings mapping.
ENVIRONMENT = {
    "PYRITE_HOST": "env-host.example",
    "PYRITE_PORT": "48123",
    "PORT": "48124",
    "PYRITE_AUTH_ENABLED": "true",
    "PYRITE_AUTH_ANONYMOUS_TIER": "read",
    "PYRITE_AUTH_ALLOW_REGISTRATION": "true",
    "PYRITE_AUTH_LOGIN_RATE_LIMIT": "7/minute",
    "PYRITE_AUTH_LOGIN_RATE_LIMIT_PER_USERNAME": "3/minute",
    "PYRITE_AUTH_REGISTER_RATE_LIMIT": "2/minute",
    "PYRITE_CORS_ORIGINS": "https://env-origin.example",
    "PYRITE_ALLOWED_HOSTS": "env-allowed.example",
    "PYRITE_API_KEY": "env-secret-api-key-0f3a",
    "PYRITE_AI_PROVIDER": "env-provider",
    "PYRITE_AI_MODEL": "env-model-9",
    "PYRITE_SEARCH_MODE": "semantic",
    "PYRITE_STRICT_PLUGINS": "true",
    "PYRITE_PREWARM_EMBEDDINGS": "true",
    "PYRITE_AUTO_EMBED": "false",
    "PYRITE_SITE_CSP_EXTRA": "img-src https://env-img.example",
    "PYRITE_DATA_DIR": None,  # filled with a temp dir
    "PYRITE_GITHUB_CLIENT_ID": "env-gh-client-id",
    "PYRITE_GITHUB_CLIENT_SECRET": "env-gh-client-secret-77",
    "EDITOR": "env-editor-bin",
}


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    for name in ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "a").mkdir()
    path = config_module.current_config_file()
    dump_yaml_file(
        {
            "version": "1.0",
            "knowledge_bases": [{"name": "a", "path": str(tmp_path / "a")}],
            "settings": dict(FILE_SETTINGS),
        },
        path,
    )
    return path


@pytest.mark.parametrize("name", sorted(ENVIRONMENT))
def test_an_environment_value_is_not_saved(name, config_file, tmp_path, monkeypatch):
    value = ENVIRONMENT[name] or str(tmp_path / "env-data-dir")
    monkeypatch.setenv(name, value)
    config = config_module.load_config()
    (tmp_path / "b").mkdir()
    config.add_kb(KBConfig(name="b", path=tmp_path / "b"))

    config_module.save_config(config)

    saved = load_yaml_file(config_file)
    assert [kb["name"] for kb in saved["knowledge_bases"]] == ["a", "b"]
    assert saved["settings"] == FILE_SETTINGS
    if value not in ("true", "false", "read"):  # words the file holds anyway
        assert value not in config_file.read_text()
    assert set(saved) <= {"version", "knowledge_bases", "settings"}


def test_a_requested_settings_change_is_saved_and_nothing_else(config_file, monkeypatch):
    monkeypatch.setenv("PYRITE_API_KEY", "env-secret-api-key-0f3a")
    config = config_module.load_config()
    config.settings.ai_model = "chosen-model"  # `pyrite config set ai_model ...`
    config_module.save_config(config)
    saved = load_yaml_file(config_file)
    assert saved["settings"] == {**FILE_SETTINGS, "ai_model": "chosen-model"}


def test_a_second_save_keeps_the_environment_out_too(config_file, tmp_path, monkeypatch):
    monkeypatch.setenv("PYRITE_API_KEY", "env-secret-api-key-0f3a")
    config = config_module.load_config()
    for name in ("b", "c"):
        (tmp_path / name).mkdir()
        config.add_kb(KBConfig(name=name, path=tmp_path / name))
        config_module.save_config(config)
    saved = load_yaml_file(config_file)
    assert [kb["name"] for kb in saved["knowledge_bases"]] == ["a", "b", "c"]
    assert "env-secret-api-key-0f3a" not in config_file.read_text()


def test_a_save_keeps_file_content_memory_does_not_hold(config_file, tmp_path):
    """Keys of the file Pyrite does not model are the file's own content."""
    data = load_yaml_file(config_file)
    data["settings"]["operator_note"] = "kept"
    dump_yaml_file(data, config_file)
    config = config_module.load_config()
    (tmp_path / "b").mkdir()
    config.add_kb(KBConfig(name="b", path=tmp_path / "b"))
    config_module.save_config(config)
    assert load_yaml_file(config_file)["settings"]["operator_note"] == "kept"


def test_an_unchanged_kb_entry_stays_as_the_operator_wrote_it(config_file, tmp_path):
    """Adding one KB rewrites that KB's entry only: the operator's hand-written
    entries keep their own text (no defaults filled in, no keys reordered)."""
    config = config_module.load_config()
    (tmp_path / "b").mkdir()
    config.add_kb(KBConfig(name="b", path=tmp_path / "b"))
    config_module.save_config(config)
    first = load_yaml_file(config_file)["knowledge_bases"][0]
    assert first == {"name": "a", "path": str(tmp_path / "a")}
