"""Subscribing to or forking a repository never adopts an existing KB.

A KB's name in a subscribed repository comes from that repository's own
``kb.yaml``, which whoever controls the repository chooses. Subscribe and fork
refuse the whole operation when a discovered name is already registered (in
config or in the KB registry), when two KBs in the repository share a name,
when a name is not a plain KB name, or when the repository name is already
registered. Nothing is half-registered and the clone directory is removed.

Medium tests: two real git repositories in tmp -- the existing KB's and the
one being subscribed -- driven through ``POST /api/repos/subscribe`` and
``POST /api/repos/fork`` on a real app. Only the network is replaced: the
GitHub URL is cloned from a local repository with a real ``git clone``, and
the GitHub fork API call returns a fork URL.
"""

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pyrite.config import AuthConfig, KBConfig, PyriteConfig, Settings, load_config
from pyrite.server.api import create_app, get_config, get_db, get_repo_service
from pyrite.services.git_service import GitService, _git_env
from pyrite.services.repo_service import RepoService
from pyrite.storage.database import PyriteDB
from tests.auth_seed import seed_and_sign_in

EXISTING = "p"
SUBSCRIBE_URL = "https://github.com/someone/notes"
FORK_UPSTREAM_URL = "https://github.com/upstream/notes"
FORK_CLONE_URL = "https://github.com/me/notes.git"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=str(cwd), check=True, capture_output=True, env=_git_env(), timeout=120
    )


def _make_repo(path: Path, kbs: dict[str, str]) -> Path:
    """A real git repo holding one KB directory per {subdir: kb.yaml name}."""
    path.mkdir(parents=True)
    _git(path, "init", "-q", "-b", "main")
    for subdir, kb_name in kbs.items():
        kb_dir = path / subdir if subdir else path
        kb_dir.mkdir(exist_ok=True)
        (kb_dir / "kb.yaml").write_text(f"name: {kb_name}\nkb_type: generic\n")
        (kb_dir / "incoming.md").write_text(
            "---\nid: incoming\ntype: note\ntitle: From the subscribed repo\n---\nbody\n"
        )
    _git(path, "add", "-A")
    _git(
        path,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@example.com",
        "commit",
        "-q",
        "-m",
        "init",
    )
    return path


@pytest.fixture
def env(tmp_path, monkeypatch):
    # The existing KB: a private KB in its own git repository, indexed.
    existing_dir = _make_repo(tmp_path / "existing-repo", {"": EXISTING})
    (existing_dir / "kb.yaml").unlink()
    config = PyriteConfig(
        knowledge_bases=[
            KBConfig(name=EXISTING, path=existing_dir, kb_type="generic", default_role="none"),
        ],
        settings=Settings(
            index_path=tmp_path / "index.db",
            workspace_path=tmp_path / "workspace",
            auth=AuthConfig(enabled=True, allow_registration=True),
        ),
    )
    app = create_app(config=config)
    db = PyriteDB(config.settings.index_path)
    app.dependency_overrides[get_config] = lambda: config
    app.dependency_overrides[get_db] = lambda: db

    def svc():
        s = RepoService(config, db)
        s._github_token = "gh-test-token"
        return s

    app.dependency_overrides[get_repo_service] = svc

    db.register_kb(EXISTING, "generic", str(existing_dir), default_role="none")
    db.upsert_entry(
        {
            "id": "secret",
            "kb_name": EXISTING,
            "entry_type": "note",
            "title": "Existing entry",
            "body": "b",
            "file_path": str(existing_dir / "secret.md"),
        }
    )

    sources: dict[str, Path] = {}

    def clone_from_local(remote_url, local_path, branch="main", depth=1, token=None):
        src = sources[remote_url]
        cmd = ["git", "clone", "-q", "--branch", branch, "--", str(src), str(local_path)]
        subprocess.run(cmd, check=True, capture_output=True, env=_git_env(), timeout=120)
        return True, "OK", "cloned"

    monkeypatch.setattr(GitService, "clone_with_code", staticmethod(clone_from_local))
    monkeypatch.setattr(
        GitService,
        "fork_repo",
        staticmethod(
            lambda owner, repo, token: (
                True,
                {"clone_url": FORK_CLONE_URL, "full_name": "me/notes"},
            )
        ),
    )

    client = TestClient(app)
    seed_and_sign_in(client, "admin", "password123")  # the sole admin, seeded via the operator path

    def snapshot():
        kb_row = db.execute_sql("SELECT * FROM kb WHERE name = :n", {"n": EXISTING})
        entries = db.execute_sql(
            "SELECT id, title, body, file_path FROM entry WHERE kb_name = :n ORDER BY id",
            {"n": EXISTING},
        )
        cfg = config.get_kb(EXISTING)
        return kb_row, entries, (cfg.path, cfg.default_role, cfg.repo, cfg.read_only)

    try:
        yield {
            "client": client,
            "db": db,
            "config": config,
            "tmp": tmp_path,
            "sources": sources,
            "snapshot": snapshot,
        }
    finally:
        db.close()


def _subscribe(env, kbs: dict[str, str], name: str | None = None):
    env["sources"][SUBSCRIBE_URL] = _make_repo(env["tmp"] / "incoming", kbs)
    body = {"remote_url": SUBSCRIBE_URL}
    if name:
        body["name"] = name
    return env["client"].post("/api/repos/subscribe", json=body)


def _fork(env, kbs: dict[str, str]):
    env["sources"][FORK_CLONE_URL] = _make_repo(env["tmp"] / "fork-src", kbs)
    return env["client"].post("/api/repos/fork", json={"remote_url": FORK_UPSTREAM_URL})


def _assert_nothing_registered(env, clone_dir: Path, repo_name: str):
    assert not clone_dir.exists(), "the clone directory must be removed"
    assert env["db"].get_repo(name=repo_name) is None
    assert env["db"].execute_sql("SELECT name FROM kb WHERE repo_id IS NOT NULL") == []


# ---------------------------------------------------------------------------
# Name collisions
# ---------------------------------------------------------------------------


def test_subscribe_refuses_a_kb_name_already_in_config(env):
    before = env["snapshot"]()
    r = _subscribe(env, {"": EXISTING})

    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "KB_NAME_CONFLICT"
    assert env["snapshot"]() == before
    assert before[0][0]["repo_id"] is None
    _assert_nothing_registered(env, env["tmp"] / "workspace" / "someone" / "notes", "someone/notes")


def test_subscribe_refuses_a_kb_name_only_in_the_registry(env):
    """A KB registered in the database but not in this process's config."""
    env["db"].register_kb("q", "generic", str(env["tmp"] / "q"), source="config")
    before = env["db"].execute_sql("SELECT * FROM kb WHERE name = 'q'")

    r = _subscribe(env, {"": "q"})

    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "KB_NAME_CONFLICT"
    assert env["db"].execute_sql("SELECT * FROM kb WHERE name = 'q'") == before
    assert env["config"].get_kb("q") is None
    _assert_nothing_registered(env, env["tmp"] / "workspace" / "someone" / "notes", "someone/notes")


def test_subscribe_refuses_when_any_one_of_several_kbs_collides(env):
    """The fresh KB listed first is not registered either: all or nothing."""
    before = env["snapshot"]()
    r = _subscribe(env, {"a-first": "fresh-kb", "b-second": EXISTING})

    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "KB_NAME_CONFLICT"
    assert env["snapshot"]() == before
    assert env["config"].get_kb("fresh-kb") is None
    assert env["db"].execute_sql("SELECT 1 FROM kb WHERE name = 'fresh-kb'") == []
    _assert_nothing_registered(env, env["tmp"] / "workspace" / "someone" / "notes", "someone/notes")


def test_subscribe_refuses_two_kbs_with_one_name_in_the_repo(env):
    r = _subscribe(env, {"one": "twin", "two": "twin"})

    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "KB_NAME_CONFLICT"
    assert env["config"].get_kb("twin") is None
    _assert_nothing_registered(env, env["tmp"] / "workspace" / "someone" / "notes", "someone/notes")


def test_subscribe_refuses_an_existing_repository_name(env):
    """`name` names the repository row; an existing one is not re-pointed."""
    existing = env["db"].register_repo("team/existing", str(env["tmp"] / "existing-repo"))
    env["db"].link_kb_to_repo(EXISTING, existing["id"], "")
    before_repo = env["db"].get_repo(name="team/existing")
    before = env["snapshot"]()

    r = _subscribe(env, {"": "fresh-kb"}, name="team/existing")

    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "REPO_NAME_CONFLICT"
    assert env["db"].get_repo(name="team/existing") == before_repo
    assert env["snapshot"]() == before
    assert env["config"].get_kb("fresh-kb") is None
    assert not (env["tmp"] / "workspace" / "someone" / "notes").exists()


def test_fork_refuses_a_kb_name_already_in_config(env):
    before = env["snapshot"]()
    r = _fork(env, {"": EXISTING})

    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "KB_NAME_CONFLICT"
    assert env["snapshot"]() == before
    _assert_nothing_registered(env, env["tmp"] / "workspace" / "me" / "notes", "me/notes")


def test_fork_refuses_an_existing_repository_name(env):
    env["db"].register_repo("me/notes", str(env["tmp"] / "elsewhere"))
    before_repo = env["db"].get_repo(name="me/notes")

    r = _fork(env, {"": "fresh-kb"})

    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "REPO_NAME_CONFLICT"
    assert env["db"].get_repo(name="me/notes") == before_repo
    assert not (env["tmp"] / "workspace" / "me" / "notes").exists()


def test_a_collision_with_a_kb_only_in_config_unwinds_too(env):
    """A KB that exists in this process's config but not in the registry."""
    other = env["tmp"] / "config-only"
    other.mkdir()
    env["config"].add_kb(KBConfig(name="c-only", path=other, kb_type="generic"))

    r = _subscribe(env, {"a-first": "fresh-kb", "b-second": "c-only"})

    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "KB_NAME_CONFLICT"
    assert env["config"].get_kb("c-only").path == other
    assert env["db"].execute_sql("SELECT 1 FROM kb WHERE name = 'c-only'") == []
    assert env["config"].get_kb("fresh-kb") is None
    assert env["db"].execute_sql("SELECT 1 FROM kb WHERE name = 'fresh-kb'") == []
    _assert_nothing_registered(env, env["tmp"] / "workspace" / "someone" / "notes", "someone/notes")


# ---------------------------------------------------------------------------
# Name validity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["../escape", "-option", "has space", "a/b", ".hidden"])
def test_subscribe_refuses_a_kb_name_that_is_not_a_plain_name(env, bad):
    r = _subscribe(env, {"": f'"{bad}"'})

    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "INVALID_KB_NAME"
    assert env["config"].get_kb(bad) is None
    _assert_nothing_registered(env, env["tmp"] / "workspace" / "someone" / "notes", "someone/notes")


# ---------------------------------------------------------------------------
# The ordinary case still works
# ---------------------------------------------------------------------------


def test_subscribe_with_fresh_names_registers_read_only_kbs(env):
    before = env["snapshot"]()
    r = _subscribe(env, {"": "fresh-kb"})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kbs"] == ["fresh-kb"]
    # The access policy of a subscribed KB is stated, not implied.
    assert body["kb_default_role"] is None
    assert env["snapshot"]() == before
    repo = env["db"].get_repo(name="someone/notes")
    rows = env["db"].execute_sql("SELECT repo_id, path FROM kb WHERE name = 'fresh-kb'")
    assert rows[0]["repo_id"] == repo["id"]
    assert env["config"].get_kb("fresh-kb").read_only is True
    assert "fresh-kb" in [k.name for k in load_config().knowledge_bases]
