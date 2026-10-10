"""Real SQLite contention must not block the auth event loop."""

import re
import pytest
from tests import test_auth_event_loop_blocking as blocking
from tests.test_auth_event_loop_blocking import _long_busy_timeout, oauth_env  # noqa: F401

CASES = [
    ("GET", "/auth/me", None),
    ("POST", "/auth/logout", None),
    ("GET", "/auth/github", None),
    ("GET", "/auth/github/connect", None),
    ("GET", "/auth/github/status", None),
    ("DELETE", "/auth/github/connect", None),
    ("GET", "/auth/api-keys", None),
    ("POST", "/auth/api-keys", {"provider": "openai", "api_key": "test-key"}),
    ("DELETE", "/auth/api-keys/openai", None),
]


@pytest.mark.parametrize(("method", "path", "body"), CASES)
def test_auth_write_wait_keeps_concurrent_request_responsive(
    oauth_env, monkeypatch, method, path, body  # noqa: F811
):
    client, db_path, db = oauth_env
    login = client.post("/auth/login", json={"username": "alice", "password": "password123"})
    assert login.status_code == 200
    # OAuth start writes oauth_state; logout deletes session. Observe all DML,
    # not just the login helper's INSERT/UPDATE session pattern.
    monkeypatch.setattr(
        blocking, "_WRITE_STATEMENT", re.compile(r"^\s*(?:INSERT|UPDATE|DELETE)\b", re.I)
    )
    monkeypatch.setattr(blocking, "WAIT_WHILE_LOCKED_S", 3.0)
    responses = []
    errors = []

    def fire():
        try:
            kwargs = {} if body is None else {"json": body}
            responses.append(client.request(method, path, follow_redirects=False, **kwargs))
        except Exception as exc:
            errors.append(exc)

    blocking._assert_request_does_not_block_the_loop(client, db_path, db, fire)
    assert not errors, errors
    assert responses and responses[0].status_code < 500
