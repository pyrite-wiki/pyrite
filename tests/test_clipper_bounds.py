"""The clipper's work is bounded: by input size, bytes read, address and message.

Properties (each fixture is written by hand from the HTML and HTTP specs):

- extraction takes time linear in the page and runs off the event loop;
- reading stops at ``MAX_CLIP_BYTES`` counted *after* decompression;
- a destination is refused unless its address is globally routable, on every
  hop (the carrier-grade NAT range ``100.64.0.0/10`` is not);
- ``CLIP_FAILED`` carries Pyrite's own message; the detail goes to the log.
"""

from __future__ import annotations

import asyncio
import gzip
import logging
import socket
import time

import httpx
import pytest

from pyrite.exceptions import ClipperBlockedHostError, PyriteError
from pyrite.services.clipper import ClipperService

MAX_CLIP_BYTES = 5 * 1024 * 1024  # the cap, written out


def _public_dns(monkeypatch, addr="93.184.216.34"):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (addr, 0))],
    )


def _serve(monkeypatch, handler):
    real = httpx.AsyncClient

    def factory(**kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


def _clip(url="https://page.example/x"):
    return asyncio.run(ClipperService().clip_url(url))


# --- linear extraction ----------------------------------------------------------

ADVERSARIAL = {
    "unclosed-meta": '<meta name="description" ' * 1000,
    "unclosed-script": "<script>" * 25000,
    "unclosed-nav": "<nav><header>" * 3000,
    "unclosed-og": '<meta property="og:description" ' * 1000,
}


@pytest.mark.timeout(120)  # the unfixed regexes take far longer; fail rather than hang
@pytest.mark.parametrize("name", sorted(ADVERSARIAL))
def test_extraction_of_a_hostile_page_is_fast(monkeypatch, name):
    _public_dns(monkeypatch)
    page = ADVERSARIAL[name].encode()
    _serve(monkeypatch, lambda request: httpx.Response(200, content=page))
    started = time.monotonic()
    _clip()
    assert time.monotonic() - started < 2.0


@pytest.mark.control(
    reason="control: extraction gives the same title, description and body as before"
)
def test_extraction_still_reads_title_description_and_content(monkeypatch):
    _public_dns(monkeypatch)
    html = (
        "<html><head><title>T</title>"
        '<meta name="description" content="About it">'
        "<style>p{color:red}</style></head><body>"
        "<nav>menu</nav><header>head</header><h1>Hello</h1>"
        "<script>alert(1)</script><p>Body text</p><footer>foot</footer></body></html>"
    )
    _serve(monkeypatch, lambda request: httpx.Response(200, text=html))
    result = _clip()
    assert result.title == "T"
    assert result.description == "About it"
    assert "Hello" in result.body and "Body text" in result.body
    for gone in ("menu", "head\n", "alert", "color:red", "foot"):
        assert gone not in result.body


@pytest.mark.control(
    reason="control: extraction gives the same title, description and body as before"
)
def test_og_description_is_the_fallback(monkeypatch):
    _public_dns(monkeypatch)
    html = '<head><meta property="og:description" content="Shared"></head><p>x</p>'
    _serve(monkeypatch, lambda request: httpx.Response(200, text=html))
    assert _clip().description == "Shared"


def test_extraction_does_not_hold_the_event_loop(monkeypatch):
    _public_dns(monkeypatch)
    page = ("<div><p>paragraph with <b>bold</b> and <a href='/x'>link</a></p></div>" * 60000)[
        :MAX_CLIP_BYTES
    ].encode()
    _serve(monkeypatch, lambda request: httpx.Response(200, content=page))

    async def go():
        gaps: list[float] = []
        stop = asyncio.Event()

        async def ticker():
            last = time.monotonic()
            while not stop.is_set():
                await asyncio.sleep(0.01)
                now = time.monotonic()
                gaps.append(now - last)
                last = now

        task = asyncio.create_task(ticker())
        await asyncio.sleep(0.05)
        await ClipperService().clip_url("https://page.example/x")
        stop.set()
        await task
        return max(gaps)

    assert asyncio.run(go()) < 1.0  # on the loop the gap is the whole parse (3 s+)


# --- byte cap after decompression -----------------------------------------------


@pytest.mark.control(reason="control: a single-layer bomb was refused at the round-1 head")
def test_a_decompression_bomb_is_refused(monkeypatch):
    _public_dns(monkeypatch)
    bomb = gzip.compress(b"a" * (MAX_CLIP_BYTES * 4))
    assert len(bomb) < MAX_CLIP_BYTES // 100
    _serve(
        monkeypatch,
        lambda request: httpx.Response(
            200, headers={"content-encoding": "gzip"}, stream=httpx.ByteStream(bomb)
        ),
    )
    with pytest.raises(PyriteError) as exc:
        _clip()
    assert exc.value.error_code == "CLIP_TOO_LARGE"


def test_reading_stops_at_the_cap_and_does_not_drain_the_stream(monkeypatch):
    _public_dns(monkeypatch)
    pulled = {"n": 0}
    chunk = b"x" * 65536

    async def endless():
        for _ in range(400):  # 25 MB: finite, so a reader with no cap ends too
            pulled["n"] += 1
            yield chunk

    _serve(monkeypatch, lambda request: httpx.Response(200, content=endless()))
    with pytest.raises(PyriteError) as exc:
        _clip()
    assert exc.value.error_code == "CLIP_TOO_LARGE"
    assert pulled["n"] * len(chunk) <= MAX_CLIP_BYTES + 4 * len(chunk)


@pytest.mark.control(reason="control: a page under the cap was always read whole")
def test_a_page_under_the_cap_is_read_whole(monkeypatch):
    _public_dns(monkeypatch)
    body = "<title>Big</title>" + "<p>word</p>" * 200000
    assert len(body) < MAX_CLIP_BYTES
    _serve(monkeypatch, lambda request: httpx.Response(200, text=body))
    assert _clip().title == "Big"


# --- destinations -----------------------------------------------------------------


_ALREADY_REFUSED = pytest.mark.control(
    reason="control: the earlier blocklist (private/reserved/link-local) refused this too"
)


@pytest.mark.parametrize(
    "host",
    [
        "100.64.0.1",  # carrier-grade NAT
        "100.127.255.254",
        "[::ffff:100.64.0.1]",  # IPv4-mapped CGNAT
        pytest.param("192.0.0.8", marks=_ALREADY_REFUSED),  # IETF protocol assignments
        pytest.param("[::ffff:10.0.0.1]", marks=_ALREADY_REFUSED),  # IPv4-mapped private
        pytest.param("[64:ff9b::a00:1]", marks=_ALREADY_REFUSED),  # NAT64 around 10.0.0.1
        pytest.param("[2002:a00:1::]", marks=_ALREADY_REFUSED),  # 6to4 around 10.0.0.1
        pytest.param("[fd00::1]", marks=_ALREADY_REFUSED),
    ],
)
def test_a_non_global_literal_is_refused(monkeypatch, host):
    # If the check lets it through, the fetch is answered here, never sent out.
    _serve(monkeypatch, lambda request: httpx.Response(200, text="<p>reached</p>"))
    with pytest.raises(ClipperBlockedHostError):
        _clip(f"http://{host}/")


def test_a_hostname_resolving_to_cgnat_is_refused(monkeypatch):
    _public_dns(monkeypatch, "100.64.0.9")
    _serve(monkeypatch, lambda request: httpx.Response(200, text="<p>reached</p>"))
    with pytest.raises(ClipperBlockedHostError):
        _clip("https://sneaky.example/")


def test_a_redirect_into_cgnat_is_refused(monkeypatch):
    _public_dns(monkeypatch)
    _serve(
        monkeypatch,
        lambda request: httpx.Response(302, headers={"Location": "http://100.64.0.1/"}),
    )
    with pytest.raises(ClipperBlockedHostError):
        _clip()


def test_the_address_check_does_not_resolve_on_the_event_loop(monkeypatch):
    """getaddrinfo is a blocking call; it runs in a worker thread."""
    import threading

    seen = {}

    def fake(host, port, *a, **k):
        seen["thread"] = threading.get_ident()
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    _serve(monkeypatch, lambda request: httpx.Response(200, text="<p>ok</p>"))

    async def go():
        loop_thread = threading.get_ident()
        await ClipperService().clip_url("https://page.example/")
        return loop_thread

    assert asyncio.run(go()) != seen["thread"]


# --- the endpoint's messages --------------------------------------------------------


@pytest.fixture
def clip_client(tmp_path):
    from pyrite.config import KBConfig
    from tests._world import build_world

    w = build_world(
        tmp_path / "w", kbs=[KBConfig(name="k", path=tmp_path / "w" / "k", kb_type="generic")]
    )
    yield w.client
    w.close()


def test_clip_failed_carries_pyrites_message_and_logs_the_detail(monkeypatch, clip_client, caplog):
    async def boom(self, url, title=None):
        raise RuntimeError("connect to 10.9.8.7:5432 refused /srv/secret/path")

    monkeypatch.setattr(ClipperService, "clip_url", boom)
    with caplog.at_level(logging.WARNING):
        resp = clip_client.post("/api/clip", json={"url": "https://page.example/", "kb": "k"})
    assert resp.status_code == 502
    detail = resp.json()["detail"]
    assert detail["code"] == "CLIP_FAILED"
    assert "10.9.8.7" not in resp.text and "secret" not in resp.text
    assert "10.9.8.7" in caplog.text


def test_a_blocked_host_answer_does_not_reveal_what_the_name_resolved_to(monkeypatch, clip_client):
    _public_dns(monkeypatch, "10.1.2.3")
    resp = clip_client.post("/api/clip", json={"url": "https://internal.example/", "kb": "k"})
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "CLIPPER_BLOCKED_HOST"
    assert "10.1.2.3" not in resp.text


def test_a_page_past_the_cap_is_413_clip_too_large(monkeypatch, clip_client):
    _public_dns(monkeypatch)
    page = b"a" * (MAX_CLIP_BYTES + 1000)
    _serve(monkeypatch, lambda request: httpx.Response(200, content=page))
    resp = clip_client.post("/api/clip", json={"url": "https://page.example/", "kb": "k"})
    assert resp.status_code == 413
    assert resp.json()["detail"]["code"] == "CLIP_TOO_LARGE"


def test_create_failed_names_no_exception_text_and_the_create_runs_off_the_loop(
    monkeypatch, clip_client, caplog
):
    import threading

    from pyrite.services.kb_service import KBService

    _public_dns(monkeypatch)
    _serve(monkeypatch, lambda request: httpx.Response(200, text="<title>T</title><p>x</p>"))
    seen = {}

    def boom(self, *a, **k):
        seen["thread"] = threading.get_ident()
        raise RuntimeError("write failed at /srv/secret/kb with token abc123")

    monkeypatch.setattr(KBService, "create", boom)
    real_clip = ClipperService.clip_url

    async def noting(self, url, title=None):
        seen["loop"] = threading.get_ident()  # clip_url runs on the event loop
        return await real_clip(self, url, title)

    monkeypatch.setattr(ClipperService, "clip_url", noting)

    with caplog.at_level(logging.WARNING):
        resp = clip_client.post("/api/clip", json={"url": "https://page.example/", "kb": "k"})
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "CREATE_FAILED"
    assert "secret" not in resp.text and "abc123" not in resp.text
    assert "abc123" in caplog.text
    assert seen["thread"] != seen["loop"]


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param(
            ["fe80::1%eth0"],
            marks=pytest.mark.control(reason="control: Python parses scoped addresses"),
        ),
        [],
        ["not-an-address"],
    ],
)
def test_an_address_the_check_cannot_read_is_refused(monkeypatch, answer):
    """The check fails closed: an unreadable or missing answer is no 'yes'."""
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *a, **k: [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", (addr, 0)) for addr in answer
        ],
    )
    _serve(monkeypatch, lambda request: httpx.Response(200, text="<p>reached</p>"))
    with pytest.raises(ClipperBlockedHostError):
        _clip("https://odd.example/")
