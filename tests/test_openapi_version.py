"""The OpenAPI document reports the installed pyrite version (#1005)."""

import pytest

import pyrite


def test_openapi_info_version_matches_package_version(tmp_path):
    pytest.importorskip("fastapi", reason="fastapi not installed")
    from fastapi.testclient import TestClient

    from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
    from pyrite.server.api import create_app

    kb_path = tmp_path / "kb"
    kb_path.mkdir()
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="test-kb", path=kb_path, kb_type=KBType.GENERIC)],
        settings=Settings(index_path=tmp_path / "index.db"),
    )
    with TestClient(create_app(config)) as client:
        resp = client.get("/openapi.json")

    assert resp.status_code == 200
    assert resp.json()["info"]["version"] == pyrite.__version__
