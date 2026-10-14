"""Turning on auth does not publish the instance.

Properties:

- With auth on and no admin, web registration and OAuth sign-up are refused.
  The first admin comes from the operator's CLI (``pyrite-admin user create
  --role admin``); nobody becomes admin by being first.
- One invite code creates at most one user, however many registrations
  present it at once.
- ``/auth/login`` and ``/auth/register`` are rate-limited: login per client
  and per username, registration per client, each configurable.
- A self-registered user reads public KBs only: a KB whose ``default_role``
  is read or write. A KB without one stays closed to them until an admin
  grants access; users an operator vetted keep their global role everywhere.
- ``pyrite serve`` warns when auth is on with open registration, naming the
  KBs a stranger could read by signing up.

Concurrency tests follow ``tests/test_session_cap_concurrency.py``: a start
barrier so the calls contend, one deadline for the group, a thread and a
``PyriteDB`` per call on one file database, and a per-statement delay that
holds each call between its steps long enough for any interleaving the code
allows to show.
"""

import re
import sqlite3
import threading
import time

import pytest
import yaml
from sqlalchemy import event
from typer.testing import CliRunner

import pyrite.config as config_module
from pyrite.config import AuthConfig, OAuthProviderConfig
from pyrite.services.auth_service import AuthService
from pyrite.services.oauth_providers import OAuthProfile
from pyrite.storage.database import PyriteDB

_GROUP_DEADLINE = 180.0
_BARRIER_TIMEOUT = 150.0
_STEP_DELAY = 0.02
_AUTH_TABLES = re.compile(r"\b(local_user|invite_code)\b", re.IGNORECASE)

_PROVIDER = OAuthProviderConfig(client_id="id", client_secret="secret", default_tier="read")


def _insert_admin(db, username="root", password="password123") -> int:
    """An admin row written straight to the table, as any operator tooling
    would: the tests below need an admin to exist, and must not depend on
    the code under test to make one."""
    import bcrypt

    db.execute_write_sql(
        "INSERT INTO local_user (username, password_hash, role, auth_provider)"
        " VALUES (:u, :h, 'admin', 'local')",
        {"u": username, "h": bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()},
    )
    return db.execute_sql("SELECT id FROM local_user WHERE username = :u", {"u": username})[0]["id"]


def _roles(db_path) -> dict[str, str]:
    conn = sqlite3.connect(db_path)
    try:
        return dict(conn.execute("SELECT username, role FROM local_user"))
    finally:
        conn.close()


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "index.db"
    with PyriteDB(path):
        pass  # migrate once, before any thread opens it
    return path


def _race(db_path, config, calls):
    """Run each ``call(service)`` on its own thread and PyriteDB, released
    together. Returns ``(results, errors)``."""
    dbs = [PyriteDB(db_path) for _ in calls]

    def widen(conn, cursor, statement, *args):
        if _AUTH_TABLES.search(statement):
            time.sleep(_STEP_DELAY)

    for db in dbs:
        event.listen(db.engine, "before_cursor_execute", widen)
    barrier = threading.Barrier(len(calls), timeout=30)
    results: list = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def run(db, call):
        try:
            service = AuthService(db, config)
            barrier.wait(timeout=_BARRIER_TIMEOUT)
            out = call(service)
            with lock:
                results.append(out)
        except BaseException as e:  # noqa: BLE001 -- returned to the test
            with lock:
                errors.append(e)

    threads = [
        threading.Thread(target=run, args=pair, daemon=True)
        for pair in zip(dbs, calls, strict=True)
    ]
    try:
        for t in threads:
            t.start()
        end = time.monotonic() + _GROUP_DEADLINE
        for t in threads:
            t.join(timeout=max(0.0, end - time.monotonic()))
        still_running = [t.name for t in threads if t.is_alive()]
        assert not still_running, f"thread(s) still running: {still_running}"
    finally:
        for db in dbs:
            db.close()
    return results, errors


# ── No admin, no sign-up ─────────────────────────────────────────────────


class TestNoAdminNoSignup:
    def test_registration_refused_while_no_admin_exists(self, db_path):
        with PyriteDB(db_path) as db:
            service = AuthService(db, AuthConfig(enabled=True))
            with pytest.raises(ValueError, match="pyrite-admin user create"):
                service.register("stranger", "password123")
        assert _roles(db_path) == {}

    def test_oauth_signup_refused_while_no_admin_exists(self, db_path):
        profile = OAuthProfile(provider="github", provider_id="1", username="octo", orgs=[])
        with PyriteDB(db_path) as db:
            service = AuthService(db, AuthConfig(enabled=True))
            with pytest.raises(ValueError, match="pyrite-admin user create"):
                service.oauth_login(profile, _PROVIDER)
        assert _roles(db_path) == {}

    def test_register_endpoint_refuses_while_no_admin_exists(self, make_client):
        client, _, _ = make_client(auth=AuthConfig(enabled=True))
        r = client.post("/auth/register", json={"username": "stranger", "password": "password123"})
        assert r.status_code == 403
        assert "pyrite-admin user create" in r.json()["detail"]

    @pytest.mark.control(
        reason="with an admin present dev also gave a registrant read; the no-admin case is the red test above"
    )
    def test_first_registrant_never_becomes_admin(self, db_path):
        with PyriteDB(db_path) as db:
            service = AuthService(db, AuthConfig(enabled=True))
            _insert_admin(db)
            user = service.register("first", "password123")
        assert user["role"] == "read"

    @pytest.mark.control(reason="with an admin present dev also gave the provider's tier")
    def test_first_oauth_user_never_becomes_admin(self, db_path):
        profile = OAuthProfile(provider="github", provider_id="1", username="octo", orgs=[])
        with PyriteDB(db_path) as db:
            service = AuthService(db, AuthConfig(enabled=True))
            _insert_admin(db)
            user, _ = service.oauth_login(profile, _PROVIDER)
        assert user["role"] == "read"

    def test_concurrent_first_registrations_create_no_admin(self, db_path):
        calls = [
            (lambda name: lambda s: s.register(name, "password123"))(f"user{i}") for i in range(8)
        ]
        _, errors = _race(db_path, AuthConfig(enabled=True), calls)
        assert "admin" not in _roles(db_path).values()
        assert _roles(db_path) == {}
        assert len(errors) == len(calls)
        assert all(isinstance(e, ValueError) for e in errors), errors


class TestCliBootstrap:
    def _write_config(self, tmp_path, db_path):
        config_module.CONFIG_FILE.write_text(
            yaml.safe_dump(
                {
                    "knowledge_bases": [],
                    "settings": {"index_path": str(db_path), "auth": {"enabled": True}},
                }
            )
        )

    def test_cli_creates_an_admin_then_registration_is_allowed(self, tmp_path, db_path):
        from pyrite.admin_cli import app

        self._write_config(tmp_path, db_path)
        result = CliRunner().invoke(
            app,
            ["user", "create", "root", "--role", "admin"],
            input="password123\npassword123\n",
        )
        assert result.exit_code == 0, result.output
        assert _roles(db_path) == {"root": "admin"}

        with PyriteDB(db_path) as db:
            service = AuthService(db, AuthConfig(enabled=True))
            user = service.register("newcomer", "password123")
            _, token = service.login("root", "password123")
            assert service.verify_session(token)["role"] == "admin"
        assert user["role"] == "read"

    @pytest.mark.control(reason="dev has no `user create` command, so any invocation fails")
    def test_cli_refuses_an_unknown_role(self, tmp_path, db_path):
        from pyrite.admin_cli import app

        self._write_config(tmp_path, db_path)
        result = CliRunner().invoke(
            app,
            ["user", "create", "root", "--role", "owner"],
            input="password123\npassword123\n",
        )
        assert result.exit_code != 0
        assert _roles(db_path) == {}

    def test_cli_refuses_a_taken_username(self, tmp_path, db_path):
        from pyrite.admin_cli import app

        self._write_config(tmp_path, db_path)
        args = ["user", "create", "root", "--role", "admin"]
        first = CliRunner().invoke(app, args, input="password123\npassword123\n")
        assert first.exit_code == 0, first.output
        second = CliRunner().invoke(app, args, input="otherpass99\notherpass99\n")
        assert second.exit_code != 0
        with PyriteDB(db_path) as db:
            AuthService(db, AuthConfig(enabled=True)).login("root", "password123")


# ── Public KBs only for self-registered users ────────────────────────────


@pytest.fixture
def two_kb_app(tmp_path):
    """An auth-enabled app with a public KB and one without a default_role,
    an admin made by the operator path, and a client per browser."""
    from fastapi.testclient import TestClient

    from pyrite.config import KBConfig, PyriteConfig, Settings
    from pyrite.server.api import create_app, get_config, get_db

    kbs = []
    for name, default_role in (("public-kb", "read"), ("team-kb", None)):
        (tmp_path / name).mkdir()
        kbs.append(
            KBConfig(name=name, path=tmp_path / name, kb_type="generic", default_role=default_role)
        )
    config = PyriteConfig(
        knowledge_bases=kbs,
        settings=Settings(index_path=tmp_path / "index.db", auth=AuthConfig(enabled=True)),
    )
    application = create_app(config=config)
    db = PyriteDB(tmp_path / "index.db")
    application.dependency_overrides[get_config] = lambda: config
    application.dependency_overrides[get_db] = lambda: db
    _insert_admin(db)
    admin = TestClient(application)
    assert (
        admin.post("/auth/login", json={"username": "root", "password": "password123"}).status_code
        == 200
    )
    yield {"app": application, "db": db, "config": config, "admin": admin}
    db.close()


def _visible_kbs(client) -> set[str]:
    r = client.get("/api/kbs")
    assert r.status_code == 200, r.text
    return {kb["name"] for kb in r.json()["kbs"]}


def _signed_up(two_kb_app):
    from fastapi.testclient import TestClient

    client = TestClient(two_kb_app["app"])
    r = client.post("/auth/register", json={"username": "newcomer", "password": "password123"})
    assert r.status_code == 200, r.text
    return client, r.json()["id"]


class TestSelfRegisteredReadsPublicKbsOnly:
    @pytest.mark.control(reason="a default_role: read KB was already readable on dev")
    def test_reads_a_public_kb(self, two_kb_app):
        client, _ = _signed_up(two_kb_app)
        assert "public-kb" in _visible_kbs(client)
        assert client.get("/api/entries", params={"kb": "public-kb"}).status_code == 200

    def test_cannot_read_a_kb_without_default_role(self, two_kb_app):
        client, _ = _signed_up(two_kb_app)
        assert "team-kb" not in _visible_kbs(client)
        assert client.get("/api/entries", params={"kb": "team-kb"}).status_code == 404

    @pytest.mark.control(
        reason="on dev the registrant could already read it; pins that a grant opens it"
    )
    def test_can_read_it_after_an_admin_grant(self, two_kb_app):
        client, user_id = _signed_up(two_kb_app)
        r = two_kb_app["admin"].post(
            "/api/kbs/team-kb/permissions", json={"user_id": user_id, "role": "read"}
        )
        assert r.status_code in (200, 201), r.text
        assert "team-kb" in _visible_kbs(client)

    @pytest.mark.control(
        reason="on dev every registrant read every KB; pins the admin path back to that"
    )
    def test_changing_the_role_alone_does_not_open_every_kb(self, two_kb_app):
        client, user_id = _signed_up(two_kb_app)
        for role in ("write", "read"):
            r = two_kb_app["admin"].put(f"/auth/users/{user_id}/role", json={"role": role})
            assert r.status_code == 200, r.text
            assert "team-kb" not in _visible_kbs(client)

    def test_an_explicit_admin_grant_of_global_access_opens_and_closes_every_kb(self, two_kb_app):
        client, user_id = _signed_up(two_kb_app)
        admin = two_kb_app["admin"]
        r = admin.put(f"/auth/users/{user_id}/role", json={"role": "read", "global_access": True})
        assert r.status_code == 200, r.text
        assert {"public-kb", "team-kb"} <= _visible_kbs(client)
        r = admin.put(f"/auth/users/{user_id}/role", json={"role": "read", "global_access": False})
        assert r.status_code == 200, r.text
        assert "team-kb" not in _visible_kbs(client)

    def test_a_self_registered_user_gets_at_most_read_on_a_public_kb(self, two_kb_app):
        _, user_id = _signed_up(two_kb_app)
        service = AuthService(two_kb_app["db"], two_kb_app["config"].settings.auth)
        assert service.get_kb_role(user_id, "open-kb", "write") == "read"
        assert service.get_kb_role(user_id, "public-kb", "read") == "read"

    def test_a_user_row_that_omits_global_access_gets_none(self, tmp_path):
        """Fail closed: an insert that does not name the column grants nothing."""
        with PyriteDB(tmp_path / "index.db") as db:
            db.execute_write_sql(
                "INSERT INTO local_user (username, password_hash, role) VALUES ('x', 'h', 'read')"
            )
            uid = db.execute_sql("SELECT id FROM local_user WHERE username = 'x'")[0]["id"]
            assert AuthService(db, AuthConfig(enabled=True)).get_kb_role(uid, "team-kb") is None

    @pytest.mark.control(reason="invited users already had their role everywhere on dev")
    def test_an_invited_user_gets_the_invite_role_on_every_kb(self, tmp_path):
        with PyriteDB(tmp_path / "index.db") as db:
            config = AuthConfig(enabled=True, require_invite_code=True)
            service = AuthService(db, config)
            _insert_admin(db)
            code = service.create_invite_code("root", role="write")["code"]
            user = service.register("invited", "password123", invite_code=code)
            assert service.get_kb_role(user["id"], "team-kb", None) == "write"

    def test_an_open_oauth_signup_reads_public_kbs_only(self, tmp_path):
        profile = OAuthProfile(provider="github", provider_id="9", username="octo", orgs=[])
        with PyriteDB(tmp_path / "index.db") as db:
            service = AuthService(db, AuthConfig(enabled=True))
            _insert_admin(db)
            user, _ = service.oauth_login(profile, _PROVIDER)
            assert service.get_kb_role(user["id"], "team-kb", None) is None
            assert service.get_kb_role(user["id"], "public-kb", "read") == "read"

    @pytest.mark.control(reason="org-vetted users already had their role everywhere on dev")
    def test_an_org_vetted_oauth_signup_keeps_its_role_everywhere(self, tmp_path):
        profile = OAuthProfile(provider="github", provider_id="9", username="octo", orgs=["acme"])
        provider = OAuthProviderConfig(
            client_id="id", client_secret="s", allowed_orgs=["acme"], default_tier="read"
        )
        with PyriteDB(tmp_path / "index.db") as db:
            service = AuthService(db, AuthConfig(enabled=True))
            _insert_admin(db)
            user, _ = service.oauth_login(profile, provider)
            assert service.get_kb_role(user["id"], "team-kb", None) == "read"

    @pytest.mark.control(reason="org-mapped users already had their role everywhere on dev")
    def test_an_org_mapped_oauth_signup_keeps_its_role_everywhere(self, tmp_path):
        profile = OAuthProfile(provider="github", provider_id="9", username="octo", orgs=["acme"])
        provider = OAuthProviderConfig(
            client_id="id", client_secret="s", org_tier_map={"acme": "write"}, default_tier="read"
        )
        with PyriteDB(tmp_path / "index.db") as db:
            service = AuthService(db, AuthConfig(enabled=True))
            _insert_admin(db)
            user, _ = service.oauth_login(profile, provider)
            assert service.get_kb_role(user["id"], "team-kb", None) == "write"

    @pytest.mark.control(reason="existing users already read every KB on dev; pins the migration")
    def test_existing_users_keep_their_global_role_after_the_migration(self, tmp_path):
        """A user row from before v26 (no global_access column) keeps reading
        KBs without a default_role once the column is added."""
        db_path = tmp_path / "old.db"
        conn = sqlite3.connect(db_path)
        conn.executescript(
            """
            CREATE TABLE local_user (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                display_name TEXT,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'read',
                created_at TEXT, updated_at TEXT,
                auth_provider TEXT DEFAULT 'local', provider_id TEXT, avatar_url TEXT,
                ephemeral_kb_count INTEGER DEFAULT 0, usage_tier TEXT DEFAULT 'default',
                github_access_token TEXT, github_token_scopes TEXT
            );
            INSERT INTO local_user (username, password_hash, role) VALUES ('veteran', 'x', 'read');
            """
        )
        conn.close()
        with PyriteDB(db_path) as db:
            uid = db.execute_sql("SELECT id FROM local_user WHERE username = 'veteran'")[0]["id"]
            assert AuthService(db, AuthConfig(enabled=True)).get_kb_role(uid, "team-kb") == "read"


# ── Invite codes ─────────────────────────────────────────────────────────


class TestInviteRedemption:
    def _code(self, db_path, config):
        with PyriteDB(db_path) as db:
            service = AuthService(db, config)
            _insert_admin(db)
            return service.create_invite_code("root", role="write")["code"]

    def test_one_invite_code_used_concurrently_creates_exactly_one_user(self, db_path):
        config = AuthConfig(enabled=True, require_invite_code=True)
        code = self._code(db_path, config)
        calls = [
            (lambda name: lambda s: s.register(name, "password123", invite_code=code))(f"u{i}")
            for i in range(8)
        ]
        results, errors = _race(db_path, config, calls)
        created = {u: r for u, r in _roles(db_path).items() if u != "root"}
        assert len(created) == 1, created
        assert len(results) == 1 and len(errors) == len(calls) - 1
        assert all(isinstance(e, ValueError) for e in errors), errors
        assert list(created.values()) == ["write"]

    @pytest.mark.control(
        reason="dev refused a used code when registrations were sequential; the race test is the red one"
    )
    def test_a_used_code_is_refused(self, db_path):
        config = AuthConfig(enabled=True, require_invite_code=True)
        code = self._code(db_path, config)
        with PyriteDB(db_path) as db:
            service = AuthService(db, config)
            service.register("first", "password123", invite_code=code)
            with pytest.raises(ValueError, match="[Ii]nvite"):
                service.register("second", "password123", invite_code=code)
        assert set(_roles(db_path)) == {"root", "first"}

    @pytest.mark.control(
        reason="dev checked expiry before redeeming; pins it in the new conditional UPDATE"
    )
    def test_an_expired_code_is_refused(self, db_path):
        config = AuthConfig(enabled=True, require_invite_code=True)
        code = self._code(db_path, config)
        with PyriteDB(db_path) as db:
            db.execute_write_sql(
                "UPDATE invite_code SET expires_at = '2000-01-01T00:00:00+00:00' WHERE code = :c",
                {"c": code},
            )
            with pytest.raises(ValueError, match="[Ii]nvite"):
                AuthService(db, config).register("late", "password123", invite_code=code)
        assert set(_roles(db_path)) == {"root"}

    @pytest.mark.control(
        reason="dev checked the name before redeeming; pins the rollback in the new transaction"
    )
    def test_a_taken_username_does_not_burn_the_code(self, db_path):
        config = AuthConfig(enabled=True, require_invite_code=True)
        code = self._code(db_path, config)
        with PyriteDB(db_path) as db:
            service = AuthService(db, config)
            with pytest.raises(ValueError, match="already taken"):
                service.register("root", "password123", invite_code=code)
            user = service.register("fresh", "password123", invite_code=code)
        assert user["role"] == "write"


# ── Rate limits ──────────────────────────────────────────────────────────


def _auth_client(make_client, **auth_kwargs):
    client, config, db = make_client(auth=AuthConfig(enabled=True, **auth_kwargs))
    _insert_admin(db)
    return client


class TestLoginRateLimit:
    def test_twenty_rapid_failed_logins_get_429(self, make_client):
        client = _auth_client(make_client)
        codes = [
            client.post(
                "/auth/login", json={"username": "root", "password": "wrong-pass"}
            ).status_code
            for _ in range(20)
        ]
        assert 429 in codes, codes
        assert codes[-1] == 429
        assert set(codes) <= {401, 429}

    def test_per_username_limit_is_configurable_and_counts_failures(self, make_client):
        client = _auth_client(
            make_client, login_rate_limit="100/minute", login_rate_limit_per_username="3/minute"
        )
        codes = [
            client.post(
                "/auth/login", json={"username": "root", "password": "wrong-pass"}
            ).status_code
            for _ in range(4)
        ]
        assert codes == [401, 401, 401, 429]
        # Another username from the same client is not held back by root's failures.
        r = client.post("/auth/login", json={"username": "other", "password": "wrong-pass"})
        assert r.status_code == 401

    def test_per_client_limit_is_configurable_and_spans_usernames(self, make_client):
        client = _auth_client(
            make_client, login_rate_limit="3/minute", login_rate_limit_per_username="100/minute"
        )
        codes = [
            client.post(
                "/auth/login", json={"username": f"u{i}", "password": "wrong-pass"}
            ).status_code
            for i in range(4)
        ]
        assert codes == [401, 401, 401, 429]

    def test_successful_logins_do_not_count_against_the_username(self, make_client):
        client = _auth_client(
            make_client, login_rate_limit="100/minute", login_rate_limit_per_username="2/minute"
        )
        for _ in range(4):
            r = client.post("/auth/login", json={"username": "root", "password": "password123"})
            assert r.status_code == 200

    def test_a_limited_login_does_not_check_the_password(self, make_client, monkeypatch):
        client = _auth_client(
            make_client, login_rate_limit="100/minute", login_rate_limit_per_username="1/minute"
        )
        client.post("/auth/login", json={"username": "root", "password": "wrong-pass"})
        calls = []
        monkeypatch.setattr(
            AuthService,
            "login",
            lambda self, *a: calls.append(a) or (_ for _ in ()).throw(ValueError()),
        )
        r = client.post("/auth/login", json={"username": "root", "password": "password123"})
        assert r.status_code == 429
        assert calls == []


class TestRegisterRateLimit:
    def test_rapid_registrations_get_429(self, make_client):
        client = _auth_client(make_client, register_rate_limit="3/minute")
        codes = [
            client.post(
                "/auth/register", json={"username": f"n{i}", "password": "password123"}
            ).status_code
            for i in range(4)
        ]
        assert codes == [200, 200, 200, 429]

    def test_default_register_limit_holds_back_automation(self, make_client):
        client = _auth_client(make_client)
        codes = [
            client.post(
                "/auth/register", json={"username": f"n{i}", "password": "password123"}
            ).status_code
            for i in range(20)
        ]
        assert codes[-1] == 429


class TestRateLimitConfigFromYaml:
    def test_auth_limits_and_invite_requirement_are_read_from_config(self):
        cfg = config_module.PyriteConfig.from_dict(
            {
                "settings": {
                    "auth": {
                        "enabled": True,
                        "require_invite_code": True,
                        "login_rate_limit": "7/minute",
                        "login_rate_limit_per_username": "2/minute",
                        "register_rate_limit": "1/hour",
                    }
                }
            }
        )
        auth = cfg.settings.auth
        assert auth.require_invite_code is True
        assert auth.login_rate_limit == "7/minute"
        assert auth.login_rate_limit_per_username == "2/minute"
        assert auth.register_rate_limit == "1/hour"


# ── Startup warning ──────────────────────────────────────────────────────


class TestServeWarning:
    def _serve(self, tmp_path, monkeypatch, auth: dict, default_role=None):
        import uvicorn

        from pyrite.cli import app

        kb = {"name": "notes", "path": str(tmp_path / "kb"), "kb_type": "generic"}
        if default_role:
            kb["default_role"] = default_role
        (tmp_path / "kb").mkdir(exist_ok=True)
        config_module.CONFIG_FILE.write_text(
            yaml.safe_dump(
                {
                    "knowledge_bases": [kb],
                    "settings": {"index_path": str(tmp_path / "index.db"), "auth": auth},
                }
            )
        )
        monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)
        result = CliRunner().invoke(app, ["serve", "--dev"])
        assert result.exit_code == 0, result.output
        return " ".join(result.output.split())

    def test_warns_with_open_registration(self, tmp_path, monkeypatch):
        out = self._serve(tmp_path, monkeypatch, {"enabled": True})
        assert "open registration" in out
        assert "can read nothing until granted" in out

    def test_names_the_kbs_a_stranger_can_read_after_signing_up(self, tmp_path, monkeypatch):
        out = self._serve(tmp_path, monkeypatch, {"enabled": True}, default_role="read")
        assert "open registration" in out
        assert "notes" in out

    @pytest.mark.control(reason="dev never warns; pins that the warning is not noise")
    def test_quiet_when_registration_needs_an_invite(self, tmp_path, monkeypatch):
        out = self._serve(tmp_path, monkeypatch, {"enabled": True, "require_invite_code": True})
        assert "open registration" not in out

    @pytest.mark.control(reason="dev never warns; pins that the warning is not noise")
    def test_quiet_when_registration_is_off(self, tmp_path, monkeypatch):
        out = self._serve(tmp_path, monkeypatch, {"enabled": True, "allow_registration": False})
        assert "open registration" not in out

    @pytest.mark.control(reason="dev never warns; pins that the warning is not noise")
    def test_quiet_when_auth_is_off(self, tmp_path, monkeypatch):
        out = self._serve(tmp_path, monkeypatch, {"enabled": False})
        assert "open registration" not in out


class TestRateLimitEnv:
    def test_env_overrides_the_auth_limits(self, monkeypatch):
        monkeypatch.setenv("PYRITE_AUTH_LOGIN_RATE_LIMIT", "1000/minute")
        monkeypatch.setenv("PYRITE_AUTH_LOGIN_RATE_LIMIT_PER_USERNAME", "900/minute")
        monkeypatch.setenv("PYRITE_AUTH_REGISTER_RATE_LIMIT", "800/minute")
        auth = config_module.load_config().settings.auth
        assert auth.login_rate_limit == "1000/minute"
        assert auth.login_rate_limit_per_username == "900/minute"
        assert auth.register_rate_limit == "800/minute"


class TestOAuthObeysTheRegistrationSwitches:
    def _profile(self, orgs=()):
        return OAuthProfile(provider="github", provider_id="77", username="octo", orgs=list(orgs))

    @pytest.mark.parametrize(
        "switches", [{"allow_registration": False}, {"require_invite_code": True}]
    )
    def test_an_unvetted_signup_is_refused_when_registration_is_closed(self, db_path, switches):
        with PyriteDB(db_path) as db:
            _insert_admin(db)
            service = AuthService(db, AuthConfig(enabled=True, **switches))
            with pytest.raises(ValueError):
                service.oauth_login(self._profile(), _PROVIDER)
        assert set(_roles(db_path)) == {"root"}

    @pytest.mark.parametrize(
        "provider",
        [
            OAuthProviderConfig(client_id="i", client_secret="s", allowed_orgs=["acme"]),
            OAuthProviderConfig(client_id="i", client_secret="s", org_tier_map={"acme": "write"}),
        ],
    )
    def test_a_vetted_signup_is_allowed_when_registration_is_closed(self, db_path, provider):
        with PyriteDB(db_path) as db:
            _insert_admin(db)
            service = AuthService(db, AuthConfig(enabled=True, allow_registration=False))
            user, _ = service.oauth_login(self._profile(["acme"]), provider)
        assert user["username"] == "octo"

    def test_an_org_map_miss_is_not_vetted(self, db_path):
        provider = OAuthProviderConfig(
            client_id="i", client_secret="s", org_tier_map={"acme": "write"}
        )
        with PyriteDB(db_path) as db:
            _insert_admin(db)
            service = AuthService(db, AuthConfig(enabled=True, allow_registration=False))
            with pytest.raises(ValueError):
                service.oauth_login(self._profile(["other"]), provider)

    @pytest.mark.control(reason="an existing OAuth user could always sign in again")
    def test_an_existing_oauth_user_still_signs_in_when_registration_is_closed(self, db_path):
        with PyriteDB(db_path) as db:
            _insert_admin(db)
            AuthService(db, AuthConfig(enabled=True)).oauth_login(self._profile(), _PROVIDER)
            closed = AuthService(db, AuthConfig(enabled=True, allow_registration=False))
            user, _ = closed.oauth_login(self._profile(), _PROVIDER)
        assert user["username"] == "octo"


class TestOpenRegistrationWarningCoversOAuth:
    def test_warning_names_github_signup_and_the_vetting_settings(self):
        from pyrite.config import PyriteConfig, Settings, open_registration_warning

        cfg = PyriteConfig(
            settings=Settings(
                auth=AuthConfig(
                    enabled=True,
                    providers={"github": OAuthProviderConfig(client_id="i", client_secret="s")},
                )
            )
        )
        text = open_registration_warning(cfg)
        assert text and "GitHub" in text and "allowed_orgs" in text

    @pytest.mark.control(reason="a closed instance never warned")
    def test_no_warning_when_registration_is_closed_even_with_github(self):
        from pyrite.config import PyriteConfig, Settings, open_registration_warning

        cfg = PyriteConfig(
            settings=Settings(
                auth=AuthConfig(
                    enabled=True,
                    allow_registration=False,
                    providers={"github": OAuthProviderConfig(client_id="i", client_secret="s")},
                )
            )
        )
        assert open_registration_warning(cfg) is None


class TestLoginTiming:
    def test_an_unknown_username_still_pays_for_a_password_check(self, db_path, monkeypatch):
        calls = []
        real = AuthService._verify_password

        def counting(self, password, pw_hash):
            calls.append(pw_hash)
            return real(self, password, pw_hash)

        monkeypatch.setattr(AuthService, "_verify_password", counting)
        with PyriteDB(db_path) as db:
            with pytest.raises(ValueError):
                AuthService(db, AuthConfig(enabled=True)).login("nobody", "password123")
        assert len(calls) == 1

    def test_a_password_login_to_an_oauth_account_pays_the_same_cost(self, db_path, monkeypatch):
        calls = []
        real = AuthService._verify_password

        def counting(self, password, pw_hash):
            calls.append(pw_hash)
            return real(self, password, pw_hash)

        profile = OAuthProfile(provider="github", provider_id="5", username="octo", orgs=[])
        with PyriteDB(db_path) as db:
            _insert_admin(db)
            service = AuthService(db, AuthConfig(enabled=True))
            service.oauth_login(profile, _PROVIDER)
            monkeypatch.setattr(AuthService, "_verify_password", counting)
            with pytest.raises(ValueError):
                service.login("octo", "password123")
        assert len(calls) == 1


def test_the_role_endpoint_refuses_a_non_boolean_global_access(two_kb_app):
    client, user_id = _signed_up(two_kb_app)
    r = two_kb_app["admin"].put(
        f"/auth/users/{user_id}/role", json={"role": "read", "global_access": "yes"}
    )
    assert r.status_code == 400
    assert "team-kb" not in _visible_kbs(client)


def test_admins_see_which_users_have_global_access(two_kb_app):
    _, user_id = _signed_up(two_kb_app)
    r = two_kb_app["admin"].get("/auth/users")
    assert r.status_code == 200, r.text
    users = {u["username"]: u for u in r.json()["users"]}
    assert users["newcomer"]["global_access"] is False
    two_kb_app["admin"].put(
        f"/auth/users/{user_id}/role", json={"role": "read", "global_access": True}
    )
    users = {u["username"]: u for u in two_kb_app["admin"].get("/auth/users").json()["users"]}
    assert users["newcomer"]["global_access"] is True
