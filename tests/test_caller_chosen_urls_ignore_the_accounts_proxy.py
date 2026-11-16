"""A request to a URL a caller chose never goes through the server account's
environment: no proxy variable (which can carry a proxy credential), no netrc
(ADR-0052 N2, the in-process HTTP half).

The URL checker and the web clipper fetch caller-chosen URLs. Each is run here
with ``http_proxy``/``https_proxy`` pointing at a local listener that records
any connection; the destination check is opened for the test's own address
only. The listener must see nothing.
"""

from __future__ import annotations

import socket
import threading

import anyio
import pytest

from pyrite.services import clipper, url_checker


@pytest.fixture
def account_proxy(monkeypatch):
    """A listener standing in for the account's proxy; records each connection."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    srv.settimeout(0.2)
    hits: list[bytes] = []
    stop = threading.Event()

    def serve():
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
            except OSError:
                continue
            conn.settimeout(1)
            try:
                hits.append(conn.recv(256))
            except OSError:
                hits.append(b"")
            conn.close()

    threading.Thread(target=serve, daemon=True).start()
    proxy = f"http://user:PROXY-SECRET@127.0.0.1:{srv.getsockname()[1]}"
    for var in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.setenv(var, proxy)
    for var in ("no_proxy", "NO_PROXY"):
        monkeypatch.delenv(var, raising=False)
    # The destination is a name that does not resolve: only a proxy could
    # carry the request anywhere.
    monkeypatch.setattr(url_checker, "_check_url_safe", lambda url: None)
    monkeypatch.setattr(clipper, "_check_url_safe", lambda url: None)
    yield hits
    stop.set()
    srv.close()


DEST = "http://pyrite-t1a-unresolvable.invalid/page"


def test_the_url_checker_never_uses_the_accounts_proxy(account_proxy, tmp_path):
    checker = url_checker.URLChecker.__new__(url_checker.URLChecker)
    checker.timeout = 2
    checker.check_url(DEST)
    assert account_proxy == []


def test_the_clipper_never_uses_the_accounts_proxy(account_proxy):
    svc = clipper.ClipperService.__new__(clipper.ClipperService)
    svc.timeout = 2

    async def clip():
        try:
            await svc.clip_url(DEST)
        except Exception:  # noqa: BLE001 - the fetch fails; the test is where it went
            pass

    anyio.run(clip)
    assert account_proxy == []
