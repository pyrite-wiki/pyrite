"""The QA URL checker contacts only public addresses, on every redirect hop.

Property: a source URL (or a redirect from one) naming a loopback, private or
otherwise non-global address is refused before any request reaches it.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from pyrite.services.clipper import _check_url_safe as check_url_safe
from pyrite.services.url_checker import URLChecker


class _Server(HTTPServer):
    def __init__(self, redirect_to: str | None = None):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.redirect_to = redirect_to
        self.hits = 0
        threading.Thread(target=self.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}/x"


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _answer(self):
        self.server.hits += 1
        if self.server.redirect_to:
            self.send_response(302)
            self.send_header("Location", self.server.redirect_to)
        else:
            self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_GET = do_HEAD = _answer  # noqa: N815


@pytest.fixture
def servers():
    made: list[_Server] = []

    def make(redirect_to=None):
        made.append(_Server(redirect_to))
        return made[-1]

    yield make
    for s in made:
        s.shutdown()
        s.server_close()


def test_a_loopback_url_is_refused_without_a_request(servers):
    target = servers()
    result = URLChecker(db=None).check_url(target.url)
    assert result.ok is False
    assert target.hits == 0


def test_a_redirect_to_a_non_public_address_is_refused(servers, monkeypatch):
    target = servers()
    hop = servers(redirect_to=target.url)
    real = check_url_safe

    def allow_the_first_hop_only(url):
        if url == hop.url:
            return
        real(url)

    # The first destination stands in for a public site; the redirect it
    # issues must meet the real rule.
    monkeypatch.setattr(
        "pyrite.services.url_checker._check_url_safe", allow_the_first_hop_only, raising=False
    )
    result = URLChecker(db=None).check_url(hop.url)
    assert result.ok is False
    assert target.hits == 0
    assert hop.hits == 1


@pytest.mark.control(reason="the rule must not refuse what it should allow: a public address")
def test_a_public_address_passes_the_rule(monkeypatch):
    check_url_safe("http://93.184.216.34/")


def test_a_name_that_does_not_resolve_is_reported_as_unresolvable(monkeypatch):
    import socket

    def refuse(*a, **k):
        raise socket.gaierror(8, "nodename nor servname provided")

    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    result = URLChecker(db=None).check_url("http://no-such-host.invalid/x")
    assert result.ok is False
    assert result.error == "host could not be resolved"


@pytest.mark.control(
    reason="the merged clipper rule already refuses it; kept as a guard for this checker"
)
def test_an_address_the_rule_cannot_read_is_refused(monkeypatch):
    import socket

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET6, 1, 6, "", ("fe80::1%en0", 0, 0, 1))],
    )
    result = URLChecker(db=None).check_url("http://scoped.example/x")
    assert result.ok is False
    assert result.error == "destination is not a public address"
