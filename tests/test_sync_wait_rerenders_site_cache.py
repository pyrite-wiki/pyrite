"""``POST /api/index/sync?wait=true`` actually re-renders the site cache (#349).

Same root cause as #326/#322: the sync handler is a plain ``def`` route, so
it runs on a worker thread with no running event loop. The old code called
``asyncio.get_running_loop()`` from there, which raises, and a broad
``except Exception`` swallowed it -- so the site cache was never rendered.
A second, independent bug hid behind the first: the guard that was supposed
to skip the render when unconfigured (``if cache_svc.config:``) read
``request.app.state.config``, which nothing in the server ever sets, so it
was always ``None`` and the render never even reached the loop lookup.

**Surface.** A ``TestClient`` used as a context manager, so the app's
lifespan runs (startup binds the loop -- #326's ``bind_loop``/``_loop`` --
even though this fix does not need it, since the render happens
synchronously while the caller is already waiting). A markdown file is
written directly into the KB path (mirroring an external edit, exactly like
``tests/test_integration.py::test_manual_file_creation_syncs``) so
``sync_incremental`` reports a real ``added`` count, then
``POST /api/index/sync?wait=true`` is called and the rendered site-cache
file is checked on disk.

The KB is created with ``default_role="read"``: since the security-batch-2
change, ``/site`` (and ``SiteCacheService.render_all``) renders only public
KBs (``public_kbs.public_kb_names``); a KB with no default role renders
nothing, which would pass this test for the wrong reason.

**Cold-read fix round (2026-09-25).** The first pass made a render failure
fail the *sync itself*: a 500, skipping ``_drain_embed_queue`` and the
``kb_synced`` broadcast, and leaving a retry with ``added=0`` (nothing
changed, so it would never render again). Sync and render are now reported
independently -- the sync commits, drains and broadcasts regardless, and the
render's own outcome rides along on ``SyncResponse.site_cache`` (``{rendered,
error}``). "No broad except hiding the failure" now means: the failure is
visible in the response and the log (with a traceback), not that it must
raise and take the whole request down with it.
"""

from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi", reason="fastapi not installed")

from fastapi.testclient import TestClient  # noqa: E402

from pyrite.config import KBConfig, PyriteConfig, Settings  # noqa: E402
from pyrite.server.api import create_app  # noqa: E402
from pyrite.server.websocket import manager  # noqa: E402
from tests.ws_helpers import receive_json

MARKER = {"type": "kb_synced", "entry_id": "__marker__", "kb_name": ""}


def _config(tmp_path: Path) -> PyriteConfig:
    kb_path = tmp_path / "public-kb"
    kb_path.mkdir()
    return PyriteConfig(
        knowledge_bases=[
            KBConfig(name="public-kb", path=kb_path, kb_type="generic", default_role="read"),
        ],
        settings=Settings(index_path=tmp_path / "index.db"),
    )


def _write_entry(kb_path: Path) -> None:
    (kb_path / "manual-entry.md").write_text(
        """---
id: manual-entry
title: Manually Created Entry
entry_type: note
---

This entry was added directly to disk, the way an external edit would be.
"""
    )


class TestSyncWaitRerendersSiteCache:
    def test_sync_wait_true_rerenders_site_cache(self, tmp_path):
        config = _config(tmp_path)
        app = create_app(config=config)

        _write_entry(config.get_kb("public-kb").path)

        # SiteCacheService derives cache_dir from index_path's parent.
        expected_cache_dir = config.settings.index_path.parent / "site-cache"

        with TestClient(app) as client:
            resp = client.post("/api/index/sync", params={"wait": "true"})
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["added"] == 1, (
                f"precondition: sync should have picked up the manually written file, got {body}"
            )

        landing = expected_cache_dir / "index.html"
        assert landing.exists(), (
            "the site cache was never rendered after a sync that added an "
            f"entry (looked for {landing})"
        )
        html = landing.read_text()
        assert "public-kb" in html

    def test_sync_wait_true_reports_rendered_true_on_success(self, tmp_path):
        """A successful render reports ``rendered: true`` with no error and
        ``errors: 0`` -- the field is always present, per #408's groom, so a
        caller reads ``rendered and errors == 0`` as "all pages are fresh"."""
        config = _config(tmp_path)
        app = create_app(config=config)
        _write_entry(config.get_kb("public-kb").path)

        with TestClient(app) as client:
            resp = client.post("/api/index/sync", params={"wait": "true"})
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["site_cache"] == {"rendered": True, "errors": 0, "error": None}, body

    def test_sync_wait_true_reports_per_entry_errors_even_though_rendered_true(
        self, tmp_path, monkeypatch
    ):
        """#408: ``render_all``'s return value carries per-entry failures in
        ``stats['errors']``, but the sync handler used to discard it, so
        ``rendered: true`` even when every entry page failed to render.
        ``rendered`` means "the render ran to completion", independent of
        whether individual entry pages succeeded -- ``errors`` is the signal
        for that.
        """
        import pyrite.services.site_cache as site_cache_module

        config = _config(tmp_path)
        app = create_app(config=config)
        _write_entry(config.get_kb("public-kb").path)

        def _boom(self, *args, **kwargs):
            raise RuntimeError("boom: entry render exploded")

        monkeypatch.setattr(site_cache_module.SiteCacheService, "_render_entry", _boom)

        with TestClient(app) as client:
            resp = client.post("/api/index/sync", params={"wait": "true"})
            assert resp.status_code == 200, resp.text
            body = resp.json()
            # The render itself ran to completion -- it's per-entry pages
            # that failed -- so `rendered` stays true; `errors` carries the count.
            assert body["site_cache"]["rendered"] is True, body
            assert body["site_cache"]["errors"] == 1, body

    def test_sync_wait_true_does_not_hide_a_real_render_failure(
        self, tmp_path, monkeypatch, caplog
    ):
        """A render failure does not fail the sync itself.

        The sync already committed (files were parsed and the index
        updated) by the time the render runs, so a render failure must not
        turn a real, successful sync into a 500: that would also skip
        ``_drain_embed_queue`` and the ``kb_synced`` broadcast, and a retry
        would see ``added=0`` (nothing changed) and so never render again.

        Acceptance criterion, verbatim: "no broad `except` hiding the
        failure." Read together with the cold read's fix-round note above:
        the failure must be visible -- in the response (``site_cache.error``)
        and in the log, with a traceback -- not that it must raise.
        """
        import logging

        import pyrite.services.site_cache as site_cache_module

        config = _config(tmp_path)
        app = create_app(config=config)
        _write_entry(config.get_kb("public-kb").path)

        def _boom(self):
            raise RuntimeError("boom: rendering exploded")

        monkeypatch.setattr(site_cache_module.SiteCacheService, "render_all", _boom)

        with (
            caplog.at_level(logging.ERROR),
            TestClient(app) as client,
            client.websocket_connect("/ws") as ws,
        ):
            resp = client.post("/api/index/sync", params={"wait": "true"})

            assert resp.status_code == 200, resp.text
            body = resp.json()
            # The sync itself succeeded: counts are real, not swallowed.
            assert body["added"] == 1, body
            assert body["site_cache"]["rendered"] is False, body
            assert body["site_cache"]["error"], "the error field must be populated, not None"
            # `render_all` never returned -- no stats to report a count from.
            assert body["site_cache"]["errors"] == 0, body
            # Generic to callers -- the raw exception text stays in the log.
            assert "boom" not in body["site_cache"]["error"]

            # The drain and broadcast still ran despite the render failure.
            client.portal.call(manager.broadcast, dict(MARKER))
            assert receive_json(ws) == {
                "type": "kb_synced",
                "entry_id": "",
                "kb_name": "",
            }
            assert receive_json(ws) == MARKER

        # The failure is not silent: the traceback reached the log.
        assert any("boom" in r.message or "boom" in str(r.exc_info) for r in caplog.records), (
            f"no log record captured the render failure: {[r.message for r in caplog.records]}"
        )
        assert any(r.exc_info for r in caplog.records if "boom" in str(r.exc_info or "")), (
            "the render failure was logged without exc_info -- no traceback"
        )

    def test_drain_and_broadcast_run_before_the_render_is_attempted(self, tmp_path, monkeypatch):
        """The render happens *after* the drain and broadcast, not before.

        Independent of whether the render's own `try`/`except` catches
        everything: a future change to that block (a narrower exception
        type, an exception the render raises from a `finally`, ...) must not
        be able to resurrect the original bug, where a render problem could
        prevent the drain or the broadcast from ever running. Pinned by call
        order, not just by outcome.
        """
        import pyrite.server.api as api_module

        config = _config(tmp_path)
        app = create_app(config=config)
        _write_entry(config.get_kb("public-kb").path)

        calls: list[str] = []

        real_drain = api_module._drain_embed_queue

        def _spy_drain(db, *args, **kwargs):
            calls.append("drain")
            return real_drain(db, *args, **kwargs)

        def _spy_broadcast(*args, **kwargs):
            calls.append("broadcast")

        def _spy_render(self):
            calls.append("render")
            return {"kbs": 0, "entries": 0, "errors": 0}

        # `sync_index` does `from ..api import _drain_embed_queue` /
        # `from ..websocket import broadcast_event` *inside the handler*, so
        # each call re-resolves the current attribute on these modules --
        # patching the modules here reaches them.
        monkeypatch.setattr(api_module, "_drain_embed_queue", _spy_drain)
        monkeypatch.setattr("pyrite.server.websocket.broadcast_event", _spy_broadcast)
        monkeypatch.setattr("pyrite.services.site_cache.SiteCacheService.render_all", _spy_render)

        with TestClient(app) as client:
            # The lifespan's own startup drain (unrelated to this sync) may
            # already have appended to `calls`; only the request matters.
            calls.clear()
            resp = client.post("/api/index/sync", params={"wait": "true"})
            assert resp.status_code == 200, resp.text

        assert calls == ["drain", "broadcast", "render"], (
            f"expected drain and broadcast before render, got {calls}"
        )

    def test_a_broken_branding_file_does_not_fail_a_committed_sync(self, tmp_path):
        """Building the render service can fail too (#402 delta cold read).

        ``SiteCacheService`` reads ``branding.yaml`` in its constructor; an
        operator's half-edited file must be reported on ``site_cache``, not
        turn the committed sync into a 500.
        """
        config = _config(tmp_path)
        branding = tmp_path / "branding"
        branding.mkdir()
        (branding / "branding.yaml").write_text("- a list, not a mapping\n")
        config.settings.branding_dir = branding
        app = create_app(config=config)
        _write_entry(config.get_kb("public-kb").path)

        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post("/api/index/sync", params={"wait": "true"})
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["added"] == 1, body
            assert body["site_cache"]["rendered"] is False, body
            assert body["site_cache"]["errors"] == 0, body

    def test_a_sync_that_changes_nothing_reports_no_site_cache(self, tmp_path):
        """``site_cache`` is null when nothing changed: no render was attempted."""
        config = _config(tmp_path)
        app = create_app(config=config)
        _write_entry(config.get_kb("public-kb").path)

        with TestClient(app) as client:
            first = client.post("/api/index/sync", params={"wait": "true"})
            assert first.status_code == 200, first.text
            second = client.post("/api/index/sync", params={"wait": "true"})
            assert second.status_code == 200, second.text
            body = second.json()
            assert body["added"] + body["updated"] + body["removed"] == 0, body
            assert "site_cache" in body and body["site_cache"] is None, body
