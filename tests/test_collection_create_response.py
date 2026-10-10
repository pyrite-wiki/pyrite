"""A completed collection write returns its actual id rather than a 500."""

import hashlib
import pytest
from fastapi.testclient import TestClient
from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.server.api import create_app, get_db
from pyrite.storage.database import PyriteDB
from pyrite.storage.repository import KBRepository


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "kb"
    root.mkdir()
    kb = KBConfig(name="test", path=root)
    config = PyriteConfig(
        knowledge_bases=[kb],
        settings=Settings(
            index_path=tmp_path / "index.db",
            api_keys=[
                {
                    "key_hash": hashlib.sha256(b"write-test").hexdigest(),
                    "role": "write",
                    "label": "test-write",
                },
                {
                    "key_hash": hashlib.sha256(b"read-test").hexdigest(),
                    "role": "read",
                    "label": "test-read",
                },
            ],
        ),
    )
    db = PyriteDB(config.settings.index_path)
    db.register_kb("test", "generic", str(root))
    app = create_app(config=config)
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)
    yield client, db, KBRepository(kb)
    client.close()
    db.close()


@pytest.mark.parametrize(
    ("title", "expected"), [("My Collection", "my-collection"), ("!!!", "collection")]
)
def test_created_collection_response_and_duplicate(env, title, expected):
    client, db, repo = env
    for suffix in ("", "-2"):
        response = client.post(
            "/api/collections",
            headers={"X-API-Key": "write-test"},
            json={"kb": "test", "title": title, "query": "*"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["id"] == expected + suffix
        assert db.get_entry(expected + suffix, "test") is not None
        assert repo.load(expected + suffix).title == title


@pytest.mark.control(reason="Read-tier callers already cannot create collections")
def test_read_tier_does_not_write_collection(env):
    client, db, _ = env
    response = client.post(
        "/api/collections",
        headers={"X-API-Key": "read-test"},
        json={"kb": "test", "title": "No", "query": "*"},
    )
    assert response.status_code == 403
    assert db.get_entry("no", "test") is None


@pytest.mark.control(reason="A read-only KB already refuses writes")
def test_read_only_kb_is_not_written(env):
    client, db, repo = env
    repo.config.read_only = True
    response = client.post(
        "/api/collections",
        headers={"X-API-Key": "write-test"},
        json={"kb": "test", "title": "No", "query": "*"},
    )
    assert response.status_code == 403
    assert db.get_entry("no", "test") is None


def test_collection_override_in_typed_kb_still_returns_created_entry(env):
    client, db, repo = env
    (repo.config.path / "kb.yaml").write_text(
        "name: test\ntypes:\n  event:\n    description: Event\n", encoding="utf-8"
    )
    repo.config.invalidate_schema_cache()
    response = client.post(
        "/api/collections",
        headers={"X-API-Key": "write-test"},
        json={"kb": "test", "title": "Typed", "query": "*"},
    )
    assert response.status_code == 200
    assert db.get_entry("typed", "test")["entry_type"] == "collection"
