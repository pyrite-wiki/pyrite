"""The session cap holds under concurrent logins (#435).

Property: at every commit point a user holds at most
``auth.max_sessions_per_user`` sessions -- for password logins, OAuth logins,
and the two racing each other for the same user. Every token a login returned
either still verifies or was evicted, and each eviction is announced as a
``CredentialChange`` after its commit, so the evicted session's sockets close
(#433's contract).

Pattern from ``tests/test_task_claim_concurrency.py``: a start barrier so the
logins actually contend, and one deadline for the whole group rather than a
fixed per-thread timeout. Threads, not processes: the server serves logins
from worker threads of one process, and each thread here opens its own
``PyriteDB`` on the one file database, as separate requests would.
"""

import hashlib
import re
import sqlite3
import threading
import time

import pytest

from sqlalchemy import event

from pyrite.config import AuthConfig, OAuthProviderConfig
from pyrite.services import credential_events
from pyrite.services.auth_service import AuthService
from pyrite.services.oauth_providers import OAuthProfile
from pyrite.storage.database import PyriteDB

CAP = 2
N_LOGINS = 10  # > CAP, so most logins must evict

# Each statement on the session table waits this long before it runs, so a
# login that has read the session table is still between its steps when the
# others read it. Without the widening the steps are sub-millisecond apart and
# the race is rarely observed; with it, any interleaving the code allows shows.
_STEP_DELAY = 0.02
_SESSION_TABLE = re.compile(r"\bsession\b", re.IGNORECASE)

_GROUP_DEADLINE = 180.0
_BARRIER_TIMEOUT = 150.0

_PROFILE = OAuthProfile(provider="github", provider_id="4242", username="octo", orgs=[])
_PROVIDER = OAuthProviderConfig(client_id="id", client_secret="secret", default_tier="read")

# A user both login paths reach: a local user with a password whose
# (auth_provider, provider_id) is also what an OAuth profile resolves to.
_MIXED_PROFILE = OAuthProfile(provider="local", provider_id="mixed-1", username="alice", orgs=[])


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@pytest.fixture
def evicted():
    """Session hashes announced as ended, recorded from any thread."""
    seen: list[str] = []
    lock = threading.Lock()

    def listener(change):
        if change.session_hash is not None:
            with lock:
                seen.append(change.session_hash)

    credential_events.subscribe(listener)
    try:
        yield seen
    finally:
        credential_events.unsubscribe(listener)


@pytest.fixture
def env(tmp_path):
    db_path = tmp_path / "index.db"
    config = AuthConfig(enabled=True, max_sessions_per_user=CAP)
    with PyriteDB(db_path) as db:
        service = AuthService(db, config)
        # The operator path: sign-up is closed until an admin exists (#13).
        alice = service.create_user("alice", "password123", role="admin")
        # Make alice reachable through oauth_login too (see _MIXED_PROFILE).
        db.execute_write_sql(
            "UPDATE local_user SET provider_id = :pid WHERE id = :id",
            {"pid": _MIXED_PROFILE.provider_id, "id": alice["id"]},
        )
        octo, _ = service.oauth_login(_PROFILE, _PROVIDER)
        service.logout_all(octo["id"])
        yield {"db_path": db_path, "config": config, "alice": alice["id"], "octo": octo["id"]}


def _race(db_path, config, calls, user_id):
    """Run each ``call(service)`` on its own thread and PyriteDB, all released
    together. Returns the tokens the calls returned; fails on any exception.

    Before each statement on the session table it also samples the user's
    *committed* session count from a separate connection, so a commit point
    above the cap fails the test even if a later eviction repairs it."""
    dbs = [PyriteDB(db_path) for _ in calls]
    committed_counts: list[int] = []

    def widen(conn, cursor, statement, *args):
        if _SESSION_TABLE.search(statement):
            time.sleep(_STEP_DELAY)
            committed_counts.append(len(_session_hashes(db_path, user_id)))

    for db in dbs:
        event.listen(db.engine, "before_cursor_execute", widen)
    barrier = threading.Barrier(len(calls), timeout=30)
    tokens: list[str] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def run(db, call):
        try:
            service = AuthService(db, config)
            barrier.wait(timeout=_BARRIER_TIMEOUT)
            _, token = call(service)
            with lock:
                tokens.append(token)
        except BaseException as e:  # noqa: BLE001 -- reported below
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
        assert not still_running, f"login thread(s) still running: {still_running}"
    finally:
        for db in dbs:
            db.close()
    assert not errors, f"concurrent login raised: {errors!r}"
    assert committed_counts and max(committed_counts) <= CAP, (
        f"a commit point left the user {max(committed_counts)} sessions, cap is {CAP}"
    )
    return tokens


def _session_hashes(db_path, user_id) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT token_hash FROM session WHERE user_id = ?", (user_id,))
        return {r[0] for r in rows}
    finally:
        conn.close()


def _assert_cap_held(db_path, user_id, tokens, evicted):
    remaining = _session_hashes(db_path, user_id)
    assert len(remaining) <= CAP, f"user holds {len(remaining)} sessions, cap is {CAP}"
    returned = {_hash(t) for t in tokens}
    assert remaining <= returned
    # Every token handed out still verifies or was announced as evicted.
    unaccounted = returned - remaining - set(evicted)
    assert not unaccounted, f"{len(unaccounted)} returned session(s) vanished unannounced"
    # And nothing still live was announced as evicted.
    assert not (remaining & set(evicted))


def _password(service):
    return service.login("alice", "password123")


def _oauth(profile):
    return lambda service: service.oauth_login(profile, _PROVIDER)


class TestSessionCapUnderConcurrency:
    def test_concurrent_password_logins(self, env, evicted):
        tokens = _race(env["db_path"], env["config"], [_password] * N_LOGINS, env["alice"])
        assert len(tokens) == N_LOGINS
        _assert_cap_held(env["db_path"], env["alice"], tokens, evicted)

    def test_concurrent_oauth_logins(self, env, evicted):
        tokens = _race(env["db_path"], env["config"], [_oauth(_PROFILE)] * N_LOGINS, env["octo"])
        assert len(tokens) == N_LOGINS
        _assert_cap_held(env["db_path"], env["octo"], tokens, evicted)

    def test_concurrent_mixed_password_and_oauth_logins(self, env, evicted):
        calls = [_password, _oauth(_MIXED_PROFILE)] * (N_LOGINS // 2)
        tokens = _race(env["db_path"], env["config"], calls, env["alice"])
        assert len(tokens) == N_LOGINS
        _assert_cap_held(env["db_path"], env["alice"], tokens, evicted)


class TestSessionCapSequential:
    """The (cap+1)th login still evicts the oldest, announced after commit."""

    @pytest.mark.control(reason="sequential eviction already worked before #435")
    def test_next_login_evicts_oldest_and_announces_it(self, env, evicted):
        with PyriteDB(env["db_path"]) as db:
            service = AuthService(db, env["config"])
            _, t1 = service.login("alice", "password123")
            _, t2 = service.login("alice", "password123")
            _, t3 = service.oauth_login(_MIXED_PROFILE, _PROVIDER)
            assert service.verify_session(t1) is None
            assert service.verify_session(t2) is not None
            assert service.verify_session(t3) is not None
        assert evicted == [_hash(t1)]

    @pytest.mark.control(reason="#433 already published after the commit")
    def test_eviction_is_announced_after_it_commits(self, env):
        """A listener reading through another connection no longer finds the
        evicted session: the announcement follows the commit."""
        still_visible: list[bool] = []

        def listener(change):
            if change.session_hash is not None:
                still_visible.append(
                    change.session_hash in _session_hashes(env["db_path"], env["alice"])
                )

        with PyriteDB(env["db_path"]) as db:
            service = AuthService(db, env["config"])
            for _ in range(CAP):
                service.login("alice", "password123")
            credential_events.subscribe(listener)
            try:
                service.login("alice", "password123")
            finally:
                credential_events.unsubscribe(listener)
        assert still_visible == [False]

    @pytest.mark.control(reason="before #435 the new session was inserted after the eviction")
    def test_login_never_evicts_the_session_it_returns(self, env, evicted):
        """Existing sessions stamped later than now (clock skew, or a login
        that took its timestamp later but committed first) still go before
        the session being created."""
        with PyriteDB(env["db_path"]) as db:
            service = AuthService(db, env["config"])
            _, t1 = service.login("alice", "password123")
            _, t2 = service.login("alice", "password123")
            db.execute_write_sql(
                "UPDATE session SET created_at = '9999-01-01T00:00:00+00:00' WHERE user_id = :u",
                {"u": env["alice"]},
            )
            _, t3 = service.login("alice", "password123")
            assert service.verify_session(t3) is not None
        assert len(_session_hashes(env["db_path"], env["alice"])) == CAP
        assert len(evicted) == 1 and evicted[0] in {_hash(t1), _hash(t2)}

    @pytest.mark.control(reason="a cap below one already left the new session before #435")
    def test_cap_below_one_still_leaves_the_new_session(self, env, evicted):
        with PyriteDB(env["db_path"]) as db:
            service = AuthService(db, AuthConfig(enabled=True, max_sessions_per_user=0))
            _, t1 = service.login("alice", "password123")
            _, t2 = service.login("alice", "password123")
            assert service.verify_session(t1) is None
            assert service.verify_session(t2) is not None
        assert evicted == [_hash(t1)]

    @pytest.mark.control(reason="dev evicted before inserting, so a failed eviction left nothing")
    def test_failed_eviction_leaves_no_session_behind(self, env, evicted, monkeypatch):
        """If the eviction fails, the login fails whole: its session is not
        left pending on the connection for a later commit to publish."""
        with PyriteDB(env["db_path"]) as db:
            service = AuthService(db, env["config"])

            def boom(*args, **kwargs):
                raise RuntimeError("eviction failed")

            monkeypatch.setattr(service, "_enforce_max_sessions", boom)
            with pytest.raises(RuntimeError):
                service.login("alice", "password123")
            monkeypatch.undo()
            db.session.commit()  # a later write on the same connection
            assert db.execute_sql(
                "SELECT COUNT(*) AS n FROM session WHERE user_id = :u", {"u": env["alice"]}
            ) == [{"n": 0}]
        assert evicted == []
