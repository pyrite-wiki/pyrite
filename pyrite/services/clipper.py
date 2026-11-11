"""Web Clipper service — fetch URL, extract content, convert to Markdown."""

import logging
import re
import zlib
from dataclasses import dataclass

import anyio.to_thread
import httpx
from bs4 import BeautifulSoup

from ..exceptions import ClipperTooLargeError
from ..utils.public_destination import check_url_safe as _check_url_safe

logger = logging.getLogger(__name__)

#: The most decoded bytes of a page the clipper reads (see ``clip_url``).
MAX_CLIP_BYTES = 5 * 1024 * 1024


async def _reject_blocked_request(request: httpx.Request) -> None:
    """Validate every request in the chain, redirect hops included (#219).

    httpx awaits a request hook before each request it sends, so a redirect to
    a blocked address is refused rather than followed, and the response the
    attacker aimed for is never fetched. The refusal is the same
    ``ClipperBlockedHostError`` a directly blocked URL raises, so a caller
    cannot tell "blocked directly" from "blocked after a redirect".
    """
    # Name resolution blocks; keep it off the event loop.
    await anyio.to_thread.run_sync(_check_url_safe, str(request.url))


@dataclass
class ClipResult:
    """Result of clipping a URL."""

    title: str
    body: str
    source_url: str
    description: str = ""


#: Elements dropped before conversion: they are page chrome, not content.
_DROPPED = ["script", "style", "nav", "footer", "header", "aside", "noscript"]


def _meta_content(soup: BeautifulSoup, attr: str, value: str) -> str:
    for tag in soup.find_all("meta"):
        if str(tag.get(attr, "")).lower() == value:
            content = tag.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()
    return ""


def _extract(html: str) -> tuple[str, str, str]:
    """``(title, description, markdown)`` of a page, in one linear parse.

    The page is parsed once into a tree (``html.parser`` through
    BeautifulSoup, linear in the input), the title and meta description are
    read from it, the chrome elements are removed, and what is left is
    converted. Time is linear in the page: no step backtracks.
    """
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text().strip() if soup.title else ""
    description = _meta_content(soup, "name", "description") or _meta_content(
        soup, "property", "og:description"
    )
    for tag in soup.find_all(_DROPPED):
        if not tag.decomposed:  # an outer drop already took its inner ones
            tag.decompose()
    try:
        from markdownify import MarkdownConverter

        body = MarkdownConverter(heading_style="ATX", strip=["img"]).convert_soup(soup)
    except ImportError:
        body = soup.get_text()
    return title, description, re.sub(r"\n{3,}", "\n\n", body).strip()


class ClipperService:
    """Fetches URLs and converts HTML to Markdown entries."""

    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout

    async def clip_url(self, url: str, title: str | None = None) -> ClipResult:
        """Fetch a URL and convert to Markdown.

        Args:
            url: The URL to clip.
            title: Optional override title.

        Returns:
            ClipResult with title, body (Markdown), source_url, description.

        Raises:
            ClipperBlockedHostError: if ``url`` uses a non-http(s) scheme
                or resolves to a loopback / link-local / RFC1918 private /
                reserved address. The check runs before the first request, and
                again for every redirect hop via ``_reject_blocked_request``.
        """
        await anyio.to_thread.run_sync(_check_url_safe, url)

        # One deadline for the whole fetch: the client's timeout is per read,
        # and a server that trickles bytes would otherwise hold it open.
        with anyio.fail_after(self.timeout * 2):
            async with httpx.AsyncClient(
                timeout=self.timeout,
                follow_redirects=True,
                # Only codings `_read_capped` decodes within the cap.
                headers={"User-Agent": "Pyrite-Clipper/1.0", "Accept-Encoding": "gzip, deflate"},
                event_hooks={"request": [_reject_blocked_request]},
            ) as client:
                async with client.stream("GET", url) as response:
                    response.raise_for_status()
                    raw = await _read_capped(response)
                    encoding = response.encoding or "utf-8"

        html = raw.decode(encoding, errors="replace")
        # Parsing and conversion scale with the page: off the event loop.
        extracted_title, description, body = await anyio.to_thread.run_sync(_extract, html)

        return ClipResult(
            title=title or extracted_title or url,
            body=body,
            source_url=url,
            description=description,
        )


_WINDOW_BITS = {"gzip": 31, "x-gzip": 31, "deflate": 15}
_STEP = 64 * 1024


def _too_large() -> ClipperTooLargeError:
    return ClipperTooLargeError(
        f"The page is larger than {MAX_CLIP_BYTES // (1024 * 1024)} MiB and was not clipped"
    )


async def _raw_chunks(response: httpx.Response):
    """The response body as it came off the wire, chunk by chunk. A response
    built in memory (a test transport) has no stream left to read; its content
    is the one chunk."""
    if response.is_stream_consumed:
        yield response.content
        return
    async for chunk in response.aiter_raw():
        yield chunk


async def _read_capped(response: httpx.Response) -> bytes:
    """The response body, read as a stream and refused past ``MAX_CLIP_BYTES``.

    The cap counts decoded bytes and bounds memory at every step. The body is
    read raw and decoded here, in ``_STEP``-byte outputs
    (``zlib.decompressobj.decompress(data, max_length)``), so no input chunk
    inflates beyond ``_STEP`` before the cap is checked. One coding is
    supported (gzip or deflate, as requested); a response with a stack of
    codings, or one this does not decode (br, zstd), is refused with a
    ``ValueError`` (the endpoint answers ``CLIP_FAILED``).
    """
    # A response built in memory (a test transport) arrives already decoded.
    codings = (
        []
        if response.is_stream_consumed
        else [
            c.strip().lower()
            for c in response.headers.get("content-encoding", "").split(",")
            if c.strip() and c.strip().lower() != "identity"
        ]
    )
    if len(codings) > 1 or (codings and codings[0] not in _WINDOW_BITS):
        raise ValueError(f"unsupported content-encoding {codings!r}")

    parts: list[bytes] = []
    total = 0

    def take(chunk: bytes) -> None:
        nonlocal total
        total += len(chunk)
        if total > MAX_CLIP_BYTES:
            raise _too_large()
        parts.append(chunk)

    if not codings:
        async for chunk in _raw_chunks(response):
            take(chunk)
        return b"".join(parts)

    bits = _WINDOW_BITS[codings[0]]
    decoder = zlib.decompressobj(bits)
    first = True
    async for chunk in _raw_chunks(response):
        data = chunk
        while True:
            try:
                out = decoder.decompress(data, _STEP)
            except zlib.error:
                if not (first and bits == 15):
                    raise
                # "deflate" is sent both zlib-wrapped and raw.
                bits = -15
                decoder = zlib.decompressobj(bits)
                out = decoder.decompress(data, _STEP)
            first = False
            if out:
                take(out)
            data = decoder.unconsumed_tail
            if not data and len(out) < _STEP:
                break
        if decoder.eof:
            break
    while not decoder.eof:  # output the decoder still holds
        out = decoder.decompress(b"", _STEP)
        if not out:
            break
        take(out)
    return b"".join(parts)
