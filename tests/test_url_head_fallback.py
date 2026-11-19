"""A GET-capable source is live even when it refuses HEAD."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from pyrite.services.url_checker import URLChecker


@pytest.mark.parametrize(
    "head_status",
    [
        405,
        501,
        pytest.param(404, marks=pytest.mark.control(reason="a real missing source remains broken")),
    ],
)
def test_method_refusal_retries_get_but_missing_sources_do_not(head_status, monkeypatch):
    # The server is on loopback; the destination rule has its own tests.
    monkeypatch.setattr("pyrite.services.url_checker._check_url_safe", lambda url: None)
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_HEAD(self):
            calls.append("HEAD")
            self.send_response(head_status)
            self.end_headers()

        def do_GET(self):
            calls.append("GET")
            self.send_response(200)
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        result = URLChecker(None, timeout=2).check_url(
            f"http://127.0.0.1:{server.server_port}/source"
        )
        assert result.ok == (head_status != 404)
        assert result.status_code == (404 if head_status == 404 else 200)
        assert calls == (["HEAD"] if head_status == 404 else ["HEAD", "GET"])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()
