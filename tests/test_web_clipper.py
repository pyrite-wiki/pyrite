"""Tests for Web Clipper feature."""

import pytest


class TestClipperSchemas:
    """Test clipper request/response schemas."""

    def test_clip_request_schema(self):
        """ClipRequest has required fields."""
        from pyrite.server.schemas import ClipRequest

        req = ClipRequest(url="https://example.com", kb="test-kb")
        assert req.url == "https://example.com"
        assert req.kb == "test-kb"
        assert req.title is None
        assert req.tags == []
        assert req.entry_type == "note"

    def test_clip_request_with_options(self):
        """ClipRequest with optional fields."""
        from pyrite.server.schemas import ClipRequest

        req = ClipRequest(
            url="https://example.com",
            kb="test-kb",
            title="Custom Title",
            tags=["web", "research"],
            entry_type="document",
        )
        assert req.title == "Custom Title"
        assert req.tags == ["web", "research"]
        assert req.entry_type == "document"

    def test_clip_response_schema(self):
        """ClipResponse has expected fields."""
        from pyrite.server.schemas import ClipResponse

        resp = ClipResponse(
            created=True,
            id="example-com",
            kb_name="test-kb",
            title="Example",
            source_url="https://example.com",
        )
        assert resp.created is True
        assert resp.id == "example-com"
        assert resp.source_url == "https://example.com"


class TestClipperEndpoint:
    """Test clipper endpoint registration."""

    def test_clipper_endpoint_exists(self):
        """Clipper router is registered in the app."""
        from pyrite.server.api import create_app

        app = create_app()
        # Walking app.routes directly is not reliable across FastAPI
        # versions -- 0.139 introduced internal _IncludedRouter wrapper
        # objects that defer route materialization and aren't reachable
        # by a straightforward .routes walk. app.openapi()["paths"] is
        # FastAPI's public, stable view of every route actually served.
        route_paths = app.openapi()["paths"].keys()
        assert "/api/clip" in route_paths


class TestClipperService:
    """ClipperService HTML processing, through ``clip_url`` (title, description, Markdown)."""

    @staticmethod
    def _clip(monkeypatch, html, **kwargs):
        import asyncio

        import httpx

        from pyrite.services.clipper import ClipperService

        _serve(monkeypatch, lambda request: httpx.Response(200, text=html))
        return asyncio.run(ClipperService().clip_url("https://example.com/p", **kwargs))

    @pytest.mark.control(
        reason="control: clip_url's output is unchanged; these moved from private helpers to the entry point"
    )
    def test_clipper_service_extracts_title(self, monkeypatch):
        html = "<html><head><title>My Page</title></head><body></body></html>"
        assert self._clip(monkeypatch, html).title == "My Page"

    @pytest.mark.control(
        reason="control: clip_url's output is unchanged; these moved from private helpers to the entry point"
    )
    def test_clipper_service_extracts_title_empty(self, monkeypatch):
        """No title: the URL stands in for it."""
        result = self._clip(monkeypatch, "<html><body>no title</body></html>")
        assert result.title == "https://example.com/p"

    @pytest.mark.control(
        reason="control: clip_url's output is unchanged; these moved from private helpers to the entry point"
    )
    def test_clipper_service_strips_scripts(self, monkeypatch):
        body = self._clip(monkeypatch, '<p>Hello</p><script>alert("x")</script><p>World</p>').body
        assert "alert" not in body
        assert "Hello" in body
        assert "World" in body

    @pytest.mark.control(
        reason="control: clip_url's output is unchanged; these moved from private helpers to the entry point"
    )
    def test_clipper_service_strips_nav(self, monkeypatch):
        html = "<nav>menu</nav><main><p>Content</p></main><footer>foot</footer>"
        body = self._clip(monkeypatch, html).body
        assert "menu" not in body
        assert "foot" not in body
        assert "Content" in body

    @pytest.mark.control(
        reason="control: clip_url's output is unchanged; these moved from private helpers to the entry point"
    )
    def test_clipper_service_extracts_description(self, monkeypatch):
        html = '<meta name="description" content="A great article">'
        assert self._clip(monkeypatch, html).description == "A great article"

    @pytest.mark.control(
        reason="control: clip_url's output is unchanged; these moved from private helpers to the entry point"
    )
    def test_clipper_service_extracts_og_description(self, monkeypatch):
        html = '<meta property="og:description" content="OG desc">'
        assert self._clip(monkeypatch, html).description == "OG desc"


def _serve(monkeypatch, handler):
    """Answer the clipper's requests with ``handler``; every name resolves public."""
    import socket

    import httpx

    real = httpx.AsyncClient

    def factory(**kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))
        ],
    )


class TestClipUrl:
    """Test full clip_url with a mocked transport."""

    @pytest.mark.control(
        reason="control: clip_url's output is unchanged; these moved from private helpers to the entry point"
    )
    def test_clip_url_fetches_and_converts(self, monkeypatch):
        """clip_url fetches HTML, extracts title and body as Markdown."""
        import asyncio

        import httpx

        from pyrite.services.clipper import ClipperService

        html = """<html>
        <head><title>Test Article</title>
        <meta name="description" content="A test article about testing.">
        </head>
        <body>
        <nav>Menu</nav>
        <main>
        <h1>Test Article</h1>
        <p>This is the article content with <strong>bold text</strong>.</p>
        </main>
        <footer>Footer</footer>
        </body>
        </html>"""
        _serve(monkeypatch, lambda request: httpx.Response(200, text=html))

        result = asyncio.run(ClipperService().clip_url("https://example.com/article"))

        assert result.title == "Test Article"
        assert result.source_url == "https://example.com/article"
        assert result.description == "A test article about testing."
        assert "article content" in result.body
        assert "Menu" not in result.body
        assert "Footer" not in result.body

    @pytest.mark.control(
        reason="control: clip_url's output is unchanged; these moved from private helpers to the entry point"
    )
    def test_clip_url_with_title_override(self, monkeypatch):
        """clip_url uses provided title instead of HTML title."""
        import asyncio

        import httpx

        from pyrite.services.clipper import ClipperService

        html = "<html><head><title>Original</title></head><body><p>Content</p></body></html>"
        _serve(monkeypatch, lambda request: httpx.Response(200, text=html))

        result = asyncio.run(ClipperService().clip_url("https://example.com", title="Custom Title"))

        assert result.title == "Custom Title"

    @pytest.mark.control(
        reason="control: clip_url's output is unchanged; these moved from private helpers to the entry point"
    )
    def test_clip_url_http_error(self, monkeypatch):
        """clip_url raises on HTTP error."""
        import asyncio

        import httpx

        from pyrite.services.clipper import ClipperService

        _serve(monkeypatch, lambda request: httpx.Response(404, text="gone"))

        with pytest.raises(httpx.HTTPStatusError):
            asyncio.run(ClipperService().clip_url("https://example.com/404"))
