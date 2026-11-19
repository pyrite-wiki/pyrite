"""The clipper's decoded-size cap bounds memory at every decode step.

A response may carry any stack of content codings. The cap is on decoded
bytes and must hold per decode step: one gzip layer is decoded in bounded
steps, a stack of codings (or one the clipper does not decode) is refused
before anything is inflated.
"""

from __future__ import annotations

import asyncio
import gzip
import socket
import subprocess
import sys
import textwrap
import zlib
from pathlib import Path

import httpx
import pytest

from pyrite.exceptions import PyriteError
from pyrite.services.clipper import ClipperService

ROOT = Path(__file__).resolve().parent.parent
CAP = 5 * 1024 * 1024


def _serve(monkeypatch, body: bytes, encoding: str | None):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))
        ],
    )
    real = httpx.AsyncClient

    def handler(request):
        headers = {"content-encoding": encoding} if encoding else {}
        return httpx.Response(200, headers=headers, stream=httpx.ByteStream(body))

    def factory(**kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


def _clip():
    return asyncio.run(ClipperService().clip_url("https://page.example/x"))


HTML = b"<html><head><title>Zipped</title></head><body><p>Hello zipped world</p></body></html>"


@pytest.mark.parametrize(
    ("body", "coding"),
    [
        (gzip.compress(HTML, mtime=0), "gzip"),
        (gzip.compress(HTML, mtime=0), "x-gzip"),
        (zlib.compress(HTML), "deflate"),
        (zlib.compress(HTML)[2:-4], "deflate"),  # raw deflate, as some servers send
        (HTML, "identity"),
        (HTML, None),
    ],
    ids=["gzip", "x-gzip", "deflate", "raw-deflate", "identity", "none"],
)
@pytest.mark.control(reason="control: single-coding pages were read before this change")
def test_a_single_coding_or_none_is_decoded(monkeypatch, body, coding):
    _serve(monkeypatch, body, coding)
    result = _clip()
    assert result.title == "Zipped" and "Hello zipped world" in result.body


@pytest.mark.control(reason="control: a single-layer bomb was refused at the round-1 head")
def test_a_single_gzip_bomb_is_refused_at_the_cap(monkeypatch):
    _serve(monkeypatch, gzip.compress(b"a" * (CAP * 6)), "gzip")
    with pytest.raises(PyriteError) as exc:
        _clip()
    assert exc.value.error_code == "CLIP_TOO_LARGE"


@pytest.mark.parametrize("coding", ["gzip, gzip", "gzip,deflate", "br", "zstd", "gzip, br"])
def test_a_stack_or_an_undecoded_coding_is_refused(monkeypatch, coding):
    _serve(monkeypatch, gzip.compress(gzip.compress(HTML, mtime=0), mtime=0), coding)
    with pytest.raises(ValueError, match="content-encoding"):
        _clip()


PROBE = textwrap.dedent(
    """
    import asyncio, gzip, resource, socket, sys, zlib
    sys.path.insert(0, {root!r})
    import httpx
    from pyrite.services.clipper import ClipperService

    # 600 MB of zeros under two gzip layers: the outer layer inflates to the
    # inner (about 600 KB), the inner to 600 MB.
    co = zlib.compressobj(9, zlib.DEFLATED, 31)
    block = b"\\0" * (1 << 20)
    inner = b"".join([co.compress(block) for _ in range(600)] + [co.flush()])
    outer = gzip.compress(inner, 9)
    assert len(outer) < 4096, len(outer)

    socket.getaddrinfo = lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
    real = httpx.AsyncClient
    def factory(**kw):
        kw["transport"] = httpx.MockTransport(lambda r: httpx.Response(
            200, headers={{"content-encoding": "gzip, gzip"}}, stream=httpx.ByteStream(outer)))
        return real(**kw)
    httpx.AsyncClient = factory

    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    try:
        asyncio.run(ClipperService().clip_url("https://page.example/x"))
    except Exception as e:
        print("raised", type(e).__name__)
    after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # ru_maxrss is bytes on macOS, kilobytes on Linux
    scale = 1 if sys.platform == "darwin" else 1024
    print("grew_mb", (after - before) * scale / 1e6)
    """
)


@pytest.mark.timeout(120)
def test_stacked_encodings_do_not_inflate_in_memory():
    out = subprocess.run(
        [sys.executable, "-c", PROBE.format(root=str(ROOT))],
        capture_output=True,
        text=True,
        timeout=100,
    )
    assert out.returncode == 0, out.stderr
    grew = float(out.stdout.split("grew_mb")[1])
    assert grew < 150, out.stdout
