"""CORS never allows credentials when the origin list holds a wildcard.

The property: credentials are allowed only when no item of ``cors_origins``
is ``"*"``. And an empty item is not an origin: it is dropped however the
setting is given (``PYRITE_CORS_ORIGINS``, the config file, ``Settings``
built in code, or a list changed before the app is built).
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")

EVIL = "https://elsewhere.example"
APP = "https://app.example"


def _credentials_allowed(resp) -> bool:
    return resp.headers.get("access-control-allow-credentials") == "true"


def _assert_no_credentials(client):
    simple = client.get("/health", headers={"Origin": EVIL, "Cookie": "pyrite_session=x"})
    preflight = client.options(
        "/health",
        headers={"Origin": EVIL, "Access-Control-Request-Method": "GET"},
    )
    assert not _credentials_allowed(simple), dict(simple.headers)
    assert not _credentials_allowed(preflight), dict(preflight.headers)


@pytest.mark.parametrize("origins", [["*", APP], [APP, "*"]])
def test_a_wildcard_anywhere_in_the_list_allows_no_credentials(make_client, origins):
    client, _config, _db = make_client(api_key="", cors_origins=origins)
    _assert_no_credentials(client)


@pytest.mark.control(reason="a list that is exactly the wildcard allowed none before too")
def test_the_wildcard_alone_allows_no_credentials(make_client):
    client, _config, _db = make_client(api_key="", cors_origins=["*"])
    _assert_no_credentials(client)


@pytest.mark.control(reason="an explicit list still allows credentials for its origins")
def test_an_explicit_list_allows_credentials(make_client):
    client, _config, _db = make_client(api_key="", cors_origins=[APP])
    resp = client.get("/health", headers={"Origin": APP})
    assert _credentials_allowed(resp), dict(resp.headers)


def test_empty_items_in_the_environment_are_dropped(monkeypatch):
    from pyrite.config import PyriteConfig, _apply_env_overrides

    monkeypatch.setenv("PYRITE_CORS_ORIGINS", f"{APP}, , ,")
    config = PyriteConfig()
    _apply_env_overrides(config)
    assert config.settings.cors_origins == [APP]


def test_empty_items_in_the_config_file_are_dropped():
    from pyrite.config import PyriteConfig

    config = PyriteConfig.from_dict({"settings": {"cors_origins": ["", APP, "  "]}})
    assert config.settings.cors_origins == [APP]


def test_empty_items_in_settings_built_in_code_are_dropped():
    from pyrite.config import Settings

    assert Settings(cors_origins=["", APP, " "]).cors_origins == [APP]


def test_the_app_drops_empty_origins_set_after_the_settings_were_built(make_client):
    """An origin list changed after the settings were made is read the same
    way when the app is built: an empty item is no origin."""
    from fastapi.testclient import TestClient

    from pyrite.server.api import create_app

    _client, config, _db = make_client(api_key="", cors_origins=[APP])
    config.settings.cors_origins = ["", APP]
    with TestClient(create_app(config=config)) as client:
        resp = client.get("/health", headers={"Origin": ""})
    assert resp.headers.get("access-control-allow-origin") is None, dict(resp.headers)
