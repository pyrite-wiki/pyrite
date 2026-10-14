"""Exporting a KB to a repo requires being able to read that KB.

`POST /api/kbs/{kb}/export` clones a caller-chosen repo, writes the KB's
entries into it and pushes. It was gated on the global write tier only, so a
write-role user with no read on a private KB could push that KB's content into
a repository they control. The route now applies the same per-KB read rule as
every read route, and a KB the caller cannot read answers exactly as a KB that
does not exist.
"""

import subprocess
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pyrite.config import AuthConfig, KBConfig, PyriteConfig, Settings
from pyrite.server.api import create_app, get_config, get_db
from pyrite.services.auth_service import AuthService
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB
from tests.auth_seed import seed_and_sign_in

PUBLIC, PRIVATE = "public-kb", "private-kb"
SECRET = "the source is Alice"


def _git(*args, cwd=None):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True, timeout=120)


def _seed_bare_repo(tmp: Path) -> Path:
    """A bare repo with one commit on main: a push target the caller controls."""
    bare = tmp / "target.git"
    _git("init", "--bare", "-b", "main", str(bare))
    work = tmp / "seed"
    _git("clone", str(bare), str(work))
    (work / "README.md").write_text("seed\n")
    _git("add", "README.md", cwd=work)
    _git(
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@example.com",
        "commit",
        "-m",
        "seed",
        cwd=work,
    )
    _git("push", "origin", "HEAD:main", cwd=work)
    return bare


def _bare_repo_history(bare: Path) -> str:
    return subprocess.run(
        ["git", "--git-dir", str(bare), "log", "-p", "--all"],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    ).stdout


@pytest.fixture
def env():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        (tmp / PUBLIC).mkdir()
        (tmp / PRIVATE).mkdir()
        config = PyriteConfig(
            knowledge_bases=[
                KBConfig(name=PUBLIC, path=tmp / PUBLIC, kb_type="generic", default_role="read"),
                KBConfig(name=PRIVATE, path=tmp / PRIVATE, kb_type="generic", default_role="none"),
            ],
            settings=Settings(
                index_path=tmp / "index.db",
                auth=AuthConfig(enabled=True, allow_registration=True),
            ),
        )
        app = create_app(config=config)
        db = PyriteDB(config.settings.index_path)
        app.dependency_overrides[get_config] = lambda: config
        app.dependency_overrides[get_db] = lambda: db
        svc = KBService(config, db)
        svc.create_entry(PRIVATE, "source-identity", "Source identity", "note", SECRET)
        svc.create_entry(PUBLIC, "public-note", "Public note", "note", "nothing to hide")

        def client_for(username, role=None):
            c = TestClient(app)
            r = c.post("/auth/register", json={"username": username, "password": "password123"})
            assert r.status_code == 200, r.text
            if role:
                AuthService(db, config.settings.auth).set_role(r.json()["id"], role)
            return c, r.json()["id"]

        admin = TestClient(app)
        seed_and_sign_in(admin, "owner", "password123")  # the sole admin, via the operator path
        writer, writer_id = client_for("mallory", role="write")
        try:
            yield {
                "tmp": tmp,
                "admin": admin,
                "writer": writer,
                "writer_id": writer_id,
                "db": db,
                "config": config,
            }
        finally:
            db.close()


def test_write_user_without_read_cannot_export_private_kb(env):
    bare = _seed_bare_repo(env["tmp"])
    before = _bare_repo_history(bare)

    r = env["writer"].post(f"/api/kbs/{PRIVATE}/export", json={"repo_url": f"file://{bare}"})
    missing = env["writer"].post("/api/kbs/no-such-kb/export", json={"repo_url": f"file://{bare}"})

    assert r.status_code == 404, r.text
    # Indistinguishable from a KB that does not exist: same status, same body
    # shape, the name substituted.
    assert missing.status_code == 404
    assert r.json() == {"detail": {"code": "KB_NOT_FOUND", "message": f"KB '{PRIVATE}' not found"}}
    assert missing.json() == {
        "detail": {"code": "KB_NOT_FOUND", "message": "KB 'no-such-kb' not found"}
    }
    after = _bare_repo_history(bare)
    assert after == before
    assert SECRET not in after


def test_write_user_with_read_grant_can_export(env):
    bare = _seed_bare_repo(env["tmp"])
    AuthService(env["db"], env["config"].settings.auth).grant_kb_permission(
        env["writer_id"], PRIVATE, "read", granted_by=1
    )

    r = env["writer"].post(f"/api/kbs/{PRIVATE}/export", json={"repo_url": f"file://{bare}"})

    assert r.status_code == 200, r.text
    assert r.json()["success"] is True
    assert SECRET in _bare_repo_history(bare)


def test_admin_can_export_private_kb(env):
    bare = _seed_bare_repo(env["tmp"])

    r = env["admin"].post(f"/api/kbs/{PRIVATE}/export", json={"repo_url": f"file://{bare}"})

    assert r.status_code == 200, r.text
    assert SECRET in _bare_repo_history(bare)


@pytest.mark.control(
    reason="characterization for #380: pins behaviour before it moves behind a service, so it passes with and without the change by design"
)
def test_export_hands_the_signed_in_users_github_token_to_the_service(env, monkeypatch):
    """Characterizes #380's move of the token lookup to the auth provider: a
    signed-in caller's stored GitHub token reaches the export service, and a
    caller with none stored passes None."""
    from pyrite.services.export_service import ExportService

    seen = []

    def fake_export(self, kb_name, repo_url, github_token=None, **kw):
        seen.append(github_token)
        return {"success": True}

    monkeypatch.setattr(ExportService, "export_kb_to_repo", fake_export)
    AuthService(env["db"], env["config"].settings.auth).store_github_token(
        env["writer_id"], "gho_characterized"
    )

    r = env["writer"].post(f"/api/kbs/{PUBLIC}/export", json={"repo_url": "file:///nowhere"})
    assert r.status_code == 200, r.text
    r = env["admin"].post(f"/api/kbs/{PUBLIC}/export", json={"repo_url": "file:///nowhere"})
    assert r.status_code == 200, r.text
    assert seen == ["gho_characterized", None]
